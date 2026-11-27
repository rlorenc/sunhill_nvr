"""Verify the only supported recording transport and rejected old manifests."""
import json,sqlite3,tempfile,unittest,subprocess,os
from pathlib import Path
from sunhill_nvr import lifecycle,security,database,controller,config_reconcile,workers
from sunhill_nvr.config import parse
from sunhill_nvr.units import render
from sunhill_nvr.upgrade_system import SystemAdapter
from sunhill_nvr.release import ReleaseError
from sunhill_nvr.cli import verify_rtsp_recorders
CFG=parse({'hostname':'nvr.example.com','database':'/var/lib/sunhill/db.sqlite','archive':'/home/store/kamery','staging':'/home/store/staging','exports':'/home/store/exports','archive_lock':'/var/lib/sunhill/archive.lock','activity_socket':'/run/sunhill/activity.sock','control_state':'/opt/sunhill-nvr/control','timezone':'Europe/Warsaw','minimum_free':10_000_000_000,'free_buffer':5_000_000_000})
class RTSPOnly(unittest.TestCase):
    def test_clean_install_schema_and_recording_modes(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);security.initialize(db);database.migrate(db);database.mark_rtsp(db)
        self.assertTrue(database.rtsp_only(db))
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse({'ftp_accounts','camera_ftp_accounts'}&names)
        db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        for mode in ('ftp','unknown'):
            with self.assertRaises(sqlite3.IntegrityError):db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://host/live',?)",(mode,))
        db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://host/live','rtsp')")
        self.assertEqual(workers.selected_recorders(db),{'cam'})
        self.assertEqual(verify_rtsp_recorders(db,probe=lambda slug:slug=='cam'),1)
        db.execute("UPDATE camera_details SET record_mode='none'");self.assertEqual(workers.selected_recorders(db),set())
    def test_legacy_modes_rejected_not_converted(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);security.initialize(db);database.migrate(db)
        db.execute('PRAGMA ignore_check_constraints=ON');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://host/live','ftp')")
        with self.assertRaises(RuntimeError):database.mark_rtsp(db)
        self.assertEqual(db.execute('SELECT record_mode FROM camera_details').fetchone(),('ftp',))
    def test_only_rtsp_manifest_and_no_legacy_units(self):
        units=render(CFG);manifest=lifecycle.default();self.assertEqual(manifest['version'],2)
        self.assertEqual(lifecycle.validate(manifest,set(units)),manifest)
        self.assertFalse(any('ftp' in name.lower() or 'ftp' in body.lower() for name,body in units.items()))
        with self.assertRaises(ValueError):lifecycle.validate(dict(manifest,version=1),set(units))
        with self.assertRaises(ValueError):lifecycle.validate(manifest,set(units)|{'sunhill-ftp.service'})
    def test_installed_manifest_missing_or_unsupported_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();units=render(CFG)
            (state/'managed-units.json').write_text(json.dumps(sorted(units)))
            adapter=SystemAdapter(root/'config',root/'current',state,root/'maintenance',1000,1000)
            with self.assertRaises(ReleaseError):adapter._runtime()
            (state/'lifecycle.json').write_text(json.dumps(dict(lifecycle.default(),version=1)))
            with self.assertRaises(ReleaseError):adapter._runtime()
    def test_gateway_renders_without_external_storage(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);security.initialize(db);database.migrate(db)
        db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://host/live','rtsp')")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[]
            def run(args,**kw):calls.append(args);return subprocess.CompletedProcess(args,0)
            controller.reconcile_gateway(db,root,os.getgid(),run=run,trusted_check=lambda p:None,readiness=lambda unit:True)
            self.assertEqual({p.name for p in (root/'current').iterdir()},{'go2rtc.json'})
            self.assertTrue(all(a[-1]=='sunhill-go2rtc.service' for a in calls))
