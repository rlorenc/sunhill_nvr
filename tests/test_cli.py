import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.cli import initialize,initialize_setup,migrate_database,export_loop,control_once
from sunhill_nvr.config import parse
from sunhill_nvr.database import open_db
class CLITests(unittest.TestCase):
 def test_uncommitted_release_does_not_claim_exports_or_run_periodic_tasks(self):
    from types import SimpleNamespace
    from sunhill_nvr.cli import periodic
    from unittest.mock import Mock
    with tempfile.TemporaryDirectory() as t:
        flag=Path(t)/'maintenance.flag';flag.touch()
        config=SimpleNamespace(database=Path(t)/'db.sqlite')
        action=Mock()
        with patch('sunhill_nvr.workers.MAINTENANCE_FLAG',flag),patch('sunhill_nvr.cli.database.open_db') as connect,patch('sunhill_nvr.cli.run_one') as export,patch('sunhill_nvr.cli.cleanup_stale_temporary') as cleanup:
            export_loop(config,once=True);periodic(config,action,1,once=True)
            connect.assert_not_called();export.assert_not_called();cleanup.assert_not_called();action.assert_not_called()
            flag.unlink()
            connect.side_effect=lambda *a:sqlite3.connect(':memory:')
            periodic(config,action,1,once=True);action.assert_called_once()
 def test_recorder_does_not_start_or_publish_during_maintenance(self):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from sunhill_nvr.cli import recorder_loop
    with tempfile.TemporaryDirectory() as t:
        flag=Path(t)/'maintenance.flag';flag.touch()
        config=SimpleNamespace(database=Path(t)/'db.sqlite')
        worker=Mock()
        with patch('sunhill_nvr.workers.MAINTENANCE_FLAG',flag),patch('sunhill_nvr.cli.workers.recorder_supervisor',return_value=worker),patch('sunhill_nvr.cli.database.open_db') as connect:
            recorder_loop(config,once=True)
            connect.assert_not_called();worker.tick.assert_not_called();self.assertTrue(worker.stop_all.called)
 def test_recorder_resumes_after_maintenance_is_released(self):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from sunhill_nvr.cli import recorder_loop
    with tempfile.TemporaryDirectory() as t:
        flag=Path(t)/'maintenance.flag';flag.touch()
        config=SimpleNamespace(database=Path(t)/'db.sqlite',archive=Path(t))
        worker=Mock();worker.audio_observations={}
        calls=[]
        def sleep(interval,stopping):
            calls.append(interval)
            if flag.exists():flag.unlink()
            else:stopping[0]=True
        with patch('sunhill_nvr.workers.MAINTENANCE_FLAG',flag),patch('sunhill_nvr.cli.workers.recorder_supervisor',return_value=worker),patch('sunhill_nvr.cli.database.open_db',side_effect=lambda *a:sqlite3.connect(':memory:')),patch('sunhill_nvr.cli.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.cli.workers.selected_recorders',return_value={'cam'}),patch('sunhill_nvr.cli.workers.recording_space_available',return_value=True),patch('sunhill_nvr.cli.workers.update_recorder_faults'),patch('sunhill_nvr.cli.workers.update_recorder_audio'),patch('sunhill_nvr.cli.signal.signal'),patch('sunhill_nvr.cli.sleep_interruptibly',side_effect=sleep):
            recorder_loop(config)
        self.assertEqual(calls,[1,5]);worker.tick.assert_called_once();self.assertEqual(worker.tick.call_args.args[0],{'cam'})
 def test_export_does_not_cleanup_if_maintenance_begins_during_job_check(self):
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as t:
        flag=Path(t)/'maintenance.flag'
        config=SimpleNamespace(database=Path(t)/'db.sqlite',archive=Path(t),exports=Path(t),archive_lock=Path(t)/'lock')
        def finish(*args,**kwargs):flag.touch();return None
        with patch('sunhill_nvr.workers.MAINTENANCE_FLAG',flag),patch('sunhill_nvr.cli.database.open_db',side_effect=lambda *a:sqlite3.connect(':memory:')),patch('sunhill_nvr.cli.workers.storage_limits',return_value=(0,0)),patch('sunhill_nvr.cli.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.cli.run_one',side_effect=finish),patch('sunhill_nvr.cli.cleanup_stale_temporary') as cleanup:
            export_loop(config,once=True);cleanup.assert_not_called()
 def test_storage_enrollment_passes_root_filesystem_choice_explicitly(self):
    from types import SimpleNamespace
    from sunhill_nvr.cli import main
    config=SimpleNamespace(database=Path('/unused/db'),archive=Path('/unused/archive'))
    with patch('sunhill_nvr.cli.load',return_value=config),patch('sunhill_nvr.cli.database.open_db',side_effect=lambda *a,**k:sqlite3.connect(':memory:')),patch('sunhill_nvr.storage_paths.remember_location') as enroll:
        self.assertEqual(main(['enroll-storage']),0)
        self.assertFalse(enroll.call_args.kwargs['allow_rootfs'])
        self.assertEqual(main(['enroll-storage','--allow-rootfs']),0)
        self.assertTrue(enroll.call_args.kwargs['allow_rootfs'])
 def test_analytics_model_status_cli_exit_and_root(self):
    from sunhill_nvr.cli import main
    from unittest.mock import Mock
    from contextlib import redirect_stdout
    from io import StringIO
    with patch('sunhill_nvr.cli.load',return_value=Mock()),patch('sunhill_nvr.cli.os.geteuid',return_value=0),patch('sunhill_nvr.analytics_models.status',return_value={'coco':{'state':'missing'}}) as status,redirect_stdout(StringIO()) as output:
        self.assertEqual(main(['analytics-model-status']),0);status.assert_called_with(verify=False)
        self.assertEqual(main(['analytics-model-status','--verify']),1);status.assert_called_with(verify=True)
        status.return_value={'coco':{'state':'present','inference':'passed'}}
        self.assertEqual(main(['analytics-model-status','--verify']),0)
        self.assertIn('missing',output.getvalue())
    with patch('sunhill_nvr.cli.load',return_value=Mock()),patch('sunhill_nvr.cli.os.geteuid',return_value=1000):
        with self.assertRaises(ValueError):main(['analytics-model-status'])
 def test_long_worker_sleep_stops_promptly_on_sigterm(self):
    import os,subprocess,sys
    program='''import signal\nfrom sunhill_nvr.cli import sleep_interruptibly\nstopping=[False]\nsignal.signal(signal.SIGTERM,lambda signum,frame:stopping.__setitem__(0,True))\nprint("ready",flush=True)\nsleep_interruptibly(900,stopping)\nprint("stopped",flush=True)\n'''
    env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
    process=subprocess.Popen([sys.executable,'-c',program],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        self.assertEqual(process.stdout.readline().strip(),'ready');process.terminate()
        output,error=process.communicate(timeout=3)
        self.assertEqual(process.returncode,0,error);self.assertIn('stopped',output)
    finally:
        if process.poll() is None:process.kill();process.wait()
 def test_controller_stops_ftp_when_database_unavailable(self):
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as t:
        cfg=SimpleNamespace(database=Path(t)/'missing.sqlite',archive=Path(t),control_state=Path(t))
        with patch('sunhill_nvr.cli.pwd.getpwnam',return_value=SimpleNamespace(pw_uid=1000,pw_gid=1000)),patch('sunhill_nvr.cli.controller.set_ftp_running') as service:
            with self.assertRaises(sqlite3.OperationalError):control_once(cfg)
            service.assert_called_once_with(False)
 def test_controller_passes_supplementary_groups_to_ftp_configuration(self):
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);database_path=root/'db.sqlite';sqlite3.connect(database_path).close()
        cfg=SimpleNamespace(database=database_path,archive=root,control_state=root)
        def guarded(db,config,uid,gid,*,guard_factory,reconcile_config,writer_check):
            reconcile_config(db,config.control_state,config.archive,uid,gid)
        with patch('sunhill_nvr.cli.pwd.getpwnam',return_value=SimpleNamespace(pw_uid=1200,pw_gid=1200)), \
             patch('sunhill_nvr.cli.os.getgrouplist',return_value=[1200,1300]), \
             patch('sunhill_nvr.cli.controller.guarded_reconcile',side_effect=guarded), \
             patch('sunhill_nvr.cli.controller.reconcile') as reconcile:
            control_once(cfg)
        self.assertEqual(reconcile.call_args.kwargs['supplementary_gids'],(1200,1300))
 def test_recorder_health_write_failure_does_not_interrupt_recording_tick(self):
    from types import SimpleNamespace
    from sunhill_nvr.cli import recorder_loop
    calls=[];worker=SimpleNamespace(corrupt_cameras={'cam'},blocked_cameras=set(),preparation_failures=set(),process_failures=set(),audio_observations={'cam':(1,False)},tick=lambda cameras,now,**kw:calls.append(('tick',cameras)),stop_all=lambda:calls.append(('stop',)))
    cfg=SimpleNamespace(database=Path('/unused/db'),archive=Path('/unused/archive'))
    def memory_db(*args):
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE system_health(name TEXT,state TEXT)');return db
    with patch('sunhill_nvr.cli.workers.recorder_supervisor',return_value=worker),patch('sunhill_nvr.cli.database.open_db',side_effect=memory_db),patch('sunhill_nvr.cli.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.cli.workers.recording_space_available',return_value=True),patch('sunhill_nvr.cli.workers.selected_recorders',return_value={'cam'}),patch('sunhill_nvr.cli.workers.update_recorder_faults',side_effect=sqlite3.OperationalError('busy')):
        recorder_loop(cfg,once=True)
    self.assertEqual(calls,[('tick',{'cam'}),('stop',)])
    self.assertEqual(worker.audio_observations,{'cam':(1,False)})
 def test_low_space_pauses_rtsp_without_marking_disk_missing(self):
    from types import SimpleNamespace
    from sunhill_nvr.cli import recorder_loop
    calls=[];worker=SimpleNamespace(corrupt_cameras=set(),blocked_cameras=set(),preparation_failures=set(),process_failures=set(),audio_observations={},tick=lambda cameras,now,**kw:calls.append(cameras),stop_all=lambda:None)
    cfg=SimpleNamespace(database=Path('/unused/db'),archive=Path('/unused/archive'))
    def memory_db(*args):
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE system_health(name TEXT,state TEXT)');return db
    with patch('sunhill_nvr.cli.workers.recorder_supervisor',return_value=worker),patch('sunhill_nvr.cli.database.open_db',side_effect=memory_db),patch('sunhill_nvr.cli.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.cli.workers.selected_recorders',return_value={'cam'}),patch('sunhill_nvr.cli.workers.recording_space_available',return_value=False),patch('sunhill_nvr.cli.workers.update_recorder_faults'),patch('sunhill_nvr.cli.workers.update_recorder_audio'):
        recorder_loop(cfg,once=True)
    self.assertEqual(calls,[set()])
 def test_recorder_restores_process_alarm_after_restart(self):
    from types import SimpleNamespace
    from sunhill_nvr.cli import recorder_loop
    calls=[];worker=SimpleNamespace(corrupt_cameras=set(),blocked_cameras=set(),preparation_failures=set(),process_failures=set(),audio_observations={},tick=lambda cameras,now,**kw:calls.append(set(worker.process_failures)),stop_all=lambda:None)
    cfg=SimpleNamespace(database=Path('/unused/db'),archive=Path('/unused/archive'))
    def memory_db(*args):
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE system_health(name TEXT,state TEXT)');db.execute("INSERT INTO system_health VALUES('recorder_process:cam','recording_process_error')");db.commit();return db
    with patch('sunhill_nvr.cli.workers.recorder_supervisor',return_value=worker),patch('sunhill_nvr.cli.database.open_db',side_effect=memory_db),patch('sunhill_nvr.cli.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.cli.workers.selected_recorders',return_value={'cam'}),patch('sunhill_nvr.cli.workers.recording_space_available',return_value=False),patch('sunhill_nvr.cli.workers.update_recorder_faults') as update,patch('sunhill_nvr.cli.workers.update_recorder_audio'):
        recorder_loop(cfg,once=True)
    self.assertEqual(calls,[{'cam'}]);self.assertEqual(update.call_args.kwargs['process'],{'cam'})
 def test_unavailable_alarm_state_does_not_stop_recording(self):
    from types import SimpleNamespace
    from sunhill_nvr.cli import recorder_loop
    calls=[];worker=SimpleNamespace(corrupt_cameras=set(),blocked_cameras=set(),preparation_failures=set(),process_failures=set(),audio_observations={},tick=lambda cameras,now,**kw:calls.append(cameras),stop_all=lambda:None)
    cfg=SimpleNamespace(database=Path('/unused/db'),archive=Path('/unused/archive'))
    with patch('sunhill_nvr.cli.workers.recorder_supervisor',return_value=worker),patch('sunhill_nvr.cli.database.open_db',side_effect=lambda *a:sqlite3.connect(':memory:')),patch('sunhill_nvr.cli.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.cli.workers.selected_recorders',return_value={'cam'}),patch('sunhill_nvr.cli.workers.recording_space_available',return_value=True),patch('sunhill_nvr.cli.workers.update_recorder_faults') as update,patch('sunhill_nvr.cli.workers.update_recorder_audio'):
        recorder_loop(cfg,once=True)
    self.assertEqual(calls,[{'cam'}]);self.assertIsNone(update.call_args.kwargs['process'])
 def test_initialization_is_non_destructive_and_worker_handles_empty_queue(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);archive=root/'archive';archive.mkdir();exports=root/'exports';exports.mkdir()
        cfg=parse({'hostname':'nvr.example.com','database':str(root/'db.sqlite'),'archive':str(archive),'staging':str(root/'staging'),'activity_socket':str(root/'activity.sock'),'control_state':str(root/'control'),'exports':str(exports),'archive_lock':str(root/'archive.lock'),'timezone':'Europe/Warsaw','minimum_free':0,'free_buffer':0})
        initialize(cfg,'admin','strong-password-example')
        migrate_database(cfg);migrate_database(cfg)
        with self.assertRaises(RuntimeError):initialize(cfg,'admin','strong-password-example')
        with patch('sunhill_nvr.cli.workers.storage_guard',return_value=lambda:True):export_loop(cfg,once=True)
        db=open_db(cfg.database);self.assertEqual(db.execute('SELECT username FROM users').fetchone()[0],'admin');db.close()
 def test_database_publication_is_atomic_single_link_and_no_overwrite(self):
    from sunhill_nvr.cli import _publish_database
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);temporary=root/'candidate.sqlite';destination=root/'primary.sqlite'
        temporary.write_bytes(b'candidate');destination.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):_publish_database(temporary,destination)
        self.assertEqual(destination.read_bytes(),b'existing');self.assertEqual(temporary.read_bytes(),b'candidate')
        destination.unlink()
        # Model a failure after the atomic rename but before the directory fsync.
        import os
        fsync=os.fsync
        def interrupted(fd):
            import stat
            if stat.S_ISDIR(os.fstat(fd).st_mode):raise KeyboardInterrupt('power loss')
            fsync(fd)
        with patch('sunhill_nvr.cli.os.fsync',side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):_publish_database(temporary,destination)
        self.assertFalse(temporary.exists());self.assertEqual(destination.stat().st_nlink,1)
        self.assertEqual(destination.read_bytes(),b'candidate')
 def test_invalid_password_does_not_leave_primary_database(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);(root/'archive').mkdir();(root/'exports').mkdir()
        cfg=parse({'hostname':'nvr.example.com','database':str(root/'db.sqlite'),'archive':str(root/'archive'),'staging':str(root/'staging'),'activity_socket':str(root/'activity.sock'),'control_state':str(root/'control'),'exports':str(root/'exports'),'archive_lock':str(root/'archive.lock'),'timezone':'Europe/Warsaw','minimum_free':0,'free_buffer':0})
        with self.assertRaises(ValueError):initialize(cfg,'admin','short')
        self.assertFalse(cfg.database.exists());self.assertFalse(list(root.glob('.sunhill-init-*')))
 def test_panel_setup_token_is_one_time_and_can_be_renewed(self):
    from sunhill_nvr import security
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);(root/'archive').mkdir();(root/'exports').mkdir()
        cfg=parse({'hostname':'nvr.example.com','database':str(root/'db.sqlite'),'archive':str(root/'archive'),'staging':str(root/'staging'),'activity_socket':str(root/'activity.sock'),'control_state':str(root/'control'),'exports':str(root/'exports'),'archive_lock':str(root/'archive.lock'),'timezone':'Europe/Warsaw','minimum_free':0,'free_buffer':0})
        token=initialize_setup(cfg);db=open_db(cfg.database)
        self.assertTrue(security.setup_available(db));self.assertNotIn(token,str(db.execute('SELECT * FROM system_settings').fetchall()))
        with self.assertRaises(security.AuthError):security.complete_setup(db,'wrong','admin','valid-admin-password')
        with self.assertRaises(ValueError):security.complete_setup(db,token,'admin','short')
        expired=security.issue_setup_token(db,now=1000)
        self.assertFalse(security.setup_available(db,now=1000+security.SETUP_LIFETIME))
        with self.assertRaises(security.AuthError):security.complete_setup(db,expired,'admin','valid-admin-password',now=1000+security.SETUP_LIFETIME)
        renewed=security.issue_setup_token(db)
        with self.assertRaises(security.AuthError):security.complete_setup(db,token,'admin','valid-admin-password')
        security.complete_setup(db,renewed,'admin','valid-admin-password')
        self.assertFalse(security.setup_available(db))
        with self.assertRaises(ValueError):security.issue_setup_token(db)
        with self.assertRaises(security.AuthError):security.complete_setup(db,renewed,'other','valid-admin-password')
        self.assertEqual(db.execute('SELECT username,role FROM users').fetchall(),[('admin','admin')]);db.close()
 def test_console_completion_revokes_token_and_preserves_existing_settings(self):
    from sunhill_nvr import security
    from sunhill_nvr.cli import main
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);(root/'archive').mkdir();(root/'exports').mkdir()
        cfg=parse({'hostname':'nvr.example.com','database':str(root/'db.sqlite'),'archive':str(root/'archive'),'staging':str(root/'staging'),'activity_socket':str(root/'activity.sock'),'control_state':str(root/'control'),'exports':str(root/'exports'),'archive_lock':str(root/'archive.lock'),'timezone':'Europe/Warsaw','minimum_free':123,'free_buffer':0})
        token=initialize_setup(cfg)
        with patch('sunhill_nvr.cli.load',return_value=cfg),patch('builtins.input',return_value='admin'),patch('sunhill_nvr.cli.getpass.getpass',side_effect=['12345','12345','123456','123456','123456','123456']):
            with self.assertRaisesRegex(security.PasswordValidationError,'6 do 1024'):main(['complete-setup'])
            self.assertEqual(main(['complete-setup']),0)
            with self.assertRaises(ValueError):main(['complete-setup'])
        from contextlib import closing
        with closing(open_db(cfg.database)) as db:
            self.assertFalse(security.setup_available(db))
            self.assertTrue(security.password_verify('123456',db.execute('SELECT password_hash FROM users').fetchone()[0]))
            self.assertEqual(db.execute("SELECT value FROM system_settings WHERE key='minimum_free'").fetchone()[0],'123')
            self.assertEqual(db.execute("SELECT action,object_id FROM audit_log").fetchall(),[('setup.complete','1')])
            with self.assertRaises(security.AuthError):security.complete_setup(db,token,'other','valid-admin-password')
if __name__=='__main__':unittest.main()
