import tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import storage_transaction as tx

class StorageTransactionTests(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.state=Path(self.tmp.name)
    self.flags={'fenced':False,'running':True,'config':'old'}
    f=self.flags
    def fence():f['fenced']=True
    def unfence():f['fenced']=False
    def stop():f['running']=False
    def assert_stopped():
        if f['running']:raise RuntimeError('still running')
    def start():f['running']=True
    def healthy():return f['running'] and f['fenced']
    def backup(directory):directory.mkdir();(directory/'config').write_text(f['config'])
    def apply(directory):f['config']=str(directory)
    def restore(directory):f['config']=(directory/'config').read_text()
    self.ops=tx.Operations(fence,unfence,stop,assert_stopped,start,healthy,backup,apply,restore)
 def tearDown(self):self.tmp.cleanup()
 def test_success_ends_maintenance_and_keeps_new_configuration(self):
    tx.activate(self.state,Path('/new'),self.ops)
    self.assertEqual(self.flags,{'fenced':False,'running':True,'config':'/new'})
    self.assertIsNone(tx._read(self.state))
 def test_failed_health_restores_previous_configuration(self):
    old=self.ops
    def healthy():return self.flags['config']=='old' and old.healthy()
    ops=tx.Operations(old.fence,old.unfence,old.stop,old.assert_stopped,old.start,healthy,old.backup,old.apply,old.restore)
    with self.assertRaises(Exception):tx.activate(self.state,Path('/new'),ops)
    self.assertEqual(self.flags,{'fenced':False,'running':True,'config':'old'})
    self.assertIsNone(tx._read(self.state))
 def test_crash_restores_offline_then_finalizes(self):
    old=self.ops
    def crash(path):self.flags['config']='partial';raise KeyboardInterrupt()
    ops=tx.Operations(old.fence,old.unfence,old.stop,old.assert_stopped,old.start,old.healthy,old.backup,crash,old.restore)
    with self.assertRaises(KeyboardInterrupt):tx.activate(self.state,Path('/new'),ops)
    self.assertEqual(tx._read(self.state)['phase'],'applying')
    self.assertTrue(tx.recover(self.state,self.ops));self.assertEqual(self.flags['config'],'old')
    self.assertTrue(self.flags['fenced']);self.assertFalse(self.flags['running'])
    self.ops.start();self.assertTrue(tx.finalize(self.state,self.ops));self.assertFalse(self.flags['fenced'])
    self.assertIsNone(tx._read(self.state))
 def test_crash_before_backup_never_attempts_restore(self):
    old=self.ops
    def crash(path):raise KeyboardInterrupt()
    ops=tx.Operations(old.fence,old.unfence,old.stop,old.assert_stopped,old.start,old.healthy,crash,old.apply,old.restore)
    with self.assertRaises(KeyboardInterrupt):tx.activate(self.state,Path('/new'),ops)
    self.assertTrue(tx.recover(self.state,self.ops));self.assertEqual(self.flags['config'],'old')

 def test_finalize_reestablishes_fence_after_interrupted_cleanup(self):
    from dataclasses import replace
    original=self.ops
    def interrupted_unfence():
        original.unfence()
        raise KeyboardInterrupt('crash after removing maintenance')
    with self.assertRaises(KeyboardInterrupt):
        tx.activate(self.state,Path('/new'),replace(original,unfence=interrupted_unfence))
    self.assertFalse(self.flags['fenced'])
    self.assertEqual(tx._read(self.state)['phase'],'committed')
    self.flags['running']=False
    with self.assertRaisesRegex(RuntimeError,'services not healthy'):
        tx.finalize(self.state,replace(original,healthy=lambda:False))
    self.assertTrue(self.flags['fenced'])
    self.assertEqual(tx._read(self.state)['phase'],'committed')
    self.assertTrue(tx.finalize(self.state,original))
    self.assertEqual(self.flags,{'fenced':False,'running':True,'config':'/new'})
    self.assertIsNone(tx._read(self.state))

if __name__=='__main__':unittest.main()
