"""Strict instance settings shared by web and workers."""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from .nginx_config import HOST
from .media_layout import validate_media_layout

@dataclass(frozen=True)
class Config:
    hostname: str
    database: Path
    archive: Path
    staging: Path
    exports: Path
    archive_lock: Path
    activity_socket: Path
    control_state: Path
    timezone: str
    minimum_free: int
    free_buffer: int
    @property
    def origin(self):return 'https://'+self.hostname

def parse(raw: dict) -> Config:
    required={'hostname','database','archive','staging','exports','archive_lock','activity_socket','control_state','timezone','minimum_free','free_buffer'}
    if not isinstance(raw,dict) or set(raw)!=required:raise ValueError('invalid configuration fields')
    host=raw['hostname']
    if not isinstance(host,str) or not HOST.fullmatch(host):raise ValueError('invalid hostname')
    host=host.lower()
    paths=[]
    for key in ('database','archive','staging','exports','archive_lock','activity_socket','control_state'):
        value=raw[key]
        if not isinstance(value,str) or '\0' in value or '\n' in value or '\r' in value:raise ValueError('invalid path')
        p=Path(value)
        if not p.is_absolute() or '..' in p.parts or p==Path('/') or str(p)!=value.rstrip('/'):
            raise ValueError('non-canonical path')
        paths.append(p)
    zone=raw['timezone']
    try:
        if not isinstance(zone,str):raise ValueError('invalid timezone')
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError,ValueError) as exc:raise ValueError('invalid timezone') from exc
    minimum,buffer=raw['minimum_free'],raw['free_buffer']
    if type(minimum) is not int or type(buffer) is not int or minimum<0 or buffer<0 or minimum+buffer>2**63-1:
        raise ValueError('invalid storage limits')
    validate_media_layout(paths[1],paths[2],paths[3])
    # FTP and recording workers can write the media trees. Never allow a
    # database, lock, activity socket or privileged daemon configuration to
    # reside inside one of them (or to alias a media root).
    def overlap(a,b):return a==b or a in b.parents or b in a.parents
    for media_root in paths[1:4]:
        for private_path in (paths[0],*paths[4:]):
            if overlap(media_root,private_path):raise ValueError('media and private state paths overlap')
    private_paths=(paths[0],*paths[4:])
    for i,first in enumerate(private_paths):
        for second in private_paths[i+1:]:
            if overlap(first,second):raise ValueError('private state paths overlap')
    return Config(host,*paths,zone,minimum,buffer)

def load(path: Path) -> Config:
    if path.is_symlink() or not path.is_file():raise ValueError('invalid configuration file')
    if path.stat().st_size > 16*1024:raise ValueError('configuration file too large')
    with path.open(encoding='utf-8') as f:return parse(json.load(f))
