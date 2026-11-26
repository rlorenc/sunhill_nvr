import os
import tempfile
import unittest
from pathlib import Path
from sys import path
from unittest.mock import patch

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import fd_mount
from sunhill_nvr.files import open_confined
from sunhill_nvr.rotation import list_recordings,unlink_confined


class MountGuardTests(unittest.TestCase):
    def test_linux_mount_id_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);file=root/'file';file.write_bytes(b'x');fd=os.open(file,os.O_RDONLY)
            try:
                (root/str(fd)).write_text('pos:\t0\nmnt_id:\t42\n')
                self.assertEqual(fd_mount.identity(fd,platform='linux',fdinfo=root),(os.fstat(fd).st_dev,42))
                (root/str(fd)).write_text('pos:\t0\n')
                with self.assertRaises(fd_mount.MountUnavailable):fd_mount.identity(fd,platform='linux',fdinfo=root)
            finally:os.close(fd)

    def test_same_device_bind_mount_cannot_be_read_or_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'archive';file=root/'cam/2026/11/21/190900.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'video');os.utime(file,(1,1))
            camera_inode=(root/'cam').stat().st_ino;info=file.stat()
            def identity(fd):
                stat=os.fstat(fd)
                return (stat.st_dev,2 if stat.st_ino==camera_inode else 1)
            with patch('sunhill_nvr.fd_mount.identity',side_effect=identity):
                self.assertEqual(list_recordings(root,now=1000,min_age_seconds=0),[])
                with self.assertRaises(fd_mount.MountBoundary):open_confined(root,'cam/2026/11/21/190900.mp4')
                with self.assertRaises(fd_mount.MountBoundary):unlink_confined(root,file,expected_device=info.st_dev,expected_inode=info.st_ino)
            self.assertTrue(file.exists())

    def test_file_level_mount_boundary_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'archive';file=root/'cam/2026/11/21/190900.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'keep');info=file.stat()
            def identity(fd):
                value=os.fstat(fd)
                return (value.st_dev,2 if value.st_ino==info.st_ino else 1)
            with patch('sunhill_nvr.fd_mount.identity',side_effect=identity):
                with self.assertRaises(fd_mount.MountBoundary):unlink_confined(root,file,expected_device=info.st_dev,expected_inode=info.st_ino)
            self.assertEqual(file.read_bytes(),b'keep')


if __name__=='__main__':unittest.main()
