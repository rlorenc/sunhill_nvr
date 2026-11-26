import json,subprocess,tempfile,unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.mount import *
class MountTests(unittest.TestCase):
 def test_inspection_and_identity(self):
    data={'filesystems':[{'target':'/home/store','source':'/dev/vdb1','fstype':'ext4','uuid':'abcd-1234'}]}
    with tempfile.TemporaryDirectory() as t,patch('sunhill_nvr.mount.subprocess.run') as run:
        run.return_value=subprocess.CompletedProcess([],0,json.dumps(data).encode(),b'')
        m=inspect(Path(t));self.assertTrue(same(m,Mount('/home/store','/dev/new','ext4','abcd-1234')))
        self.assertFalse(same(m,Mount('/','/dev/vda1','ext4','ffff')))
 def test_unknown_uuid_uses_source(self):
    self.assertTrue(same(Mount('/m','server:/share','nfs',None),Mount('/m','server:/share','nfs',None)))
    self.assertFalse(same(Mount('/m','server:/share','nfs',None),Mount('/m','other:/share','nfs',None)))
 def test_systemd_private_bind_requires_exact_underlying_subtree(self):
    original=Mount('/home/store','/dev/vdb1','ext4','abcd')
    self.assertTrue(same(original,Mount('/home/store/kamery','/dev/vdb1[/kamery]','ext4','abcd')))
    self.assertFalse(same(original,Mount('/home/store/kamery','/dev/vdb1[/other]','ext4','abcd')))
    self.assertFalse(same(original,Mount('/home/store/kamery','/dev/other[/kamery]','ext4','abcd')))
    self.assertFalse(same(original,Mount('/home/store/kamery','/dev/vdb1[/kamery]','ext4','different')))
    subvolume=Mount('/data','/dev/vdb1[/subvol]','btrfs','abcd')
    self.assertTrue(same(subvolume,Mount('/data/kamery','/dev/vdb1[/subvol/kamery]','btrfs','abcd')))
 def test_same_uuid_and_target_do_not_authorize_another_subtree(self):
    original=Mount('/data','/dev/vdb1[/archive]','btrfs','abcd')
    self.assertFalse(same(original,Mount('/data','/dev/vdb1[/other]','btrfs','abcd')))
    self.assertFalse(same(original,Mount('/data','/dev/vdb1','btrfs','abcd')))
    self.assertTrue(same(original,Mount('/data','/dev/vdc1[/archive]','btrfs','abcd')))
 def test_bad_or_failed_findmnt_fails_closed(self):
    with tempfile.TemporaryDirectory() as t,patch('sunhill_nvr.mount.subprocess.run',side_effect=subprocess.TimeoutExpired('findmnt',1)):
        with self.assertRaises(RuntimeError):inspect(Path(t))
 def test_rotation_requires_supported_filesystem_type(self):
    for kind in ('ext4','xfs','btrfs','zfs','f2fs'):
        require_local(Mount('/data','device',kind,None))
    for kind in ('nfs','nfs4','cifs','9p','virtiofs','fuse.sshfs','unknown'):
        with self.subTest(kind=kind),self.assertRaises(ValueError):
            require_local(Mount('/data','device',kind,None))
if __name__=='__main__':unittest.main()
