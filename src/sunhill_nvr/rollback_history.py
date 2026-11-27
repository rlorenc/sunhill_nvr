"""Root-owned restore points and bounded web requests. No media files are copied."""
from __future__ import annotations
import json, os, re, secrets, stat, time
from pathlib import Path
from .release import VERSION, ReleaseError, _matches_existing
from .upgrade_transaction import _sync_dir, _current_version, _read_journal, activate
from .atomic_publish import publish_new_file
from .upgrade_inbox import _upload_lock, _directory
from dataclasses import replace

INDEX='rollback-history.json'
REQUEST='rollback-request.json'
TOKEN=re.compile(r'[0-9a-f]{32}\Z')
LIMIT=5


def valid(row):
    return (isinstance(row,dict) and set(row)=={'id','version','replaced_by','created'}
            and isinstance(row['id'],str) and TOKEN.fullmatch(row['id'])
            and all(isinstance(row[k],str) and VERSION.fullmatch(row[k]) for k in ('version','replaced_by'))
            and type(row['created']) is int and row['created']>=0)


def read(state: Path):
    """Only the non-secret index is readable by the web service."""
    try:fd=os.open(state/INDEX,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return []
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode&0o022 or info.st_size>16384:raise ReleaseError('invalid rollback index')
        rows=json.loads(os.read(fd,16385))
        if not isinstance(rows,list) or len(rows)>LIMIT or not all(valid(r) for r in rows) or len({r['id'] for r in rows})!=len(rows):raise ReleaseError('invalid rollback index')
        return rows
    finally:os.close(fd)


def _publish(state,rows):
    temp=state/('.rollback-'+secrets.token_hex(16))
    try:
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),0o644);f.write(json.dumps(rows,separators=(',',':')).encode());f.flush();os.fsync(f.fileno())
        os.replace(temp,state/INDEX);_sync_dir(state)
    finally:temp.unlink(missing_ok=True)


def retain(adapter,journal):
    """Keep existing transaction backups in place; commit index before cleanup."""
    import shutil
    from .upgrade_admin import trusted_file
    state=adapter.state
    token=journal['backup'][7:-7]
    if not TOKEN.fullmatch(token) or not journal.get('system_backup'):raise ReleaseError('rollback requires complete system backup')
    system=state/journal['system_backup'];adapter._trusted_backup_directory(system)
    trusted_file(state/journal['backup'])
    if adapter._read_trusted(system/'complete',limit=64)!=b'complete\n':raise ReleaseError('incomplete rollback backup')
    # Store the system backup name in a private, immutable companion file.
    # It is not exposed in the public index.
    pointer=state/('rollback-'+token+'.json')
    payload=json.dumps({'system':system.name},separators=(',',':')).encode()
    if not pointer.exists():
        temp=state/('.rollback-pointer-'+secrets.token_hex(16))
        try:
            adapter._write_private(temp,payload);publish_new_file(temp,pointer)
        finally:temp.unlink(missing_ok=True)
    elif adapter._read_trusted(pointer)!=payload:raise ReleaseError('rollback backup pointer differs')
    rows=read(state)
    if any(r['id']==token for r in rows):return True
    row={'id':token,'version':journal['old'],'replaced_by':journal['new'],'created':int(time.time())}
    all_rows=[row,*rows];_publish(state,all_rows[:LIMIT])
    # Drop only previously published restore points. Never touch media or release code.
    for expired in all_rows[LIMIT:]:
        try:
            _,folder=paths(adapter,expired)
            (state/('backup-'+expired['id']+'.sqlite')).unlink(missing_ok=True)
            shutil.rmtree(folder)
            (state/('rollback-'+expired['id']+'.json')).unlink(missing_ok=True)
            _sync_dir(state)
        except (OSError,ValueError,ReleaseError):pass # orphan retained safely
    return True


def paths(adapter,row):
    from .upgrade_admin import trusted_file
    if not valid(row):raise ReleaseError('invalid rollback point')
    token=row['id'];state=adapter.state;db=state/('backup-'+token+'.sqlite')
    trusted_file(db)
    value=json.loads(adapter._read_trusted(state/('rollback-'+token+'.json'),limit=4096))
    if not isinstance(value,dict) or set(value)!={'system'} or not isinstance(value['system'],str) or not re.fullmatch(r'config-[0-9a-f]{32}',value['system']):raise ReleaseError('invalid rollback pointer')
    folder=state/value['system'];adapter._trusted_backup_directory(folder)
    if adapter._read_trusted(folder/'complete',limit=64)!=b'complete\n':raise ReleaseError('incomplete rollback backup')
    return db,folder


def select(state,current,version=None,token=None):
    if not isinstance(current,str) or not VERSION.fullmatch(current):raise ReleaseError('invalid active version')
    if version is not None and (not isinstance(version,str) or not VERSION.fullmatch(version)):raise ReleaseError('invalid rollback version')
    if token is not None and (not isinstance(token,str) or not TOKEN.fullmatch(token)):raise ReleaseError('invalid rollback point')
    for row in read(state):
        if row['version']!=current and (version is None or row['version']==version) and (token is None or row['id']==token):return row
    raise ReleaseError('no retained restore point for requested version')


def rollback(adapter,versions,current,*,version=None,token=None,expected=None):
    active=_current_version(versions,current)
    if expected is not None and expected!=active:raise ReleaseError('active version changed; refresh rollback list')
    for name in ('transaction.json','domain-transaction.json','tls-transaction.json','storage-transaction.json','recovery-unit-migration'):
        p=adapter.state/name
        if p.exists() or p.is_symlink():raise ReleaseError('pending recovery transaction')
    if adapter.maintenance.exists() or adapter.maintenance.is_symlink():raise ReleaseError('maintenance already active')
    row=select(adapter.state,active,version,token);db,folder=paths(adapter,row)
    candidate=versions/row['version']
    from .recovery_migration import _trusted_tree
    _trusted_tree(candidate)
    manifest=json.loads(adapter._read_trusted(candidate/'manifest.json',limit=1024*1024))
    if manifest.get('version')!=row['version'] or not _matches_existing(candidate,manifest):raise ReleaseError('retained release integrity check failed')
    # Fail closed if the storage/domain configuration changed. Never point an old
    # database at a different archive. TLS mode must also remain unchanged.
    adapter.check_rollback_context(folder)
    ops=adapter.operations()
    ops=replace(ops,backup_system=lambda candidate,dest:adapter.backup_rollback_system(folder,dest),migrate=lambda candidate:adapter.restore_retained(db))
    activate(versions,current,adapter.state,row['version'],ops,allow_restore=True)
    return row['version']


def submit(inbox,state,token,expected,user_id):
    _directory(inbox)
    if type(user_id) is not int or user_id<1:raise ValueError('invalid user')
    select(state,expected,token=token)
    with _upload_lock(inbox):
        for name in (REQUEST,'request.json','approved-update.json','domain-request.json','storage-request.json','tls-request.json'):
            p=inbox/name
            if p.exists() or p.is_symlink():raise FileExistsError('operation pending')
        temp=inbox/('.rollback-'+secrets.token_hex(16))
        try:
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as f:
                f.write(json.dumps({'id':token,'expected':expected,'user_id':user_id}).encode());f.flush();os.fsync(f.fileno())
            publish_new_file(temp,inbox/REQUEST)
        finally:temp.unlink(missing_ok=True)
