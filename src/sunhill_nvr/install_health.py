"""Check that an experimental installation actually started its main services."""
from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request

from .nginx_config import HOST
from .controller import service_ready
from .lifecycle import ENABLED

SERVICES = (
    'nginx.service', 'sunhill-activity.service', 'sunhill-web.service',
    'sunhill-live-proxy.service', 'sunhill-go2rtc.service', 'sunhill-ftp.service',
    'sunhill-export.service', 'sunhill-index.service', 'sunhill-recorder.service',
    'sunhill-health.service', 'sunhill-stream-health.service', 'sunhill-clock.service',
    'sunhill-retention.service', 'sunhill-retention-preview.service',
    'sunhill-controller.timer', 'sunhill-upgrade-apply.timer',
)

BOOT_UNITS = ('nginx.service', *ENABLED, 'sunhill-upgrade-ready.service', 'sunhill-upgrade-apply.timer')

def check(host: str, *, run=subprocess.run, opener=None, readiness=None) -> None:
    if not HOST.fullmatch(host):
        raise ValueError('invalid hostname')
    for unit in SERVICES:
        result = run(['/usr/bin/systemctl', 'is-active', '--quiet', unit],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, timeout=5)
        if result.returncode != 0:
            raise RuntimeError('service is not active: ' + unit)
    # Running now is not proof that services survive a reboot. go2rtc and
    # FTP deliberately start through the boot controller, not independently.
    for unit in BOOT_UNITS:
        result = run(['/usr/bin/systemctl', 'is-enabled', '--quiet', unit],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, timeout=5)
        if result.returncode != 0:
            raise RuntimeError('service is not enabled for boot: ' + unit)
    readiness = readiness or service_ready
    for unit in ('sunhill-go2rtc.service', 'sunhill-ftp.service'):
        if not readiness(unit, timeout=3):
            raise RuntimeError('service is not responding: ' + unit)
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8088/internal/health', timeout=3) as response:
        if response.status != 200 or response.read(1024) != b'{"status":"ok"}':
            raise RuntimeError('web application health check failed')
    request = urllib.request.Request('http://127.0.0.1:8080/', headers={'Host': host})
    with opener.open(request, timeout=3) as response:
        if response.status != 200 or not response.headers.get('Content-Type', '').startswith('text/html'):
            raise RuntimeError('Nginx endpoint health check failed')


def wait(host: str, *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            check(host)
            return
        except (RuntimeError, OSError, subprocess.SubprocessError, urllib.error.URLError) as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(str(exc)) from exc
            time.sleep(1)


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2:
        raise SystemExit('usage: python3 -m sunhill_nvr.install_health HOSTNAME')
    try:
        wait(sys.argv[1])
    except (RuntimeError, ValueError) as error:
        raise SystemExit('Installation did not pass startup checks: ' + str(error)) from error
