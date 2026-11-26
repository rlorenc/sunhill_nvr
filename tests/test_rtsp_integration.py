"""Real RTSP-over-TCP recording against an isolated synthetic camera."""
from contextlib import closing
import datetime as dt
import os
from pathlib import Path
import re
import socket
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from sys import path
path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sunhill_nvr.media import probe
from sunhill_nvr.supervisor import Supervisor
from sunhill_nvr import database,security,stream_health


class Camera:
    def __init__(self, *, continuous=False):
        self.continuous=continuous;self.stop=threading.Event();self.clients=set();self.workers=[];self.lock=threading.Lock()
        raw = subprocess.check_output(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
            'color=blue:s=64x48:r=10:d=2','-c:v','libx264','-tune','zerolatency',
            '-x264-params','aud=1:keyint=10','-f','h264','pipe:1'])
        self.frames = []
        for nal in re.split(rb'\x00\x00\x00?\x01',raw):
            if not nal:continue
            if nal[0]&31 == 9 or not self.frames:self.frames.append([])
            self.frames[-1].append(nal)
        self.audio = subprocess.check_output(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
            'sine=frequency=440:sample_rate=8000:duration=2','-c:a','pcm_alaw','-f','alaw','pipe:1'])
        self.server = socket.socket()
        try:self.server.bind(('127.0.0.1',0));self.server.listen(8 if continuous else 1)
        except BaseException:self.server.close();raise
        self.server.settimeout(.25 if continuous else 10)
        self.port = self.server.getsockname()[1]
        self.errors = [];self.played = threading.Event()
        self.thread = threading.Thread(target=self.serve_many if continuous else self.serve,daemon=True);self.thread.start()

    def close(self):
        self.stop.set();self.server.close();self.thread.join(timeout=12)
        with self.lock:
            for client in self.clients:
                try:client.shutdown(socket.SHUT_RDWR)
                except OSError:pass
        for worker in self.workers:worker.join(timeout=2)

    def serve_many(self):
        while not self.stop.is_set():
            try:client,_=self.server.accept()
            except TimeoutError:continue
            except OSError:
                if not self.stop.is_set():raise
                return
            with self.lock:self.clients.add(client)
            worker=threading.Thread(target=self.serve,args=(client,),daemon=True)
            self.workers.append(worker);worker.start()

    def serve(self,client=None):
        try:
            if client is None:client,_=self.server.accept()
            client.settimeout(10)
            with client,client.makefile('rb') as stream:
                channels={}
                while True:
                    line=stream.readline().decode('ascii').strip()
                    if not line:raise RuntimeError('client closed before PLAY')
                    method,url,_=line.split();headers={}
                    while True:
                        header=stream.readline().decode('ascii').strip()
                        if not header:break
                        key,value=header.split(':',1);headers[key.lower()]=value.strip()
                    body=b'';extra=''
                    if method=='OPTIONS':extra='Public: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN\r\n'
                    elif method=='DESCRIBE':
                        body=('v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=fixture\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\na=control:*\r\n'
                            'm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\na=fmtp:96 packetization-mode=1\r\na=control:trackID=0\r\n'
                            'm=audio 0 RTP/AVP 8\r\na=rtpmap:8 PCMA/8000/1\r\na=control:trackID=1\r\n').encode()
                        extra=f'Content-Type: application/sdp\r\nContent-Base: rtsp://127.0.0.1:{self.port}/cam/\r\nContent-Length: {len(body)}\r\n'
                    elif method=='SETUP':
                        channel=int(re.search(r'interleaved=(\d+)',headers['transport']).group(1));channels[int(url[-1])]=channel
                        extra=f'Transport: RTP/AVP/TCP;unicast;interleaved={channel}-{channel+1}\r\nSession: 12345678\r\n'
                    elif method=='PLAY':extra='Session: 12345678\r\nRange: npt=0.000-\r\n'
                    else:raise RuntimeError('unexpected RTSP request')
                    client.sendall(f'RTSP/1.0 200 OK\r\nCSeq: {headers["cseq"]}\r\n{extra}\r\n'.encode()+body)
                    if method=='PLAY':break
                self.played.set();seq=[0,0]
                def packet(track,payload,timestamp,marker):
                    seq[track]+=1
                    data=struct.pack('!BBHII',0x80,(0x80 if marker else 0)|(96 if track==0 else 8),seq[track],timestamp,100+track)+payload
                    client.sendall(struct.pack('!BBH',36,channels[track],len(data))+data)
                index=0
                while not self.stop.is_set():
                    if not self.continuous and index>=len(self.frames):break
                    frame_index=index%len(self.frames);frame=self.frames[frame_index]
                    if 0 in channels:
                        for j,nal in enumerate(frame):
                            if len(nal)>1400:raise RuntimeError('fixture packet too large')
                            packet(0,nal,index*9000,j==len(frame)-1)
                    if 1 in channels:
                        for j in range(5):
                            offset=(frame_index*5+j)*160;timestamp=(index*5+j)*160
                            packet(1,self.audio[offset:offset+160],timestamp,False)
                    index+=1
                    self.stop.wait(.1 if self.continuous else .02)
                if not self.continuous:time.sleep(.2)
        except (BrokenPipeError,ConnectionResetError) as exc:
            # A failed audio-copy attempt can close the RTSP socket early.
            if not self.played.is_set():self.errors.append(exc)
        except Exception as exc:
            if not self.stop.is_set():self.errors.append(exc)
        finally:
            if client is not None:
                client.close()
                with self.lock:self.clients.discard(client)


class RTSPIntegrationTests(unittest.TestCase):
    def test_continuous_camera_supports_two_simultaneous_media_consumers(self):
        camera=Camera(continuous=True);processes=[]
        try:
            for _ in range(2):
                processes.append(subprocess.Popen(['ffmpeg','-nostdin','-hide_banner','-loglevel','error',
                    '-rtsp_transport','tcp','-i',f'rtsp://127.0.0.1:{camera.port}/cam',
                    '-map','0:v:0','-map','0:a:0','-t','1','-f','null','-'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE))
            for process in processes:
                _,error=process.communicate(timeout=20);self.assertEqual(process.returncode,0,error)
            self.assertTrue(camera.played.is_set());self.assertFalse(camera.errors)
        finally:
            for process in processes:
                if process.poll() is None:process.kill();process.communicate(timeout=5)
            camera.close()

    def test_monitor_decodes_real_rtsp_and_reports_loss_and_recovery(self):
        with closing(sqlite3.connect(':memory:')) as db, db:
            security.initialize(db);database.migrate(db)
            db.execute("INSERT INTO cameras VALUES(1,'cam','Camera',1)")
            db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://unused/cam','rtsp')");db.commit()
            # A local fixture sends actual RTP, not a mocked FFmpeg result.
            def healthy(slug):
                camera=Camera()
                try:
                    result=stream_health.probe(slug,port=camera.port)
                    self.assertTrue(camera.played.is_set());self.assertFalse(camera.errors)
                    return result
                finally:camera.close()
            def unavailable(slug):
                # Bind without listening: the OS refuses real connections
                # without accidentally probing another service on a reused port.
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1',0))
                    return stream_health.probe(slug,port=sock.getsockname()[1],timeout=3)
            stream_health.check(db,now=10,gateway=lambda:True,inspect=healthy)
            self.assertEqual(db.execute('SELECT state FROM stream_health').fetchone(),('available',))
            stream_health.check(db,now=20,gateway=lambda:True,inspect=unavailable)
            stream_health.check(db,now=30,gateway=lambda:True,inspect=unavailable)
            self.assertEqual(db.execute('SELECT state FROM stream_health').fetchone(),('unavailable',))
            stream_health.check(db,now=40,gateway=lambda:True,inspect=healthy)
            self.assertEqual(db.execute('SELECT state FROM stream_health').fetchone(),('available',))
            self.assertEqual(db.execute('SELECT state FROM pending_notifications ORDER BY id').fetchall(),[('offline',),('online',)])

    def test_real_rtsp_audio_fallback_and_reconnect_publish_new_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);staging=root/'staging';archive=root/'archive';staging.mkdir();archive.mkdir()
            cameras=[];processes=[]
            def spawn(command):
                camera=Camera();cameras.append(camera)
                command=[arg.replace('rtsp://127.0.0.1:8554/','rtsp://127.0.0.1:'+str(camera.port)+'/') for arg in command]
                process=subprocess.Popen(command,env={**os.environ,'TZ':'UTC'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                processes.append(process);return process
            def cleanup():
                for process in processes:
                    if process.poll() is None:process.kill();process.wait(timeout=5)
                for camera in cameras:camera.close()
            try:
                # The fixture owns its processes; in production the privileged
                # activity helper determines whether each file is still open.
                worker=Supervisor(staging,archive,timezone='UTC',active=lambda p:any(proc.poll() is None for proc in processes),spawn=spawn)
                now=time.time();worker.tick({'cam'},now)
                first=worker.running['cam'];first.process.wait(timeout=15)
                self.assertTrue(cameras[0].played.is_set());self.assertFalse(cameras[0].errors)
                # PCMA cannot be copied into MP4 on the supported FFmpeg build.
                self.assertNotEqual(first.process.returncode,0)
                worker.tick({'cam'},now+1);worker.tick({'cam'},now+4)
                second=worker.running['cam'];self.assertEqual(second.audio,'aac');self.assertNotEqual(first.session,second.session)
                second.process.wait(timeout=15);self.assertEqual(second.process.returncode,0)
                worker.tick({'cam'},now+5)
                files=list(archive.rglob('*.mp4'));self.assertEqual(len(files),1)
                first_file=files[0];self.assertEqual({s['codec_type']:s['codec_name'] for s in probe(first_file)['streams']},{'video':'h264','audio':'aac'})
                original=first_file.read_bytes();worker.tick({'cam'},now+10)
                third=worker.running['cam'];self.assertEqual(third.audio,'aac');self.assertNotEqual(second.session,third.session)
                third.process.wait(timeout=15);self.assertEqual(third.process.returncode,0)
                worker.tick({'cam'},now+11)
                files=list(archive.rglob('*.mp4'));self.assertEqual(len(files),2);self.assertEqual(first_file.read_bytes(),original)
                for camera in cameras:self.assertTrue(camera.played.is_set());self.assertFalse(camera.errors)
                for file in files:self.assertEqual({s['codec_type']:s['codec_name'] for s in probe(file)['streams']},{'video':'h264','audio':'aac'})
            finally:cleanup()

if __name__=='__main__':unittest.main()
