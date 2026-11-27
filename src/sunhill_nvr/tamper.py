"""Default-enabled, bounded local camera obstruction heuristic. No images persisted.

An alert requires repeated nearly uniform frames. Dark scenes and fog can
also trigger it; missing video is not a tamper observation.
"""
from __future__ import annotations
import contextlib
import math
import sqlite3
import subprocess
import time
from . import camera_events, database, workers
from .cameras import SLUG

SOURCE = 'nvr-tamper'
WIDTH, HEIGHT = 96, 54


def uniform(frame):
    if not isinstance(frame, bytes) or len(frame) != WIDTH * HEIGHT:
        raise ValueError('invalid sample')
    mean = sum(frame) / len(frame)
    deviation = math.sqrt(sum((v-mean)**2 for v in frame)/len(frame))
    edges = sum(abs(frame[i]-frame[i-1]) >= 12 for i in range(len(frame)) if i % WIDTH)
    edges += sum(abs(frame[i]-frame[i-WIDTH]) >= 12 for i in range(WIDTH,len(frame)))
    return deviation <= 7 and edges <= len(frame)*.015


class Detector:
    def __init__(self):
        self.count = 0
        self.clear = 0
        self.active = False

    def observe(self, frame):
        if frame is None:
            self.count = self.clear = 0
            return None
        if uniform(frame):
            self.count += 1
            self.clear = 0
            if self.count >= 3: self.active = True
        else:
            self.count = 0
            self.clear += 1
            if self.clear >= 2: self.active = False
        return self.active


def sample(slug):
    if not SLUG.fullmatch(slug) and not (slug.endswith('_sub') and SLUG.fullmatch(slug[:-4])): raise ValueError('invalid camera')
    try:
        proc = subprocess.run(['/usr/bin/ffmpeg','-nostdin','-v','error','-rtsp_transport','tcp','-threads','1','-filter_threads','1','-i',f'rtsp://127.0.0.1:8554/{slug}','-map','0:v:0','-an','-sn','-dn','-vf',f'scale={WIDTH}:{HEIGHT},format=gray','-frames:v','1','-f','rawvideo','pipe:1'],stdin=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdout=subprocess.PIPE,timeout=8,check=True)
        return proc.stdout if len(proc.stdout)==WIDTH*HEIGHT else None
    except (OSError,subprocess.SubprocessError): return None


def persist(db,cid,identity,state,now):
    if not workers.writes_permitted():return
    with db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT c.slug,d.main_url,d.sub_url,c.enabled FROM cameras c JOIN camera_details d ON d.camera_id=c.id WHERE c.id=?',(cid,)).fetchone()
        opts=camera_events.options(db,cid)
        enabled=bool(current and current[:3]==identity and current[3] and opts['enabled'] and 'tamper' in opts['types'])
        row=db.execute('SELECT id,started_at,last_seen FROM event_markers WHERE camera_id=? AND source=? AND ended_at IS NULL',(cid,SOURCE)).fetchone()
        if row and (not enabled or state is False or now-(row[2] or row[1])>120):
            db.execute('UPDATE event_markers SET ended_at=? WHERE id=?',(max(row[1],row[2] or row[1]),row[0]));row=None
        if enabled and state is True:
            if row:db.execute('UPDATE event_markers SET last_seen=? WHERE id=?',(now,row[0]))
            else:db.execute('INSERT OR IGNORE INTO event_markers(camera_id,started_at,event_type,source,last_seen) VALUES(?,?,?,?,?)',(cid,now,'tamper',SOURCE,now))


def supervise(config,stop):
    detectors={}
    while not stop.is_set():
        try:
            with contextlib.closing(database.open_db(config.database)) as db:
                rows=db.execute('SELECT c.id,c.slug,d.main_url,d.sub_url FROM cameras c JOIN camera_details d ON d.camera_id=c.id LEFT JOIN camera_event_options o ON o.camera_id=c.id WHERE c.enabled=1 AND COALESCE(o.enabled,1)=1 ORDER BY c.id LIMIT 32').fetchall() if workers.writes_permitted() else []
                wanted={r[0]:tuple(r[1:]) for r in rows}
                for cid, in db.execute("SELECT DISTINCT camera_id FROM event_markers WHERE source=? AND ended_at IS NULL",(SOURCE,)).fetchall():
                    if cid not in wanted: persist(db,cid,(),None,int(time.time()))
                for cid,(identity,_) in list(detectors.items()):
                    if wanted.get(cid)!=identity:
                        persist(db,cid,identity,None,int(time.time()));del detectors[cid]
            for cid,identity in wanted.items():
                if stop.is_set() or not workers.writes_permitted():break
                if cid not in detectors:detectors[cid]=(identity,Detector())
                detector=detectors[cid][1]
                frame=sample(identity[0]+'_sub' if identity[2] else identity[0])
                state=detector.observe(frame)
                with contextlib.closing(database.open_db(config.database)) as db:persist(db,cid,identity,state,int(time.time()))
        except (OSError,sqlite3.Error,ValueError):pass
        stop.wait(10)
