import unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.config import parse
class ConfigTests(unittest.TestCase):
 def good(self):return {'hostname':'nvr.example.com','database':'/var/lib/sunhill/data.sqlite','archive':'/home/store/kamery','staging':'/home/store/staging','activity_socket':'/run/sunhill/activity.sock','control_state':'/opt/sunhill-nvr/control','exports':'/var/lib/sunhill/exports','archive_lock':'/var/lib/sunhill/archive.lock','timezone':'Europe/Warsaw','minimum_free':10_000_000_000,'free_buffer':5_000_000_000}
 def test_valid(self):
    c=parse(self.good());self.assertEqual(c.origin,'https://nvr.example.com');self.assertEqual(c.minimum_free,10_000_000_000)
 def test_hostname_is_normalized_for_browser_origins(self):
    raw=self.good();raw['hostname']='KaMeRy.SlOneCZNeWzgORZe4.Pl'
    cfg=parse(raw)
    self.assertEqual(cfg.hostname,'kamery.slonecznewzgorze4.pl')
    self.assertEqual(cfg.origin,'https://kamery.slonecznewzgorze4.pl')
 def test_invalid_root_or_limits(self):
    for key,value in [('archive','/'),('archive','/home/../etc'),('hostname','example.com;foo'),('minimum_free',True),('timezone','Fake/Zone')]:
        c=self.good();c[key]=value
        with self.assertRaises(ValueError):parse(c)
 def test_nested_archive_and_export_rejected(self):
    c=self.good();c['exports']='/home/store/kamery/tmp/export'
    with self.assertRaises(ValueError):parse(c)
 def test_nested_auxiliary_directories(self):
    c=self.good();c.update(staging='/home/store/kamery/.sunhill-staging/',exports='/home/store/kamery/.sunhill-exports/')
    cfg=parse(c);self.assertEqual(cfg.staging,Path('/home/store/kamery/.sunhill-staging'));self.assertEqual(cfg.exports,Path('/home/store/kamery/.sunhill-exports'))
    for key,value in [('staging',c['exports']),('exports','/home/store'),('exports','/home/store/kamery/.sunhill-volume-id'),('staging','/home/store/kamery/cam'),('exports','/home/store/kamery/.sunhill-staging/sub')]:
        with self.subTest(key=key,value=value):
            bad=dict(c);bad[key]=value
            with self.assertRaises(ValueError):parse(bad)
 def test_media_and_private_state_must_not_overlap(self):
    for key,value in [('database','/home/store/kamery/db.sqlite'),('archive_lock','/home/store/kamery/lock'),('control_state','/home/store/staging/control'),('activity_socket','/var/lib/sunhill/exports/activity.sock'),('archive','/var/lib/sunhill'),('control_state','/home/store'),('archive_lock','/var/lib/sunhill/data.sqlite'),('control_state','/var/lib/sunhill')]:
        with self.subTest(key=key,value=value):
            raw=self.good();raw[key]=value
            with self.assertRaises(ValueError):parse(raw)
if __name__=='__main__':unittest.main()
