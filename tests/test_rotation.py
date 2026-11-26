import os
import tempfile
import time
import unittest
from pathlib import Path
from sys import path
path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sunhill_nvr.rotation import recording_info, list_recordings, rotate

class RotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def make(self, rel, data=b'abc'):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        os.utime(p, (time.time()-3600, time.time()-3600))
        return p
    def test_original_renamed_and_sorted(self):
        p = self.make('brama/2026/11/21/190900.mp4')
        q = self.make('brama/2026/11/20/Brama_00_20261120180900.mp4')
        self.assertEqual([q,p], [r.path for r in list_recordings(self.root)])
    def test_nested_auxiliary_never_scanned_or_rotated(self):
        from sunhill_nvr.rotation import unlink_confined
        a=self.make('cam/2026/11/20/100000.mp4')
        others=[self.make(f'{name}/2026/11/19/100000.mp4') for name in ('.sunhill-staging','.sunhill-exports')]
        self.assertEqual([r.path for r in list_recordings(self.root)],[a])
        for p in others:
            self.assertIsNone(recording_info(self.root,p))
            with self.assertRaises(ValueError):unlink_confined(self.root,p,expected_device=p.stat().st_dev,expected_inode=p.stat().st_ino)
        rotate(self.root,10,11,lambda:True,lambda:8,lambda p:p.unlink(),lambda p:False,False)
        self.assertFalse(a.exists());self.assertTrue(all(p.exists() for p in others))
    def test_sort_uses_offsets_across_repeated_hour(self):
        earlier=self.make('cam/2026/10/25/20261025T025900+0200.mp4')
        later=self.make('cam/2026/10/25/20261025T020000+0100.mp4')
        self.assertEqual([r.path for r in list_recordings(self.root)],[earlier,later])
    def test_rejects_nonvideo_bad_date_and_symlinks(self):
        self.assertIsNone(recording_info(self.root, self.make('brama/2026/11/21/keep.txt')))
        self.assertIsNone(recording_info(self.root, self.make('brama/2026/11/21/Brama_00_20261120180900.mp4')))
        a = self.make('brama/2026/11/21/190900.mp4')
        b = a.parent / '191000.mp4'; b.symlink_to(a)
        self.assertIsNone(recording_info(self.root, b))
    def test_only_oldest_and_no_delete_in_preview(self):
        a = self.make('a/2026/11/20/100000.mp4')
        b = self.make('b/2026/11/21/100000.mp4')
        output = rotate(self.root, 10, 12, lambda: True, lambda: 8, lambda p:p.unlink(), lambda p:False)
        self.assertEqual(output['files'], [str(a), str(b)])
        self.assertTrue(a.exists()); self.assertTrue(b.exists())
    def test_mount_missing_fails_closed(self):
        a = self.make('a/2026/11/20/100000.mp4')
        with self.assertRaises(RuntimeError):
            rotate(self.root, 10, 12, lambda:False, lambda:0, lambda p:p.unlink(), lambda p:False, False)
        self.assertTrue(a.exists())
    def test_protected_file_skipped(self):
        a = self.make('a/2026/11/20/100000.mp4')
        b = self.make('b/2026/11/21/100000.mp4')
        output = rotate(self.root, 10, 11, lambda:True, lambda:8, lambda p:p.unlink(), lambda p:p==a)
        self.assertEqual(output['files'], [str(b)])

    def test_unreadable_recognized_day_aborts_before_deleting_anything(self):
        from unittest.mock import patch
        old=self.make('a/2026/11/20/100000.mp4')
        newer=self.make('b/2026/11/21/100000.mp4')
        real_open=os.open
        def denied(path,flags,*args,**kwargs):
            if Path(path)==old.parent:raise PermissionError('unreadable day')
            return real_open(path,flags,*args,**kwargs)
        with patch('sunhill_nvr.rotation.os.open',side_effect=denied):
            with self.assertRaisesRegex(RuntimeError,'directory'):
                rotate(self.root,10,11,lambda:True,lambda:8,lambda p:p.unlink(),lambda p:False,False)
        self.assertTrue(old.exists());self.assertTrue(newer.exists())

    def test_unrelated_directories_are_not_scanned(self):
        from unittest.mock import patch
        recording=self.make('a/2026/11/20/100000.mp4')
        unrelated=self.root/'unrelated folder';unrelated.mkdir()
        real_open=os.open
        def denied(path,flags,*args,**kwargs):
            if Path(path)==unrelated:raise PermissionError('unrelated')
            return real_open(path,flags,*args,**kwargs)
        with patch('sunhill_nvr.rotation.os.open',side_effect=denied):
            self.assertEqual([r.path for r in list_recordings(self.root)],[recording])

    def test_permission_error_during_file_recheck_is_not_silenced(self):
        from unittest.mock import patch
        recording=self.make('a/2026/11/20/100000.mp4');original=Path.lstat;calls=[]
        def lstat(path,*args,**kwargs):
            if path==recording:
                calls.append(path)
                if len(calls)>1:raise PermissionError('denied')
            return original(path,*args,**kwargs)
        with patch.object(Path,'lstat',lstat):
            with self.assertRaisesRegex(RuntimeError,'file'):
                list_recordings(self.root)
        self.assertTrue(recording.exists())

    def test_file_that_becomes_recent_during_scan_is_not_eligible(self):
        from unittest.mock import patch
        recording=self.make('a/2026/11/20/100000.mp4');os.utime(recording,(1,1));original=Path.lstat;calls=[]
        def lstat(path,*args,**kwargs):
            if path==recording:
                calls.append(path)
                if len(calls)==2:os.utime(path,(10000,10000))
            return original(path,*args,**kwargs)
        with patch.object(Path,'lstat',lstat):
            self.assertEqual(list_recordings(self.root,now=10000),[])
        self.assertTrue(recording.exists())

    def test_directory_enumeration_error_aborts_scan(self):
        from unittest.mock import patch
        recording=self.make('a/2026/11/20/100000.mp4');real_scandir=os.scandir
        def scandir(path):
            if Path(path)==recording.parent:raise PermissionError('cannot enumerate')
            return real_scandir(path)
        with patch('sunhill_nvr.rotation.os.scandir',side_effect=scandir):
            with self.assertRaisesRegex(RuntimeError,'traversal'):
                list_recordings(self.root)
        self.assertTrue(recording.exists())

if __name__ == '__main__': unittest.main()
