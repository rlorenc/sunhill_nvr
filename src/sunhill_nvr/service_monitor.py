"""Monitor fixed Sunhill systemd services without running privileged commands."""
from __future__ import annotations

import sqlite3
import subprocess
import time
from .upgrade_system import SERVICES
from . import monitor


class MonitorError(RuntimeError):pass


def query(*, run=subprocess.run, monotonic=time.monotonic, budget: float=15) -> dict[str,str]:
    if not isinstance(budget,(int,float)) or isinstance(budget,bool) or not 0<budget<=60:raise ValueError('invalid monitor budget')
    states={}
    deadline=monotonic()+budget
    try:
        for service in SERVICES:
            remaining=deadline-monotonic()
            if remaining<=0:raise MonitorError('service state query timed out')
            result=run(['/usr/bin/systemctl','show','--property=ActiveState','--value',service],
                       stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=min(5,remaining))
            value=result.stdout.decode('ascii').strip()
            if result.returncode!=0 or value not in ('active','inactive','failed','activating','deactivating','reloading'):
                raise MonitorError('service state unavailable')
            states[service]=value
    except (OSError,UnicodeError,AttributeError,TypeError,subprocess.SubprocessError) as exc:raise MonitorError('service state unavailable') from exc
    return states


def _monitor_status(db: sqlite3.Connection, available: bool, now: int) -> None:
    state='monitor_ok' if available else 'monitor_error'
    with db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute("SELECT state FROM system_health WHERE name='service_monitor'").fetchone()
        db.execute("INSERT INTO system_health(name,state,changed_at) VALUES('service_monitor',?,?) ON CONFLICT(name) DO UPDATE SET changed_at=CASE WHEN state<>excluded.state THEN excluded.changed_at ELSE changed_at END,state=excluded.state",(state,now))
        if (not previous and not available) or (previous and previous[0]!=state):
            db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,'service_monitor',?,?)",(state,now))


def check(db: sqlite3.Connection, *, now: int | None=None, inspect=query) -> None:
    now=int(time.time()) if now is None else now
    try:
        states=inspect()
        if set(states)!=set(SERVICES) or any(s not in ('active','inactive','failed','activating','deactivating','reloading') for s in states.values()):
            raise MonitorError('incomplete service state')
    except (MonitorError,OSError):
        _monitor_status(db,False,now);return
    _monitor_status(db,True,now)
    with db:
        db.execute('BEGIN IMMEDIATE')
        for name,raw in states.items():
            old=db.execute('SELECT state,failures,checked_at FROM service_health WHERE name=?',(name,)).fetchone()
            previous=old[0] if old else 'unknown';failures=old[1] if old else 0
            if raw=='active':state='active';failures=0
            elif raw in ('inactive','failed'):
                failures=min(100,failures+1)
                stale=bool(old and not monitor.sample_fresh(old[2],now,monitor.HEALTH_MAX_AGE))
                state='inactive' if failures>=2 else 'unknown' if stale and previous=='active' else previous
            else:state=previous
            # A transitional systemd state does not confirm the previous result.
            # Keep its old timestamp so it eventually becomes unknown and a
            # subsequent confirmed failure can be reported again.
            checked_at=now if raw=='active' or (raw in ('inactive','failed') and state!='active') or not old else old[2]
            db.execute('INSERT INTO service_health(name,state,failures,checked_at,changed_at) VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET state=excluded.state,failures=excluded.failures,checked_at=excluded.checked_at,changed_at=CASE WHEN state<>excluded.state THEN excluded.changed_at ELSE changed_at END',(name,state,failures,checked_at,now))
            reconfirmed=bool(old and raw in ('inactive','failed') and state=='inactive' and previous=='inactive' and not monitor.sample_fresh(old[2],now,monitor.HEALTH_MAX_AGE))
            if (state!=previous and (state=='inactive' or (state=='active' and previous=='inactive'))) or reconfirmed:
                db.execute('INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,?,?,?)',(name,'service_up' if state=='active' else 'service_down',now))
