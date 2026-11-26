import os,tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.files import *
class FileTests(unittest.TestCase):
 def test_valid_ranges(self):
    self.assertEqual(parse_range('bytes=3-5',10),ByteRange(3,5,10))
    self.assertEqual(parse_range('bytes=7-',10),ByteRange(7,9,10))
    self.assertEqual(parse_range('bytes=-3',10),ByteRange(7,9,10))
    for bad in ('bytes=20-30','bytes=2-1','bytes=0-1,3-4','bytes=-0'):
        with self.assertRaises(InvalidRange):parse_range(bad,10)
 def test_long_response_stops_and_closes_if_authorization_fails(self):
    with tempfile.TemporaryDirectory() as t:
        file=Path(t)/'data';file.write_bytes(b'x'*(600*1024));fd=os.open(file,os.O_RDONLY);clock=[0.0];checks=[]
        body=FileBody(fd,parse_range(None,file.stat().st_size),authorize=lambda:checks.append(True) or len(checks)==1,monotonic=lambda:clock[0])
        chunks=iter(body);self.assertEqual(len(next(chunks)),256*1024);clock[0]=6
        with self.assertRaises(StopIteration):next(chunks)
        self.assertEqual(checks,[True,True]);self.assertIsNone(body.fd)
 def test_revoked_before_first_chunk_sends_no_bytes(self):
    with tempfile.TemporaryDirectory() as t:
        file=Path(t)/'data';file.write_bytes(b'secret');fd=os.open(file,os.O_RDONLY)
        body=FileBody(fd,parse_range(None,file.stat().st_size),authorize=lambda:False)
        self.assertEqual(b''.join(body),b'');self.assertIsNone(body.fd)
 def test_head_closes_file_without_sending_recording_bytes(self):
    with tempfile.TemporaryDirectory() as t:
        file=Path(t)/'data';file.write_bytes(b'secret video');fd=os.open(file,os.O_RDONLY)
        body=FileBody(fd,parse_range(None,file.stat().st_size),head=True)
        self.assertEqual(b''.join(body),b'');self.assertIsNone(body.fd)
        with self.assertRaises(OSError):os.fstat(fd)
 def test_confined_reads_and_symlinks(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);file=root/'cam/2026/11/21/190000.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'0123456789')
        fd,info=open_confined(root,'cam/2026/11/21/190000.mp4');self.assertEqual(b''.join(FileBody(fd,parse_range('bytes=3-5',10))),b'345')
        file.unlink();file.symlink_to('/etc/passwd')
        with self.assertRaises(OSError):open_confined(root,'cam/2026/11/21/190000.mp4')
 def test_index_identity_must_match_and_partial_records_are_rejected(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);file=root/'cam/2026/11/21/190000.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'keep')
        info=file.stat();identity=recorded_identity(str(info.st_dev),str(info.st_ino),str(info.st_ctime_ns))
        fd,_=open_confined(root,'cam/2026/11/21/190000.mp4',expected_identity=identity);os.close(fd)
        with self.assertRaises(ValueError):open_confined(root,'cam/2026/11/21/190000.mp4',expected_identity=(identity[0],'0',identity[2]))
        with self.assertRaises(ValueError):recorded_identity(identity[0],None,identity[2])
        self.assertIsNone(recorded_identity(None,None,None))
 def test_export_shared_lock_blocks_retention_lock(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);f=root/'cam/2026/11/21/190000.mp4';f.parent.mkdir(parents=True);f.write_bytes(b'123')
        first,_=open_confined(root,'cam/2026/11/21/190000.mp4');second,_=open_confined(root,'cam/2026/11/21/190000.mp4')
        try:
            acquire_shared(first);self.assertFalse(try_exclusive(second));os.close(first);first=None
            self.assertTrue(try_exclusive(second))
        finally:
            if first is not None:os.close(first)
            os.close(second)
 def test_export_deletion_rejects_other_jobs_links_and_mounts(self):
    from unittest.mock import patch
    from sunhill_nvr import fd_mount
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);name='export-2-0123456789abcdef.mp4';file=root/name;file.write_bytes(b'keep')
        with self.assertRaises(ValueError):delete_export(root,name,1)
        with patch('sunhill_nvr.files.fd_mount.require_same',side_effect=fd_mount.MountBoundary()):
            with self.assertRaises(fd_mount.MountBoundary):delete_export(root,name,2)
            with self.assertRaises(fd_mount.MountBoundary):open_export(root,name)
        self.assertEqual(file.read_bytes(),b'keep')
        outside=root/'other';file.rename(outside);file.symlink_to(outside)
        with self.assertRaises(OSError):delete_export(root,name,2)
        self.assertEqual(outside.read_bytes(),b'keep')
if __name__=='__main__':unittest.main()
