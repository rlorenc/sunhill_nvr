import json,os,tempfile,unittest
from dataclasses import asdict
from pathlib import Path
from sys import path
from types import SimpleNamespace
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import config,mount,units
from sunhill_nvr.storage_system import StorageSystem,MediaSelection
from sunhill_nvr.upgrade_system import SystemAdapter

class StorageSystemTests(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();base=Path(self.tmp.name).resolve();self.base=base
    self.state=base/'state';self.state.mkdir();self.system=base/'system';self.system.mkdir()
    self.data=base/'data';self.data.mkdir();self.old=self.data/'old';self.old.mkdir();self.new=self.data/'new';self.new.mkdir()
    self.stage=self.data/'stage';self.stage.mkdir();exports=self.data/'exports';exports.mkdir()
    dbdir=base/'db';dbdir.mkdir();runtime=base/'runtime';runtime.mkdir()
    raw={'hostname':'nvr.example.com','database':str(dbdir/'db.sqlite'),'archive':str(self.old),'staging':str(self.stage),'exports':str(exports),'archive_lock':str(dbdir/'lock'),'activity_socket':'/run/sunhill/activity.sock','control_state':str(self.state),'timezone':'Europe/Warsaw','minimum_free':10,'free_buffer':5}
    self.conf=base/'config.json';self.conf.write_text(json.dumps(raw));self.cfg=config.parse(raw)
    generated=units.render(self.cfg)
    for name,content in generated.items():(self.system/name).write_text(content)
    (self.state/'managed-units.json').write_text(json.dumps(sorted(generated)))
    self.release=base/'release';self.release.mkdir();self.current=base/'current';self.current.symlink_to(self.release)
    self.adapter=SystemAdapter(self.conf,self.current,self.state,base/'maintenance',os.getuid(),os.getgid(),run=lambda *a,**kw:SimpleNamespace(returncode=0),system_dir=self.system)
    self.system_adapter=StorageSystem(self.adapter);self.backup=self.state/'backup'
 def tearDown(self):self.tmp.cleanup()
 def test_identical_media_selection_returns_current_config(self):
    self.assertEqual(self.system_adapter.candidate(MediaSelection(self.cfg.archive,self.cfg.exports,self.cfg.staging)),self.cfg)
 def test_missing_media_leaf_prevalidated_without_creating(self):
    archive=self.data/'missing';exports=self.data/'exports-new';stage=self.data/'stage-new';disk=mount.Mount('/','/dev/test','ext4','abc')
    with patch('sunhill_nvr.storage_system.mount.inspect',return_value=disk):
        result=self.system_adapter.candidate(MediaSelection(archive,exports,stage))
    self.assertEqual(result.archive,archive);self.assertFalse(archive.exists());self.assertFalse(exports.exists());self.assertFalse(stage.exists())
 def test_nested_auxiliary_candidate_and_ftp_isolation(self):
    disk=mount.Mount('/','/dev/test','ext4','abc')
    for archive in (self.old,self.data/'missing'):
        with self.subTest(archive=archive):
            stage=archive/'.sunhill-staging';exports=archive/'.sunhill-exports'
            with patch('sunhill_nvr.storage_system.mount.inspect',return_value=disk):
                result=self.system_adapter.candidate(MediaSelection(archive,exports,stage))
            self.assertEqual((result.archive,result.staging,result.exports),(archive,stage,exports))
            self.assertFalse(stage.exists());self.assertFalse(exports.exists())
            generated=units.render(result)
            self.assertIn(f'InaccessiblePaths="{stage}" "{exports}"',generated['sunhill-ftp.service'])
            self.assertNotIn('InaccessiblePaths=',generated['sunhill-recorder.service'])
            self.assertEqual({n:v for n,v in units.render(self.cfg).items() if n.startswith('sunhill-upgrade-')},{n:v for n,v in generated.items() if n.startswith('sunhill-upgrade-')})
 def test_nested_symlink_is_rejected(self):
    (self.old/'.sunhill-staging').symlink_to(self.stage,target_is_directory=True)
    with patch('sunhill_nvr.storage_system.mount.inspect',return_value=mount.Mount('/','/dev/test','ext4','abc')),self.assertRaisesRegex(Exception,'canonical'):
        self.system_adapter.candidate(MediaSelection(self.old,self.old/'.sunhill-exports',self.old/'.sunhill-staging'))
 def test_coordinator_noop_status_correlates_and_skips_transaction(self):
    from sunhill_nvr import storage_admin,storage_inbox
    inbox=self.base/'inbox';inbox.mkdir(mode=0o700)
    storage_inbox.submit(inbox,str(self.cfg.archive)+'/',1,exports=str(self.cfg.exports),staging=str(self.cfg.staging),request_id='b'*32)
    with patch('sunhill_nvr.storage_admin.os.chown'),patch('sunhill_nvr.storage_admin.storage_transaction.activate') as activate:
        self.assertTrue(storage_admin.process(self.adapter,inbox));activate.assert_not_called()
    status=json.loads((self.state/'storage-status.json').read_text());self.assertEqual(status['state'],'done');self.assertEqual(status['request_id'],'b'*32);self.assertFalse((inbox/'storage-request.json').exists())
 def test_apply_and_restore_switch_configuration_and_units(self):
    disk=mount.Mount('/data','/dev/test','ext4','abc');calls=[]
    def copy(adapter,destination,*,restore=False):
        calls.append('restore' if restore else 'backup')
        if not restore:destination.write_bytes(b'database backup')
        else:self.assertEqual(destination.read_bytes(),b'database backup')
    def service(adapter,package,module,*args):calls.append(module)
    with patch.object(StorageSystem,'_db_copy',copy),patch.object(SystemAdapter,'_as_service',service),patch('sunhill_nvr.storage_system.mount.inspect',return_value=disk):
        self.system_adapter.backup(self.backup)
        self.system_adapter.apply(self.new)
        self.assertEqual(config.load(self.conf).archive,self.new)
        self.assertIn(str(self.new),(self.system/'sunhill-index.service').read_text())
        self.system_adapter.restore(self.backup)
    self.assertEqual(config.load(self.conf).archive,self.old)
    self.assertIn(str(self.old),(self.system/'sunhill-index.service').read_text())
    self.assertEqual(calls,['backup','sunhill_nvr.storage_database','restore'])
 def test_new_root_outside_data_directory_rejected(self):
    outside=self.base/'outside';outside.mkdir()
    with self.assertRaises(Exception):self.system_adapter.candidate(outside)

 def test_media_selection_can_use_separate_export_mount_and_new_archive_mount(self):
    outside=self.base/'other';outside.mkdir();new=outside/'recordings';new.mkdir();exports=self.base/'other-exports';exports.mkdir()
    disk=mount.Mount(str(outside),'/dev/other','ext4','other');export_disk=mount.Mount(str(exports),'/dev/export','ext4','exp')
    def inspect(p):return export_disk if p==exports else disk
    with patch('sunhill_nvr.storage_system.mount.inspect',side_effect=inspect):
        candidate=self.system_adapter.candidate(MediaSelection(new,exports))
    self.assertEqual(candidate.archive,new);self.assertEqual(candidate.exports,exports);self.assertEqual(candidate.staging,outside/'.sunhill-staging')
    before=units.render(self.cfg);after=units.render(candidate)
    self.assertEqual({n:v for n,v in before.items() if n.startswith('sunhill-upgrade-')},{n:v for n,v in after.items() if n.startswith('sunhill-upgrade-')})
 def test_media_selection_rejects_private_state_paths(self):
    with self.assertRaises(Exception):self.system_adapter.candidate(MediaSelection(self.old,self.state))

if __name__=='__main__':unittest.main()
