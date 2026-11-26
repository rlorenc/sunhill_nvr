"""Constrained read-only recording access with single-range HTTP support."""
from __future__ import annotations
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from . import fd_mount

@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive
    total: int
    @property
    def length(self):return self.end-self.start+1

class InvalidRange(ValueError):pass

def recorded_identity(device, inode, ctime_ns):
    values=(device,inode,ctime_ns)
    if values==(None,None,None):return None  # Legacy index entries are reindexed later.
    if any(not isinstance(value,str) or not value.isascii() or not value.isdigit() or len(value)>30 for value in values):
        raise ValueError('invalid recorded file identity')
    return values

def parse_range(header: str | None, size: int) -> ByteRange:
    if size <= 0:raise InvalidRange('empty file')
    if not header:return ByteRange(0,size-1,size)
    if len(header)>128:raise InvalidRange('range header too long')
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',header.strip())
    if not match or not any(match.groups()):raise InvalidRange('unsupported range')
    a,b=match.groups()
    if a:
        start=int(a);end=min(int(b),size-1) if b else size-1
        if start>=size or end<start:raise InvalidRange('range outside file')
    else:
        suffix=int(b)
        if suffix<=0:raise InvalidRange('invalid suffix')
        start=max(0,size-suffix);end=size-1
    return ByteRange(start,end,size)

def open_confined(root: Path, relative: str, *, expected_size: int | None = None,
                  expected_mtime_ns: int | None = None, expected_identity=None) -> tuple[int,os.stat_result]:
    path=PurePosixPath(relative)
    if path.is_absolute() or len(path.parts)!=5 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',path.parts[0]) or str(path)!=relative or any(p in ('','.','..') for p in path.parts):
        raise ValueError('invalid archive path')
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    fds=[];file_fd=None
    try:
        current=os.open(str(root),flags);fds.append(current);mount=fd_mount.identity(current);dev=mount[0]
        for component in path.parts[:-1]:
            current=os.open(component,flags,dir_fd=current);fds.append(current)
            fd_mount.require_same(mount,current)
        file_fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=current)
        info=os.fstat(file_fd)
        fd_mount.require_same(mount,file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_dev!=dev or info.st_nlink!=1:
            raise ValueError('invalid recording file')
        if expected_size is not None and info.st_size!=expected_size:raise ValueError('recording size changed')
        if expected_mtime_ns is not None and info.st_mtime_ns!=expected_mtime_ns:raise ValueError('recording changed')
        if expected_identity is not None and (str(info.st_dev),str(info.st_ino),str(info.st_ctime_ns))!=expected_identity:
            raise ValueError('recording identity changed')
        result=file_fd;file_fd=None
        return result,info
    finally:
        if file_fd is not None:os.close(file_fd)
        for fd in reversed(fds):os.close(fd)

class FileBody:
    def __init__(self,fd: int,part: ByteRange, *, authorize=None, interval: float=5.0, monotonic=None, head: bool=False):
        import time
        self.fd=fd;self.remaining=part.length;os.lseek(fd,part.start,os.SEEK_SET)
        self.authorize=authorize;self.interval=interval;self.monotonic=monotonic or time.monotonic;self.head=head
    def __iter__(self):
        try:
            if self.head:return
            next_check=self.monotonic()
            while self.remaining>0:
                if self.authorize is not None and self.monotonic()>=next_check:
                    try:
                        if not self.authorize():break
                    except Exception:
                        # A disconnected database must not keep granting access.
                        break
                    next_check=self.monotonic()+self.interval
                data=os.read(self.fd,min(self.remaining,256*1024))
                if not data:break
                self.remaining-=len(data)
                yield data
        finally:self.close()
    def close(self):
        if self.fd is not None:
            os.close(self.fd);self.fd=None


def acquire_shared(fd: int) -> None:
    """Keep an indexed recording protected from cooperating retention workers."""
    import fcntl
    fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)

def try_exclusive(fd: int) -> bool:
    """Return False when another worker is using this inode."""
    import errno,fcntl
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES,errno.EAGAIN):return False
        raise


def open_export(root: Path, name: str) -> tuple[int,os.stat_result]:
    if not re.fullmatch(r'export-[0-9]+-[a-f0-9]{16}\.mp4',name):raise ValueError('invalid export name')
    directory=os.open(str(root),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    fd=None
    try:
        mount=fd_mount.identity(directory)
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        info=os.fstat(fd)
        fd_mount.require_same(mount,fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('invalid export file')
        result=fd;fd=None;return result,info
    finally:
        if fd is not None:os.close(fd)
        os.close(directory)

def delete_export(root: Path, name: str, job_id: int) -> None:
    """Remove only this job's regular export, never crossing a mount boundary."""
    if type(job_id) is not int or job_id<1 or not re.fullmatch(r'export-'+str(job_id)+r'-[a-f0-9]{16}\.mp4',name):
        raise ValueError('invalid export name')
    directory=os.open(str(root),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        mount=fd_mount.identity(directory)
        try:fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        except FileNotFoundError:return
        try:
            info=os.fstat(fd);fd_mount.require_same(mount,fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('invalid export file')
            current=os.stat(name,dir_fd=directory,follow_symlinks=False)
            if (current.st_dev,current.st_ino)!=(info.st_dev,info.st_ino):raise RuntimeError('export file changed')
            os.unlink(name,dir_fd=directory)
        finally:os.close(fd)
    finally:os.close(directory)
