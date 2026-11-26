import tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.storage import *
class StorageTests(unittest.TestCase):
 def test_marker_identity_replacement_disables_rotation(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);marker=root/'.sunhill-volume-id';marker.write_text('test')
        identity=identify(root);self.assertTrue(matches(root,identity))
        # Replace while keeping original alive, preventing inode reuse.
        marker.rename(root/'old');marker.write_text('new')
        self.assertFalse(matches(root,identity))
 def test_symlink_marker_rejected(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);(root/'file').write_text('x');(root/'.sunhill-volume-id').symlink_to(root/'file')
        with self.assertRaises(RuntimeError):identify(root)
 def test_actual_disk_space_sane(self):
    with tempfile.TemporaryDirectory() as tmp:
        info=space_for(Path(tmp));self.assertGreater(info.total,0)
        self.assertLessEqual(info.free_for_service,info.free_including_reserved)
if __name__=='__main__':unittest.main()
