import os, tempfile, unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.rotation import unlink_confined
class SafeDeleteTests(unittest.TestCase):
    def test_deletes_exact_file(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);f=root/'cam'/'2026'/'11'/'21'/'190000.mp4';f.parent.mkdir(parents=True);f.write_text('x')
            s=f.stat();unlink_confined(root,f,expected_device=s.st_dev,expected_inode=s.st_ino)
            self.assertFalse(f.exists())
    def test_rejects_replaced_file(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);f=root/'cam'/'2026'/'11'/'21'/'190000.mp4';f.parent.mkdir(parents=True);f.write_text('x')
            s=f.stat();f.unlink();f.symlink_to('/etc/passwd')
            with self.assertRaises((RuntimeError,OSError)):
                unlink_confined(root,f,expected_device=s.st_dev,expected_inode=s.st_ino)
            self.assertTrue(f.is_symlink())
    def test_rejects_same_inode_with_changed_content(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);f=root/'cam'/'2026'/'11'/'21'/'190000.mp4';f.parent.mkdir(parents=True);f.write_text('x')
            s=f.stat();f.write_text('new content')
            with self.assertRaisesRegex(RuntimeError,'content changed'):
                unlink_confined(root,f,expected_device=s.st_dev,expected_inode=s.st_ino,expected_metadata=(s.st_size,s.st_mtime_ns,s.st_ctime_ns))
            self.assertEqual(f.read_text(),'new content')
    def test_rejects_parent_symlink(self):
        with tempfile.TemporaryDirectory() as t, tempfile.TemporaryDirectory() as other:
            root=Path(t);external=Path(other);f=external/'2026'/'11'/'21'/'190000.mp4';f.parent.mkdir(parents=True);f.write_text('x')
            (root/'cam').symlink_to(external, target_is_directory=True);s=f.stat()
            with self.assertRaises(OSError):
                unlink_confined(root,root/'cam'/'2026'/'11'/'21'/'190000.mp4',expected_device=s.st_dev,expected_inode=s.st_ino)
            self.assertTrue(f.exists())
if __name__=='__main__':unittest.main()
