"""Retention policy for recordings, independent of the web application."""
from __future__ import annotations
import datetime as dt
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from . import fd_mount
from .timeline import utc_candidates
from zoneinfo import ZoneInfo

VIDEO = re.compile(r"(?i)(?:\d{6}|[^/]+_00_\d{14}|\d{8}T\d{6}(?:[+-]\d{4})?(?:[-_]\d+)?)\.mp4\Z")
CAMERA = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")

@dataclass(frozen=True)
class Recording:
    path: Path
    timestamp: dt.datetime
    size: int
    utc_timestamp: dt.datetime | None = None
    device: int | None = None
    inode: int | None = None
    mtime_ns: int | None = None
    ctime_ns: int | None = None

def recording_info(root: Path, path: Path, *, strict: bool = False) -> Recording | None:
    """Accept only camera/YYYY/MM/DD/filename inside the canonical archive."""
    try:
        rel = path.relative_to(root)
        if len(rel.parts) != 5 or not CAMERA.fullmatch(rel.parts[0]):
            return None
        year, month, day = rel.parts[1:4]
        name = rel.name
        if not (re.fullmatch(r"\d{4}", year) and re.fullmatch(r"\d{2}", month)
                and re.fullmatch(r"\d{2}", day) and VIDEO.fullmatch(name)):
            return None
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            return None
        utc_timestamp=None
        if re.fullmatch(r"\d{6}\.mp4", name, re.I):
            stamp = dt.datetime.strptime(year + month + day + name[:6], "%Y%m%d%H%M%S")
        else:
            m = re.search(r"_00_(\d{14})\.mp4$", name, re.I)
            if m:
                stamp = dt.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
            else:
                match=re.match(r"(\d{8}T\d{6})([+-]\d{4})?",name)
                stamp = dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
                if match.group(2):
                    utc_timestamp=dt.datetime.strptime(match.group(1)+match.group(2),"%Y%m%dT%H%M%S%z").astimezone(dt.timezone.utc)
            if stamp.strftime("%Y%m%d") != year + month + day:
                return None
        return Recording(path, stamp, file_stat.st_size,utc_timestamp,file_stat.st_dev,file_stat.st_ino,file_stat.st_mtime_ns,file_stat.st_ctime_ns)
    except OSError as exc:
        if strict and not isinstance(exc,FileNotFoundError):
            raise RuntimeError('archive file could not be inspected') from exc
        return None
    except (ValueError, AttributeError):
        return None

def iter_recordings(root: Path, min_age_seconds: int = 900, now: float | None = None, *, dates: set[dt.date] | None = None, timezone_name: str = 'Europe/Warsaw', camera: str | None = None):
    import time
    now = time.time() if now is None else now
    if dates is not None and (not dates or any(not isinstance(day,dt.date) for day in dates)):
        raise ValueError('invalid recording dates')
    prefixes={tuple(day.strftime('%Y/%m/%d').split('/')) for day in dates} if dates is not None else None
    if camera is not None and not CAMERA.fullmatch(camera):raise ValueError('invalid camera')
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    root_fd=os.open(root,flags)
    try:root_mount=fd_mount.identity(root_fd)
    finally:os.close(root_fd)
    root_device=root_mount[0]
    def relevant(parts):
        if not 1<=len(parts)<=4 or not CAMERA.fullmatch(parts[0]):return False
        for index,part in enumerate(parts[1:],1):
            if not re.fullmatch(r'\d{4}' if index==1 else r'\d{2}',part):return False
        if len(parts)>1:
            try:dt.date(int(parts[1]),int(parts[2]) if len(parts)>2 else 1,int(parts[3]) if len(parts)>3 else 1)
            except ValueError:return False
        return True
    def failed(exc):
        raise RuntimeError('archive traversal failed') from exc
    # Ignoring a failed directory could cause a newer recording to be deleted
    # while older recordings in the unreadable part of the archive are missed.
    for current, dirs, files in os.walk(root, followlinks=False,onerror=failed):
        safe_dirs = []
        for d in dirs:
            child = Path(current) / d
            parts=child.relative_to(root).parts
            if not relevant(parts) or (camera is not None and parts[0]!=camera):continue
            if prefixes is not None:
                if len(parts)>4 or (len(parts)>1 and not any(parts[1:]==prefix[:len(parts)-1] for prefix in prefixes)):
                    continue
            try:
                if child.is_symlink():continue
                child_fd=os.open(child,flags)
                try:fd_mount.require_same(root_mount,child_fd)
                finally:os.close(child_fd)
                safe_dirs.append(d)
            except fd_mount.MountBoundary:
                continue
            except OSError as exc:
                raise RuntimeError('archive directory could not be inspected') from exc
        dirs[:] = safe_dirs
        if len(Path(current).relative_to(root).parts)!=4:continue
        for name in files:
            if not VIDEO.fullmatch(name):continue
            p = Path(current) / name
            try:
                if now - p.lstat().st_mtime < min_age_seconds:
                    continue
            except FileNotFoundError:continue
            except OSError as exc:raise RuntimeError('archive file could not be inspected') from exc
            rec = recording_info(root, p,strict=True)
            if rec and rec.device == root_device and now-rec.mtime_ns/1_000_000_000>=min_age_seconds:
                yield rec

def list_recordings(root: Path, min_age_seconds: int = 900, now: float | None = None, *, dates: set[dt.date] | None = None, timezone_name: str = 'Europe/Warsaw') -> list[Recording]:
    result=iter_recordings(root,min_age_seconds,now,dates=dates,timezone_name=timezone_name)
    zone=ZoneInfo(timezone_name)
    def sort_key(record):
        # For legacy ambiguous times, use the later possible instant so a
        # potentially newer recording is not treated as certainly older.
        times=(record.utc_timestamp,) if record.utc_timestamp is not None else utc_candidates(record.timestamp,zone)
        return (times[-1] if times else dt.datetime.max.replace(tzinfo=dt.timezone.utc),str(record.path))
    return sorted(result,key=sort_key)

def rotate(root: Path, minimum_bytes: int, target_bytes: int, mount_check: Callable[[], bool],
           free_bytes: Callable[[], int], remove: Callable[[Path], None],
           protected: Callable[[Path], bool], dry_run: bool = True, *, timezone_name: str = 'Europe/Warsaw') -> dict:
    """Remove oldest eligible files until target; callers enforce single-writer locking.

    Disk space is checked after every deletion. Never act if the expected mount is absent.
    """
    if minimum_bytes < 0 or target_bytes < minimum_bytes:
        raise ValueError("invalid thresholds")
    if root.is_symlink() or not root.is_dir() or not mount_check():
        raise RuntimeError("archive mount is unavailable")
    available = free_bytes()
    result = {"before": available, "after": available, "files": [], "dry_run": dry_run}
    if available >= minimum_bytes:
        return result
    zone=ZoneInfo(timezone_name)
    for rec in list_recordings(root,timezone_name=timezone_name):
        if rec.utc_timestamp is None and not utc_candidates(rec.timestamp,zone):continue
        if not mount_check():
            raise RuntimeError("archive mount changed during rotation")
        if protected(rec.path):
            continue
        # Revalidate immediately before unlink. Higher-level worker must hold an archive lock.
        current = recording_info(root, rec.path,strict=True)
        if current is None:
            continue
        if not dry_run:
            remove(current.path)
            available = free_bytes()
        else:
            available += current.size
        result["files"].append(str(current.path))
        if available >= target_bytes:
            break
    result["after"] = available
    return result


def unlink_confined(root: Path, candidate: Path, *, expected_device: int, expected_inode: int,
                    expected_metadata: tuple[int,int,int] | None = None) -> None:
    """Unlink a verified regular file using no-follow directory descriptors.

    Refuses symlinks, mount boundaries and files that changed identity. This function
    must be coupled with a real mount identity check and an active-writer check.
    """
    import stat
    rel = candidate.relative_to(root)
    if len(rel.parts) != 5 or not CAMERA.fullmatch(rel.parts[0]) or any(part in ('', '.', '..') for part in rel.parts):
        raise ValueError('not an archive recording')
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise RuntimeError('safe directory traversal is unavailable')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    try:
        current = os.open(str(root), flags)
        descriptors.append(current)
        root_mount=fd_mount.identity(current)
        if root_mount[0] != expected_device:
            raise RuntimeError('archive device changed')
        for part in rel.parts[:-1]:
            current = os.open(part, flags, dir_fd=current)
            descriptors.append(current)
            fd_mount.require_same(root_mount,current)
        file_fd=os.open(rel.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=current)
        try:
            info=os.fstat(file_fd)
            fd_mount.require_same(root_mount,file_fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_dev != expected_device or info.st_ino != expected_inode):
                raise RuntimeError('recording identity changed')
            if expected_metadata is not None and (info.st_size,info.st_mtime_ns,info.st_ctime_ns)!=expected_metadata:
                raise RuntimeError('recording content changed')
            current_info=os.stat(rel.name,dir_fd=current,follow_symlinks=False)
            if (current_info.st_dev,current_info.st_ino)!=(info.st_dev,info.st_ino):
                raise RuntimeError('recording identity changed')
            if expected_metadata is not None and (current_info.st_size,current_info.st_mtime_ns,current_info.st_ctime_ns)!=expected_metadata:
                raise RuntimeError('recording content changed')
            os.unlink(rel.name, dir_fd=current)
        finally:os.close(file_fd)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
