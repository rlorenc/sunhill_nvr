import os,sqlite3,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import security,database
from sunhill_nvr.controller import reconcile,guarded_reconcile,require_trusted_directory,ControllerError
from sunhill_nvr.ftp_accounts import make_hash
class ControllerTests(unittest.TestCase):
 def test_readiness_accepts_fragmented_real_protocol_greetings(self):
    import socket,threading,time
    from sunhill_nvr.controller import service_ready
    for service,parts in [('sunhill-ftp.service',[b'2',b'20 Ready\r',b'\n']),('sunhill-ftp.service',[b'220-Welcome\r\n220-',b'Info\r\n220 Ready\r\n']),('sunhill-go2rtc.service',[b'HT',b'TP/1.1 ',b'200 OK\r\n'])]:
        with self.subTest(service=service):
            client,server=socket.socketpair()
            def respond():
                with server:
                    if service=='sunhill-go2rtc.service':server.recv(1024)
                    for part in parts:server.sendall(part);time.sleep(.01)
            worker=threading.Thread(target=respond);worker.start()
            try:
                with patch('sunhill_nvr.controller.socket.create_connection',return_value=client):self.assertTrue(service_ready(service,timeout=.5))
            finally:client.close();worker.join(1)
 def test_readiness_rejects_invalid_http_status_and_incomplete_greeting(self):
    import socket,time
    from sunhill_nvr.controller import service_ready
    for service,payload in [('sunhill-go2rtc.service',b'HTTP/1.1 2000 bogus\r\n'),('sunhill-ftp.service',b'220'),('sunhill-ftp.service',b'220-Welcome\r\n'),('sunhill-ftp.service',b'220 Ready\n')]:
        client,server=socket.socketpair()
        with client,server:
            server.sendall(payload);start=time.monotonic()
            with patch('sunhill_nvr.controller.socket.create_connection',return_value=client):self.assertFalse(service_ready(service,timeout=.1))
            self.assertLess(time.monotonic()-start,.5)
 def test_ftp_stops_on_storage_loss_and_resumes_only_when_storage_is_valid(self):
    from types import SimpleNamespace
    tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
    state=Path(tmp.name);(state/'current').mkdir();(state/'current/go2rtc.json').write_text('{}')
    config=SimpleNamespace(archive=Path('/archive'),control_state=state);calls=[]
    kwargs={'reconcile_config':lambda *args:calls.append('reconcile'),'set_running':lambda value:calls.append(value),'ensure_stream':lambda:calls.append('stream')}
    guarded_reconcile(None,config,1000,1000,guard_factory=lambda *args:lambda:False,**kwargs)
    self.assertEqual(calls,[False,'stream']);calls.clear()
    guarded_reconcile(None,config,1000,1000,guard_factory=lambda *args:lambda:True,**kwargs)
    self.assertEqual(calls,['reconcile',True,'stream']);calls.clear()
    states=iter([True,False])
    guarded_reconcile(None,config,1000,1000,guard_factory=lambda *args:lambda:next(states),**kwargs)
    self.assertEqual(calls,['reconcile',False,'stream'])
 def test_failed_configuration_stops_ftp(self):
    from types import SimpleNamespace
    tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
    state=Path(tmp.name);(state/'current').mkdir();(state/'current/go2rtc.json').write_text('{}')
    config=SimpleNamespace(archive=Path('/archive'),control_state=state);calls=[]
    def fail(*args):raise RuntimeError('FTP home preparation failed')
    with self.assertRaisesRegex(RuntimeError,'FTP home preparation failed'):
        guarded_reconcile(None,config,1200,1200,guard_factory=lambda *args:lambda:True,reconcile_config=fail,
                          set_running=lambda value:calls.append(value),ensure_stream=lambda:calls.append('stream'))
    self.assertEqual(calls,[False,'stream'])
 def test_pending_activation_never_restarts_unverified_gateway_on_error(self):
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as tmp:
        state=Path(tmp);config=SimpleNamespace(archive=Path('/archive'),control_state=state);calls=[]
        def fail(*args):
            (state/'.activation-pending').touch()
            raise RuntimeError('activation interrupted')
        kwargs={'guard_factory':lambda *args:lambda:True,'reconcile_config':fail,
                'set_running':lambda value:calls.append(value),'ensure_stream':lambda:calls.append('stream')}
        with self.assertRaisesRegex(RuntimeError,'activation interrupted'):
            guarded_reconcile(None,config,1200,1200,**kwargs)
        self.assertEqual(calls,[False]);calls.clear()
        guarded_reconcile(None,config,1200,1200,writer_check=lambda:False,**kwargs)
        self.assertEqual(calls,[False]);calls.clear()
        def success(*args):
            (state/'.activation-pending').unlink();(state/'current').mkdir();(state/'current/go2rtc.json').write_text('{}')
        kwargs['reconcile_config']=success
        guarded_reconcile(None,config,1200,1200,**kwargs)
        self.assertEqual(calls,[True,'stream'])
 def test_low_space_stops_ftp_but_keeps_live_gateway_and_recovers(self):
    from types import SimpleNamespace
    tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
    state=Path(tmp.name);(state/'current').mkdir();(state/'current/go2rtc.json').write_text('{}')
    config=SimpleNamespace(archive=Path('/archive'),control_state=state);calls=[]
    kwargs={'guard_factory':lambda *args:lambda:True,'reconcile_config':lambda *args:calls.append('reconcile'),
            'set_running':lambda value:calls.append(value),'ensure_stream':lambda:calls.append('stream')}
    guarded_reconcile(None,config,1000,1000,writer_check=lambda:False,**kwargs)
    self.assertEqual(calls,[False,'stream']);calls.clear()
    def unavailable():raise OSError('cannot determine free space')
    guarded_reconcile(None,config,1000,1000,writer_check=unavailable,**kwargs)
    self.assertEqual(calls,[False,'stream']);calls.clear()
    values=iter([True,False])
    guarded_reconcile(None,config,1000,1000,writer_check=lambda:next(values),**kwargs)
    self.assertEqual(calls,['reconcile',False,'stream']);calls.clear()
    guarded_reconcile(None,config,1000,1000,writer_check=lambda:True,**kwargs)
    self.assertEqual(calls,['reconcile',True,'stream'])
 def test_missing_config_never_starts_gateway_on_validation_failure(self):
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as tmp:
        cfg=SimpleNamespace(archive=Path('/archive'),control_state=Path(tmp));calls=[]
        def fail(*args):raise RuntimeError('invalid FTP configuration')
        with self.assertRaises(RuntimeError):
            guarded_reconcile(None,cfg,1200,1200,guard_factory=lambda *args:lambda:True,reconcile_config=fail,set_running=lambda x:calls.append(x),ensure_stream=lambda:calls.append('stream'))
        self.assertEqual(calls,[False])
 def test_failed_cleanup_does_not_mask_reconciliation_error(self):
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as tmp:
        cfg=SimpleNamespace(archive=Path('/archive'),control_state=Path(tmp));(cfg.control_state/'current').mkdir();(cfg.control_state/'current/go2rtc.json').write_text('{}')
        def fail(*args):raise RuntimeError('original configuration error')
        def secondary(*args):raise ControllerError('secondary service failure')
        with self.assertRaisesRegex(RuntimeError,'original configuration error'):
            guarded_reconcile(None,cfg,1200,1200,guard_factory=lambda *args:lambda:True,reconcile_config=fail,set_running=secondary,ensure_stream=secondary)
 def test_stream_gateway_is_started_when_configuration_is_unchanged(self):
    from sunhill_nvr.controller import ensure_stream_running
    commands=[]
    def run(args,**kwargs):commands.append(args);return subprocess.CompletedProcess(args,0)
    ensure_stream_running(run=run)
    self.assertEqual(commands,[['/usr/bin/systemctl','start','sunhill-go2rtc.service']])
    def fail(args,**kwargs):return subprocess.CompletedProcess(args,1)
    with self.assertRaises(ControllerError):ensure_stream_running(run=fail)
 def test_controller_recovers_gateway_with_preexisting_config(self):
    from types import SimpleNamespace
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);config=SimpleNamespace(archive=Path('/home/store/kamery'),control_state=root);calls=[]
        def run(args,**kwargs):calls.append(args);return subprocess.CompletedProcess(args,0)
        def apply_config(db,state,archive,uid,gid):
            reconcile(db,state,archive,uid,gid,run=run,trusted_check=lambda p:None,readiness=lambda service:True)
        from sunhill_nvr.controller import ensure_stream_running
        kwargs={'guard_factory':lambda *args:lambda:True,'reconcile_config':apply_config,
                'set_running':lambda state:None,'ensure_stream':lambda:ensure_stream_running(run=run)}
        with patch('sunhill_nvr.config_reconcile.os.chown'),patch('sunhill_nvr.config_reconcile.os.fchown'):
            guarded_reconcile(db,config,1200,1200,**kwargs);calls.clear()
            guarded_reconcile(db,config,1200,1200,**kwargs)
        self.assertEqual(calls,[['/usr/bin/systemctl','start','sunhill-go2rtc.service']])
    db.close()
 def test_validated_config_and_static_commands(self):
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://host/live','ftp')");db.commit()
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);calls=[]
        def run(args,**kwargs):calls.append(args);return subprocess.CompletedProcess(args,0)
        with patch('sunhill_nvr.config_reconcile.os.chown'),patch('sunhill_nvr.config_reconcile.os.fchown'):
            reconcile(db,root,Path('/home/store/kamery'),1200,1200,run=run,trusted_check=lambda p:None,readiness=lambda service:True)
        self.assertTrue((root/'current/proftpd.conf').is_file());self.assertEqual(calls[0][0],'/usr/sbin/proftpd')
        self.assertTrue(all(c[0] in ('/usr/sbin/proftpd','/usr/bin/systemctl') for c in calls))
    db.close()
 def test_cold_ftp_validation_has_private_runtime_and_cleans_it(self):
    db=sqlite3.connect(':memory:');self.addCleanup(db.close);security.initialize(db);database.migrate(db)
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);calls=[];runtime_paths=[]
        def run(args,**kwargs):
            calls.append(args)
            if args[0]=='/usr/sbin/proftpd':
                check=Path(args[-1]);lines=dict(line.split(' ',1) for line in check.read_text().splitlines() if line.startswith(('PidFile ','ScoreboardFile ')))
                pid=Path(lines['PidFile']);score=Path(lines['ScoreboardFile']);runtime=score.parent
                self.assertEqual(pid.parent,runtime);self.assertEqual(runtime.parent,check.parent)
                self.assertTrue(runtime.name.startswith('validate-runtime-'));self.assertTrue(runtime.is_dir());self.assertEqual(runtime.stat().st_mode&0o777,0o700)
                self.assertNotEqual(runtime,Path('/run/sunhill-ftp'));self.assertFalse((root/'current').exists())
                score.write_text('parser temporary state');pid.touch();runtime_paths.append(runtime)
            return subprocess.CompletedProcess(args,0)
        with patch('sunhill_nvr.config_reconcile.os.chown'),patch('sunhill_nvr.config_reconcile.os.fchown'):
            reconcile(db,root,Path('/home/store/kamery'),1200,1200,run=run,trusted_check=lambda p:None,readiness=lambda service:True)
        self.assertEqual(len(runtime_paths),1);self.assertFalse(runtime_paths[0].exists())
        self.assertEqual(set(p.name for p in (root/'current').iterdir()),{'ftp.passwd','ftp.group','go2rtc.json','proftpd.conf'})
        self.assertIn('ScoreboardFile /run/sunhill-ftp/proftpd.scoreboard', (root/'current/proftpd.conf').read_text())
        self.assertEqual(calls[0][0],'/usr/sbin/proftpd')
 def test_rejected_ftp_validation_cleans_private_runtime(self):
    from sunhill_nvr.config_reconcile import ReconcileError
    db=sqlite3.connect(':memory:');self.addCleanup(db.close);security.initialize(db);database.migrate(db)
    with tempfile.TemporaryDirectory() as t:
        root=Path(t)
        def run(args,**kwargs):
            self.assertEqual(args[0],'/usr/sbin/proftpd')
            score=Path(next(line.split(' ',1)[1] for line in Path(args[-1]).read_text().splitlines() if line.startswith('ScoreboardFile ')))
            score.write_text('temporary state');return subprocess.CompletedProcess(args,1)
        with patch('sunhill_nvr.config_reconcile.os.chown'),patch('sunhill_nvr.config_reconcile.os.fchown'):
            with self.assertRaises(ReconcileError):reconcile(db,root,Path('/home/store/kamery'),1200,1200,run=run,trusted_check=lambda p:None,readiness=lambda service:True)
        self.assertEqual(list(root.iterdir()),[])
 def test_initial_failure_stops_gateway_and_ftp_without_orphaned_config(self):
    from sunhill_nvr.config_reconcile import ReconcileError
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);calls=[]
        def run(args,**kwargs):
            calls.append(args)
            fail=args[:3]==['/usr/bin/systemctl','is-active','--quiet'] and args[-1]=='sunhill-ftp.service'
            return subprocess.CompletedProcess(args,1 if fail else 0)
        with patch('sunhill_nvr.config_reconcile.os.chown'),patch('sunhill_nvr.config_reconcile.os.fchown'):
            with self.assertRaises(ReconcileError):reconcile(db,root,Path('/home/store/kamery'),1200,1200,run=run,trusted_check=lambda p:None,readiness=lambda service:True)
        self.assertIn(['/usr/bin/systemctl','stop','sunhill-go2rtc.service'],calls)
        self.assertIn(['/usr/bin/systemctl','stop','sunhill-ftp.service'],calls)
        self.assertFalse((root/'current').is_symlink());self.assertEqual(list(root.iterdir()),[])
    db.close()
 def test_failed_gateway_stop_still_attempts_ftp_stop(self):
    from sunhill_nvr.config_reconcile import ReconcileError
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if args[:2]==['/usr/bin/systemctl','is-active'] and args[-1]=='sunhill-ftp.service':return subprocess.CompletedProcess(args,1)
            if args[:2]==['/usr/bin/systemctl','stop'] and args[-1]=='sunhill-go2rtc.service':return subprocess.CompletedProcess(args,1)
            return subprocess.CompletedProcess(args,0)
        with patch('sunhill_nvr.config_reconcile.os.chown'),patch('sunhill_nvr.config_reconcile.os.fchown'):
            with self.assertRaises(ReconcileError):reconcile(db,root,Path('/home/store/kamery'),1200,1200,run=run,trusted_check=lambda p:None,readiness=lambda service:True)
        self.assertIn(['/usr/bin/systemctl','stop','sunhill-ftp.service'],calls)
        self.assertTrue((root/'current').is_symlink())
    db.close()
 def test_ftp_change_does_not_restart_camera_stream_service(self):
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);calls=[]
        def run(args,**kwargs):calls.append(args);return subprocess.CompletedProcess(args,0)
        with patch('sunhill_nvr.config_reconcile.os.chown'),patch('sunhill_nvr.config_reconcile.os.fchown'):
            reconcile(db,root,Path('/home/store/kamery'),1200,1200,run=run,trusted_check=lambda p:None,readiness=lambda service:True)
            calls.clear();db.execute('INSERT INTO ftp_accounts(username,password_hash) VALUES (?,?)',('camera',make_hash('camera-password-123')));db.commit()
            reconcile(db,root,Path('/home/store/kamery'),1200,1200,run=run,trusted_check=lambda p:None,readiness=lambda service:True)
        self.assertFalse(any('sunhill-go2rtc.service' in call for call in calls))
        self.assertTrue(any('sunhill-ftp.service' in call for call in calls))
    db.close()
 def test_ftp_server_configuration_change_is_reconciled(self):
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);calls=[]
        def run(args,**kwargs):calls.append(args);return subprocess.CompletedProcess(args,0)
        with patch('sunhill_nvr.config_reconcile.os.chown'),patch('sunhill_nvr.config_reconcile.os.fchown'):
            reconcile(db,root,Path('/home/store/kamery'),1200,1200,run=run,trusted_check=lambda p:None,readiness=lambda service:True)
            conf=root/'current'/'proftpd.conf';conf.write_text(conf.read_text().replace('User sunhill','User proftpd'))
            calls.clear()
            reconcile(db,root,Path('/home/store/kamery'),1200,1200,run=run,trusted_check=lambda p:None,readiness=lambda service:True)
        self.assertIn('User sunhill',conf.read_text())
        self.assertTrue(any('sunhill-ftp.service' in call and 'restart' in call for call in calls))
        self.assertFalse(any('sunhill-go2rtc.service' in call and 'restart' in call for call in calls))
    db.close()
 def test_untrusted_directory_rejected(self):
    with tempfile.TemporaryDirectory() as t:
        p=Path(t);p.chmod(0o777)
        with self.assertRaises(ControllerError):require_trusted_directory(p)
if __name__=='__main__':unittest.main()
