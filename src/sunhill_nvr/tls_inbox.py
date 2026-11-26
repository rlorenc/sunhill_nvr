"""One private, bounded request for replacing the LAN TLS certificate."""
from __future__ import annotations
import json
import os
import secrets
import stat
from pathlib import Path
from .atomic_publish import publish_new_file

LIMIT=96*1024

def submit(root: Path, certificate: str, key: str, user_id: int) -> None:
    info=root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
        raise ValueError('invalid request directory')
    if type(user_id) is not int or user_id<1 or not isinstance(certificate,str) or not isinstance(key,str):
        raise ValueError('invalid certificate request')
    if not certificate.isascii() or not key.isascii() or not certificate or not key:
        raise ValueError('PEM must be ASCII')
    data=json.dumps({'certificate':certificate,'key':key,'user_id':user_id},separators=(',',':')).encode('ascii')
    if len(data)>LIMIT:raise ValueError('certificate request is too large')
    temp=root/('.tls-request-'+secrets.token_hex(16))
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data);output.flush();os.fsync(output.fileno())
        publish_new_file(temp,root/'tls-request.json')
    finally:temp.unlink(missing_ok=True)
