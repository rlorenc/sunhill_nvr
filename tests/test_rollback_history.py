import hashlib,json,os,sqlite3,tempfile,unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import rollback_history as history, lifecycle
from sunhill_nvr.upgrade_system import SystemAdapter
from sunhill_nvr.upgrade_transaction import Operations,activate,recover
from sunhill_nvr.release import ReleaseError


@unittest.skipUnless(os.geteuid()==0,'root-owned restore points')
class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.state=self.root/'state';self.state.mkdir();self.versions=self.root/'releases';self.versions.mkdir();self.current=self.root/'current';self.current.symlink_to('releases/1.0.0')
        for i in range(8):
            version=f'1.0.{i}';p=self.versions/version;p.mkdir();(p/'app').mkdir();content=version.encode();(p/'app/version').write_bytes(content)
            (p/'manifest.json').write_text(json.dumps({'version':version,'files':{'app/version':hashlib.sha256(content).hexdigest()}}))
        self.config=self.root/'config';self.config.write_text('{}');self.db=self.root/'database';self.db.write_text('0');self.media=self.root/'recording.mp4';self.media.write_bytes(b'preserve')
        self.adapter=SystemAdapter(self.config,self.current,self.state,self.root/'maintenance',0,0,tls_certificate=self.root/'cert',tls_key=self.root/'key')
        self.running=True;self.fenced=False;self.fail_version=None;self.crash=False;self.system='1.0.0'
    def operations(self):
        def fence():self.fenced=True
        def unfence():self.fenced=False
        def stop():self.running=False
        def start():self.running=True
        def backup(dest):dest.write_bytes(self.db.read_bytes());dest.chmod(0o600)
        def restore(src):self.db.write_bytes(src.read_bytes())
        def migrate(candidate):self.db.write_text(candidate.name.split('.')[-1])
        def system_backup(candidate,destination):
            destination.mkdir(mode=0o700);(destination/'previous').write_text(self.system);(destination/'next').write_text(candidate.name);(destination/'complete').write_bytes(b'complete\n');self.adapter._save_rollback_context(destination)
        def system_apply(folder):
            self.system=(folder/'next').read_text()
            if self.crash:raise KeyboardInterrupt()
        def system_restore(folder):self.system=(folder/'previous').read_text()
        return Operations(fence,unfence,stop,lambda:self.assertFalse(self.running),start,lambda:self.running and self.fenced and self.current.resolve().name!=self.fail_version,backup,restore,migrate,system_backup,system_apply,system_restore,system_restore,lambda:None,self.adapter.retain_rollback)
    def upgrade(self,version):activate(self.versions,self.current,self.state,version,self.operations())
    def manual(self,version=None,token=None):
        def prepare(folder,destination):
            self.operations().backup_system(self.versions/(folder/'previous').read_text(),destination)
        with patch.object(SystemAdapter,'operations',lambda _:self.operations()),patch.object(SystemAdapter,'backup_rollback_system',lambda _,f,d:prepare(f,d)),patch.object(SystemAdapter,'restore_retained',lambda _,db:self.db.write_bytes(db.read_bytes())):
            return history.rollback(self.adapter,self.versions,self.current,version=version,token=token)
    def test_retention_restore_and_return(self):
        self.upgrade('1.0.1');self.db.write_text('later');self.upgrade('1.0.2');self.assertEqual([r['version'] for r in history.read(self.state)],['1.0.1','1.0.0'])
        self.assertEqual(self.manual('1.0.0'),'1.0.0');self.assertEqual(self.db.read_text(),'0');self.assertEqual(self.system,'1.0.0')
        self.assertEqual(self.manual(),'1.0.2');self.assertEqual(self.db.read_text(),'2');self.assertEqual(self.media.read_bytes(),b'preserve');self.assertFalse(self.fenced)
    def test_failed_restore_reverts_and_keeps_restore_point(self):
        self.upgrade('1.0.1');self.fail_version='1.0.0';before=history.read(self.state)
        with self.assertRaises(ReleaseError):self.manual()
        self.assertEqual(self.current.resolve().name,'1.0.1');self.assertEqual(self.db.read_text(),'1');self.assertEqual(self.system,'1.0.1');self.assertEqual(history.read(self.state),before);self.assertFalse(self.fenced)
    def test_interruption_recovered_without_losing_restore_point(self):
        self.upgrade('1.0.1');self.crash=True
        with self.assertRaises(KeyboardInterrupt):self.manual()
        self.crash=False;self.assertTrue(recover(self.versions,self.current,self.state,self.operations()));self.assertEqual(self.db.read_text(),'1');self.assertEqual(self.current.resolve().name,'1.0.1');self.assertEqual(len(history.read(self.state)),1)
    def test_expiry_and_rejection(self):
        for i in range(1,8):self.upgrade(f'1.0.{i}')
        self.assertEqual(len(history.read(self.state)),5);self.assertEqual(len(list(self.state.glob('backup-*.sqlite'))),5)
        with self.assertRaisesRegex(ReleaseError,'no retained'):self.manual('1.0.0')
        self.config.write_text('{"changed":true}')
        with self.assertRaisesRegex(ReleaseError,'configuration changed'):self.manual()
        self.assertEqual(self.current.resolve().name,'1.0.7');self.assertFalse(self.fenced)
    def test_tampered_release_rejected(self):
        self.upgrade('1.0.1');(self.versions/'1.0.0/app/version').write_text('tampered')
        with self.assertRaisesRegex(ReleaseError,'integrity check'):self.manual()
        self.assertEqual(self.current.resolve().name,'1.0.1')
    def test_retention_idempotent_after_commit_crash(self):
        ops=self.operations()
        def crash(journal):
            self.adapter.retain_rollback(journal);raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):activate(self.versions,self.current,self.state,'1.0.1',replace(ops,retain=crash))
        self.assertTrue(recover(self.versions,self.current,self.state,ops));self.assertEqual(len(history.read(self.state)),1);self.assertEqual(len(list(self.state.glob('backup-*.sqlite'))),1);self.assertFalse(self.fenced)
    def test_request_requires_exact_retained_id_and_one_pending(self):
        self.upgrade('1.0.1');inbox=self.root/'inbox';inbox.mkdir(mode=0o700);row=history.read(self.state)[0]
        with self.assertRaises(ReleaseError):history.submit(inbox,self.state,'0'*32,'1.0.1',1)
        history.submit(inbox,self.state,row['id'],'1.0.1',1)
        with self.assertRaises(FileExistsError):history.submit(inbox,self.state,row['id'],'1.0.1',1)
        self.assertEqual(json.loads((inbox/history.REQUEST).read_text())['expected'],'1.0.1')

if __name__=='__main__':unittest.main()
