import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import camera_discovery as d

class DiscoveryTests(unittest.TestCase):
    def test_private_ip_and_credentials(self):
        for ip in ('127.0.0.1','169.254.169.254','8.8.8.8','example.com','192.168.1.2:80','::1'):
            with self.assertRaises(d.DiscoveryError):d.validate_input(ip)
        self.assertEqual(d.validate_input('192.168.0.10','user',''), '192.168.0.10')
        self.assertEqual(d.authenticated_rtsp('rtsp://192.168.0.10/main','192.168.0.10','u@a','p:/#'),'rtsp://u%40a:p%3A%2F%23@192.168.0.10/main')
        with self.assertRaises(ValueError):d.same_camera('http://192.168.0.11/service','192.168.0.10',('http',))
    def test_xml_rejects_dtd_and_fault(self):
        for xml in (b'<!DOCTYPE x [<!ENTITY a "foo">]><x>&a;</x>',b'\x00<x/>',b'<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope"><Body><Fault/></Body></Envelope>'):
            with self.assertRaises(ValueError):d.parse_xml(xml)
    def test_profiles_rank_and_get_uri(self):
        def soap(endpoint,ip,user,pw,ns,operation,fill=None,mode='digest'):
            if operation=='GetCapabilities':return ET.fromstring('<x><Media><XAddr>http://192.168.0.10:8000/onvif/media</XAddr></Media></x>')
            if operation=='GetProfiles':return ET.fromstring('<x><Profiles token="small"><VideoEncoderConfiguration><Resolution><Width>640</Width><Height>360</Height></Resolution></VideoEncoderConfiguration></Profiles><Profiles token="large"><VideoEncoderConfiguration><Resolution><Width>1920</Width><Height>1080</Height></Resolution></VideoEncoderConfiguration></Profiles></x>')
            node=ET.Element('request');fill(node);token=d.local(node,'ProfileToken').text
            return ET.fromstring(f'<x><Uri>rtsp://192.168.0.10/{token}</Uri></x>')
        with patch.object(d,'soap',side_effect=soap):r=d.profiles('192.168.0.10','u','p','digest',8000)
        self.assertEqual(r.main,'rtsp://u:p@192.168.0.10/large');self.assertEqual(r.sub,'rtsp://u:p@192.168.0.10/small')
    def test_onvif_verified_main_and_optional_sub(self):
        with patch.object(d,'profiles',return_value=d.Streams('rtsp://192.168.0.10/main','rtsp://192.168.0.10/sub')),patch.object(d,'probe',side_effect=lambda url:url.endswith('/main')):
            r=d.discover('192.168.0.10');self.assertEqual(r.method,'onvif');self.assertIsNone(r.sub)
    def test_vendor_fallback_reolink_and_failure(self):
        with patch.object(d,'profiles',side_effect=ValueError),patch('sunhill_nvr.onvif_capabilities.streams',side_effect=ValueError),patch.object(d,'probe',side_effect=lambda url:'h264Preview' in url):
            r=d.discover('192.168.0.10');self.assertEqual(r.method,'rtsp');self.assertTrue(r.main.endswith('h264Preview_01_main'));self.assertTrue(r.sub.endswith('h264Preview_01_sub'))
        with patch.object(d,'profiles',side_effect=ValueError),patch('sunhill_nvr.onvif_capabilities.streams',side_effect=ValueError),patch.object(d,'probe',return_value=False):
            with self.assertRaises(d.DiscoveryError):d.discover('192.168.0.10')
    def test_worker_timeout_redacts_credentials(self):
        import subprocess
        with patch.object(d.subprocess,'run',side_effect=subprocess.TimeoutExpired('secret',27)):
            with self.assertRaises(d.DiscoveryError) as e:d.discover_bounded('192.168.0.10','u','secret')
        self.assertNotIn('secret',str(e.exception))
