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
host=nvr.example.com
package=$(pwd -P)
version=$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$package/src" python3 -c 'from sunhill_nvr import __version__; print(__version__)')
logs=$(mktemp -d "$RUNNER_TEMP/sunhill-default-${mode}.XXXXXXXX")
root=/opt/sunhill-nvr
# Do not print the disposable installation's setup token in CI logs.
show_log() { sed '/One-time setup code/c\One-time setup code: [redacted]' "$1" >&2; }
if [[ $mode == resume ]]; then
    # Immutable source snapshot, independent of a rewritten public Git history.
    fixture="$package/tests/fixtures/sunhill-0.2.8-resume.tar.gz"
    printf '%s  %s\n' '243aa331cd8ef6f1058e40b23fefe24a0b522720d92677ab5593a5f382eb5a6d' "$fixture" | sha256sum --check
    tar -xzf "$fixture" -C "$logs"
    # Deterministically interrupt the genuine old installer just before the
    # first controller startup, after config, DB and units are published.
    python3 - "$logs/previous/install.sh" <<'PYFIXTURE'
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text();needle='if ! systemctl start sunhill-controller.service; then'
assert s.count(needle)==1
p.write_text(s.replace(needle, 'echo "CI controlled interruption before first controller start" >&2\nexit 73\n'+needle))
PYFIXTURE
    if (cd "$logs/previous" && sudo bash install.sh nvr.example.com) >"$logs/old-install.log" 2>&1; then
        echo 'Expected controlled interruption was not reproduced.' >&2; exit 1
    fi
    grep -q 'CI controlled interruption before first controller start' "$logs/old-install.log" || { show_log "$logs/old-install.log"; exit 1; }
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
    # Exercise the documented zero-argument installation, including local TLS.
    host=nvr.local
    if ! sudo bash install.sh </dev/null > "$logs/install.log" 2>&1; then show_log "$logs/install.log"; exit 1; fi
fi
[[ $(sudo readlink "$root/current") == "releases/$version" ]]
[[ $(sudo stat -c %a "$root") == 755 ]]
sudo -u sunhill test -w "$root/media/kamery"
sudo -u sunhill test -r "$root/data/sunhill.sqlite"
sudo grep -qx 'proftpd (enforce)' /sys/kernel/security/apparmor/profiles
pid=$(systemctl show -p MainPID --value sunhill-ftp.service)
[[ $pid =~ ^[1-9][0-9]*$ ]]
[[ $(sudo cat "/proc/$pid/attr/apparmor/current") == 'proftpd (enforce)' ]]
sudo env PYTHONPATH="$root/current/app" /usr/bin/python3 -m sunhill_nvr.install_health "$host"
# Check the port 80 vhost even when the Ubuntu welcome site is enabled.
/usr/bin/python3 - "$host" <<'PYCODE'
import sys
from http.client import HTTPConnection
canonical=sys.argv[1]
for host,status in [('127.0.0.1',200),(canonical,301)]:
    conn=HTTPConnection('127.0.0.1',80,timeout=5)
    try:
        conn.request('GET','/?probe=1',headers={'Host':host})
        response=conn.getresponse()
        if response.status!=status or response.getheader('Location')!=(None if status==200 else 'https://'+canonical+'/?probe=1'):
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
if [[ $mode == clean ]]; then
    # Prove setup works from the same installed LAN origin a user can open.
    # Do not print the token, password, session cookie, or response body.
    /usr/bin/python3 - "$logs/install.log" <<'PYSETUP'
import json,re,secrets,sys
from http.client import HTTPConnection
from http.cookies import SimpleCookie
from pathlib import Path
text=Path(sys.argv[1]).read_text()
match=re.search(r'One-time setup code \(valid 24 hours\): ([A-Za-z0-9_-]+)',text)
assert match, 'No setup code produced'
assert 'DONE' in text.splitlines(), 'Installer did not finish'
password=secrets.token_urlsafe(24)
headers={'Host':'127.0.0.1','Origin':'http://127.0.0.1','Content-Type':'application/json'}
def request(path,method='GET',data=None,expected=200):
    conn=HTTPConnection('127.0.0.1',80,timeout=15)
    try:
        conn.request(method,path,body=None if data is None else json.dumps(data),headers=headers)
        response=conn.getresponse(); body=response.read()
        assert response.status==expected, f'{method} {path}: HTTP {response.status}'
        if path=='/api/login':
            cookies=SimpleCookie()
            for name,value in response.getheaders():
                if name.lower()=='set-cookie': cookies.load(value)
            assert 'sunhill_session' in cookies
            headers['Cookie']='; '.join(f'{key}={item.value}' for key,item in cookies.items())
        return json.loads(body)
    finally: conn.close()
assert request('/api/setup')['available'] is True
request('/api/setup','POST',{'token':match.group(1),'username':'ci-admin','password':password},201)
assert request('/api/setup')['available'] is False
request('/api/login','POST',{'username':'ci-admin','password':password})
assert request('/api/me')['role']=='admin'
from datetime import datetime
from zoneinfo import ZoneInfo
assert request('/api/settings/database')['events']==0
assert request('/api/events?date='+datetime.now(ZoneInfo('Europe/Warsaw')).date().isoformat())['events']==[]
print('PASS: initial administrator created and authenticated via LAN')
PYSETUP
    # Test local HTTPS with the CA installed by the default installer.
    sudo curl --fail --silent --show-error --max-time 10 --cacert "$root/config/tls/current/local-ca.pem" --resolve nvr.local:443:127.0.0.1 --noproxy '*' https://nvr.local/ >/dev/null
fi
printf 'PASS: real Ubuntu default-storage %s for %s\n' "$mode" "$version"
