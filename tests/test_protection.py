import sqlite3,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import security,database,archive
from sunhill_nvr.protection import *
class ProtectionTests(unittest.TestCase):
 def setUp(self):
    self.db=sqlite3.connect(':memory:');security.initialize(self.db);database.migrate(self.db);archive.initialize(self.db)
    self.uid=security.create_user(self.db,'test','password-for-tests','admin')
    self.path='brama/2026/11/21/190900.mp4'
    self.db.execute('INSERT INTO recordings(relative_path,camera,local_time,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?)',(self.path,'brama','2026-11-21T19:09:00',60,100,1,1));self.db.commit()
 def tearDown(self):self.db.close()
 def test_protect_and_unprotect(self):
    self.assertEqual(protect(self.db,[self.path,self.path],self.uid),1);self.assertTrue(is_protected(self.db,self.path))
    self.assertEqual(unprotect(self.db,[self.path]),1);self.assertFalse(is_protected(self.db,self.path))
 def test_unknown_or_escape_rolls_back_all(self):
    with self.assertRaises(ValueError):protect(self.db,[self.path,'other/2026/11/21/190900.mp4'],self.uid)
    self.assertFalse(is_protected(self.db,self.path))
    with self.assertRaises(ValueError):protect(self.db,['../etc/passwd'],self.uid)
 def test_protection_participates_in_caller_transaction(self):
    self.db.execute('BEGIN IMMEDIATE')
    self.assertEqual(protect(self.db,[self.path],self.uid),1)
    self.assertTrue(self.db.in_transaction);self.db.rollback()
    self.assertFalse(is_protected(self.db,self.path))
    protect(self.db,[self.path],self.uid)
    self.db.execute('BEGIN IMMEDIATE');self.assertEqual(unprotect(self.db,[self.path]),1)
    self.assertTrue(self.db.in_transaction);self.db.rollback()
    self.assertTrue(is_protected(self.db,self.path))
if __name__=='__main__':unittest.main()
