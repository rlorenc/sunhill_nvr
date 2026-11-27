import tempfile
import unittest
import subprocess
from pathlib import Path
from sys import path

path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from sunhill_nvr.install_preflight import listening_ports, require_ports_free, require_systemd_operational


HEADER = '  sl  local_address rem_address   st tx_queue rx_queue\n'


def row(port, state='0A', address='00000000'):
    return f'   0: {address}:{port:04X} 00000000:0000 {state} 0:0 0:0\n'


class PortPreflightTests(unittest.TestCase):
    def test_systemd_must_be_operational_before_installation(self):
        def status(state,code):
            def run(args,**kwargs):
                self.assertEqual(args,['/usr/bin/systemctl','is-system-running']);self.assertEqual(kwargs['timeout'],10)
                return subprocess.CompletedProcess(args,code,stdout=state)
            return run
        self.assertEqual(require_systemd_operational(run=status(b'running\n',0)),'running')
        self.assertEqual(require_systemd_operational(run=status(b'degraded\n',1)),'degraded')
        for state,code in ((b'offline',1),(b'starting',1),(b'maintenance',1),(b'stopping',1),(b'unknown',1),(b'running',1),(b'\xff',1)):
            with self.subTest(state=state,code=code):
                with self.assertRaises(ValueError):require_systemd_operational(run=status(state,code))
        def stalled(*args,**kwargs):raise subprocess.TimeoutExpired('systemctl',10)
        with self.assertRaises(ValueError):require_systemd_operational(run=stalled)
    def test_only_listening_ports_are_collected(self):
        self.assertEqual(listening_ports(HEADER + row(21) + row(8080, '01')), {21})

    def test_detects_ipv4_and_ipv6_conflicts(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'tcp').write_text(HEADER + row(21), encoding='ascii')
            (root / 'tcp6').write_text(HEADER + row(1984, address='0' * 32), encoding='ascii')
            with self.assertRaisesRegex(ValueError, r'1984 \(go2rtc\)'):
                require_ports_free(root)

    def test_audio_backchannel_port_is_required(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'tcp').write_text(HEADER+row(1986),encoding='ascii')
            with self.assertRaisesRegex(ValueError,'1986.*audio backchannel'):require_ports_free(root)

    def test_missing_ipv6_table_and_unrelated_ports_are_allowed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'tcp').write_text(HEADER + row(443) + row(21), encoding='ascii')
            require_ports_free(root)

    def test_http_port_always_required_https_only_when_requested(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'tcp').write_text(HEADER + row(80) + row(443), encoding='ascii')
            with self.assertRaisesRegex(ValueError, r'80 \(LAN HTTP access\)'):
                require_ports_free(root)
            with self.assertRaisesRegex(ValueError, r'80 .*443'):
                require_ports_free(root,lan_tls=True)

    def test_missing_or_malformed_tables_fail_closed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with self.assertRaises(OSError):
                require_ports_free(root)
            (root / 'tcp').write_text(HEADER + 'malformed\n', encoding='ascii')
            with self.assertRaises(ValueError):
                require_ports_free(root)
            (root / 'tcp').write_text(HEADER, encoding='ascii')
            (root / 'tcp6').write_text(HEADER + row(0).replace(':0000 ', ':oops ', 1), encoding='ascii')
            with self.assertRaises(ValueError):
                require_ports_free(root)


if __name__ == '__main__':
    unittest.main()
