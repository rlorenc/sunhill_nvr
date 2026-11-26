import asyncio,base64,hashlib,json,os,tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import database,live_proxy,security

class LiveProxyTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.dbfile=Path(self.tmp.name)/'db.sqlite'
    db=database.open_db(self.dbfile,create=True);security.initialize(db);database.migrate(db)
    uid=security.create_user(db,'viewer','viewer-test-password','viewer')
    with db:
        db.execute("INSERT INTO cameras VALUES(1,'cam','Camera',1)")
        db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://camera','ftp')")
        db.execute('INSERT INTO camera_permissions VALUES(?,1,0)',(uid,))
    self.token=security.authenticate(db,'viewer','viewer-test-password','client').token;db.close()
    self.upstream_count=0
    self.upstream_headers=[]
    async def upstream(reader,writer):
        self.upstream_count+=1
        try:
            raw=await reader.readuntil(b'\r\n\r\n')
            headers=dict((name.lower(),value.strip()) for name,value in (line.decode('ascii').split(':',1) for line in raw.split(b'\r\n')[1:] if line))
            self.upstream_headers.append(headers)
            # Model the default same-host Origin check of the go2rtc upgrader.
            from urllib.parse import urlsplit
            if urlsplit(headers.get('origin','')).netloc.lower()!=headers['host'].lower():
                writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');await writer.drain();return
            key=headers['sec-websocket-key']
            accept=base64.b64encode(hashlib.sha1((key+live_proxy.GUID).encode()).digest()).decode()
            writer.write(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+'\r\n\r\n').encode());await writer.drain()
            while True:
                data=await reader.read(1024)
                if not data:break
                writer.write(data);await writer.drain()
        finally:
            writer.close();await writer.wait_closed()
    self.upstream=await asyncio.start_server(upstream,'127.0.0.1',0)
    port=self.upstream.sockets[0].getsockname()[1]
    async def handle(reader,writer):
        await live_proxy.handle(reader,writer,origin='https://nvr.example.com',authorize_fn=lambda token,alias:live_proxy.authorize(self.dbfile,token,alias),upstream_port=port,check_interval=.05)
    self.server=await asyncio.start_server(handle,'127.0.0.1',0,limit=live_proxy.MAX_HEADER)
    self.port=self.server.sockets[0].getsockname()[1]
 async def asyncTearDown(self):
    self.server.close();self.upstream.close();await self.server.wait_closed();await self.upstream.wait_closed();self.tmp.cleanup()
 def handshake(self,origin='https://nvr.example.com',alias='cam'):
    key=base64.b64encode(os.urandom(16)).decode()
    return (f'GET /api/ws?src={alias} HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
            f'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\nOrigin: {origin}\r\nCookie: sunhill_session={self.token}\r\n\r\n').encode()
 async def test_same_host_upstream_origin_for_all_camera_profiles(self):
    db=database.open_db(self.dbfile)
    with db:db.execute("UPDATE camera_details SET sub_url='rtsp://camera/sub' WHERE camera_id=1")
    db.close()
    for alias in ('cam','cam_sub','~cam'):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
        writer.write(self.handshake(alias=alias));await writer.drain()
        try:
            self.assertTrue((await reader.readuntil(b'\r\n\r\n')).startswith(b'HTTP/1.1 101'))
            headers=self.upstream_headers[-1]
            self.assertEqual(headers['origin'],'http://'+headers['host'])
            self.assertNotIn('cookie',headers)
        finally:writer.close();await writer.wait_closed()
 async def test_stream_closes_after_permission_is_revoked(self):
    reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
    writer.write(self.handshake());await writer.drain()
    self.assertTrue((await reader.readuntil(b'\r\n\r\n')).startswith(b'HTTP/1.1 101'))
    frame=b'\x81\x82\x01\x02\x03\x04\x69\x6b';writer.write(frame);await writer.drain()
    self.assertEqual(await asyncio.wait_for(reader.readexactly(len(frame)),2),frame)
    db=database.open_db(self.dbfile)
    with db:db.execute('DELETE FROM camera_permissions WHERE camera_id=1')
    db.close()
    self.assertEqual(await asyncio.wait_for(reader.read(1),3),b'')
    writer.close();await writer.wait_closed()
 async def test_compatible_stream_requires_the_same_camera_permission(self):
    self.assertTrue(live_proxy.authorize(self.dbfile,self.token,'~cam'))
    reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
    writer.write(self.handshake(alias='~cam'));await writer.drain()
    self.assertTrue((await reader.readuntil(b'\r\n\r\n')).startswith(b'HTTP/1.1 101'))
    db=database.open_db(self.dbfile)
    with db:db.execute('DELETE FROM camera_permissions WHERE camera_id=1')
    db.close();self.assertEqual(await asyncio.wait_for(reader.read(1),3),b'')
    writer.close();await writer.wait_closed()
    self.assertFalse(live_proxy.authorize(self.dbfile,self.token,'~cam'))
 async def test_ambiguous_alias_fails_closed_even_for_allowed_owner(self):
    db=database.open_db(self.dbfile)
    with db:
        db.execute("UPDATE camera_details SET sub_url='rtsp://camera/sub' WHERE camera_id=1")
        db.execute("INSERT INTO cameras VALUES(2,'cam_sub','Other',1)")
        db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(2,'rtsp://other','ftp')")
    db.close()
    self.assertFalse(live_proxy.authorize(self.dbfile,self.token,'cam_sub'))
    self.assertTrue(live_proxy.authorize(self.dbfile,self.token,'cam'))
 async def test_revocation_during_upstream_handshake_is_rechecked(self):
    checks=[]
    async def serve(reader,writer):
        def auth(token,alias):
            checks.append(alias);return len(checks)==1
        await live_proxy.handle(reader,writer,origin='https://nvr.example.com',authorize_fn=auth,upstream_port=self.upstream.sockets[0].getsockname()[1])
    server=await asyncio.start_server(serve,'127.0.0.1',0)
    try:
        reader,writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1])
        writer.write(self.handshake());await writer.drain()
        self.assertTrue((await reader.readuntil(b'\r\n\r\n')).startswith(b'HTTP/1.1 403'))
        self.assertEqual(checks,['cam','cam']);writer.close();await writer.wait_closed()
    finally:server.close();await server.wait_closed()
 @unittest.skipUnless(os.geteuid()==0,'root-owned gateway fixture required')
 async def test_production_gateway_must_match_authorized_source_and_be_activated(self):
    state=Path(self.tmp.name)/'control';state.mkdir(mode=0o750)
    folder=state/'config-test';folder.mkdir(mode=0o750)
    path=folder/'go2rtc.json';(state/'current').symlink_to(folder.name)
    def publish(main):
        path.write_text(json.dumps({'streams':{'cam':main,'~cam':'ffmpeg:cam#video=h264#audio=aac'}}));path.chmod(0o640)
    publish('rtsp://camera')
    auth=lambda:live_proxy.authorize_stream(self.dbfile,state,self.token,'cam')
    self.assertEqual(auth(),(1,folder.name))
    from unittest.mock import patch
    flag=Path(self.tmp.name)/'maintenance.flag';flag.touch()
    with patch('sunhill_nvr.workers.MAINTENANCE_FLAG',flag):
        self.assertIsNone(auth());flag.unlink();self.assertEqual(auth(),(1,folder.name))
    (state/'.activation-pending').touch();self.assertIsNone(auth());(state/'.activation-pending').unlink()
    db=database.open_db(self.dbfile)
    with db:db.execute("UPDATE camera_details SET main_url='rtsp://replacement' WHERE camera_id=1")
    db.close();self.assertIsNone(auth())
    self.assertIsNone(live_proxy.authorize_stream(self.dbfile,state,self.token,'~cam'))
    publish('rtsp://replacement');self.assertEqual(auth(),(1,folder.name))
    path.chmod(0o666);self.assertIsNone(auth())
 async def test_identity_change_during_handshake_denies_old_stream(self):
    identities=iter([(1,'config-old'),(2,'config-new')])
    async def serve(reader,writer):
        await live_proxy.handle(reader,writer,origin='https://nvr.example.com',authorize_fn=lambda token,alias:next(identities),upstream_port=self.upstream.sockets[0].getsockname()[1])
    server=await asyncio.start_server(serve,'127.0.0.1',0)
    try:
        reader,writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1])
        writer.write(self.handshake());await writer.drain()
        self.assertTrue((await reader.readuntil(b'\r\n\r\n')).startswith(b'HTTP/1.1 403'))
        writer.close();await writer.wait_closed()
    finally:server.close();await server.wait_closed()
 async def test_wrong_origin_or_unknown_stream_never_reaches_upstream(self):
    for request in (self.handshake(origin='https://evil.example'),self.handshake(alias='other')):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port);writer.write(request);await writer.drain()
        self.assertTrue((await reader.readuntil(b'\r\n\r\n')).startswith(b'HTTP/1.1 403'))
        writer.close();await writer.wait_closed()
    self.assertEqual(self.upstream_count,0)
 async def test_logout_and_disabled_camera_deny_existing_token(self):
    self.assertTrue(live_proxy.authorize(self.dbfile,self.token,'cam'))
    db=database.open_db(self.dbfile)
    with db:db.execute('UPDATE cameras SET enabled=0 WHERE id=1')
    self.assertFalse(live_proxy.authorize(self.dbfile,self.token,'cam'))
    with db:db.execute('UPDATE cameras SET enabled=1 WHERE id=1')
    security.logout(db,self.token);db.close()
    self.assertFalse(live_proxy.authorize(self.dbfile,self.token,'cam'))
 async def test_changing_password_closes_an_existing_live_stream(self):
    reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
    writer.write(self.handshake());await writer.drain()
    self.assertTrue((await reader.readuntil(b'\r\n\r\n')).startswith(b'HTTP/1.1 101'))
    db=database.open_db(self.dbfile)
    uid=db.execute("SELECT id FROM users WHERE username='viewer'").fetchone()[0]
    security.change_own_password(db,uid,self.token,'viewer-test-password','viewer-new-password');db.close()
    self.assertEqual(await asyncio.wait_for(reader.read(1),3),b'')
    writer.close();await writer.wait_closed()

if __name__=='__main__':unittest.main()
