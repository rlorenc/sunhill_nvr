import asyncio,base64,struct,unittest
from unittest.mock import patch
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import talkback as t, onvif_metadata as rtsp

SDP=b'v=0\r\nm=audio 0 RTP/AVP 0\r\na=recvonly\r\na=control:listen\r\nm=audio 0 RTP/AVP 97\r\na=rtpmap:97 PCMA/8000/1\r\na=sendonly\r\na=control:talk\r\n'
BASE='rtsp://192.168.1.2:554/main/'

class BackchannelTests(unittest.TestCase):
    def test_only_explicit_backchannel_and_same_endpoint(self):
        self.assertEqual(t.audio_track(SDP,BASE,'192.168.1.2',554),(BASE+'talk',97,'PCMA'))
        for bad in (SDP.replace(b'sendonly',b'recvonly'), SDP.replace(b'PCMA/8000',b'AAC/48000'),SDP.replace(b'control:talk',b'control:rtsp://192.168.1.3/talk')):
            with self.assertRaises(ValueError):t.audio_track(bad,BASE,'192.168.1.2',554)
    def test_rtp_g711_sequence_and_payload(self):
        c=t.Backchannel('rtsp://user:pass@192.168.1.2/main');c.codec='PCMU';c.pt=0;c.channel=4;c.seq=65535;c.timestamp=0xffffffff-79
        frames=[];c.interleaved=lambda ch,packet:frames.append((ch,packet))
        c.send_pcm(bytes(320));c.send_pcm(bytes(320))
        self.assertEqual(frames[0][0],4);self.assertEqual(frames[0][1][12:],bytes([255])*160)
        self.assertEqual(struct.unpack('!BBHII',frames[1][1][:12])[:4],(128,0,0,80))
        self.assertEqual(t.alaw(0),0xd5);self.assertEqual(t.alaw(-1),0x55)
        with self.assertRaises(ValueError):c.send_pcm(bytes(319))
    def test_negotiation_and_no_unsupported_transport(self):
        c=t.Backchannel('rtsp://user:pass@192.168.1.2/main');calls=[]
        def request(method,uri,headers=None):
            calls.append((method,uri,headers));return ({'content-base':BASE},SDP) if method=='DESCRIBE' else ({'session':'session1','transport':'RTP/AVP/TCP;unicast;interleaved=4-5'},b'')
        c.request=request;c.start();self.assertEqual([i[0] for i in calls],['DESCRIBE','SETUP','PLAY']);self.assertEqual(c.channel,4)
        self.assertTrue(all(i[2]['Require']=='www.onvif.org/ver20/backchannel' for i in calls))
        self.assertEqual(c.codec,'PCMA')

class WebsocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_masked_pcm_and_unmasked_rejection(self):
        mask=b'abcd';body=bytes(range(160))*2
        reader=asyncio.StreamReader();reader.feed_data(b'\x82\xfe'+struct.pack('!H',len(body))+mask+bytes(x^mask[i%4] for i,x in enumerate(body)))
        self.assertEqual(await t.frame(reader),(2,body))
        reader=asyncio.StreamReader();reader.feed_data(b'\x82\x00')
        with self.assertRaises(ValueError):await t.frame(reader)
    async def test_real_websocket_relay_and_origin_rejection(self):
        received=[];finished=[];active=set();semaphore=asyncio.Semaphore(4)
        class Camera:
            def __init__(self,url):pass
            def __enter__(self):return self
            def start(self):pass
            def send_pcm(self,raw):received.append(raw)
            def finish(self):finished.append(True)
        async def accept(reader,writer):
            await t.handle(reader,writer,origin='https://nvr.example',authorize_fn=lambda token,slug:(1,'rtsp://192.168.1.2/main') if token=='valid' and slug=='cam' else None,active=active,semaphore=semaphore,client_factory=Camera)
        server=await asyncio.start_server(accept,'127.0.0.1',0);port=server.sockets[0].getsockname()[1]
        async def connect(origin,token='valid'):
            reader,writer=await asyncio.open_connection('127.0.0.1',port)
            key=base64.b64encode(b'1234567890123456').decode()
            writer.write(f'GET /api/talk?src=cam HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\nOrigin: {origin}\r\nCookie: sunhill_session={token}\r\n\r\n'.encode());await writer.drain()
            return reader,writer,await reader.readuntil(b'\r\n\r\n')
        try:
            for origin,token in [('https://evil.example','valid'),('https://nvr.example','bad')]:
                reader,writer,headers=await connect(origin,token);self.assertIn(b'403 Forbidden',headers);writer.close();await writer.wait_closed()
            reader,writer,headers=await connect('https://nvr.example');self.assertIn(b'101 Switching',headers);self.assertEqual(await reader.readexactly(7),b'\x81\x05ready')
            body=bytes(320);mask=b'abcd';writer.write(b'\x82\xfe'+struct.pack('!H',320)+mask+bytes(mask[i%4] for i in range(320))+b'\x88\x80'+mask);await writer.drain()
            self.assertEqual(await reader.readexactly(4),b'\x88\x02\x03\xe8');self.assertEqual(received,[body]);self.assertEqual(finished,[True]);self.assertFalse(active)
            writer.close();await writer.wait_closed()
        finally:server.close();await server.wait_closed()
