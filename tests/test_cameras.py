import json,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.cameras import Camera,validate,go2rtc_config,with_credentials
class CameraTests(unittest.TestCase):
 def test_private_config_and_secrets_not_in_repr(self):
    c=Camera('brama','Brama','rtsp://admin:secret@192.0.2.3/main','rtsp://admin:secret@192.0.2.3/sub')
    self.assertNotIn('secret',repr(c));cfg=json.loads(go2rtc_config([c]))
    self.assertEqual(cfg['api']['listen'],'127.0.0.1:1984');self.assertEqual(cfg['rtsp']['listen'],'127.0.0.1:8554')
    self.assertEqual(list(cfg['streams']),['brama','brama_sub','~brama'])
    self.assertEqual(cfg['streams']['~brama'],'ffmpeg:brama#video=h264#audio=aac')
    self.assertEqual(cfg['streams']['brama'],'rtsp://admin:secret@192.0.2.3/main')
 def test_unsafe_types_and_yaml_injection(self):
    for url in ('exec:echo hi','http://example.com','rtsp://example.com/\napi: evil','rtsp://example.com:99999/x'):
        with self.assertRaises(ValueError):validate(Camera('cam','Cam',url))
 def test_duplicate_and_disabled(self):
    c=Camera('cam','Cam','rtsp://example.com/x');off=Camera('off','Off','rtsp://example.com/x',enabled=False)
    with self.assertRaises(ValueError):go2rtc_config([c,c])
    self.assertNotIn('off',json.loads(go2rtc_config([off]))['streams'])
    first=Camera('cam','Cam','rtsp://example.com/x','rtsp://example.com/sub')
    second=Camera('cam_sub','Cam sub','rtsp://example.com/x')
    with self.assertRaises(ValueError):go2rtc_config([first,second])
    self.assertEqual(json.loads(go2rtc_config([c]))['streams']['~cam'],'ffmpeg:cam#video=h264#audio=aac')
 def test_credentials_are_encoded_and_replace_existing_userinfo(self):
    url=with_credentials('rtsp://old:secret@[2001:db8::1]:554/stream?x=1','u@ser','p:a@ss%')
    self.assertEqual(url,'rtsp://u%40ser:p%3Aa%40ss%25@[2001:db8::1]:554/stream?x=1')
    with self.assertRaises(ValueError):with_credentials('http://host','user','pass')
    with self.assertRaises(ValueError):with_credentials('rtsp://host','user','')
if __name__=='__main__':unittest.main()
