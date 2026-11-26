"""Exercise integration clients against local HTTP endpoints, without devices."""
import datetime
import hashlib
import json
import sqlite3
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from sys import path

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import archive,database,health_service,onvif_time,security


class Server:
    def __init__(self,handler):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        self.thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.05),daemon=True)
    def __enter__(self):
        self.thread.start()
        return 'http://127.0.0.1:'+str(self.server.server_port)
    def __exit__(self,*_):
        self.server.shutdown();self.thread.join(timeout=5);self.server.server_close()


class MonitoringHTTPIntegrationTests(unittest.TestCase):
    def test_health_worker_delivers_real_camera_and_disk_transitions(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from sunhill_nvr import workers
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.end_headers()
        db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db);archive.initialize(db)
        try:
            with Server(Handler) as url:
                with db:
                    db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url',url),('ha_token','test-token'),('ha_enabled','1'),('recording_stale_after','60')])
                    db.execute("INSERT INTO cameras VALUES(1,'brama','Brama',1)")
                    db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode,recording_since) VALUES(1,'rtsp://cam','ftp',?)",(int(time.time())-120,))
                config=SimpleNamespace(archive=Path('/unused'),minimum_free=10,free_buffer=0)
                with patch('sunhill_nvr.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.workers.space_for',return_value=SimpleNamespace(free_for_service=9)),patch('sunhill_nvr.workers.service_monitor.check'):
                    workers.health_once(config,db);workers.health_once(config,db)
                self.assertEqual(received,[{'camera':'brama','state':'stale'},{'camera':'storage','state':'disk_low'}])
                self.assertEqual(db.execute('SELECT count(*) FROM pending_notifications').fetchone()[0],0)
        finally:db.close()

    def test_home_assistant_http_retry_and_event_order(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                received.append((self.path,self.headers.get('Authorization'),json.loads(body)))
                self.send_response(503 if len(received)==1 else 200);self.end_headers()
        db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db);archive.initialize(db)
        try:
            with Server(Handler) as url:
                with db:
                    db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url',url),('ha_token','test-token'),('ha_enabled','1')])
                    db.executemany('INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,?,?,1)',[('brama','stale'),('brama','recording')])
                self.assertEqual(health_service.deliver(db),0)
                self.assertEqual(db.execute('SELECT state,attempts FROM pending_notifications ORDER BY id').fetchall(),[('stale',1),('recording',0)])
                self.assertEqual(health_service.deliver(db),2)
                self.assertEqual(db.execute('SELECT count(*) FROM pending_notifications').fetchone()[0],0)
            self.assertEqual([item[2]['state'] for item in received],['stale','stale','recording'])
            self.assertTrue(all(item[0]=='/api/events/sunhill_nvr' and item[1]=='Bearer test-token' for item in received))
        finally:db.close()

    def test_home_assistant_redirect_does_not_forward_token(self):
        from sunhill_nvr import home_assistant
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                received.append(self.path);self.rfile.read(int(self.headers.get('Content-Length','0')))
                self.send_response(307);self.send_header('Location','/leak');self.end_headers()
        with Server(Handler) as url:
            with self.assertRaises(home_assistant.NotificationError):home_assistant.send(url,'test-token',camera_id='brama',state='stale')
        self.assertEqual(received,['/api/events/sunhill_nvr'])

    def test_onvif_digest_and_wsse_over_http(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                auth=self.headers.get('Authorization','');requests.append((auth,body))
                if b'UsernameToken' not in body and not auth:
                    self.send_response(401);self.send_header('WWW-Authenticate','Digest realm="camera", nonce="abcdef", algorithm=MD5, qop="auth"');self.end_headers();return
                if auth:
                    import re
                    fields={}
                    for key,quoted,plain in re.findall(r'(\w+)=(?:"([^"]*)"|([^, ]+))',auth[7:]):fields[key]=quoted or plain
                    md5=lambda value:hashlib.md5(value.encode()).hexdigest()
                    expected=md5(':'.join((md5('admin:camera:secret'),'abcdef',fields['nc'],fields['cnonce'],'auth',md5('POST:/onvif/device_service'))))
                    if fields.get('username')!='admin' or fields.get('response')!=expected:
                        self.send_response(403);self.end_headers();return
                now=datetime.datetime.now(datetime.timezone.utc)
                body=(f'<Envelope><UTCDateTime><Time><Hour>{now.hour}</Hour><Minute>{now.minute}</Minute><Second>{now.second}</Second></Time>'
                      f'<Date><Year>{now.year}</Year><Month>{now.month}</Month><Day>{now.day}</Day></Date></UTCDateTime></Envelope>').encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        with Server(Handler) as url:
            endpoint=url+'/onvif/device_service'
            digest=onvif_time.query(endpoint,'admin','secret',auth_mode='digest')
            wsse=onvif_time.query(endpoint,'admin','secret',auth_mode='wsse')
        self.assertLess(abs(digest.offset_seconds),5);self.assertLess(abs(wsse.offset_seconds),5)
        self.assertTrue(requests[1][0].startswith('Digest '));self.assertIn(b'PasswordDigest',requests[2][1]);self.assertNotIn(b'secret',requests[2][1])

    def test_bad_onvif_camera_response_does_not_block_next_camera(self):
        from sunhill_nvr import clock_service
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                now=datetime.datetime.now(datetime.timezone.utc)
                answer=(b'<bad/>' if b'bad-camera' in body else
                        (f'<UTCDateTime><Hour>{now.hour}</Hour><Minute>{now.minute}</Minute><Second>{now.second}</Second>'
                         f'<Year>{now.year}</Year><Month>{now.month}</Month><Day>{now.day}</Day></UTCDateTime>').encode())
                self.send_response(200);self.send_header('Content-Length',str(len(answer)));self.end_headers();self.wfile.write(answer)
        db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
        try:
            with Server(Handler) as url:
                with db:
                    for cid,slug in ((1,'bad-camera'),(2,'good-camera')):
                        db.execute('INSERT INTO cameras VALUES(?,?,?,1)',(cid,slug,slug))
                        db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode,onvif_url,onvif_username,onvif_password,onvif_auth) VALUES(?,'rtsp://x','ftp',?,?,?,'wsse')",(cid,url+'/onvif/device_service',slug,'secret'))
                clock_service.check(db)
            self.assertEqual(db.execute("SELECT name,state FROM system_health ORDER BY name").fetchall(),[('clock:1','clock_unavailable'),('clock:2','clock_ok')])
            self.assertEqual(db.execute('SELECT source,state FROM pending_notifications').fetchall(),[('bad-camera','clock_unavailable')])
        finally:db.close()


if __name__=='__main__':unittest.main()
