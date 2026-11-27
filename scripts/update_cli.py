#!/usr/bin/env python3
"""Root CLI for local release discovery and visible update acceptance checks."""
from __future__ import annotations
import http.client
import hashlib
import io
import tarfile
import tempfile
from pathlib import PurePosixPath
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT=Path('/opt/sunhill-nvr')
VERSION=re.compile(r'[0-9]+\.[0-9]+\.[0-9]+\Z')
PACKAGE=re.compile(r'sunhill-nvr-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz\Z')
LAN=tuple(ipaddress.IPv4Network(x) for x in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8'))


def key(version):
    if not VERSION.fullmatch(version):raise ValueError('Invalid stable version: '+version)
    return tuple(map(int,version.split('.')))


def active_version(root):
    current=root/'current'
    if not current.is_symlink():raise ValueError('No installed release (current is not a symlink)')
    path=current.resolve(strict=True)
    if path.parent!=(root/'releases').resolve(strict=True) or not path.is_dir():raise ValueError('Invalid active release path')
    return path.name


def trusted_package(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode&0o022:
        raise ValueError('Package must be a regular root-owned file, not writable by group/others: '+str(path))
    return path


def trusted_inbox(path):
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
        raise ValueError('Untrusted update inbox: '+str(path))


def read_release_file(source,relative,limit):
    # Read via directory descriptors, rejecting symlinks and mutable source
    # components. The extracted package is explicitly approved by root running
    # this installer; manifest hashes verify consistency, not publisher identity.
    parts=PurePosixPath(relative).parts
    fd=os.open(source,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        trusted=lambda info:info.st_uid==0 and not info.st_mode&0o022
        if not trusted(os.fstat(fd)):raise ValueError('Extracted release directory must be root-owned and not writable by group/others')
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
            if not trusted(os.fstat(fd)):raise ValueError('Untrusted extracted release directory: '+relative)
        item=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        with os.fdopen(item,'rb') as f:
            before=os.fstat(f.fileno())
            if not stat.S_ISREG(before.st_mode) or not trusted(before) or before.st_nlink!=1 or before.st_size>limit:
                raise ValueError('Untrusted or oversized extracted release file: '+relative)
            data=f.read(limit+1);after=os.fstat(f.fileno())
            if len(data)>limit or len(data)!=before.st_size or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                raise ValueError('Extracted release file changed: '+relative)
            return data
    finally:os.close(fd)


def extracted_manifest(source):
    raw=read_release_file(source,'release-manifest.json',1024*1024)
    meta=json.loads(raw)
    if not isinstance(meta,dict) or set(meta)!={'version','files'} or not isinstance(meta['version'],str) or not VERSION.fullmatch(meta['version']) or not isinstance(meta['files'],dict) or not 1<=len(meta['files'])<=4999:
        raise ValueError('Invalid extracted release manifest')
    return raw,meta


def stage_extracted(source,inbox,raw,meta):
    version=meta['version'];prefix='sunhill-nvr-'+version+'/'
    inbox.mkdir(mode=0o700,exist_ok=True);trusted_inbox(inbox)
    destination=inbox/(prefix.rstrip('/')+'.tar.gz')
    total=len(raw);limit=200*1024*1024
    with tempfile.TemporaryDirectory(prefix='.local-update-',dir=inbox) as name:
        temp=Path(name)/'release.tar.gz'
        with tarfile.open(temp,'x:gz') as tar:
            def add(path,data):
                info=tarfile.TarInfo(prefix+path);info.size=len(data);info.mode=0o644;tar.addfile(info,io.BytesIO(data))
            add('release-manifest.json',raw)
            for path,digest in sorted(meta['files'].items()):
                p=PurePosixPath(path)
                if not path.startswith('app/sunhill_nvr/') or str(p)!=path or '..' in p.parts or '\\' in path or not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
                    raise ValueError('Invalid extracted manifest member')
                relative='src/'+path[4:]
                data=read_release_file(source,relative,limit-total);total+=len(data)
                if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Manifest mismatch: '+relative)
                add(relative,data)
        with temp.open('rb') as f:os.fsync(f.fileno())
        os.chmod(temp,0o600)
        os.link(temp,destination) # never replace a previously approved package
    fd=os.open(inbox,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    return trusted_package(destination)


def discover(source,root,installed):
    # Prefer the exact extracted release, then its adjacent original archive.
    # If only the extracted distribution remains, reconstruct a minimal package
    # from the root-approved manifest and its verified application files.
    match=re.fullmatch(r'sunhill-nvr-([0-9]+\.[0-9]+\.[0-9]+)',source.name)
    manifest=None
    if (source/'release-manifest.json').exists() or (source/'release-manifest.json').is_symlink():
        manifest=extracted_manifest(source)
        if match and match[1]!=manifest[1]['version']:raise ValueError('Extracted directory and manifest versions differ')
    version=manifest[1]['version'] if manifest else match[1] if match else None
    inbox=root/'updates'
    if version and key(version)>key(installed):
        filename='sunhill-nvr-'+version+'.tar.gz'
        sibling=source.parent/filename
        if sibling.exists() or sibling.is_symlink():
            try:return trusted_package(sibling)
            except ValueError:
                if not manifest:raise
                print('Adjacent archive is not root-approved; using verified extracted application instead.',flush=True)
        if inbox.exists() or inbox.is_symlink():
            trusted_inbox(inbox)
            exact=inbox/filename
            if exact.exists() or exact.is_symlink():return trusted_package(exact)
        if manifest:return stage_extracted(source,inbox,*manifest)
    options=[]
    if inbox.exists() or inbox.is_symlink():
        trusted_inbox(inbox)
        for path in inbox.iterdir():
            m=PACKAGE.fullmatch(path.name)
            if m and key(m[1])>key(installed):options.append((key(m[1]),path))
    if options:return trusted_package(max(options)[1])
    if version and key(version)>key(installed):
        raise ValueError('Release archive missing; place sunhill-nvr-'+version+'.tar.gz beside the extracted directory or in '+str(inbox))
    return None


def addresses(run=subprocess.run):
    result=run(['/usr/bin/hostname','-I'],check=True,capture_output=True,text=True,timeout=5)
    found=set()
    for value in result.stdout.split():
        try:ip=ipaddress.IPv4Address(value)
        except ValueError:continue
        if not ip.is_loopback and any(ip in net for net in LAN):found.add(ip)
    return ['127.0.0.1',*(str(ip) for ip in sorted(found))]


def head(address,port,host):
    # No proxy environment, no automatic redirect following.
    conn=http.client.HTTPConnection(address,port,timeout=5)
    try:
        conn.request('HEAD','/',headers={'Host':host})
        response=conn.getresponse()
        return response.status,response.getheader('Location'),response.getheader('Content-Type',''),response.getheader('Content-Security-Policy','')
    finally:conn.close()


def access(root,*,probe=head,ips=addresses,emit=print):
    host=json.loads((root/'config/config.json').read_text())['hostname']
    if not re.fullmatch(r'[a-zA-Z0-9.-]+',host):raise ValueError('Invalid configured hostname')
    status,location,mime,_=probe('127.0.0.1',8080,host)
    if status!=200 or location is not None or not mime.startswith('text/html'):
        raise RuntimeError('Domain origin check failed: HTTP '+str(status))
    emit('[OK] Domain origin (Cloudflare): http://127.0.0.1:8080')
    emit('Domain access: https://'+host+' (external HTTPS not tested locally)')
    for ip in ips():
        status,location,mime,csp=probe(ip,80,ip)
        if status!=200 or location is not None or not mime.startswith('text/html') or 'ws://'+ip not in csp.replace(';',' ').split():
            raise RuntimeError('LAN access failed: http://'+ip+' returned HTTP '+str(status)+((' Location: '+location) if location else ''))
        emit('[OK] Local IP access: http://'+ip+' (HTTP 200, no redirect)')
    status,location,_,_=probe('127.0.0.1',80,host)
    if status!=301 or location!='https://'+host+'/':raise RuntimeError('Domain HTTP-to-HTTPS redirect check failed')
    emit('[OK] Domain HTTP redirects to HTTPS')


def media_paths(root):
    config=json.loads((root/'config/config.json').read_text())
    return {key:config[key] for key in ('archive','exports','staging')}


def ensure_analytics_dependencies(*,run=subprocess.run):
    check=['/usr/bin/python3','-c','import cv2,numpy; assert hasattr(cv2, "dnn")']
    packages=['python3-opencv','opencv-data','tesseract-ocr','tesseract-ocr-eng']
    def ready():
        if run(check,capture_output=True).returncode!=0:return False
        try:result=run(['/usr/bin/tesseract','--list-langs'],capture_output=True,text=True)
        except FileNotFoundError:return False
        return result.returncode==0 and 'eng' in result.stdout.splitlines()
    if ready():return
    run(['/usr/bin/apt-get','update'],check=True)
    run(['/usr/bin/apt-get','install','-y',*packages],check=True)
    if not ready():raise RuntimeError('Local analytics dependencies unavailable after installation')

def update(source,root,archive=None,*,run=subprocess.run,check=access,emit=print,repair=False,rtsp_only=False):
    old=active_version(root)
    # New recording-only contract: do not attempt a conversion of old setups.
    contract=source/'src/sunhill_nvr/lifecycle.py'
    if contract.is_file() and 'RTSP_CONTRACT=1' in contract.read_text(encoding='utf-8'):
        installed=root/'control/upgrade/lifecycle.json'
        try:active=json.loads(installed.read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:raise ValueError('A clean installation is required: no compatible lifecycle manifest') from exc
        if not isinstance(active,dict) or active.get('version')!=2:raise ValueError('A clean installation is required: unsupported lifecycle')
        pinned=root/'recovery/app/sunhill_nvr/lifecycle.py'
        if not pinned.is_file() or 'RTSP_CONTRACT=1' not in pinned.read_text(encoding='utf-8'):
            if not (source/'release-manifest.json').is_file():raise ValueError('Use the extracted current release to prepare its recovery manager')
            emit('Preparing the current RTSP recovery manager before update.');repair=True
    before=media_paths(root)
    emit('Sunhill NVR Update');emit('Installed version: '+old)
    automatic=archive is None
    selected=discover(source,root,old) if automatic else trusted_package(Path(archive).absolute())
    if selected is None:
        if (root/'config/maintenance.flag').exists() or any((root/'control/upgrade'/name).exists() for name in ('transaction.json','domain-transaction.json','tls-transaction.json','storage-transaction.json')):
            raise RuntimeError('Pending maintenance or recovery; refusing to report success')
        emit('No newer local release found; remote releases are not checked.')
        check(root);emit('DONE');return 0
    expected=PACKAGE.fullmatch(selected.name)
    if automatic and expected:emit('Available version: '+expected[1])
    emit('Release package: '+str(selected))
    emit('Verifying manifest, backing up configuration/database, activating and checking services...')
    env=dict(os.environ,PYTHONPATH=str(root/'recovery/app'),PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
    # First installation of rollback-aware code must also migrate the pinned
    # manager before activation, otherwise the old manager deletes its backups.
    if not repair and (source/'src/sunhill_nvr/rollback_history.py').is_file() and not (root/'recovery/app/sunhill_nvr/rollback_history.py').is_file():
        if not (source/'release-manifest.json').is_file():raise ValueError('Use the extracted rollback-aware release with --update --repair')
        raw,meta=extracted_manifest(source)
        if not expected or meta['version']!=expected[1]:raise ValueError('First rollback-aware update must match extracted release')
        emit('Preparing rollback-aware recovery manager before first update.');repair=True
    if repair:
        # Explicit approval of the unpacked release. Verify every application
        # member before importing the repair manager from the new distribution.
        raw,meta=extracted_manifest(source)
        if not expected or meta['version']!=expected[1]:raise ValueError('Repair package must match extracted release')
        listed=set()
        with tarfile.open(selected,'r:gz') as package:
            manifest=package.getmember('sunhill-nvr-'+meta['version']+'/release-manifest.json')
            if manifest.size>1024*1024 or json.load(package.extractfile(manifest))!=meta:raise ValueError('Repair archive differs from extracted manifest')
        for member,digest in meta['files'].items():
            relative='src/'+member[4:]
            if not member.startswith('app/sunhill_nvr/') or '..' in PurePosixPath(member).parts:raise ValueError('Invalid repair manifest member')
            if hashlib.sha256(read_release_file(source,relative,200*1024*1024)).hexdigest()!=digest:raise ValueError('Repair manifest mismatch: '+relative)
            listed.add(relative)
        for path in (source/'src/sunhill_nvr').rglob('*'):
            if path.is_symlink() or (path.is_file() and str(path.relative_to(source)) not in listed):raise ValueError('Unexpected repair application member: '+str(path))
        if rtsp_only:
            emit('Checking/installing local analytics dependencies before stopping services...')
            ensure_analytics_dependencies(run=run)
        timer='sunhill-upgrade-apply.timer'
        run(['/usr/bin/systemctl','stop',timer],check=True)
        # Stop the finalization retry loop; do not kill an active update.
        apply_state=run(['/usr/bin/systemctl','show','--property=ActiveState','--value','sunhill-upgrade-apply.service'],capture_output=True,text=True,check=True).stdout.strip()
        if apply_state not in ('inactive','failed'):raise RuntimeError('An update is still running')
        run(['/usr/bin/systemctl','stop','sunhill-upgrade-ready.service'],check=True)
        run(['/usr/bin/systemctl','start','sunhill-upgrade-recover.service'],check=True)
        run(['/usr/bin/systemctl','start','nginx.service'],check=True)
        env=dict(env,PYTHONPATH=str(source/'src'))
        command=['/usr/bin/python3','-m','sunhill_nvr.repair_upgrade',str(selected)]
        if rtsp_only:command.append('--rtsp-only')
        result=run(command,env=env)
        if result.returncode==0:
            run(['/usr/bin/systemctl','reset-failed','sunhill-upgrade-apply.service'],check=False)
            run(['/usr/bin/systemctl','start',timer],check=True)
    else:
        result=run(['/usr/bin/python3','-m','sunhill_nvr.upgrade_admin','apply',str(selected)],env=env)
    if result.returncode:
        emit('Update command failed (exit '+str(result.returncode)+'). Active version: '+active_version(root))
        if (root/'control/upgrade/transaction.json').exists():emit('Recovery required; inspect upgrade journal before retrying.')
        else:emit('No pending upgrade journal; inspect the error above.')
        return result.returncode if result.returncode>0 else 1
    new=active_version(root)
    if new==old or (automatic and expected and new!=expected[1]):raise RuntimeError('Unexpected active version after update: '+new)
    emit('[OK] Update transaction completed: '+old+' -> '+new)
    after=media_paths(root)
    for key in ('archive','exports','staging'):emit('Storage '+key+': '+after[key])
    if before!=after:
        raise RuntimeError('Storage paths changed during the update; review configuration. Version '+new+' is active; no automatic rollback after commit.')
    emit('[OK] Storage paths unchanged')
    try:check(root)
    except Exception:
        emit('Version '+new+' is active, but post-update access checks failed. No automatic rollback after commit.')
        raise
    emit('Update completed successfully.');emit('DONE');return 0


def refresh_recovery(source,root,*,run=subprocess.run,emit=print):
    """Explicit migration of the active release, including extracted-only installs."""
    active=active_version(root)
    raw,meta=extracted_manifest(source)
    if meta['version']!=active:raise ValueError('Recovery package must match active version: '+active)
    if (root/'recovery/app/sunhill_nvr/media_layout.py').is_file():
        emit('[OK] Storage recovery already supports nested directories');emit('DONE');return 0
    filename='sunhill-nvr-'+active+'.tar.gz'
    inbox=root/'updates';sibling=source.parent/filename
    selected=None
    if sibling.exists() or sibling.is_symlink():
        try:selected=trusted_package(sibling)
        except ValueError:pass
    if selected is None:
        if (inbox/filename).exists() or (inbox/filename).is_symlink():
            trusted_inbox(inbox);selected=trusted_package(inbox/filename)
        else:selected=stage_extracted(source,inbox,raw,meta)
    timer='sunhill-upgrade-apply.timer'
    # Stop only the scheduler, not an in-flight transaction. The migration
    # checks quiescence, journals and unit compatibility under its own lock.
    state=run(['/usr/bin/systemctl','show','--property=ActiveState','--value',timer],capture_output=True,text=True,check=True).stdout.strip()
    if state not in ('active','inactive'):raise RuntimeError('Recovery timer is not in a stable state')
    if state=='active':run(['/usr/bin/systemctl','stop',timer],check=True)
    try:
        env=dict(os.environ,PYTHONPATH=str(root/'current/app'),PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
        run(['/usr/bin/python3','-m','sunhill_nvr.recovery_migration',str(selected)],env=env,check=True)
        if not (root/'recovery/app/sunhill_nvr/media_layout.py').is_file():raise RuntimeError('Nested storage recovery was not installed')
    finally:
        if state=='active':run(['/usr/bin/systemctl','start',timer],check=True)
    emit('[OK] Storage recovery supports nested directories');emit('DONE');return 0


def restore_cli(root,args,*,run=subprocess.run,confirm=input):
    env=dict(os.environ,PYTHONPATH=str(root/'recovery/app'),PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
    base=['/usr/bin/python3','-m','sunhill_nvr.upgrade_admin']
    if args==['--rollback-history']:return run(base+['rollback-history'],env=env).returncode
    yes='--yes' in args
    if args.count('--yes')>1:raise ValueError('Duplicate confirmation')
    flags=[x for x in args if x!='--yes']
    if flags==['--rollback']:target=[]
    elif len(flags)==2 and flags[0]=='--downgrade' and VERSION.fullmatch(flags[1]):target=[flags[1]]
    else:raise ValueError('Usage: install.sh --rollback [--yes] | --downgrade VERSION [--yes] | --rollback-history')
    if not yes:
        if not sys.stdin.isatty():raise ValueError('Use --yes to explicitly confirm restoring an older database')
        if confirm('Restore previous database and configuration, losing later changes? Type YES: ')!='YES':return 1
    return run(base+['rollback',*target,'--yes'],env=env).returncode


def main():
    if os.geteuid()!=0:raise PermissionError('Run as root.')
    source=Path(__file__).resolve().parents[1]
    args=sys.argv[1:]
    if args and args[0] in ('--rollback','--downgrade','--rollback-history'):return restore_cli(ROOT,args)
    if args==['--access']:access(ROOT);return 0
    if args==['--refresh-recovery']:return refresh_recovery(source,ROOT)
    if args==['--repair']:return update(source,ROOT,repair=True,emit=lambda message:print(message,flush=True))
    if len(args)>1:raise ValueError('Usage: bash install.sh --update [RELEASE.tar.gz]')
    return update(source,ROOT,args[0] if args else None,emit=lambda message:print(message,flush=True))


if __name__=='__main__':
    try:sys.exit(main())
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr,flush=True);sys.exit(1)
