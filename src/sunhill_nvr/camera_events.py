"""Camera-native alarm collection, without decoding or storing video.

Baichuan alarm state (31/33, 93 keepalive) and CGI GetAiState fallback.
One bounded independent session per enabled standalone camera. Times are
server receipt times, not frame timestamps. No fabricated object positions.
"""
from __future__ import annotations
import contextlib
import json
import logging
import math
import select
import sqlite3
import threading
import time
from . import database, workers
from .camera_control import Reolink, Unsupported
from .baichuan_control import Baichuan

TYPES=('person','vehicle','animal','motion','face','visitor','tamper','plate')
ALIASES={'people':'person','person':'person','vehicle':'vehicle','dog_cat':'animal','animal':'animal','face':'face','visitor':'visitor','tamper':'tamper','md':'motion'}
DEFAULT_MASK=255
SOURCE='reolink'
MAX_CAMERAS=32


def normalize_box(value):
    if not isinstance(value,list) or len(value)!=4 or any(type(n) not in (int,float) or not 0<=n<=1 or not math.isfinite(n) for n in value):raise ValueError('invalid box')
    x,y,w,h=value
    if min(x,y)<0 or min(w,h)<=0 or x+w>1.000000001 or y+h>1.000000001:raise ValueError('invalid box')
    return value


def options(db,cid):
    row=db.execute('SELECT enabled,mask,retention_days FROM camera_event_options WHERE camera_id=?',(cid,)).fetchone()
    enabled,mask,days=row or (1,DEFAULT_MASK,365)
    status=db.execute('SELECT transport,state,checked_at,supported FROM camera_event_status WHERE camera_id=?',(cid,)).fetchone()
    from . import native_capabilities
    supported=native_capabilities.supported(db,cid)
    return {'enabled':bool(enabled),'types':list(TYPES),'supported':[t for t in TYPES if t in supported],'retention_policy':'archive',
            'status':{'transport':status[0],'state':status[1],'checked_at':status[2],'supported':json.loads(status[3])} if status else None}


def save_options(db,cid,data):
    if set(data) not in ({'enabled','types'},{'enabled','types','retention_days'}) or type(data['enabled']) is not bool or not isinstance(data['types'],list) or any(not isinstance(t,str) or t not in TYPES for t in data['types']) or len(set(data['types']))!=len(data['types']) or ('retention_days' in data and (type(data['retention_days']) is not int or not 1<=data['retention_days']<=3650)):raise ValueError('invalid options')
    mask=DEFAULT_MASK
    db.execute('INSERT INTO camera_event_options VALUES(?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET enabled=excluded.enabled,mask=excluded.mask,retention_days=excluded.retention_days',(cid,int(data['enabled']),mask,365))


def parse_push(root):
    if root is None:return {}
    for alarm in root.findall('.//AlarmEvent'):
        if alarm.findtext('channelId')!='0':continue
        status=alarm.findtext('status');ai=alarm.findtext('AItype')
        result={}
        if status is not None:
            active=set(status.lower().replace(' ','').split(','))
            result.update({t:raw in active for raw,t in [('md','motion'),('visitor','visitor'),('tamper','tamper')]})
        if ai is not None:
            active={ALIASES[v] for v in ai.lower().replace(' ','').split(',') if v in ALIASES}
            result.update({t:t in active for t in ('person','vehicle','animal','face')})
        return result
    return {}


def parse_cgi(value):
    if not isinstance(value,dict) or value.get('channel',0)!=0:return {}
    result={}
    for raw,kind in ALIASES.items():
        row=value.get(raw)
        if isinstance(row,dict) and row.get('support')==1 and row.get('alarm_state') in (0,1):result[kind]=bool(row['alarm_state'])
    return result


def snapshot(db,cid,url,states,transport,now=None):
    """Persist transitions atomically, recheck camera identity and options.

    Open events are closed at last observation on a gap, not fabricated as
    continuous detection across a restart. No repeated writes for stable idle.
    """
    now=int(time.time() if now is None else now)
    if not workers.writes_permitted():return False
    with db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT c.enabled,d.main_url FROM cameras c JOIN camera_details d ON c.id=d.camera_id WHERE c.id=?',(cid,)).fetchone()
        if row!=(1,url):return False
        cfg=options(db,cid)
        if not cfg['enabled']:return False
        active={r[0]:(r[1],r[2],r[3]) for r in db.execute("SELECT event_type,id,started_at,last_seen FROM event_markers WHERE camera_id=? AND source=? AND ended_at IS NULL",(cid,SOURCE))}
        selected=set(cfg['types'])
        for kind,(eid,start,last) in active.items():
            if kind not in selected or states.get(kind) is False or (last is not None and now-last>90):
                end=max(start,last or start) if kind not in selected or (last is not None and now-last>90) else max(start,now)
                db.execute('UPDATE event_markers SET ended_at=? WHERE id=?',(end,eid))
        # Re-query after closures. A single event per camera/type at a time.
        active={r[0]:(r[1],r[2]) for r in db.execute("SELECT event_type,id,last_seen FROM event_markers WHERE camera_id=? AND source=? AND ended_at IS NULL",(cid,SOURCE))}
        for kind,state in states.items():
            if kind not in selected or state is not True:continue
            if kind in active:
                eid,last=active[kind]
                if last is None or now-last>=15:db.execute('UPDATE event_markers SET last_seen=? WHERE id=?',(now,eid))
            else:db.execute('INSERT OR IGNORE INTO event_markers(camera_id,started_at,event_type,source,last_seen) VALUES(?,?,?,?,?)',(cid,now,kind,SOURCE,now))
        from . import native_capabilities
        native_capabilities.record(db,cid,({k for k,v in states.items() if v} if transport=='baichuan' else set(states)) & set(TYPES))
        previous=cfg['status']; supported=sorted((({k for k,v in states.items() if v} if transport=='baichuan' else set(states)) | set(previous['supported'] if previous and previous['transport']==transport else [])) & set(TYPES))
        if not previous or previous['state']!='active' or previous['transport']!=transport or now-previous['checked_at']>=30 or supported!=previous['supported']:
            db.execute('INSERT INTO camera_event_status VALUES(?,?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET transport=excluded.transport,state=excluded.state,checked_at=excluded.checked_at,supported=excluded.supported',(cid,transport,'active',now,json.dumps(supported)))
    return True


def offline(db,cid,url,state='unavailable'):
    if not workers.writes_permitted():return
    with db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT main_url FROM camera_details WHERE camera_id=?',(cid,)).fetchone()!=(url,):return
        db.execute("UPDATE event_markers SET ended_at=MAX(started_at,COALESCE(last_seen,started_at)) WHERE camera_id=? AND source=? AND ended_at IS NULL",(cid,SOURCE))
        db.execute('INSERT INTO camera_event_status VALUES(?,?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET state=excluded.state,checked_at=excluded.checked_at',(cid,'',state,int(time.time()),'[]'))


def push_session(url,stop,emit):
    client=Reolink(url)
    with Baichuan(client.ip,client.username,client.password) as native:
        states={}
        def pushed(root):
            states.update(parse_push(root))
            if states:emit(states.copy(),'baichuan')
        native.deadline=time.monotonic()+5
        native.request(31,push_channel=True,on_push=pushed)
        # Verify the transport even before the first alarm arrives.
        emit({},'baichuan'); next_ping=time.monotonic()+8
        while not stop.is_set() and workers.writes_permitted():
            if time.monotonic()>=next_ping:
                native.deadline=time.monotonic()+5;native.request(93,on_push=pushed)
                emit(states.copy(),'baichuan');next_ping=time.monotonic()+8
            if select.select([native.sock],[],[],1)[0]:
                native.deadline=time.monotonic()+5
                cmd,mid,status,kind,raw=native.read_frame()
                if cmd==33 and status in (0,200):pushed(native.decode_body(raw,mid))


def cgi_session(url,stop,emit):
    with Reolink(url) as client:
        if not isinstance(client,Reolink):raise Unsupported('CGI unavailable')
        while not stop.is_set() and workers.writes_permitted():
            client.deadline=time.monotonic()+7
            try:states=parse_cgi(client.get('GetAiState',action=0)['value'])
            except Unsupported:states={}
            try:
                md=client.get('GetMdState',action=0)['value'].get('state')
                if md in (0,1):states['motion']=bool(md)
            except Unsupported:pass
            if not states:raise Unsupported('no alarm states')
            emit(states,'cgi');stop.wait(3)


def run_camera(config,cid,url,stop):
    try:
        with contextlib.closing(database.open_db(config.database)) as db:offline(db,cid,url)
    except (OSError,ValueError,sqlite3.Error):pass
    def emit(states,transport):
        if stop.is_set():raise InterruptedError()
        with contextlib.closing(database.open_db(config.database)) as db:
            if not snapshot(db,cid,url,states,transport):raise InterruptedError()
    while not stop.is_set():
        if not workers.writes_permitted():stop.wait(2);continue
        for session in (push_session,cgi_session):
            if stop.is_set() or not workers.writes_permitted():break
            try:session(url,stop,emit)
            except InterruptedError:break
            except (OSError,ValueError,sqlite3.Error):pass
            finally:
                try:
                    with contextlib.closing(database.open_db(config.database)) as db:offline(db,cid,url)
                except (OSError,ValueError,sqlite3.Error):pass
        stop.wait(20)


def supervise(config,stop):
    """Run next to existing health worker, no extra install dependencies."""
    children={}
    try:
        while not stop.is_set():
            try:
                with contextlib.closing(database.open_db(config.database)) as db:
                    rows=db.execute('SELECT c.id,d.main_url FROM cameras c JOIN camera_details d ON c.id=d.camera_id LEFT JOIN camera_event_options o ON o.camera_id=c.id WHERE c.enabled=1 AND COALESCE(o.enabled,1)=1 ORDER BY c.id LIMIT ?',(MAX_CAMERAS,)).fetchall() if workers.writes_permitted() else []
                    wanted=dict(rows)
                for cid,(url,cancel,thread) in list(children.items()):
                    if wanted.get(cid)!=url:cancel.set()
                    if not thread.is_alive():del children[cid]
                for cid,url in wanted.items():
                    if cid in children or len(children)>=MAX_CAMERAS:continue
                    cancel=threading.Event();thread=threading.Thread(target=run_camera,args=(config,cid,url,cancel),daemon=True)
                    children[cid]=(url,cancel,thread);thread.start()
            except (OSError,ValueError,sqlite3.Error) as exc:logging.warning('Camera event worker: %s',type(exc).__name__)
            stop.wait(5)
    finally:
        for _,cancel,_ in children.values():cancel.set()
