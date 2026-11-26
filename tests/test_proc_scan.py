import errno,tempfile,unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.proc_scan import *
class ProcScanTests(unittest.TestCase):
 def test_open_inode_is_detected(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);file=root/'data';file.write_bytes(b'x');fd=root/'123456'/'task'/'123456'/'fd';fd.mkdir(parents=True);(fd/'4').symlink_to(file)
        opened=snapshot(root,ignore=set());self.assertTrue(in_snapshot(file,opened))
 def test_open_inode_in_nonleader_task_is_detected(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);file=root/'recording';file.write_bytes(b'x')
        leader=root/'123456/task/123456/fd';leader.mkdir(parents=True)
        thread=root/'123456/task/123457/fd';thread.mkdir(parents=True);(thread/'9').symlink_to(file)
        self.assertTrue(in_snapshot(file,snapshot(root)))
 def test_missing_task_directory_of_surviving_process_fails_closed(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);(root/'123456').mkdir()
        with self.assertRaises(IncompleteScan):snapshot(root)
 def test_real_child_open_file_is_detected_with_scoped_proc_fixture(self):
    import subprocess,sys,os
    if not sys.platform.startswith('linux'):self.skipTest('Linux procfs required')
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);file=root/'recording';file.write_bytes(b'x');fixture=root/'proc';fixture.mkdir()
        proc=subprocess.Popen([sys.executable,'-c','import sys; f=open(sys.argv[1],"rb"); print("ready",flush=True); sys.stdin.read()',str(file)],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
        try:
            self.assertEqual(proc.stdout.readline(),b'ready\n')
            (fixture/str(proc.pid)).symlink_to(Path('/proc')/str(proc.pid),target_is_directory=True)
            self.assertTrue(in_snapshot(file,snapshot(fixture)))
            proc.stdin.close();proc.wait(timeout=5)
            self.assertFalse(in_snapshot(file,snapshot(fixture)))
        finally:
            if proc.poll() is None:proc.kill();proc.wait(timeout=5)
            proc.stdin.close();proc.stdout.close()
 def test_permission_failure_is_not_treated_as_closed(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);(root/'123456'/'task'/'123456'/'fd').mkdir(parents=True)
        original=Path.iterdir
        def guarded(p):
            if p.name=='fd':raise PermissionError(errno.EACCES,'denied')
            return original(p)
        with patch.object(Path,'iterdir',guarded):
            with self.assertRaises(IncompleteScan):snapshot(root)
 def test_restricted_or_unverifiable_proc_mount_is_rejected(self):
    prefix='34 1 0:5 / /proc rw,nosuid,nodev,noexec,relatime - proc proc '
    for options in ('rw','rw,hidepid=0','rw,hidepid=off'):
        validate_proc_mountinfo(prefix+options,34)
    for options in ('rw,hidepid=1','rw,hidepid=2','rw,hidepid=invisible','rw,hidepid=ptraceable','rw,hidepid'):
        with self.subTest(options=options),self.assertRaises(IncompleteScan):
            validate_proc_mountinfo(prefix+options,34)
    for data,ident in [(prefix+'rw',35),(prefix+'rw\n'+prefix+'rw',34),
                       ('34 1 0:5 / /proc rw - tmpfs tmpfs rw',34),('34 invalid',34)]:
        with self.subTest(data=data,ident=ident),self.assertRaises(IncompleteScan):validate_proc_mountinfo(data,ident)
 def test_real_proc_scan_checks_visibility_before_enumeration(self):
    with patch('sunhill_nvr.proc_scan.require_proc_visibility',side_effect=IncompleteScan('restricted')):
        with self.assertRaises(IncompleteScan):snapshot()
 def test_proc_mount_descriptor_is_closed_if_mountinfo_read_fails(self):
    with patch('sunhill_nvr.proc_scan.sys.platform','linux'),patch('sunhill_nvr.proc_scan.os.open',return_value=91), \
         patch('sunhill_nvr.proc_scan.os.close') as close,patch('sunhill_nvr.proc_scan.fd_mount.identity',return_value=(1,34)), \
         patch.object(Path,'read_text',side_effect=PermissionError('denied')):
        with self.assertRaises(IncompleteScan):require_proc_visibility()
        close.assert_called_once_with(91)
if __name__=='__main__':unittest.main()
