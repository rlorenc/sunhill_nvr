"""Exercise the application over loopback HTTP with real synthetic media.

This is not an Ubuntu, Nginx, Cloudflare, or camera integration test.
"""
import contextlib
import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from sys import path
from wsgiref.simple_server import WSGIRequestHandler, make_server

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import archive,database,export_service,jobs,security
from sunhill_nvr.media import probe
from sunhill_nvr.web import Application


class QuietHandler(WSGIRequestHandler):
    def log_message(self,format,*args):pass


class HttpArchiveIntegrationTests(unittest.TestCase):
    def test_login_browse_range_frame_export_and_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive_root=root/'archive';archive_root.mkdir();exports=root/'exports';exports.mkdir()
            dbfile=root/'db.sqlite';db=database.open_db(dbfile,create=True)
            security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
            security.create_user(db,'admin','long-test-password','admin')
            with db:
                db.execute("INSERT INTO cameras VALUES(1,'brama','Brama',1)")
                db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://camera/live','rtsp')")
            for second,color in [(0,'red'),(1,'blue')]:
                file=archive_root/f'brama/2026/11/21/19090{second}.mp4';file.parent.mkdir(parents=True,exist_ok=True)
                subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',f'color=c={color}:s=160x90:r=25:d=1',
                                '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-pix_fmt','yuv420p',
                                '-c:a','aac','-shortest',str(file)],check=True,stderr=subprocess.PIPE)
            archive.refresh(db,archive_root,'Europe/Warsaw',inspect_duration=lambda p:float(probe(p)['format']['duration']),
                            is_open=lambda p:False,mount_check=lambda:True,min_age_seconds=0)
            db.close()
            app=Application(dbfile,archive_root,exports,allowed_origin='https://nvr.example',archive_lock=root/'archive.lock')
            server=make_server('127.0.0.1',0,app,handler_class=QuietHandler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(endpoint,method='GET',payload=None,cookie=None,csrf=None,extra=None):
                headers={'Origin':'https://nvr.example'}
                if cookie:headers['Cookie']=cookie
                if csrf:headers['X-CSRF-Token']=csrf
                if extra:headers.update(extra)
                body=None
                if payload is not None:body=json.dumps(payload).encode();headers['Content-Type']='application/json'
                req=urllib.request.Request(base+endpoint,data=body,headers=headers,method=method)
                with opener.open(req,timeout=10) as response:return response.status,response.headers,response.read()
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:request('/api/cameras')
                self.assertEqual(denied.exception.code,401)
                status,headers,body=request('/api/login','POST',{'username':'admin','password':'long-test-password'})
                self.assertEqual(status,200);csrf=json.loads(body)['csrf']
                cookie='; '.join(value.split(';',1)[0] for value in headers.get_all('Set-Cookie'))
                _,_,body=request('/api/cameras',cookie=cookie);self.assertEqual(json.loads(body)['cameras'][0]['slug'],'brama')
                _,_,body=request('/api/cameras/1/calendar?month=2026-11',cookie=cookie)
                self.assertEqual(json.loads(body)['days'],['2026-11-21'])
                _,_,body=request('/api/cameras/1/recordings?date=2026-11-21',cookie=cookie)
                records=json.loads(body)['recordings'];self.assertEqual(len(records),2);rid=records[0]['id']
                status,headers,body=request(f'/api/recordings/{rid}/file',cookie=cookie,extra={'Range':'bytes=0-7'})
                self.assertEqual(status,206);self.assertEqual(len(body),8);self.assertTrue(headers['Content-Range'].startswith('bytes 0-7/'))
                status,headers,body=request(f'/api/recordings/{rid}/file','HEAD',cookie=cookie)
                self.assertEqual(status,200);self.assertEqual(body,b'');self.assertGreater(int(headers['Content-Length']),8)
                _,_,body=request(f'/api/recordings/{rid}/frame?offset=0.2',cookie=cookie)
                self.assertTrue(body.startswith(b'\xff\xd8'))
                selection={'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:02Z','gaps':'skip'}
                _,_,body=request('/api/cameras/1/export-preview','POST',selection,cookie,csrf)
                self.assertEqual(json.loads(body)['segments'],2)
                _,_,body=request('/api/cameras/1/export-jobs','POST',selection,cookie,csrf);jid=json.loads(body)['id']
                with contextlib.closing(database.open_db(dbfile)) as db:
                    export_service.run_one(db,archive_root,exports,archive_lock=root/'archive.lock',free=lambda p:10**12)
                _,_,body=request(f'/api/export-jobs/{jid}',cookie=cookie);self.assertEqual(json.loads(body)['status'],'done')
                _,headers,body=request(f'/api/export-jobs/{jid}/file',cookie=cookie)
                result=root/'result.mp4';result.write_bytes(body);media=probe(result)
                self.assertAlmostEqual(float(media['format']['duration']),2,delta=.2)
                self.assertTrue(any(s.get('codec_type')=='audio' for s in media['streams']))
                self.assertEqual(len(list(archive_root.glob('brama/*/*/*/*.mp4'))),2)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':unittest.main()
