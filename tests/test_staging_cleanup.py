import os
import tempfile
import unittest
from pathlib import Path
from sys import path
from unittest.mock import patch

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import fd_mount,staging_cleanup


class StagingCleanupTests(unittest.TestCase):
    def test_prunes_only_old_empty_inactive_session_trees(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);camera=root/'cam';camera.mkdir()
            sessions=[c*32 for c in 'abcd']
            for session in sessions:
                (camera/session/'2026/11/21').mkdir(parents=True)
            keep=camera/sessions[1]/'2026/11/21'/'unfinished.mp4';keep.write_bytes(b'keep')
            for session in sessions:
                tree=camera/session
                for item in sorted(tree.rglob('*'),key=lambda p:len(p.parts),reverse=True):os.utime(item,(1,1))
                os.utime(tree,(1,1))
            recent=camera/sessions[3]/'2026/11/21';os.utime(recent,(199999,199999))
            removed=staging_cleanup.prune(root,{('cam',sessions[2])},now=200000)
            self.assertEqual(removed,4)
            self.assertFalse((camera/sessions[0]).exists());self.assertEqual(keep.read_bytes(),b'keep')
            self.assertTrue((camera/sessions[2]).exists());self.assertTrue(recent.exists())

    def test_unknown_layout_links_and_other_mounts_are_not_touched(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);camera=root/'cam';camera.mkdir();outside=root/'outside';outside.mkdir()
            old=camera/('a'*32);old.mkdir();(old/'private').write_bytes(b'keep')
            link=camera/('b'*32);link.symlink_to(outside,target_is_directory=True)
            mounted=camera/('c'*32);mounted.mkdir()
            for item in (old/'private',old,mounted):os.utime(item,(1,1))
            original=fd_mount.require_same
            def boundary(expected,fd):
                if os.fstat(fd).st_ino==mounted.stat().st_ino:raise fd_mount.MountBoundary()
                return original(expected,fd)
            with patch('sunhill_nvr.staging_cleanup.fd_mount.require_same',side_effect=boundary):
                self.assertEqual(staging_cleanup.prune(root,set(),now=200000),0)
            self.assertEqual((old/'private').read_bytes(),b'keep');self.assertTrue(link.is_symlink())
            self.assertTrue(mounted.is_dir());self.assertEqual(list(outside.iterdir()),[])

    def test_refuses_insecure_cleanup_options(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            with self.assertRaises(ValueError):staging_cleanup.prune(root,set(),minimum_age=10)
            with self.assertRaises(ValueError):staging_cleanup.prune(root,{('../bad','a'*32)})


if __name__=='__main__':unittest.main()
