import json,sqlite3,tempfile,unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import security,database,mount,archive
from sunhill_nvr.workers import expected_mount,selected_recorders,enroll_storage,storage_guard,ensure_camera_directories,retention_once,WorkerError
class WorkerTests(unittest.TestCase):
 def setUp(self):self.db=sqlite3.connect(':memory:');security.initialize(self.db);database.migrate(self.db);archive.initialize(self.db)
 def tearDown(self):self.db.close()
 def test_missing_storage_enrollment_fails_closed(self):
    with self.assertRaises(WorkerError):expected_mount(self.db)
    info={'target':'/home/store','source':'/dev/vdb','fstype':'ext4','uuid':'abc-123'}
    self.db.execute("INSERT INTO system_settings VALUES('archive_mount',?)",(json.dumps(info),));self.db.commit()
    self.assertEqual(expected_mount(self.db),mount.Mount(**info))
 def test_only_enabled_rtsp_cameras_selected(self):
    self.db.execute("INSERT INTO cameras VALUES(1,'a','A',1)");self.db.execute("INSERT INTO cameras VALUES(2,'b','B',1)")
    self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://a','rtsp')")
    self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(2,'rtsp://b','ftp')");self.db.commit()
    self.assertEqual(selected_recorders(self.db),{'a'})
 def test_recording_reserve_uses_configured_minimum_and_fails_closed(self):
    from types import SimpleNamespace
    from sunhill_nvr.workers import recording_space_available
    config=SimpleNamespace(minimum_free=10,free_buffer=5,archive=Path('/archive'))
    self.assertFalse(recording_space_available(self.db,config,inspect=lambda p:SimpleNamespace(free_for_service=9)))
    self.assertTrue(recording_space_available(self.db,config,inspect=lambda p:SimpleNamespace(free_for_service=10)))
    with self.assertRaises(WorkerError):recording_space_available(self.db,config,inspect=lambda p:SimpleNamespace(free_for_service=-1))
    with self.assertRaises(OSError):recording_space_available(self.db,config,inspect=lambda p:(_ for _ in ()).throw(OSError('unavailable')))
 def test_recording_health_threshold_controls_worker_and_invalid_value_falls_back(self):
    from types import SimpleNamespace
    from sunhill_nvr import workers
    config=SimpleNamespace(archive=Path('/archive'))
    with self.db:self.db.execute("INSERT INTO system_settings VALUES('recording_stale_after','1800')")
    with patch('sunhill_nvr.workers.health_service.update') as update,patch('sunhill_nvr.workers.service_monitor.check'),patch('sunhill_nvr.workers.storage_guard',side_effect=OSError('unavailable')),patch('sunhill_nvr.workers.health_service.storage_unavailable') as unavailable,patch('sunhill_nvr.workers.health_service.deliver') as deliver:
        workers.health_once(config,self.db)
        update.assert_called_with(self.db,stale_after=1800)
        with self.db:self.db.execute("UPDATE system_settings SET value='broken' WHERE key='recording_stale_after'")
        workers.health_once(config,self.db)
        update.assert_called_with(self.db,stale_after=900)
        self.assertEqual(unavailable.call_count,2);self.assertEqual(deliver.call_count,2)
 def test_corrupt_segment_warning_and_recovery_are_transition_only(self):
    from sunhill_nvr.workers import update_recorder_faults
    from sunhill_nvr.home_assistant import build_request
    self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");self.db.commit()
    update_recorder_faults(self.db,{'cam'},now=1);update_recorder_faults(self.db,{'cam'},now=2)
    self.assertEqual(self.db.execute('SELECT source,state,camera_id FROM pending_notifications').fetchall(),[('cam','recording_corrupt',1)])
    self.assertEqual(self.db.execute("SELECT state FROM system_health WHERE name='recorder_corrupt:cam'").fetchone()[0],'recording_corrupt')
    build_request('http://192.0.2.1:8123','token',camera_id='cam',state='recording_corrupt')
    update_recorder_faults(self.db,set(),now=3);update_recorder_faults(self.db,set(),now=4)
    self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['recording_corrupt','recording_corrupt_cleared'])
    self.assertEqual(self.db.execute("SELECT state FROM system_health WHERE name='recorder_corrupt:cam'").fetchone()[0],'recording_corrupt_cleared')
    build_request('http://192.0.2.1:8123','token',camera_id='cam',state='recording_corrupt_cleared')
    with self.assertRaises(ValueError):update_recorder_faults(self.db,{'../outside'})
 def test_publication_warning_is_separate_and_transition_only(self):
    from sunhill_nvr.workers import update_recorder_faults
    from sunhill_nvr.home_assistant import build_request
    self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");self.db.commit()
    update_recorder_faults(self.db,set(),blocked={'cam'},now=1)
    update_recorder_faults(self.db,set(),blocked={'cam'},now=2)
    self.assertEqual(self.db.execute('SELECT state FROM pending_notifications').fetchall(),[('recording_publish_error',)])
    build_request('http://192.0.2.1:8123','token',camera_id='cam',state='recording_publish_error')
    update_recorder_faults(self.db,{'cam'},blocked=set(),now=3)
    self.assertEqual([x[0] for x in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],
                     ['recording_publish_error','recording_corrupt','recording_publish_cleared'])
    build_request('http://192.0.2.1:8123','token',camera_id='cam',state='recording_publish_cleared')
    with self.assertRaises(ValueError):update_recorder_faults(self.db,set(),blocked={'../outside'})
 def test_staging_warning_and_recovery_are_independent(self):
    from sunhill_nvr.workers import update_recorder_faults
    from sunhill_nvr.home_assistant import build_request
    self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");self.db.commit()
    update_recorder_faults(self.db,set(),blocked=set(),staging={'cam'},now=1)
    update_recorder_faults(self.db,set(),blocked=set(),staging={'cam'},now=2)
    update_recorder_faults(self.db,set(),blocked=set(),staging=set(),now=3)
    states=[x[0] for x in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')]
    self.assertEqual(states,['recording_staging_error','recording_staging_cleared'])
    self.assertEqual(self.db.execute("SELECT state FROM system_health WHERE name='recorder_staging:cam'").fetchone()[0],'recording_staging_cleared')
    for state in states:build_request('http://192.0.2.1:8123','token',camera_id='cam',state=state)
    with self.assertRaises(ValueError):update_recorder_faults(self.db,set(),staging={'../bad'})
 def test_process_failure_and_recovery_are_queued_once(self):
    from sunhill_nvr.workers import update_recorder_faults
    from sunhill_nvr.home_assistant import build_request
    self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");self.db.commit()
    update_recorder_faults(self.db,set(),process={'cam'},now=1);update_recorder_faults(self.db,set(),process={'cam'},now=2)
    update_recorder_faults(self.db,set(),process=set(),now=3)
    states=[row[0] for row in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')]
    self.assertEqual(states,['recording_process_error','recording_process_cleared'])
    for state in states:build_request('http://192.0.2.1:8123','token',camera_id='cam',state=state)
 def test_audio_warning_persists_and_ignores_older_segments(self):
    from sunhill_nvr.workers import update_recorder_audio
    from sunhill_nvr.home_assistant import build_request
    self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
    self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://cam','rtsp')");self.db.commit()
    update_recorder_audio(self.db,{'cam':(100_000_000_000,False)},now=100)
    update_recorder_audio(self.db,{'cam':(100_000_000_000,False)},now=101)
    update_recorder_audio(self.db,{'cam':(90_000_000_000,True)},now=102)
    self.assertEqual(self.db.execute('SELECT state FROM pending_notifications').fetchall(),[('recording_audio_missing',)])
    self.assertEqual(self.db.execute("SELECT state FROM system_health WHERE name='recorder_audio:cam'").fetchone()[0],'recording_audio_missing')
    update_recorder_audio(self.db,{'cam':(110_000_000_000,True)},now=110)
    self.assertEqual([x[0] for x in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['recording_audio_missing','recording_audio_ok'])
    for state in ('recording_audio_missing','recording_audio_ok'):
        build_request('http://192.0.2.1:8123','token',camera_id='cam',state=state)
    self.db.execute("UPDATE cameras SET enabled=0 WHERE id=1");self.db.commit()
    update_recorder_audio(self.db,{'cam':(120_000_000_000,False)},now=120)
    self.assertEqual(self.db.execute('SELECT count(*) FROM pending_notifications').fetchone()[0],2)
    with self.assertRaises(ValueError):update_recorder_audio(self.db,{'cam':(1,'yes')})
 def test_audio_warning_recovers_after_clock_is_corrected(self):
    from sunhill_nvr.workers import update_recorder_audio
    self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
    self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://cam','rtsp')")
    self.db.execute("INSERT INTO system_settings(key,value) VALUES('recorder_audio_seen:cam',?)",(str(999999999999999),));self.db.commit()
    update_recorder_audio(self.db,{'cam':(100_000_000_000,False)},now=100)
    self.assertEqual(self.db.execute("SELECT state FROM system_health WHERE name='recorder_audio:cam'").fetchone()[0],'recording_audio_missing')
 def test_ftp_audio_warning_is_retried_without_reprobing_unchanged_file(self):
    import os
    from datetime import datetime,timezone
    from types import SimpleNamespace
    from sunhill_nvr import workers
    now=int(datetime(2026,11,21,12,tzinfo=timezone.utc).timestamp())
    self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
    self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://cam','ftp')");self.db.commit()
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);file=root/'cam/2026/11/21/130000.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'video');os.utime(file,(now-120,now-120))
        config=SimpleNamespace(archive=root,archive_lock=root/'archive.lock',timezone='Europe/Warsaw')
        data={'format':{'duration':'10'},'streams':[{'codec_type':'video'}]}
        with patch('sunhill_nvr.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.workers.detector',return_value=lambda p:False),patch('sunhill_nvr.workers.probe',return_value=data) as probe:
            with patch('sunhill_nvr.workers.update_recorder_audio',side_effect=sqlite3.OperationalError('busy')):
                workers.index_once(config,self.db,now=now)
            self.assertEqual(self.db.execute('SELECT has_audio FROM recording_stream_info').fetchone()[0],0)
            workers.index_once(config,self.db,now=now+1)
            self.assertEqual(probe.call_count,1)
        self.assertEqual(self.db.execute("SELECT state FROM system_health WHERE name='recorder_audio:cam'").fetchone()[0],'recording_audio_missing')
        self.assertEqual(file.read_bytes(),b'video')
 def test_archive_probe_uses_locked_descriptor_and_rejects_mutated_file(self):
    import os,time
    from types import SimpleNamespace
    from sunhill_nvr import workers
    from sunhill_nvr.files import open_confined,try_exclusive
    now=time.time()
    self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
    self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://cam','ftp')");self.db.commit()
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);file=root/'cam/2026/11/21/130000.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'video');os.utime(file,(now-120,now-120))
        cfg=SimpleNamespace(archive=root,archive_lock=root/'archive.lock',timezone='Europe/Warsaw')
        calls=[]
        def probe(fd):
            self.assertIsInstance(fd,int);self.assertEqual(os.pread(fd,5,0),b'video')
            other,_=open_confined(root,str(file.relative_to(root)))
            try:self.assertFalse(try_exclusive(other))
            finally:os.close(other)
            file.write_bytes(b'changed');calls.append(fd)
            return {'format':{'duration':'10'},'streams':[{'codec_type':'video'}]}
        with patch('sunhill_nvr.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.workers.detector',return_value=lambda p:False),patch('sunhill_nvr.workers.probe',side_effect=probe):
            result=workers.index_once(cfg,self.db,now=now)
        self.assertEqual(len(calls),1);self.assertEqual((result.accepted,result.failed),(0,1));self.assertEqual(file.read_bytes(),b'changed')
 def test_ftp_audio_detection_uses_real_media_streams(self):
    import os,subprocess,time
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from types import SimpleNamespace
    from sunhill_nvr import workers
    now=int(time.time());day=datetime.fromtimestamp(now,ZoneInfo('Europe/Warsaw')).strftime('%Y/%m/%d')
    self.db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
    self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://cam','ftp')");self.db.commit()
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);folder=root/'cam'/day;folder.mkdir(parents=True);config=SimpleNamespace(archive=root,archive_lock=root/'archive.lock',timezone='Europe/Warsaw')
        silent=folder/'120000.mp4';sound=folder/'120100.mp4'
        base=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=blue:s=160x90:r=25:d=1']
        subprocess.run(base+['-c:v','libx264','-pix_fmt','yuv420p',str(silent)],check=True,stderr=subprocess.PIPE);os.utime(silent,(now-120,now-120))
        with patch('sunhill_nvr.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.workers.detector',return_value=lambda p:False):
            workers.index_once(config,self.db,now=now)
            subprocess.run(base+['-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(sound)],check=True,stderr=subprocess.PIPE);os.utime(sound,(now-60,now-60))
            workers.index_once(config,self.db,now=now)
        states=[row[0] for row in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')]
        self.assertEqual(states,['recording_audio_missing','recording_audio_ok'])
        self.assertTrue(silent.exists());self.assertTrue(sound.exists())
 def test_storage_enrollment_is_non_destructive(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);identity=mount.Mount('/data','/dev/vdb1','ext4','uuid-123')
        self.assertEqual(enroll_storage(self.db,root,inspect=lambda p:identity),identity)
        self.assertFalse((root/'.sunhill-volume-id').exists())
        self.assertEqual(enroll_storage(self.db,root,inspect=lambda p:identity),identity)
 def test_system_root_requires_explicit_choice(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);identity=mount.Mount('/','/dev/vda','ext4','uuid')
        with self.assertRaises(WorkerError):enroll_storage(self.db,root,inspect=lambda p:identity)
        self.assertFalse((root/'.sunhill-volume-id').exists())
        self.assertEqual(enroll_storage(self.db,root,inspect=lambda p:identity,allow_rootfs=True),identity)
        self.assertFalse((root/'.sunhill-volume-id').exists())
 def test_network_mount_rejected_before_enrollment_or_rotation(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);remote=mount.Mount('/data','server:/share','nfs',None)
        with self.assertRaisesRegex(WorkerError,'filesystem'):
            enroll_storage(self.db,root,inspect=lambda p:remote)
        self.assertFalse((root/'.sunhill-volume-id').exists())
        self.db.execute("INSERT INTO system_settings VALUES('archive_mount',?)",(json.dumps(remote.__dict__),));self.db.commit()
        with patch('sunhill_nvr.storage_paths.mounted',side_effect=ValueError('unsupported local storage filesystem')):
            with self.assertRaises(ValueError):storage_guard(self.db,root)
 def test_guard_rejects_mid_operation_change_but_accepts_new_disk(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);first=(root,123,10,40);second=(root,456,11,41)
        with patch('sunhill_nvr.storage_paths.check_location',return_value=first) as check:
            guard=storage_guard(self.db,root);self.assertTrue(guard())
            check.return_value=second
            self.assertFalse(guard())
            self.assertTrue(storage_guard(self.db,root)())
        self.assertFalse((root/'.sunhill-volume-id').exists())
 def test_creates_camera_roots_without_following_symlinks(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)/'archive';root.mkdir();outside=Path(tmp)/'outside';outside.mkdir()
        self.db.execute("INSERT INTO cameras VALUES(1,'brama','Brama',1)");self.db.commit()
        ensure_camera_directories(self.db,root,lambda:True)
        self.assertTrue((root/'brama').is_dir())
        (root/'brama').rmdir();(root/'brama').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(OSError):ensure_camera_directories(self.db,root,lambda:True)
        self.assertEqual(list(outside.iterdir()),[])
        with self.assertRaises(WorkerError):ensure_camera_directories(self.db,root,lambda:False)
 def test_rotation_does_not_delete_while_update_can_roll_back(self):
    from types import SimpleNamespace
    from sunhill_nvr.retention import RetentionBusy
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);flag=root/'maintenance.flag';flag.touch()
        cfg=SimpleNamespace(archive=root,archive_lock=root/'lock',timezone='Europe/Warsaw')
        with patch('sunhill_nvr.workers.MAINTENANCE_FLAG',flag),patch('sunhill_nvr.workers.retention.run') as run:
            with self.assertRaises(RetentionBusy):retention_once(cfg,self.db,preview=False)
            run.assert_not_called()
        flag.unlink()
        def simulate_rotation(*args,**kwargs):
            flag.touch();kwargs['mount_guard']()
            self.fail('rotation must abort when maintenance begins')
        with patch('sunhill_nvr.workers.MAINTENANCE_FLAG',flag),patch('sunhill_nvr.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.workers.identify'),patch('sunhill_nvr.workers.storage_limits',return_value=(10,5)),patch('sunhill_nvr.workers.detector'),patch('sunhill_nvr.workers.retention.run',side_effect=simulate_rotation):
            with self.assertRaises(RetentionBusy):retention_once(cfg,self.db,preview=False)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM rotation_history').fetchone()[0],0)
 def test_rotation_records_protected_space_and_sends_warning(self):
    from types import SimpleNamespace
    from sunhill_nvr.retention import RetentionResult
    cfg=SimpleNamespace(archive=Path('/unused/archive'),archive_lock=Path('/unused/lock'),timezone='Europe/Warsaw')
    result=RetentionResult(8,9,('cam/2026/11/21/190900.mp4',),False,False,3,50)
    def run(*args,**kwargs):
        self.assertEqual((kwargs['minimum'],kwargs['target']),(12,15))
        kwargs['on_remove']('cam/2026/11/21/190900.mp4');return result
    with patch('sunhill_nvr.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.workers.identify'),patch('sunhill_nvr.workers.storage_limits',return_value=(10,5)),patch('sunhill_nvr.workers.detector'),patch('sunhill_nvr.workers.retention.run',side_effect=run):
        self.assertEqual(retention_once(cfg,self.db,preview=False),result)
    self.assertEqual(self.db.execute('SELECT state,files_count,protected_count FROM rotation_history').fetchone(),('rotation_blocked',1,3))
    self.assertEqual(self.db.execute('SELECT state FROM pending_notifications').fetchone()[0],'rotation_blocked')
if __name__=='__main__':unittest.main()
