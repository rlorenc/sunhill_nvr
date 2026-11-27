"""Render and atomically activate validated camera configuration.

The caller is a narrowly scoped privileged service. The state directory and its
parents must be administrator-owned and inaccessible for writing by web workers.
"""
from __future__ import annotations
import json
import os
import secrets
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path
from .cameras import Camera,go2rtc_config

class ReconcileError(RuntimeError):pass

def desired_gateway(db: sqlite3.Connection) -> dict[str,str]:
    """Render the RTSP gateway without querying legacy FTP accounts or storage."""
    rows=db.execute('SELECT cameras.slug,cameras.name,camera_details.main_url,camera_details.sub_url,camera_details.record_mode,cameras.enabled FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id ORDER BY cameras.slug').fetchall()
    return {'go2rtc.json':go2rtc_config([Camera(slug,name,main,sub,mode,bool(enabled)) for slug,name,main,sub,mode,enabled in rows])}


def _sync_dir(path: Path) -> None:
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def _pending(path: Path) -> bool:
    try:info=path.lstat()
    except FileNotFoundError:return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or info.st_mode&0o077:
        raise ReconcileError('invalid activation marker')
    return True


def _mark_pending(path: Path) -> None:
    if not _pending(path):
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:os.fsync(fd)
        finally:os.close(fd)
    _sync_dir(path.parent)


def _clear_pending(path: Path) -> None:
    path.unlink(missing_ok=True)
    _sync_dir(path.parent)


def apply(state_dir: Path, contents: dict[str,str], *, validate, restart, stop=None, service_gid: int | None = None, required_files: tuple[str,...] = ()) -> None:
    """Publish all config files via a single symlink switch and roll back on failure.

    validate receives the candidate directory. restart returns True only when all
    affected services are healthy. Actual gateway validation is the
    responsibility of the privileged service integrating this component.
    """
    if state_dir.is_symlink() or not state_dir.is_dir():raise ReconcileError('invalid state directory')
    if set(contents)!={'go2rtc.json'}:raise ReconcileError('invalid configuration set')
    if any(not name or '/' in name or name in ('.','..') for name in required_files):raise ReconcileError('invalid required file')
    current=state_dir/'current'
    pending=state_dir/'.activation-pending';interrupted=_pending(pending)
    if current.exists() and not current.is_symlink():raise ReconcileError('invalid current configuration')
    old=os.readlink(current) if current.is_symlink() else None
    if old is not None and (Path(old).is_absolute() or len(Path(old).parts)!=1 or not old.startswith('config-')):
        raise ReconcileError('invalid previous configuration link')
    previous_contents={};rollback_safe=False;repair=set()
    if old is not None:
        previous=state_dir/old
        if previous.is_symlink() or not previous.is_dir():raise ReconcileError('previous configuration is unavailable')
        directory_info=previous.lstat()
        if directory_info.st_uid!=os.geteuid() or directory_info.st_mode&0o022:
            raise ReconcileError('previous configuration directory is unsafe')
        try:
            for name in contents:
                info=(previous/name).lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid():
                    raise ReconcileError('previous configuration contains an unsafe file')
                expected=0o640
                if stat.S_IMODE(info.st_mode)!=expected:repair.add(name)
            previous_contents={name:(previous/name).read_text(encoding='utf-8') for name in contents}
            rollback_safe=not repair and all((previous/name).is_file() and not (previous/name).is_symlink() for name in required_files)
            if not interrupted and rollback_safe and previous_contents==contents and (set(contents)!={'go2rtc.json'} or not any(os.path.lexists(previous/name) for name in ('ftp.passwd','ftp.group','proftpd.conf'))):
                return
        except OSError:
            pass
    changed=frozenset(name for name in contents if previous_contents.get(name)!=contents[name] or name in repair)
    if interrupted or not changed:changed=frozenset(contents)
    new_dir=Path(tempfile.mkdtemp(prefix='config-',dir=state_dir))
    may_be_published=False
    try:
        os.chmod(new_dir,0o750)
        if service_gid is not None:os.chown(new_dir,-1,service_gid)
        for name,content in contents.items():
            path=new_dir/name
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                f.write(content);f.flush()
                if name=='go2rtc.json':
                    if service_gid is not None:os.fchown(f.fileno(),-1,service_gid)
                    os.fchmod(f.fileno(),0o640)
                os.fsync(f.fileno())
        if validate(new_dir) is not True:raise ReconcileError('candidate configuration rejected')
        _sync_dir(new_dir)
        # Persist the intent before changing current. If the controller dies
        # after publication, a later reconciliation must restart both daemons
        # even when their on-disk configuration already matches the database.
        _mark_pending(pending)
        def swap(name: str):
            temp=state_dir/('.next-'+secrets.token_hex(12))
            try:os.symlink(name,temp);os.replace(temp,current);_sync_dir(state_dir)
            finally:
                if temp.is_symlink():temp.unlink()
        # Do not delete a directory if the pointer switch might have reached disk.
        may_be_published=True;swap(new_dir.name)
        try:
            if restart(changed) is not True:raise ReconcileError('service health check failed')
        except Exception as exc:
            if old is not None and not rollback_safe:
                # An incomplete or writable snapshot is not a valid recovery
                # target. Keep the validated candidate and the durable marker;
                # retry both daemons on the next controller cycle.
                if stop is not None and stop(frozenset(contents)) is not True:
                    raise ReconcileError('cannot stop services after unsafe rollback target') from exc
                raise ReconcileError('unsafe previous configuration; activation remains pending') from exc
            if old is not None:
                swap(old)
                if restart(changed) is not True:raise ReconcileError('configuration rollback failed') from exc
            else:
                if stop is not None and stop(changed) is not True:
                    raise ReconcileError('cannot stop services after initial configuration failure') from exc
                current.unlink(missing_ok=True);_sync_dir(state_dir)
            may_be_published=False
            _clear_pending(pending)
            raise ReconcileError('configuration rejected; previous version restored') from exc
        _clear_pending(pending)
    finally:
        if not may_be_published and new_dir.exists():shutil.rmtree(new_dir)
