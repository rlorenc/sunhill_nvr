"""Explicit root-approved upgrade across a terminal failed rollback.

Never deletes an old journal/backup. A nonterminal transaction must be
recovered by the ordinary recovery manager before this command is allowed.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
from . import upgrade_admin as admin
from .release import ReleaseError, stage_unsigned_tar
from .upgrade_transaction import _current_version,_read_journal,_version_key,activate
from .recovery_migration import migrate


def permitted(adapter,old):
    prior=_read_journal(adapter.state)
    if prior and (prior['phase']!='rolled_back' or prior['old']!=old):
        raise ReleaseError('repair accepts only a completed rollback matching current')
    for name in ('domain-transaction.json','tls-transaction.json','storage-transaction.json','recovery-unit-migration'):
        p=adapter.state/name
        if p.exists() or p.is_symlink():raise ReleaseError('other recovery transaction pending: '+name)
    if prior is None and (adapter.maintenance.exists() or adapter.maintenance.is_symlink()):
        # A crash between superseding the previous journal and starting the new
        # one leaves the write fence in place. Only an archived terminal journal
        # matching the current release permits resuming that explicit repair.
        found=False
        for p in adapter.state.glob('superseded-*.json'):
            try:
                data=json.loads(adapter._read_trusted(p,limit=4096))
                found=found or (data.get('old')==old and data.get('phase')=='rolled_back')
            except (OSError,ValueError,ReleaseError,AttributeError):pass
        if not found:raise ReleaseError('unrelated maintenance is active')
    if prior:
        backup=adapter.state/prior['backup']
        admin.trusted_file(backup)
        if prior.get('system_backup'):
            folder=adapter.state/prior['system_backup']
            adapter._trusted_backup_directory(folder)
            admin.trusted_file(folder/'complete')
    return prior


def repair(adapter,archive,*,root=admin.ROOT,versions=admin.VERSIONS,current=admin.CURRENT,rtsp_only=False):
    fd=os.open(adapter.state/'manager.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        old=_current_version(versions,current)
        permitted(adapter,old)
        admin.trusted_file(archive)
        staged=stage_unsigned_tar(archive,versions,reuse_existing=True)
        if _version_key(staged.name)<=_version_key(old):raise ReleaseError('repair package must be newer than current')
        adapter._runtime()
        previous=migrate(adapter,root,versions,current,admin.PUBLIC_KEY,archive,None,prepare_next=True,repair=True)
        print('Previous recovery preserved: '+str(previous),flush=True)
        activate(versions,current,adapter.state,staged.name,adapter.operations(),supersede_rollback=True)
    finally:os.close(fd)


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('archive',type=Path)
    parser.add_argument('--rtsp-only',action='store_true')
    args=parser.parse_args(argv)
    repair(admin.context(),args.archive,rtsp_only=args.rtsp_only)
    return 0

if __name__=='__main__':raise SystemExit(main())
