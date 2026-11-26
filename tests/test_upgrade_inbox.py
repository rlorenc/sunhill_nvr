import io
import os
import tempfile
import unittest
from pathlib import Path
from sys import path

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import upgrade_inbox


class UpgradeInboxTests(unittest.TestCase):
    def test_upload_and_atomic_single_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=upgrade_inbox.store(root,'archive',io.BytesIO(b'archive'),7)
            signature=upgrade_inbox.store(root,'signature',io.BytesIO(b'signature'),9)
            upgrade_inbox.request(root,archive,signature,2)
            self.assertEqual(upgrade_inbox.read_request(root),{'archive':archive,'signature':signature,'user_id':2})
            with self.assertRaises(FileExistsError):upgrade_inbox.request(root,archive,signature,2)
            self.assertEqual(upgrade_inbox.read_request(root)['archive'],archive)

    def test_request_cannot_race_upload_removal_or_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=upgrade_inbox.store(root,'archive',io.BytesIO(b'archive'),7)
            signature=upgrade_inbox.store(root,'signature',io.BytesIO(b'signature'),9)
            with upgrade_inbox._upload_lock(root):
                with self.assertRaises(BlockingIOError):upgrade_inbox.request(root,archive,signature,2)
                self.assertIsNone(upgrade_inbox.read_request(root))
            upgrade_inbox.request(root,archive,signature,2)
            with self.assertRaises(ValueError):upgrade_inbox.remove(root,'archive',archive)
            self.assertEqual(upgrade_inbox.read_request(root)['signature'],signature)

    def test_truncated_upload_and_symlink_request_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaises(ValueError):upgrade_inbox.store(root,'archive',io.BytesIO(b'part'),10)
            self.assertEqual(list(root.glob('upload-*')),[])
            (root/'request.json').symlink_to('/etc/passwd')
            with self.assertRaises(OSError):upgrade_inbox.read_request(root)
            with self.assertRaises(ValueError):upgrade_inbox.filename('archive','../../etc/passwd')

    def test_inbox_permissions_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);root.chmod(0o755)
            with self.assertRaises(ValueError):upgrade_inbox.store(root,'archive',io.BytesIO(b'x'),1)


if __name__=='__main__':unittest.main()
