#!/usr/bin/env bash
# Real legacy 1.0.2 -> RTSP-only migration. Disposable GitHub-hosted VM only.
set -euo pipefail
umask 077
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && -n ${RUNNER_TEMP:-} ]] || exit 1
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 26.04 ]] || exit 1
[[ ! -e /opt/sunhill-nvr && ! -e /etc/sunhill-nvr && ! -e /var/lib/sunhill-nvr ]] || exit 1
repo=$(pwd -P)
work=$(mktemp -d "$RUNNER_TEMP/sunhill-migration.XXXXXXXX")
base=5ce156eb75ddad320035ac7295cf024d3cf707cd
root=/opt/sunhill-nvr
mkdir "$work/old"
git archive "$base" | tar -x -C "$work/old"
if ! (cd "$work/old" && sudo bash install.sh nvr.example.com) >"$work/old.log" 2>&1; then
    sed '/One-time setup code/c\One-time setup code: [redacted]' "$work/old.log" >&2; exit 1
fi
[[ $(sudo readlink "$root/current") == releases/1.0.2 ]]
systemctl is-active --quiet sunhill-ftp.service
# While developing at version 1.0.2, produce a test-only fixture with exactly
# one version-field change. Once version is bumped, use the exact Git tree.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo/scripts:$repo/src" python3 - "$repo" "$work" <<'PY'
from pathlib import Path
from package_release import write_tar
from sunhill_nvr import __version__
import subprocess,sys
repo,work=map(Path,sys.argv[1:]);names=subprocess.check_output(['git','-C',str(repo),'ls-files','-z']).decode().rstrip('\0').split('\0')
files={name:subprocess.check_output(['git','-C',str(repo),'show','HEAD:'+name]) for name in names}
version=__version__
if version=='1.0.2':
    version='1.1.0'
    name='src/sunhill_nvr/__init__.py'
    assert files[name].count(b'__version__ = "1.0.2"')==1
    files[name]=files[name].replace(b'__version__ = "1.0.2"',b'__version__ = "1.1.0"')
    print('CI-only version override; not an actual release')
assert tuple(map(int,version.split('.')))>(1,0,2)
write_tar(work/f'sunhill-nvr-{version}.tar.gz',files,version)
(work/'version').write_text(version)
PY
version=$(cat "$work/version")
sudo chown root:root "$work/sunhill-nvr-$version.tar.gz"
sudo tar -xzf "$work/sunhill-nvr-$version.tar.gz" -C "$work"
# Real RTP stream, keep the synthetic camera alive across migration.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo/tests:$repo/src" python3 - "$work" "$version" <<'PY'
import subprocess,sys,time,secrets
from pathlib import Path
from test_rtsp_integration import Camera
from sunhill_nvr.stream_health import probe
work=Path(sys.argv[1]);version=sys.argv[2];root=Path('/opt/sunhill-nvr')
def sudo(*args,**kw):return subprocess.run(['sudo',*map(str,args)],check=True,**kw)
def db(source,*args):sudo('-u','sunhill','/usr/bin/python3','-c',source,root/'data/sunhill.sqlite',*args)
def wait(predicate, seconds=120):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if predicate():return
        time.sleep(2)
    raise RuntimeError('CI timeout waiting for actual RTSP or recording')
password_hash=subprocess.check_output(['openssl','passwd','-6','-stdin'],input=(secrets.token_urlsafe(24)+'\n').encode()).decode().strip()
cam=Camera(continuous=True)
try:
    db('''import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("INSERT INTO cameras VALUES(1,'ci-migrate','CI camera',1)")
    db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,?,'ftp')",(sys.argv[2],))
    db.execute("INSERT INTO ftp_accounts(id,username,password_hash,home_camera_id) VALUES(1,'ci-migrate',?,1)",(sys.argv[3],))
    db.execute("INSERT INTO camera_ftp_accounts VALUES(1,1)")
    db.execute("INSERT INTO system_settings(key,value) VALUES('ci_migration_sentinel','retain'),('recording_segment_seconds','30') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
''',f'rtsp://127.0.0.1:{cam.port}/cam',password_hash)
    # Reconcile actual old go2rtc configuration before migration preflight.
    wait(lambda:probe('ci-migrate'))
    archive=root/'media/kamery'
    # A valid historical recording is retained byte-for-byte.
    from datetime import datetime,timedelta
    from zoneinfo import ZoneInfo
    stamp=datetime.now(ZoneInfo('Europe/Warsaw'))-timedelta(hours=1)
    old=archive/'ci-migrate'/stamp.strftime('%Y/%m/%d/%H%M%S.mp4')
    sudo('-u','sunhill','mkdir','-p',old.parent)
    sudo('-u','sunhill','ffmpeg','-v','error','-f','lavfi','-i','color=blue:s=64x48:r=10:d=2','-c:v','libx264',old)
    before=sudo('sha256sum',root/'config/config.json',archive/'.sunhill-volume-id',old,capture_output=True,text=True).stdout
    log=work/'migration.log'
    with log.open('w') as out:
        result=subprocess.run(['sudo','bash','install.sh','--update','--rtsp-only'],cwd=work/f'sunhill-nvr-{version}',stdout=out,stderr=subprocess.STDOUT)
    if result.returncode:
        for line in log.read_text().splitlines():
            if 'One-time setup code' not in line:print(line,file=sys.stderr)
        raise RuntimeError('RTSP migration failed')
    assert sudo('readlink',root/'current',capture_output=True,text=True).stdout.strip()==f'releases/{version}'
    assert sudo('sha256sum',root/'config/config.json',archive/'.sunhill-volume-id',old,capture_output=True,text=True).stdout==before
    assert subprocess.run(['systemctl','is-active','--quiet','sunhill-ftp.service']).returncode!=0
    sudo('test','!','-e','/etc/systemd/system/sunhill-ftp.service')
    sudo('test','!','-e','/etc/systemd/system/sunhill-ftp-policy.service')
    db('''import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as db:
    assert db.execute("SELECT record_mode FROM camera_details WHERE camera_id=1").fetchone()==('rtsp',)
    assert db.execute("SELECT value FROM system_settings WHERE key='recording_transport'").fetchone()==('rtsp-only',)
    assert db.execute("SELECT value FROM system_settings WHERE key='ci_migration_sentinel'").fetchone()==('retain',)
    assert db.execute('SELECT COUNT(*) FROM ftp_accounts').fetchone()==(0,)
    assert db.execute('PRAGMA quick_check').fetchone()==('ok',)
''')
    sudo('env',f'PYTHONPATH={root}/current/app','/usr/bin/python3','-m','sunhill_nvr.install_health','nvr.example.com','--rtsp-only')
    def recorded():
        paths=sudo('find',archive/'ci-migrate','-type','f','-name','*.mp4',capture_output=True,text=True).stdout.splitlines()
        return any(Path(p)!=old and subprocess.run(['sudo','ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=codec_name','-of','csv=p=0',p],capture_output=True,text=True).stdout.strip()=='h264' for p in paths)
    wait(recorded,180)
    print('PASS: legacy FTP camera migrated, old MP4 retained, new real RTSP recording published')
    # 1.1.3+: restore the exact pre-migration database/units, then restore
    # the RTSP-only state again; both directions must keep the archive.
    if tuple(map(int,version.split('.'))) >= (1,1,3):
        extracted=work/f'sunhill-nvr-{version}'
        points=sudo('bash','install.sh','--rollback-history',cwd=extracted,capture_output=True,text=True).stdout
        import json
        assert any(r['version']=='1.0.2' for r in json.loads(points))
        sudo('bash','install.sh','--downgrade','1.0.2','--yes',cwd=extracted)
        assert sudo('readlink',root/'current',capture_output=True,text=True).stdout.strip()=='releases/1.0.2'
        assert sudo('sha256sum',root/'config/config.json',archive/'.sunhill-volume-id',old,capture_output=True,text=True).stdout==before
        db("import sqlite3,sys; d=sqlite3.connect(sys.argv[1]); assert d.execute('SELECT record_mode FROM camera_details WHERE camera_id=1').fetchone()==('ftp',); assert d.execute('SELECT COUNT(*) FROM ftp_accounts').fetchone()==(1,)")
        sudo('systemctl','is-active','--quiet','sunhill-ftp.service')
        sudo('bash','install.sh','--rollback','--yes',cwd=extracted)
        assert sudo('readlink',root/'current',capture_output=True,text=True).stdout.strip()==f'releases/{version}'
        assert sudo('sha256sum',root/'config/config.json',archive/'.sunhill-volume-id',old,capture_output=True,text=True).stdout==before
        assert subprocess.run(['systemctl','is-active','--quiet','sunhill-ftp.service']).returncode!=0
        db("import sqlite3,sys; d=sqlite3.connect(sys.argv[1]); assert d.execute('SELECT record_mode FROM camera_details WHERE camera_id=1').fetchone()==('rtsp',); assert d.execute('SELECT COUNT(*) FROM ftp_accounts').fetchone()==(0,)")
        sudo('env',f'PYTHONPATH={root}/current/app','/usr/bin/python3','-m','sunhill_nvr.install_health','nvr.example.com','--rtsp-only')
        print('PASS: retained downgrade and restoration of RTSP-only version')
finally:cam.close()
PY
