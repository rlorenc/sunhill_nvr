"""Validated camera inputs and private go2rtc configuration generation."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote,urlsplit,urlunsplit

SLUG=re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')

def camera_aliases(slug: str, has_sub: bool) -> tuple[str,...]:
    """Return native streams and a separate on-demand browser-compatible alias."""
    if not isinstance(slug,str) or not SLUG.fullmatch(slug):raise ValueError('invalid camera')
    return (slug,slug+'_sub','~'+slug) if has_sub else (slug,'~'+slug)

@dataclass(frozen=True)
class Camera:
    slug: str
    name: str
    main_url: str=field(repr=False)
    sub_url: str | None=field(default=None,repr=False)
    recording: str='ftp'
    enabled: bool=True

def validate_rtsp(url: str) -> None:
    if not isinstance(url,str) or not 1 <= len(url) <= 2048 or any(ord(c)<32 or ord(c)==127 for c in url):
        raise ValueError('invalid RTSP URL')
    try:
        u=urlsplit(url)
        port=u.port
    except ValueError as exc:
        raise ValueError('invalid RTSP URL') from exc
    if (u.scheme.lower() not in ('rtsp','rtsps') or not u.hostname or u.fragment or
        (port is not None and not 1 <= port <= 65535) or '\\' in u.netloc):
        raise ValueError('invalid RTSP URL')


def with_credentials(url: str, username: str, password: str) -> str:
    """Set camera-specific credentials without exposing them in API responses."""
    validate_rtsp(url)
    if (not isinstance(username,str) or not isinstance(password,str) or not username or not password
        or len(username)>128 or len(password)>512 or any(ord(c)<32 or ord(c)==127 for c in username+password)):
        raise ValueError('invalid camera credentials')
    parts=urlsplit(url);host=parts.netloc.rsplit('@',1)[-1]
    result=urlunsplit((parts.scheme,quote(username,safe='')+':'+quote(password,safe='')+'@'+host,parts.path,parts.query,''))
    validate_rtsp(result)
    return result

def validate(camera: Camera) -> None:
    if not isinstance(camera.slug,str) or not isinstance(camera.name,str) or not SLUG.fullmatch(camera.slug) or not camera.name or len(camera.name)>120:
        raise ValueError('invalid camera name')
    if any(ord(c)<32 for c in camera.name):
        raise ValueError('invalid camera name')
    if not isinstance(camera.recording,str) or camera.recording not in ('ftp','rtsp','none'):
        raise ValueError('invalid recording mode')
    validate_rtsp(camera.main_url)
    if camera.sub_url is not None:
        validate_rtsp(camera.sub_url)

def go2rtc_config(cameras: list[Camera]) -> str:
    """JSON is valid YAML and avoids raw YAML injection by camera URL or name.

    The management API and RTSP output bind only to loopback. An authenticated
    reverse proxy must expose only the streaming endpoints, never the API config.
    """
    names=set();streams={}
    for camera in cameras:
        validate(camera)
        if camera.slug in names:
            raise ValueError('duplicate camera slug')
        names.add(camera.slug)
        if not camera.enabled:
            continue
        aliases = [(camera.slug,camera.main_url)]
        if camera.sub_url:
            aliases.append((camera.slug+'_sub',camera.sub_url))
        for alias,source in aliases:
            if alias in streams:
                raise ValueError('camera stream aliases collide')
            streams[alias]=source
        # '~' is forbidden in camera slugs, so this alias cannot shadow an
        # existing camera or alter the native stream used for recording.
        streams['~'+camera.slug]='ffmpeg:'+camera.slug+'#video=h264#audio=aac'
    return json.dumps({'api':{'listen':'127.0.0.1:1984'},
                       'rtsp':{'listen':'127.0.0.1:8554'},
                       'webrtc':{'listen':''},'streams':streams},ensure_ascii=False,indent=2)+'\n'
