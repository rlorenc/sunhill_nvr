"""Length-bounded local Unix RPC for the read-only privileged activity helper."""
from __future__ import annotations
import json
import os
import socket
import stat
import struct
from pathlib import Path
from .activity_helper import handle
from .proc_scan import snapshot

MAX_REQUEST=16*1024

def peer_uid(connection) -> int:
    if not hasattr(socket,'SO_PEERCRED'):raise RuntimeError('SO_PEERCRED unavailable')
    data=connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i'))
    _,uid,_=struct.unpack('3i',data)
    return uid

def peer_pid(connection) -> int:
    if not hasattr(socket,'SO_PEERCRED'):raise RuntimeError('SO_PEERCRED unavailable')
    data=connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i'))
    pid,_,_=struct.unpack('3i',data)
    if pid<=0:raise RuntimeError('invalid peer process')
    return pid

def read_message(connection) -> dict:
    data=bytearray()
    while b'\n' not in data:
        chunk=connection.recv(min(4096,MAX_REQUEST+1-len(data)))
        if not chunk:raise ValueError('truncated request')
        data.extend(chunk)
        if len(data)>MAX_REQUEST:raise ValueError('request too large')
    raw,remaining=bytes(data).split(b'\n',1)
    if remaining:raise ValueError('multiple requests are not supported')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('invalid request')
    return value

def handle_connection(connection, *, allowed_uid: int, archive: Path, staging: Path, exports: Path | None=None) -> None:
    connection.settimeout(10)
    try:
        uid=peer_uid(connection)
        if uid!=allowed_uid:raise PermissionError('unauthorized peer')
        # Retention holds its own exclusive file descriptor during this check.
        # Ignore only this authenticated local caller; FFmpeg, FTP, web and
        # every other process remain visible to the privileged scan.
        pid=peer_pid(connection)
        result=handle(read_message(connection),peer_uid=uid,allowed_uid=allowed_uid,archive=archive,staging=staging,exports=exports,scan=lambda:snapshot(ignore={pid}))
        message={'ok':True,**result}
    except Exception:
        message={'ok':False,'error':'file activity check unavailable'}
    connection.sendall(json.dumps(message,separators=(',',':')).encode()+b'\n')

def serve(socket_path: Path, *, allowed_uid: int, socket_gid: int, archive: Path, staging: Path, exports: Path | None=None) -> None:
    """Serve until systemd stops the process. Parent directory must be root-owned."""
    parent=socket_path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=0 or parent.st_mode&0o022:
        raise RuntimeError('socket parent is not trusted')
    try:
        old=socket_path.lstat()
        if not stat.S_ISSOCK(old.st_mode) or old.st_uid!=0:raise RuntimeError('refusing to replace socket path')
        socket_path.unlink()
    except FileNotFoundError:pass
    server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path));os.chown(socket_path,0,socket_gid);os.chmod(socket_path,0o660);server.listen(16)
        while True:
            connection,_=server.accept()
            with connection:
                try:handle_connection(connection,allowed_uid=allowed_uid,archive=archive,staging=staging,exports=exports)
                except OSError:pass
    finally:
        server.close()
        try:socket_path.unlink()
        except FileNotFoundError:pass

def query(socket_path: Path, scope: str, paths: list[str], *, timeout: int = 15) -> list[bool]:
    raw=json.dumps({'scope':scope,'paths':paths},separators=(',',':')).encode()+b'\n'
    if len(raw)>MAX_REQUEST:raise ValueError('request too large')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout);connection.connect(str(socket_path));connection.sendall(raw)
        response=read_message(connection)
    if response.get('ok') is not True or not isinstance(response.get('active'),list) or len(response['active'])!=len(paths) or any(type(x) is not bool for x in response['active']):
        raise RuntimeError('file activity check unavailable')
    return response['active']
