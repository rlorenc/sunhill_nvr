"""Build RTSP recording processes and safely publish completed segments.

This is a core component, not a standalone daemon. A service manager must supervise
it, create day directories and guarantee that a file is closed before publication.
"""
from __future__ import annotations
import datetime as dt
import os
import re
import stat
from pathlib import Path
from urllib.parse import quote
from . import fd_mount

SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')
SEGMENT = re.compile(r'(\d{8})T(\d{6})([+-]\d{4})?\.mp4\Z')

def validate_staging_parts(parts: tuple[str, ...]) -> None:
    """Validate a relative staging path, including legacy sessionless paths."""
    if len(parts) == 6 and re.fullmatch(r'[a-f0-9]{32}', parts[1]):
        date_parts = parts[2:5]
    elif len(parts) == 5:
        date_parts = parts[1:4]
    else:
        raise ValueError('invalid staging location')
    if not SLUG.fullmatch(parts[0]):
        raise ValueError('invalid camera')
    match = SEGMENT.fullmatch(parts[-1])
    if not match:
        raise ValueError('invalid segment name')
    try:
        timestamp = dt.datetime.strptime(match.group(1) + match.group(2), '%Y%m%d%H%M%S')
        if match.group(3):
            dt.datetime.strptime(match.group(3), '%z')
    except ValueError as exc:
        raise ValueError('invalid segment timestamp') from exc
    if timestamp.strftime('%Y/%m/%d') != '/'.join(date_parts):
        raise ValueError('segment date does not match directory')

def recording_command(slug: str, staging_root: Path, *, segment_seconds: int = 300,
                      rtsp_port: int = 8554, ffmpeg: str = 'ffmpeg', session: str | None = None,
                      audio: str = 'copy', io_timeout_seconds: int = 30, hevc: bool = False) -> list[str]:
    """Record a local go2rtc alias without exposing camera credentials in argv."""
    if not SLUG.fullmatch(slug) or not 1 <= rtsp_port <= 65535:
        raise ValueError('invalid camera or port')
    if not 30 <= segment_seconds <= 3600:
        raise ValueError('invalid segment duration')
    if not staging_root.is_absolute():
        raise ValueError('staging root must be absolute')
    if session is not None and not re.fullmatch(r'[a-f0-9]{32}', session):
        raise ValueError('invalid recording session')
    if audio not in ('copy','aac'):raise ValueError('invalid recording audio mode')
    if type(io_timeout_seconds) is not int or not 5 <= io_timeout_seconds <= 300:
        raise ValueError('invalid RTSP I/O timeout')
    session_root = staging_root / slug / session if session else staging_root / slug
    output = session_root / '%Y' / '%m' / '%d' / '%Y%m%dT%H%M%S%z.mp4'
    return [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'warning',
            '-rtsp_transport', 'tcp', '-timeout', str(io_timeout_seconds*1000000), '-i', f'rtsp://127.0.0.1:{rtsp_port}/{quote(slug)}',
            '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'copy', '-c:a', audio,
            *(['-bsf:v', 'hevc_mp4toannexb', '-tag:v', 'hvc1'] if hevc else []),
            '-f', 'segment', '-segment_time', str(segment_seconds),
            '-reset_timestamps', '1', '-strftime', '1',
            '-segment_format', 'mp4', '-segment_format_options', 'movflags=+frag_keyframe+empty_moov+default_base_moof', str(output)]

def prepare_days(staging_root: Path, slug: str, today: dt.date, days: int = 2, session: str | None = None) -> None:
    if not SLUG.fullmatch(slug) or not 1 <= days <= 7:
        raise ValueError('invalid camera or day range')
    if session is not None and not re.fullmatch(r'[a-f0-9]{32}', session):
        raise ValueError('invalid recording session')
    if staging_root.is_symlink():raise ValueError('invalid staging root')
    staging_root.mkdir(parents=True,exist_ok=True,mode=0o750)
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    root=os.open(staging_root,flags)
    try:
        mount=fd_mount.identity(root)
        for n in range(days):
            d=today+dt.timedelta(days=n)
            components=(slug,)+((session,) if session else ())+tuple(d.strftime('%Y/%m/%d').split('/'))
            current=root;opened=[]
            try:
                for component in components:
                    try:os.mkdir(component,mode=0o750,dir_fd=current)
                    except FileExistsError:pass
                    current=os.open(component,flags,dir_fd=current);opened.append(current)
                    fd_mount.require_same(mount,current)
            finally:
                for fd in reversed(opened):os.close(fd)
    finally:os.close(root)

def publish_closed_segment(staging_root: Path, archive_root: Path, slug: str, source: Path, *, expected: tuple[int,int,int,int] | None = None) -> Path:
    """Publish one caller-verified closed file without overwriting an archive file.

    Requires staging and archive on the same filesystem. Caller must ensure exclusive
    ownership of staging and exclude files still open by FFmpeg.
    """
    if not SLUG.fullmatch(slug) or staging_root.is_symlink() or archive_root.is_symlink():
        raise ValueError('invalid root or camera')
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError('not a regular segment')
    rel=source.relative_to(staging_root)
    validate_staging_parts(rel.parts)
    if rel.parts[0]!=slug:raise ValueError('segment camera does not match')
    m=SEGMENT.fullmatch(rel.name)
    timestamp=dt.datetime.strptime(m.group(1)+m.group(2),'%Y%m%d%H%M%S')
    # Pin every source and destination directory, refusing symlinks and nested
    # mounts. A completed hard link is recovered if power failed before unlink.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = [];source_fd=None
    try:
        stage=os.open(str(staging_root),flags);descriptors.append(stage)
        stage_mount=fd_mount.identity(stage)
        for component in rel.parts[:-1]:
            stage=os.open(component,flags,dir_fd=stage);descriptors.append(stage)
            fd_mount.require_same(stage_mount,stage)
        source_fd=os.open(rel.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=stage)
        fd_mount.require_same(stage_mount,source_fd)
        srcstat=os.fstat(source_fd)
        if not stat.S_ISREG(srcstat.st_mode) or srcstat.st_nlink not in (1,2):raise ValueError('invalid segment')
        signature=(srcstat.st_dev,srcstat.st_ino,srcstat.st_size,srcstat.st_mtime_ns)
        if expected is not None and signature!=expected:raise ValueError('segment changed before publication')
        current = os.open(str(archive_root), flags)
        descriptors.append(current)
        root_mount=fd_mount.identity(current)
        if root_mount != stage_mount:
            raise ValueError('staging and archive must use the same filesystem')
        parts = (slug, timestamp.strftime('%Y'), timestamp.strftime('%m'), timestamp.strftime('%d'))
        for component in parts:
            try:
                os.mkdir(component, mode=0o750, dir_fd=current)
                os.fsync(current)
            except FileExistsError:
                pass
            current = os.open(component, flags, dir_fd=current)
            descriptors.append(current)
            fd_mount.require_same(root_mount,current)
        # Preserve the UTC offset for new RTSP files so repeated local hours
        # remain distinguishable. Legacy sessions retain their old names.
        base=timestamp.strftime('%Y%m%dT%H%M%S')+(m.group(3) or '')
        first=base+'.mp4' if m.group(3) else timestamp.strftime('%H%M%S.mp4')
        names = [first] + [base + f'-{n:03}.mp4' for n in range(1,1000)]
        os.fsync(source_fd)
        synced=os.fstat(source_fd)
        if (synced.st_dev,synced.st_ino,synced.st_size,synced.st_mtime_ns)!=signature:
            raise ValueError('segment changed during synchronization')
        if srcstat.st_nlink==2:
            # Only remove the staging name when the sole other hard link is
            # one of this segment's valid archive names on the same mount.
            for name in names:
                try:existing=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=current)
                except FileNotFoundError:continue
                try:
                    fd_mount.require_same(root_mount,existing)
                    info=os.fstat(existing)
                    if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=(srcstat.st_dev,srcstat.st_ino):continue
                    if info.st_nlink!=2 or (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=signature:raise ValueError('segment changed during recovery')
                    check=os.stat(rel.name,dir_fd=stage,follow_symlinks=False)
                    if (check.st_dev,check.st_ino,check.st_nlink)!=(info.st_dev,info.st_ino,2):raise ValueError('segment changed during recovery')
                    os.fsync(current)
                    os.unlink(rel.name,dir_fd=stage);os.fsync(stage)
                    return archive_root.joinpath(*parts,name)
                finally:os.close(existing)
            raise ValueError('segment has an unexpected hard link')
        for name in names:
            try:
                os.link(rel.name,name,src_dir_fd=stage,dst_dir_fd=current,follow_symlinks=False)
            except FileExistsError:
                continue
            linked=os.stat(name,dir_fd=current,follow_symlinks=False)
            check=os.stat(rel.name,dir_fd=stage,follow_symlinks=False)
            if ((linked.st_dev,linked.st_ino,linked.st_size,linked.st_mtime_ns)!=signature or
                (check.st_dev,check.st_ino,check.st_nlink)!=(srcstat.st_dev,srcstat.st_ino,2)):
                raise ValueError('segment changed during publication')
            os.fsync(current)
            os.unlink(rel.name,dir_fd=stage);os.fsync(stage)
            return archive_root.joinpath(*parts, name)
        raise FileExistsError('too many segment name collisions')
    finally:
        if source_fd is not None:os.close(source_fd)
        for fd in reversed(descriptors):
            os.close(fd)
