"""Check that camera streams yield a decodable frame through local go2rtc."""
from __future__ import annotations

import socket
import sqlite3
import subprocess
import time

from .cameras import SLUG
from . import monitor

def max_sample_age(camera_count: int) -> int:
    """Account for the interval and a bounded sequential probe per camera."""
    if type(camera_count) is not int or camera_count<0:raise ValueError('invalid camera count')
    return max(900,360+15*camera_count)


def gateway_available(*, timeout: float=2) -> bool:
    try:
        with socket.create_connection(('127.0.0.1',8554),timeout=timeout):return True
    except OSError:return False


def probe(slug: str, *, run=subprocess.run, timeout: int=12, port: int=8554) -> bool:
    """Decode one frame without passing camera credentials as command arguments."""
    if not isinstance(slug,str) or not SLUG.fullmatch(slug):raise ValueError('invalid camera')
    if type(port) is not int or not 1<=port<=65535:raise ValueError('invalid gateway port')
    args=['/usr/bin/ffmpeg','-hide_banner','-loglevel','error','-nostdin','-abort_on','empty_output',
          '-rtsp_transport','tcp','-i',f'rtsp://127.0.0.1:{port}/{slug}',
          '-map','0:v:0','-frames:v','1','-an','-f','null','-']
    try:
        result=run(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout)
        return result.returncode==0
    except (OSError,subprocess.SubprocessError):return False


def _gateway_state(db: sqlite3.Connection, available: bool, now: int) -> None:
    state='stream_service_ok' if available else 'stream_service_error'
    with db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute("SELECT state FROM system_health WHERE name='stream_service'").fetchone()
        db.execute("INSERT INTO system_health(name,state,changed_at) VALUES('stream_service',?,?) ON CONFLICT(name) DO UPDATE SET changed_at=CASE WHEN state<>excluded.state OR changed_at>excluded.changed_at+30 THEN excluded.changed_at ELSE changed_at END,state=excluded.state",(state,now))
        if (not previous and not available) or (previous and previous[0]!=state):
            db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,'go2rtc',?,?)",(state,now))


def check(db: sqlite3.Connection, *, now: int | None=None, gateway=gateway_available, inspect=probe, should_stop=lambda:False) -> None:
    fixed_now=now is not None
    now=int(time.time()) if now is None else now
    available=gateway();_gateway_state(db,available,now)
    if not available:return
    cameras=db.execute('SELECT cameras.id,cameras.slug,camera_details.main_url FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id WHERE cameras.enabled=1 ORDER BY cameras.id').fetchall()
    for cid,slug,main_url in cameras:
        if should_stop():return
        success=inspect(slug)
        checked_at=now if fixed_now else int(time.time())
        if not success and not gateway():
            _gateway_state(db,False,checked_at);return
        with db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT cameras.slug,camera_details.main_url FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id WHERE cameras.id=? AND cameras.enabled=1',(cid,)).fetchone()
            if current!=(slug,main_url):continue
            old=db.execute('SELECT state,failures,checked_at FROM stream_health WHERE camera_id=?',(cid,)).fetchone()
            previous=old[0] if old else 'unknown';failures=0 if success else min(100,(old[1] if old else 0)+1)
            gateway_state=db.execute("SELECT state,changed_at FROM system_health WHERE name='stream_service'").fetchone()
            rechecked_after_outage=bool(old and gateway_state and gateway_state[0]=='stream_service_ok' and (old[2]<gateway_state[1] or old[2]>checked_at+30))
            stale=bool(old and not monitor.sample_fresh(old[2],checked_at,max_sample_age(len(cameras))))
            # A single failed probe normally gets a grace period. It must not
            # renew a pre-outage or stale positive sample as current, though.
            state='available' if success else 'unavailable' if failures>=2 else 'unknown' if rechecked_after_outage or stale else previous
            db.execute('INSERT INTO stream_health(camera_id,state,failures,checked_at,changed_at) VALUES(?,?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET state=excluded.state,failures=excluded.failures,checked_at=excluded.checked_at,changed_at=CASE WHEN state<>excluded.state THEN excluded.changed_at ELSE changed_at END',(cid,state,failures,checked_at,checked_at))
            reconfirmed=bool(old and not monitor.sample_fresh(old[2],checked_at,max_sample_age(len(cameras))))
            if (state=='unavailable' and (state!=previous or rechecked_after_outage or reconfirmed)) or (state=='available' and previous=='unavailable'):
                db.execute('INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES (?,?,?,?)',(cid,slug,'online' if state=='available' else 'offline',checked_at))
