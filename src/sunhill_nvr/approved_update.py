"""Requests reference root-approved packages, never arbitrary web-uploaded code."""
from __future__ import annotations
import json,os,re,secrets,stat
from pathlib import Path
from .atomic_publish import publish_new_file
from .upgrade_inbox import _upload_lock

ROOT=Path('/opt/sunhill-nvr/updates')
NAME=re.compile(r'sunhill-nvr-[0-9]+\.[0-9]+\.[0-9]+(?:[-.][A-Za-z0-9]+)*\.tar\.gz\Z')
REQUEST='approved-update.json'

def valid(name):return isinstance(name,str) and NAME.fullmatch(name) is not None

def available(root: Path | None=None):
    root=ROOT if root is None else root
    try:
        info=root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:return []
        result=[]
        for file in root.iterdir():
            if not valid(file.name):continue
            info=file.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and not info.st_mode&0o022:result.append(file.name)
        return sorted(result)
    except OSError:return []

def submit(inbox: Path,name: str,user_id: int,root: Path | None=None):
    root=ROOT if root is None else root
    info=inbox.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:raise ValueError('invalid inbox')
    if name not in available(root) or type(user_id) is not int or user_id<1:raise ValueError('invalid approved update')
    with _upload_lock(inbox):
        if (inbox/'rollback-request.json').exists() or (inbox/'rollback-request.json').is_symlink():raise FileExistsError('rollback pending')
        if (inbox/'request.json').exists() or (inbox/'request.json').is_symlink():raise FileExistsError('signed update pending')
        temp=inbox/('.approved-'+secrets.token_hex(16))
        try:
            fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as file:
                file.write(json.dumps({'package':name,'user_id':user_id}).encode());file.flush();os.fsync(file.fileno())
            publish_new_file(temp,inbox/REQUEST)
        finally:temp.unlink(missing_ok=True)
