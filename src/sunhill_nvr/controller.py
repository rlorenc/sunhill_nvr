"""Privileged adapter for applying ProFTPD and go2rtc configuration."""
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
from .config_reconcile import apply,desired
from .ftp_accounts import render_server

class ControllerError(RuntimeError):pass

def set_ftp_running(running: bool, *, run=subprocess.run) -> None:
    """Start or stop only the dedicated NVR FTP service."""
    if type(running) is not bool:raise ValueError('invalid FTP service state')
    try:
        result=run(['/usr/bin/systemctl','start' if running else 'stop','sunhill-ftp.service'],
                   stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=None,timeout=30)
        if result.returncode!=0:raise ControllerError('could not change FTP service state')
    except (OSError,subprocess.SubprocessError) as exc:raise ControllerError('could not change FTP service state') from exc

def ensure_stream_running(*, run=subprocess.run) -> None:
    """Start the stream gateway idempotently, including after a host reboot."""
    try:
        result=run(['/usr/bin/systemctl','start','sunhill-go2rtc.service'],
                   stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=None,timeout=30)
        if result.returncode!=0:raise ControllerError('could not start stream gateway')
    except (OSError,subprocess.SubprocessError) as exc:
        raise ControllerError('could not start stream gateway') from exc

def guarded_reconcile(db, config, uid: int, gid: int, *, guard_factory, reconcile_config=None, set_running=None, ensure_stream=None, writer_check=None) -> None:
    """Disable FTP if storage identity cannot be established; re-enable on recovery."""
    reconcile_config=reconcile_config or reconcile
    set_running=set_running or set_ftp_running
    ensure_stream=ensure_stream or ensure_stream_running
    writer_check=writer_check or (lambda:True)
    def restore_gateway():
        # An interrupted configuration activation is not a verified previous
        # gateway. Do not undo a deliberate stop after a failed rollback.
        try:(config.control_state/'.activation-pending').lstat()
        except FileNotFoundError:
            if (config.control_state/'current'/'go2rtc.json').is_file():ensure_stream()
        except OSError:pass
    def writing_allowed():
        try:return writer_check() is True
        except (OSError,ValueError,RuntimeError,sqlite3.Error):return False
    try:
        valid=guard_factory(db,config.archive)()
    except (OSError,RuntimeError,ValueError) as exc:
        logging.warning('Archive availability check failed: %s',exc);valid=False
    if not valid:
        set_running(False)
        # A verified previous gateway configuration is independent of the
        # archive disk. Restore live view after reboot even when FTP is blocked.
        restore_gateway()
        if not (config.control_state/'current'/'go2rtc.json').is_file():
            raise ControllerError('archive storage unavailable and no previous gateway configuration')
        return
    if not writing_allowed():
        set_running(False);restore_gateway()
        if not (config.control_state/'current'/'go2rtc.json').is_file() and not (config.control_state/'.activation-pending').exists():
            raise ControllerError('initial configuration blocked by recording reserve or unreadable free space')
        return
    try:reconcile_config(db,config.control_state,config.archive,uid,gid)
    except Exception:
        try:set_running(False)
        except ControllerError:logging.exception('Failed to stop FTP after reconciliation error')
        try:restore_gateway()
        except ControllerError:logging.exception('Failed to restore previous gateway after reconciliation error')
        raise
    # Recheck after any configuration work before allowing uploads again.
    try:valid=guard_factory(db,config.archive)()
    except (OSError,RuntimeError,ValueError) as exc:
        logging.warning('Archive availability check failed: %s',exc);valid=False
    if not valid:
        set_running(False)
        restore_gateway()
        return
    if not writing_allowed():
        set_running(False);restore_gateway();return
    set_running(True)
    restore_gateway()

def require_trusted_directory(path: Path) -> None:
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
        raise ControllerError('configuration directory must be root owned and not group/world writable')

def _read_status_line(connection, deadline: float, *, ftp: bool = False) -> bytes:
    """Read a bounded greeting, including a complete FTP multiline reply."""
    pending=bytearray();total=0;multiline=False
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
            if not ftp:return line
            if multiline:
                if line.startswith(b'220 '):return line
            elif line.startswith(b'220-'):multiline=True
            else:return line
        if len(pending)>1024:return b''
    return b''


def service_ready(service: str, *, timeout: float = 10) -> bool:
    ports={'sunhill-go2rtc.service':1984,'sunhill-ftp.service':21}
    if service not in ports:return False
    deadline=time.monotonic()+timeout
    while (remaining:=deadline-time.monotonic())>0:
        try:
            with socket.create_connection(('127.0.0.1',ports[service]),timeout=min(1,remaining)) as connection:
                attempt=min(deadline,time.monotonic()+1)
                if service!='sunhill-ftp.service':
                    connection.sendall(b'GET /api HTTP/1.0\r\nHost: localhost\r\n\r\n')
                line=_read_status_line(connection,attempt,ftp=service=='sunhill-ftp.service')
                if service=='sunhill-ftp.service':
                    # A multiline banner is ready only after its final 220 line.
                    if line.startswith(b'220 '):return True
                elif line.split(b' ',2)[:2] in ([b'HTTP/1.1',b'200'],[b'HTTP/1.0',b'200']):return True
        except OSError:pass
        time.sleep(max(0,min(.25,deadline-time.monotonic())))
    return False


def reconcile(db, state_dir: Path, archive_root: Path, uid: int, gid: int, *,
              run=subprocess.run, trusted_check=require_trusted_directory, readiness=service_ready,
              supplementary_gids=()) -> None:
    """Apply config only after ProFTPD parses it and services restart successfully."""
    trusted_check(state_dir)
    contents=desired(db,archive_root,uid,gid,supplementary_gids=supplementary_gids)
    contents['proftpd.conf']=render_server(state_dir/'current'/'ftp.passwd',state_dir/'current'/'ftp.group')
    def validate(candidate: Path) -> bool:
        # Parse as JSON before ever exposing camera credentials to a daemon.
        try:json.loads((candidate/'go2rtc.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):return False
        # The live RuntimeDirectory is created by systemd only when FTP
        # starts and removed when it stops. Syntax validation must work before
        # the first start and must never touch a running daemon's scoreboard.
        check=candidate/'validate-proftpd.conf'
        try:
            with tempfile.TemporaryDirectory(prefix='validate-runtime-',dir=candidate) as runtime:
                check.write_text(render_server(candidate/'ftp.passwd',candidate/'ftp.group',runtime_dir=Path(runtime)),encoding='utf-8')
                os.chmod(check,0o600)
                result=run(['/usr/sbin/proftpd','-t','-c',str(check)],stdin=subprocess.DEVNULL,
                           stdout=None,stderr=None,timeout=15)
                if result.returncode!=0:
                    logging.error('ProFTPD rejected generated configuration (exit %d); inspect daemon and AppArmor logs',result.returncode)
                    return False
                return True
        except (OSError,subprocess.SubprocessError):
            logging.error('Unable to execute ProFTPD validation');return False
        finally:check.unlink(missing_ok=True)
    def restart(changed) -> bool:
        services=[]
        if 'go2rtc.json' in changed:services.append('sunhill-go2rtc.service')
        if changed & {'ftp.passwd','ftp.group','proftpd.conf'}:services.append('sunhill-ftp.service')
        try:
            for service in services:
                result=run(['/usr/bin/systemctl','restart',service],stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL,stderr=None,timeout=30)
                if result.returncode!=0:
                    logging.error('Could not restart service: %s (exit %d)',service,result.returncode);return False
                result=run(['/usr/bin/systemctl','is-active','--quiet',service],stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                if result.returncode!=0 or not readiness(service):
                    logging.error('Service failed readiness check: %s',service);return False
            return True
        except (OSError,subprocess.SubprocessError):return False
    def stop(changed) -> bool:
        services=[]
        if 'go2rtc.json' in changed:services.append('sunhill-go2rtc.service')
        if changed & {'ftp.passwd','ftp.group','proftpd.conf'}:services.append('sunhill-ftp.service')
        success=True
        for service in services:
            try:
                result=run(['/usr/bin/systemctl','stop',service],stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL,stderr=None,timeout=30)
                if result.returncode!=0:success=False
            except (OSError,subprocess.SubprocessError):success=False
        return success
    apply(state_dir,contents,validate=validate,restart=restart,stop=stop,service_gid=gid,required_files=('proftpd.conf',))
