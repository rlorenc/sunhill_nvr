import unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.monitor import *
from sunhill_nvr.home_assistant import build_request

class MonitoringTests(unittest.TestCase):
 def test_separates_missing_recording_and_network_offline(self):
    a=camera_status(enabled=True,reachable=True,last_recording=80,now=100,stale_after=10,started=0)
    b=camera_status(enabled=True,reachable=False,last_recording=80,now=100,stale_after=10,started=0)
    self.assertEqual(a.state,State.STALE);self.assertEqual(b.state,State.OFFLINE)
 def test_reenabled_camera_gets_grace_period_despite_old_archive(self):
    status=camera_status(enabled=True,reachable=True,last_recording=100,now=1005,stale_after=20,started=1000)
    self.assertEqual(status.state,State.WAITING);self.assertEqual(status.last_recording,100)
    status=camera_status(enabled=True,reachable=True,last_recording=100,now=1021,stale_after=20,started=1000)
    self.assertEqual(status.state,State.STALE)
    status=camera_status(enabled=True,reachable=True,last_recording=1006,now=1007,stale_after=20,started=1000)
    self.assertEqual(status.state,State.RECORDING)
 def test_only_state_transitions_notify(self):
    self.assertTrue(should_notify(State.RECORDING,State.STALE));self.assertTrue(should_notify(State.STALE,State.OFFLINE))
    self.assertTrue(should_notify(State.STALE,State.CLOCK_SKEW));self.assertFalse(should_notify(State.CLOCK_SKEW,State.CLOCK_SKEW))
    self.assertTrue(should_notify(State.STALE,State.RECORDING))
 def test_ha_url_and_header_validation(self):
    request=build_request('http://192.0.2.10:8123','token',camera_id='brama',state='stale')
    self.assertEqual(request.full_url,'http://192.0.2.10:8123/api/events/sunhill_nvr')
    with self.assertRaises(ValueError):build_request('http://example.com/path','token',camera_id='x',state='stale')
    with self.assertRaises(ValueError):build_request('http://example.com','bad\r\nheader',camera_id='x',state='stale')
 def test_home_assistant_address_ports_and_token_are_validated_before_saving(self):
    for base in ('http://host:0','http://host:65536','http://host:notaport','http://ho st:8123','http://host\\attacker','http://host\n:8123'):
        with self.subTest(base=base),self.assertRaises(ValueError):build_request(base,'token',camera_id='cam',state='stale')
    for token in ('abc\x00def','bad token','unicode-ż'):
        with self.subTest(token=token),self.assertRaises(ValueError):build_request('http://192.0.2.10:8123',token,camera_id='cam',state='stale')
    build_request('http://[::1]:8123','valid.jwt_token-123',camera_id='cam',state='stale')
if __name__=='__main__':unittest.main()
