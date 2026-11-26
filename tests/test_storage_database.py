import sqlite3,tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import archive,database,jobs,mount,security,storage_database

class StorageDatabaseTests(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
    self.dest=self.root/'archive';self.dest.mkdir();self.stage=self.root/'stage';self.stage.mkdir()
    self.db=database.open_db(self.root/'db',create=True);security.initialize(self.db);database.migrate(self.db);archive.initialize(self.db);jobs.initialize(self.db)
    self.db.execute("INSERT INTO recordings(relative_path,camera,local_time,duration,file_size,mtime_ns,generation) VALUES ('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00',1,1,1,1)")
    self.db.execute("INSERT INTO export_jobs(user_id,camera_id,request_json,status,created,updated) VALUES(1,1,'{}','queued',1,1)");self.db.commit()
    self.disk=mount.Mount('/data','/dev/test','ext4','abc')
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def test_missing_media_leaf_created_by_helper(self):
    from types import SimpleNamespace
    from unittest.mock import patch
    old=SimpleNamespace(archive=self.dest,staging=self.stage,exports=self.root/'old-exports');old.exports.mkdir()
    new=self.root/'new-archive';stage=self.root/'new-stage';exports=self.root/'new-exports'
    with patch('sunhill_nvr.storage_database.mount.inspect',return_value=self.disk),patch('sunhill_nvr.storage_database.switch') as switch:
        storage_database.switch_media(self.db,old,new,stage,exports)
    self.assertTrue(all(p.is_dir() for p in (new,stage,exports)));switch.assert_called_once()
 def test_nested_paths_preserve_archive_index_and_completed_exports(self):
    from types import SimpleNamespace
    from unittest.mock import patch
    old=SimpleNamespace(archive=self.dest,staging=self.stage,exports=self.root/'old-exports');old.exports.mkdir()
    name='export-1-0123456789abcdef.mp4';(old.exports/name).write_bytes(b'original')
    self.db.execute("UPDATE export_jobs SET status='done',result_name=? WHERE id=1",(name,));self.db.commit()
    stage=self.dest/'.sunhill-staging';exports=self.dest/'.sunhill-exports'
    with patch('sunhill_nvr.storage_database.mount.inspect',return_value=self.disk):
        storage_database.switch_media(self.db,old,self.dest,stage,exports)
    self.assertTrue(stage.is_dir());self.assertEqual((exports/name).read_bytes(),b'original')
    self.assertEqual((old.exports/name).read_bytes(),b'original')
    self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],1)
 def test_switch_records_identity_and_invalidates_old_index(self):
    storage_database.switch(self.db,self.dest,self.stage,inspect=lambda p:self.disk)
    self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],0)
    self.assertEqual(self.db.execute('SELECT status FROM export_jobs').fetchone()[0],'failed')
    self.assertFalse((self.dest/'.sunhill-volume-id').exists())
    self.assertEqual(self.db.execute("SELECT value FROM system_settings WHERE key='archive_mountpoint'").fetchone()[0],'/data')
    storage_database.switch(self.db,self.dest,self.stage,inspect=lambda p:self.disk)
    self.assertFalse((self.dest/'.sunhill-volume-id').exists())
 def test_different_filesystem_does_not_touch_database_or_marker(self):
    def inspect(p):return self.disk if p==self.dest else mount.Mount('/other','/dev/other','ext4','def')
    with self.assertRaises(ValueError):storage_database.switch(self.db,self.dest,self.stage,inspect=inspect)
    self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],1)
    self.assertFalse((self.dest/'.sunhill-volume-id').exists())
 def test_network_filesystem_is_rejected_before_modification(self):
    remote=mount.Mount('/data','server:/share','nfs',None)
    with self.assertRaisesRegex(ValueError,'filesystem'):
        storage_database.switch(self.db,self.dest,self.stage,inspect=lambda p:remote)
    self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],1)
    self.assertFalse((self.dest/'.sunhill-volume-id').exists())

 def test_completed_export_is_copied_and_conflicts_fail_without_overwrite(self):
    old=self.root/'old-exports';old.mkdir();new=self.root/'new-exports';new.mkdir();name='export-1-0123456789abcdef.mp4'
    (old/name).write_bytes(b'original')
    self.db.execute("UPDATE export_jobs SET status='done',result_name=? WHERE id=1",(name,));self.db.commit()
    storage_database.copy_exports(self.db,old,new)
    self.assertEqual((old/name).read_bytes(),b'original');self.assertEqual((new/name).read_bytes(),b'original')
    storage_database.copy_exports(self.db,old,new)
    (new/name).write_bytes(b'different')
    with self.assertRaises(ValueError):storage_database.copy_exports(self.db,old,new)
    self.assertEqual((new/name).read_bytes(),b'different');self.assertEqual((old/name).read_bytes(),b'original')

if __name__=='__main__':unittest.main()
