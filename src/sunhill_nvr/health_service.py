"""Persistent camera-recording health transitions and notification outbox."""
from __future__ import annotations
import sqlite3
import secrets
import time
import math
from . import monitor

DEFAULT_STALE_AFTER=900

def stale_threshold(db: sqlite3.Connection) -> int:
    row=db.execute("SELECT value FROM system_settings WHERE key='recording_stale_after'").fetchone()
    if not row:return DEFAULT_STALE_AFTER
    try:value=int(row[0])
    except (ValueError,TypeError) as exc:raise ValueError('invalid recording threshold') from exc
    if not 60<=value<=86400:raise ValueError('invalid recording threshold')
    return value

def update(db: sqlite3.Connection, *, now: int | None = None, stale_after: int = 900) -> int:
    now=int(time.time()) if now is None else now
    if stale_after<=0:raise ValueError('invalid stale threshold')
    changes=0
    with db:
        db.execute('BEGIN IMMEDIATE')
        rows=db.execute('SELECT cameras.id,cameras.slug,cameras.enabled,camera_details.record_mode,camera_details.recording_since,camera_health.state,camera_health.last_checked FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id LEFT JOIN camera_health ON cameras.id=camera_health.camera_id').fetchall()
        from .recording_settings import segment_seconds
        duration_setting=segment_seconds(db)
        for cid,slug,enabled,mode,started,old,old_checked in rows:
            effective_stale=max(stale_after,duration_setting+120) if mode=='rtsp' and duration_setting>300 else stale_after
            # A large backward correction of the server clock must not keep a
            # camera in the initial grace period until the old time catches up.
            if type(started) is not int or started<0:
                # An invalid persisted value must not disable monitoring of
                # every camera or indefinitely extend this camera's grace.
                started=max(1,now-effective_stale-1);db.execute('UPDATE camera_details SET recording_since=? WHERE camera_id=?',(started,cid))
            elif not started or started>now+30:
                started=now;db.execute('UPDATE camera_details SET recording_since=? WHERE camera_id=?',(started,cid))
            # Inspect the most recently written indexed file, rather than the
            # largest camera timestamp. Otherwise a single file dated in the
            # future masks recovery after the camera clock is corrected.
            # For legacy repeated hours retain the conservative later candidate.
            row=db.execute("""SELECT CAST(strftime('%s',COALESCE(utc_second,utc_first)) AS INTEGER),duration
                FROM recordings WHERE camera=? AND utc_first IS NOT NULL
                ORDER BY mtime_ns DESC,id DESC LIMIT 1""",(slug,)).fetchone()
            last=None
            if row and row[0] is not None:
                try:
                    duration=float(row[1]);end=float(row[0])+duration
                    if not math.isfinite(duration) or not 0<duration<=86400 or not math.isfinite(end) or not -62135596800<=end<=253402300799:
                        raise ValueError('invalid recording timestamp')
                    last=int(end)
                except (ValueError,TypeError,OverflowError):pass
            status=monitor.camera_status(enabled=bool(enabled) and mode!='none',reachable=None,last_recording=last,now=now,stale_after=effective_stale,started=started)
            try:previous=monitor.State(old) if old else None
            except ValueError:previous=None
            if old!=status.state.value:changes+=1
            db.execute('INSERT INTO camera_health(camera_id,state,changed_at,last_checked) VALUES(?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET changed_at=CASE WHEN state<>excluded.state THEN excluded.changed_at ELSE changed_at END,state=excluded.state,last_checked=excluded.last_checked',(cid,status.state.value,now,now))
            db.execute('UPDATE camera_details SET last_file_at=? WHERE camera_id=?',(last,cid))
            reconfirmed=(previous==status.state and status.state in (monitor.State.STALE,monitor.State.OFFLINE,monitor.State.CLOCK_SKEW)
                         and not monitor.sample_fresh(old_checked,now,monitor.HEALTH_MAX_AGE))
            if monitor.should_notify(previous,status.state) or reconfirmed:
                db.execute('INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(?,?,?,?)',(cid,slug,status.state.value,now))
    return changes


def _delivery_state(db: sqlite3.Connection, state: str, settings: dict) -> None:
    if state not in ('ha_delivery_ok','ha_delivery_error'):raise ValueError('invalid delivery state')
    with db:
        db.execute('BEGIN IMMEDIATE')
        if _settings(db)!=settings:return
        db.execute("INSERT INTO system_health(name,state,changed_at) VALUES('ha_delivery',?,?) ON CONFLICT(name) DO UPDATE SET changed_at=CASE WHEN state<>excluded.state THEN excluded.changed_at ELSE changed_at END,state=excluded.state",(state,int(time.time())))

def _settings(db: sqlite3.Connection) -> dict:
    return dict(db.execute("SELECT key,value FROM system_settings WHERE key IN ('ha_url','ha_token','ha_enabled','ha_generation')"))

def invalidate_delivery(db: sqlite3.Connection) -> None:
    """Invalidate in-flight acknowledgements when another transaction clears events."""
    if not db.in_transaction:raise ValueError('transaction required')
    db.execute("INSERT INTO system_settings(key,value) VALUES('ha_generation',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(secrets.token_hex(16),))

def _finish_delivery(db: sqlite3.Connection, settings: dict, nid: int, *, failed: bool) -> bool:
    # IDs may be reused after a disable/enable cycle clears the outbox. Never
    # acknowledge a replacement event after sending with old settings.
    with db:
        db.execute('BEGIN IMMEDIATE')
        if _settings(db)!=settings:return False
        if failed:db.execute('UPDATE pending_notifications SET attempts=attempts+1 WHERE id=?',(nid,))
        else:db.execute('DELETE FROM pending_notifications WHERE id=?',(nid,))
    return True

def enqueue_active_faults(db: sqlite3.Connection, *, now: int | None = None) -> None:
    """Queue current faults when the integration is enabled, inside the caller's transaction."""
    if not db.in_transaction:raise ValueError('transaction required')
    from .stream_health import max_sample_age
    from .clock_service import max_sample_age as clock_sample_age
    now=int(time.time()) if now is None else now
    camera_count=db.execute('SELECT count(*) FROM cameras WHERE enabled=1').fetchone()[0]
    clock_count=db.execute('SELECT count(*) FROM cameras c JOIN camera_details d ON d.camera_id=c.id WHERE c.enabled=1 AND d.onvif_url IS NOT NULL').fetchone()[0]
    faults=set()
    for cid,slug,state in db.execute("""SELECT c.id,c.slug,h.state FROM cameras c
            JOIN camera_details d ON d.camera_id=c.id JOIN camera_health h ON h.camera_id=c.id
            WHERE c.enabled=1 AND d.record_mode<>'none' AND h.state IN ('stale','offline','clock_skew')
            AND h.last_checked BETWEEN ? AND ?""",(now-monitor.HEALTH_MAX_AGE,now+30)):
        faults.add((cid,slug,state,''))
    for cid,slug in db.execute("""SELECT c.id,c.slug FROM cameras c JOIN stream_health h ON h.camera_id=c.id
            LEFT JOIN system_health g ON g.name='stream_service'
            WHERE c.enabled=1 AND h.state='unavailable' AND h.checked_at BETWEEN ? AND ? AND
            (g.name IS NULL OR (g.state='stream_service_ok' AND h.checked_at>=g.changed_at))""",(now-max_sample_age(camera_count),now+30)):
        faults.add((cid,slug,'offline',''))
    for cid,slug,state in db.execute("""SELECT c.id,c.slug,h.state FROM cameras c
            JOIN camera_details d ON d.camera_id=c.id JOIN system_health h ON h.name='clock:'||c.id
            WHERE c.enabled=1 AND d.onvif_url IS NOT NULL AND h.state IN ('clock_skew','clock_unavailable')
            AND d.clock_checked BETWEEN ? AND ?""",(now-clock_sample_age(clock_count),now+30)):
        faults.add((cid,slug,state,'onvif'))
    for cid,slug,state in db.execute("""SELECT c.id,c.slug,h.state FROM cameras c
            JOIN camera_details d ON d.camera_id=c.id JOIN system_health h
            ON h.name IN ('recorder_corrupt:'||c.slug,'recorder_publish:'||c.slug,
                          'recorder_staging:'||c.slug,'recorder_process:'||c.slug,'recorder_audio:'||c.slug)
            WHERE c.enabled=1 AND d.record_mode<>'none' AND h.state IN
            ('recording_corrupt','recording_publish_error','recording_staging_error','recording_process_error','recording_audio_missing')"""):
        faults.add((cid,slug,state,''))
    system_sources={'storage':('storage',{'disk_low','disk_unavailable'}),
                    'rotation':('rotation',{'rotation_blocked','rotation_error'}),
                    'stream_service':('go2rtc',{'stream_service_error'}),
                    'service_monitor':('service_monitor',{'monitor_error'})}
    for name,state in db.execute('SELECT name,state FROM system_health'):
        source,states=system_sources.get(name,('',set()))
        if state in states:faults.add((None,source,state,''))
    for (name,) in db.execute("SELECT name FROM service_health WHERE state='inactive' AND checked_at BETWEEN ? AND ?",(now-monitor.HEALTH_MAX_AGE,now+30)):
        faults.add((None,name,'service_down',''))
    db.executemany('INSERT INTO pending_notifications(camera_id,source,state,created_at,origin) VALUES(?,?,?,?,?)',
                   [(cid,source,state,now,origin) for cid,source,state,origin in sorted(faults,key=lambda x:(x[1],x[2],x[0] or 0,x[3]))])

def deliver(db: sqlite3.Connection, *, send=None, limit: int = 20, budget: float=20, monotonic=time.monotonic) -> int:
    """Deliver queued status changes. Failed delivery remains queued for retry."""
    from . import home_assistant
    settings=_settings(db)
    if settings.get('ha_enabled')!='1':
        with db:
            db.execute('BEGIN IMMEDIATE')
            settings=_settings(db)
            if settings.get('ha_enabled')!='1':
                db.execute('DELETE FROM pending_notifications')
                db.execute("DELETE FROM system_health WHERE name='ha_delivery'")
                return 0
    sender=home_assistant.send if send is None else send
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('invalid notification limit')
    if not isinstance(budget,(int,float)) or isinstance(budget,bool) or not 5<budget<=60:raise ValueError('invalid notification budget')
    deadline=monotonic()+budget
    delivered=0;attempted=0;failed_sources=set()
    # Do not start a request without its full normal timeout: shortening the
    # last request could turn a healthy but slow server into a false alarm.
    while attempted<limit and deadline-monotonic()>=5:
        # A camera slug can equal a system source such as 'storage'. Separate
        # their queues while preserving order within each camera or system source.
        rows=db.execute("""SELECT id,source,state,camera_id FROM pending_notifications
                           WHERE id IN (SELECT MIN(id) FROM pending_notifications GROUP BY source,camera_id)
                           ORDER BY id LIMIT ?""",(limit+len(failed_sources),)).fetchall()
        eligible=[row for row in rows if (row[1],row[3]) not in failed_sources]
        if not eligible:break
        for nid,slug,state,camera_id in eligible:
            if attempted>=limit:break
            remaining=deadline-monotonic()
            if remaining<5:break
            if _settings(db)!=settings:return delivered
            attempted+=1
            try:
                if send is None:sender(settings.get('ha_url',''),settings.get('ha_token',''),camera_id=slug,state=state,timeout=5)
                else:sender(settings.get('ha_url',''),settings.get('ha_token',''),camera_id=slug,state=state)
            except ValueError:
                if not _finish_delivery(db,settings,nid,failed=True):return delivered
                failed_sources.add((slug,camera_id));continue
            except (home_assistant.NotificationError,OSError):
                # A global outage should not consume one timeout per source.
                if not _finish_delivery(db,settings,nid,failed=True):return delivered
                _delivery_state(db,'ha_delivery_error',settings)
                return delivered
            if not _finish_delivery(db,settings,nid,failed=False):return delivered
            delivered+=1
    if failed_sources:_delivery_state(db,'ha_delivery_error',settings)
    elif attempted:_delivery_state(db,'ha_delivery_ok',settings)
    return delivered


def update_storage(db: sqlite3.Connection, free_bytes: int, minimum: int, *, now: int | None = None) -> bool:
    if type(free_bytes) is not int or type(minimum) is not int or free_bytes<0 or minimum<0:raise ValueError('invalid space')
    now=int(time.time()) if now is None else now
    state='disk_low' if free_bytes<minimum else 'disk_ok'
    with db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute("SELECT state FROM system_health WHERE name='storage'").fetchone()
        db.execute("INSERT INTO system_health(name,state,changed_at) VALUES('storage',?,?) ON CONFLICT(name) DO UPDATE SET changed_at=CASE WHEN state<>excluded.state THEN excluded.changed_at ELSE changed_at END,state=excluded.state",(state,now))
        if (not previous and state=='disk_low') or (previous and previous[0]!=state):
            db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,'storage',?,?)",(state,now))
    return state=='disk_low'


def storage_unavailable(db: sqlite3.Connection, *, now: int | None = None) -> None:
    now=int(time.time()) if now is None else now
    with db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute("SELECT state FROM system_health WHERE name='storage'").fetchone()
        db.execute("INSERT INTO system_health(name,state,changed_at) VALUES('storage','disk_unavailable',?) ON CONFLICT(name) DO UPDATE SET changed_at=CASE WHEN state<>excluded.state THEN excluded.changed_at ELSE changed_at END,state=excluded.state",(now,))
        if not previous or previous[0]!='disk_unavailable':
            db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,'storage','disk_unavailable',?)",(now,))


def rotation_state(db: sqlite3.Connection,state: str, *, now: int | None=None) -> None:
    if state not in ('rotation_blocked','rotation_error','rotation_ok'):raise ValueError('invalid rotation state')
    now=int(time.time()) if now is None else now
    def update():
        previous=db.execute("SELECT state FROM system_health WHERE name='rotation'").fetchone()
        db.execute("INSERT INTO system_health(name,state,changed_at) VALUES('rotation',?,?) ON CONFLICT(name) DO UPDATE SET changed_at=CASE WHEN state<>excluded.state THEN excluded.changed_at ELSE changed_at END,state=excluded.state",(state,now))
        if (not previous and state!='rotation_ok') or (previous and previous[0]!=state):
            db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,'rotation',?,?)",(state,now))
    if db.in_transaction:
        update();return
    with db:
        db.execute('BEGIN IMMEDIATE');update()
