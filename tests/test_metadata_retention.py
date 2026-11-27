import sqlite3
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from sunhill_nvr import archive,database,security,metadata_retention as m,object_analytics as a,camera_events as e

def iso(seconds):return datetime.fromtimestamp(seconds,timezone.utc).isoformat()

class ArchiveMetadataTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        security.initialize(self.db);database.migrate(self.db);archive.initialize(self.db)
        self.guard=patch('sunhill_nvr.workers.writes_permitted',return_value=True);self.guard.start();self.addCleanup(self.guard.stop)
        with self.db:
            self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
            self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://192.168.1.2/main','rtsp')")
            self.db.execute("INSERT INTO cameras VALUES(2,'other','Other',1)")
            self.db.execute("INSERT INTO recordings VALUES(1,'cam/a.mp4','cam','1970-01-01',?,?,100,1,1,1)",(iso(100),iso(400)))
            self.db.execute("INSERT INTO recordings VALUES(2,'cam/b.mp4','cam','1970-01-01',?,NULL,100,1,1,1)",(iso(150),))
            for t in (50,100,150,249,250,399,400,499,500,99999):
                self.db.execute("INSERT INTO camera_metadata_history VALUES(1,?,'[]','s')",(t*1000,))
                self.db.execute("INSERT INTO event_markers(camera_id,started_at,ended_at,event_type,source) VALUES(1,?,?,'person','reolink')",(t,t))
            self.db.execute("INSERT INTO event_markers(camera_id,started_at,ended_at,event_type,source) VALUES(1,50,50,'person','home_assistant')")
            self.db.execute("INSERT INTO event_markers(camera_id,started_at,ended_at,event_type,source) VALUES(1,50,120,'tamper','nvr-tamper')")
            self.db.execute("INSERT INTO event_markers(camera_id,started_at,event_type,source) VALUES(1,50,'motion','reolink')")
            self.db.execute("INSERT INTO camera_metadata_history VALUES(2,50000,'[]','s')")
    def stamps(self,cid=1):return [r[0]//1000 for r in self.db.execute('SELECT stamp_ms FROM camera_metadata_history WHERE camera_id=? ORDER BY stamp_ms',(cid,))]
    def test_complete_keeps_overlaps_dst_active_and_recent(self):
        m.reconcile(self.db,guard=lambda:True,complete=True,now=100000)
        self.assertEqual(self.stamps(),[100,150,249,400,499,99999]);self.assertEqual(self.stamps(2),[])
        self.assertEqual(self.db.execute("SELECT count(*) FROM event_markers WHERE source IN ('home_assistant','nvr-tamper') OR ended_at IS NULL").fetchone()[0],3)
    def test_rotation_only_removed_interval_and_overlapping_recording(self):
        with self.db:self.db.execute('DELETE FROM recordings WHERE id=1')
        m.reconcile(self.db,guard=lambda:True,removed=[('cam',iso(100),iso(400),100)],now=100000)
        self.assertEqual(self.stamps(),[50,150,249,250,399,500,99999]);self.assertEqual(self.stamps(2),[50])
    def test_mount_change_rolls_back(self):
        previous=self.stamps()
        with self.assertRaises(RuntimeError):m.reconcile(self.db,guard=iter([True,False]).__next__,complete=True,now=100000)
        self.assertEqual(self.stamps(),previous)
        self.assertEqual(m.reconcile(self.db,guard=lambda:False,complete=True,now=100000),0)
    def test_all_types_and_no_limit(self):
        self.assertEqual(e.options(self.db,1)['types'],list(e.TYPES))
        with self.db:e.save_options(self.db,1,{'enabled':True,'types':['person']})
        self.assertEqual(e.options(self.db,1)['types'],list(e.TYPES));self.assertEqual(e.options(self.db,1)['retention_policy'],'archive');self.assertNotIn('retention_days',a.options(self.db,1))
        objects=[{'id':str(i),'kind':'person','box':[.1,.1,.1,.1]} for i in range(150)]
        self.assertTrue(a.snapshot(self.db,1,'rtsp://192.168.1.2/main',100000000,objects,'test',a.RuleEngine(),received=100000000))
        self.assertEqual(len(next(f for f in a.history(self.db,1,100000000) if f['stamp_ms']==100000000)['objects']),150)
