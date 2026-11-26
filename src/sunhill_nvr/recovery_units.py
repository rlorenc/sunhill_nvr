"""Console-only migration of the stable recovery unit contract.

Ordinary signed updates must never replace their own boot recovery units. This
separate operation runs in persistent rescue boot mode, with all NVR units and
Nginx stopped. It preserves old code and both unit sets before switching either.
No data, database, current release pointer or autostart links are changed.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path

from .release import ReleaseError, VERSION, stage_verified, stage_unsigned_tar
from .upgrade_transaction import _current_version, _lock, _sync_dir, _version_key
from .recovery_migration import _exchange, _trusted_tree
from .upgrade_system import SystemAdapter
from .tls_apply import _write_atomic

JOURNAL = 'recovery-unit-migration'
PROTECTED = frozenset(('sunhill-upgrade-recover.service','sunhill-upgrade-ready.service',
                       'sunhill-upgrade-apply.service','sunhill-upgrade-apply.timer'))
BACKUP = re.compile(r'recovery-previous-[0-9a-f]{32}\Z')


def _offline(adapter) -> None:
    """A reboot must not start the application in the middle of migration."""
    def query(command, codes):
        try:
            result = adapter.run(['/usr/bin/systemctl',command],stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15)
            if result.returncode not in codes:raise ReleaseError('cannot verify rescue mode')
            return result.stdout.decode('ascii').strip()
        except (OSError,UnicodeError,subprocess.SubprocessError) as exc:
            raise ReleaseError('cannot verify rescue mode') from exc
    if query('get-default',{0}) != 'rescue.target' or query('is-system-running',{0,1}) != 'maintenance':
        raise ReleaseError('persistent rescue boot and maintenance mode required')
    for unit in ('nginx.service', *adapter._managed_names()):
        if adapter._unit_state(unit) not in ('inactive','failed'):
            raise ReleaseError('all application and recovery units must be stopped')


def _clean_state(adapter) -> None:
    for name in ('transaction.json','domain-transaction.json','tls-transaction.json','storage-transaction.json'):
        path=adapter.state/name
        if path.exists() or path.is_symlink():raise ReleaseError('pending recovery transaction')
    if adapter.maintenance.exists() or adapter.maintenance.is_symlink():
        raise ReleaseError('maintenance already active')


def _read(adapter) -> dict:
    directory=adapter.state/JOURNAL
    adapter._trusted_backup_directory(directory)
    try:value=json.loads(adapter._read_trusted(directory/'journal.json',limit=4096))
    except (ValueError,UnicodeError) as exc:raise ReleaseError('invalid recovery unit journal') from exc
    if (not isinstance(value,dict) or set(value)!={'phase','active','target','backup','old_inode','new_inode'}
        or value['phase'] not in ('prepared','applied','rolled_back')
        or any(not isinstance(value[k],str) or not VERSION.fullmatch(value[k]) for k in ('active','target'))
        or not isinstance(value['backup'],str) or not BACKUP.fullmatch(value['backup'])
        or any(type(value[k]) is not int or value[k]<=0 for k in ('old_inode','new_inode'))
        or value['old_inode']==value['new_inode']):raise ReleaseError('invalid recovery unit journal')
    return value


def require_ready(adapter) -> None:
    """Fail closed if a newer manager is launched during a partial switch."""
    directory=adapter.state/JOURNAL
    if directory.exists() or directory.is_symlink():
        if _read(adapter)['phase']=='prepared':raise ReleaseError('incomplete offline recovery unit migration')


def _save(adapter, journal: dict) -> None:
    _write_atomic(adapter.state/JOURNAL/'journal.json',json.dumps(journal,sort_keys=True).encode())


def _switch(adapter, root: Path, journal: dict, *, old: bool) -> None:
    directory=adapter.state/JOURNAL
    recovery=root/'recovery';backup=root/journal['backup']
    _trusted_tree(recovery);_trusted_tree(backup)
    if {recovery.stat().st_ino,backup.stat().st_ino}!={journal['old_inode'],journal['new_inode']}:
        raise ReleaseError('recovery trees changed outside migration')
    previous,following={},{}
    for name in sorted(PROTECTED):
        previous[name]=adapter._read_trusted(directory/'old'/name)
        following[name]=adapter._read_trusted(directory/'new'/name)
        if adapter._read_trusted(adapter.system_dir/name) not in (previous[name],following[name]):
            raise ReleaseError('recovery unit changed outside migration')
    desired=previous if old else following
    for name,data in desired.items():adapter._replace(adapter.system_dir/name,data)
    desired_inode=journal['old_inode'] if old else journal['new_inode']
    if recovery.stat().st_ino!=desired_inode:_exchange(recovery,backup)
    _sync_dir(root)
    adapter._command(['/usr/bin/systemctl','daemon-reload'])
    # Never start services or change default boot target here. The operator
    # must run the VM's boot/health checks after the durable switch completes.


def migrate_units(adapter, root: Path, versions: Path, current: Path, public_key: Path,
                  archive: Path, signature: Path | None, *, prepare_next: bool=False) -> Path:
    """Replace contents of the four stable units and the signed recovery tree.

    For a newer release, the operator explicitly prepares its recovery manager
    before applying the ordinary update. Verify backward compatibility on a VM.
    A different recovery unit *set* is deliberately rejected; changing the
    stable boot entry points requires a separate installation migration design.
    """
    fd=_lock(adapter.state)
    try:
        _offline(adapter);_clean_state(adapter)
        directory=adapter.state/JOURNAL
        if directory.exists() or directory.is_symlink():raise ReleaseError('previous recovery unit migration must be archived or rolled back')
        active=_current_version(versions,current)
        recovery=root/'recovery';_trusted_tree(recovery)
        if {n for n in adapter._managed_names() if n.startswith('sunhill-upgrade-')}!=PROTECTED:
            raise ReleaseError('unsupported installed recovery unit set')
        with tempfile.TemporaryDirectory(prefix='.recovery-unit-stage-',dir=root) as name:
            work=Path(name);staged=stage_unsigned_tar(archive,work) if signature is None else stage_verified(archive,signature,public_key,work)
            if staged.name!=active and (not prepare_next or _version_key(staged.name)<=_version_key(active)):
                raise ReleaseError('recovery package must match active version or explicitly prepare a newer version')
            generated=work/'generated';generated.mkdir(mode=0o700)
            command=['/usr/bin/env','-i','PATH=/usr/bin:/bin','PYTHONDONTWRITEBYTECODE=1',f'PYTHONPATH={staged/"app"}',
                     '/usr/bin/python3','-m','sunhill_nvr.deployment_cli','--config',str(adapter.config_file),'--output',str(generated)]
            if adapter.tls_certificate.exists():command.append('--lan-tls')
            adapter._command(command,timeout=60)
            units=generated/'units'
            if {p.name for p in units.iterdir() if p.name.startswith('sunhill-upgrade-')}!=PROTECTED:
                raise ReleaseError('unsupported candidate recovery unit set')
            adapter._command(['/usr/bin/systemd-analyze','verify',*[str(units/n) for n in sorted(PROTECTED)]],timeout=60)
            adapter._command(['/usr/bin/env','-i','PATH=/usr/bin:/bin','PYTHONDONTWRITEBYTECODE=1',f'PYTHONPATH={staged/"app"}',
                              '/usr/bin/python3','-m','sunhill_nvr.upgrade_admin','--help'],timeout=60)
            _trusted_tree(staged,sync=True)
            previous=root/('recovery-previous-'+secrets.token_hex(16))
            # The staging and active tree must be on the same filesystem.
            if recovery.stat().st_dev!=staged.stat().st_dev:raise ReleaseError('recovery trees are not on the same filesystem')
            os.rename(staged,previous);_sync_dir(root)
            # Complete the journal in a private staging directory and publish
            # it atomically before modifying any installed unit or code tree.
            pending=Path(tempfile.mkdtemp(prefix='.recovery-unit-journal-',dir=adapter.state))
            journal={'phase':'prepared','active':active,'target':staged.name,'backup':previous.name,
                     'old_inode':recovery.stat().st_ino,'new_inode':previous.stat().st_ino}
            for side,source in (('old',adapter.system_dir),('new',units)):
                folder=pending/side;folder.mkdir(mode=0o700)
                for unit in sorted(PROTECTED):adapter._write_private(folder/unit,adapter._read_trusted(source/unit))
                _sync_dir(folder)
            adapter._write_private(pending/'journal.json',json.dumps(journal,sort_keys=True).encode())
            _sync_dir(pending);os.rename(pending,directory);_sync_dir(adapter.state)
            try:
                _switch(adapter,root,journal,old=False)
                journal['phase']='applied';_save(adapter,journal)
            except Exception:
                # On ordinary errors restore the old contract immediately;
                # on a crash the rescue boot and durable journal remain.
                journal['phase']='prepared';_save(adapter,journal)
                _switch(adapter,root,journal,old=True)
                journal['phase']='rolled_back';_save(adapter,journal)
                raise
            return previous
    finally:os.close(fd)


def rollback_units(adapter, root: Path, versions: Path, current: Path) -> None:
    fd=_lock(adapter.state)
    try:
        _offline(adapter);_clean_state(adapter)
        journal=_read(adapter)
        if _current_version(versions,current)!=journal['active']:
            raise ReleaseError('active release changed; refusing recovery unit rollback')
        journal['phase']='prepared';_save(adapter,journal)
        _switch(adapter,root,journal,old=True)
        journal['phase']='rolled_back';_save(adapter,journal)
    finally:os.close(fd)


def archive_journal(adapter) -> Path:
    fd=_lock(adapter.state)
    try:
        journal=_read(adapter)
        if journal['phase']=='prepared':raise ReleaseError('incomplete recovery migration must be rolled back')
        target=adapter.state/(JOURNAL+'-'+secrets.token_hex(16))
        os.rename(adapter.state/JOURNAL,target);_sync_dir(adapter.state)
        return target
    finally:os.close(fd)
