import json,os,tempfile,unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from repair_0_2_1_units import repair,NAMES
from sunhill_nvr.config import load
from sunhill_nvr.units import render,systemd_path

@unittest.skipUnless(os.geteuid()==0,'requires root-owned unit fixtures')
class ResumeInstallTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
    self.root=Path(self.temp.name)/'app';self.system=Path(self.temp.name)/'system';self.system.mkdir()
    for name in ('config','control/upgrade','data','releases/0.2.1'):(self.root/name).mkdir(parents=True,exist_ok=True)
    (self.root/'current').symlink_to('releases/0.2.1')
    cfg={'hostname':'nvr.example.com','database':str(self.root/'data/sunhill.sqlite'),'archive':str(self.root/'media/kamery'),'staging':str(self.root/'media/staging'),'exports':str(self.root/'media/exports'),'archive_lock':str(self.root/'data/archive.lock'),'activity_socket':'/run/sunhill-nvr/activity.sock','control_state':str(self.root/'control'),'timezone':'Europe/Warsaw','minimum_free':0,'free_buffer':0}
    conf=self.root/'config/config.json';conf.write_text(json.dumps(cfg));self.db=self.root/'data/sunhill.sqlite';self.db.write_bytes(b'original database untouched')
    c=load(conf);self.new=render(c,legacy_policy=True);self.old={}
    for n in NAMES:
        self.old[n]=self.new[n].replace('ReadWritePaths='+systemd_path(c.database.parent)+' ','ReadWritePaths=',1)
        (self.system/n).write_text(self.old[n])
 def run_repair(self):
    # Fixtures live under /tmp; production code still verifies every ancestor.
    with patch('repair_0_2_1_units.trusted_directory'):
        return repair(self.root,self.system)
 def test_known_units_repaired_and_backed_up_without_database_change(self):
    host,backup=self.run_repair();self.assertEqual(host,'nvr.example.com');self.assertIsNotNone(backup)
    for n in NAMES:
        self.assertEqual((self.system/n).read_text(),self.new[n]);self.assertEqual((backup/n).read_text(),self.old[n])
    self.assertEqual(self.db.read_bytes(),b'original database untouched')
    self.assertEqual(self.run_repair(),(host,None))
 def test_modified_unit_rejected_before_any_unit_changes(self):
    (self.system/NAMES[1]).write_text('custom')
    with self.assertRaises(ValueError):self.run_repair()
    self.assertEqual((self.system/NAMES[0]).read_text(),self.old[NAMES[0]])
    self.assertEqual((self.system/NAMES[1]).read_text(),'custom')
 def test_pending_recovery_and_wrong_version_refused(self):
    journal=self.root/'control/upgrade/recovery-unit-migration';journal.mkdir()
    with self.assertRaises(ValueError):self.run_repair()
    journal.rmdir();current=self.root/'current';current.unlink();current.symlink_to('releases/0.2.0')
    with self.assertRaises(ValueError):self.run_repair()
    self.assertEqual((self.system/NAMES[0]).read_text(),self.old[NAMES[0]])
