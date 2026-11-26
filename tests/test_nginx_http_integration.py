"""Real local Nginx HTTP/TLS + WSGI. No external DNS, cameras or Cloudflare."""
import contextlib
import datetime as dt
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from sys import path
from wsgiref.simple_server import make_server, WSGIRequestHandler
from zoneinfo import ZoneInfo

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import __version__, archive, database, jobs, nginx_config, security, tls
from sunhill_nvr.web import Application

class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args): pass

@unittest.skipUnless(shutil.which('nginx') and shutil.which('openssl'), 'requires local Nginx and OpenSSL')
class NginxHttpIntegrationTests(unittest.TestCase):
    def test_ip_vhost_with_distribution_default_site_and_no_local_tls(self):
        with tempfile.TemporaryDirectory() as name, contextlib.ExitStack() as stack:
            root=Path(name); dbfile=root/'db.sqlite'; archive_root=root/'archive'; archive_root.mkdir(); exports=root/'exports'; exports.mkdir()
            with contextlib.closing(database.open_db(dbfile,create=True)) as db:
                security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
                security.create_user(db,'admin','admin-password-for-test','admin')
            app=Application(dbfile,archive_root,exports,allowed_origin='https://nvr.example',archive_lock=root/'archive.lock',trust_client_header=True)
            server=make_server('127.0.0.1',0,app,handler_class=QuietHandler)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            def stop_server():server.shutdown();server.server_close();worker.join(timeout=5)
            stack.callback(stop_server)
            with contextlib.ExitStack() as sockets:
                ports=[]
                for _ in range(2):
                    sock=sockets.enter_context(socket.socket());sock.bind(('127.0.0.1',0));ports.append(sock.getsockname()[1])
            origin_port,http_port=ports
            config=nginx_config.render('nvr.example',app_port=server.server_port)
            config=config.replace('listen 127.0.0.1:8080;',f'listen 127.0.0.1:{origin_port};').replace('listen 80;',f'listen 127.0.0.1:{http_port};')
            # Emulate Ubuntu's enabled default site, leaving it intact.
            config+=f'\nserver {{ listen 127.0.0.1:{http_port} default_server; server_name _; return 200 "distribution default"; }}\n'
            conf=root/'nginx.conf';conf.write_text(f'pid {root}/nginx.pid;\nerror_log stderr;\nmaster_process off;\ndaemon off;\nevents {{}}\nhttp {{ access_log off; client_body_temp_path {root}/body; proxy_temp_path {root}/proxy; fastcgi_temp_path {root}/fastcgi; uwsgi_temp_path {root}/uwsgi; scgi_temp_path {root}/scgi;\n{config}\n}}\n')
            cmd=[shutil.which('nginx'),'-p',str(root)+'/', '-c',str(conf)]
            subprocess.run(cmd+['-t'],check=True,capture_output=True,timeout=10)
            with (root/'nginx.log').open('w+b') as log:
                proc=subprocess.Popen(cmd,stdout=log,stderr=log)
                try:
                    for _ in range(100):
                        if proc.poll() is not None:
                            log.seek(0);self.fail(log.read().decode(errors='replace'))
                        try:
                            with socket.create_connection(('127.0.0.1',http_port),timeout=.1):break
                        except OSError:time.sleep(.02)
                    else:self.fail('nginx failed to listen')
                    def request(host,method='GET',port=http_port,endpoint='/?x=1',body=None,headers=None):
                        with contextlib.closing(http.client.HTTPConnection('127.0.0.1',port,timeout=5)) as conn:
                            conn.request(method,endpoint,body=body,headers={'Host':host,**(headers or {})});res=conn.getresponse()
                            return res.status,res.getheader('Location'),res.read()
                    status,location,body=request('127.0.0.1')
                    self.assertEqual((status,location),(200,None));self.assertIn(__version__.encode(),body)
                    self.assertEqual(request('127.0.0.1','HEAD')[:2],(200,None))
                    self.assertEqual(request('127.0.0.1','POST',endpoint='/api/login',body=json.dumps({'username':'admin','password':'admin-password-for-test'}),headers={'Content-Type':'application/json','Origin':'http://127.0.0.1'})[0],200)
                    self.assertEqual(request('192.168.0.7')[0],421)
                    self.assertEqual(request('nvr.example')[:2],(301,'https://nvr.example/?x=1'))
                    self.assertEqual(request('unrelated.example'),(200,None,b'distribution default'))
                    self.assertEqual(request('192.168.0.7',port=origin_port)[0],421)
                finally:
                    proc.terminate()
                    try:proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)

    def test_tls_origin_access_events_and_revocation(self):
        with tempfile.TemporaryDirectory() as name, contextlib.ExitStack() as stack:
            root=Path(name); dbfile=root/'db.sqlite'; storage=root/'archive'; storage.mkdir(); exports=root/'exports'; exports.mkdir()
            with contextlib.closing(database.open_db(dbfile,create=True)) as db:
                security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
                security.create_user(db,'admin','admin-password-for-test','admin')
                viewer=security.create_user(db,'viewer','viewer-password-for-test','viewer')
                with db:
                    db.execute("INSERT INTO cameras VALUES(1,'cam','Camera',1)")
                    db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://private:secret@camera/live','ftp')")
                    db.execute('INSERT INTO camera_permissions VALUES(?,?,?)',(viewer,1,0))
            app=Application(dbfile,storage,exports,allowed_origin='https://nvr.example',archive_lock=root/'archive.lock',trust_client_header=True)
            server=make_server('127.0.0.1',0,app,handler_class=QuietHandler)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            def stop_server():server.shutdown();server.server_close();worker.join(timeout=5)
            stack.callback(stop_server)
            # Exercise the actual Nginx auth_request and Origin forwarding; the
            # gateway is a sentinel, not a real go2rtc/media test.
            seen=[]
            class Gateway(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_GET(self):
                    seen.append((self.path,self.headers.get('Origin')))
                    self.send_response(418);self.end_headers()
            gateway=HTTPServer(('127.0.0.1',0),Gateway)
            gateway_thread=threading.Thread(target=gateway.serve_forever,daemon=True);gateway_thread.start()
            def stop_gateway():gateway.shutdown();gateway.server_close();gateway_thread.join(timeout=5)
            stack.callback(stop_gateway)
            # Bind ephemeral, unprivileged ports on loopback only. The generated
            # locations, security headers, and TLS directives are unchanged.
            with contextlib.ExitStack() as sockets:
                ports=[]
                for _ in range(3):
                    sock=sockets.enter_context(socket.socket());sock.bind(('127.0.0.1',0));ports.append(sock.getsockname()[1])
            origin_port,tls_port,redirect_port=ports
            cert,key,ca=tls.generate_local('nvr.example',root)
            config=nginx_config.render('nvr.example',app_port=server.server_port,tls_certificate=cert,tls_key=key).replace('127.0.0.1:1985',f'127.0.0.1:{gateway.server_port}')
            config=config.replace('listen 127.0.0.1:8080;',f'listen 127.0.0.1:{origin_port};').replace('listen 443 ssl;',f'listen 127.0.0.1:{tls_port} ssl;').replace('listen 80;',f'listen 127.0.0.1:{redirect_port};').replace('/opt/sunhill-nvr/config/maintenance.flag',str(root/'maintenance.flag'))
            conf=root/'nginx.conf';conf.write_text(f'pid {root}/nginx.pid;\nerror_log stderr;\nmaster_process off;\ndaemon off;\nevents {{}}\nhttp {{ access_log off; client_body_temp_path {root}/body; proxy_temp_path {root}/proxy; fastcgi_temp_path {root}/fastcgi; uwsgi_temp_path {root}/uwsgi; scgi_temp_path {root}/scgi;\n{config}\n}}\n')
            cmd=[shutil.which('nginx'),'-p',str(root)+'/', '-c',str(conf)]
            subprocess.run(cmd+['-t'],check=True,capture_output=True,timeout=10)
            log=stack.enter_context((root/'nginx.log').open('w+b'));proc=subprocess.Popen(cmd,stdout=log,stderr=log)
            def stop_nginx():
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            stack.callback(stop_nginx)
            for _ in range(100):
                if proc.poll() is not None:
                    log.seek(0);self.fail(log.read().decode(errors='replace'))
                try:
                    with socket.create_connection(('127.0.0.1',origin_port),timeout=.1):break
                except OSError:time.sleep(.02)
            else:self.fail('nginx failed to listen')
            trust=ssl.create_default_context(cafile=str(ca))
            # Connect to loopback while verifying the intended DNS identity.
            class LocalHTTPS(http.client.HTTPSConnection):
                def connect(self):
                    sock=socket.create_connection(('127.0.0.1',self.port),self.timeout)
                    self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
            with self.assertRaises(ssl.SSLCertVerificationError):
                with contextlib.closing(LocalHTTPS('nvr.example',tls_port,context=ssl.create_default_context(),timeout=5)) as conn:
                    conn.request('GET','/')
            def request(endpoint,method='GET',payload=None,*,port=tls_port,cookie=None,csrf=None,extra=None):
                headers={'Host':'nvr.example','Origin':'https://nvr.example'}
                if cookie:headers['Cookie']=cookie
                if csrf:headers['X-CSRF-Token']=csrf
                if extra:headers.update(extra)
                body=None if payload is None else json.dumps(payload).encode()
                if body is not None:headers['Content-Type']='application/json'
                conn=(LocalHTTPS('nvr.example',port,context=trust,timeout=5) if port==tls_port else http.client.HTTPConnection('127.0.0.1',port,timeout=5))
                try:
                    conn.request(method,endpoint,body=body,headers=headers);res=conn.getresponse()
                    return res.status,res.headers,res.read()
                finally:conn.close()
            def login(username,password,port=tls_port):
                status,headers,body=request('/api/login','POST',{'username':username,'password':password},port=port)
                self.assertEqual(status,200,body);self.assertTrue(any('Secure' in c and 'HttpOnly' in c for c in headers.get_all('Set-Cookie')))
                return '; '.join(c.split(';',1)[0] for c in headers.get_all('Set-Cookie')),json.loads(body)['csrf']
            self.assertEqual(request('/',port=redirect_port)[0],301)
            lan={'Host':'127.0.0.1','Origin':'http://127.0.0.1'}
            status,headers,body=request('/?a=1',port=redirect_port,extra=lan)
            self.assertEqual((status,headers.get('Location')),(200,None));self.assertIn(__version__.encode(),body)
            self.assertIn('ws://127.0.0.1',headers['Content-Security-Policy'])
            self.assertEqual(request('/','HEAD',port=redirect_port,extra=lan)[0],200)
            status,headers,body=request('/api/login','POST',{'username':'admin','password':'admin-password-for-test'},port=redirect_port,extra=lan)
            self.assertEqual(status,200,body);self.assertTrue(all('Secure' not in c for c in headers.get_all('Set-Cookie')))
            lan_cookie='; '.join(c.split(';',1)[0] for c in headers.get_all('Set-Cookie'));lan_csrf=json.loads(body)['csrf']
            self.assertEqual(request('/api/me',port=redirect_port,cookie=lan_cookie,extra=lan)[0],200)
            self.assertEqual(request('/api/cameras',port=redirect_port,cookie=lan_cookie,extra=lan)[0],200)
            self.assertEqual(request('/live/api/ws?src=cam',port=redirect_port,cookie=lan_cookie,extra={**lan,'Origin':'https://attacker.example'})[0],403)
            self.assertEqual(seen,[])
            self.assertEqual(request('/live/api/ws?src=cam',port=redirect_port,cookie=lan_cookie,extra=lan)[0],418)
            self.assertEqual(seen,[('/api/ws?src=cam','https://nvr.example')])
            self.assertEqual(request('/api/logout','POST',port=redirect_port,cookie=lan_cookie,csrf=lan_csrf,extra={**lan,'Origin':'https://attacker.example'})[0],403)
            self.assertEqual(request('/api/logout','POST',port=redirect_port,cookie=lan_cookie,extra=lan)[0],403)
            status,headers,_=request('/api/logout','POST',port=redirect_port,cookie=lan_cookie,csrf=lan_csrf,extra=lan)
            self.assertEqual(status,200);self.assertTrue(all('Secure' not in c for c in headers.get_all('Set-Cookie')))
            self.assertEqual(request('/api/me',port=redirect_port,cookie=lan_cookie,extra=lan)[0],401)
            self.assertEqual(request('/api/login','POST',{'username':'admin','password':'admin-password-for-test'},port=origin_port,extra={'Origin':'http://127.0.0.1','X-Sunhill-LAN-Origin':'http://127.0.0.1','X-Sunhill-Client-IP':'127.0.0.1'})[0],403)
            self.assertEqual(request('/api/cameras',port=origin_port)[0],401)
            self.assertEqual(request('/internal/health')[0],404)
            self.assertEqual(request('/internal/live-auth')[0],404)
            self.assertEqual(request('/live/api/ws?src=cam',extra={'Upgrade':'websocket','Connection':'Upgrade'})[0],401)
            self.assertEqual(request('/',extra={'Host':'invalid.example'})[0],421)
            admin,csrf=login('admin','admin-password-for-test',origin_port)
            user,usercsrf=login('viewer','viewer-password-for-test')
            status,_,body=request('/api/cameras',cookie=user)
            self.assertEqual(status,200);self.assertNotIn(b'secret',body);self.assertNotIn(b'rtsp://',body)
            status,_,body=request('/api/integrations/events/token','POST',{},cookie=admin,csrf=csrf)
            self.assertEqual(status,201,body);token=json.loads(body)['token']
            now=dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            day=now.astimezone(ZoneInfo('Europe/Warsaw')).date().isoformat()
            def send(kind):return request('/api/integrations/events','POST',{'camera':'cam','type':kind,'time':now.isoformat()},extra={'Authorization':'Bearer '+token,'Origin':''})[0]
            for kind in ('motion','person','vehicle','motion'):self.assertEqual(send(kind),202)
            self.assertEqual(send('unknown'),400)
            endpoint=f'/api/cameras/1/events?date={day}'
            status,_,body=request(endpoint,cookie=user);self.assertEqual(status,200)
            events=json.loads(body)['events'];self.assertEqual({e['type'] for e in events},{'motion','person','vehicle'});self.assertEqual(len(events),3)
            self.assertTrue(all(e['timestamp']==int(now.timestamp()) for e in events))
            # Historic DST fixtures are injected only into the test DB because
            # external event ingestion intentionally limits backdating to 7 days.
            with contextlib.closing(database.open_db(dbfile)) as db,db:
                for hour in (0,1):
                    stamp=int(dt.datetime(2020,10,25,hour,30,tzinfo=dt.timezone.utc).timestamp())
                    db.execute('INSERT OR IGNORE INTO event_markers(camera_id,started_at,event_type,source) VALUES(1,?,?,?)',(stamp,'person','fixture'))
            status,_,body=request('/api/cameras/1/events?date=2020-10-25',cookie=user);self.assertEqual(status,200)
            self.assertEqual([e['fold'] for e in json.loads(body)['events'] if e['type']=='person' and dt.datetime.fromtimestamp(e['timestamp'],dt.timezone.utc).minute==30],[0,1])
            self.assertEqual(request(f'/api/users/{viewer}/permissions','POST',{'cameras':[]},cookie=admin,csrf=csrf)[0],200)
            self.assertEqual(request(endpoint,cookie=user)[0],403)
            self.assertEqual(request('/api/integrations/events/token','DELETE',cookie=admin,csrf=csrf)[0],200)
            self.assertEqual(send('motion'),401)
            self.assertEqual(request('/api/logout','POST',cookie=user,csrf=usercsrf)[0],200)
            self.assertEqual(request('/api/me',cookie=user)[0],401)
            (root/'maintenance.flag').touch();self.assertEqual(request('/api/cameras',cookie=admin)[0],503)

if __name__=='__main__':unittest.main()
