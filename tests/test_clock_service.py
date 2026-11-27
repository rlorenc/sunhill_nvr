import sqlite3,unittest
from pathlib import Path
from sys import path
from types import SimpleNamespace
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import security,database,clock_service
class ClockTests(unittest.TestCase):
 def test_changed_onvif_settings_discard_inflight_result(self):
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode,onvif_url) VALUES(1,'rtsp://x','rtsp','http://x/onvif/device_service')");db.commit()
    def changed(*args,**kwargs):
        with db:db.execute("UPDATE camera_details SET onvif_url='http://new/onvif/device_service' WHERE camera_id=1")
        return SimpleNamespace(offset_seconds=90)
    clock_service.check(db,now=100,query=changed)
    self.assertIsNone(db.execute('SELECT clock_checked FROM camera_details').fetchone()[0]);self.assertEqual(db.execute('SELECT count(*) FROM pending_notifications').fetchone()[0],0)
    db.close()
 def test_checks_offsets_and_queues_alarm_recovery(self):
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode,onvif_url) VALUES(1,'rtsp://x','rtsp','http://x/onvif/device_service')");db.commit()
    clock_service.check(db,now=100,query=lambda *args,**kwargs:SimpleNamespace(offset_seconds=50))
    clock_service.check(db,now=200,query=lambda *args,**kwargs:SimpleNamespace(offset_seconds=0))
    self.assertEqual(db.execute('SELECT state,origin FROM pending_notifications ORDER BY id').fetchall(),[('clock_skew','onvif'),('clock_ok','onvif')]);db.close()
 def test_checked_timestamp_is_taken_after_each_camera_query(self):
    from unittest.mock import patch
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    for cid,slug in ((1,'cam'),(2,'cam2')):
        db.execute('INSERT INTO cameras VALUES(?,?,?,1)',(cid,slug,slug))
        db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode,onvif_url) VALUES(?,'rtsp://x','rtsp','http://x/onvif/device_service')",(cid,))
    db.commit()
    with patch('sunhill_nvr.clock_service.time.time',side_effect=[100,110,120]):
        clock_service.check(db,query=lambda *a,**k:SimpleNamespace(offset_seconds=50))
    self.assertEqual(db.execute('SELECT camera_id,clock_checked FROM camera_details ORDER BY camera_id').fetchall(),[(1,110),(2,120)])
    self.assertEqual(db.execute('SELECT created_at FROM pending_notifications ORDER BY id').fetchall(),[(110,),(120,)]);db.close()
 def test_failed_clock_replaces_old_alarm_and_recovers_without_duplicates(self):
    from sunhill_nvr.home_assistant import build_request
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode,onvif_url) VALUES(1,'rtsp://x','rtsp','http://x/onvif/device_service')");db.commit()
    clock_service.check(db,now=100,query=lambda *a,**k:SimpleNamespace(offset_seconds=50))
    def failed(*args,**kwargs):raise OSError('camera unavailable')
    clock_service.check(db,now=200,query=failed);clock_service.check(db,now=201,query=failed)
    self.assertEqual(db.execute("SELECT state FROM system_health WHERE name='clock:1'").fetchone()[0],'clock_unavailable')
    self.assertEqual(db.execute('SELECT clock_offset,clock_error FROM camera_details').fetchone(),(None,'Nie udało się odczytać czasu ONVIF'))
    clock_service.check(db,now=300,query=lambda *a,**k:SimpleNamespace(offset_seconds=float('nan')))
    clock_service.check(db,now=400,query=lambda *a,**k:SimpleNamespace(offset_seconds=0))
    states=[row[0] for row in db.execute('SELECT state FROM pending_notifications ORDER BY id')]
    self.assertEqual(states,['clock_skew','clock_unavailable','clock_ok'])
    for state in states:build_request('http://192.0.2.1:8123','token',camera_id='cam',state=state)
    db.close()
 def test_old_clock_error_is_reannounced_only_after_recheck(self):
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    with db:
        db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode,onvif_url,clock_checked) VALUES(1,'rtsp://x','rtsp','http://x/onvif/device_service',1)")
        db.execute("INSERT INTO system_health VALUES('clock:1','clock_skew',1)")
    clock_service.check(db,now=2000,query=lambda *a,**k:SimpleNamespace(offset_seconds=50))
    clock_service.check(db,now=2001,query=lambda *a,**k:SimpleNamespace(offset_seconds=50))
    self.assertEqual(db.execute('SELECT state,origin FROM pending_notifications').fetchall(),[('clock_skew','onvif')]);db.close()
if __name__=='__main__':unittest.main()
