import os
import tempfile
import unittest
from pathlib import Path
from sys import path

path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from preflight_archive import PreflightError,inspect_archive,valid_recording
from sunhill_nvr.rotation import recording_info

class ArchivePreflightTests(unittest.TestCase):
 def test_existing_recordings_are_checked_without_changes(self):
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary)/'archive';day=root/'Brama/2026/11/21';day.mkdir(parents=True)
        names=['190900.mp4','Brama_00_20261121191000.mp4','20261121T191100+0100.mp4']
        for name in names:(day/name).write_bytes(name.encode())
        (day/'unrelated.txt').write_bytes(b'keep');outside=Path(temporary)/'outside';outside.mkdir();(outside/'file').write_bytes(b'keep')
        (root/'link').symlink_to(outside,target_is_directory=True)
        before={name:(day/name).read_bytes() for name in names}
        report=inspect_archive(root)
        self.assertEqual(report.directories,5);self.assertEqual(report.recordings,3);self.assertEqual(report.skipped_links,1)
        self.assertEqual({name:(day/name).read_bytes() for name in names},before);self.assertEqual((outside/'file').read_bytes(),b'keep')
 def test_recording_names_follow_the_archive_scanner(self):
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);day=root/'cam/2026/11/21';day.mkdir(parents=True)
        for name in ('190900.mp4','Cam_00_20261121190900.mp4','20261121T190900+0100-001.mp4',
                     '20261122T190900+0100.mp4','Cam_00_20261122190900.mp4','246000.mp4','unrelated.mp4'):
            file=day/name;file.write_bytes(b'x')
            self.assertEqual(valid_recording(name,'2026','11','21'),recording_info(root,file) is not None,name)
 def test_read_failure_aborts_and_closes_every_open_descriptor(self):
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);day=root/'cam/2026/11/21';day.mkdir(parents=True);recording=day/'190900.mp4';recording.write_bytes(b'keep');opened=[]
        def opener(name,flags,**kwargs):
            if name=='190900.mp4':raise PermissionError('denied')
            fd=os.open(name,flags,**kwargs);opened.append(fd);return fd
        with self.assertRaises(PreflightError):inspect_archive(root,open_fn=opener)
        for fd in opened:
            with self.assertRaises(OSError):os.fstat(fd)
        self.assertEqual(recording.read_bytes(),b'keep')
 def test_unwritable_day_is_reported_and_another_mount_is_skipped(self):
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);day=root/'cam/2026/11/21';day.mkdir(parents=True);(day/'190900.mp4').write_bytes(b'keep');inode=day.stat().st_ino
        def access(name,mode,*,dir_fd,effective_ids):
            return os.fstat(dir_fd).st_ino!=inode
        with self.assertRaises(PreflightError):inspect_archive(root,access_fn=access)
        def identity(fd):
            info=os.fstat(fd);return info.st_dev,2 if info.st_ino==inode else 1
        report=inspect_archive(root,identity=identity)
        self.assertEqual(report.skipped_mounts,1);self.assertEqual(report.recordings,0);self.assertTrue((day/'190900.mp4').exists())
 def test_stdin_invocation_supports_paths_with_spaces(self):
    import subprocess,sys
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary)/'archive with spaces';day=root/'cam/2026/11/21';day.mkdir(parents=True);(day/'190900.mp4').write_bytes(b'keep')
        script=Path(__file__).resolve().parents[1]/'scripts/preflight_archive.py'
        result=subprocess.run([sys.executable,'-',str(root)],input=script.read_bytes(),capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn(b'nagrania: 1',result.stdout)
 def test_sticky_directory_rejects_files_owned_by_another_user(self):
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);day=root/'cam/2026/11/21';day.mkdir(parents=True)
        recording=day/'190900.mp4';recording.write_bytes(b'keep');day.chmod(0o1777)
        with patch('preflight_archive.os.geteuid',return_value=os.geteuid()+10000):
            with self.assertRaisesRegex(PreflightError,'sticky'):inspect_archive(root)
        self.assertEqual(recording.read_bytes(),b'keep')
 def test_unreadable_file_error_names_the_file(self):
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);day=root/'cam/2026/11/21';day.mkdir(parents=True)
        (day/'190900.mp4').write_bytes(b'keep')
        def opener(name,flags,**kwargs):
            if name=='190900.mp4':raise PermissionError('denied')
            return os.open(name,flags,**kwargs)
        with self.assertRaisesRegex(PreflightError,'190900.mp4'):inspect_archive(root,open_fn=opener)

if __name__=='__main__':unittest.main()
