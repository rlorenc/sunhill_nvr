import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.camera_reboot import reboot, target, RebootError
from sunhill_nvr.camera_discovery import SOAP, DEVICE

class CameraRebootTests(unittest.TestCase):
    config=('rtsp://192.168.1.5/main','http://192.168.1.5/onvif/device_service','admin','secret','digest')
    def test_explicit_soap_response_and_no_retry(self):
        valid=ET.fromstring(f'<s:Envelope xmlns:s="{SOAP}" xmlns:d="{DEVICE}"><s:Body><d:SystemRebootResponse/></s:Body></s:Envelope>')
        with patch('sunhill_nvr.camera_reboot.soap',return_value=valid) as soap:
            reboot(self.config);soap.assert_called_once_with(self.config[1],'192.168.1.5','admin','secret',DEVICE,'SystemReboot',mode='digest')
        with patch('sunhill_nvr.camera_reboot.soap',side_effect=TimeoutError()) as soap:
            with self.assertRaises(RebootError):reboot(self.config)
            soap.assert_called_once()
        with patch('sunhill_nvr.camera_reboot.soap',return_value=ET.fromstring('<x/>')) as soap:
            with self.assertRaises(RebootError):reboot(self.config)
            soap.assert_called_once()
    def test_target_confined(self):
        for endpoint in ('http://192.168.1.6/onvif/device_service','http://192.168.1.5/other','http://127.0.0.1/onvif/device_service','http://192.168.1.5@192.168.1.6/onvif/device_service','http://192.168.1.5/onvif/device_service?x=1'):
            with self.assertRaises(RebootError):target(self.config[0],endpoint,*self.config[2:])
