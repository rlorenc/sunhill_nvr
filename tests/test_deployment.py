import tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.config import parse
from sunhill_nvr.deployment import generate,preflight_unit_targets
class DeploymentTests(unittest.TestCase):
 def test_generates_units_without_replacing_existing_files(self):
    cfg=parse({'hostname':'nvr.example.com','database':'/var/lib/sunhill/db.sqlite','archive':'/home/store/kamery','staging':'/home/store/staging','exports':'/home/store/exports','archive_lock':'/var/lib/sunhill/archive.lock','activity_socket':'/run/sunhill/activity.sock','control_state':'/opt/sunhill-nvr/control','timezone':'Europe/Warsaw','minimum_free':10_000_000_000,'free_buffer':5_000_000_000})
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);generate(cfg,root);self.assertTrue((root/'units/sunhill-web.service').is_file());self.assertTrue((root/'nginx.conf').is_file())
        import json
        from sunhill_nvr import lifecycle
        manifest=json.loads((root/'lifecycle.json').read_text());self.assertEqual(manifest,lifecycle.default(rtsp_only=True))
        lifecycle.validate(manifest,{p.name for p in (root/'units').iterdir()})
        with self.assertRaises(FileExistsError):generate(cfg,root)
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);generate(cfg,root,tls_certificate=Path('/opt/sunhill-nvr/config/tls/fullchain.pem'),tls_key=Path('/opt/sunhill-nvr/config/tls/key.pem'))
        self.assertIn('listen 443 ssl;',(root/'nginx.conf').read_text())
 def test_preflight_rejects_existing_and_dangling_unit_without_changes(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);generated=root/'generated';generated.mkdir();system=root/'system';system.mkdir()
        unit=generated/'sunhill-web.service';unit.write_text('unit')
        preflight_unit_targets(generated,system)
        existing=system/unit.name;existing.write_text('existing')
        with self.assertRaises(FileExistsError):preflight_unit_targets(generated,system)
        self.assertEqual(existing.read_text(),'existing');existing.unlink()
        existing.symlink_to(system/'missing')
        with self.assertRaises(FileExistsError):preflight_unit_targets(generated,system)
        self.assertTrue(existing.is_symlink())
if __name__=='__main__':unittest.main()
