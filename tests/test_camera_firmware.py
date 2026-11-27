import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import xml.etree.ElementTree as ET
from sunhill_nvr import camera_firmware as f
CONFIG=('rtsp://192.168.1.10/main','http://192.168.1.10/onvif/device_service','u','p','digest')
EXPECTED={'Manufacturer':'Vendor','Model':'Cam','HardwareId':'R1'}
IDENTITY={**EXPECTED,'FirmwareVersion':'old'}
class FirmwareTests(unittest.TestCase):
    def test_hash_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'fw';p.write_bytes(b'firmware');h=hashlib.sha256(b'firmware').hexdigest();self.assertEqual(f.image(p,h),b'firmware')
            with self.assertRaises(f.FirmwareError):f.image(p,'0'*64)
            link=Path(tmp)/'link';link.symlink_to(p)
            with self.assertRaises(OSError):f.image(link,h)
    def test_identity_mismatch_never_starts_upgrade(self):
        with patch.object(f,'identity',return_value={**IDENTITY,'HardwareId':'R2'}),patch.object(f,'soap') as soap:
            with self.assertRaises(f.FirmwareError):f.upload(CONFIG,b'firmware',EXPECTED)
            soap.assert_not_called()
    def test_cross_host_upload_rejected(self):
        with patch.object(f,'identity',return_value=IDENTITY),patch.object(f,'soap',return_value=ET.fromstring('<x><StartFirmwareUpgradeResponse><UploadUri>http://192.168.1.11/upload</UploadUri></StartFirmwareUpgradeResponse></x>')),patch.object(f.urllib.request,'build_opener') as opener:
            with self.assertRaises(f.FirmwareError):f.upload(CONFIG,b'firmware',EXPECTED)
            opener.assert_not_called()
    def test_post_only_after_delay_and_matching_identity(self):
        response=MagicMock();response.__enter__.return_value.status=200;opener=MagicMock();opener.open.return_value=response
        with patch.object(f,'identity',return_value=IDENTITY),patch.object(f,'soap',return_value=ET.fromstring('<x><StartFirmwareUpgradeResponse><UploadUri>http://192.168.1.10/upload</UploadUri><UploadDelay>PT2S</UploadDelay></StartFirmwareUpgradeResponse></x>')),patch.object(f.urllib.request,'build_opener',return_value=opener),patch.object(f.time,'sleep') as sleep:
            self.assertEqual(f.upload(CONFIG,b'firmware',EXPECTED),IDENTITY);sleep.assert_called_once_with(2);req=opener.open.call_args.args[0];self.assertEqual(req.data,b'firmware');self.assertEqual(req.get_method(),'POST');self.assertEqual(req.get_header('Content-type'),'application/octet-stream')

class VerificationTests(unittest.TestCase):
    def test_version_verification_after_offline_and_old_version(self):
        now=[0];reads=iter([f.FirmwareError('offline'),IDENTITY,{**IDENTITY,'FirmwareVersion':'new'}])
        def read(config):
            value=next(reads)
            if isinstance(value,Exception):raise value
            return value
        result=f.verify_installed(CONFIG,EXPECTED,'new',read=read,monotonic=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n))
        self.assertEqual(result['FirmwareVersion'],'new');self.assertEqual(now[0],10)
    def test_timeout_and_identity_change(self):
        self.assertIsNone(f.verify_installed(CONFIG,EXPECTED,'new',timeout=0,read=lambda c:IDENTITY))
        with self.assertRaises(f.FirmwareError):f.verify_installed(CONFIG,EXPECTED,'new',read=lambda c:{**IDENTITY,'HardwareId':'R2'})
