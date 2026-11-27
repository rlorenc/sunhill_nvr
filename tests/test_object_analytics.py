import sqlite3,unittest
from unittest.mock import patch
from sunhill_nvr import object_analytics as a,database,security,archive,native_capabilities as caps

class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        security.initialize(self.db);database.migrate(self.db);archive.initialize(self.db)
        self.url='rtsp://192.168.1.2/main'
        self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,?,'rtsp')",(self.url,))
        self.admin=security.create_user(self.db,'admin','password-long','admin');self.db.commit()
        guard=patch('sunhill_nvr.workers.writes_permitted',return_value=True);guard.start();self.addCleanup(guard.stop)
    def test_native_only(self):
        self.assertEqual(caps.supported(self.db,1),set())
        obj={'id':'1','kind':'person','box':[.1,.2,.3,.4]}
        self.assertFalse(a.snapshot(self.db,1,self.url,1000,[obj],'local',received=1000,source='local'))
        self.assertEqual(a.history(self.db,1,1000),[])
        self.assertTrue(a.snapshot(self.db,1,self.url,1000,[obj],'cam',received=1000))
        self.assertEqual(caps.supported(self.db,1),{'boxes','person'})
        self.assertEqual(a.history(self.db,1,1000)[0]['objects'],[obj]);self.assertIn('1',a.live(self.db,(self.admin,'admin','admin'),now=1000))
        self.assertEqual(a.live(self.db,(self.admin,'admin','admin'),now=6000),{})
        self.assertEqual(a.live(self.db,(999,'viewer','viewer'),now=1000),{})
        self.db.execute("INSERT INTO camera_metadata_history VALUES(1,1500,'[]','unverified-old')");self.db.commit()
        self.assertEqual(len(a.history(self.db,1,1000)),1)
        plate={'id':'2','kind':'plate','box':[.1,.2,.3,.4]}
        self.assertTrue(a.snapshot(self.db,1,self.url,2000,[plate],'cam',received=2000));self.assertNotIn('plate',caps.supported(self.db,1))
        plate['plate_number']='KR12345';self.assertTrue(a.snapshot(self.db,1,self.url,3000,[plate],'cam',received=3000));self.assertIn('plate',caps.supported(self.db,1))
        self.assertEqual(self.db.execute('SELECT event_type,source FROM event_markers').fetchall(),[('plate','camera-plate')])
        self.db.execute("UPDATE camera_details SET main_url='rtsp://192.168.1.3/main' WHERE camera_id=1");self.db.commit();self.assertEqual(caps.supported(self.db,1),set())
    def test_reject_local_mode_and_spatial_rules(self):
        for invalid in ({'enabled':True,'rules':[],'source_mode':'local'},{'enabled':True,'rules':[{'type':'line_cross'}]}):
            with self.assertRaises(ValueError):a.save_options(self.db,1,invalid)
    def test_no_local_fallback_after_unavailable_stream(self):
        import tempfile,threading
        from pathlib import Path
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db.sqlite';other=sqlite3.connect(path);self.db.backup(other);other.close()
            stop=threading.Event();cfg=SimpleNamespace(database=path)
            with patch('sunhill_nvr.onvif_metadata.Client',side_effect=OSError('no metadata')),patch.object(stop,'wait',side_effect=lambda *_:stop.set()),patch('subprocess.Popen',side_effect=AssertionError('decoder invoked')):
                a.run_camera(cfg,1,self.url,stop)
