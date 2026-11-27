import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from sunhill_nvr import onvif_capabilities as o, camera_discovery as d

CONFIG=('rtsp://192.168.1.10/main','http://192.168.1.10/onvif/device_service','u','p','digest')
SERVICES=f'<x><Service><Namespace>{o.MEDIA2}</Namespace><XAddr>http://192.168.1.10/media2</XAddr></Service><Service><Namespace>{o.EVENTS}</Namespace><XAddr>http://192.168.1.11/events</XAddr></Service></x>'
PROFILES='<x><Profiles token="main"><Configurations><VideoEncoder><Encoding>H265</Encoding><Resolution><Width>1920</Width><Height>1080</Height></Resolution></VideoEncoder><Metadata/><AudioSource/><AudioOutput/><AudioDecoder/></Configurations></Profiles><Profiles token="sub"><Configurations><VideoEncoder><Encoding>H264</Encoding><Resolution><Width>640</Width><Height>360</Height></Resolution></VideoEncoder></Configurations></Profiles></x>'

class CapabilityTests(unittest.TestCase):
    def fake(self,endpoint,ip,user,password,namespace,operation,fill=None,mode='digest'):
        self.assertEqual(ip,'192.168.1.10');self.assertEqual((user,password),('u','p'))
        if operation=='GetServices':return ET.fromstring(SERVICES)
        self.assertEqual(endpoint,'http://192.168.1.10/media2')
        if operation=='GetProfiles':return ET.fromstring(PROFILES)
        node=ET.Element('req');fill(node);token=d.local(node,'ProfileToken').text
        return ET.fromstring(f'<x><Uri>rtsp://192.168.1.10/{token}</Uri></x>')
    def test_discovery_and_foreign_endpoint(self):
        with patch.object(o,'soap',side_effect=self.fake):
            result=o.inspect(CONFIG);streams=o.streams(CONFIG)
        self.assertTrue(result['media2']);self.assertFalse(result['events']);self.assertFalse(result['media2_error'])
        self.assertEqual(result['profiles'][0]['encoding'],'H265');self.assertTrue(result['profiles'][0]['audio_decoder']);self.assertTrue(result['profiles'][0]['metadata'])
        self.assertEqual(streams.main,'rtsp://u:p@192.168.1.10/main');self.assertEqual(streams.sub,'rtsp://u:p@192.168.1.10/sub');self.assertEqual(streams.method,'onvif-media2')
    def test_no_claim_when_profiles_fail(self):
        with patch.object(o,'soap',side_effect=[ET.fromstring(SERVICES),ValueError()]):result=o.inspect(CONFIG)
        self.assertTrue(result['media2_error']);self.assertEqual(result['profiles'],[])
    def test_discovery_fallback(self):
        with patch.object(d,'profiles',side_effect=ValueError),patch.object(o,'streams',return_value=d.Streams('rtsp://192.168.1.10/main',None,'onvif-media2')),patch.object(d,'probe',return_value=True):
            self.assertEqual(d.discover('192.168.1.10').method,'onvif-media2')

class MetadataProfileTests(unittest.TestCase):
    def test_metadata_only_profile_must_share_video_source_config(self):
        profiles='''<x><Profiles token="main"><Configurations><VideoSource token="source-a"/><VideoEncoder><Encoding>H264</Encoding><Resolution><Width>1920</Width><Height>1080</Height></Resolution></VideoEncoder></Configurations></Profiles><Profiles token="analytics"><Configurations><VideoSource token="source-a"/><Metadata/></Configurations></Profiles><Profiles token="different"><Configurations><VideoSource token="source-b"/><Metadata/></Configurations></Profiles></x>'''
        def fake(endpoint,ip,user,password,namespace,operation,fill=None,mode='digest'):
            if operation=='GetServices':return ET.fromstring(SERVICES)
            if operation=='GetProfiles':return ET.fromstring(profiles)
            node=ET.Element('req');fill(node);token=d.local(node,'ProfileToken').text
            return ET.fromstring(f'<x><Uri>rtsp://192.168.1.10/{token}</Uri></x>')
        with patch.object(o,'soap',side_effect=fake):
            self.assertEqual(o.metadata_streams(CONFIG),['rtsp://u:p@192.168.1.10/analytics'])
            self.assertEqual(o.streams(CONFIG).main,'rtsp://u:p@192.168.1.10/main')
            self.assertEqual(o.metadata_streams(('rtsp://192.168.1.10/other',*CONFIG[1:])),[])
