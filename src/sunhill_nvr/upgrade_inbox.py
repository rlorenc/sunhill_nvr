"""Bounded, non-executable update uploads and one atomic request at a time."""
from __future__ import annotations

import json
import contextlib
import fcntl
import os
import re
import secrets
import stat
import time
from pathlib import Path
from .atomic_publish import publish_new_file

from .release import MAX_BYTES

UPLOAD_ID=re.compile(r'[0-9a-f]{32}\Z')
LIMITS={'archive':MAX_BYTES,'signature':65536}


def _directory(root: Path) -> None:
    info=root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
        raise ValueError('update inbox must be a private directory')


def filename(kind: str, token: str) -> str:
    if kind not in LIMITS or not isinstance(token,str) or not UPLOAD_ID.fullmatch(token):
        raise ValueError('invalid upload identifier')
    return 'upload-'+token+('.zip' if kind=='archive' else '.sig')


@contextlib.contextmanager
def _upload_lock(root: Path):
    fd=os.open(root/'.uploads.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:os.close(fd)


def store(root: Path,kind: str,stream,length: int) -> str:
    _directory(root)
    if kind not in LIMITS or type(length) is not int or not 1<=length<=LIMITS[kind]:raise ValueError('invalid upload size')
    with _upload_lock(root):
        if (root/'request.json').exists() or (root/'request.json').is_symlink():raise ValueError('update request is pending')
        uploads=[]
        for item in root.iterdir():
            if not re.fullmatch(r'upload-[0-9a-f]{32}\.(?:zip|sig)',item.name):continue
            info=item.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_nlink==1 and time.time()-info.st_mtime>86400:
                item.unlink();continue
            uploads.append(item)
        if len(uploads)>=8:
            raise ValueError('too many update uploads')
        space=os.statvfs(root)
        if space.f_bavail*space.f_frsize < length*2+512*1024*1024:raise ValueError('insufficient space for update')
        token=secrets.token_hex(16);target=root/filename(kind,token)
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,'wb') as output:
                remaining=length
                while remaining:
                    chunk=stream.read(min(1024*1024,remaining))
                    if not chunk or len(chunk)>remaining:raise ValueError('incomplete update upload')
                    output.write(chunk);remaining-=len(chunk)
                output.flush();os.fsync(output.fileno())
        except BaseException:
            target.unlink(missing_ok=True);raise
        return token


def request(root: Path,archive: str,signature: str,user_id: int) -> None:
    _directory(root)
    if type(user_id) is not int or user_id<1:raise ValueError('invalid requesting user')
    # Serialize with upload cleanup and removal until the request is published.
    with _upload_lock(root):
        if (root/'rollback-request.json').exists() or (root/'rollback-request.json').is_symlink():raise FileExistsError('rollback pending')
        if (root/'approved-update.json').exists() or (root/'approved-update.json').is_symlink():raise FileExistsError('approved update pending')
        files=[filename('archive',archive),filename('signature',signature)]
        for name in files:
            fd=os.open(root/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size<=0:raise ValueError('invalid upload')
            finally:os.close(fd)
        payload=json.dumps({'archive':archive,'signature':signature,'user_id':user_id},separators=(',',':')).encode()
        temp=root/('.request-'+secrets.token_hex(16))
        fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,'wb') as output:output.write(payload);output.flush();os.fsync(output.fileno())
            publish_new_file(temp,root/'request.json')
        finally:temp.unlink(missing_ok=True)


def remove(root: Path,kind: str,token: str) -> None:
    _directory(root);name=filename(kind,token)
    with _upload_lock(root):
        pending=read_request(root)
        if pending and pending[kind]==token:raise ValueError('upload belongs to a pending update')
        directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            info=os.stat(name,dir_fd=directory,follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('invalid upload')
            os.unlink(name,dir_fd=directory)
        finally:os.close(directory)


def read_request(root: Path, *, with_identity: bool=False):
    """Read a request using fixed paths; safe to call from a privileged worker."""
    try:fd=os.open(root/'request.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return None
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>4096:raise ValueError('invalid update request')
        # Reject unsafe multi-link requests, including legacy interrupted
        # publications. New publications always have exactly one link.
        if info.st_nlink!=1:return None
        raw=os.read(fd,4097)
        value=json.loads(raw)
        if not isinstance(value,dict) or set(value)!={'archive','signature','user_id'} or type(value['user_id']) is not int or value['user_id']<1:
            raise ValueError('invalid update request')
        filename('archive',value['archive']);filename('signature',value['signature'])
        return (value,(info.st_dev,info.st_ino)) if with_identity else value
    except (UnicodeError,json.JSONDecodeError,KeyError,TypeError) as exc:raise ValueError('invalid update request') from exc
    finally:os.close(fd)
