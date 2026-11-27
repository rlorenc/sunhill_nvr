"""Configuration parser checks, enabled only in a prepared Ubuntu test VM."""
import os,subprocess,tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import nginx_config,fd_mount

@unittest.skipUnless(os.environ.get('SUNHILL_UBUNTU_INTEGRATION')=='1','requires prepared Ubuntu 26.04')
class UbuntuConfigurationTests(unittest.TestCase):
 def test_linux_proc_finds_a_real_open_recording_and_detects_close(self):
    with tempfile.TemporaryDirectory() as name:
        file=Path(name)/'recording.mp4';file.write_bytes(b'test')
        script=('import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
                'from sunhill_nvr import proc_scan; p=Path(sys.argv[2]); '
                'print(int(proc_scan.in_snapshot(p,proc_scan.snapshot())))')
        def check():
            result=subprocess.run(['sudo','-n','/usr/bin/python3','-c',script,str(Path(__file__).resolve().parents[1]/'src'),str(file)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr);return result.stdout.strip()
        with file.open('rb'):
            self.assertEqual(check(),'1')
        self.assertEqual(check(),'0')
 def test_linux_fd_mount_identity_is_stable_for_a_child_directory(self):
    with tempfile.TemporaryDirectory() as name:
        root=Path(name);child=root/'child';child.mkdir()
        parent_fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        child_fd=os.open(child,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            identity=fd_mount.identity(parent_fd)
            self.assertIsNotNone(identity[1]);fd_mount.require_same(identity,child_fd)
        finally:os.close(child_fd);os.close(parent_fd)
 def test_nginx_accepts_generated_http_configuration(self):
    with tempfile.TemporaryDirectory() as name:
        root=Path(name);server=root/'sunhill.conf';server.write_text(nginx_config.render('nvr.example.com'))
        config=root/'nginx.conf';config.write_text(f'pid {root}/nginx.pid;\nerror_log stderr;\nevents {{}}\nhttp {{ include {server}; }}\n')
        result=subprocess.run(['/usr/sbin/nginx','-t','-c',str(config)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
if __name__=='__main__':unittest.main()
