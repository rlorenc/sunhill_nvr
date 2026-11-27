"""Local visual motion regions, independent of camera AI and ONVIF metadata.
Only normalized rectangles/timestamps are persisted, with archive-based retention.
"""
from __future__ import annotations
import contextlib
import json
import logging
import os
import secrets
import select
import sqlite3
import subprocess
import threading
import time
from . import database, security, workers
from .cameras import SLUG

WIDTH,HEIGHT=320,180
MAX_CAMERAS=32
SOURCE='nvr-motion'


class Detector:
    def __init__(self):
        import cv2
        import numpy
        cv2.setNumThreads(1);self.cv=cv2;self.np=numpy;self.previous=None
    def observe(self,raw):
        cv,np=self.cv,self.np
        if len(raw)!=WIDTH*HEIGHT:raise ValueError('invalid motion frame')
        image=cv.GaussianBlur(np.frombuffer(raw,dtype=np.uint8).reshape(HEIGHT,WIDTH),(5,5),0)
        previous,self.previous=self.previous,image
        if previous is None:return []
        mask=cv.threshold(cv.absdiff(image,previous),24,255,cv.THRESH_BINARY)[1]
        # Large exposure changes or camera movement are not localized motion.
        if np.count_nonzero(mask)>.55*mask.size:return []
        mask=cv.morphologyEx(mask,cv.MORPH_OPEN,np.ones((3,3),np.uint8))
        mask=cv.morphologyEx(mask,cv.MORPH_CLOSE,np.ones((9,9),np.uint8))
        mask=cv.dilate(mask,np.ones((5,5),np.uint8))
        _,_,stats,_=cv.connectedComponentsWithStats(mask)
        boxes=[]
        for x,y,w,h,area in stats[1:]:
            if area<70 or min(w,h)<5:continue
            boxes.append([float(x)/WIDTH,float(y)/HEIGHT,float(w)/WIDTH,float(h)/HEIGHT])
        return boxes


def persist(db,cid,url,stamp,objects,session):
    if not workers.writes_permitted():return False
    raw=json.dumps(objects,separators=(',',':'))
    with db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT d.main_url,c.enabled,COALESCE(o.enabled,1) FROM camera_details d JOIN cameras c ON c.id=d.camera_id LEFT JOIN camera_metadata_options o ON o.camera_id=c.id WHERE c.id=?',(cid,)).fetchone()
        if row!=(url,1,1):return False
        old=db.execute('SELECT stamp_ms,objects_json,session FROM camera_motion_latest WHERE camera_id=?',(cid,)).fetchone()
        if old and old[0]>=stamp:return False
        db.execute('INSERT INTO camera_motion_latest VALUES(?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET stamp_ms=excluded.stamp_ms,objects_json=excluded.objects_json,session=excluded.session',(cid,stamp,raw,session))
        if objects or (old and old[1]!='[]'):
            db.execute('INSERT OR IGNORE INTO camera_motion_history VALUES(?,?,?,?)',(cid,stamp,raw,session))
        if objects:
            seconds=stamp//1000
            # Extend an immediately preceding closed interval; no open rows after a crash.
            last=db.execute('SELECT id,ended_at FROM event_markers WHERE camera_id=? AND source=? ORDER BY started_at DESC LIMIT 1',(cid,SOURCE)).fetchone()
            if last and last[1] is not None and 0<=seconds-last[1]<=2:
                db.execute('UPDATE event_markers SET ended_at=?,last_seen=? WHERE id=?',(seconds,seconds,last[0]))
            else:db.execute('INSERT OR IGNORE INTO event_markers(camera_id,started_at,ended_at,event_type,source,last_seen) VALUES(?,?,?,?,?,?)',(cid,seconds,seconds,'motion',SOURCE,seconds))
    return True


def live(db,user,now=None):
    now=int(time.time()*1000) if now is None else now
    return {str(cid):{'stamp_ms':stamp,'objects':json.loads(raw),'session':session} for cid,stamp,raw,session in db.execute('SELECT l.camera_id,l.stamp_ms,l.objects_json,l.session FROM camera_motion_latest l JOIN cameras c ON c.id=l.camera_id LEFT JOIN camera_metadata_options o ON o.camera_id=c.id WHERE c.enabled=1 AND COALESCE(o.enabled,1)=1 AND l.stamp_ms BETWEEN ? AND ?',(now-2000,now+1000)) if security.may_access(db,user,cid,live=True)}


def history(db,cid,stamp):
    return [{'stamp_ms':t,'objects':json.loads(raw),'session':session} for t,raw,session in db.execute('SELECT stamp_ms,objects_json,session FROM camera_motion_history WHERE camera_id=? AND stamp_ms BETWEEN ? AND ? ORDER BY stamp_ms',(cid,stamp-5000,stamp+5000))]


def run_camera(config,cid,slug,url,stop):
    from .local_analytics import Tracks
    if not SLUG.fullmatch(slug):return
    while not stop.is_set() and workers.writes_permitted():
        proc=None
        try:
            detector=Detector();tracker=Tracks();session=secrets.token_hex(8)
            proc=subprocess.Popen(['/usr/bin/ffmpeg','-nostdin','-v','error','-rtsp_transport','tcp','-threads','1','-filter_threads','1','-i',f'rtsp://127.0.0.1:8554/{slug}','-map','0:v:0','-an','-sn','-dn','-vf',f'fps=2,scale={WIDTH}:{HEIGHT},format=gray','-f','rawvideo','pipe:1'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
            fd=proc.stdout.fileno();buffer=bytearray();last=time.monotonic()
            while not stop.is_set() and workers.writes_permitted():
                if not select.select([fd],[],[],.5)[0]:
                    if time.monotonic()-last>10:break
                    continue
                data=os.read(fd,WIDTH*HEIGHT-len(buffer))
                if not data:break
                buffer.extend(data)
                if len(buffer)!=WIDTH*HEIGHT:continue
                last=time.monotonic();stamp=int(time.time()*1000)
                objects=tracker.observe([('motion',box,None) for box in detector.observe(buffer)],stamp);buffer.clear()
                for obj in objects:obj['id']='motion-'+obj['id']
                with contextlib.closing(database.open_db(config.database)) as db:
                    if not persist(db,cid,url,stamp,objects,session):return
        except (OSError,ValueError,ImportError,sqlite3.Error) as exc:
            logging.warning('Motion worker: %s',type(exc).__name__)
        finally:
            if proc is not None:
                proc.terminate()
                try:proc.wait(timeout=2)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
                proc.stdout.close()
        stop.wait(5)


def supervise(config,stop):
    children={}
    try:
        while not stop.is_set():
            try:
                with contextlib.closing(database.open_db(config.database)) as db:
                    wanted={cid:(slug,url) for cid,slug,url in db.execute('SELECT c.id,c.slug,d.main_url FROM cameras c JOIN camera_details d ON d.camera_id=c.id LEFT JOIN camera_metadata_options o ON o.camera_id=c.id WHERE c.enabled=1 AND COALESCE(o.enabled,1)=1 ORDER BY c.id LIMIT ?',(MAX_CAMERAS,))} if workers.writes_permitted() else {}
                for cid,(spec,cancel,thread) in list(children.items()):
                    if wanted.get(cid)!=spec:cancel.set()
                    if not thread.is_alive():del children[cid]
                for cid,(slug,url) in wanted.items():
                    if cid in children or len(children)>=MAX_CAMERAS:continue
                    cancel=threading.Event();thread=threading.Thread(target=run_camera,args=(config,cid,slug,url,cancel),daemon=True);children[cid]=((slug,url),cancel,thread);thread.start()
            except (OSError,sqlite3.Error,ValueError):pass
            stop.wait(5)
    finally:
        for _,cancel,_ in children.values():cancel.set()
