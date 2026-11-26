from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from sys import path
from types import SimpleNamespace

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.database import open_db
from sunhill_nvr.upgrade_database import backup, restore


class UpgradeDatabaseTests(unittest.TestCase):
    def test_backup_and_restore_include_committed_wal_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db_path=root/'db.sqlite';db=open_db(db_path,create=True)
            db.execute('CREATE TABLE items(value INTEGER)');db.execute('INSERT INTO items VALUES(7)');db.commit()
            backup(SimpleNamespace(database=db_path),root/'backup.sqlite')
            db.execute('UPDATE items SET value=9');db.commit();db.close()
            restore(SimpleNamespace(database=db_path),root/'backup.sqlite')
            with closing(sqlite3.connect(db_path)) as check, check:self.assertEqual(check.execute('SELECT value FROM items').fetchone()[0],7)

    def test_invalid_relational_backup_never_overwrites_live_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);live=root/'live.sqlite';bad=root/'bad.sqlite';out=root/'backup.sqlite'
            cfg=SimpleNamespace(database=live)
            with closing(sqlite3.connect(live)) as db, db:
                db.execute('CREATE TABLE original(value INTEGER)');db.execute('INSERT INTO original VALUES(42)')
            with closing(sqlite3.connect(bad)) as db, db:
                db.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)')
                db.execute('CREATE TABLE child(parent_id REFERENCES parent(id))')
                db.execute('INSERT INTO child VALUES(999)')
            bad.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError,'foreign key'):restore(cfg,bad)
            with closing(sqlite3.connect(live)) as db, db:self.assertEqual(db.execute('SELECT value FROM original').fetchone(),(42,))
            with self.assertRaisesRegex(RuntimeError,'foreign key'):backup(SimpleNamespace(database=bad),out)
            self.assertFalse(out.exists())

    def test_restore_rejects_symbolic_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);link=root/'backup.sqlite';link.symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):restore(SimpleNamespace(database=root/'db.sqlite'),link)


if __name__=='__main__':unittest.main()
