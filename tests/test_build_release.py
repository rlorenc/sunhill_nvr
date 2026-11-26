import subprocess
import tempfile
import unittest
from pathlib import Path
from sys import path

path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sunhill_nvr.build_release import build
from sunhill_nvr.release import ReleaseError, stage_verified


class BuildReleaseTests(unittest.TestCase):
    def test_signed_release_round_trip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'src'; package = source / 'sunhill_nvr'; package.mkdir(parents=True)
            (package / '__init__.py').write_text('VERSION = 1\n')
            (package / 'static').mkdir(); (package / 'static' / 'index.html').write_text('test')
            key = root / 'key'; public = root / 'pub'; archive = root / 'release.zip'; signature = root / 'release.sig'
            subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(key)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(public)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            build(source,'1.2.3',key,archive,signature)
            versions = root / 'versions';versions.mkdir()
            staged = stage_verified(archive,signature,public,versions)
            self.assertEqual((staged / 'app/sunhill_nvr/__init__.py').read_text(),'VERSION = 1\n')
            original = archive.read_bytes()
            with self.assertRaises(FileExistsError):build(source,'1.2.3',key,archive,signature)
            self.assertEqual(archive.read_bytes(),original)
            archive.write_bytes(original + b'altered')
            with self.assertRaises(ReleaseError):stage_verified(archive,signature,public,versions)

    def test_source_symlink_is_rejected_before_signing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);package=root/'src/sunhill_nvr';package.mkdir(parents=True)
            (package/'__init__.py').write_text('');(package/'outside.py').symlink_to('/etc/passwd')
            key=root/'key';key.write_text('unused')
            with self.assertRaises(ValueError):build(root/'src','1.0.0',key,root/'out.zip',root/'out.sig')
            self.assertFalse((root/'out.zip').exists())

    def test_encrypted_key_signs_and_wrong_password_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'src';package=source/'sunhill_nvr';package.mkdir(parents=True)
            (package/'__init__.py').write_text('VERSION = 1\n')
            key=root/'private.pem';public=root/'public.pem';archive=root/'release.zip';signature=root/'release.sig'
            subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-aes-256-cbc','-pass','stdin','-out',str(key)],input=b'test-passphrase\n',check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['openssl','pkey','-in',str(key),'-passin','stdin','-pubout','-out',str(public)],input=b'test-passphrase\n',check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            with self.assertRaises(ReleaseError):build(source,'1.2.3',key,archive,signature,passphrase='incorrect')
            self.assertFalse(archive.exists());self.assertFalse(signature.exists())
            build(source,'1.2.3',key,archive,signature,passphrase='test-passphrase')
            versions=root/'versions';versions.mkdir()
            self.assertTrue((stage_verified(archive,signature,public,versions)/'app/sunhill_nvr/__init__.py').is_file())
            with self.assertRaises(ValueError):build(source,'1.2.4',key,root/'bad.zip',root/'bad.sig',passphrase='bad\npassword')


if __name__ == '__main__':
    unittest.main()
