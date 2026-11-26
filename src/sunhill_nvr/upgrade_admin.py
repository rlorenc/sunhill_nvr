"""Root-only, fixed-path update and boot recovery commands.

Unsigned tar.gz releases require root approval. Legacy signed packages remain
compatible when a public key has explicitly been provisioned.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import pwd
import secrets
import stat
import time
import tempfile
from pathlib import Path

from .release import ReleaseError, stage_verified, stage_unsigned_tar, _snapshot
from .upgrade_system import SystemAdapter
from .upgrade_transaction import activate, finalize_offline, recover, recover_offline, _current_version, _read_journal
from . import approved_update, upgrade_inbox, tls_apply, storage_admin, storage_transaction, domain_change, tls, config
from .storage_system import StorageSystem


ROOT=Path('/opt/sunhill-nvr')
VERSIONS=ROOT/'releases'
CURRENT=ROOT/'current'
STATE=Path('/opt/sunhill-nvr/control/upgrade')
CONFIG=Path('/opt/sunhill-nvr/config/config.json')
PUBLIC_KEY=Path('/opt/sunhill-nvr/config/release-public.pem')
MAINTENANCE=Path('/opt/sunhill-nvr/config/maintenance.flag')
INBOX=Path('/opt/sunhill-nvr/data/upgrade-inbox')
TLS_ROOT=Path('/opt/sunhill-nvr/config/tls')

_ERROR_MESSAGES={
    'release signature is invalid':'Podpis aktualizacji jest nieprawidłowy. Wersja nie została zainstalowana.',
    'signature verification unavailable':'Nie można sprawdzić podpisu aktualizacji.',
    'release is not newer than the installed version':'Wersja aktualizacji nie jest nowsza od zainstalowanej.',
    'update changes recovery units; manual migration required':'Aktualizacja wymaga zmiany mechanizmu odzyskiwania i osobnej migracji.',
    'update changes recovery unit list; manual migration required':'Aktualizacja wymaga zmiany mechanizmu odzyskiwania i osobnej migracji.',
    'update changes managed unit list without lifecycle manifest':'Paczka nie zawiera opisu usług potrzebnego do aktualizacji.',
    'new unit conflicts with existing configuration':'Nowa usługa koliduje z istniejącą konfiguracją systemu.',
    'new unit lacks boot recovery dependency':'Nowa konfiguracja usług nie zapewnia odzyskiwania po restarcie.',
    'unsupported service enablement state':'Nieobsługiwany stan autostartu usługi. Aktualizacja została zatrzymana.',
    'new release failed its health check':'Nowa wersja nie przeszła kontroli usług. Przywrócono poprzednią wersję.',
}

def failure_message(error: Exception) -> str:
    """Return only fixed, public messages; never expose exception values."""
    seen=set();current=error
    while current is not None and id(current) not in seen and len(seen)<8:
        seen.add(id(current))
        if type(current) is ReleaseError and str(current) in _ERROR_MESSAGES:
            return _ERROR_MESSAGES[str(current)]
        current=current.__cause__
    return 'Aktualizacja nie powiodła się.'


def trusted_directory(directory: Path) -> None:
    """Reject mutable or symlinked directories on a privileged path."""
    if not directory.is_absolute():raise ReleaseError('directory must be absolute')
    current=Path('/')
    for component in directory.parts[1:]:
        current=current/component
        info=current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise ReleaseError('upgrade directory is not trusted')


def trusted_file(file: Path) -> None:
    info=file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode&0o022:
        raise ReleaseError('upgrade file is not trusted')


def context() -> SystemAdapter:
    if os.geteuid()!=0:raise PermissionError('update operation requires root')
    for directory in (ROOT,VERSIONS,STATE,CONFIG.parent,TLS_ROOT,TLS_ROOT/'versions',Path('/etc/systemd/system'),Path('/etc/nginx/conf.d')):trusted_directory(directory)
    trusted_file(CONFIG)
    account=pwd.getpwnam('sunhill')
    if account.pw_uid<1000 or account.pw_gid<1000:raise ReleaseError('unsafe service account')
    return SystemAdapter(CONFIG,CURRENT,STATE,MAINTENANCE,account.pw_uid,account.pw_gid)


def _status(adapter: SystemAdapter,state: str,message: str) -> None:
    if state not in ('verifying','applying','done','failed','recovery_required'):
        raise ValueError('invalid update status')
    data=json.dumps({'state':state,'message':message,'updated':int(time.time())},ensure_ascii=False,separators=(',',':')).encode()
    temp=STATE/('.status-'+secrets.token_hex(16))
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o640)
    try:
        os.fchown(fd,0,adapter.gid);os.fchmod(fd,0o640)
        with os.fdopen(fd,'wb') as output:output.write(data);output.flush();os.fsync(output.fileno())
        os.replace(temp,STATE/'status.json')
        directory=os.open(STATE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:temp.unlink(missing_ok=True)


def _discard_request(expected_id: str, identity=None) -> None:
    """Remove only the still-matching request and its two inbox files."""
    item=upgrade_inbox.read_request(INBOX,with_identity=True)
    if item is None:return
    request,found_identity=item
    if request['archive']!=expected_id or (identity is not None and found_identity!=identity):return
    directory=os.open(INBOX,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        current=os.stat('request.json',dir_fd=directory,follow_symlinks=False)
        if (current.st_dev,current.st_ino)!=found_identity:return
        os.unlink('request.json',dir_fd=directory)
        for kind in ('archive','signature'):
            name=upgrade_inbox.filename(kind,request[kind])
            try:os.unlink(name,dir_fd=directory)
            except FileNotFoundError:pass
    finally:os.close(directory)


def process_approved(adapter: SystemAdapter) -> bool:
    path=INBOX/approved_update.REQUEST
    try:info=path.lstat()
    except FileNotFoundError:return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=adapter.uid or info.st_nlink!=1 or info.st_size>4096:raise ReleaseError('invalid approved update request')
    identity=info.st_dev,info.st_ino
    try:
        with tempfile.TemporaryDirectory(prefix='.approved-request-',dir=STATE) as directory:
            copy=Path(directory)/'request';_snapshot(path,copy,4096)
            value=json.loads(copy.read_bytes())
        if not isinstance(value,dict) or set(value)!={'package','user_id'} or not approved_update.valid(value['package']) or type(value['user_id']) is not int or value['user_id']<1:raise ReleaseError('invalid approved request')
        trusted_directory(approved_update.ROOT)
        package=approved_update.ROOT/value['package'];trusted_file(package)
        _status(adapter,'verifying','Sprawdzanie integralności zatwierdzonej paczki.')
        staged=stage_unsigned_tar(package,VERSIONS,reuse_existing=True)
        if package.name!=f'sunhill-nvr-{staged.name}.tar.gz':raise ReleaseError('package filename and version differ')
        if _current_version(VERSIONS,CURRENT)!=staged.name:
            _status(adapter,'applying','Instalowanie aktualizacji.')
            activate(VERSIONS,CURRENT,STATE,staged.name,adapter.operations())
        _status(adapter,'done','Aktualizacja zakończona.')
    except Exception as error:
        logging.error('Approved update failed: %s',type(error).__name__)
        _status(adapter,'recovery_required' if (STATE/'transaction.json').exists() else 'failed',failure_message(error))
    finally:
        try:
            now=path.lstat()
            if (now.st_dev,now.st_ino)==identity:path.unlink()
        except FileNotFoundError:pass
    return True


def process_inbox(adapter: SystemAdapter) -> bool:
    """Process one untrusted request. Called under the exclusive manager lock."""
    journal=_read_journal(STATE)
    if journal is not None:
        try:
            if journal['phase'] in ('committed','rolled_back'):
                finalize_offline(VERSIONS,CURRENT,STATE,adapter.operations())
            else:recover(VERSIONS,CURRENT,STATE,adapter.operations())
            if journal.get('request_id'):_discard_request(journal['request_id'])
            _status(adapter,'done' if journal['phase']=='committed' else 'failed',
                    'Aktualizacja zakończona.' if journal['phase']=='committed' else 'Poprzednia wersja została przywrócona po przerwaniu aktualizacji.')
        except Exception as error:
            logging.error('Upgrade recovery failed: %s',type(error).__name__)
            _status(adapter,'recovery_required','Odzyskiwanie aktualizacji nie powiodło się; dostęp pozostaje zablokowany.')
        return True
    info=INBOX.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=adapter.uid or info.st_mode&0o077:
        raise ReleaseError('update inbox is not private')
    if tls_apply.recover(adapter,TLS_ROOT,online=True):
        tls_apply.status(adapter,'failed','Przywrócono poprzednią konfigurację po przerwaniu zmiany certyfikatu.')
        return True
    domain_journal=domain_change._journal(adapter)
    if domain_journal and domain_change.recover(adapter,online=True):
        domain_change.status(adapter,'done' if domain_journal['phase']=='committed' else 'failed',
                             'Zmiana domeny została zakończona.' if domain_journal['phase']=='committed' else 'Przywrócono poprzednią domenę po przerwaniu zmiany.')
        return True
    storage_journal=storage_transaction._read(STATE)
    if storage_journal and storage_transaction.recover(STATE,StorageSystem(adapter).operations(),online=True):
        storage_admin.status(adapter,'done' if storage_journal['phase']=='committed' else 'failed',
                             'Zmiana katalogu została zakończona.' if storage_journal['phase']=='committed' else 'Przywrócono konfigurację po przerwaniu zmiany katalogu nagrań.')
        return True
    if storage_admin.process(adapter,INBOX):return True
    if tls_apply.process(adapter,INBOX,TLS_ROOT):return True
    if domain_change.process(adapter,INBOX,TLS_ROOT):return True
    if process_approved(adapter):return True
    item=upgrade_inbox.read_request(INBOX,with_identity=True)
    if item is None:return False
    request,request_identity=item
    try:
        _status(adapter,'verifying','Sprawdzanie podpisu aktualizacji.')
        trusted_file(PUBLIC_KEY)
        archive=INBOX/upgrade_inbox.filename('archive',request['archive'])
        signature=INBOX/upgrade_inbox.filename('signature',request['signature'])
        staged=stage_verified(archive,signature,PUBLIC_KEY,VERSIONS,reuse_existing=True)
        if _current_version(VERSIONS,CURRENT)!=staged.name:
            _status(adapter,'applying','Instalowanie aktualizacji.')
            activate(VERSIONS,CURRENT,STATE,staged.name,adapter.operations(),request_id=request['archive'])
        _status(adapter,'done','Aktualizacja zakończona.')
    except Exception as error:
        logging.error('Upgrade failed: %s',type(error).__name__)
        needs_recovery=(STATE/'transaction.json').exists()
        _status(adapter,'recovery_required' if needs_recovery else 'failed',
                'Aktualizacja przerwana. Wymagane odzyskanie.' if needs_recovery else failure_message(error))
    finally:
        # Do not unlink a request replaced concurrently by another submission.
        try:_discard_request(request['archive'],request_identity)
        except FileNotFoundError:pass
    return True


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(prog='sunhill-upgrade')
    sub=parser.add_subparsers(dest='command',required=True)
    apply=sub.add_parser('apply');apply.add_argument('archive',type=Path);apply.add_argument('signature',type=Path,nargs='?')
    sub.add_parser('recover-offline');sub.add_parser('finalize');sub.add_parser('process-inbox')
    lan=sub.add_parser('generate-lan-tls',help='Generate a private LAN CA and HTTPS certificate; clients must trust the CA')
    lan.add_argument('--next-hostname',help='Include a second DNS name before a planned domain change')
    args=parser.parse_args(argv);adapter=context();ops=adapter.operations()
    # The manager lock also serializes verification/staging with recovery.
    fd=os.open(STATE/'manager.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        from .recovery_units import require_ready
        require_ready(adapter)
        if args.command=='recover-offline':
            domain_journal=domain_change._journal(adapter)
            if domain_journal and domain_change.recover(adapter):
                domain_change.status(adapter,'done' if domain_journal['phase']=='committed' else 'failed',
                                     'Zmiana domeny została zakończona.' if domain_journal['phase']=='committed' else 'Przywrócono poprzednią domenę po przerwaniu zmiany.')
            if tls_apply.recover(adapter,TLS_ROOT):
                tls_apply.status(adapter,'failed','Przywrócono poprzednią konfigurację po przerwaniu zmiany certyfikatu.')
            storage_journal=storage_transaction._read(STATE)
            if storage_journal and storage_transaction.recover(STATE,StorageSystem(adapter).operations()):
                storage_admin.status(adapter,'applying','Odzyskiwanie po przerwanej zmianie katalogu nagrań.')
            recover_offline(VERSIONS,CURRENT,STATE,ops)
        elif args.command=='finalize':
            storage_journal=storage_transaction._read(STATE)
            if storage_journal and storage_transaction.finalize(STATE,StorageSystem(adapter).operations()):
                storage_admin.status(adapter,'done' if storage_journal['phase']=='committed' else 'failed',
                                     'Zmiana katalogu została zakończona.' if storage_journal['phase']=='committed' else 'Przywrócono poprzedni katalog nagrań.')
            journal=_read_journal(STATE)
            if finalize_offline(VERSIONS,CURRENT,STATE,ops):
                if journal.get('request_id'):_discard_request(journal['request_id'])
                _status(adapter,'done' if journal['phase']=='committed' else 'failed',
                        'Aktualizacja zakończona.' if journal['phase']=='committed' else 'Poprzednia wersja została przywrócona po przerwaniu aktualizacji.')
        elif args.command=='process-inbox':process_inbox(adapter)
        elif args.command=='generate-lan-tls':
            for name in ('transaction.json','domain-transaction.json','tls-transaction.json','storage-transaction.json'):
                if (STATE/name).exists() or (STATE/name).is_symlink():raise ReleaseError('pending recovery transaction')
            if MAINTENANCE.exists() or MAINTENANCE.is_symlink():raise ReleaseError('maintenance already active')
            with tempfile.TemporaryDirectory(prefix='.local-tls-',dir=STATE) as directory:
                cert,key,ca=tls.generate_local(config.load(CONFIG).hostname,Path(directory),next_hostname=args.next_hostname)
                tls_apply.install(adapter,TLS_ROOT,cert.read_bytes(),key.read_bytes(),local_ca=ca.read_bytes())
            print('LAN CA: '+str(TLS_ROOT/'current'/'local-ca.pem')+' (install this public CA on clients via a trusted channel)')
        else:
            for name in ('transaction.json','domain-transaction.json','tls-transaction.json','storage-transaction.json'):
                if (STATE/name).exists() or (STATE/name).is_symlink():raise ReleaseError('pending recovery transaction')
            if MAINTENANCE.exists() or MAINTENANCE.is_symlink():raise ReleaseError('maintenance already active')
            if args.signature is None:
                trusted_file(args.archive)
                staged=stage_unsigned_tar(args.archive,VERSIONS,reuse_existing=True)
            else:
                trusted_file(PUBLIC_KEY)
                staged=stage_verified(args.archive,args.signature,PUBLIC_KEY,VERSIONS)
            activate(VERSIONS,CURRENT,STATE,staged.name,ops)
    finally:os.close(fd)
    return 0


if __name__=='__main__':raise SystemExit(main())
