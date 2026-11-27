"""Durable administrator requests for the existing single index worker."""
from __future__ import annotations
from datetime import date,timedelta
import json
import logging
import secrets
import time
from dataclasses import asdict

KEY='archive_index_status'


def read(db):
    row=db.execute('SELECT value FROM system_settings WHERE key=?',(KEY,)).fetchone()
    return json.loads(row[0]) if row else {'state':'idle'}


def save(db,value):
    db.execute('INSERT INTO system_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(KEY,json.dumps(value,separators=(',',':'))))


def scope(db,camera,start,end):
    if camera is not None and (not isinstance(camera,str) or not db.execute('SELECT 1 FROM cameras WHERE slug=?',(camera,)).fetchone()):raise ValueError('invalid camera')
    if start is None and end is None:return None
    if not isinstance(start,str) or not isinstance(end,str):raise ValueError('invalid dates')
    a,b=date.fromisoformat(start),date.fromisoformat(end)
    if a.isoformat()!=start or b.isoformat()!=end or not 0<=(b-a).days<=3660:raise ValueError('invalid dates')
    return {a+timedelta(days=i) for i in range((b-a).days+1)}


def request(db,camera=None,start=None,end=None):
    scope(db,camera,start,end)
    current=read(db)
    if current['state'] in ('queued','running'):return current
    value={'id':secrets.token_hex(16),'state':'queued','camera':camera,'start':start,'end':end,'updated':int(time.time())}
    save(db,value)
    return value


def pause(db):
    value=read(db)
    if value['state'] in ('queued','running'):
        value.update(state='paused',updated=int(time.time()))
        save(db,value)
    return value


def update(db,job_id,**fields):
    with db:
        db.execute('BEGIN IMMEDIATE')
        value=read(db)
        if value.get('id')!=job_id or value['state'] not in ('queued','running'):return False
        value.update(fields,updated=int(time.time()))
        save(db,value)
    return True


def run(config,db,*,automatic,should_stop):
    from . import workers
    with db:
        db.execute('BEGIN IMMEDIATE')
        job=read(db)
        if job['state'] not in ('queued','running'):
            if not automatic or job['state']=='paused':return None
            job=request(db)
    ident=job['id']
    if not update(db,ident,state='running',scanned=0,accepted=0,unchanged=0,failed=0,skipped=0,removed=0,empty_removed=0,empty_deferred=0,started=int(time.time()),error=None):return None
    def stop():
        if should_stop() or not workers.writes_permitted():return True
        current=read(db)
        return current.get('id')!=ident or current['state']!='running'
    next_recent=time.monotonic()+60
    def progress(report):
        nonlocal next_recent
        update(db,ident,**asdict(report))
        # A large historical import must not starve normal recording indexing.
        # RTSP segments still enter via index_published independently.
        if time.monotonic()>=next_recent and not stop():
            try:workers.index_once(config,db,full=False,should_stop=stop)
            except Exception as exc:logging.error('Sunhill recent index error: %s',type(exc).__name__)
            next_recent=time.monotonic()+60
    try:
        dates=scope(db,job.get('camera'),job.get('start'),job.get('end'))
        result=workers.index_once(config,db,camera=job.get('camera'),dates=dates,should_stop=stop,progress=progress)
        if not result.interrupted:update(db,ident,state='done',finished=int(time.time()),**asdict(result))
        return result
    except Exception as exc:
        update(db,ident,state='error',error=type(exc).__name__)
        raise
