"""Authenticated loopback WebSocket relay with continuous authorization.

Only go2rtc's /api/ws stream endpoint is reachable. Connections are closed
when the session expires, the user is disabled, or camera access is revoked.
"""
from __future__ import annotations
import asyncio
import base64
import contextlib
import hashlib
import ipaddress
import json
import stat
import os
import re
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs,quote,urlsplit
from . import database,security,workers
from .config import load
from .cameras import camera_aliases

MAX_HEADER=16384
CHECK_SECONDS=15
GUID='258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

def _resolved_alias(db, token: str, alias: str):
    """Return camera identity and expected gateway source in one DB snapshot."""
    with db:
        db.execute('BEGIN')
        user=security.get_user(db,token)
        if not user:return None
        candidates=(alias.removeprefix('~'),alias[:-4] if alias.endswith('_sub') else alias)
        rows=db.execute('SELECT cameras.id,cameras.slug,camera_details.main_url,camera_details.sub_url FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id WHERE cameras.slug IN (?,?)',candidates).fetchall()
        matches=[(cid,slug,main,sub) for cid,slug,main,sub in rows if alias in camera_aliases(slug,bool(sub))]
        if len(matches)!=1:return None
        cid,slug,main,sub=matches[0]
        if not security.may_access(db,user,cid,live=True):return None
        if alias=='~'+slug:
            expected={alias:'ffmpeg:'+slug+'#video=h264#audio=aac',slug:main}
        else:expected={alias:main if alias==slug else sub}
        return cid,expected


def authorize_alias(db, token: str, alias: str) -> bool:
    """Database authorization for the preliminary HTTP auth request."""
    return _resolved_alias(db,token,alias) is not None


def _gateway_revision(state: Path, expected: dict[str,str]) -> str | None:
    """Check the root-published, fully activated mapping, without exposing URLs.

    A database edit may precede a controller tick, and a failed activation may
    leave an older gateway configuration running indefinitely. Reject a stream
    until its actual published mapping matches the authorized camera. The
    durable activation marker covers the pointer switch and daemon restarts.
    """
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    def trusted(fd):
        info=os.fstat(fd)
        return info.st_uid==0 and not info.st_mode&0o022
    def pending(fd):
        try:os.stat('.activation-pending',dir_fd=fd,follow_symlinks=False)
        except FileNotFoundError:return False
        return True
    try:
        root=os.open(state,flags)
        try:
            if not trusted(root) or pending(root):return None
            revision=os.readlink('current',dir_fd=root)
            if not re.fullmatch(r'config-[A-Za-z0-9_-]+',revision):return None
            folder=os.open(revision,flags,dir_fd=root)
            try:
                if not trusted(folder):return None
                fd=os.open('go2rtc.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=folder)
                with os.fdopen(fd,'rb') as file:
                    info=os.fstat(file.fileno())
                    if not trusted(file.fileno()) or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:return None
                    raw=file.read(16*1024*1024+1)
                if len(raw)>16*1024*1024:return None
                streams=json.loads(raw)['streams']
                if not isinstance(streams,dict) or any(streams.get(name)!=source for name,source in expected.items()):return None
                if pending(root) or os.readlink('current',dir_fd=root)!=revision:return None
                return revision
            finally:os.close(folder)
        finally:os.close(root)
    except (OSError,ValueError,KeyError,TypeError):return None


def authorize(db_path: Path, token: str, alias: str) -> bool:
    try:
        with contextlib.closing(database.open_db(db_path)) as db:
            return authorize_alias(db,token,alias)
    except Exception:return False


def authorize_stream(db_path: Path, state: Path, token: str, alias: str):
    """Production relay: pin both camera identity and activated revision."""
    if not workers.writes_permitted():return None
    try:
        with contextlib.closing(database.open_db(db_path)) as db:
            resolved=_resolved_alias(db,token,alias)
        if resolved is None:return None
        camera_id,expected=resolved
        revision=_gateway_revision(state,expected)
        if revision is None:return None
        return camera_id,revision
    except Exception:return None

async def _header(reader: asyncio.StreamReader) -> bytes:
    data=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),timeout=10)
    if len(data)>MAX_HEADER:raise ValueError('header too large')
    return data

def _parse_request(raw: bytes, origin: str) -> tuple[str,str,str]:
    lines=raw.decode('ascii').split('\r\n');parts=lines[0].split(' ')
    if len(parts)!=3 or parts[0]!='GET' or parts[2]!='HTTP/1.1':raise ValueError('invalid request')
    parsed=urlsplit(parts[1]);params=parse_qs(parsed.query,keep_blank_values=True)
    if parsed.path!='/api/ws' or parsed.netloc or parsed.scheme or parsed.fragment or set(params)!={'src'} or len(params['src'])!=1:
        raise ValueError('invalid stream')
    alias=params['src'][0]
    if not re.fullmatch(r'~?[a-z0-9][a-z0-9_-]{0,67}',alias):raise ValueError('invalid stream')
    headers={}
    for line in lines[1:]:
        if not line:continue
        name,sep,value=line.partition(':');name=name.lower()
        if not sep or not re.fullmatch(r'[a-z0-9-]+',name) or name in headers:raise ValueError('invalid headers')
        headers[name]=value.strip()
    if (headers.get('origin')!=origin or headers.get('upgrade','').lower()!='websocket' or
        'upgrade' not in [x.strip().lower() for x in headers.get('connection','').split(',')] or
        headers.get('sec-websocket-version')!='13'):raise ValueError('invalid websocket request')
    key=headers.get('sec-websocket-key','')
    if len(key)!=24 or len(base64.b64decode(key,validate=True))!=16:raise ValueError('invalid websocket key')
    cookie=SimpleCookie();cookie.load(headers.get('cookie',''))
    token=cookie['sunhill_session'].value if 'sunhill_session' in cookie else ''
    if not token or not token.isascii() or len(token)>128:raise ValueError('missing session')
    return alias,token,key

async def _copy(reader: asyncio.StreamReader,writer: asyncio.StreamWriter) -> None:
    while True:
        data=await reader.read(65536)
        if not data:return
        writer.write(data);await writer.drain()

async def _watch(authorize_fn, token: str, alias: str, interval: float, identity) -> None:
    while True:
        await asyncio.sleep(interval)
        if await asyncio.to_thread(authorize_fn,token,alias)!=identity:return

async def handle(reader,writer,*,origin: str,authorize_fn,upstream_port: int=1984,check_interval: float=CHECK_SECONDS,semaphore=None):
    upstream_writer=None;tasks=[];accepted=False;acquired=False
    try:
        peer=writer.get_extra_info('peername')
        if not peer or not ipaddress.ip_address(peer[0]).is_loopback:raise ValueError('non-loopback client')
        if semaphore is not None:
            if semaphore.locked():raise ValueError('too many streams')
            await semaphore.acquire();acquired=True
        alias,token,key=_parse_request(await _header(reader),origin)
        identity=await asyncio.to_thread(authorize_fn,token,alias)
        if not identity:raise ValueError('access denied')
        upstream_reader,upstream_writer=await asyncio.wait_for(asyncio.open_connection('127.0.0.1',upstream_port,limit=MAX_HEADER),timeout=10)
        # The client Origin was checked before authorization. The next hop is
        # plain HTTP to a loopback-only gateway; use its own same-host Origin.
        # Never forward the public Origin or session cookie to go2rtc.
        request=(f'GET /api/ws?src={quote(alias,safe="")} HTTP/1.1\r\nHost: 127.0.0.1:{upstream_port}\r\n'
                 f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                 f'Sec-WebSocket-Version: 13\r\nOrigin: http://127.0.0.1:{upstream_port}\r\n\r\n').encode('ascii')
        upstream_writer.write(request);await upstream_writer.drain()
        response=await _header(upstream_reader)
        lines=response.decode('ascii').split('\r\n')
        if not lines[0].startswith('HTTP/1.1 101 '):raise ValueError('upstream rejected websocket')
        headers={}
        for line in lines[1:]:
            if not line:continue
            name,sep,value=line.partition(':');name=name.lower()
            if not sep or name in headers:raise ValueError('invalid upstream response')
            headers[name]=value.strip()
        accept=base64.b64encode(hashlib.sha1((key+GUID).encode('ascii')).digest()).decode('ascii')
        if (headers.get('sec-websocket-accept')!=accept or headers.get('upgrade','').lower()!='websocket' or
            'upgrade' not in [x.strip().lower() for x in headers.get('connection','').split(',')]):raise ValueError('invalid upstream handshake')
        if await asyncio.to_thread(authorize_fn,token,alias)!=identity:raise ValueError('access revoked during handshake')
        writer.write(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+'\r\n\r\n').encode('ascii'))
        await writer.drain();accepted=True
        tasks=[asyncio.create_task(_copy(reader,upstream_writer)),asyncio.create_task(_copy(upstream_reader,writer)),
               asyncio.create_task(_watch(authorize_fn,token,alias,check_interval,identity))]
        await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
    except (Exception,asyncio.CancelledError):
        if not accepted:
            try:writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');await writer.drain()
            except Exception:pass
    finally:
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        if upstream_writer is not None:
            upstream_writer.close()
            with contextlib.suppress(Exception):await upstream_writer.wait_closed()
        writer.close()
        with contextlib.suppress(Exception):await writer.wait_closed()
        if acquired:semaphore.release()

async def serve(cfg) -> None:
    semaphore=asyncio.Semaphore(64)
    async def accept(reader,writer):
        await handle(reader,writer,origin=cfg.origin,authorize_fn=lambda token,alias:authorize_stream(cfg.database,cfg.control_state,token,alias),semaphore=semaphore)
    server=await asyncio.start_server(accept,'127.0.0.1',1985,limit=MAX_HEADER,backlog=64)
    async with server:await server.serve_forever()

def main() -> int:
    cfg=load(Path(os.environ.get('SUNHILL_CONFIG','/opt/sunhill-nvr/config/config.json')))
    asyncio.run(serve(cfg));return 0

if __name__=='__main__':raise SystemExit(main())
