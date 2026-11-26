import os,subprocess,tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.signing_setup import generate
from sunhill_nvr.build_release import build
from sunhill_nvr.release import stage_verified

class SigningSetupTests(unittest.TestCase):
 def test_encrypted_key_can_sign_a_release_and_existing_keys_are_preserved(self):
    with tempfile.TemporaryDirectory() as name:
        root=Path(name);keys=root/'keys';keys.mkdir(mode=0o700);private=keys/'private.pem';public=root/'public.pem'
        generate(private,public,'test-passphrase-123')
        self.assertEqual(private.stat().st_mode&0o777,0o600)
        self.assertIn('ENCRYPTED PRIVATE KEY',private.read_text())
        original=private.read_bytes()
        with self.assertRaises(FileExistsError):generate(private,public,'test-passphrase-123')
        self.assertEqual(private.read_bytes(),original)
        package=root/'src'/'sunhill_nvr';package.mkdir(parents=True);(package/'__init__.py').write_text('version=1\n')
        build(root/'src','1.2.3',private,root/'release.zip',root/'release.sig',passphrase='test-passphrase-123')
        versions=root/'versions';versions.mkdir()
        self.assertTrue((stage_verified(root/'release.zip',root/'release.sig',public,versions)/'app/sunhill_nvr/__init__.py').is_file())
 def test_insecure_key_directory_is_rejected(self):
    with tempfile.TemporaryDirectory() as name:
        root=Path(name);keys=root/'keys';keys.mkdir(mode=0o755);os.chmod(keys,0o755)
        with self.assertRaises(ValueError):generate(keys/'private.pem',root/'public.pem','test-passphrase-123')
        self.assertFalse((keys/'private.pem').exists())

if __name__=='__main__':unittest.main()
