import datetime as dt,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.onvif_time import *
class ONVIFTests(unittest.TestCase):
 def xml(self):return b'<Envelope><UTCDateTime><Time><Hour>12</Hour><Minute>30</Minute><Second>0</Second></Time><Date><Year>2026</Year><Month>11</Month><Day>21</Day></Date></UTCDateTime></Envelope>'
 def test_clock_uses_utc_and_midpoint(self):
    timestamp=dt.datetime(2026,11,21,12,30,tzinfo=dt.timezone.utc).timestamp()
    result=compare(self.xml(),timestamp-11,timestamp-9)
    self.assertEqual(result.offset_seconds,10);self.assertEqual(result.measurement_seconds,2)
 def test_unsafe_xml_and_high_latency_rejected(self):
    with self.assertRaises(ValueError):parse_response(b'<!DOCTYPE a [<!ENTITY x "bad">]><a/>')
    with self.assertRaises(ValueError):compare(self.xml(),0,10,max_latency=5)
 def test_unrecognized_encodings_and_extreme_year_are_rejected_cleanly(self):
    for encoding in ('nonesuch','utf-32'):
        body=('<?xml version="1.0" encoding="'+encoding+'"?>').encode()+self.xml()
        with self.subTest(encoding=encoding),self.assertRaises(ValueError):parse_response(body)
    with self.assertRaises(ValueError):parse_response(self.xml().replace(b'2026',b'999999999999999999999999999999'))
 def test_truncated_http_response_is_reported_as_camera_error(self):
    import http.client
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):raise http.client.IncompleteRead(b'partial')
    class Opener:
        def open(self,*args,**kwargs):return Response()
    with self.assertRaises(ValueError):query('http://192.0.2.1/onvif/device_service',opener_factory=lambda *a:Opener())
 def test_utf16_entities_are_rejected_but_regular_response_is_supported(self):
    plain=self.xml().decode('ascii')
    dangerous='<!DOCTYPE Envelope [<!ENTITY n "2026">]>'+plain.replace('2026','&n;')
    for encoding in ('utf-16','utf-16-le','utf-16-be'):
        with self.subTest(encoding=encoding):
            with self.assertRaises(ValueError):parse_response(dangerous.encode(encoding))
            self.assertEqual(parse_response(plain.encode(encoding)).year,2026)
    legacy='<?xml version="1.0" encoding="iso-8859-1"?>'+plain.replace('</Envelope>','<Extra>café</Extra></Envelope>')
    self.assertEqual(parse_response(legacy.encode('iso-8859-1')).year,2026)
 def test_wsse_digest_matches_onvif_reference_vector_and_escapes_username(self):
    import base64,xml.etree.ElementTree as ET
    nonce=base64.b64decode('LKqI6G/AikKCQrN0zqZFlg==')
    body=wsse_request('user<&','userpassword',nonce=nonce,created='2010-09-16T07:50:45Z')
    self.assertNotIn(b'userpassword',body);root=ET.fromstring(body)
    values={item.tag.rsplit('}',1)[-1]:item.text for item in root.iter()}
    self.assertEqual(values['Username'],'user<&');self.assertEqual(values['Password'],'tuOSpGlFlIXsozq4HFNeeGeFLEI=')
    self.assertEqual(values['Nonce'],'LKqI6G/AikKCQrN0zqZFlg==')
 def test_wsse_mode_sends_digest_only_when_explicitly_selected(self):
    from unittest.mock import patch
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return self_body
    class Opener:
        def open(self,request,timeout):requests.append(request);return Response()
    self_body=self.xml();requests=[];timestamp=dt.datetime(2026,11,21,12,30,tzinfo=dt.timezone.utc).timestamp()
    with patch('time.time',side_effect=[timestamp,timestamp]):
        query('http://192.0.2.1/onvif/device_service','admin','secret',auth_mode='wsse',opener_factory=lambda *a:Opener())
    self.assertIn(b'UsernameToken',requests[0].data);self.assertNotIn(b'secret',requests[0].data)
    with self.assertRaises(ValueError):query('http://192.0.2.1/onvif/device_service',auth_mode='wsse')
 def test_endpoint_validation_and_response(self):
    from unittest.mock import patch
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return self.body
    class Opener:
        def open(self,request,timeout):return response
    response=Response();response.body=self.xml()
    timestamp=dt.datetime(2026,11,21,12,30,tzinfo=dt.timezone.utc).timestamp()
    with patch('time.time',side_effect=[timestamp-1,timestamp+1]):
        result=query('http://192.0.2.1:8000/onvif/device_service',opener_factory=lambda *a:Opener())
    self.assertEqual(result.offset_seconds,0)
    with self.assertRaises(ValueError):query('http://user:password@192.0.2.1/onvif/device_service')
 def test_onvif_rejects_invalid_port_whitespace_and_backslashes(self):
    for url in ('http://cam:0/onvif/device_service','http://cam:65536/onvif/device_service','http://cam:xyz/onvif/device_service','http://ca m/onvif/device_service','http://cam\\evil/onvif/device_service'):
        with self.subTest(url=url),self.assertRaises(ValueError):validate_endpoint(url)
    validate_endpoint('http://[::1]:8000/onvif/device_service')
if __name__=='__main__':unittest.main()
