import sqlite3,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.jobs import *
class JobTests(unittest.TestCase):
    def setUp(self):self.db=sqlite3.connect(':memory:');initialize(self.db)
    def tearDown(self):self.db.close()
    def test_one_worker_lease_progress_completion(self):
        a=enqueue(self.db,1,1,{'start':10},now=1);b=enqueue(self.db,1,1,{'start':20},now=2)
        job=claim(self.db,now=3);self.assertEqual(job.id,a);self.assertIsNone(claim(self.db,now=4))
        self.assertTrue(heartbeat(self.db,job,.5,now=10));self.assertTrue(complete(self.db,job,'clip.mp4',now=11))
        self.assertEqual(claim(self.db,now=12).id,b)
    def test_expired_worker_cannot_publish(self):
        enqueue(self.db,1,1,{},now=1);job=claim(self.db,now=2,lease_seconds=10)
        self.assertFalse(complete(self.db,job,'clip.mp4',now=12));claim(self.db,now=12)
        self.assertEqual(self.db.execute('SELECT status FROM export_jobs').fetchone()[0],'failed')
    def test_unsafe_filename_and_nonfinite_progress(self):
        enqueue(self.db,1,1,{},now=1);job=claim(self.db,now=2)
        with self.assertRaises(ValueError):complete(self.db,job,'../file.mp4',now=3)
        with self.assertRaises(ValueError):heartbeat(self.db,job,float('nan'),now=3)
    def test_output_reservation_is_tied_to_job_and_live_lease(self):
        jid=enqueue(self.db,1,1,{},now=1);job=claim(self.db,now=2,lease_seconds=10)
        name=f'export-{jid}-0123456789abcdef.mp4'
        with self.assertRaises(ValueError):reserve_result(self.db,job,'export-99-0123456789abcdef.mp4',now=3)
        self.assertTrue(reserve_result(self.db,job,name,now=3));self.assertFalse(reserve_result(self.db,job,name,now=4))
        self.assertFalse(complete(self.db,job,f'export-{jid}-fedcba9876543210.mp4',now=5,require_reserved=True))
        self.assertTrue(complete(self.db,job,name,now=6,require_reserved=True))
        jid=enqueue(self.db,1,1,{},now=20);job=claim(self.db,now=21,lease_seconds=10)
        self.assertFalse(reserve_result(self.db,job,f'export-{jid}-0123456789abcdef.mp4',now=31))
    def test_cancel_running_prevents_heartbeat_and_completion(self):
        jid=enqueue(self.db,1,1,{},now=1);job=claim(self.db,now=2,lease_seconds=10)
        self.db.execute('BEGIN IMMEDIATE');self.assertTrue(request_cancel(self.db,jid,now=3));self.db.commit()
        self.assertTrue(cancel_requested(self.db,jid))
        self.assertFalse(heartbeat(self.db,job,.5,now=4))
        self.assertFalse(complete(self.db,job,'clip.mp4',now=4))
        self.assertTrue(fail(self.db,job,reason='generic',now=5))
        self.assertEqual(self.db.execute('SELECT status,error FROM export_jobs').fetchone(),('failed','Anulowano eksport'))
    def test_cancel_queued_and_crashed_running_jobs(self):
        running=enqueue(self.db,1,1,{},now=1);queued=enqueue(self.db,1,1,{},now=2)
        claim(self.db,now=3,lease_seconds=10)
        self.db.execute('BEGIN IMMEDIATE')
        self.assertTrue(request_cancel(self.db,running,now=4));self.assertTrue(request_cancel(self.db,queued,now=4))
        self.db.commit();self.assertIsNone(claim(self.db,now=13))
        self.assertEqual(self.db.execute('SELECT error FROM export_jobs ORDER BY id').fetchall(),[('Anulowano eksport',),('Anulowano eksport',)])
        with self.assertRaises(ValueError):request_cancel(self.db,queued)
if __name__=='__main__':unittest.main()
