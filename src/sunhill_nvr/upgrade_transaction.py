"""Crash-recoverable release activation coordinator.

All paths passed here must be owned and writable only by the administrator.
Service management and SQLite backup/migration are supplied by the privileged
adapter; this module does not expose a privileged interface to web requests.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .release import VERSION, ReleaseError


@dataclass(frozen=True)
class Operations:
    # Fence blocks external writes and FTP uploads while still permitting local
    # health checks. It must remain effective across a crash and be idempotent.
    fence: Callable[[], None]
    unfence: Callable[[], None]
    stop: Callable[[], None]
    assert_stopped: Callable[[], None]
    start: Callable[[], None]
    healthy: Callable[[], bool]
    backup: Callable[[Path], None]
    restore: Callable[[Path], None]
    migrate: Callable[[Path], None]
    backup_system: Callable[[Path,Path], None] | None = None
    apply_system: Callable[[Path], None] | None = None
    restore_system: Callable[[Path], None] | None = None
    restore_system_offline: Callable[[Path], None] | None = None
    finalize_system: Callable[[], None] | None = None
    retain: Callable[[dict], bool] | None = None


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def _write_journal(state: Path, value: dict) -> None:
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    name = '.journal-' + secrets.token_hex(16)
    temp = state / name
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(data);file.flush();os.fsync(file.fileno())
        os.replace(temp, state / 'transaction.json')
        _sync_dir(state)
    finally:temp.unlink(missing_ok=True)


def _link_version(versions: Path, current: Path, version: str) -> None:
    if not VERSION.fullmatch(version):raise ReleaseError('invalid version')
    target = versions / version
    if target.is_symlink() or not target.is_dir():raise ReleaseError('release unavailable')
    pending = current.parent / ('.switch-' + secrets.token_hex(16))
    try:
        os.symlink(os.path.relpath(target, current.parent), pending)
        os.replace(pending, current)
        _sync_dir(current.parent)
    finally:pending.unlink(missing_ok=True)


def _current_version(versions: Path, current: Path) -> str:
    if not current.is_symlink():raise ReleaseError('current release is not a symbolic link')
    value = os.readlink(current)
    if Path(value).is_absolute():raise ReleaseError('invalid current release')
    path = (current.parent / value).resolve(strict=True)
    if path.parent != versions.resolve(strict=True) or not VERSION.fullmatch(path.name) or not path.is_dir():
        raise ReleaseError('current release outside trusted directory')
    return path.name


def _version_key(value: str):
    """Compare release versions; a stable release follows its prereleases."""
    if not VERSION.fullmatch(value):raise ReleaseError('invalid version')
    match=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(.*)',value)
    major,minor,patch=(int(match.group(i)) for i in (1,2,3))
    suffix=match.group(4)
    if not suffix:return (major,minor,patch,1,())
    parts=re.split(r'[-.]',suffix[1:])
    # Numeric prerelease identifiers sort below textual identifiers.
    prerelease=tuple((0,int(part)) if part.isdigit() else (1,part) for part in parts)
    return (major,minor,patch,0,prerelease)


def _read_journal(state: Path) -> dict | None:
    path = state / 'transaction.json'
    if not path.exists() and not path.is_symlink():return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ReleaseError('invalid upgrade journal')
    try:value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:raise ReleaseError('invalid upgrade journal') from exc
    required={'old','new','phase','backup'};optional={'request_id','system_backup'}
    if (not isinstance(value,dict) or not required.issubset(value) or set(value)-required-optional
        or not all(isinstance(value.get(k),str) for k in value)
        or not VERSION.fullmatch(value['old']) or not VERSION.fullmatch(value['new'])
        or value['phase'] not in ('stopping','backed_up','migrating','committed','rolled_back')
        or not re.fullmatch(r'backup-[0-9a-f]{32}\.sqlite',value['backup'])
        or ('request_id' in value and not re.fullmatch(r'[0-9a-f]{32}',value['request_id']))
        or ('system_backup' in value and not re.fullmatch(r'config-[0-9a-f]{32}',value['system_backup']))):
        raise ReleaseError('invalid upgrade journal')
    return value


def _remove_journal(state: Path, backup: Path, system_backup: Path | None=None, *, preserve: bool=False) -> None:
    if not preserve:backup.unlink(missing_ok=True)
    if not preserve and system_backup is not None and (system_backup.exists() or system_backup.is_symlink()):
        if system_backup.is_symlink() or not system_backup.is_dir():raise ReleaseError('invalid system backup')
        shutil.rmtree(system_backup)
    (state / 'transaction.json').unlink(missing_ok=True)
    _sync_dir(state)


def _recover_locked(versions: Path, current: Path, state: Path, ops: Operations) -> bool:
    journal = _read_journal(state)
    if journal is None:return False
    backup = state / journal['backup']
    system_backup=state/journal['system_backup'] if journal.get('system_backup') else None
    if journal['phase'] in ('committed','rolled_back'):
        # A crash after recording the terminal phase can precede service
        # startup (offline recovery) or occur during maintenance cleanup.
        # Never discard the only journal until the active release is healthy.
        _finalize_locked(versions,current,state,ops,journal)
        return True
    ops.fence()
    ops.stop()
    ops.assert_stopped()
    if journal['phase'] in ('backed_up','migrating'):
        if backup.is_symlink() or not backup.is_file():raise ReleaseError('upgrade backup unavailable')
        if system_backup is not None:
            if ops.restore_system is None:raise ReleaseError('system configuration recovery unavailable')
            ops.restore_system(system_backup)
        ops.restore(backup)
    _link_version(versions,current,journal['old'])
    # Persist the completed restore before any old-version writer can start.
    # A failed health check must never cause a second database restore.
    journal['phase']='rolled_back';_write_journal(state,journal)
    ops.start()
    if ops.healthy() is not True:raise ReleaseError('old release failed after recovery')
    ops.unfence()
    _remove_journal(state,backup,system_backup)
    return True


def _lock(state: Path) -> int:
    if state.is_symlink() or not state.is_dir():raise ReleaseError('invalid upgrade state directory')
    fd=os.open(state / 'upgrade.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BaseException:
        os.close(fd);raise
    return fd


def recover(versions: Path, current: Path, state: Path, ops: Operations) -> bool:
    """Recover an interrupted transaction before allowing application services."""
    fd=_lock(state)
    try:return _recover_locked(versions,current,state,ops)
    finally:os.close(fd)


def recover_offline(versions: Path, current: Path, state: Path, ops: Operations) -> bool:
    """Restore an interrupted transaction before application services can start.

    The caller must enforce service ordering and prove no database writers are
    active. Maintenance remains enabled until finalize_offline checks the service.
    """
    fd=_lock(state)
    try:
        journal=_read_journal(state)
        if journal is None:return False
        ops.fence()
        ops.assert_stopped()
        if journal['phase'] in ('committed','rolled_back'):
            expected=journal['new'] if journal['phase']=='committed' else journal['old']
            if _current_version(versions,current)!=expected:raise ReleaseError('release state does not match journal')
            return True
        backup=state/journal['backup']
        if journal['phase'] in ('backed_up','migrating'):
            if backup.is_symlink() or not backup.is_file():raise ReleaseError('upgrade backup unavailable')
            if journal.get('system_backup'):
                if ops.restore_system_offline is None:raise ReleaseError('offline system configuration recovery unavailable')
                ops.restore_system_offline(state/journal['system_backup'])
            ops.restore(backup)
        _link_version(versions,current,journal['old'])
        journal['phase']='rolled_back';_write_journal(state,journal)
        return True
    finally:os.close(fd)


def _finalize_locked(versions: Path, current: Path, state: Path, ops: Operations, journal: dict) -> None:
    if journal['phase'] not in ('committed','rolled_back'):
        raise ReleaseError('offline recovery has not completed')
    expected=journal['new'] if journal['phase']=='committed' else journal['old']
    if _current_version(versions,current)!=expected:raise ReleaseError('recovered release is not active')
    # Re-establish the fence even if a previous process removed it before
    # failing to delete the journal. No writes during recovery validation.
    ops.fence()
    if journal.get('system_backup'):
        if ops.finalize_system is None:raise ReleaseError('system configuration finalization unavailable')
        ops.finalize_system()
    # Units restored during offline recovery may not have boot jobs yet.
    ops.start()
    if ops.healthy() is not True:raise ReleaseError('recovered services are not healthy')
    preserve=journal['phase']=='committed' and ops.retain is not None and ops.retain(journal) is True
    ops.unfence()
    _remove_journal(state,state/journal['backup'],state/journal['system_backup'] if journal.get('system_backup') else None,preserve=preserve)


def finalize_offline(versions: Path, current: Path, state: Path, ops: Operations) -> bool:
    """Release maintenance after recovered services have passed health checks."""
    fd=_lock(state)
    try:
        journal=_read_journal(state)
        if journal is None:return False
        _finalize_locked(versions,current,state,ops,journal)
        return True
    finally:os.close(fd)


def activate(versions: Path, current: Path, state: Path, version: str, ops: Operations, *, request_id: str | None=None, supersede_rollback: bool=False, allow_restore: bool=False) -> None:
    """Activate an already verified release, rolling back on error or interruption."""
    if versions.is_symlink() or not versions.is_dir() or not VERSION.fullmatch(version):
        raise ReleaseError('invalid release location')
    if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch(r'[0-9a-f]{32}',request_id)):
        raise ReleaseError('invalid request identifier')
    has_system=[ops.backup_system is not None,ops.apply_system is not None,ops.restore_system is not None,
                ops.restore_system_offline is not None,ops.finalize_system is not None]
    if any(has_system) and not all(has_system):raise ReleaseError('incomplete system update operations')
    candidate=versions/version
    if candidate.is_symlink() or not candidate.is_dir() or (candidate/'manifest.json').is_symlink() or not (candidate/'manifest.json').is_file():
        raise ReleaseError('release has not been staged')
    fd=_lock(state)
    try:
        prior=_read_journal(state)
        old=_current_version(versions,current)
        if version==old or (not allow_restore and _version_key(version)<=_version_key(old)):raise ReleaseError('release is not newer than the installed version')
        if allow_restore and ops.retain is None:raise ReleaseError('restore requires retained backup support')
        if prior is not None:
            if not supersede_rollback or prior['phase']!='rolled_back' or prior['old']!=old:
                raise ReleaseError('interrupted upgrade requires recovery')
            # An already restored database is the authoritative current state.
            # Do not restore the old backup again, which would undo repairs.
            # Preserve the entire prior transaction and its backup references.
            ops.fence();ops.stop();ops.assert_stopped()
            preserved=state/('superseded-'+secrets.token_hex(16)+'.json')
            os.rename(state/'transaction.json',preserved);_sync_dir(state)
        backup=state/('backup-'+secrets.token_hex(16)+'.sqlite')
        journal={'old':old,'new':version,'phase':'stopping','backup':backup.name}
        system_backup=state/('config-'+secrets.token_hex(16)) if all(has_system) else None
        if system_backup is not None:journal['system_backup']=system_backup.name
        if request_id is not None:journal['request_id']=request_id
        _write_journal(state,journal)
        try:
            ops.fence()
            ops.stop()
            ops.assert_stopped()
            ops.backup(backup)
            if backup.is_symlink() or not backup.is_file():raise ReleaseError('database backup was not created')
            if system_backup is not None:
                ops.backup_system(candidate,system_backup)
                if system_backup.is_symlink() or not (system_backup/'complete').is_file():raise ReleaseError('system backup was not created')
            # Backup data must reach disk before the journal allows migrations.
            with backup.open('rb') as file:os.fsync(file.fileno())
            _sync_dir(state)
            journal['phase']='backed_up';_write_journal(state,journal)
            journal['phase']='migrating';_write_journal(state,journal)
            ops.migrate(candidate)
            if system_backup is not None:ops.apply_system(system_backup)
            _link_version(versions,current,version)
            ops.start()
            if ops.healthy() is not True:raise ReleaseError('new release failed its health check')
            journal['phase']='committed';_write_journal(state,journal)
        except Exception as failure:
            existing=_read_journal(state)
            if existing and existing['phase']=='committed':
                raise ReleaseError('release activated but final cleanup is incomplete') from failure
            try:_recover_locked(versions,current,state,ops)
            except Exception as recovery:
                raise ReleaseError('upgrade failed and recovery remains required') from recovery
            raise ReleaseError('upgrade failed; previous version restored') from failure
        try:
            preserve=ops.retain is not None and ops.retain(journal) is True
            ops.unfence()
            _remove_journal(state,backup,system_backup,preserve=preserve)
        except Exception as failure:raise ReleaseError('release activated but final cleanup is incomplete') from failure
    finally:os.close(fd)
