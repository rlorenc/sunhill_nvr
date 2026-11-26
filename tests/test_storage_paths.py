import os,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sunhill_nvr import storage_paths as sp, mount

class StoragePathsTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.execute('CREATE TABLE system_settings(key TEXT PRIMARY KEY,value TEXT)')
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup);self.addCleanup(self.db.close)
    def test_kernel_mountinfo_without_findmnt_or_marker(self):
        # Container uses overlay, Ubuntu production requires a supported local FS.
        with patch.object(mount,'LOCAL_FILESYSTEMS',mount.LOCAL_FILESYSTEMS|{'overlay','tmpfs'}),patch.object(mount.subprocess,'run',side_effect=AssertionError('findmnt must not run')):
            target,dev,inode,mid=sp.mounted(self.root)
            self.assertEqual(dev,self.root.stat().st_dev);self.assertEqual(inode,self.root.stat().st_ino);self.assertGreater(mid,0)
            self.assertTrue(sp.guard(self.db,self.root)());self.assertFalse((self.root/'.sunhill-volume-id').exists())
    def test_legacy_uuid_and_marker_are_ignored_after_replacement(self):
        loc=self.root/'disk';archive=loc/'archive';loc.mkdir();archive.mkdir()
        self.db.execute('INSERT INTO system_settings VALUES(?,?)',('archive_mount','{"target":"'+str(loc)+'","uuid":"obsolete"}'))
        self.db.execute('INSERT INTO system_settings VALUES(?,?)',('archive_marker','obsolete'))
        real=os.stat('/').st_dev
        states={loc:(loc,real+1,1,10),archive:(loc,real+1,2,10)}
        with patch.object(sp,'mounted',side_effect=lambda p:states[p]):
            old=sp.guard(self.db,archive);self.assertTrue(old())
            states.update({loc:(loc,real+2,3,20),archive:(loc,real+2,4,20)})
            self.assertFalse(old());self.assertTrue(sp.guard(self.db,archive)())
            states.update({loc:(Path('/'),real,1,1),archive:(Path('/'),real,2,1)})
            with self.assertRaisesRegex(RuntimeError,'unavailable'):sp.guard(self.db,archive)
    def test_create_missing_leaf_and_nested_paths_without_touching_existing_files(self):
        cfg=SimpleNamespace(archive=self.root/'kamery',staging=self.root/'kamery/.stage',exports=self.root/'kamery/.exports')
        with patch.object(mount,'LOCAL_FILESYSTEMS',mount.LOCAL_FILESYSTEMS|{'overlay','tmpfs'}):
            sp.prepare(self.db,cfg,os.getuid(),os.getgid());file=cfg.archive/'keep.mp4';file.write_text('recording')
            sp.prepare(self.db,cfg,os.getuid(),os.getgid())
            self.assertEqual(file.read_text(),'recording');self.assertTrue(cfg.staging.is_dir());self.assertTrue(cfg.exports.is_dir());self.assertFalse((cfg.archive/'.sunhill-volume-id').exists())
    def test_missing_data_mount_never_creates_fallback_dirs(self):
        loc=self.root/'disk';loc.mkdir();archive=loc/'kamery';cfg=SimpleNamespace(archive=archive,staging=archive/'.stage',exports=archive/'.exports')
        self.db.execute('INSERT INTO system_settings VALUES(?,?)',('archive_mountpoint',str(loc)))
        with patch.object(sp,'mounted',return_value=(Path('/'),os.stat('/').st_dev,1,1)):
            with self.assertRaisesRegex(RuntimeError,'unavailable'):sp.prepare(self.db,cfg,os.getuid(),os.getgid())
        self.assertFalse(archive.exists())
