"""Privileged, crash-recoverable LAN TLS configuration.

All methods run under the same manager lock as the signed updater. A request
is untrusted input: only validated PEM bytes reach root-owned version files.
"""
from __future__ import annotations
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import time
from pathlib import Path

from . import config, nginx_config, tls, tls_inbox
from .release import ReleaseError, _snapshot
from .upgrade_system import SystemAdapter

VERSION=re.compile(r'versions/[0-9a-f]{32}\Z')

def _sync(path: Path) -> None:
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

def _write_atomic(path: Path, data: bytes, mode: int=0o600) -> None:
    temp=path.parent/('.tls-'+secrets.token_hex(16))
    try:
        SystemAdapter._write_private(temp,data,mode=mode)
        os.chmod(temp,mode);os.replace(temp,path);_sync(path.parent)
    finally:temp.unlink(missing_ok=True)

def _current(root: Path) -> str | None:
    link=root/'current'
    if not link.exists() and not link.is_symlink():return None
    if not link.is_symlink():raise ReleaseError('invalid TLS version pointer')
    value=os.readlink(link)
    if not VERSION.fullmatch(value):raise ReleaseError('invalid TLS version pointer')
    directory=root/value
    if directory.is_symlink() or not directory.is_dir():raise ReleaseError('TLS version unavailable')
    return value

def _set_current(root: Path, value: str | None) -> None:
    current=root/'current'
    if value is None:
        if current.exists() and not current.is_symlink():raise ReleaseError('invalid TLS version pointer')
        current.unlink(missing_ok=True);_sync(root);return
    if not VERSION.fullmatch(value):raise ReleaseError('invalid TLS version pointer')
    directory=root/value
    if directory.is_symlink() or not directory.is_dir():raise ReleaseError('TLS version unavailable')
    temp=root/('.current-'+secrets.token_hex(16))
    try:
        os.symlink(value,temp);os.replace(temp,current);_sync(root)
    finally:temp.unlink(missing_ok=True)

def _read_journal(state: Path) -> dict | None:
    file=state/'tls-transaction.json'
    try:data=SystemAdapter._read_trusted(file,limit=4096)
    except FileNotFoundError:return None
    try:value=json.loads(data)
    except (ValueError,UnicodeError) as exc:raise ReleaseError('invalid TLS journal') from exc
    if (not isinstance(value,dict) or set(value)!={'old','new'} or
        (value['old'] is not None and (not isinstance(value['old'],str) or not VERSION.fullmatch(value['old']))) or
        not isinstance(value['new'],str) or not VERSION.fullmatch(value['new'])):
        raise ReleaseError('invalid TLS journal')
    return value

def recover(adapter: SystemAdapter, root: Path, *, online: bool=False) -> bool:
    """Always roll back an interrupted transaction before nginx can start."""
    journal=_read_journal(adapter.state)
    if journal is None:return False
    previous=SystemAdapter._read_trusted(adapter.state/'tls-nginx.backup')
    _set_current(root,journal['old'])
    SystemAdapter._replace(adapter.nginx_file,previous)
    adapter._command(['/usr/sbin/nginx','-t'])
    if online:adapter._command(['/usr/bin/systemctl','reload','nginx.service'])
    (adapter.state/'tls-transaction.json').unlink();_sync(adapter.state)
    (adapter.state/'tls-nginx.backup').unlink(missing_ok=True);_sync(adapter.state)
    return True

def install(adapter: SystemAdapter, root: Path, certificate: bytes, key: bytes, *, verify=tls.validate, local_ca: bytes | None = None) -> None:
    if _read_journal(adapter.state) is not None:raise ReleaseError('pending TLS recovery')
    if root.is_symlink() or not root.is_dir() or (root/'versions').is_symlink() or not (root/'versions').is_dir():
        raise ReleaseError('invalid TLS directory')
    previous=SystemAdapter._read_trusted(adapter.nginx_file)
    old=_current(root)
    version='versions/'+secrets.token_hex(16);directory=root/version
    directory.mkdir(mode=0o700)
    try:
        SystemAdapter._write_private(directory/'fullchain.pem',certificate,mode=0o644)
        SystemAdapter._write_private(directory/'key.pem',key,mode=0o600)
        host=config.load(adapter.config_file).hostname
        if local_ca is None:
            verify(directory/'fullchain.pem',directory/'key.pem',host)
        else:
            # Only root-console generation uses this path, never the web inbox.
            SystemAdapter._write_private(directory/'local-ca.pem',local_ca,mode=0o644)
            tls.validate(directory/'fullchain.pem',directory/'key.pem',host,ca_bundle=directory/'local-ca.pem')
        _sync(directory);_sync(root/'versions')
        candidate=nginx_config.render(host,tls_certificate=root/'current'/'fullchain.pem',tls_key=root/'current'/'key.pem').encode()
        _write_atomic(adapter.state/'tls-nginx.backup',previous)
        _write_atomic(adapter.state/'tls-transaction.json',json.dumps({'old':old,'new':version},separators=(',',':')).encode())
        try:
            _set_current(root,version)
            SystemAdapter._replace(adapter.nginx_file,candidate)
            adapter._command(['/usr/sbin/nginx','-t'])
            adapter._command(['/usr/bin/systemctl','reload','nginx.service'])
        except Exception:
            recover(adapter,root,online=True)
            raise
        (adapter.state/'tls-transaction.json').unlink();_sync(adapter.state)
        (adapter.state/'tls-nginx.backup').unlink(missing_ok=True);_sync(adapter.state)
    except BaseException:
        # Do not delete a version that crash recovery may still need.
        if _read_journal(adapter.state) is None and _current(root)!=version:
            shutil.rmtree(directory,ignore_errors=True)
        raise

def status(adapter: SystemAdapter, state: str, message: str) -> None:
    if state not in ('applying','done','failed','recovery_required'):raise ValueError('invalid TLS status')
    data=json.dumps({'state':state,'message':message,'updated':int(time.time())},ensure_ascii=False,separators=(',',':')).encode()
    path=adapter.state/'tls-status.json';temp=adapter.state/('.tls-status-'+secrets.token_hex(16))
    try:
        SystemAdapter._write_private(temp,data,mode=0o640)
        os.chown(temp,0,adapter.gid);os.chmod(temp,0o640);os.replace(temp,path);_sync(adapter.state)
    finally:temp.unlink(missing_ok=True)

def process(adapter: SystemAdapter, inbox: Path, root: Path) -> bool:
    request=inbox/'tls-request.json'
    try:
        info=request.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=adapter.uid or info.st_nlink!=1 or info.st_size>tls_inbox.LIMIT:
            return False
    except FileNotFoundError:return False
    identity=(info.st_dev,info.st_ino)
    try:
        status(adapter,'applying','Sprawdzanie i instalowanie certyfikatu.')
        with tempfile.TemporaryDirectory(prefix='.tls-input-',dir=adapter.state) as tmp:
            snapshot=Path(tmp)/'request.json';_snapshot(request,snapshot,tls_inbox.LIMIT)
            data=json.loads(snapshot.read_bytes())
            if (not isinstance(data,dict) or set(data)!={'certificate','key','user_id'} or type(data['user_id']) is not int
                or data['user_id']<1 or not isinstance(data['certificate'],str) or not isinstance(data['key'],str)):
                raise ValueError('invalid TLS request')
            install(adapter,root,data['certificate'].encode('ascii'),data['key'].encode('ascii'))
        status(adapter,'done','Certyfikat został zainstalowany.')
    except Exception:
        pending=_read_journal(adapter.state) is not None
        status(adapter,'recovery_required' if pending else 'failed',
               'Nie udało się przywrócić konfiguracji TLS.' if pending else 'Nie udało się zainstalować certyfikatu. Sprawdź nazwę, ważność, zaufanie i klucz.')
    finally:
        try:
            current=request.lstat()
            if (current.st_dev,current.st_ino)==identity:request.unlink()
        except FileNotFoundError:pass
    return True
