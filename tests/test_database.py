import os,sqlite3,tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.database import open_db,migrate,backup,MIGRATIONS
from sunhill_nvr.security import initialize
class DatabaseTests(unittest.TestCase):
 @unittest.skipUnless(os.geteuid()==0, 'root and an unprivileged service user required')
 def test_readonly_root_reader_cold_wal_preserves_service_user_ownership(self):
    import pwd,subprocess,sys
    from contextlib import closing
    account=pwd.getpwnam('nobody')
    with tempfile.TemporaryDirectory() as t:
        directory=Path(t);os.chown(directory,account.pw_uid,account.pw_gid);directory.chmod(0o700)
        p=directory/'db.sqlite'
        def as_service(code):
            subprocess.run(['runuser','-u','nobody','--',sys.executable,'-c',code,str(p)],check=True,capture_output=True)
        as_service('import os,sqlite3,sys; os.umask(0o077); d=sqlite3.connect(sys.argv[1]); d.execute("PRAGMA journal_mode=WAL"); d.execute("CREATE TABLE t(x)"); d.commit(); d.close()')
        self.assertFalse(Path(str(p)+'-wal').exists())
        self.assertFalse(Path(str(p)+'-shm').exists())
        with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as db:
            self.assertEqual(db.execute('SELECT * FROM t').fetchall(),[])
            for suffix in ('-wal','-shm'):
                st=Path(str(p)+suffix).stat()
                self.assertEqual((st.st_uid,st.st_gid),(account.pw_uid,account.pw_gid))
                self.assertEqual(st.st_mode&0o777,0o600)
            with self.assertRaises(sqlite3.OperationalError):db.execute('INSERT INTO t VALUES(0)')
            as_service('import sqlite3,sys; d=sqlite3.connect(sys.argv[1]); d.execute("INSERT INTO t VALUES(1)"); d.commit(); d.close()')
            self.assertEqual(db.execute('SELECT * FROM t').fetchall(),[(1,)])
 def test_camera_counter_includes_legacy_exports_and_audit_history(self):
    from sunhill_nvr import database,jobs
    db=sqlite3.connect(':memory:');initialize(db);migrate(db);jobs.initialize(db)
    jobs.enqueue(db,1,50,{})
    db.execute("INSERT INTO audit_log(created_at,action,object_type,object_id) VALUES(1,'camera.delete','camera','99')");db.commit()
    db.execute('BEGIN IMMEDIATE');self.assertEqual(database.allocate_camera_id(db),100);db.commit()
    db.execute('DELETE FROM export_jobs');db.execute('DELETE FROM audit_log');db.commit()
    db.execute('BEGIN IMMEDIATE');self.assertEqual(database.allocate_camera_id(db),101);db.commit();db.close()
 def test_idempotent_migration_and_foreign_keys(self):
    with tempfile.TemporaryDirectory() as t:
        p=Path(t)/'db.sqlite';db=open_db(p,create=True);initialize(db);migrate(db);migrate(db)
        self.assertEqual(db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0],len(MIGRATIONS))
        with self.assertRaises(sqlite3.IntegrityError):db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES (42,'rtsp://x','rtsp')")
        db.close()
 def test_backup_is_consistent_and_not_overwritten(self):
    with tempfile.TemporaryDirectory() as t:
        p=Path(t)/'db.sqlite';db=open_db(p,create=True);initialize(db);migrate(db);target=Path(t)/'backup.sqlite'
        backup(db,target)
        from contextlib import closing
        with closing(sqlite3.connect(str(target))) as other:self.assertEqual(other.execute('SELECT count(*) FROM schema_migrations').fetchone()[0],len(MIGRATIONS))
        with self.assertRaises(FileExistsError):backup(db,target)
        db.close()
 def test_migration_preserves_existing_onvif_configuration(self):
    from unittest.mock import patch
    db=sqlite3.connect(':memory:');initialize(db)
    with patch.dict(MIGRATIONS,{k:v for k,v in MIGRATIONS.items() if k<5},clear=True):migrate(db)
    db.execute("INSERT INTO cameras VALUES(1,'cam','Camera',1)")
    db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode,onvif_url,onvif_username,onvif_password) VALUES (1,'rtsp://x','rtsp','http://x/onvif/device_service','admin','secret')");db.commit()
    migrate(db)
    self.assertEqual(db.execute('SELECT onvif_username,onvif_password,onvif_auth FROM camera_details').fetchone(),('admin','secret','digest'));db.close()
 def test_cancel_table_migration_preserves_existing_export_jobs(self):
    from unittest.mock import patch
    db=sqlite3.connect(':memory:');initialize(db)
    with patch.dict(MIGRATIONS,{k:v for k,v in MIGRATIONS.items() if k<7},clear=True):migrate(db)
    db.executescript('''CREATE TABLE export_jobs(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,camera_id INTEGER NOT NULL,
       request_json TEXT NOT NULL,status TEXT NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL,
       worker TEXT,lease_until INTEGER,progress REAL NOT NULL DEFAULT 0,result_name TEXT,error TEXT);
       INSERT INTO export_jobs(id,user_id,camera_id,request_json,status,created,updated)
       VALUES(1,1,1,'{}','queued',1,1);''')
    migrate(db);migrate(db)
    self.assertEqual(db.execute('SELECT id,status FROM export_jobs').fetchall(),[(1,'queued')])
    self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name='export_cancel_requests'").fetchone()[0],'export_cancel_requests')
    db.close()
 def test_notification_origin_migration_preserves_legacy_queue_and_inserts(self):
    from unittest.mock import patch
    db=sqlite3.connect(':memory:');initialize(db)
    with patch.dict(MIGRATIONS,{k:v for k,v in MIGRATIONS.items() if k<8},clear=True):migrate(db)
    with db:db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,'storage','disk_low',1)")
    migrate(db);migrate(db)
    with db:db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,'storage','disk_ok',2)")
    self.assertEqual(db.execute('SELECT state,origin FROM pending_notifications ORDER BY id').fetchall(),[('disk_low',''),('disk_ok','')]);db.close()
 def test_newer_database_fails_closed(self):
    db=sqlite3.connect(':memory:');initialize(db);migrate(db);db.execute('INSERT INTO schema_migrations VALUES (999)');db.commit()
    with self.assertRaises(RuntimeError):migrate(db)
    db.close()
 def test_open_db_closes_connection_when_setup_fails(self):
    from unittest.mock import patch,Mock
    with tempfile.TemporaryDirectory() as t:
        p=Path(t)/'db.sqlite';p.touch(mode=0o600)
        connection=Mock();connection.execute.side_effect=sqlite3.OperationalError('disk error')
        with patch('sunhill_nvr.database.sqlite3.connect',return_value=connection):
            with self.assertRaises(sqlite3.OperationalError):open_db(p)
        connection.close.assert_called_once_with()
 def test_open_db_rejects_non_wal_journal_and_closes(self):
    from unittest.mock import patch,Mock
    with tempfile.TemporaryDirectory() as t:
        p=Path(t)/'db.sqlite';p.touch(mode=0o600)
        connection=Mock();connection.execute.return_value.fetchone.return_value=('delete',)
        with patch('sunhill_nvr.database.sqlite3.connect',return_value=connection):
            with self.assertRaisesRegex(RuntimeError,'WAL'):open_db(p)
        connection.close.assert_called_once_with()
 def test_world_readable_database_rejected(self):
    with tempfile.TemporaryDirectory() as t:
        p=Path(t)/'db.sqlite';p.write_bytes(b'');p.chmod(0o644)
        with self.assertRaises(ValueError):open_db(p)
if __name__=='__main__':unittest.main()
