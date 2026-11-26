"""Privileged, crash-recoverable change of the application's public hostname.

The caller holds the shared upgrade manager lock. Backups and the journal are
root-owned; recovery runs before nginx on boot as well as after online errors.
"""
from __future__ import annotations
import json
import os
import secrets
import stat
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from . import config, nginx_config, tls, tls_apply
from .release import ReleaseError, _snapshot
from .storage_system import StorageSystem
from .upgrade_system import SystemAdapter

JOURNAL='domain-transaction.json'
CONFIG_BACKUP='domain-config.backup'
NGINX_BACKUP='domain-nginx.backup'

def _journal(adapter: SystemAdapter) -> dict | None:
    try:data=SystemAdapter._read_trusted(adapter.state/JOURNAL,limit=4096)
    except FileNotFoundError:return None
    try:value=json.loads(data)
    except (ValueError,UnicodeError) as exc:raise ReleaseError('invalid domain journal') from exc
    if not isinstance(value,dict) or set(value)!={'phase','hostname'} or value['phase'] not in ('switching','committed') or not isinstance(value['hostname'],str) or not nginx_config.HOST.fullmatch(value['hostname']):
        raise ReleaseError('invalid domain journal')
    return value

def _save(adapter: SystemAdapter, journal: dict) -> None:
    tls_apply._write_atomic(adapter.state/JOURNAL,json.dumps(journal,separators=(',',':')).encode())

def _clean(adapter: SystemAdapter) -> None:
    (adapter.state/JOURNAL).unlink(missing_ok=True);tls_apply._sync(adapter.state)
    for name in (CONFIG_BACKUP,NGINX_BACKUP):(adapter.state/name).unlink(missing_ok=True)
    tls_apply._sync(adapter.state)

def _restart(adapter: SystemAdapter) -> None:
    adapter._command(['/usr/bin/systemctl','restart','sunhill-web.service','sunhill-live-proxy.service'])
    adapter._command(['/usr/bin/systemctl','reload','nginx.service'])

def _check(adapter: SystemAdapter, hostname: str) -> None:
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request=urllib.request.Request('http://127.0.0.1:8080/',headers={'Host':hostname})
    deadline=time.monotonic()+30
    while True:
        try:
            for unit in ('sunhill-web.service','sunhill-live-proxy.service','nginx.service'):
                adapter._command(['/usr/bin/systemctl','is-active','--quiet',unit])
            with opener.open(request,timeout=5) as response:
                if response.status!=200 or not response.headers.get('Content-Type','').startswith('text/html'):
                    raise ReleaseError('new domain did not pass the proxy health check')
            return
        except (ReleaseError,OSError,urllib.error.URLError) as exc:
            if time.monotonic()>=deadline:raise ReleaseError('domain health check failed') from exc
            time.sleep(1)

def recover(adapter: SystemAdapter, *, online: bool=False) -> bool:
    journal=_journal(adapter)
    if journal is None:return False
    if journal['phase']=='switching':
        previous=SystemAdapter._read_trusted(adapter.state/CONFIG_BACKUP,limit=16*1024)
        proxy=SystemAdapter._read_trusted(adapter.state/NGINX_BACKUP)
        old=config.parse(json.loads(previous))
        StorageSystem(adapter)._replace_config(previous)
        SystemAdapter._replace(adapter.nginx_file,proxy)
        adapter._command(['/usr/sbin/nginx','-t'])
        if online:
            _restart(adapter);_check(adapter,old.hostname)
    _clean(adapter)
    return True

def install(adapter: SystemAdapter, root: Path, hostname: str, *, verify=tls.validate) -> None:
    if _journal(adapter) is not None:raise ReleaseError('pending domain recovery')
    if not isinstance(hostname,str) or not nginx_config.HOST.fullmatch(hostname):raise ValueError('invalid hostname')
    hostname=hostname.lower()
    previous=SystemAdapter._read_trusted(adapter.config_file,limit=16*1024)
    raw=json.loads(previous);old=config.parse(raw)
    if hostname==old.hostname:raise ValueError('hostname is unchanged')
    proxy=SystemAdapter._read_trusted(adapter.nginx_file)
    options={}
    if tls_apply._current(root) is not None:
        cert=root/'current'/'fullchain.pem';key=root/'current'/'key.pem'
        local_ca=root/'current'/'local-ca.pem'
        if local_ca.exists() or local_ca.is_symlink():
            # Only the root console can provision a local CA; an uploaded
            # web certificate never gets to choose its own trust anchor.
            SystemAdapter._read_trusted(local_ca)
            verify(cert,key,hostname,ca_bundle=local_ca)
        else:verify(cert,key,hostname)
        options={'tls_certificate':cert,'tls_key':key}
    raw['hostname']=hostname
    config.parse(raw)
    candidate=(json.dumps(raw,indent=2)+'\n').encode()
    rendered=nginx_config.render(hostname,**options).encode()
    tls_apply._write_atomic(adapter.state/CONFIG_BACKUP,previous)
    tls_apply._write_atomic(adapter.state/NGINX_BACKUP,proxy)
    journal={'phase':'switching','hostname':hostname};_save(adapter,journal)
    try:
        StorageSystem(adapter)._replace_config(candidate)
        SystemAdapter._replace(adapter.nginx_file,rendered)
        adapter._command(['/usr/sbin/nginx','-t'])
        _restart(adapter);_check(adapter,hostname)
        journal['phase']='committed';_save(adapter,journal)
    except Exception as failure:
        current=_journal(adapter)
        if current is not None and current['phase']=='committed':
            # The journal may have been replaced even if its final fsync failed.
            # Leave it for recovery rather than claim the old domain is active.
            raise ReleaseError('domain change committed; recovery must verify cleanup') from failure
        try:recover(adapter,online=True)
        except Exception as recovery:raise ReleaseError('domain change and recovery failed') from recovery
        raise ReleaseError('domain change failed; previous configuration restored') from failure
    _clean(adapter)

def status(adapter: SystemAdapter, state: str, message: str) -> None:
    if state not in ('applying','done','failed','recovery_required'):raise ValueError('invalid domain status')
    data=json.dumps({'state':state,'message':message,'updated':int(time.time())},ensure_ascii=False,separators=(',',':')).encode()
    temp=adapter.state/('.domain-status-'+secrets.token_hex(16))
    try:
        SystemAdapter._write_private(temp,data,mode=0o640)
        os.chown(temp,os.geteuid(),adapter.gid);os.chmod(temp,0o640)
        os.replace(temp,adapter.state/'domain-status.json');tls_apply._sync(adapter.state)
    finally:temp.unlink(missing_ok=True)

def process(adapter: SystemAdapter, inbox: Path, root: Path) -> bool:
    request=inbox/'domain-request.json'
    try:
        info=request.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=adapter.uid or info.st_nlink!=1 or info.st_size>4096:return False
    except FileNotFoundError:
        # The previous process can die after consuming the request but before
        # creating its journal. No change needs recovery in that case.
        try:
            previous=json.loads(SystemAdapter._read_trusted(adapter.state/'domain-status.json',limit=4096))
            if isinstance(previous,dict) and previous.get('state')=='applying':
                status(adapter,'failed','Operacja została przerwana. Sprawdź aktualny adres i w razie potrzeby ponów zmianę.')
                return True
        except (FileNotFoundError,ValueError,UnicodeError):pass
        return False
    identity=(info.st_dev,info.st_ino)
    try:
        with tempfile.TemporaryDirectory(prefix='.domain-input-',dir=adapter.state) as tmp:
            snapshot=Path(tmp)/'request.json';_snapshot(request,snapshot,4096)
            value=json.loads(snapshot.read_bytes())
            if not isinstance(value,dict) or set(value)!={'hostname','user_id'} or type(value['user_id']) is not int or value['user_id']<1 or not isinstance(value['hostname'],str):
                raise ValueError('invalid domain request')
            status(adapter,'applying','Zmienianie nazwy serwera.')
            # Consume this request before mutating the configuration, so a
            # restart after commit cannot submit the same change again.
            current=request.lstat()
            if (current.st_dev,current.st_ino)!=identity:raise ValueError('domain request changed')
            request.unlink();tls_apply._sync(inbox)
            install(adapter,root,value['hostname'])
        status(adapter,'done','Nazwa serwera została zmieniona. Otwórz nowy adres i zaloguj się ponownie.')
    except Exception:
        pending=_journal(adapter) is not None
        status(adapter,'recovery_required' if pending else 'failed',
               'Zmiana domeny przerwana. Wymagane odzyskiwanie.' if pending else 'Zmiana domeny nie powiodła się. Sprawdź nazwę i certyfikat lokalny.')
    finally:
        try:
            current=request.lstat()
            if (current.st_dev,current.st_ino)==identity:request.unlink()
        except FileNotFoundError:pass
    return True
