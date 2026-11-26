"""Retention orchestration with explicit mount, lock and activity checks."""
from __future__ import annotations
import contextlib
import fcntl
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from .files import open_confined,try_exclusive
from .rotation import list_recordings,unlink_confined
from .storage import MountIdentity,DirectoryIdentity,matches,space_for
from .timeline import utc_candidates
from zoneinfo import ZoneInfo

class RetentionBusy(RuntimeError):pass

@dataclass(frozen=True)
class RetentionResult:
    before: int
    after: int
    selected: tuple[str,...]
    preview: bool
    reached_target: bool
    protected_count: int = 0
    protected_bytes: int = 0
    unknown_time_count: int = 0
    unknown_time_bytes: int = 0

@contextlib.contextmanager
def exclusive_worker_lock(lock_file: Path):
    flags=os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(lock_file,flags,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RetentionBusy('another retention worker is running')
        yield
    finally:os.close(fd)

@contextlib.contextmanager
def shared_archive_lock(lock_file: Path):
    flags=os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(lock_file,flags,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BlockingIOError:raise RetentionBusy('archive rotation is running')
        yield
    finally:os.close(fd)

def run(root: Path, identity: MountIdentity | DirectoryIdentity, *, minimum: int, target: int, lock_file: Path,
        protected: Callable[[str], bool], active: Callable[[Path], bool], preview: bool = True,
        free: Callable[[Path],int] | None = None, mount_guard: Callable[[],bool] | None = None,
        on_remove: Callable[[str],None] | None = None, timezone_name: str = 'Europe/Warsaw') -> RetentionResult:
    """Remove oldest completed recordings, failing closed on uncertain conditions.

    active must detect any process holding a file open and raise on uncertainty.
    File locking protects only cooperating processes. The active detector must run
    with sufficient privileges and the storage must be a local filesystem.
    """
    if minimum<0 or target<minimum:raise ValueError('invalid space threshold')
    mount_guard=mount_guard or (lambda:True)
    if not root.is_absolute() or root.is_symlink() or not matches(root,identity) or not mount_guard():
        raise RuntimeError('archive identity mismatch')
    free=free or (lambda p:space_for(p).free_for_service)
    zone=ZoneInfo(timezone_name)
    with exclusive_worker_lock(lock_file):
        if not matches(root,identity) or not mount_guard():raise RuntimeError('archive identity changed')
        before=free(root);remaining=before;selected=[];protected_count=0;protected_bytes=0;unknown_time_count=0;unknown_time_bytes=0
        needs_rotation=before<minimum
        if not needs_rotation and not preview:
            if not matches(root,identity) or not mount_guard():raise RuntimeError('archive identity changed')
            return RetentionResult(before,before,(),preview,True)
        for index,recording in enumerate(list_recordings(root,timezone_name=timezone_name)):
            # A preview inspects the complete archive but does not delete; avoid
            # spawning a mount probe for every file. Actual deletion still
            # rechecks immediately before every unlink below.
            if (not preview or index%128==0) and (not matches(root,identity) or not mount_guard()):raise RuntimeError('archive identity changed')
            relative=str(recording.path.relative_to(root))
            is_protected=protected(relative)
            if is_protected:protected_count+=1;protected_bytes+=recording.size
            if recording.utc_timestamp is None and not utc_candidates(recording.timestamp,zone):
                unknown_time_count+=1;unknown_time_bytes+=recording.size;continue
            if is_protected:continue
            # The preview reports all protected and unresolvable recordings,
            # even after the target is reached or rotation is not necessary.
            if not needs_rotation or remaining>=target:continue
            fd=None
            try:
                fd,info=open_confined(root,relative)
                # Never delete a replacement, or a recording modified after
                # the initial sorted scan, even if its name still looks old.
                snapshot=(recording.device,recording.inode,recording.size,recording.mtime_ns,recording.ctime_ns)
                def current(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
                if current(info)!=snapshot:continue
                if not try_exclusive(fd) or active(recording.path) or protected(relative):continue
                if not matches(root,identity) or not mount_guard():raise RuntimeError('archive identity changed')
                if current(os.fstat(fd))!=snapshot:continue
                if not preview:
                    unlink_confined(root,recording.path,expected_device=info.st_dev,expected_inode=info.st_ino,
                                    expected_metadata=(info.st_size,info.st_mtime_ns,info.st_ctime_ns))
                    if on_remove:on_remove(relative)
                    remaining=free(root)
                else:
                    # A sparse recording may have a large logical length but
                    # occupy few blocks. Never promise its full apparent size.
                    blocks=getattr(info,'st_blocks',None)
                    estimate=min(info.st_size,blocks*512) if type(blocks) is int and blocks>=0 else info.st_size
                    remaining+=estimate
                selected.append(relative)
                if remaining>=target and not preview:break
            except FileNotFoundError:
                continue
            finally:
                if fd is not None:os.close(fd)
        if not matches(root,identity) or not mount_guard():raise RuntimeError('archive identity changed')
        return RetentionResult(before,remaining,tuple(selected),preview,not needs_rotation or remaining>=target,protected_count,protected_bytes,unknown_time_count,unknown_time_bytes)
