#!/usr/bin/env bash
# Real default-storage installation and interrupted 0.2.8 repair. Disposable VM only.
set -euo pipefail
umask 077
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && -n ${RUNNER_TEMP:-} ]] || { echo 'Disposable GitHub-hosted VM required.' >&2; exit 1; }
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 26.04 ]] || { echo 'Ubuntu 26.04 required.' >&2; exit 1; }
[[ $# == 1 && ( $1 == clean || $1 == resume ) ]] || { echo 'Use clean or resume.' >&2; exit 1; }
[[ ! -e /opt/sunhill-nvr && ! -e /etc/sunhill-nvr && ! -e /var/lib/sunhill-nvr ]] || { echo 'Existing Sunhill installation detected.' >&2; exit 1; }
mode=$1
package=$(pwd -P)
version=$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$package/src" python3 -c 'from sunhill_nvr import __version__; print(__version__)')
logs=$(mktemp -d "$RUNNER_TEMP/sunhill-default-${mode}.XXXXXXXX")
root=/opt/sunhill-nvr
# Do not print the disposable installation's setup token in CI logs.
show_log() { sed '/One-time setup code/c\One-time setup code: [redacted]' "$1" >&2; }
if [[ $mode == resume ]]; then
    git clone --quiet "$package/sunhill-nvr-history.bundle" "$logs/previous"
    git -C "$logs/previous" checkout --quiet 28d6376
    if (cd "$logs/previous" && sudo bash install.sh nvr.example.com) >"$logs/old-install.log" 2>&1; then
        echo 'Expected 0.2.8 FTP failure was not reproduced.' >&2; exit 1
    fi
    grep -q 'Service failed readiness check: sunhill-ftp.service' "$logs/old-install.log" || { show_log "$logs/old-install.log"; exit 1; }
    [[ $(sudo readlink "$root/current") == releases/0.2.8 ]]
    # Add a unique test-only DB sentinel under the real service identity. The
    # resume must retain the existing database and marker, not reinitialize it.
    sudo -u sunhill /usr/bin/python3 - "$root/data/sunhill.sqlite" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute('INSERT INTO system_settings(key,value) VALUES(?,?)',('ci_resume_sentinel','preserve-existing-database'))
PY
    sudo sha256sum "$root/config/config.json" "$root/media/kamery/.sunhill-volume-id" > "$logs/before.sha256"
    if ! sudo bash install.sh --resume > "$logs/install.log" 2>&1; then show_log "$logs/install.log"; exit 1; fi
    sudo sha256sum --check "$logs/before.sha256"
    sudo -u sunhill /usr/bin/python3 - "$root/data/sunhill.sqlite" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as db:
    if db.execute("SELECT value FROM system_settings WHERE key='ci_resume_sentinel'").fetchone() != ('preserve-existing-database',):
        raise SystemExit('Resume lost the existing DB sentinel')
    if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
        raise SystemExit('Resume left a corrupted database')
PY
else
    if ! sudo bash install.sh nvr.example.com > "$logs/install.log" 2>&1; then show_log "$logs/install.log"; exit 1; fi
fi
[[ $(sudo readlink "$root/current") == "releases/$version" ]]
[[ $(sudo stat -c %a "$root") == 755 ]]
sudo -u sunhill test -w "$root/media/kamery"
sudo -u sunhill test -r "$root/data/sunhill.sqlite"
sudo grep -qx 'proftpd (enforce)' /sys/kernel/security/apparmor/profiles
pid=$(systemctl show -p MainPID --value sunhill-ftp.service)
[[ $pid =~ ^[1-9][0-9]*$ ]]
[[ $(sudo cat "/proc/$pid/attr/apparmor/current") == 'proftpd (enforce)' ]]
sudo env PYTHONPATH="$root/current/app" /usr/bin/python3 -m sunhill_nvr.install_health nvr.example.com
# Check the port 80 vhost even when the Ubuntu welcome site is enabled.
/usr/bin/python3 - <<'PYCODE'
from http.client import HTTPConnection
for host,status in [('127.0.0.1',200),('nvr.example.com',301)]:
    conn=HTTPConnection('127.0.0.1',80,timeout=5)
    try:
        conn.request('GET','/?probe=1',headers={'Host':host})
        response=conn.getresponse()
        if response.status!=status or response.getheader('Location')!=(None if status==200 else 'https://nvr.example.com/?probe=1'):
            raise SystemExit('Sunhill HTTP vhost failed: '+host)
        response.read()
    finally:conn.close()
PYCODE
/usr/bin/python3 - <<'PY'
import ftplib
with ftplib.FTP() as ftp:
    if not ftp.connect('127.0.0.1',21,timeout=5).startswith('220'):
        raise SystemExit('Real FTP greeting missing')
    try:ftp.login('nonexistent-ci-account','not-a-valid-password')
    except ftplib.error_perm:pass
    else:raise SystemExit('Unknown FTP account accepted')
PY
printf 'PASS: real Ubuntu default-storage %s for %s\n' "$mode" "$version"
