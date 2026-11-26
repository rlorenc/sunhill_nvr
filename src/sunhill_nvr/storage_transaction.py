"""Crash-recoverable change of the recording root.

Only the privileged coordinator calls this module. Data migration is never
implicit: the selected directory is an existing recording tree. The previous
tree and its files remain untouched.
"""
from __future__ import annotations
import json
import os
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .release import ReleaseError
from .upgrade_transaction import _sync_dir
from .tls_apply import _write_atomic

@dataclass(frozen=True)
class Operations:
    fence: Callable[[],None]
    unfence: Callable[[],None]
    stop: Callable[[],None]
    assert_stopped: Callable[[],None]
    start: Callable[[],None]
    healthy: Callable[[],bool]
    backup: Callable[[Path],None]
    apply: Callable[[Path],None]
    restore: Callable[[Path],None]

def _read(state: Path) -> dict | None:
    file=state/'storage-transaction.json'
    try:
        from .upgrade_system import SystemAdapter
        data=SystemAdapter._read_trusted(file,limit=4096)
    except FileNotFoundError:return None
    try:value=json.loads(data)
    except (ValueError,UnicodeError) as exc:raise ReleaseError('invalid storage journal') from exc
    if (not isinstance(value,dict) or set(value)!={'phase','backup'} or
        value['phase'] not in ('stopping','applying','committed','rolled_back') or
        not isinstance(value['backup'],str) or not re.fullmatch(r'storage-backup-[0-9a-f]{32}',value['backup'])):
        raise ReleaseError('invalid storage journal')
    return value

def _save(state: Path, journal: dict) -> None:
    _write_atomic(state/'storage-transaction.json',json.dumps(journal,separators=(',',':')).encode())

def _cleanup(state: Path, journal: dict) -> None:
    backup=state/journal['backup']
    if backup.is_symlink():raise ReleaseError('invalid storage backup')
    if backup.exists():
        if not backup.is_dir():raise ReleaseError('invalid storage backup')
        shutil.rmtree(backup)
    (state/'storage-transaction.json').unlink(missing_ok=True);_sync_dir(state)

def activate(state: Path, path: Path, ops: Operations) -> None:
    if _read(state) is not None:raise ReleaseError('storage recovery required')
    journal={'phase':'stopping','backup':'storage-backup-'+secrets.token_hex(16)}
    _save(state,journal)
    try:
        ops.fence();ops.stop();ops.assert_stopped()
        ops.backup(state/journal['backup'])
        journal['phase']='applying';_save(state,journal)
        ops.apply(path)
        ops.start()
        if ops.healthy() is not True:raise ReleaseError('services failed after storage change')
        journal['phase']='committed';_save(state,journal)
    except Exception as failure:
        try:recover(state,ops,online=True)
        except Exception as recovery:raise ReleaseError('storage change and recovery failed') from recovery
        raise ReleaseError('storage change failed; previous configuration restored') from failure
    ops.unfence();_cleanup(state,journal)

def recover(state: Path, ops: Operations, *, online: bool=False) -> bool:
    journal=_read(state)
    if journal is None:return False
    ops.fence()
    if online:ops.stop()
    ops.assert_stopped()
    if journal['phase']=='applying':ops.restore(state/journal['backup'])
    if journal['phase'] not in ('committed','rolled_back'):
        journal['phase']='rolled_back';_save(state,journal)
    if online:
        ops.start()
        if ops.healthy() is not True:raise ReleaseError('storage recovery health check failed')
        ops.unfence();_cleanup(state,journal)
    return True

def finalize(state: Path, ops: Operations) -> bool:
    journal=_read(state)
    if journal is None:return False
    if journal['phase'] not in ('committed','rolled_back'):raise ReleaseError('storage recovery not complete')
    # Final cleanup may have been interrupted after removing maintenance.
    # Re-establish it before validating the terminal state, and start services
    # explicitly rather than assuming that their boot jobs have completed.
    ops.fence();ops.start()
    if ops.healthy() is not True:raise ReleaseError('storage services not healthy')
    ops.unfence();_cleanup(state,journal);return True
