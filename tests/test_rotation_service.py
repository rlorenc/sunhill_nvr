import json
import sqlite3
import unittest
from pathlib import Path
from sys import path

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import database,retention,rotation_service,security


class RotationServiceTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');security.initialize(self.db);database.migrate(self.db)
        self.user=security.create_user(self.db,'admin','password-for-test','admin')
    def tearDown(self):self.db.close()

    def test_blocked_rotation_warning_is_deduplicated_and_recovery_is_reported(self):
        rotation_service.record(self.db,'rotation_blocked',before=8,after=8,protected_count=2,protected_bytes=20,now=100)
        rotation_service.record(self.db,'rotation_blocked',before=8,after=8,protected_count=2,protected_bytes=20,now=101)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM rotation_history').fetchone()[0],1)
        rotation_service.record(self.db,'rotation_ok',before=20,after=20,now=200)
        self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['rotation_blocked','rotation_ok'])

    def test_backward_clock_correction_does_not_suppress_history_for_hours(self):
        rotation_service.record(self.db,'rotation_blocked',now=10000)
        rotation_service.record(self.db,'rotation_blocked',now=100)
        rotation_service.record(self.db,'rotation_blocked',now=101)
        self.assertEqual(self.db.execute('SELECT created FROM rotation_history ORDER BY id').fetchall(),[(10000,),(100,)])

    def test_history_and_alarm_are_rolled_back_together(self):
        from unittest.mock import patch
        with patch('sunhill_nvr.rotation_service.health_service.rotation_state',side_effect=sqlite3.OperationalError('disk error')):
            with self.assertRaises(sqlite3.OperationalError):rotation_service.record(self.db,'rotation_blocked',files=1,now=100)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM rotation_history').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM pending_notifications').fetchone()[0],0)
        rotation_service.record(self.db,'rotation_blocked',files=1,now=101)
        self.assertEqual(self.db.execute('SELECT state FROM rotation_history').fetchone()[0],'rotation_blocked')
        self.assertEqual(self.db.execute('SELECT state FROM pending_notifications').fetchone()[0],'rotation_blocked')

    def test_preview_is_queued_and_contains_protected_counts(self):
        jid=rotation_service.enqueue_preview(self.db,self.user,now=100)
        with self.assertRaises(RuntimeError):rotation_service.enqueue_preview(self.db,self.user,now=101)
        result=retention.RetentionResult(8,9,('cam/2026/11/21/190900.mp4',),True,False,2,40,1,50)
        rotation_service.run_preview(self.db,lambda:result,now=102)
        row=self.db.execute('SELECT status,result_json FROM rotation_preview_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(row[0],'done');data=json.loads(row[1])
        self.assertEqual(data['files_count'],1);self.assertEqual(data['protected_count'],2);self.assertEqual(data['unknown_time_count'],1);self.assertEqual(data['unknown_time_bytes'],50);self.assertFalse(data['reached_target'])

    def test_preview_waits_for_rotation_lock_and_rejects_non_preview_result(self):
        jid=rotation_service.enqueue_preview(self.db,self.user)
        def busy():raise retention.RetentionBusy('busy')
        rotation_service.run_preview(self.db,busy)
        self.assertEqual(self.db.execute('SELECT status FROM rotation_preview_jobs WHERE id=?',(jid,)).fetchone()[0],'queued')
        rotation_service.run_preview(self.db,lambda:retention.RetentionResult(1,2,(),False,True))
        self.assertEqual(self.db.execute('SELECT status FROM rotation_preview_jobs WHERE id=?',(jid,)).fetchone()[0],'failed')

    def test_locked_worker_is_not_mistaken_for_abandoned_long_scan(self):
        import tempfile
        from types import SimpleNamespace
        from sunhill_nvr import workers
        with tempfile.TemporaryDirectory() as tmp:
            config=SimpleNamespace(archive_lock=Path(tmp)/'archive.lock')
            with self.db:self.db.execute("INSERT INTO rotation_preview_jobs(user_id,status,created,updated) VALUES(?,'running',1,1)",(self.user,))
            with retention.exclusive_worker_lock(Path(tmp)/'archive.lock.preview-worker'):
                workers.retention_preview_once(config,self.db)
            self.assertEqual(self.db.execute('SELECT status FROM rotation_preview_jobs').fetchone()[0],'running')
            workers.retention_preview_once(config,self.db)
            self.assertEqual(self.db.execute('SELECT status FROM rotation_preview_jobs').fetchone()[0],'failed')


if __name__=='__main__':unittest.main()
