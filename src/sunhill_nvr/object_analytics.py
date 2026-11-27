"""Camera-native object observations and bounded local geometry rules.

This module does not infer identities from native metadata or create frames when camera data is absent.
Tracks are scoped to one RTSP session; no cross-camera re-identification.
"""
from __future__ import annotations
import contextlib
import json
import math
import re
import sqlite3
import threading
import time
import secrets
import logging
from . import database, workers, onvif_metadata, security

RULE_TYPES=('line_cross','intrusion','abandoned')
MAX_CAMERAS=32
KINDS=set(onvif_metadata.CLASSES.values())|{'object'}


def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def intersects(a,b,c,d):
    eps=1e-9
    def contains(p,q,r):return min(p[0],q[0])-eps<=r[0]<=max(p[0],q[0])+eps and min(p[1],q[1])-eps<=r[1]<=max(p[1],q[1])+eps
    x,y,z,w=cross(a,b,c),cross(a,b,d),cross(c,d,a),cross(c,d,b)
    if abs(x)<=eps and contains(a,b,c) or abs(y)<=eps and contains(a,b,d) or abs(z)<=eps and contains(c,d,a) or abs(w)<=eps and contains(c,d,b):return True
    return x*y<0 and z*w<0


def inside(point,polygon):
    x,y=point;hit=False
    for i,a in enumerate(polygon):
        b=polygon[(i+1)%len(polygon)]
        if abs(cross(a,b,point))<1e-9 and min(a[0],b[0])<=x<=max(a[0],b[0]) and min(a[1],b[1])<=y<=max(a[1],b[1]):return True
        if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:hit=not hit
    return hit


def validate(data):
    if not isinstance(data,dict) or not {'enabled','rules'}<=set(data)<={'enabled','rules','retention_days','source_mode'} or data.get('source_mode','auto') not in ('auto','local') or type(data['enabled']) is not bool or ('retention_days' in data and (type(data['retention_days']) is not int or not 1<=data['retention_days']<=30)) or not isinstance(data['rules'],list) or len(data['rules'])>16:raise ValueError('invalid analytics options')
    ids=set()
    for r in data['rules']:
        if not isinstance(r,dict) or set(r)!={'id','name','type','points','kind','dwell','direction'}:raise ValueError('invalid rule')
        if not isinstance(r['id'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,32}',r['id']) or r['id'] in ids:raise ValueError('invalid rule id')
        ids.add(r['id'])
        if not isinstance(r['name'],str) or not 1<=len(r['name'])<=80 or any(ord(c)<32 for c in r['name']):raise ValueError('invalid rule name')
        if r['type'] not in RULE_TYPES or r['kind'] not in KINDS|{'all'} or type(r['dwell']) is not int or not 0<=r['dwell']<=600 or r['direction'] not in ('both','positive','negative'):raise ValueError('invalid rule parameters')
        points=r['points'];line=r['type']=='line_cross'
        if not isinstance(points,list) or (len(points)!=2 if line else not 3<=len(points)<=32):raise ValueError('invalid geometry')
        if any(not isinstance(p,list) or len(p)!=2 or any(type(n) not in (int,float) or not 0<=n<=1 for n in p) for p in points):raise ValueError('invalid coordinates')
        if len({tuple(p) for p in points})!=len(points):raise ValueError('repeated vertices')
        if not line:
            area=abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(points,points[1:]+points[:1])))/2
            if area<1e-5:raise ValueError('empty zone')
            edges=list(zip(points,points[1:]+points[:1]))
            for i,(a,b) in enumerate(edges):
                for j,(c,d) in enumerate(edges):
                    if j>i and j not in (i+1,) and not (i==0 and j==len(edges)-1) and intersects(a,b,c,d):raise ValueError('self-intersecting zone')
        elif math.dist(*points)<.01:raise ValueError('line too short')
        if r['type']=='abandoned' and (r['kind'] not in ('package','object','all') or r['dwell']<10):raise ValueError('abandoned rule requires stationary object/package and at least 10 s')
    return data


def options(db,cid):
    row=db.execute('SELECT enabled,retention_days,rules_json,source_mode FROM camera_metadata_options WHERE camera_id=?',(cid,)).fetchone()
    result={'enabled':bool(row[0]),'retention_policy':'archive','rules':[],'source_mode':row[3]} if row else {'enabled':True,'retention_policy':'archive','rules':[],'source_mode':'auto'}
    status=db.execute('SELECT state,checked_ms FROM camera_metadata_status WHERE camera_id=?',(cid,)).fetchone()
    result['status']={'state':status[0],'checked_ms':status[1]} if status else None
    return result


def save_options(db,cid,data):
    validate(data)
    if data['rules']:raise ValueError('spatial event rules retired')
    old=options(db,cid)['source_mode'];mode=data.get('source_mode',old)
    db.execute('INSERT INTO camera_metadata_options(camera_id,enabled,retention_days,rules_json,source_mode) VALUES(?,?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET enabled=excluded.enabled,retention_days=excluded.retention_days,rules_json=excluded.rules_json,source_mode=excluded.source_mode',(cid,int(data['enabled']),7,json.dumps(data['rules'],separators=(',',':')),mode))
    if not data['enabled'] or mode!=old:
        db.execute('DELETE FROM camera_metadata_latest WHERE camera_id=?',(cid,));db.execute('DELETE FROM camera_metadata_status WHERE camera_id=?',(cid,))


def center(obj):
    x,y,w,h=obj['box'];return (x+w/2,y+h/2)


class RuleEngine:
    def __init__(self):self.states={};self.rules=None
    def process(self,rules,stamp,objects):
        if rules!=self.rules:self.states={};self.rules=rules
        active=set();events=[]
        people=[center(o) for o in objects if o['kind']=='person']
        for rule in rules:
            typ=rule['type'];points=rule['points'];delay=rule['dwell']*1000
            for obj in objects:
                if typ=='abandoned' and obj['kind'] not in ('object','package'):continue
                if rule['kind']!='all' and obj['kind']!=rule['kind']:continue
                key=(rule['id'],obj['id']);active.add(key);p=center(obj);state=self.states.get(key)
                if state is None or not 0<stamp-state['last']<=3000:
                    state={'point':p,'last':stamp,'entered':stamp,'fired':False,'inside':typ!='line_cross' and inside(p,points),'side':0,'stable':stamp,'anchor':p,'near_person':False}
                    self.states[key]=state
                    if typ=='line_cross':state['side']=self.side(points,p)
                    if typ=='abandoned':state['near_person']=any(math.dist(p,q)<=.15 for q in people)
                    # Initial observations cannot prove entry or crossing.
                    if typ=='intrusion' and state['inside']:state['entered']=None
                    continue
                fire=False
                if typ=='line_cross':
                    side=self.side(points,p);old=state['side']
                    if side and old and side!=old and intersects(points[0],points[1],state['point'],p):
                        fire=rule['direction']=='both' or rule['direction']==('positive' if side>0 else 'negative')
                    if side:state['side']=side;state['point']=p
                elif typ=='intrusion':
                    contained=inside(p,points)
                    if contained and not state['inside']:state['entered']=stamp;state['fired']=False
                    if not contained:state['entered']=None;state['fired']=False
                    if contained and state['entered'] is not None and not state['fired'] and stamp-state['entered']>=delay:fire=True;state['fired']=True
                    state['inside']=contained
                elif typ=='abandoned':
                    contained=inside(p,points);near=any(math.dist(p,q)<=.15 for q in people)
                    if not contained or math.dist(p,state['anchor'])>.025:
                        state.update(stable=stamp,anchor=p,near_person=near,fired=False)
                    if near:state['near_person']=True;state['stable']=stamp;state['fired']=False
                    if contained and state['near_person'] and not near and not state['fired'] and stamp-state['stable']>=delay:fire=True;state['fired']=True
                state['last']=stamp
                if fire:events.append((rule,obj))
        # A missing track must not bridge a gap to fabricate a crossing.
        self.states={k:v for k,v in self.states.items() if k in active}
        return events
    @staticmethod
    def side(points,p):
        value=cross(points[0],points[1],p)
        return 1 if value>1e-5 else -1 if value< -1e-5 else 0


def status(db,cid,url,state,*,expected_mode=None):
    if not workers.writes_permitted():return False
    with db:
        db.execute('BEGIN IMMEDIATE')
        if expected_mode is not None and options(db,cid)['source_mode']!=expected_mode:return False
        if db.execute('SELECT d.main_url,c.enabled,COALESCE(o.enabled,1) FROM camera_details d JOIN cameras c ON c.id=d.camera_id LEFT JOIN camera_metadata_options o ON o.camera_id=c.id WHERE c.id=?',(cid,)).fetchone()!=(url,1,1):return False
        db.execute('INSERT INTO camera_metadata_status VALUES(?,?,?) ON CONFLICT(camera_id) DO UPDATE SET state=excluded.state,checked_ms=excluded.checked_ms',(cid,state,int(time.time()*1000)))
        if state!='active':db.execute('DELETE FROM camera_metadata_latest WHERE camera_id=?',(cid,))
    return True


def snapshot(db,cid,url,stamp,objects,session,engine,received=None, *, source="active",expected_mode=None):
    objects=[o for o in objects if o['kind']!='package' and o.get('label')!='foreground_change']
    received=int(time.time()*1000) if received is None else received
    # Camera clocks must agree with the NVR recording clock; do not move detections to invented times.
    if abs(stamp-received)>30000:return False
    if not workers.writes_permitted():raise InterruptedError()
    with db:
        db.execute('BEGIN IMMEDIATE')
        if expected_mode is not None and options(db,cid)['source_mode']!=expected_mode:raise InterruptedError()
        if db.execute('SELECT d.main_url,c.enabled,COALESCE(o.enabled,1) FROM camera_details d JOIN cameras c ON c.id=d.camera_id LEFT JOIN camera_metadata_options o ON o.camera_id=c.id WHERE c.id=?',(cid,)).fetchone()!=(url,1,1):raise InterruptedError()
        cfg=options(db,cid);current=db.execute('SELECT stamp_ms,objects_json,session FROM camera_metadata_latest WHERE camera_id=?',(cid,)).fetchone()
        if current and current[2]==session and stamp<=current[0]:return False
        raw=json.dumps(objects,separators=(',',':'))
        db.execute('INSERT INTO camera_metadata_latest VALUES(?,?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET stamp_ms=excluded.stamp_ms,received_ms=excluded.received_ms,objects_json=excluded.objects_json,session=excluded.session',(cid,stamp,received,raw,session))
        db.execute('INSERT INTO camera_metadata_status VALUES(?,?,?) ON CONFLICT(camera_id) DO UPDATE SET state=excluded.state,checked_ms=excluded.checked_ms',(cid,source,received))
        # At most one historical nonempty observation per second, plus the first empty observation.
        last=db.execute('SELECT stamp_ms,objects_json,session FROM camera_metadata_history WHERE camera_id=? ORDER BY stamp_ms DESC LIMIT 1',(cid,)).fetchone()
        def signature(items):
            return {(o['id'],o['kind'],o.get('plate_number'),o.get('label'),o.get('face_profile')) for o in items}
        if (last is None and objects) or (last and (last[2]!=session or signature(objects)!=signature(json.loads(last[1])) or (objects and stamp-last[0]>=1000))):
            db.execute('INSERT OR IGNORE INTO camera_metadata_history VALUES(?,?,?,?)',(cid,stamp,raw,session))
        # Retired spatial rules must not produce events, including rules retained
        # in a restored older SQLite snapshot. ANPR markers require a real number.
        for obj in objects:
            if not obj.get('plate_number'):continue
            seconds=stamp//1000
            db.execute('INSERT OR IGNORE INTO event_markers(camera_id,started_at,ended_at,event_type,source,last_seen) VALUES(?,?,?,?,?,?)',(cid,seconds,seconds,'plate','nvr-plate',seconds))
            break
    return True


def live(db,user,now=None):
    from . import face_profiles
    now=int(time.time()*1000) if now is None else now
    labels=face_profiles.labels(db) if user and user[2]=='admin' else None
    return {str(cid):{'stamp_ms':stamp,'objects':face_profiles.public_objects([o for o in json.loads(raw) if o['kind']!='package' and o.get('label')!='foreground_change'],labels),'session':session} for cid,stamp,raw,session in db.execute('SELECT l.camera_id,l.stamp_ms,l.objects_json,l.session FROM camera_metadata_latest l LEFT JOIN camera_metadata_options o ON o.camera_id=l.camera_id WHERE COALESCE(o.enabled,1)=1 AND l.received_ms BETWEEN ? AND ?',(now-3000,now+1000)) if security.may_access(db,user,cid,live=True)}


def history(db,cid,stamp,user=None):
    from . import face_profiles
    labels=face_profiles.labels(db) if user and user[2]=='admin' else None
    return [{'stamp_ms':t,'objects':face_profiles.public_objects([o for o in json.loads(raw) if o['kind']!='package' and o.get('label')!='foreground_change'],labels),'session':session} for t,raw,session in db.execute('SELECT stamp_ms,objects_json,session FROM camera_metadata_history WHERE camera_id=? AND stamp_ms BETWEEN ? AND ? ORDER BY stamp_ms LIMIT 64',(cid,stamp-5000,stamp+5000))]


def run_camera(config,cid,url,stop,mode="auto"):
    while not stop.is_set() and workers.writes_permitted():
        session=secrets.token_hex(8);engine=RuleEngine();last=0
        def ready():
            with contextlib.closing(database.open_db(config.database)) as db:
                if not status(db,cid,url,'streaming',expected_mode=mode):raise InterruptedError()
        def emit(stamp,objects):
            nonlocal last
            if stop.is_set() or not workers.writes_permitted():raise InterruptedError()
            now=time.monotonic()
            if now-last<.2:return
            last=now
            with contextlib.closing(database.open_db(config.database)) as db:snapshot(db,cid,url,stamp,objects,session,engine,expected_mode=mode)
        if mode=="auto":
            try:
                try:
                    with onvif_metadata.Client(url) as client:client.run(stop,emit,ready)
                except (OSError,ValueError):
                    # A camera may expose its analytics on a separate Media2
                    # profile. Never mix coordinate systems from another source.
                    if stop.is_set():raise InterruptedError()
                    from . import onvif_capabilities
                    with contextlib.closing(database.open_db(config.database)) as db:
                        target=db.execute('SELECT main_url,onvif_url,onvif_username,onvif_password,onvif_auth FROM camera_details WHERE camera_id=? AND main_url=?',(cid,url)).fetchone()
                    if target and target[1]:
                        for metadata_url in onvif_capabilities.metadata_streams(target):
                            if stop.is_set():raise InterruptedError()
                            session=secrets.token_hex(8);engine=RuleEngine();last=0
                            try:
                                with onvif_metadata.Client(metadata_url) as client:client.run(stop,emit,ready)
                                break
                            except (OSError,ValueError):continue
            except InterruptedError:break
            except (OSError,ValueError,sqlite3.Error):pass
            finally:
                try:
                    with contextlib.closing(database.open_db(config.database)) as db:status(db,cid,url,'unavailable',expected_mode=mode)
                except (OSError,ValueError,sqlite3.Error):pass
        if stop.is_set() or not workers.writes_permitted():break
        # Native metadata takes precedence. A local visual fallback is used
        # only between native connection attempts, with a separate track session.
        from . import local_analytics
        try:
            with contextlib.closing(database.open_db(config.database)) as db:
                row=db.execute('SELECT c.slug FROM cameras c JOIN camera_details d ON d.camera_id=c.id WHERE c.id=? AND c.enabled=1 AND d.main_url=?',(cid,url)).fetchone()
            if not row:break
            local_session=secrets.token_hex(8);local_engine=RuleEngine()
            def emit_local(stamp,objects):
                if stop.is_set() or not workers.writes_permitted():raise InterruptedError()
                with contextlib.closing(database.open_db(config.database)) as db:
                    snapshot(db,cid,url,stamp,objects,local_session,local_engine,source='local',expected_mode=mode)
            def template_provider(model_id):
                from . import face_profiles
                with contextlib.closing(database.open_db(config.database)) as db:return face_profiles.active_templates(db,model_id)
            if not local_analytics.fallback(row[0],stop,emit_local,factory=lambda:local_analytics.Detector(template_provider)):stop.wait(20)
        except InterruptedError:break
        except (OSError,ValueError,sqlite3.Error,RuntimeError) as exc:
            logging.warning('Local analytics fallback: %s',type(exc).__name__);stop.wait(20)
        finally:
            try:
                with contextlib.closing(database.open_db(config.database)) as db:status(db,cid,url,'unavailable',expected_mode=mode)
            except (OSError,ValueError,sqlite3.Error):pass


def supervise(config,stop):
    children={}
    try:
        while not stop.is_set():
            try:
                with contextlib.closing(database.open_db(config.database)) as db:
                    wanted={cid:(url,mode) for cid,url,mode in db.execute("SELECT c.id,d.main_url,COALESCE(o.source_mode,'auto') FROM cameras c JOIN camera_details d ON d.camera_id=c.id LEFT JOIN camera_metadata_options o ON o.camera_id=c.id WHERE c.enabled=1 AND COALESCE(o.enabled,1)=1 ORDER BY c.id LIMIT ?",(MAX_CAMERAS,))} if workers.writes_permitted() else {}
                for cid,(spec,cancel,thread) in list(children.items()):
                    if wanted.get(cid)!=spec:cancel.set()
                    if not thread.is_alive():del children[cid]
                for cid,(url,mode) in wanted.items():
                    if cid in children or len(children)>=MAX_CAMERAS:continue
                    cancel=threading.Event();thread=threading.Thread(target=run_camera,args=(config,cid,url,cancel,mode),daemon=True);children[cid]=((url,mode),cancel,thread);thread.start()
            except (OSError,ValueError,sqlite3.Error) as exc:logging.warning('ONVIF metadata worker: %s',type(exc).__name__)
            stop.wait(5)
    finally:
        for _,cancel,_ in children.values():cancel.set()
