import subprocess,unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.linux_activity import file_in_use
class ActivityTests(unittest.TestCase):
 def test_unknown_is_never_treated_as_safe(self):
    with patch('sunhill_nvr.linux_activity.sys.platform','darwin'):
        with self.assertRaises(RuntimeError):file_in_use(Path('/tmp/file'))
 def test_linux_fuser_result_and_error(self):
    with patch('sunhill_nvr.linux_activity.sys.platform','linux'), patch('sunhill_nvr.linux_activity.os.geteuid',return_value=0), patch.object(Path,'is_file',return_value=True), patch('sunhill_nvr.linux_activity.subprocess.run') as run:
        run.return_value=subprocess.CompletedProcess([],0,b'',b'');self.assertTrue(file_in_use(Path('/tmp/file')))
        run.return_value=subprocess.CompletedProcess([],1,b'',b'');self.assertFalse(file_in_use(Path('/tmp/file')))
        run.return_value=subprocess.CompletedProcess([],1,b'',b'permission denied')
        with self.assertRaises(RuntimeError):file_in_use(Path('/tmp/file'))
if __name__=='__main__':unittest.main()
