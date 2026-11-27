import socket,struct,threading,unittest
from unittest.mock import patch
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import onvif_metadata as m

XML=b'''<tt:MetadataStream xmlns:tt="http://www.onvif.org/ver10/schema"><tt:VideoAnalytics><tt:Frame UtcTime="2026-11-26T16:30:00Z"><tt:Object ObjectId="42"><tt:Appearance><tt:Shape><tt:BoundingBox left="-0.5" right="0.5" top="0.5" bottom="-0.5"/></tt:Shape><tt:Class><tt:Type Likelihood="0.9">Human</tt:Type></tt:Class></tt:Appearance></tt:Object></tt:Frame></tt:VideoAnalytics></tt:MetadataStream>'''
SDP=b'v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\nm=application 0 RTP/AVP 98\r\na=rtpmap:98 vnd.onvif.metadata/90000\r\na=control:trackID=2\r\n'
def packet(seq,body,marker=False):return struct.pack('!BBHII',128,98|(128 if marker else 0),seq,123,456)+body
class MetadataTests(unittest.TestCase):
 def test_parser_and_rtp(self):
    stamp,objects=list(m.parse_document(XML))[0];self.assertEqual(objects,[{'id':'42','kind':'person','box':[.25,.25,.5,.5]}]);self.assertGreater(stamp,0)
    r=m.RTPXML(98);self.assertIsNone(r.feed(packet(1,XML[:100])));self.assertEqual(r.feed(packet(2,XML[100:],True)),XML)
    r.feed(packet(3,b'bad'));self.assertIsNone(r.feed(packet(5,XML,True)));self.assertEqual(r.feed(packet(6,XML,True)),XML)
    with self.assertRaises(m.MetadataError):list(m.parse_document(b'<!DOCTYPE evil>'+XML))
    with self.assertRaises(m.MetadataError):m.metadata_track(SDP.replace(b'trackID=2',b'rtsp://192.168.1.99:554/a'),'rtsp://192.168.1.2:554/main','192.168.1.2',554)
 def test_native_plate_descriptor(self):
    descriptor=b'<tt:LicensePlateInfo><tt:PlateNumber Likelihood="0.2">LOW</tt:PlateNumber><tt:PlateNumber Likelihood="0.95">KR 12345</tt:PlateNumber><tt:PlateNumber Likelihood="2">BAD</tt:PlateNumber></tt:LicensePlateInfo>'
    _,objects=list(m.parse_document(XML.replace(b'</tt:Appearance>',descriptor+b'</tt:Appearance>')))[0]
    self.assertEqual(objects[0]['plate_number'],'KR 12345');self.assertEqual(objects[0]['plate_confidence'],.95)
 def test_rtsp_session_digest_and_interleaved_rtp(self):
    client,server=socket.socketpair();server.settimeout(5);stop=threading.Event();received=[];errors=[];methods=[]
    def serve():
      try:
        with server,server.makefile('rb') as f:
          for i in range(4):
            first=f.readline().decode().strip();method=first.split()[0];methods.append(method);headers={}
            while (line:=f.readline().decode().strip()):k,v=line.split(':',1);headers[k.lower()]=v.strip()
            response={'CSeq':headers['cseq']};body=b'';status='200 OK'
            if i==0:status='401 Unauthorized';response['WWW-Authenticate']='Digest realm="Camera", nonce="test", qop="auth"'
            else:
              assert headers.get('authorization','').startswith('Digest ')
              if i==1:body=SDP;response['Content-Base']='rtsp://192.168.1.2:554/main/'
              elif i==2:assert 'trackID=2' in first;response.update(Session='abc',Transport='RTP/AVP/TCP;unicast;interleaved=0-1')
              else:assert headers.get('session')=='abc'
            response['Content-Length']=str(len(body));server.sendall(('RTSP/1.0 '+status+'\r\n'+''.join(k+': '+v+'\r\n' for k,v in response.items())+'\r\n').encode()+body)
          for p in (packet(1,XML[:100]),packet(2,XML[100:],True)):server.sendall(b'$\x00'+struct.pack('!H',len(p))+p)
          stop.wait(5)
      except Exception as exc:errors.append(exc);stop.set()
    t=threading.Thread(target=serve);t.start()
    def emit(stamp,objects):received.extend(objects);stop.set()
    try:
      with patch.object(m.socket,'create_connection',return_value=client):
        with m.Client('rtsp://user:password@192.168.1.2/main') as c:c.run(stop,emit,lambda:None)
    finally:stop.set();t.join(6)
    self.assertFalse(errors);self.assertEqual(methods,['DESCRIBE','DESCRIBE','SETUP','PLAY']);self.assertEqual(received[0]['id'],'42')
