"""Privileged RTSP gateway adapter."""
from __future__ import annotations
import json
import logging
import os
import stat
import socket
import sqlite3
import time
import tempfile
import subprocess
from pathlib import Path
from .config_reconcile import apply,desired_gateway

class ControllerError(RuntimeError):pass

def require_trusted_directory(path: Path) -> None:
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
        raise ControllerError('configuration directory must be root owned and not group/world writable')

def _read_status_line(connection, deadline: float) -> bytes:
    """Read a bounded HTTP status line."""
    pending=bytearray();total=0
    while total<8192:
        remaining=deadline-time.monotonic()
        if remaining<=0:return b''
        connection.settimeout(remaining)
        chunk=connection.recv(min(256,8192-total))
        if not chunk:return b''
        total+=len(chunk);pending.extend(chunk)
        while b'\n' in pending:
            raw,_,rest=pending.partition(b'\n');pending=bytearray(rest)
            if len(raw)>1024 or not raw.endswith(b'\r'):return b''
            line=bytes(raw[:-1])
            return line
        if len(pending)>1024:return b''
    return b''


def service_ready(service: str, *, timeout: float = 10) -> bool:
    ports={'sunhill-go2rtc.service':1984}
    if service not in ports:return False
    deadline=time.monotonic()+timeout
    while (remaining:=deadline-time.monotonic())>0:
        try:
            with socket.create_connection(('127.0.0.1',ports[service]),timeout=min(1,remaining)) as connection:
                attempt=min(deadline,time.monotonic()+1)
                connection.sendall(b'GET /api HTTP/1.0\r\nHost: localhost\r\n\r\n')
                line=_read_status_line(connection,attempt)
                if line.split(b' ',2)[:2] in ([b'HTTP/1.1',b'200'],[b'HTTP/1.0',b'200']):return True
        except OSError:pass
        time.sleep(max(0,min(.25,deadline-time.monotonic())))
    return False


def reconcile_gateway(db, state_dir: Path, gid: int, *, run=subprocess.run,
                      trusted_check=require_trusted_directory, readiness=service_ready) -> None:
    """Activate the RTSP gateway independently of the archive."""
    trusted_check(state_dir)
    unit='sunhill-go2rtc.service'
    def validate(candidate):
        try:return isinstance(json.loads((candidate/'go2rtc.json').read_text(encoding='utf-8')),dict)
        except (OSError,ValueError):return False
    def action(verb):
        try:
            return run(['/usr/bin/systemctl',verb,unit],stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL,stderr=None,timeout=30).returncode==0
        except (OSError,subprocess.SubprocessError):return False
    def restart(changed):
        return action('restart') and readiness(unit)
    apply(state_dir,desired_gateway(db),validate=validate,restart=restart,
          stop=lambda changed:action('stop'),service_gid=gid,required_files=('go2rtc.json',))
    # An unchanged configuration still has to be started after reboot.
    if not action('start') or not readiness(unit):raise ControllerError('stream gateway not ready')
