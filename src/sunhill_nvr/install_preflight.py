"""Read-only checks for resources needed by a new installation on Linux."""
from __future__ import annotations

from pathlib import Path
import subprocess

# FTP listens on all interfaces. The remaining services listen on loopback,
# but rejecting a port used on a specific interface also avoids IPv6/dual-stack
# ambiguity and unintentional connection to a different application's service.
REQUIRED_TCP_PORTS = {21: 'FTP', 1984: 'go2rtc', 1985: 'live proxy',
                      80: 'LAN HTTP access', 8080: 'Nginx tunnel endpoint', 8088: 'web application',
                      8554: 'local RTSP gateway'}

def require_systemd_operational(*, run=subprocess.run) -> str:
    """Fail before system changes when systemd is absent or not operational.

    systemctl reports a nonzero exit status for 'degraded' even though systemd
    is running; do not confuse an unrelated failed unit with an offline manager.
    """
    try:
        result=run(['/usr/bin/systemctl','is-system-running'],stdin=subprocess.DEVNULL,
                   stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10)
        state=result.stdout.decode('ascii').strip()
    except (OSError,UnicodeError,AttributeError,subprocess.SubprocessError) as exc:
        raise ValueError('Cannot verify that systemd is operational') from exc
    if state not in ('running','degraded') or (state=='running' and result.returncode!=0):
        raise ValueError('Systemd must be running before installation')
    return state


def listening_ports(contents: str) -> set[int]:
    lines = contents.splitlines()
    if not lines or 'local_address' not in lines[0] or 'st' not in lines[0].split():
        raise ValueError('Cannot inspect listening TCP ports: invalid proc table')
    result = set()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 4 or ':' not in fields[1]:
            raise ValueError('Cannot inspect listening TCP ports: invalid proc entry')
        if fields[3] != '0A':
            continue
        try:
            port = int(fields[1].rsplit(':', 1)[1], 16)
        except ValueError as exc:
            raise ValueError('Cannot inspect listening TCP ports: invalid port') from exc
        if not 0 <= port <= 65535:
            raise ValueError('Cannot inspect listening TCP ports: invalid port')
        result.add(port)
    return result


def require_ports_free(proc_net: Path = Path('/proc/net'), *, lan_tls: bool = False) -> None:
    occupied = listening_ports((proc_net / 'tcp').read_text(encoding='ascii'))
    try:
        occupied |= listening_ports((proc_net / 'tcp6').read_text(encoding='ascii'))
    except FileNotFoundError:
        # Linux with IPv6 disabled need not expose this table.
        pass
    required = dict(REQUIRED_TCP_PORTS)
    if lan_tls:required[443] = 'LAN HTTPS'
    conflicts = sorted(occupied & required.keys())
    if conflicts:
        detail = ', '.join(f'{port} ({required[port]})' for port in conflicts)
        raise ValueError('Required TCP ports already in use: ' + detail)
