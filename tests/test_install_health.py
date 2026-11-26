import io
import unittest
from pathlib import Path
from sys import path
from types import SimpleNamespace

path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sunhill_nvr.install_health import SERVICES, BOOT_UNITS, check


class Response(io.BytesIO):
    def __init__(self, data, content_type):
        super().__init__(data)
        self.status = 200
        self.headers = {'Content-Type': content_type}


class Opener:
    def __init__(self, app=b'{"status":"ok"}', content_type='text/html; charset=utf-8'):
        self.app, self.content_type, self.requests = app, content_type, []
    def open(self, request, timeout):
        self.requests.append(request)
        if isinstance(request, str):
            return Response(self.app, 'application/json')
        return Response(b'<html></html>', self.content_type)


class HealthTests(unittest.TestCase):
    def test_checks_each_service_and_both_http_paths(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command[1],command[-1]))
            return SimpleNamespace(returncode=0)
        opener = Opener()
        probes = []
        check('nvr.example.com', run=run, opener=opener,
              readiness=lambda unit, timeout: probes.append((unit, timeout)) or True)
        self.assertEqual(calls, [('is-active',unit) for unit in SERVICES]+[('is-enabled',unit) for unit in BOOT_UNITS])
        self.assertNotIn('sunhill-ftp.service',BOOT_UNITS)
        self.assertNotIn('sunhill-go2rtc.service',BOOT_UNITS)
        self.assertEqual(probes, [('sunhill-go2rtc.service', 3), ('sunhill-ftp.service', 3)])
        self.assertEqual(opener.requests[1].get_header('Host'), 'nvr.example.com')

    def test_running_but_disabled_service_is_rejected(self):
        def run(command,**kwargs):
            return SimpleNamespace(returncode=int(command[1]=='is-enabled' and command[-1]=='sunhill-recorder.service'))
        with self.assertRaisesRegex(RuntimeError,'not enabled for boot: sunhill-recorder.service'):
            check('nvr.example.com',run=run,opener=Opener(),readiness=lambda *a,**k:True)

    def test_failed_service_and_http_are_not_reported_as_success(self):
        good = lambda command, **kwargs: SimpleNamespace(returncode=0)
        bad = lambda command, **kwargs: SimpleNamespace(returncode=int(command[-1] == 'sunhill-recorder.service'))
        with self.assertRaisesRegex(RuntimeError, 'sunhill-recorder.service'):
            check('nvr.example.com', run=bad, opener=Opener())
        ready = lambda unit, timeout: True
        for failing in ('sunhill-go2rtc.service', 'sunhill-ftp.service'):
            with self.subTest(service=failing), self.assertRaisesRegex(RuntimeError, failing):
                check('nvr.example.com', run=good, opener=Opener(), readiness=lambda unit, timeout: unit != failing)
        with self.assertRaisesRegex(RuntimeError, 'application'):
            check('nvr.example.com', run=good, opener=Opener(app=b'not ok'), readiness=ready)
        with self.assertRaisesRegex(RuntimeError, 'Nginx'):
            check('nvr.example.com', run=good, opener=Opener(content_type='application/json'), readiness=ready)
        with self.assertRaises(ValueError):
            check('invalid hostname', run=good, opener=Opener())


if __name__ == '__main__':
    unittest.main()
