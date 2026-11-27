import os,sqlite3,tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import security,database
from sunhill_nvr.config_reconcile import *
class ReconcileTests(unittest.TestCase):
 def test_renders_and_rolls_back_configuration(self):
    db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
    db.execute("INSERT INTO cameras VALUES(1,'cam','Kamera',1)")
    db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://admin:secret@host/live','rtsp')")
    contents=desired_gateway(db);db.close()
    self.assertEqual(set(contents),{'go2rtc.json'});self.assertIn('rtsp://admin:secret@host/live',contents['go2rtc.json'])
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);apply(root,contents,validate=lambda p:True,restart=lambda changed:True)
        previous=(root/'current').resolve();checks=iter([False,True]);changed={'go2rtc.json':'{}'}
        with self.assertRaises(ReconcileError):apply(root,changed,validate=lambda p:True,restart=lambda changed:next(checks))
        self.assertEqual((root/'current').resolve(),previous)
        self.assertEqual((previous/'go2rtc.json').stat().st_mode&0o777,0o640)
        apply(root,contents,validate=lambda p:False,restart=lambda changed:False)
        self.assertEqual((root/'current').resolve(),previous)
 def test_interrupted_activation_restarts_all_services_even_if_config_matches(self):
    data={'go2rtc.json':'{}'}
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);calls=[]
        def crash(changed):raise KeyboardInterrupt('power loss before service restart')
        with self.assertRaises(KeyboardInterrupt):apply(root,data,validate=lambda p:True,restart=crash)
        self.assertTrue((root/'.activation-pending').is_file());self.assertTrue((root/'current/go2rtc.json').is_file())
        apply(root,data,validate=lambda p:True,restart=lambda changed:calls.append(changed) or True)
        self.assertEqual(calls,[frozenset(data)]);self.assertFalse((root/'.activation-pending').exists())
        apply(root,data,validate=lambda p:False,restart=lambda changed:self.fail('unexpected restart'))
 def test_sync_failure_after_pointer_switch_does_not_delete_active_config(self):
    from unittest.mock import patch
    from sunhill_nvr import config_reconcile as module
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);data={'go2rtc.json':'{}'};real=module._sync_dir
        def sync(path):
            if path==root and (root/'current').is_symlink():raise OSError('simulated disk sync failure')
            real(path)
        with patch.object(module,'_sync_dir',side_effect=sync):
            with self.assertRaises(OSError):apply(root,data,validate=lambda p:True,restart=lambda changed:True)
        self.assertTrue((root/'.activation-pending').exists());self.assertTrue((root/'current/go2rtc.json').is_file())
        apply(root,data,validate=lambda p:True,restart=lambda changed:True)
        self.assertFalse((root/'.activation-pending').exists())
 def test_initial_failure_stops_services_before_removing_configuration(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);data={'go2rtc.json':'{}'};events=[]
        def stop(changed):
            events.append((changed,(root/'current').is_symlink()))
            return True
        with self.assertRaises(ReconcileError):apply(root,data,validate=lambda p:True,restart=lambda changed:False,stop=stop)
        self.assertEqual(len(events),1);self.assertTrue(events[0][1]);self.assertFalse((root/'current').is_symlink())
        self.assertEqual(list(root.iterdir()),[])
 def test_failed_stop_keeps_configuration_for_running_service(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);data={'go2rtc.json':'{}'}
        with self.assertRaisesRegex(ReconcileError,'cannot stop services'):
            apply(root,data,validate=lambda p:True,restart=lambda changed:False,stop=lambda changed:False)
        self.assertTrue((root/'current').is_symlink());self.assertTrue((root/'current/go2rtc.json').is_file())
 def test_validation_failure_does_not_activate(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);data={'go2rtc.json':'{}'}
        with self.assertRaises(ReconcileError):apply(root,data,validate=lambda p:False,restart=lambda changed:True)
        self.assertFalse((root/'current').exists());self.assertEqual(list(root.iterdir()),[])
 def test_auth_files_are_group_readable_but_not_group_writable(self):
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);contents={'go2rtc.json':'{}'}
        with patch('sunhill_nvr.config_reconcile.os.chown'),patch('sunhill_nvr.config_reconcile.os.fchown') as change:
            apply(root,contents,validate=lambda p:True,restart=lambda changed:True,service_gid=1200)
        self.assertEqual(change.call_count,1)
        for name in contents:self.assertEqual((root/'current'/name).stat().st_mode&0o777,0o640)
 def test_reconcile_repairs_unsafe_permissions_and_rejects_symlink_files(self):
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);contents={'go2rtc.json':'{}'};restarts=[]
        with patch('sunhill_nvr.config_reconcile.os.chown'),patch('sunhill_nvr.config_reconcile.os.fchown'):
            apply(root,contents,validate=lambda p:True,restart=lambda changed:restarts.append(changed) or True,service_gid=1200)
            (root/'current/go2rtc.json').chmod(0o666)
            apply(root,contents,validate=lambda p:True,restart=lambda changed:restarts.append(changed) or True,service_gid=1200)
            self.assertEqual((root/'current/go2rtc.json').stat().st_mode&0o777,0o640)
            self.assertEqual(len(restarts),2)
            target=root/'current/go2rtc.json';target.unlink();target.symlink_to(root/'outside')
            with self.assertRaisesRegex(ReconcileError,'unsafe file'):
                apply(root,contents,validate=lambda p:True,restart=lambda changed:True,service_gid=1200)
 def test_unsafe_previous_snapshot_is_not_restored_after_failed_activation(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);data={'go2rtc.json':'{}'}
        apply(root,data,validate=lambda p:True,restart=lambda changed:True)
        old=(root/'current').resolve();(old/'go2rtc.json').chmod(0o666)
        calls=[];candidate=dict(data,**{'go2rtc.json':'{"streams":{}}'})
        with self.assertRaisesRegex(ReconcileError,'unsafe previous configuration'):
            apply(root,candidate,validate=lambda p:True,restart=lambda changed:calls.append(('restart',changed)) or False,
                  stop=lambda changed:calls.append(('stop',changed)) or True)
        self.assertNotEqual((root/'current').resolve(),old)
        self.assertEqual((root/'current/go2rtc.json').stat().st_mode&0o777,0o640)
        self.assertEqual(calls,[('restart',frozenset(('go2rtc.json',))),('stop',frozenset(data))])
        self.assertTrue((root/'.activation-pending').is_file())
        apply(root,candidate,validate=lambda p:True,restart=lambda changed:calls.append(('retry',changed)) or True)
        self.assertEqual(calls[-1],('retry',frozenset(data)))
        self.assertFalse((root/'.activation-pending').exists())
 def test_missing_previous_file_is_not_a_rollback_target(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);data={'go2rtc.json':'{}'}
        apply(root,data,validate=lambda p:True,restart=lambda changed:True)
        old=(root/'current').resolve();(old/'go2rtc.json').unlink()
        with self.assertRaisesRegex(ReconcileError,'unsafe previous configuration'):
            apply(root,data,validate=lambda p:True,restart=lambda changed:False,stop=lambda changed:True)
        self.assertNotEqual((root/'current').resolve(),old)
        self.assertTrue((root/'current/go2rtc.json').is_file())
        self.assertTrue((root/'.activation-pending').is_file())
if __name__=='__main__':unittest.main()
