import sqlite3
import subprocess
import unittest
from pathlib import Path
from sys import path

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import database,security,service_monitor
from sunhill_nvr.upgrade_system import SERVICES


class ServiceMonitorTests(unittest.TestCase):
    def setUp(self):self.db=sqlite3.connect(':memory:');security.initialize(self.db);database.migrate(self.db)
    def tearDown(self):self.db.close()

    def test_two_failures_and_recovery_emit_only_transitions(self):
        states={name:'active' for name in SERVICES};target='sunhill-recorder.service'
        service_monitor.check(self.db,now=10,inspect=lambda:states)
        states[target]='failed';service_monitor.check(self.db,now=20,inspect=lambda:states)
        self.assertEqual(self.db.execute('SELECT state FROM service_health WHERE name=?',(target,)).fetchone()[0],'active')
        service_monitor.check(self.db,now=30,inspect=lambda:states);service_monitor.check(self.db,now=40,inspect=lambda:states)
        states[target]='active';service_monitor.check(self.db,now=50,inspect=lambda:states)
        self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['service_down','service_up'])

    def test_monitor_failure_does_not_claim_all_services_are_down(self):
        service_monitor.check(self.db,now=10,inspect=lambda:{})
        self.assertEqual(self.db.execute('SELECT state FROM pending_notifications').fetchone()[0],'monitor_error')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM service_health').fetchone()[0],0)
        service_monitor.check(self.db,now=20,inspect=lambda:{name:'active' for name in SERVICES})
        self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM pending_notifications ORDER BY id')],['monitor_error','monitor_ok'])

    def test_query_rejects_incomplete_systemctl_output(self):
        def run(args,**kwargs):return subprocess.CompletedProcess(args,0,stdout=b'not-a-state')
        with self.assertRaises(service_monitor.MonitorError):service_monitor.query(run=run)

    def test_stale_failure_is_reported_when_reconfirmed_without_duplicates(self):
        name='sunhill-index.service';states={name:'active' for name in SERVICES};states[name]='failed'
        with self.db:self.db.execute('INSERT INTO service_health VALUES(?,?,2,1,1)',(name,'inactive'))
        service_monitor.check(self.db,now=2000,inspect=lambda:states);service_monitor.check(self.db,now=2001,inspect=lambda:states)
        self.assertEqual(self.db.execute('SELECT source,state FROM pending_notifications').fetchall(),[(name,'service_down')])

    def test_transitional_state_does_not_falsely_confirm_an_old_failure(self):
        name='sunhill-index.service';states={name:'active' for name in SERVICES};states[name]='activating'
        with self.db:self.db.execute('INSERT INTO service_health VALUES(?,?,2,1,1)',(name,'inactive'))
        service_monitor.check(self.db,now=2000,inspect=lambda:states)
        self.assertEqual(self.db.execute('SELECT checked_at FROM service_health WHERE name=?',(name,)).fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM pending_notifications').fetchone()[0],0)
        states[name]='failed';service_monitor.check(self.db,now=2001,inspect=lambda:states)
        self.assertEqual(self.db.execute('SELECT source,state FROM pending_notifications').fetchall(),[(name,'service_down')])

    def test_first_failed_sample_does_not_renew_active_status(self):
        name='sunhill-index.service';states={unit:'active' for unit in SERVICES}
        service_monitor.check(self.db,now=10,inspect=lambda:states)
        states[name]='failed';service_monitor.check(self.db,now=20,inspect=lambda:states)
        self.assertEqual(self.db.execute('SELECT state,checked_at FROM service_health WHERE name=?',(name,)).fetchone(),('active',10))
        states[name]='active';service_monitor.check(self.db,now=30,inspect=lambda:states)
        states[name]='failed';service_monitor.check(self.db,now=2000,inspect=lambda:states)
        self.assertEqual(self.db.execute('SELECT state,checked_at FROM service_health WHERE name=?',(name,)).fetchone(),('unknown',2000))
        service_monitor.check(self.db,now=2001,inspect=lambda:states)
        self.assertEqual(self.db.execute('SELECT state FROM service_health WHERE name=?',(name,)).fetchone()[0],'inactive')
        self.assertEqual(self.db.execute('SELECT source,state FROM pending_notifications').fetchall(),[(name,'service_down')])

    def test_slow_service_checks_have_total_time_budget(self):
        clock=[0];timeouts=[]
        def run(args,**kwargs):
            timeouts.append(kwargs['timeout']);clock[0]+=4
            return subprocess.CompletedProcess(args,0,stdout=b'active')
        with self.assertRaises(service_monitor.MonitorError):service_monitor.query(run=run,monotonic=lambda:clock[0],budget=10)
        self.assertEqual(timeouts,[5,5,2])


if __name__=='__main__':unittest.main()
