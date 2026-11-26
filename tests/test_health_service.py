import sqlite3,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import security,database,archive,health_service
class HealthTests(unittest.TestCase):
 def setUp(self):
    self.db=sqlite3.connect(':memory:');security.initialize(self.db);database.migrate(self.db);archive.initialize(self.db)
    self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode,recording_since) VALUES(1,'rtsp://x','ftp',100)");self.db.commit()
 def tearDown(self):self.db.close()
 def test_long_rtsp_segments_extend_alarm_but_not_ftp(self):
    with self.db:
        self.db.execute("UPDATE camera_details SET record_mode='rtsp' WHERE camera_id=1")
        self.db.execute("INSERT INTO system_settings(key,value) VALUES('recording_segment_seconds','3600')")
    health_service.update(self.db,now=1100,stale_after=900)
    self.assertEqual(self.db.execute('SELECT state FROM camera_health').fetchone()[0],'waiting')
    health_service.update(self.db,now=3821,stale_after=900)
    self.assertEqual(self.db.execute('SELECT state FROM camera_health').fetchone()[0],'stale')
    with self.db:self.db.execute("UPDATE camera_details SET record_mode='ftp' WHERE camera_id=1")
    health_service.update(self.db,now=1100,stale_after=900)
    self.assertEqual(self.db.execute('SELECT state FROM camera_health').fetchone()[0],'stale')
 def test_stale_and_recovery_notifications_not_duplicated(self):
    health_service.update(self.db,now=110,stale_after=20)
    self.assertEqual(self.db.execute('SELECT state FROM camera_health').fetchone()[0],'waiting')
    health_service.update(self.db,now=121,stale_after=20);health_service.update(self.db,now=122,stale_after=20)
    self.assertEqual(self.db.execute('SELECT count(*) FROM pending_notifications').fetchone()[0],1)
    self.db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES ('cam/1970/01/01/000200.mp4','cam','1970-01-01T00:02:00','1970-01-01T00:02:00+00:00',5,1,1,1)");self.db.commit()
    health_service.update(self.db,now=123,stale_after=20)
    self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['stale','recording'])
 def test_backward_clock_correction_does_not_suppress_missing_recording_alarm(self):
    with self.db:self.db.execute('UPDATE camera_details SET recording_since=10000 WHERE camera_id=1')
    health_service.update(self.db,now=100,stale_after=20)
    self.assertEqual(self.db.execute('SELECT recording_since FROM camera_details').fetchone()[0],100)
    health_service.update(self.db,now=121,stale_after=20)
    self.assertEqual(self.db.execute('SELECT state FROM camera_health').fetchone()[0],'stale')
    self.assertEqual(self.db.execute('SELECT state FROM pending_notifications').fetchall(),[('stale',)])
 def test_invalid_camera_health_data_does_not_disable_other_camera_alerts(self):
    with self.db:
        self.db.execute("UPDATE camera_details SET recording_since='invalid' WHERE camera_id=1")
        self.db.execute("INSERT INTO camera_health VALUES(1,'invalid',1,1)")
        self.db.execute("INSERT INTO cameras VALUES(2,'cam2','Second',1)")
        self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode,recording_since) VALUES(2,'rtsp://second','ftp',100)")
        self.db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00','invalid',1,1,1)")
    health_service.update(self.db,now=2000,stale_after=60)
    self.assertEqual(self.db.execute('SELECT camera_id,state FROM camera_health ORDER BY camera_id').fetchall(),[(1,'stale'),(2,'stale')])
    self.assertEqual(self.db.execute('SELECT source,state FROM pending_notifications ORDER BY source').fetchall(),[('cam','stale'),('cam2','stale')])
    self.assertIsNone(self.db.execute('SELECT last_file_at FROM camera_details WHERE camera_id=1').fetchone()[0])
 def test_current_fault_snapshot_ignores_outdated_stream_states(self):
    self.db.execute("INSERT INTO stream_health VALUES(1,'unavailable',2,10,10)")
    self.db.execute("INSERT INTO system_health VALUES('stream_service','stream_service_error',20)");self.db.commit()
    def snapshot():
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');self.db.execute('DELETE FROM pending_notifications');health_service.enqueue_active_faults(self.db,now=30)
        return self.db.execute('SELECT source,state FROM pending_notifications ORDER BY id').fetchall()
    self.assertEqual(snapshot(),[('go2rtc','stream_service_error')])
    with self.db:self.db.execute("UPDATE system_health SET state='stream_service_ok',changed_at=25 WHERE name='stream_service'")
    self.assertEqual(snapshot(),[])
    with self.db:self.db.execute('UPDATE stream_health SET checked_at=26 WHERE camera_id=1')
    self.assertEqual(snapshot(),[('cam','offline')])
    with self.db:self.db.execute('UPDATE stream_health SET checked_at=10000 WHERE camera_id=1')
    self.assertEqual(snapshot(),[])
 def test_fault_snapshot_does_not_report_stale_camera_or_service_results(self):
    with self.db:
        self.db.execute("INSERT INTO camera_health VALUES(1,'stale',1,1)")
        self.db.execute("INSERT INTO stream_health VALUES(1,'unavailable',2,1,1)")
        self.db.execute("UPDATE camera_details SET onvif_url='http://cam/onvif/device_service',clock_checked=1 WHERE camera_id=1")
        self.db.execute("INSERT INTO system_health VALUES('clock:1','clock_skew',1)")
        self.db.execute("INSERT INTO service_health VALUES('sunhill-index.service','inactive',2,1,1)")
        health_service.enqueue_active_faults(self.db,now=2000)
    self.assertEqual(self.db.execute('SELECT state FROM pending_notifications').fetchall(),[])
    with self.db:
        self.db.execute('UPDATE camera_health SET last_checked=2000')
        self.db.execute('UPDATE stream_health SET checked_at=2000')
        self.db.execute('UPDATE camera_details SET clock_checked=2000')
        self.db.execute('UPDATE service_health SET checked_at=2000')
        health_service.enqueue_active_faults(self.db,now=2000)
    self.assertEqual(set(self.db.execute('SELECT source,state FROM pending_notifications')),{('cam','stale'),('cam','offline'),('cam','clock_skew'),('sunhill-index.service','service_down')})
 def test_old_fault_is_reported_again_only_after_a_fresh_check_confirms_it(self):
    with self.db:self.db.execute("INSERT INTO camera_health VALUES(1,'stale',1,1)")
    health_service.update(self.db,now=2000,stale_after=60)
    health_service.update(self.db,now=2001,stale_after=60)
    self.assertEqual(self.db.execute('SELECT source,state FROM pending_notifications').fetchall(),[('cam','stale')])
 def test_reenabled_integration_receives_stale_faults_only_after_reconfirmation(self):
    from types import SimpleNamespace
    from sunhill_nvr import clock_service,service_monitor,stream_health
    from sunhill_nvr.upgrade_system import SERVICES
    with self.db:
        self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','test-token'),('ha_enabled','1')])
        self.db.execute("INSERT INTO camera_health VALUES(1,'stale',1,1)")
        self.db.execute("INSERT INTO stream_health VALUES(1,'unavailable',2,1,1)")
        self.db.execute("UPDATE camera_details SET onvif_url='http://cam/onvif/device_service',clock_checked=1 WHERE camera_id=1")
        self.db.execute("INSERT INTO system_health VALUES('clock:1','clock_skew',1)")
        self.db.execute("INSERT INTO system_health VALUES('stream_service','stream_service_ok',1)")
        self.db.execute("INSERT INTO service_health VALUES('sunhill-index.service','inactive',2,1,1)")
        health_service.enqueue_active_faults(self.db,now=2000)
    self.assertEqual(self.db.execute('SELECT count(*) FROM pending_notifications').fetchone()[0],0)
    services={name:'active' for name in SERVICES};services['sunhill-index.service']='failed'
    def check(now):
        health_service.update(self.db,now=now,stale_after=60)
        stream_health.check(self.db,now=now,gateway=lambda:True,inspect=lambda slug:False)
        clock_service.check(self.db,now=now,query=lambda *a,**k:SimpleNamespace(offset_seconds=50))
        service_monitor.check(self.db,now=now,inspect=lambda:services)
    check(2000);sent=[]
    self.assertEqual(health_service.deliver(self.db,send=lambda *a,**k:sent.append((k['camera_id'],k['state']))),4)
    self.assertEqual(set(sent),{('cam','stale'),('cam','offline'),('cam','clock_skew'),('sunhill-index.service','service_down')})
    check(2001);self.assertEqual(health_service.deliver(self.db,send=lambda *a,**k:sent.append((k['camera_id'],k['state']))),0)
 def test_delivery_retries_without_leaking_token(self):
    from sunhill_nvr import home_assistant
    health_service.update(self.db,now=121,stale_after=20)
    self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','test-token'),('ha_enabled','1')]);self.db.commit()
    def failed(*args,**kwargs):raise home_assistant.NotificationError('unavailable')
    self.assertEqual(health_service.deliver(self.db,send=failed),0)
    self.assertEqual(self.db.execute('SELECT attempts FROM pending_notifications').fetchone()[0],1)
    calls=[];self.assertEqual(health_service.deliver(self.db,send=lambda *args,**kwargs:calls.append((args,kwargs))),1)
    self.assertEqual(calls[0][1]['camera_id'],'cam');self.assertEqual(self.db.execute('SELECT count(*) FROM pending_notifications').fetchone()[0],0)
 def test_notification_delivery_failure_is_visible_until_success_or_disable(self):
    from sunhill_nvr import home_assistant
    self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','test-token'),('ha_enabled','1')])
    self.db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(1,'cam','stale',1)");self.db.commit()
    def failed(*args,**kwargs):raise home_assistant.NotificationError('secret URL and token')
    health_service.deliver(self.db,send=failed)
    self.assertEqual(self.db.execute("SELECT state FROM system_health WHERE name='ha_delivery'").fetchone()[0],'ha_delivery_error')
    health_service.deliver(self.db,send=lambda *args,**kwargs:None)
    self.assertEqual(self.db.execute("SELECT state FROM system_health WHERE name='ha_delivery'").fetchone()[0],'ha_delivery_ok')
    self.db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(1,'cam','stale',2)");self.db.commit()
    health_service.deliver(self.db,send=failed)
    with self.db:self.db.execute("UPDATE system_settings SET value='0' WHERE key='ha_enabled'")
    health_service.deliver(self.db,send=failed)
    self.assertIsNone(self.db.execute("SELECT state FROM system_health WHERE name='ha_delivery'").fetchone())
 def test_failed_source_does_not_block_other_sources_or_reorder_its_events(self):
    from sunhill_nvr import home_assistant
    self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','test-token'),('ha_enabled','1')])
    self.db.executemany('INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,?,?,1)',[('cam','stale'),('cam','recording'),('storage','disk_low')]);self.db.commit()
    calls=[]
    def send(*args,**kwargs):
        calls.append((kwargs['camera_id'],kwargs['state']))
        if kwargs['camera_id']=='cam':raise ValueError('invalid event')
    self.assertEqual(health_service.deliver(self.db,send=send),1)
    self.assertEqual(calls,[('cam','stale'),('storage','disk_low')])
    self.assertEqual(self.db.execute('SELECT source,state,attempts FROM pending_notifications ORDER BY id').fetchall(),[('cam','stale',1),('cam','recording',0)])
 def test_camera_slug_matching_system_source_does_not_block_disk_alarm(self):
    with self.db:
        self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','test-token'),('ha_enabled','1')])
        self.db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(1,'storage','stale',1)")
        self.db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(1,'storage','recording',2)")
        self.db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,'storage','disk_low',3)")
    calls=[]
    def send(*args,**kwargs):
        calls.append(kwargs['state'])
        if kwargs['state']=='stale':raise ValueError('bad camera event')
    self.assertEqual(health_service.deliver(self.db,send=send),1)
    self.assertEqual(calls,['stale','disk_low'])
    self.assertEqual(self.db.execute('SELECT camera_id,state,attempts FROM pending_notifications ORDER BY id').fetchall(),[(1,'stale',1),(1,'recording',0)])
 def test_onvif_clock_alarm_uses_existing_public_home_assistant_state(self):
    with self.db:
        self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','test-token'),('ha_enabled','1')])
        self.db.execute("UPDATE camera_details SET onvif_url='http://camera/onvif/device_service',clock_checked=2 WHERE camera_id=1")
        self.db.execute("INSERT INTO system_health VALUES('clock:1','clock_skew',1)")
        health_service.enqueue_active_faults(self.db,now=2)
    self.assertEqual(self.db.execute('SELECT state,origin FROM pending_notifications').fetchall(),[('clock_skew','onvif')])
    sent=[];self.assertEqual(health_service.deliver(self.db,send=lambda *a,**k:sent.append(k['state'])),1)
    self.assertEqual(sent,['clock_skew'])
 def test_long_failing_source_backlog_does_not_starve_other_alerts(self):
    self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','test-token'),('ha_enabled','1')])
    self.db.executemany('INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,?,?,1)',[('cam','stale')]*25+[('storage','disk_low')]);self.db.commit()
    calls=[]
    def send(*args,**kwargs):
        calls.append(kwargs['camera_id'])
        if kwargs['camera_id']=='cam':raise ValueError('bad event')
    self.assertEqual(health_service.deliver(self.db,send=send),1)
    self.assertEqual(calls,['cam','storage'])
    self.assertEqual(self.db.execute("SELECT count(*) FROM pending_notifications WHERE source='cam'").fetchone()[0],25)
 def test_order_is_preserved_when_multiple_events_succeed(self):
    self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','test-token'),('ha_enabled','1')])
    self.db.executemany('INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,?,?,1)',[('cam','stale'),('cam','recording')]);self.db.commit()
    states=[];self.assertEqual(health_service.deliver(self.db,send=lambda *a,**k:states.append(k['state'])),2)
    self.assertEqual(states,['stale','recording'])
 def test_slow_successes_respect_time_budget_and_keep_remaining_events(self):
    from unittest.mock import patch
    with self.db:
        self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','test-token'),('ha_enabled','1')])
        self.db.executemany('INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,?,?,1)',[('cam','stale'),('cam','recording'),('storage','disk_low'),('storage','disk_ok')])
    clock=[0];timeouts=[]
    def send(*args,**kwargs):timeouts.append(kwargs['timeout']);clock[0]+=4
    with patch('sunhill_nvr.home_assistant.send',side_effect=send):
        self.assertEqual(health_service.deliver(self.db,monotonic=lambda:clock[0],budget=10),2)
    self.assertEqual(timeouts,[5,5])
    self.assertEqual(self.db.execute('SELECT source,state FROM pending_notifications ORDER BY id').fetchall(),[('cam','recording'),('storage','disk_ok')])
 def test_enabling_during_disabled_check_does_not_delete_outbox(self):
    from unittest.mock import patch
    self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','token'),('ha_enabled','1')])
    self.db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(1,'cam','stale',1)");self.db.commit()
    original=health_service._settings;calls=[]
    def read(db):
        calls.append(1)
        return {'ha_enabled':'0'} if len(calls)==1 else original(db)
    sent=[]
    with patch.object(health_service,'_settings',side_effect=read):
        self.assertEqual(health_service.deliver(self.db,send=lambda *a,**k:sent.append(k['state'])),1)
    self.assertEqual(sent,['stale'])
 def test_configuration_change_during_send_preserves_replacement_event(self):
    self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','token'),('ha_enabled','1'),('ha_generation','old')])
    self.db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(1,'cam','stale',1)");self.db.commit()
    def send(*args,**kwargs):
        with self.db:
            self.db.execute('DELETE FROM pending_notifications')
            self.db.execute("UPDATE system_settings SET value='new' WHERE key='ha_generation'")
            self.db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(1,'cam','stale',2)")
    self.assertEqual(health_service.deliver(self.db,send=send),0)
    self.assertEqual(self.db.execute('SELECT state,created_at FROM pending_notifications').fetchall(),[('stale',2)])
 def test_old_delivery_result_cannot_restore_error_after_settings_change(self):
    self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_enabled','1'),('ha_generation','old')]);self.db.commit()
    settings=health_service._settings(self.db)
    with self.db:self.db.execute("UPDATE system_settings SET value='new' WHERE key='ha_generation'")
    health_service._delivery_state(self.db,'ha_delivery_error',settings)
    self.assertIsNone(self.db.execute("SELECT state FROM system_health WHERE name='ha_delivery'").fetchone())
 def test_malformed_home_assistant_http_response_is_retried(self):
    import http.client
    from unittest.mock import patch
    from sunhill_nvr import home_assistant
    self.db.executemany('INSERT INTO system_settings VALUES (?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','token'),('ha_enabled','1')])
    self.db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(1,'cam','stale',1)");self.db.commit()
    class Opener:
        def open(self,*args,**kwargs):raise http.client.BadStatusLine('malformed')
    with patch.object(home_assistant.urllib.request,'build_opener',return_value=Opener()):
        self.assertEqual(health_service.deliver(self.db),0)
    self.assertEqual(self.db.execute('SELECT attempts FROM pending_notifications').fetchone()[0],1)
 def test_future_recording_reports_clock_skew(self):
    self.db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES ('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',5,1,1,1)");self.db.commit()
    health_service.update(self.db,now=121,stale_after=20)
    self.assertEqual(self.db.execute('SELECT state FROM camera_health').fetchone()[0],'clock_skew')
 def test_new_file_recovers_after_camera_clock_was_in_the_future(self):
    self.db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES ('cam/future','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',5,1,100,1)");self.db.commit()
    health_service.update(self.db,now=121,stale_after=20)
    self.assertEqual(self.db.execute('SELECT state FROM camera_health').fetchone()[0],'clock_skew')
    self.db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES ('cam/current','cam','1970-01-01T00:02:00','1970-01-01T00:02:00+00:00',5,1,200,1)");self.db.commit()
    health_service.update(self.db,now=123,stale_after=20)
    self.assertEqual(self.db.execute('SELECT state FROM camera_health').fetchone()[0],'recording')
    self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['clock_skew','recording'])
    query="EXPLAIN QUERY PLAN SELECT id FROM recordings WHERE camera=? AND utc_first IS NOT NULL ORDER BY mtime_ns DESC,id DESC LIMIT 1"
    self.assertIn('idx_recordings_camera_mtime',' '.join(row[-1] for row in self.db.execute(query,('cam',))))
 def test_a_different_fault_is_reported_without_repeating_it(self):
    health_service.update(self.db,now=121,stale_after=20)
    self.db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES ('cam/future','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',5,1,100,1)");self.db.commit()
    health_service.update(self.db,now=122,stale_after=20);health_service.update(self.db,now=123,stale_after=20)
    self.assertEqual([row[0] for row in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['stale','clock_skew'])
 def test_storage_alarm_and_recovery(self):
    self.assertTrue(health_service.update_storage(self.db,9,10,now=1));self.assertTrue(health_service.update_storage(self.db,8,10,now=2));self.assertFalse(health_service.update_storage(self.db,11,10,now=3))
    self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['disk_low','disk_ok'])
 def test_concurrent_fault_reports_queue_only_one_transition(self):
    import tempfile,threading
    from concurrent.futures import ThreadPoolExecutor
    from sunhill_nvr import service_monitor,stream_health
    with tempfile.TemporaryDirectory() as tmp:
        file=Path(tmp)/'health.sqlite'
        db=sqlite3.connect(file);security.initialize(db);database.migrate(db);db.close()
        operations=[lambda d:health_service.update_storage(d,9,10),
                    lambda d:health_service.storage_unavailable(d),
                    lambda d:health_service.rotation_state(d,'rotation_blocked'),
                    lambda d:service_monitor._monitor_status(d,False,1),
                    lambda d:stream_health._gateway_state(d,False,1)]
        for operation in operations:
            db=sqlite3.connect(file)
            with db:db.execute('DELETE FROM system_health');db.execute('DELETE FROM pending_notifications')
            db.close();barrier=threading.Barrier(4)
            def report():
                connection=sqlite3.connect(file,timeout=10)
                try:barrier.wait(timeout=5);operation(connection)
                finally:connection.close()
            with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:report(),range(4)))
            db=sqlite3.connect(file)
            self.assertEqual(db.execute('SELECT count(*) FROM pending_notifications').fetchone()[0],1)
            db.close()
 def test_disconnected_storage_generates_event_once_and_recovers(self):
    health_service.storage_unavailable(self.db,now=1);health_service.storage_unavailable(self.db,now=2);health_service.update_storage(self.db,100,10,now=3)
    self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['disk_unavailable','disk_ok'])
if __name__=='__main__':unittest.main()
