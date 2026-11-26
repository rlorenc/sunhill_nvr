"""Restricted, read-only helpers for checking active camera files.

A systemd-managed Unix socket wrapper must enforce peer UID and request limits.
This module deliberately provides no deletion or arbitrary command execution.
"""
from __future__ import annotations
import os
import stat
from pathlib import Path,PurePosixPath
from .proc_scan import snapshot
from .recorder import validate_staging_parts
from .rotation import VIDEO,CAMERA
from . import fd_mount
import re

def _open_checked(root: Path, relative: str, *, staging: bool, exports: bool=False) -> tuple[int,os.stat_result]:
    if not isinstance(relative,str) or not relative or '\\' in relative or '\0' in relative:
        raise ValueError('invalid path')
    parts=PurePosixPath(relative).parts
    if PurePosixPath(relative).is_absolute() or str(PurePosixPath(relative))!=relative:
        raise ValueError('invalid path')
    if exports:
        temporary=r'\.sunhill-export-[A-Za-z0-9_-]+\.mp4'
        if len(parts)==1 and re.fullmatch(temporary,parts[0]):pass
        elif len(parts)==1 and re.fullmatch(r'export-[1-9][0-9]{0,12}-[a-f0-9]{16}\.mp4',parts[0]):pass
        elif len(parts)==2 and re.fullmatch(r'\.sunhill-(?:job|batch)-[A-Za-z0-9_-]+',parts[0]) and re.fullmatch(r'(?:part-\d+|pass-\d+-\d+|\.sunhill-export-[A-Za-z0-9_-]+)\.mp4',parts[1]):pass
        else:raise ValueError('invalid export path')
        date_parts=None
    elif staging:
        validate_staging_parts(parts)
        date_parts=None
    else:
        if len(parts)!=5 or not CAMERA.fullmatch(parts[0]) or not VIDEO.fullmatch(parts[-1]):raise ValueError('invalid archive path')
        date_parts=parts[1:4]
    if date_parts is not None and not (re.fullmatch(r'\d{4}',date_parts[0]) and re.fullmatch(r'\d{2}',date_parts[1]) and re.fullmatch(r'\d{2}',date_parts[2])):
        raise ValueError('invalid date path')
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    dirs=[];file_fd=None
    try:
        current=os.open(root,flags);dirs.append(current);mount=fd_mount.identity(current);dev=mount[0]
        for component in parts[:-1]:
            current=os.open(component,flags,dir_fd=current);dirs.append(current)
            fd_mount.require_same(mount,current)
        file_fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=current)
        info=os.fstat(file_fd)
        fd_mount.require_same(mount,file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_dev!=dev:raise ValueError('not a regular file')
        out=file_fd;file_fd=None;return out,info
    finally:
        if file_fd is not None:os.close(file_fd)
        for fd in reversed(dirs):os.close(fd)

def check_batch(root: Path, paths: list[str], *, staging: bool, exports: bool=False, scan=snapshot) -> list[bool]:
    if not isinstance(paths,list) or not 1<=len(paths)<=64:raise ValueError('invalid batch')
    handles=[];stats=[]
    try:
        for path in paths:
            fd,info=_open_checked(root,path,staging=staging,exports=exports);handles.append(fd);stats.append(info)
        active=scan()
        return [(info.st_dev,info.st_ino) in active for info in stats]
    finally:
        for fd in handles:os.close(fd)

def handle(request: dict, *, peer_uid: int, allowed_uid: int, archive: Path, staging: Path, exports: Path | None=None, scan=snapshot) -> dict:
    if peer_uid!=allowed_uid:raise PermissionError('unauthorized peer')
    if not isinstance(request,dict) or set(request)!={'scope','paths'} or request['scope'] not in ('archive','staging','exports'):
        raise ValueError('invalid request')
    root={'archive':archive,'staging':staging,'exports':exports}[request['scope']]
    if root is None:raise ValueError('scope unavailable')
    return {'active':check_batch(root,request['paths'],staging=request['scope']=='staging',exports=request['scope']=='exports',scan=scan)}
