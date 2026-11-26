"""Check configured ONVIF clocks without changing camera settings."""
from __future__ import annotations
import math,sqlite3,time
from . import onvif_time,monitor

def max_sample_age(camera_count: int) -> int:
    if type(camera_count) is not int or camera_count<0:raise ValueError('invalid camera count')
    return max(1800,1020+6*camera_count)

def check(db: sqlite3.Connection, *, now: int | None=None, query=onvif_time.query, threshold: int=30, should_stop=lambda:False) -> None:
    fixed_now=now is not None
    now=int(time.time()) if now is None else now
    rows=db.execute('SELECT cameras.id,cameras.slug,camera_details.onvif_url,camera_details.onvif_username,camera_details.onvif_password,camera_details.onvif_auth FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id WHERE cameras.enabled=1 AND camera_details.onvif_url IS NOT NULL').fetchall()
    for cid,slug,url,username,password,auth in rows:
        if should_stop():return
        try:
            result=query(url,username or '',password or '',auth_mode=auth)
            offset=result.offset_seconds
            if not isinstance(offset,(int,float)) or isinstance(offset,bool) or not math.isfinite(offset):raise ValueError('invalid clock offset')
            state='clock_skew' if abs(offset)>threshold else 'clock_ok'
            failed=False
        except (OSError,ValueError):
            failed=True;state='clock_unavailable'
        checked_at=now if fixed_now else int(time.time())
        with db:
            # The camera can be edited, disabled or removed while the network
            # request is in flight. Do not publish stale health results.
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT cameras.slug,camera_details.onvif_url,camera_details.onvif_username,camera_details.onvif_password,camera_details.onvif_auth,camera_details.clock_checked FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id WHERE cameras.id=? AND cameras.enabled=1',(cid,)).fetchone()
            if not current or current[:5]!=(slug,url,username,password,auth):continue
            if failed:
                db.execute("UPDATE camera_details SET clock_offset=NULL,clock_checked=?,clock_error='Nie udało się odczytać czasu ONVIF' WHERE camera_id=?",(checked_at,cid))
            else:
                db.execute('UPDATE camera_details SET clock_offset=?,clock_checked=?,clock_error=NULL WHERE camera_id=?',(offset,checked_at,cid))
            key='clock:'+str(cid);previous=db.execute('SELECT state FROM system_health WHERE name=?',(key,)).fetchone()
            db.execute('INSERT INTO system_health(name,state,changed_at) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET changed_at=CASE WHEN state<>excluded.state THEN excluded.changed_at ELSE changed_at END,state=excluded.state',(key,state,checked_at))
            reconfirmed=bool(previous and previous[0]==state and state!='clock_ok' and not monitor.sample_fresh(current[5],checked_at,max_sample_age(len(rows))))
            if (not previous and state!='clock_ok') or (previous and previous[0]!=state) or reconfirmed:
                # Recording timestamps use the public state clock_skew too.
                # Preserve the source internally so changing ONVIF settings
                # does not delete an unrelated recording alarm.
                db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at,origin) VALUES (?,?,?,?,'onvif')",(cid,slug,state,checked_at))
