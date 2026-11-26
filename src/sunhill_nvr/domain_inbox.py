"""Queue one bounded, unprivileged request to change the public hostname."""
from __future__ import annotations
import json
import os
import secrets
import stat
from pathlib import Path
from .atomic_publish import publish_new_file
from .nginx_config import HOST

def recovery_available() -> bool:
    # The privileged manager is deliberately pinned to the installation's
    # recovery copy, not to the currently active application release.
    helper=Path('/opt/sunhill-nvr/recovery/app/sunhill_nvr/domain_change.py')
    return helper.is_file() and not helper.is_symlink()

def submit(root: Path, hostname: str, user_id: int) -> None:
    info=root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
        raise ValueError('invalid request directory')
    if not isinstance(hostname,str) or not HOST.fullmatch(hostname) or type(user_id) is not int or user_id<1:
        raise ValueError('invalid hostname request')
    data=json.dumps({'hostname':hostname.lower(),'user_id':user_id},separators=(',',':')).encode('ascii')
    temp=root/('.domain-request-'+secrets.token_hex(16))
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data);output.flush();os.fsync(output.fileno())
        publish_new_file(temp,root/'domain-request.json')
    finally:temp.unlink(missing_ok=True)
