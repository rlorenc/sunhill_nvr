"""Persistent, single-worker export queue (storage layer only)."""
from __future__ import annotations
import json
import math
import secrets
import sqlite3
import time
from dataclasses import dataclass

SCHEMA = '''
CREATE TABLE IF NOT EXISTS export_jobs (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, camera_id INTEGER NOT NULL,
 request_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed')),
 created INTEGER NOT NULL, updated INTEGER NOT NULL, worker TEXT, lease_until INTEGER,
 progress REAL NOT NULL DEFAULT 0, result_name TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_export_jobs_status ON export_jobs(status,created,id);
CREATE TABLE IF NOT EXISTS export_cancel_requests (
 job_id INTEGER PRIMARY KEY REFERENCES export_jobs(id) ON DELETE CASCADE,
 requested_at INTEGER NOT NULL
);
'''

def initialize(db: sqlite3.Connection):
    db.executescript(SCHEMA)
    db.commit()

def enqueue(db: sqlite3.Connection, user_id: int, camera_id: int, request: dict, *, now: int | None = None) -> int:
    if user_id <= 0 or camera_id <= 0 or not isinstance(request, dict):
        raise ValueError('invalid job')
    encoded = json.dumps(request, allow_nan=False, ensure_ascii=False, separators=(',', ':'))
    if len(encoded.encode('utf-8')) > 16000:
        raise ValueError('request too large')
    now = int(time.time()) if now is None else now
    with db:
        c=db.execute('INSERT INTO export_jobs(user_id,camera_id,request_json,status,created,updated) VALUES (?,?,?,\'queued\',?,?)',
                     (user_id,camera_id,encoded,now,now))
        return int(c.lastrowid)

@dataclass(frozen=True)
class Job:
    id: int
    user_id: int
    camera_id: int
    request: dict
    lease_token: str

def claim(db: sqlite3.Connection, *, now: int | None = None, lease_seconds: int = 120) -> Job | None:
    """Claim one job. BEGIN IMMEDIATE serializes competing workers."""
    now = int(time.time()) if now is None else now
    if not 10 <= lease_seconds <= 3600:
        raise ValueError('invalid lease')
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute("""UPDATE export_jobs SET status='failed',error=CASE WHEN EXISTS
          (SELECT 1 FROM export_cancel_requests c WHERE c.job_id=export_jobs.id)
          THEN 'Anulowano eksport' ELSE 'Przerwany eksport' END,
          worker=NULL,lease_until=NULL,updated=? WHERE status='running' AND lease_until<=?""",(now,now))
        if db.execute("SELECT 1 FROM export_jobs WHERE status='running' LIMIT 1").fetchone():
            db.commit(); return None
        row=db.execute("SELECT id,user_id,camera_id,request_json FROM export_jobs WHERE status='queued' ORDER BY created,id LIMIT 1").fetchone()
        if not row:
            db.commit();return None
        token=secrets.token_urlsafe(32)
        db.execute("UPDATE export_jobs SET status='running',worker=?,lease_until=?,updated=? WHERE id=? AND status='queued'",(token,now+lease_seconds,now,row[0]))
        db.commit()
        return Job(row[0],row[1],row[2],json.loads(row[3]),token)
    except BaseException:
        db.rollback();raise

def heartbeat(db: sqlite3.Connection, job: Job, progress: float, *, now: int | None = None, lease_seconds: int = 120) -> bool:
    if not math.isfinite(progress) or not 0 <= progress <= 1 or not 10 <= lease_seconds <= 3600:
        raise ValueError('invalid progress or lease')
    now = int(time.time()) if now is None else now
    with db:
        c=db.execute("""UPDATE export_jobs SET progress=?,updated=?,lease_until=? WHERE id=? AND status='running' AND worker=? AND lease_until>?
          AND NOT EXISTS (SELECT 1 FROM export_cancel_requests c WHERE c.job_id=export_jobs.id)""",(progress,now,now+lease_seconds,job.id,job.lease_token,now))
        return c.rowcount == 1

def reserve_result(db: sqlite3.Connection, job: Job, result_name: str, *, now: int | None = None) -> bool:
    """Record the output name before writing, so interrupted output is identifiable."""
    import re
    if not re.fullmatch(r'export-'+str(job.id)+r'-[a-f0-9]{16}\.mp4',result_name):raise ValueError('invalid result name')
    now=int(time.time()) if now is None else now
    with db:
        result=db.execute("UPDATE export_jobs SET result_name=?,updated=? WHERE id=? AND status='running' AND worker=? AND lease_until>? AND result_name IS NULL",(result_name,now,job.id,job.lease_token,now))
        return result.rowcount==1

def complete(db: sqlite3.Connection, job: Job, result_name: str, *, now: int | None = None, require_access: bool = False, require_reserved: bool = False) -> bool:
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}\.mp4',result_name):
        raise ValueError('unsafe result name')
    now = int(time.time()) if now is None else now
    with db:
        access=""" AND EXISTS (SELECT 1 FROM users u JOIN cameras c ON c.id=export_jobs.camera_id
            WHERE u.id=export_jobs.user_id AND u.enabled=1 AND (u.role='admin' OR EXISTS
            (SELECT 1 FROM camera_permissions p WHERE p.user_id=u.id AND p.camera_id=c.id AND p.may_download=1)))""" if require_access else ''
        reserved=' AND result_name=?' if require_reserved else ''
        parameters=(result_name,now,job.id,job.lease_token,now)+((result_name,) if require_reserved else ())
        c=db.execute("UPDATE export_jobs SET status='done',progress=1,result_name=?,worker=NULL,lease_until=NULL,updated=? WHERE id=? AND status='running' AND worker=? AND lease_until>? AND NOT EXISTS (SELECT 1 FROM export_cancel_requests c WHERE c.job_id=export_jobs.id)"+access+reserved,parameters)
        return c.rowcount == 1

FAILURE_MESSAGES={
    'generic':'Nie udało się przygotować pliku',
    'permission':'Użytkownik nie ma już uprawnienia do pobierania z tej kamery',
    'disk_space':'Za mało wolnego miejsca na przygotowanie eksportu',
    'storage':'Dysk nagrań jest niedostępny lub został odłączony',
    'source_changed':'Plik źródłowy zmienił się podczas eksportu',
    'no_recordings':'Brak nagrań w wybranym zakresie',
    'ambiguous_time':'Czas nagrania jest niejednoznaczny po zmianie czasu',
    'media':'Nie można odczytać lub przetworzyć nagrania',
    'cancelled':'Anulowano eksport',
}

def request_cancel(db: sqlite3.Connection, job_id: int, *, now: int | None = None) -> bool:
    """Request cancellation under the caller's existing write transaction."""
    if type(job_id) is not int or job_id < 1 or not db.in_transaction:
        raise ValueError('cancellation requires a write transaction')
    now=int(time.time()) if now is None else now
    row=db.execute('SELECT status,error FROM export_jobs WHERE id=?',(job_id,)).fetchone()
    if not row:return False
    if row[0]=='queued':
        db.execute("UPDATE export_jobs SET status='failed',error=?,updated=? WHERE id=? AND status='queued'",(FAILURE_MESSAGES['cancelled'],now,job_id))
        return True
    if row[0]=='running':
        db.execute('INSERT OR IGNORE INTO export_cancel_requests(job_id,requested_at) VALUES (?,?)',(job_id,now))
        return True
    return row[0]=='failed' and row[1]==FAILURE_MESSAGES['cancelled']

def cancel_requested(db: sqlite3.Connection, job_id: int) -> bool:
    return db.execute('SELECT 1 FROM export_cancel_requests WHERE job_id=?',(job_id,)).fetchone() is not None

def fail(db: sqlite3.Connection, job: Job, *, now: int | None = None, reason: str = 'generic') -> bool:
    if reason not in FAILURE_MESSAGES:raise ValueError('invalid failure reason')
    now = int(time.time()) if now is None else now
    with db:
        c=db.execute("""UPDATE export_jobs SET status='failed',error=CASE WHEN EXISTS
            (SELECT 1 FROM export_cancel_requests c WHERE c.job_id=export_jobs.id) THEN ? ELSE ? END,
            worker=NULL,lease_until=NULL,updated=? WHERE id=? AND status='running' AND worker=? AND lease_until>?""",
            (FAILURE_MESSAGES['cancelled'],FAILURE_MESSAGES[reason],now,job.id,job.lease_token,now))
        return c.rowcount == 1
