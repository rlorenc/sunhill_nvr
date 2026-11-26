import unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.config import parse
from sunhill_nvr.units import render,systemd_path
class UnitTests(unittest.TestCase):
 def config(self):return parse({'hostname':'nvr.example.com','database':'/var/lib/sunhill/db.sqlite','archive':'/home/store/kamery','staging':'/home/store/staging','exports':'/home/store/exports','archive_lock':'/var/lib/sunhill/archive.lock','activity_socket':'/run/sunhill/activity.sock','control_state':'/opt/sunhill-nvr/control','timezone':'Europe/Warsaw','minimum_free':10_000_000_000,'free_buffer':5_000_000_000})
 def test_web_is_unprivileged_and_sensitive_services_are_distinct(self):
    units=render(self.config());self.assertIn('User=sunhill',units['sunhill-web.service']);self.assertIn('User=root',units['sunhill-activity.service'])
    self.assertIn('RuntimeDirectory=sunhill',units['sunhill-activity.service']);self.assertIn('RuntimeDirectory=sunhill-ftp',units['sunhill-ftp.service']);self.assertIn('ProtectSystem=strict',units['sunhill-web.service']);self.assertIn('retention-worker --apply',units['sunhill-retention.service'])
    self.assertIn('clock-worker',units['sunhill-clock.service']);self.assertIn('User=sunhill',units['sunhill-clock.service'])
    self.assertIn('ExecStartPre=/usr/bin/python3 -m sunhill_nvr.cli check-storage',units['sunhill-ftp.service'])
    self.assertIn('ExecStartPre=/usr/sbin/runuser -u sunhill -- /usr/bin/python3 -m sunhill_nvr.cli prepare-ftp-homes',units['sunhill-ftp.service'])
    self.assertIn('Requires=sunhill-upgrade-recover.service',units['sunhill-recorder.service'])
    self.assertIn('retention-preview-worker',units['sunhill-retention-preview.service'])
    self.assertIn('stream-health-worker',units['sunhill-stream-health.service'])
    self.assertIn('sunhill_nvr.live_proxy',units['sunhill-live-proxy.service']);self.assertIn('User=sunhill',units['sunhill-live-proxy.service'])
    self.assertIn('"/home/store"',units['sunhill-recorder.service'])
    self.assertNotIn('"/home/store/kamery"',units['sunhill-recorder.service'])
    self.assertIn('"/home/store/kamery"',units['sunhill-index.service'])
    self.assertIn('upgrade_admin recover-offline',units['sunhill-upgrade-recover.service'])
    self.assertIn('Before=nginx.service ',units['sunhill-upgrade-recover.service'])
    self.assertIn('After=sunhill-upgrade-recover.service',units['sunhill-controller.timer'])
    self.assertIn('upgrade_admin finalize',units['sunhill-upgrade-ready.service'])
    self.assertIn('upgrade_admin process-inbox',units['sunhill-upgrade-apply.service']);self.assertIn('OnUnitActiveSec=15s',units['sunhill-upgrade-apply.timer'])
 def test_sqlite_wal_paths_for_controller_and_ftp_preflight(self):
    from dataclasses import replace
    for directory in ('/opt/sunhill-nvr/data','/var/lib/custom database'):
        config=replace(self.config(),database=Path(directory)/'sunhill.sqlite')
        services=render(config)
        for service in ('sunhill-controller.service','sunhill-ftp.service'):
            with self.subTest(directory=directory,service=service):
                unit=services[service]
                writable=next(line for line in unit.splitlines() if line.startswith('ReadWritePaths='))
                self.assertIn(systemd_path(config.database.parent),writable)
                self.assertIn('ProtectSystem=strict',unit)
                self.assertNotIn('"/opt/sunhill-nvr"',writable)
 def test_policy_and_gateway_guards(self):
    units=render(self.config());policy=units['sunhill-ftp-policy.service'];controller=units['sunhill-controller.service']
    self.assertIn('sunhill-ftp-policy.service',controller.split('After=')[1].splitlines()[0]);self.assertIn('ReadWritePaths=-/etc/apparmor.d/local /run',policy)
    self.assertIn('ExecCondition=/usr/bin/test -s /opt/sunhill-nvr/control/current/go2rtc.json',units['sunhill-go2rtc.service'])
    self.assertNotIn('/etc/apparmor.d',controller)
 def test_unit_path_escaping(self):
    self.assertEqual(systemd_path(Path('/tmp/50% photos')),'"/tmp/50%% photos"')
    with self.assertRaises(ValueError):systemd_path(Path('/tmp/evil\nUser=root'))
 def test_index_service_can_write_a_separately_configured_archive_lock(self):
    from dataclasses import replace
    config=replace(self.config(),archive_lock=Path('/var/locks/sunhill/archive.lock'))
    self.assertIn('"/var/locks/sunhill"',render(config)['sunhill-index.service'])
if __name__=='__main__':unittest.main()
