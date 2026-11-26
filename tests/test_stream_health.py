import sqlite3
import subprocess
import unittest
from pathlib import Path
from sys import path

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import database,security,stream_health


class StreamHealthTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');security.initialize(self.db);database.migrate(self.db)
        self.db.execute("INSERT INTO cameras VALUES(1,'brama','Brama',1)");self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://x','rtsp')");self.db.commit()
    def tearDown(self):self.db.close()

    def test_two_failures_trigger_alarm_and_success_clears_it(self):
        stream_health.check(self.db,now=10,gateway=lambda:True,inspect=lambda slug:False)
        self.assertEqual(self.db.execute('SELECT state,failures FROM stream_health').fetchone(),('unknown',1))
        stream_health.check(self.db,now=20,gateway=lambda:True,inspect=lambda slug:False)
        self.assertEqual(self.db.execute('SELECT state,failures FROM stream_health').fetchone(),('unavailable',2))
        stream_health.check(self.db,now=30,gateway=lambda:True,inspect=lambda slug:True)
        self.assertEqual(self.db.execute('SELECT state,failures FROM stream_health').fetchone(),('available',0))
        self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['offline','online'])

    def test_gateway_failure_is_not_misreported_as_camera_failure(self):
        stream_health.check(self.db,now=10,gateway=lambda:False,inspect=lambda slug:self.fail('should not probe'))
        self.assertIsNone(self.db.execute('SELECT state FROM stream_health').fetchone())
        self.assertEqual(self.db.execute('SELECT state FROM pending_notifications').fetchone()[0],'stream_service_error')
        stream_health.check(self.db,now=20,gateway=lambda:True,inspect=lambda slug:True)
        self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['stream_service_error','stream_service_ok'])

    def test_camera_still_unavailable_is_reported_again_after_gateway_recovers(self):
        with self.db:
            self.db.execute("INSERT INTO stream_health VALUES(1,'unavailable',2,10,10)")
            self.db.execute("INSERT INTO system_health VALUES('stream_service','stream_service_error',20)")
        stream_health.check(self.db,now=30,gateway=lambda:True,inspect=lambda slug:False)
        stream_health.check(self.db,now=31,gateway=lambda:True,inspect=lambda slug:False)
        self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['stream_service_ok','offline'])

    def test_failed_first_probe_does_not_renew_positive_sample_after_outage(self):
        stream_health.check(self.db,now=10,gateway=lambda:True,inspect=lambda slug:True)
        stream_health.check(self.db,now=20,gateway=lambda:False)
        stream_health.check(self.db,now=30,gateway=lambda:True,inspect=lambda slug:False)
        self.assertEqual(self.db.execute('SELECT state,failures FROM stream_health').fetchone(),('unknown',1))
        stream_health.check(self.db,now=40,gateway=lambda:True,inspect=lambda slug:False)
        self.assertEqual(self.db.execute('SELECT state,failures FROM stream_health').fetchone(),('unavailable',2))

    def test_failed_first_probe_does_not_renew_expired_positive_sample(self):
        stream_health.check(self.db,now=10,gateway=lambda:True,inspect=lambda slug:True)
        stream_health.check(self.db,now=2000,gateway=lambda:True,inspect=lambda slug:False)
        self.assertEqual(self.db.execute('SELECT state,failures FROM stream_health').fetchone(),('unknown',1))

    def test_clock_correction_replaces_future_gateway_and_stream_timestamps(self):
        with self.db:
            self.db.execute("INSERT INTO stream_health VALUES(1,'unavailable',2,10000,10000)")
            self.db.execute("INSERT INTO system_health VALUES('stream_service','stream_service_ok',10000)")
        stream_health.check(self.db,now=100,gateway=lambda:True,inspect=lambda slug:False)
        self.assertEqual(self.db.execute("SELECT changed_at FROM system_health WHERE name='stream_service'").fetchone()[0],100)
        self.assertEqual(self.db.execute('SELECT checked_at FROM stream_health').fetchone()[0],100)
        self.assertEqual(self.db.execute('SELECT state FROM pending_notifications').fetchall(),[('offline',)])

    def test_probe_uses_only_local_alias_and_checks_empty_output(self):
        calls=[]
        def run(args,**kwargs):calls.append(args);return subprocess.CompletedProcess(args,0)
        self.assertTrue(stream_health.probe('brama',run=run))
        self.assertIn('rtsp://127.0.0.1:8554/brama',calls[0]);self.assertIn('empty_output',calls[0])
        with self.assertRaises(ValueError):stream_health.probe('../outside',run=run)

    def test_changed_or_removed_camera_discards_inflight_probe(self):
        def changed(slug):
            with self.db:self.db.execute("UPDATE camera_details SET main_url='rtsp://new' WHERE camera_id=1")
            return False
        stream_health.check(self.db,now=10,gateway=lambda:True,inspect=changed)
        self.assertIsNone(self.db.execute('SELECT state FROM stream_health').fetchone())
        def removed(slug):
            with self.db:self.db.execute('DELETE FROM cameras WHERE id=1')
            return False
        stream_health.check(self.db,now=20,gateway=lambda:True,inspect=removed)
        self.assertIsNone(self.db.execute('SELECT state FROM stream_health').fetchone())

    def test_old_unavailable_state_is_reannounced_only_after_recheck(self):
        with self.db:
            self.db.execute("INSERT INTO stream_health VALUES(1,'unavailable',2,1,1)")
            self.db.execute("INSERT INTO system_health VALUES('stream_service','stream_service_ok',1)")
        stream_health.check(self.db,now=2000,gateway=lambda:True,inspect=lambda slug:False)
        stream_health.check(self.db,now=2001,gateway=lambda:True,inspect=lambda slug:False)
        self.assertEqual(self.db.execute('SELECT source,state FROM pending_notifications').fetchall(),[('brama','offline')])

    def test_stop_request_skips_remaining_camera_probes(self):
        self.db.execute("INSERT INTO cameras VALUES(2,'dom','Dom',1)")
        self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(2,'rtsp://y','rtsp')");self.db.commit()
        calls=[];stopping=[False]
        def inspect(slug):calls.append(slug);stopping[0]=True;return True
        stream_health.check(self.db,now=10,gateway=lambda:True,inspect=inspect,should_stop=lambda:stopping[0])
        self.assertEqual(calls,['brama'])

    def test_stream_check_records_each_probe_completion_time(self):
        from unittest.mock import patch
        self.db.execute("INSERT INTO cameras VALUES(2,'dom','Dom',1)")
        self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(2,'rtsp://y','rtsp')");self.db.commit()
        with patch('sunhill_nvr.stream_health.time.time',side_effect=[100,110,120]):
            stream_health.check(self.db,gateway=lambda:True,inspect=lambda slug:True)
        self.assertEqual(self.db.execute('SELECT camera_id,checked_at FROM stream_health ORDER BY camera_id').fetchall(),[(1,110),(2,120)])


if __name__=='__main__':unittest.main()
