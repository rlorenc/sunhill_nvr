import tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.activity_helper import handle
class ActivityHelperTests(unittest.TestCase):
 def test_only_authorized_uid_and_valid_archive_file(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);f=root/'cam/2026/11/21/190900.mp4';f.parent.mkdir(parents=True);f.write_bytes(b'video');st=f.stat()
        req={'scope':'archive','paths':['cam/2026/11/21/190900.mp4']}
        self.assertEqual(handle(req,peer_uid=1000,allowed_uid=1000,archive=root,staging=root,scan=lambda:{(st.st_dev,st.st_ino)}),{'active':[True]})
        with self.assertRaises(PermissionError):handle(req,peer_uid=1001,allowed_uid=1000,archive=root,staging=root,scan=lambda:set())
 def test_symlink_escape_and_parent_traversal_rejected(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);f=root/'cam/2026/11/21/190900.mp4';f.parent.mkdir(parents=True);f.symlink_to('/etc/passwd')
        with self.assertRaises(OSError):handle({'scope':'archive','paths':['cam/2026/11/21/190900.mp4']},peer_uid=1000,allowed_uid=1000,archive=root,staging=root,scan=lambda:set())
        with self.assertRaises(ValueError):handle({'scope':'archive','paths':['../../etc/passwd']},peer_uid=1000,allowed_uid=1000,archive=root,staging=root,scan=lambda:set())
 def test_archive_names_match_the_rotators_accepted_camera_names(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);f=root/'Brama_1/2026/11/21/190900.mp4';f.parent.mkdir(parents=True);f.write_bytes(b'video');st=f.stat()
        self.assertEqual(handle({'scope':'archive','paths':['Brama_1/2026/11/21/190900.mp4']},peer_uid=1000,allowed_uid=1000,archive=root,staging=root,scan=lambda:{(st.st_dev,st.st_ino)}),{'active':[True]})
 def test_staging_accepts_old_and_offset_names_with_or_without_session(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t)
        for relative in ('cam/2026/11/21/20261121T190900.mp4','cam/'+('a'*32)+'/2026/11/21/20261121T190900+0100.mp4'):
            file=root/relative;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'video');info=file.stat()
            req={'scope':'staging','paths':[relative]}
            self.assertEqual(handle(req,peer_uid=1000,allowed_uid=1000,archive=root,staging=root,scan=lambda:{(info.st_dev,info.st_ino)}),{'active':[True]})
        with self.assertRaises(ValueError):handle({'scope':'staging','paths':['cam/invalid/2026/11/21/20261121T190900.mp4']},peer_uid=1000,allowed_uid=1000,archive=root,staging=root,scan=lambda:set())
 def test_export_scope_allows_only_known_temporary_files(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);temp=root/'.sunhill-job-abc'/'part-0.mp4';temp.parent.mkdir();temp.write_bytes(b'data');info=temp.stat()
        request={'scope':'exports','paths':['.sunhill-job-abc/part-0.mp4']}
        self.assertEqual(handle(request,peer_uid=1000,allowed_uid=1000,archive=root,staging=root,exports=root,scan=lambda:{(info.st_dev,info.st_ino)}),{'active':[True]})
        final=root/'export-3-0123456789abcdef.mp4';final.write_bytes(b'data');info=final.stat()
        self.assertEqual(handle({'scope':'exports','paths':[final.name]},peer_uid=1000,allowed_uid=1000,archive=root,staging=root,exports=root,scan=lambda:{(info.st_dev,info.st_ino)}),{'active':[True]})
        with self.assertRaises(ValueError):handle({'scope':'exports','paths':['important.mp4']},peer_uid=1000,allowed_uid=1000,archive=root,staging=root,exports=root,scan=lambda:set())
if __name__=='__main__':unittest.main()
