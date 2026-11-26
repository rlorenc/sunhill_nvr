import hashlib
import io
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from sys import path

path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sunhill_nvr.install_download import HttpsRedirects, copy_verified, download


class Response(io.BytesIO):
    status = 200
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {} if length is None else {'Content-Length': str(length)}
    def geturl(self):
        return 'https://release.example/binary'


class Opener:
    def __init__(self, data, length=None):
        self.data, self.length = data, length
    def open(self, url, timeout):
        return Response(self.data, self.length)


class InstallDownloadTests(unittest.TestCase):
    def test_verified_download_and_existing_output_are_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / 'binary'
            content = b'gateway binary'
            digest = hashlib.sha256(content).hexdigest()
            download('https://example.test/file', digest, target, opener=Opener(content, len(content)))
            self.assertEqual(target.read_bytes(), content)
            with self.assertRaises(FileExistsError):
                download('https://example.test/file', digest, target, opener=Opener(content))
            self.assertEqual(target.read_bytes(), content)

    def test_wrong_hash_truncation_and_large_download_are_removed(self):
        content = b'gateway binary'
        digest = hashlib.sha256(content).hexdigest()
        for source, length, limit in ((b'wrong', None, 100), (content, 50, 100), (content, None, 3), (content, 500, 100)):
            with self.subTest(source=source, length=length, limit=limit), tempfile.TemporaryDirectory() as name:
                target = Path(name) / 'binary'
                with self.assertRaises(ValueError):
                    download('https://example.test/file', digest, target, opener=Opener(source, length), limit=limit)
                self.assertFalse(target.exists())

    def test_offline_gateway_uses_same_digest_and_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / 'source'; target = Path(name) / 'target'
            content = b'pinned gateway'; source.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            copy_verified(source, digest, target)
            self.assertEqual(target.read_bytes(), content)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):copy_verified(source, digest, target)
            self.assertEqual(target.read_bytes(), content)

    def test_offline_gateway_rejects_bad_hash_symlink_fifo_and_size(self):
        import os
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / 'source'; target = Path(name) / 'target'
            source.write_bytes(b'pinned gateway')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):copy_verified(source, '0'*64, target)
            with self.assertRaises(ValueError):copy_verified(source, digest, target, limit=1)
            link = Path(name) / 'link'; link.symlink_to(source)
            with self.assertRaises(OSError):copy_verified(link, digest, target)
            fifo = Path(name) / 'fifo'; os.mkfifo(fifo)
            with self.assertRaises(ValueError):copy_verified(fifo, digest, target)
            self.assertFalse(target.exists())

    def test_non_https_and_downgrade_redirect_are_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(ValueError):
                download('http://example.test/file', '0' * 64, Path(name) / 'binary', opener=Opener(b''))
        request = urllib.request.Request('https://example.test/file')
        with self.assertRaises(urllib.error.URLError):
            HttpsRedirects().redirect_request(request, None, 302, '', {}, 'http://example.test/file')


if __name__ == '__main__':
    unittest.main()
