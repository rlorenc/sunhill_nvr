"""Administrator-only replacement of the pinned recovery code.

The default command never changes the boot unit contract. A signed, compatible recovery tree
is prepared and synced before one atomic Linux directory exchange. The old
complete tree remains available for operator recovery, including after a crash.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import os
import secrets
import stat
import tempfile
from pathlib import Path

from .release import ReleaseError, stage_verified, stage_unsigned_tar
from .upgrade_transaction import _current_version, _lock, _sync_dir, _read_journal, _version_key


def _exchange(left: Path, right: Path) -> None:
    """Require atomic exchange; never fall back to a two-rename sequence."""
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as exc:
        raise ReleaseError('atomic recovery exchange unavailable') from exc
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(left), -100, os.fsencode(right), 2) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _trusted_tree(root: Path, *, sync: bool = False) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ReleaseError('invalid recovery tree')
    for directory, dirs, files in os.walk(root, topdown=False, followlinks=False):
        base = Path(directory)
        for name in dirs + files:
            child = base / name
            info = child.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022 or not (
                stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
            ):
                raise ReleaseError('untrusted recovery tree')
            if sync and stat.S_ISREG(info.st_mode):
                fd = os.open(child, os.O_RDONLY | os.O_NOFOLLOW)
                try: os.fsync(fd)
                finally: os.close(fd)
        if sync: _sync_dir(base)
    info = root.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ReleaseError('untrusted recovery tree')


def migrate(adapter, root: Path, versions: Path, current: Path, public_key: Path,
            archive: Path, signature: Path | None, *, prepare_next: bool=False, repair: bool=False) -> Path:
    """Called with the manager lock held. No pending transaction is allowed.

    Use the signed release matching the active version. Unit changes require
    their own offline migration; they are not silently applied by this command.
    """
    recovery = root / 'recovery'
    fd = _lock(adapter.state)
    try:
        active = _current_version(versions, current)
        prior=_read_journal(adapter.state) if repair else None
        for name in ('transaction.json', 'domain-transaction.json', 'tls-transaction.json', 'storage-transaction.json', 'recovery-unit-migration'):
            path = adapter.state / name
            if path.exists() or path.is_symlink():
                if not (name=='transaction.json' and repair and prior and prior['phase']=='rolled_back' and prior['old']==active):
                    raise ReleaseError('pending recovery transaction')
        if adapter.maintenance.exists() or adapter.maintenance.is_symlink():
            if not repair:raise ReleaseError('maintenance already active')
            adapter.fence()
        # Stop the timer from the console before calling this command. The
        # manager lock serializes transactions, but a fresh Python process can
        # import the pinned tree before attempting that lock.
        for unit in ('sunhill-upgrade-apply.timer','sunhill-upgrade-apply.service','sunhill-upgrade-ready.service'):
            if adapter._unit_state(unit) not in (('inactive','failed') if repair else ('inactive',)):
                raise ReleaseError('recovery manager must be quiescent')
        _trusted_tree(recovery)
        active = _current_version(versions, current)
        with tempfile.TemporaryDirectory(prefix='.recovery-stage-', dir=root) as name:
            work = Path(name)
            staged = stage_unsigned_tar(archive,work) if signature is None else stage_verified(archive, signature, public_key, work)
            if staged.name != active and not (prepare_next and _version_key(staged.name)>_version_key(active)):
                raise ReleaseError('recovery package must match active version or explicitly prepare a newer version')
            # Match installed boot/recovery units byte-for-byte before switching
            # code. Render with the signed package, not the old recovery copy.
            generated = work / 'generated'; generated.mkdir(mode=0o700)
            command = ['/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE=1', f'PYTHONPATH={staged / "app"}',
                       '/usr/bin/python3', '-m', 'sunhill_nvr.deployment_cli', '--config',
                       str(adapter.config_file), '--output', str(generated)]
            if adapter.tls_certificate.exists(): command.append('--lan-tls')
            adapter._command(command, timeout=60)
            units = generated / 'units'
            old_names = {n for n in adapter._managed_names() if n.startswith('sunhill-upgrade-')}
            new_names = {p.name for p in units.iterdir() if p.name.startswith('sunhill-upgrade-')}
            if not old_names or old_names != new_names or any(
                adapter._read_trusted(adapter.system_dir / n) != adapter._read_trusted(units / n) for n in old_names
            ):
                raise ReleaseError('recovery units differ; offline unit migration required')
            adapter._command(['/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE=1',
                              f'PYTHONPATH={staged / "app"}', '/usr/bin/python3', '-m',
                              'sunhill_nvr.upgrade_admin', '--help'], timeout=60)
            # Verify the candidate manager imports before publication.
            _trusted_tree(staged, sync=True)
            previous = root / ('recovery-previous-' + secrets.token_hex(16))
            os.rename(staged, previous)
            _sync_dir(root)
            # Both directory entries now exist and are durable. If exchange or
            # the following fsync fails, retain both for inspection; do not
            # delete a tree that could be the only usable recovery copy.
            _exchange(recovery, previous)
            _sync_dir(root)
            return previous
    finally:
        os.close(fd)


def main(argv=None) -> int:
    from . import upgrade_admin
    parser = argparse.ArgumentParser(prog='sunhill-recovery-migration')
    parser.add_argument('archive', type=Path, nargs='?')
    parser.add_argument('signature', type=Path, nargs='?')
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--offline-units',action='store_true')
    modes.add_argument('--rollback-units',action='store_true')
    modes.add_argument('--archive-unit-journal',action='store_true')
    parser.add_argument('--prepare-next',action='store_true')
    args = parser.parse_args(argv)
    if args.prepare_next and not args.offline_units:parser.error('--prepare-next requires --offline-units')
    if args.rollback_units or args.archive_unit_journal:
        if args.archive or args.signature:parser.error('rollback/archive takes no package')
    elif not args.archive:parser.error('release archive required')
    adapter = upgrade_admin.context()
    if args.signature is not None:upgrade_admin.trusted_file(upgrade_admin.PUBLIC_KEY)
    elif args.archive is not None:upgrade_admin.trusted_file(args.archive)
    fd = os.open(adapter.state / 'manager.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.offline_units or args.rollback_units or args.archive_unit_journal:
            from . import recovery_units
            if args.rollback_units:
                recovery_units.rollback_units(adapter,upgrade_admin.ROOT,upgrade_admin.VERSIONS,upgrade_admin.CURRENT)
                print('Recovery unit migration rolled back; rescue mode remains active.')
            elif args.archive_unit_journal:
                print(f'Archived recovery unit journal: {recovery_units.archive_journal(adapter)}')
            else:
                previous=recovery_units.migrate_units(adapter,upgrade_admin.ROOT,upgrade_admin.VERSIONS,upgrade_admin.CURRENT,
                    upgrade_admin.PUBLIC_KEY,args.archive,args.signature,prepare_next=args.prepare_next)
                print(f'Recovery code and units replaced; previous tree: {previous}; rescue mode remains active.')
        else:
            previous = migrate(adapter, upgrade_admin.ROOT, upgrade_admin.VERSIONS, upgrade_admin.CURRENT,
                               upgrade_admin.PUBLIC_KEY, args.archive, args.signature)
            print(f'Recovery code replaced; previous tree: {previous}')
    finally: os.close(fd)
    return 0


if __name__ == '__main__': raise SystemExit(main())
