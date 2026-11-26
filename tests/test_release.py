import hashlib,json,os,subprocess,tempfile,unittest,zipfile
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.release import *

class ReleaseTests(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.d=Path(self.tmp.name);self.versions=self.d/'versions';self.versions.mkdir()
    subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(self.d/'key')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    # dgst -sha256 cannot sign Ed25519. Use RSA for digest-based package signing.
    subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(self.d/'key')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['openssl','pkey','-in',str(self.d/'key'),'-pubout','-out',str(self.d/'pub')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 def tearDown(self):self.tmp.cleanup()
 def build(self,extra=None):
    archive=self.d/'release.zip';data=b'print(1)\n';manifest={'version':'1.2.3','files':{'app/main.py':hashlib.sha256(data).hexdigest()}}
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('manifest.json',json.dumps(manifest));z.writestr('app/main.py',data)
        if extra:z.writestr(extra,b'evil')
    subprocess.run(['openssl','dgst','-sha256','-sign',str(self.d/'key'),'-out',str(self.d/'sig'),str(archive)],check=True)
    return archive
 def test_valid_signed_release(self):
    out=stage_verified(self.build(),self.d/'sig',self.d/'pub',self.versions)
    self.assertEqual((out/'app/main.py').read_bytes(),b'print(1)\n')
    self.assertEqual(out.stat().st_mode&0o777,0o755);self.assertEqual((out/'app').stat().st_mode&0o777,0o755)
    self.assertEqual((out/'app/main.py').stat().st_mode&0o777,0o644)
    self.assertEqual(stage_verified(self.d/'release.zip',self.d/'sig',self.d/'pub',self.versions,reuse_existing=True),out)
    (out/'app/main.py').write_text('changed')
    with self.assertRaises(ReleaseError):stage_verified(self.d/'release.zip',self.d/'sig',self.d/'pub',self.versions,reuse_existing=True)
 def test_signed_archive_path_traversal_rejected(self):
    with self.assertRaises(ReleaseError):stage_verified(self.build('app/../../escape'),self.d/'sig',self.d/'pub',self.versions)
    self.assertFalse((self.d/'escape').exists());self.assertEqual(list(self.versions.iterdir()),[])
 def test_tampering_rejected(self):
    archive=self.build();archive.write_bytes(archive.read_bytes()+b'tampered')
    with self.assertRaises(ReleaseError):stage_verified(archive,self.d/'sig',self.d/'pub',self.versions)
    self.assertEqual(list(self.versions.iterdir()),[])
 def test_untrusted_fifo_and_symbolic_link_are_rejected(self):
    archive=self.build();fifo=self.d/'fifo';os.mkfifo(fifo)
    with self.assertRaises(ReleaseError):stage_verified(fifo,self.d/'sig',self.d/'pub',self.versions)
    link=self.d/'symlink';link.symlink_to(archive)
    with self.assertRaises(ReleaseError):stage_verified(link,self.d/'sig',self.d/'pub',self.versions)
    self.assertEqual(list(self.versions.iterdir()),[])
 def test_oversized_manifest_is_rejected_even_if_signed(self):
    archive=self.d/'large.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('manifest.json',b' '* (MAX_MANIFEST_BYTES+1))
    subprocess.run(['openssl','dgst','-sha256','-sign',str(self.d/'key'),'-out',str(self.d/'sig'),str(archive)],check=True)
    with self.assertRaises(ReleaseError):stage_verified(archive,self.d/'sig',self.d/'pub',self.versions)
 def test_activation_rolls_back_on_failed_health_check(self):
    old=self.versions/'1.0.0';old.mkdir();(old/'manifest.json').write_text('{}')
    new=stage_verified(self.build(),self.d/'sig',self.d/'pub',self.versions)
    link=self.d/'current';link.symlink_to(old)
    checks=iter([False,True])
    with self.assertRaises(ReleaseError):activate(self.versions,link,'1.2.3',restart_and_check=lambda:next(checks))
    self.assertEqual(link.resolve(),old.resolve())
 def test_activation_accepts_healthy_release(self):
    out=stage_verified(self.build(),self.d/'sig',self.d/'pub',self.versions)
    link=self.d/'current';activate(self.versions,link,'1.2.3',restart_and_check=lambda:True)
    self.assertEqual(link.resolve(),out.resolve())
if __name__=='__main__':unittest.main()
