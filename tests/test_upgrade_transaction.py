from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from sys import path

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.release import ReleaseError
from sunhill_nvr.upgrade_transaction import Operations, activate, recover, recover_offline, finalize_offline


class UpgradeTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.versions=self.root/'releases';self.versions.mkdir();self.state=self.root/'state';self.state.mkdir()
        for version in ('1.0.0','1.1.0'):
            release=self.versions/version;release.mkdir();(release/'manifest.json').write_text('{}')
        self.current=self.root/'current';self.current.symlink_to('releases/1.0.0')
        self.db=self.root/'data.sqlite'
        with closing(sqlite3.connect(self.db)) as db, db:db.execute('CREATE TABLE data (value INTEGER)');db.execute('INSERT INTO data VALUES(1)')
        self.running=True;self.fenced=False;self.fail_unfence=False;self.fail_health=False;self.fail_restore=False;self.crash=False;self.fail_migrate=False
        self.with_system=False;self.system_value='old';self.fail_system=False;self.crash_system=False

    def tearDown(self):self.tmp.cleanup()

    def operations(self):
        def fence():self.fenced=True
        def unfence():
            if self.fail_unfence:raise RuntimeError('maintenance could not be disabled')
            self.fenced=False
        def stop():self.running=False
        def assert_stopped():
            if self.running:raise RuntimeError('still running')
        def start():self.running=True
        def healthy():self.assertTrue(self.fenced);return not self.fail_health
        def backup(destination):
            with closing(sqlite3.connect(self.db)) as src, src,closing(sqlite3.connect(destination)) as dst, dst:src.backup(dst)
        def restore(source):
            if self.fail_restore:raise RuntimeError('restore failed')
            with closing(sqlite3.connect(source)) as src, src,closing(sqlite3.connect(self.db)) as dst, dst:src.backup(dst)
        def migrate(candidate):
            self.assertFalse(self.running);self.assertTrue(self.fenced)
            with closing(sqlite3.connect(self.db)) as db, db:db.execute('UPDATE data SET value=2')
            if self.crash:raise KeyboardInterrupt()
            if self.fail_migrate:raise RuntimeError('migration failed')
        def backup_system(candidate,destination):
            destination.mkdir();(destination/'previous').write_text(self.system_value);(destination/'complete').write_text('complete')
        def apply_system(destination):
            self.system_value='new'
            if self.crash_system:raise KeyboardInterrupt()
            if self.fail_system:raise RuntimeError('config failed')
        def restore_system(destination):self.system_value=(destination/'previous').read_text()
        extra=(backup_system,apply_system,restore_system,restore_system,lambda:None) if self.with_system else ()
        return Operations(fence,unfence,stop,assert_stopped,start,healthy,backup,restore,migrate,*extra)

    def value(self):
        with closing(sqlite3.connect(self.db)) as db, db:return db.execute('SELECT value FROM data').fetchone()[0]

    def test_successful_update_and_backup_cleanup(self):
        activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.assertEqual(self.current.resolve().name,'1.1.0');self.assertEqual(self.value(),2)
        self.assertTrue(self.running);self.assertFalse(self.fenced);self.assertFalse((self.state/'transaction.json').exists())
        self.assertEqual(list(self.state.glob('backup-*')),[])

    def test_repair_supersedes_rolled_back_without_starting_old_release(self):
        import json
        from sunhill_nvr.upgrade_transaction import _write_journal
        prior=self.state/('backup-'+'1'*32+'.sqlite');prior.write_bytes(b'old backup')
        sysbackup=self.state/('config-'+'2'*32);sysbackup.mkdir();(sysbackup/'complete').touch()
        _write_journal(self.state,{'old':'1.0.0','new':'1.1.0','phase':'rolled_back','backup':prior.name,'system_backup':sysbackup.name})
        self.fenced=True;self.running=False
        with closing(sqlite3.connect(self.db)) as db,db:db.execute('UPDATE data SET value=99')
        ops=self.operations();from dataclasses import replace
        def migrate(candidate):
            self.assertEqual(self.value(),99);self.assertFalse(self.running);ops.migrate(candidate)
        def start():
            self.assertEqual(self.current.resolve().name,'1.1.0');ops.start()
        activate(self.versions,self.current,self.state,'1.1.0',replace(ops,migrate=migrate,start=start),supersede_rollback=True)
        self.assertEqual(self.current.resolve().name,'1.1.0');self.assertFalse(self.fenced)
        preserved=list(self.state.glob('superseded-*.json'));self.assertEqual(len(preserved),1)
        self.assertEqual(json.loads(preserved[0].read_text())['backup'],prior.name)
        self.assertEqual(prior.read_bytes(),b'old backup');self.assertTrue(sysbackup.is_dir());self.assertFalse((self.state/'transaction.json').exists())
    def test_repair_refuses_nonterminal_journal(self):
        from sunhill_nvr.upgrade_transaction import _write_journal
        _write_journal(self.state,{'old':'1.0.0','new':'1.1.0','phase':'migrating','backup':'backup-'+'1'*32+'.sqlite'})
        with self.assertRaisesRegex(ReleaseError,'requires recovery'):
            activate(self.versions,self.current,self.state,'1.1.0',self.operations(),supersede_rollback=True)
        self.assertTrue((self.state/'transaction.json').exists());self.assertFalse(list(self.state.glob('superseded-*.json')))

    def test_signed_old_release_cannot_be_replayed_as_an_update(self):
        self.current.unlink();self.current.symlink_to('releases/1.1.0')
        with self.assertRaises(ReleaseError):activate(self.versions,self.current,self.state,'1.0.0',self.operations())
        self.assertEqual(self.current.resolve().name,'1.1.0');self.assertEqual(self.value(),1);self.assertFalse(self.fenced)

    def test_failed_migration_restores_database_and_version(self):
        self.fail_migrate=True
        with self.assertRaises(ReleaseError):activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.assertEqual(self.current.resolve().name,'1.0.0');self.assertEqual(self.value(),1)
        self.assertTrue(self.running);self.assertFalse(self.fenced);self.assertFalse((self.state/'transaction.json').exists())

    def test_interruption_requires_explicit_recovery_before_next_upgrade(self):
        self.crash=True
        with self.assertRaises(KeyboardInterrupt):activate(self.versions,self.current,self.state,'1.1.0',self.operations(),request_id='a'*32)
        self.assertEqual(self.value(),2);self.assertTrue(self.fenced);self.assertTrue((self.state/'transaction.json').exists())
        import json
        self.assertEqual(json.loads((self.state/'transaction.json').read_text())['request_id'],'a'*32)
        self.crash=False
        with self.assertRaises(ReleaseError):activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.assertTrue(recover(self.versions,self.current,self.state,self.operations()))
        self.assertEqual(self.current.resolve().name,'1.0.0');self.assertEqual(self.value(),1);self.assertTrue(self.running)
        self.assertFalse(recover(self.versions,self.current,self.state,self.operations()))

    def test_failed_recovery_keeps_backup_and_journal(self):
        self.fail_migrate=True;self.fail_restore=True
        with self.assertRaises(ReleaseError):activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.assertTrue((self.state/'transaction.json').exists());self.assertEqual(len(list(self.state.glob('backup-*'))),1)
        self.fail_restore=False;self.fail_migrate=False
        self.assertTrue(recover(self.versions,self.current,self.state,self.operations()))
        self.assertEqual(self.value(),1)

    def test_recovery_health_failure_does_not_repeat_database_restore(self):
        import json
        self.fail_migrate=True;self.fail_health=True
        with self.assertRaises(ReleaseError):activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.assertEqual(json.loads((self.state/'transaction.json').read_text())['phase'],'rolled_back')
        self.assertEqual(self.current.resolve().name,'1.0.0');self.assertEqual(self.value(),1)
        self.assertTrue(self.fenced);self.assertTrue(self.running)
        # Model an older release whose background writer does not honor maintenance.
        with closing(sqlite3.connect(self.db)) as db, db:db.execute('UPDATE data SET value=77')
        self.fail_health=False
        self.assertTrue(recover(self.versions,self.current,self.state,self.operations()))
        self.assertEqual(self.value(),77);self.assertFalse(self.fenced)
        self.assertFalse((self.state/'transaction.json').exists())

    def test_committed_update_is_not_rolled_back_if_maintenance_cleanup_fails(self):
        self.fail_unfence=True
        with self.assertRaises(ReleaseError):activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.assertEqual(self.current.resolve().name,'1.1.0');self.assertEqual(self.value(),2);self.assertTrue(self.fenced)
        self.assertTrue((self.state/'transaction.json').exists())
        self.fail_unfence=False
        self.assertTrue(recover(self.versions,self.current,self.state,self.operations()))
        self.assertEqual(self.current.resolve().name,'1.1.0');self.assertEqual(self.value(),2);self.assertFalse(self.fenced)

    def test_boot_recovery_restores_before_service_start_and_keeps_maintenance(self):
        self.crash=True
        with self.assertRaises(KeyboardInterrupt):activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.assertTrue(recover_offline(self.versions,self.current,self.state,self.operations()))
        self.assertFalse(self.running);self.assertTrue(self.fenced);self.assertEqual(self.value(),1)
        self.fail_health=True;self.running=True
        with self.assertRaises(ReleaseError):finalize_offline(self.versions,self.current,self.state,self.operations())
        self.assertTrue(self.fenced);self.assertTrue((self.state/'transaction.json').exists())
        self.fail_health=False
        self.assertTrue(finalize_offline(self.versions,self.current,self.state,self.operations()))
        self.assertFalse(self.fenced);self.assertFalse((self.state/'transaction.json').exists())

    def test_system_configuration_and_database_roll_back_together(self):
        self.with_system=True;self.fail_system=True
        with self.assertRaises(ReleaseError):activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.assertEqual(self.value(),1);self.assertEqual(self.system_value,'old');self.assertEqual(self.current.resolve().name,'1.0.0')
        self.assertFalse(self.fenced);self.assertEqual(list(self.state.glob('config-*')),[])

    def test_interrupted_configuration_update_is_recovered_before_start(self):
        self.with_system=True;self.crash_system=True
        with self.assertRaises(KeyboardInterrupt):activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.assertEqual(self.system_value,'new');self.assertEqual(self.value(),2);self.assertTrue(self.fenced)
        self.assertTrue(recover_offline(self.versions,self.current,self.state,self.operations()))
        self.assertEqual(self.system_value,'old');self.assertEqual(self.value(),1);self.assertFalse(self.running)
        self.running=True;self.assertTrue(finalize_offline(self.versions,self.current,self.state,self.operations()))

    def test_offline_finalization_starts_units_restored_during_boot(self):
        self.with_system=True;self.crash_system=True
        with self.assertRaises(KeyboardInterrupt):activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.assertTrue(recover_offline(self.versions,self.current,self.state,self.operations()))
        self.assertFalse(self.running)
        self.assertTrue(finalize_offline(self.versions,self.current,self.state,self.operations()))
        self.assertTrue(self.running);self.assertFalse(self.fenced)

    def test_online_recovery_after_offline_rollback_keeps_fence_until_healthy(self):
        self.with_system=True;self.crash_system=True
        with self.assertRaises(KeyboardInterrupt):activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.assertTrue(recover_offline(self.versions,self.current,self.state,self.operations()))
        self.assertFalse(self.running);self.fail_health=True
        with self.assertRaisesRegex(ReleaseError,'not healthy'):recover(self.versions,self.current,self.state,self.operations())
        self.assertTrue(self.fenced);self.assertTrue((self.state/'transaction.json').exists())
        self.fail_health=False;self.assertTrue(recover(self.versions,self.current,self.state,self.operations()))
        self.assertTrue(self.running);self.assertFalse(self.fenced);self.assertEqual(self.value(),1)

    def test_finalization_reestablishes_fence_after_cleanup_interruption(self):
        self.fail_unfence=True
        with self.assertRaises(ReleaseError):activate(self.versions,self.current,self.state,'1.1.0',self.operations())
        self.fail_unfence=False;self.fenced=False;self.running=False;self.fail_health=True
        with self.assertRaisesRegex(ReleaseError,'not healthy'):finalize_offline(self.versions,self.current,self.state,self.operations())
        self.assertTrue(self.fenced);self.assertTrue((self.state/'transaction.json').exists())
        self.fail_health=False;self.assertTrue(finalize_offline(self.versions,self.current,self.state,self.operations()))
        self.assertTrue(self.running);self.assertFalse(self.fenced);self.assertEqual(self.value(),2)


if __name__=='__main__':unittest.main()
