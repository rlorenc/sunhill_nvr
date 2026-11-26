"""Rotation history and asynchronous, non-destructive preview requests."""
from __future__ import annotations

import json
import sqlite3
import time

from . import health_service,retention


def record(db: sqlite3.Connection, state: str, *, before: int | None=None, after: int | None=None,
           files: int=0, protected_count: int=0, protected_bytes: int=0, now: int | None=None) -> None:
    if state not in ('rotation_ok','rotation_blocked','rotation_error') or min(files,protected_count,protected_bytes)<0:
        raise ValueError('invalid rotation record')
    now=int(time.time()) if now is None else now
    with db:
        db.execute('BEGIN IMMEDIATE')
        last=db.execute('SELECT created,state,files_count,protected_count FROM rotation_history ORDER BY id DESC LIMIT 1').fetchone()
        # Repeated failures with no deletions get a periodic entry, not one per minute.
        if not last or files or (state,protected_count)!=(last[1],last[3]) or now<last[0] or now-last[0]>=900:
            db.execute('INSERT INTO rotation_history(created,state,before_bytes,after_bytes,files_count,protected_count,protected_bytes) VALUES(?,?,?,?,?,?,?)',
                       (now,state,before,after,files,protected_count,protected_bytes))
            db.execute('DELETE FROM rotation_history WHERE id NOT IN (SELECT id FROM rotation_history ORDER BY id DESC LIMIT 10000)')
        health_service.rotation_state(db,state,now=now)


def enqueue_preview(db: sqlite3.Connection,user_id: int, *, now: int | None=None, authorize=None, on_enqueue=None) -> int:
    if type(user_id) is not int or user_id<=0:raise ValueError('invalid user')
    now=int(time.time()) if now is None else now
    db.execute('BEGIN IMMEDIATE')
    try:
        if authorize is not None:authorize()
        if db.execute("SELECT 1 FROM rotation_preview_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise RuntimeError('rotation preview already running')
        db.execute("DELETE FROM rotation_preview_jobs WHERE status IN ('done','failed') AND id NOT IN (SELECT id FROM rotation_preview_jobs ORDER BY id DESC LIMIT 1000)")
        result=db.execute("INSERT INTO rotation_preview_jobs(user_id,status,created,updated) VALUES(?,'queued',?,?)",(user_id,now,now))
        if on_enqueue is not None:on_enqueue()
        db.commit();return int(result.lastrowid)
    except BaseException:
        db.rollback();raise


def run_preview(db: sqlite3.Connection, preview, *, now: int | None=None, recover_running: bool=False) -> int | None:
    """Run one queued preview. The callable must not delete files."""
    now=int(time.time()) if now is None else now
    db.execute('BEGIN IMMEDIATE')
    try:
        if recover_running:
            # Only the caller holding the separate preview worker lock may use
            # this mode: any previous worker has already released that lock.
            db.execute("UPDATE rotation_preview_jobs SET status='failed',error='Podgląd został przerwany',updated=? WHERE status='running'",(now,))
        else:
            db.execute("UPDATE rotation_preview_jobs SET status='failed',error='Podgląd został przerwany',updated=? WHERE status='running' AND updated<?",(now,now-3600))
        if db.execute("SELECT 1 FROM rotation_preview_jobs WHERE status='running' LIMIT 1").fetchone():
            db.commit();return None
        row=db.execute("SELECT id FROM rotation_preview_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:db.commit();return None
        jid=row[0];db.execute("UPDATE rotation_preview_jobs SET status='running',updated=? WHERE id=?",(now,jid));db.commit()
    except BaseException:
        db.rollback();raise
    try:
        result=preview()
        if not result.preview:raise RuntimeError('preview unexpectedly allowed deletion')
        data={'before':result.before,'after':result.after,'files_count':len(result.selected),
              'files':list(result.selected[:500]),'truncated':len(result.selected)>500,
              'protected_count':result.protected_count,'protected_bytes':result.protected_bytes,
              'unknown_time_count':result.unknown_time_count,'unknown_time_bytes':result.unknown_time_bytes,
              'reached_target':result.reached_target}
        with db:db.execute("UPDATE rotation_preview_jobs SET status='done',result_json=?,updated=? WHERE id=? AND status='running'",(json.dumps(data,separators=(',',':')),int(time.time()),jid))
    except retention.RetentionBusy:
        with db:db.execute("UPDATE rotation_preview_jobs SET status='queued',updated=? WHERE id=? AND status='running'",(int(time.time()),jid))
    except Exception:
        with db:db.execute("UPDATE rotation_preview_jobs SET status='failed',error='Nie można przygotować podglądu rotacji',updated=? WHERE id=? AND status='running'",(int(time.time()),jid))
    return jid
