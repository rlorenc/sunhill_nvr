#!/usr/bin/env python3
"""Real ProFTPD acceptance test on a disposable Ubuntu VM, never a production NVR.

Uses the production generator and AppArmor policy. Only listener address/port
and temporary fixture paths differ from production; no mocked daemon or socket.
Missing dependencies, profile enforcement or a skipped scenario are failures.
"""
from __future__ import annotations

import contextlib
import ftplib
import io
import os
from pathlib import Path
import pwd
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sunhill_nvr import apparmor_ftp, ftp_accounts


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def denied(operation):
    try:
        operation()
    except ftplib.error_perm:
        return
    raise RuntimeError('FTP unexpectedly accepted forbidden operation')


def connect(port, source='127.0.0.1'):
    client = ftplib.FTP()
    try:
        banner = client.connect('127.0.0.1', port, timeout=5, source_address=(source, 0))
        require(banner.startswith('220'), 'missing complete 220 FTP greeting')
        return client
    except BaseException:
        client.close()
        raise


@contextlib.contextmanager
def daemon(config, port, log):
    binary = '/usr/sbin/proftpd'
    result = subprocess.run([binary, '-t', '-c', str(config)], capture_output=True, text=True, timeout=20)
    require(result.returncode == 0, 'ProFTPD parser failed: ' + result.stdout + result.stderr)
    require('mod_ctrls' not in result.stderr, 'unexpected controls diagnostic: ' + result.stderr)
    with log.open('w+') as stream:
        process = subprocess.Popen([binary, '-n', '-c', str(config)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', start_new_session=True)
        # The test process, not the confined daemon, writes the diagnostic log.
        def drain():
            for line in process.stdout:
                stream.write(line)
        reader = threading.Thread(target=drain, daemon=True); reader.start()
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('ProFTPD exited before greeting')
                try:
                    with connect(port) as client:
                        client.quit()
                    break
                except (OSError, EOFError, ftplib.Error):
                    time.sleep(.2)
            else:
                raise RuntimeError('real ProFTPD never returned a 220 greeting')
            attr = Path(f'/proc/{process.pid}/attr/apparmor/current')
            require(attr.read_text().strip() == 'proftpd (enforce)', 'real daemon is not confined by the expected AppArmor profile')
            yield
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            reader.join(timeout=5)
            if reader.is_alive():
                try:os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:pass
                reader.join(timeout=5)
            require(not reader.is_alive(), 'daemon output pipe did not close')
            process.stdout.close()
            stream.flush(); stream.seek(0)
            diagnostics = stream.read()
            print(diagnostics, file=sys.stderr, end='')
            require('unable to bind to local socket' not in diagnostics, 'unexpected controls socket failure')


def main():
    require(os.geteuid() == 0, 'run as root on disposable VM')
    require((os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted') or
            os.environ.get('SUNHILL_DISPOSABLE_TEST_VM') == '1', 'requires explicitly disposable VM')
    require(not os.path.lexists('/opt/sunhill-nvr'), 'existing installation detected; refusing test')
    require(Path('/usr/sbin/proftpd').is_file(), 'real ProFTPD binary missing')
    os_release = Path('/etc/os-release').read_text().splitlines()
    require('ID=ubuntu' in os_release and any(line in ('VERSION_ID="26.04"', 'VERSION_ID=26.04') for line in os_release), 'requires Ubuntu 26.04')
    account = pwd.getpwnam('sunhill')
    require(account.pw_uid >= 1000 and account.pw_gid >= 1000, 'invalid service account')
    profile = Path('/sys/kernel/security/apparmor/profiles')
    require(profile.is_file() and 'proftpd (enforce)' in profile.read_text().splitlines(), 'ProFTPD AppArmor profile must be enforced')
    print(subprocess.check_output(['/usr/sbin/proftpd', '-v'], text=True).strip(), flush=True)
    policy = Path('/etc/apparmor.d/local/proftpd')
    old_policy = policy.read_text() if policy.exists() else None
    with tempfile.TemporaryDirectory(prefix='sunhill-proftpd-smoke-', dir='/opt') as tmp:
        root = Path(tmp); root.chmod(0o755)
        archive = root/'kamery'; archive.mkdir(mode=0o750)
        for folder in (archive, archive/'cam-a', archive/'cam-b'):
            folder.mkdir(mode=0o750, exist_ok=True)
            os.chown(folder, account.pw_uid, account.pw_gid)
        control = root/'control'; control.mkdir(mode=0o750); os.chown(control, 0, account.pw_gid)
        candidate = control/'config-smoke'; candidate.mkdir(mode=0o750); os.chown(candidate, 0, account.pw_gid)
        runtime = candidate/'validate-runtime-smoke'; runtime.mkdir(mode=0o700)
        auth, group = candidate/'ftp.passwd', candidate/'ftp.group'
        config = candidate/'proftpd.conf'; log = root/'daemon.log'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        password = secrets.token_urlsafe(24)
        h = ftp_accounts.make_hash(password)
        shared = ftp_accounts.Account('test-shared', h)
        private = ftp_accounts.Account('test-private', h, 'cam-a')
        def write_config(accounts):
            auth.write_text(ftp_accounts.render(accounts, archive, account.pw_uid, account.pw_gid))
            group.write_text(ftp_accounts.render_group('sunhillftp', account.pw_gid, accounts))
            for p in (auth, group):
                os.chown(p, 0, account.pw_gid); p.chmod(0o640)
            generated = ftp_accounts.render_server(auth, group, runtime_dir=runtime, passive_start=52000, passive_end=52100)
            require(generated.count('Port 21\n') == 1 and generated.count('DefaultAddress 0.0.0.0\n') == 1, 'listener fixture mismatch')
            config.write_text(generated.replace('Port 21\n', f'Port {port}\n').replace('DefaultAddress 0.0.0.0\n', 'DefaultAddress 127.0.0.1\n'))
            config.chmod(0o600)
        try:
            require(apparmor_ftp.configure(archive, control), 'could not configure real AppArmor profile')
            write_config([])
            with daemon(config, port, log):
                with connect(port) as client:
                    denied(lambda: client.login('unknown', password))
                try:
                    with connect(port, '127.0.0.2'):
                        raise RuntimeError('address outside allowlist accepted')
                except (ftplib.error_perm, ftplib.error_temp, EOFError, ConnectionResetError):
                    pass
            print('PASS: empty account table, real banner, deny unknown account and non-allowlisted IP', flush=True)
            write_config([shared, private])
            with daemon(config, port, log):
                with connect(port) as client:
                    denied(lambda: client.login(shared.username, 'wrong-password-123'))
                with connect(port) as client:
                    client.login(shared.username, password)
                    client.cwd('/cam-b')
                    client.storbinary('STOR sample.mp4', io.BytesIO(b'original bytes'))
                    require((archive/'cam-b/sample.mp4').read_bytes() == b'original bytes', 'uploaded bytes differ')
                    denied(lambda: client.storbinary('STOR sample.mp4', io.BytesIO(b'overwritten')))
                    denied(lambda: client.delete('sample.mp4'))
                    require((archive/'cam-b/sample.mp4').read_bytes() == b'original bytes', 'overwrite/delete altered original')
                    data = io.BytesIO(); client.retrbinary('RETR sample.mp4', data.write)
                    require(data.getvalue() == b'original bytes', 'retrieved bytes differ')
                    client.quit()
                with connect(port) as client:
                    client.login(private.username, password)
                    client.cwd('..'); require(client.pwd() == '/', 'escaped chroot')
                    denied(lambda: client.cwd('/cam-b'))
                    client.mkd('nested'); client.cwd('nested')
                    client.storbinary('STOR private.mp4', io.BytesIO(b'private bytes'))
                    client.quit()
                require((archive/'cam-a/nested/private.mp4').read_bytes() == b'private bytes', 'private upload not isolated')
            print('PASS: real auth, shared/dedicated upload, readback, overwrite/delete denial, chroot', flush=True)
            write_config([private])
            with daemon(config, port, log):
                with connect(port) as client:
                    denied(lambda: client.login(shared.username, password))
                with connect(port) as client:
                    client.login(private.username, password); client.quit()
            print('PASS: real restart and account revocation', flush=True)
        finally:
            if old_policy is None:
                policy.unlink(missing_ok=True)
            else:
                apparmor_ftp._replace(policy, old_policy)
            subprocess.run(['/usr/sbin/apparmor_parser', '-r', '-T', '/etc/apparmor.d/proftpd'], check=True, timeout=60)


if __name__ == '__main__':
    main()
