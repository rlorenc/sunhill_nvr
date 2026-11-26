"""Single untrusted administrator request for an archive directory change."""
from __future__ import annotations
import json
import os
import secrets
import stat
from pathlib import Path
from .atomic_publish import publish_new_file

def recovery_available() -> bool:
    # Marker introduced in 0.2.19; pinned older coordinators cannot parse IDs.
    helper=Path('/opt/sunhill-nvr/recovery/app/sunhill_nvr/recording_settings.py')
    return helper.is_file() and not helper.is_symlink()

def nested_recovery_available() -> bool:
    helper=Path('/opt/sunhill-nvr/recovery/app/sunhill_nvr/media_layout.py')
    return helper.is_file() and not helper.is_symlink()

def normalize(path: str) -> str:
    if not isinstance(path,str) or not path.startswith('/') or len(path)>2048 or any(c in path for c in '\0\r\n'):
        raise ValueError('invalid media path')
    parsed=Path(path)
    if '..' in parsed.parts or parsed==Path('/') or path.startswith('//'):raise ValueError('invalid media path')
    return str(parsed)

def submit(root: Path, archive: str, user_id: int, *, exports: str | None=None, staging: str | None=None, request_id: str | None=None) -> None:
    info=root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:raise ValueError('invalid request directory')
    if (not isinstance(archive,str) or len(archive)>2048 or not archive.startswith('/') or '\0' in archive
        or '\n' in archive or '\r' in archive or type(user_id) is not int or user_id<1):raise ValueError('invalid archive request')
    value={'archive':normalize(archive),'user_id':user_id}
    if request_id is not None:
        import re
        if not isinstance(request_id,str) or not re.fullmatch('[0-9a-f]{32}',request_id):raise ValueError('invalid request id')
        value['request_id']=request_id
    for name,path in (('exports',exports),('staging',staging)):
        if path is not None:
            if not isinstance(path,str) or not path.startswith('/') or len(path)>2048 or any(c in path for c in '\0\r\n'):raise ValueError('invalid media path')
            value[name]=normalize(path)
    data=json.dumps(value,separators=(',',':')).encode()
    if len(data)>4096:raise ValueError('archive request too large')
    temp=root/('.storage-request-'+secrets.token_hex(16))
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as output:output.write(data);output.flush();os.fsync(output.fileno())
        publish_new_file(temp,root/'storage-request.json')
    finally:temp.unlink(missing_ok=True)
