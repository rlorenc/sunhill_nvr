"""Send a minimal event to Home Assistant without following redirects."""
from __future__ import annotations
import http.client
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

class NotificationError(RuntimeError): pass

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def validate_token(token: str) -> None:
    if not isinstance(token,str) or not token or len(token)>4096 or any(not 33<=ord(c)<=126 for c in token):
        raise ValueError('invalid token')

def build_request(base_url: str, token: str, *, camera_id: str, state: str) -> urllib.request.Request:
    if not isinstance(base_url,str) or not 1<=len(base_url)<=2048 or any(ord(c)<=32 or ord(c)==127 for c in base_url):
        raise ValueError('invalid Home Assistant base URL')
    try:
        url=urlsplit(base_url);port=url.port
    except ValueError as exc:raise ValueError('invalid Home Assistant base URL') from exc
    if (url.scheme not in ('http','https') or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ('','/') or '\\' in url.netloc
            or (port is not None and not 1<=port<=65535)):
        raise ValueError('invalid Home Assistant base URL')
    validate_token(token)
    if not camera_id or len(camera_id)>64 or state not in ('offline','online','stale','recording','clock_skew','clock_unavailable','disk_low','disk_ok','disk_unavailable','clock_ok','rotation_blocked','rotation_ok','rotation_error','stream_service_ok','stream_service_error','service_down','service_up','monitor_error','monitor_ok','recording_corrupt','recording_corrupt_cleared','recording_publish_error','recording_publish_cleared','recording_staging_error','recording_staging_cleared','recording_process_error','recording_process_cleared','recording_audio_missing','recording_audio_ok'):
        raise ValueError('invalid event')
    data=json.dumps({'camera':camera_id,'state':state},ensure_ascii=False).encode('utf-8')
    return urllib.request.Request(base_url.rstrip('/') + '/api/events/sunhill_nvr',
                                  data=data, method='POST',
                                  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})

def send(base_url: str, token: str, *, camera_id: str, state: str, timeout: int = 5) -> None:
    request=build_request(base_url,token,camera_id=camera_id,state=state)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request,timeout=timeout) as response:
            if not 200 <= response.status < 300:
                raise NotificationError('Home Assistant rejected event')
    except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
        # Do not propagate URL, access token, or response body into app logs.
        raise NotificationError('Could not deliver Home Assistant event') from exc
