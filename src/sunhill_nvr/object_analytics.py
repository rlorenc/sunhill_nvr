"""Camera-native object observations.

This module does not infer identities from native metadata or create frames when camera data is absent.
Tracks are scoped to one RTSP session; no cross-camera re-identification.
"""
from __future__ import annotations
import contextlib
import json
import sqlite3
import threading
import time
import secrets
import logging
from . import database, workers, onvif_metadata, security, native_capabilities, baichuan_metadata, device_diagnosis

MAX_CAMERAS=32


def validate(data):
    if not isinstance(data,dict) or not {'enabled','rules'}<=set(data)<={'enabled','rules','retention_days','source_mode'} or type(data['enabled']) is not bool or data['rules']!=[] or data.get('source_mode','auto')!='auto' or ('retention_days' in data and (type(data['retention_days']) is not int or not 1<=data['retention_days']<=30)):
        raise ValueError('only camera-native metadata is supported')
    return data


def options(db,cid):
    row=db.execute('SELECT enabled,retention_days,rules_json,source_mode FROM camera_metadata_options WHERE camera_id=?',(cid,)).fetchone()
    result={'enabled':bool(row[0]),'retention_policy':'archive','rules':[],'source_mode':'auto'} if row else {'enabled':True,'retention_policy':'archive','rules':[],'source_mode':'auto'}
    status=db.execute('SELECT state,checked_ms FROM camera_metadata_status WHERE camera_id=?',(cid,)).fetchone()
    result['status']={'state':status[0],'checked_ms':status[1]} if status else None
    result['supported']=sorted(native_capabilities.supported(db,cid))
    return result


def save_options(db,cid,data):
    validate(data)
    mode='auto'
    db.execute('INSERT INTO camera_metadata_options(camera_id,enabled,retention_days,rules_json,source_mode) VALUES(?,?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET enabled=excluded.enabled,retention_days=excluded.retention_days,rules_json=excluded.rules_json,source_mode=excluded.source_mode',(cid,int(data['enabled']),7,json.dumps(data['rules'],separators=(',',':')),mode))
    if not data['enabled']:
        db.execute('DELETE FROM camera_metadata_latest WHERE camera_id=?',(cid,));db.execute('DELETE FROM camera_metadata_status WHERE camera_id=?',(cid,))


def status(db,cid,url,state,*,expected_fingerprint=None):
    if not workers.writes_permitted():return False
    with db:
        db.execute('BEGIN IMMEDIATE')
        if expected_fingerprint is not None and native_capabilities.key(db,cid)!=expected_fingerprint:return False
        if db.execute('SELECT d.main_url,c.enabled,COALESCE(o.enabled,1) FROM camera_details d JOIN cameras c ON c.id=d.camera_id LEFT JOIN camera_metadata_options o ON o.camera_id=c.id WHERE c.id=?',(cid,)).fetchone()!=(url,1,1):return False
        db.execute('INSERT INTO camera_metadata_status VALUES(?,?,?) ON CONFLICT(camera_id) DO UPDATE SET state=excluded.state,checked_ms=excluded.checked_ms',(cid,state,int(time.time()*1000)))
        if state!='active':db.execute('DELETE FROM camera_metadata_latest WHERE camera_id=?',(cid,))
    return True


def snapshot(db,cid,url,stamp,objects,session,received=None, *, source="active",expected_fingerprint=None):
    if source!='active':return False
    session='native:'+session.removeprefix('native:')
    objects=[o for o in objects if o['kind']!='package' and o.get('label')!='foreground_change']
    received=int(time.time()*1000) if received is None else received
    # Camera clocks must agree with the NVR recording clock; do not move detections to invented times.
    if abs(stamp-received)>30000:return False
    if not workers.writes_permitted():raise InterruptedError()
    with db:
        db.execute('BEGIN IMMEDIATE')
        if expected_fingerprint is not None and native_capabilities.key(db,cid)!=expected_fingerprint:raise InterruptedError()
        if db.execute('SELECT d.main_url,c.enabled,COALESCE(o.enabled,1) FROM camera_details d JOIN cameras c ON c.id=d.camera_id LEFT JOIN camera_metadata_options o ON o.camera_id=c.id WHERE c.id=?',(cid,)).fetchone()!=(url,1,1):raise InterruptedError()
        if objects:
            native_capabilities.record(db,cid,({'baichuan_boxes','baichuan_'+session.split(':')[2]+'_boxes'} if session.startswith(('native:baichuan:sub:','native:baichuan:main:')) else set())|{'boxes'}|{o['kind'] for o in objects if o['kind'] not in ('object','plate')}|({'plate'} if any(o.get('plate_number') for o in objects) else set()))
        current=db.execute('SELECT stamp_ms,objects_json,session FROM camera_metadata_latest WHERE camera_id=?',(cid,)).fetchone()
        if current and current[2]==session and stamp<=current[0]:return False
        raw=json.dumps(objects,separators=(',',':'))
        db.execute('INSERT INTO camera_metadata_latest VALUES(?,?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET stamp_ms=excluded.stamp_ms,received_ms=excluded.received_ms,objects_json=excluded.objects_json,session=excluded.session',(cid,stamp,received,raw,session))
        db.execute('INSERT INTO camera_metadata_status VALUES(?,?,?) ON CONFLICT(camera_id) DO UPDATE SET state=excluded.state,checked_ms=excluded.checked_ms',(cid,source,received))
        # At most one historical nonempty observation per second, plus the first empty observation.
        last=db.execute('SELECT stamp_ms,objects_json,session FROM camera_metadata_history WHERE camera_id=? ORDER BY stamp_ms DESC LIMIT 1',(cid,)).fetchone()
        def signature(items):
            return {(o['id'],o['kind'],o.get('plate_number'),o.get('label'),o.get('identity')) for o in items}
        if (last is None and objects) or (last and (last[2]!=session or signature(objects)!=signature(json.loads(last[1])) or (objects and stamp-last[0]>=1000))):
            db.execute('INSERT OR IGNORE INTO camera_metadata_history VALUES(?,?,?,?)',(cid,stamp,raw,session))
        # Retired spatial rules must not produce events, including rules retained
        # in a restored older SQLite snapshot. ANPR markers require a real number.
        for obj in objects:
            if not obj.get('plate_number'):continue
            seconds=stamp//1000
            db.execute('INSERT OR IGNORE INTO event_markers(camera_id,started_at,ended_at,event_type,source,last_seen) VALUES(?,?,?,?,?,?)',(cid,seconds,seconds,'plate','camera-plate',seconds))
            break
    return True


def public_objects(raw):
    # Only camera-native observations; never expose old locally generated face IDs.
    return [{k:v for k,v in o.items() if k not in ('face_profile','face_label')} for o in json.loads(raw) if o['kind']!='package' and o.get('label')!='foreground_change']

def live(db,user,now=None):
    now=int(time.time()*1000) if now is None else now
    return {str(cid):{'stamp_ms':stamp,'objects':public_objects(raw),'session':session} for cid,stamp,raw,session in db.execute("SELECT l.camera_id,l.stamp_ms,l.objects_json,l.session FROM camera_metadata_latest l LEFT JOIN camera_metadata_options o ON o.camera_id=l.camera_id JOIN cameras c ON c.id=l.camera_id WHERE c.enabled=1 AND COALESCE(o.enabled,1)=1 AND l.session LIKE 'native:%' AND l.received_ms BETWEEN ? AND ?",(now-3000,now+1000)) if security.may_access(db,user,cid,live=True)}

def history(db,cid,stamp,user=None):
    return [{'stamp_ms':t,'objects':public_objects(raw),'session':session} for t,raw,session in db.execute("SELECT stamp_ms,objects_json,session FROM camera_metadata_history WHERE camera_id=? AND stamp_ms BETWEEN ? AND ? AND session LIKE 'native:%' ORDER BY stamp_ms LIMIT 64",(cid,stamp-5000,stamp+5000))]


def run_camera(config,cid,url,stop,fingerprint=None):
    # One native metadata transport per camera at a time. Prefer BcMedia for
    # positively identified Reolink devices; preserve standard ONVIF elsewhere.
    while not stop.is_set() and workers.writes_permitted():
        last=0;session='';native_attempted=False
        def reset(transport):
            nonlocal last,session
            last=0;session=transport+':'+secrets.token_hex(8)
        def ready():
            with contextlib.closing(database.open_db(config.database)) as db:
                if not status(db,cid,url,'streaming',expected_fingerprint=fingerprint):raise InterruptedError()
        def emit(stamp,objects):
            nonlocal last
            if stop.is_set() or not workers.writes_permitted():raise InterruptedError()
            now=time.monotonic()
            if now-last<.2:return
            last=now
            with contextlib.closing(database.open_db(config.database)) as db:snapshot(db,cid,url,stamp,objects,session,expected_fingerprint=fingerprint)
        def native():
            nonlocal native_attempted
            native_attempted=True
            for profile in profiles:
                if stop.is_set() or not workers.writes_permitted():raise InterruptedError()
                reset('baichuan:'+profile)
                try:
                    baichuan_metadata.run(url,stop,emit,ready,profile)
                    return
                except (OSError,ValueError):continue
        def onvif():
            reset('onvif')
            try:
                with onvif_metadata.Client(url) as client:client.run(stop,emit,ready)
                return
            except (OSError,ValueError):pass
            if stop.is_set():raise InterruptedError()
            from . import onvif_capabilities
            with contextlib.closing(database.open_db(config.database)) as db:
                target=db.execute('SELECT main_url,onvif_url,onvif_username,onvif_password,onvif_auth FROM camera_details WHERE camera_id=? AND main_url=?',(cid,url)).fetchone()
            if target and target[1]:
                for metadata_url in onvif_capabilities.metadata_streams(target):
                    if stop.is_set():raise InterruptedError()
                    reset('onvif')
                    try:
                        with onvif_metadata.Client(metadata_url) as client:client.run(stop,emit,ready)
                        return
                    except (OSError,ValueError):continue
        try:
            with contextlib.closing(database.open_db(config.database)) as db:
                row=db.execute(device_diagnosis.TARGET_SQL,(cid,)).fetchone()
                diagnosis=device_diagnosis.cached(db,cid,row) if row else None
                manufacturer=((diagnosis or {}).get('device') or {}).get('manufacturer','').lower()
                event=db.execute('SELECT transport FROM camera_event_status WHERE camera_id=?',(cid,)).fetchone()
                caps=native_capabilities.supported(db,cid)
                profiles=('main','sub') if 'baichuan_main_boxes' in caps and 'baichuan_sub_boxes' not in caps else ('sub','main')
                known='baichuan_boxes' in caps or manufacturer.startswith('reolink') or bool(event and event[0] in ('baichuan','cgi'))
            # Do not replace already proven richer ONVIF observations with
            # a Baichuan format that currently carries only three classes.
            prefer_onvif='boxes' in caps and 'baichuan_boxes' not in caps
            if known and not prefer_onvif:native()
            if not stop.is_set():onvif()
            if not stop.is_set() and not native_attempted and (known or not manufacturer):native()
        except InterruptedError:break
        except (OSError,ValueError,sqlite3.Error):pass
        finally:
            try:
                with contextlib.closing(database.open_db(config.database)) as db:status(db,cid,url,'unavailable',expected_fingerprint=fingerprint)
            except (OSError,ValueError,sqlite3.Error):pass
        if stop.is_set() or not workers.writes_permitted():break
        # Unsupported previews are re-probed at a conservative interval.
        stop.wait(120 if native_attempted else 20)


def supervise(config,stop):
    children={}
    try:
        while not stop.is_set():
            try:
                with contextlib.closing(database.open_db(config.database)) as db:
                    wanted={cid:(url,native_capabilities.key(db,cid)) for cid,url in db.execute("SELECT c.id,d.main_url FROM cameras c JOIN camera_details d ON d.camera_id=c.id LEFT JOIN camera_metadata_options o ON o.camera_id=c.id WHERE c.enabled=1 AND COALESCE(o.enabled,1)=1 ORDER BY c.id LIMIT ?",(MAX_CAMERAS,))} if workers.writes_permitted() else {}
                for cid,(spec,cancel,thread) in list(children.items()):
                    if wanted.get(cid)!=spec:cancel.set()
                    if not thread.is_alive():del children[cid]
                for cid,(url,fingerprint) in wanted.items():
                    if cid in children or len(children)>=MAX_CAMERAS:continue
                    cancel=threading.Event();thread=threading.Thread(target=run_camera,args=(config,cid,url,cancel,fingerprint),daemon=True);children[cid]=((url,fingerprint),cancel,thread);thread.start()
            except (OSError,ValueError,sqlite3.Error) as exc:logging.warning('ONVIF metadata worker: %s',type(exc).__name__)
            stop.wait(5)
    finally:
        for _,cancel,_ in children.values():cancel.set()
