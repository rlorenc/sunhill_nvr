"""Bounded local stream sampling and read-only indexed recording statistics."""
from __future__ import annotations
import json
import math
import subprocess
import time
from fractions import Fraction
from datetime import datetime, timezone
from .cameras import SLUG
from . import monitor, storage

PREFIX = 'camera_stream_stats:'
DAY = 86400


def _rate(value):
    try:
        n = float(Fraction(value))
        return round(n, 3) if math.isfinite(n) and 0 < n <= 1000 else None
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


PROBE_MAX_SECONDS = 14


def _parse_sample(data):
    stream=data['streams'][0]
    w,h=int(stream['width']),int(stream['height']); codec=stream['codec_name']
    if not (0<w<=16384 and 0<h<=16384 and isinstance(codec,str) and codec.isascii() and codec.isalnum() and len(codec)<=32):return None
    packets=[]
    for p in data.get('packets',[]):
        try:
            t=float(p.get('dts_time',p.get('pts_time'))); size=int(p['size']); duration=float(p.get('duration_time',0))
            if math.isfinite(t) and math.isfinite(duration) and 0<=duration<=2 and 0<size<=100_000_000:packets.append((t,size,duration))
        except (ValueError,TypeError,KeyError):continue
    fps=_rate(stream.get('avg_frame_rate')) or _rate(stream.get('r_frame_rate'))
    bitrate=None
    if len(packets)>=2:
        span=max(t+(d or (1/fps if fps else 0)) for t,_,d in packets)-min(t for t,_,_ in packets)
        if 0.5<=span<=10:bitrate=round(sum(s for _,s,_ in packets)*8/span)
    return {'codec':codec,'width':w,'height':h,'fps':fps,'video_bitrate':bitrate}


def sample(alias, *, run=subprocess.run):
    # Only server-generated aliases; credentials never enter process arguments.
    if not isinstance(alias, str) or not (SLUG.fullmatch(alias) or (alias.endswith('_sub') and SLUG.fullmatch(alias[:-4]))):
        raise ValueError('invalid stream alias')
    base=['/usr/bin/ffprobe','-v','error','-rtsp_transport','tcp','-timeout','5000000',
          '-analyzeduration','2000000','-probesize','1000000','-select_streams','v:0']
    fields='stream=codec_name,width,height,avg_frame_rate,r_frame_rate,bit_rate'
    url=f'rtsp://127.0.0.1:8554/{alias}'
    # Some RTSP streams have unreliable packet timestamps. A metadata-only
    # retry can still report resolution, codec and FPS without inventing bitrate.
    for extra,entries,timeout in [(['-read_intervals','%+2','-show_packets'],fields+':packet=dts_time,pts_time,duration_time,size',8),([],fields,6)]:
        args=base+extra+['-show_entries',entries,'-of','json',url]
        try:
            result=run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=timeout)
            if result.returncode or len(result.stdout)>1_000_000:continue
            profile=_parse_sample(json.loads(result.stdout))
            if profile is not None:return profile
        except (OSError,subprocess.SubprocessError,ValueError,TypeError,KeyError,IndexError,AttributeError,OverflowError):continue
    return None


def _cached(db,cid):
    row=db.execute('SELECT value FROM system_settings WHERE key=?',(PREFIX+str(cid),)).fetchone()
    try:
        value=json.loads(row[0]) if row else {}
        return value if isinstance(value,dict) else {}
    except (ValueError,TypeError):return {}


def clear(db,cid):
    db.execute('DELETE FROM system_settings WHERE key=?',(PREFIX+str(cid),))


def _stamp(value):
    return value if type(value) is int and value>0 else 0


def _legacy(profile):
    return not any(key+'_attempt_at' in profile for key in ('main','sub'))


def refresh(db, *, inspect=sample, should_stop=lambda:False, clock=time.time, monotonic=time.monotonic, budget=64):
    # Schedule individual profiles, not whole cameras. Commit each result even
    # if there is insufficient time for the other profile in this cycle.
    rows=db.execute('SELECT c.id,c.slug,d.main_url,d.sub_url FROM cameras c JOIN camera_details d ON d.camera_id=c.id WHERE c.enabled=1').fetchall()
    tasks=[]
    for row in rows:
        cid,slug,main,sub=row; cached=_cached(db,cid)
        for key in ('main','sub') if sub else ('main',):
            tasks.append((_stamp(cached.get(key+'_attempt_at',cached.get('checked_at') if _legacy(cached) else None)),cid,key,row))
    started=monotonic()
    for _,cid,key,(cid,slug,main,sub) in sorted(tasks):
        if should_stop() or monotonic()-started+PROBE_MAX_SECONDS>budget:return
        profile=inspect(slug if key=='main' else slug+'_sub')
        if should_stop():return
        stamp=int(clock())
        with db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT c.slug,d.main_url,d.sub_url FROM cameras c JOIN camera_details d ON d.camera_id=c.id WHERE c.id=? AND c.enabled=1',(cid,)).fetchone()
            if current is None or tuple(current)!=(slug,main,sub):continue
            value=_cached(db,cid)
            # Upgrade the old shared timestamp lazily. Do not renew the other
            # profile's age or erase the last successful result after a failure.
            legacy=_legacy(value)
            for kind in ('main','sub'):
                if legacy and value.get('checked_at'):
                    value[kind+'_attempt_at']=value['checked_at']
                    if value.get(kind):value[kind+'_checked_at']=value['checked_at']
            value[key+'_attempt_at']=stamp;value[key+'_failed']=profile is None
            if profile is not None:
                value[key]=profile;value[key+'_checked_at']=stamp
            value['sub_configured']=bool(sub);value['checked_at']=stamp
            if not sub:
                for name in ('sub','sub_attempt_at','sub_checked_at','sub_failed'):value.pop(name,None)
            db.execute('INSERT INTO system_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(PREFIX+str(cid),json.dumps(value,separators=(',',':'))))


def _profile_status(profile,key,now,max_age,configured=True):
    if not configured:return None,'not_configured',None
    data=profile.get(key);stamp=profile.get(key+'_checked_at',profile.get('checked_at') if data else None)
    attempted=profile.get(key+'_attempt_at',profile.get('checked_at') if _legacy(profile) else None)
    if data:
        return data, 'current' if monitor.sample_fresh(stamp,now,max_age) and not profile.get(key+'_failed') else 'last_known',stamp
    return None,'failed' if attempted else 'pending',None


def coverage(intervals, start, end):
    spans=[]
    for a,b in sorted(intervals):
        if not (math.isfinite(a) and math.isfinite(b)):continue
        a,b=max(a,start),min(b,end)
        if a>=b:continue
        if spans and a<=spans[-1][1]:spans[-1]=(spans[-1][0],max(b,spans[-1][1]))
        else:spans.append((a,b))
    gaps=[b[0]-a[1] for a,b in zip(spans,spans[1:]) if b[0]-a[1]>30]
    return {'covered_seconds':round(sum(b-a for a,b in spans)), 'gaps':len(gaps),'gap_seconds':round(sum(gaps))}


def inventory(db, root, *, minimum, buffer, now=None, space=storage.space_for, guard=None):
    now=int(time.time()) if now is None else now
    cutoff=now-DAY
    rows=db.execute('SELECT c.id,c.slug,c.enabled,d.record_mode,d.recording_since,d.sub_url,h.state,h.last_checked,d.last_file_at FROM cameras c JOIN camera_details d ON d.camera_id=c.id LEFT JOIN camera_health h ON h.camera_id=c.id').fetchall()
    result={}; daily=0; ready=True; active=0
    # Bytes are finalized indexed files written in the last 24h, not a
    # projection from nominal bitrate; report newly configured cameras as partial.
    for cid,slug,enabled,mode,since,sub,state,checked,last in rows:
        total,size=db.execute('SELECT COUNT(*),COALESCE(SUM(file_size),0) FROM recordings WHERE camera=?',(slug,)).fetchone()
        count,amount=db.execute('SELECT COUNT(*),COALESCE(SUM(file_size),0) FROM recordings WHERE camera=? AND mtime_ns BETWEEN ? AND ?',(slug,cutoff*10**9,now*10**9)).fetchone()
        intervals=[]; ambiguous=0
        low=datetime.fromtimestamp(cutoff-DAY,timezone.utc).isoformat();high=datetime.fromtimestamp(now,timezone.utc).isoformat()
        for first,second,duration in db.execute('SELECT utc_first,utc_second,duration FROM recordings WHERE camera=? AND utc_first BETWEEN ? AND ? ORDER BY utc_first',(slug,low,high)):
            try:
                a=datetime.fromisoformat(first).timestamp(); d=float(duration)
                if not 0<d<=DAY or a+d<=cutoff:continue
                if second is not None:ambiguous+=1;continue
                intervals.append((a,a+d))
            except (ValueError,TypeError,OverflowError):continue
        health=state if monitor.sample_fresh(checked,now,monitor.HEALTH_MAX_AGE) else 'unknown'
        full=type(since) is int and 0<since<=cutoff
        profile=_cached(db,cid) if enabled else {}
        stamp=profile.get('checked_at'); max_age=max(1800,420*len(rows))
        main_profile,main_state,main_checked=_profile_status(profile,'main',now,max_age,bool(enabled))
        sub_profile,sub_state,sub_checked=_profile_status(profile,'sub',now,max_age,bool(enabled and sub))
        fresh=main_state=='current' and (not sub or sub_state=='current')
        result[str(cid)]={'main':main_profile,'sub':sub_profile,'main_state':main_state,'sub_state':sub_state,'main_checked_at':main_checked,'sub_checked_at':sub_checked,
                          'sub_configured':bool(sub),'checked_at':stamp,'stream_fresh':fresh,'files':total,'bytes':size,
                          'files_24h':count,'bytes_24h':amount,'full_day':full,'health':health,'last_file_at':last,
                          'ambiguous':ambiguous,**coverage(intervals,cutoff,now)}
        if enabled and mode!='none':
            active+=1;daily+=amount
            if not full or not amount or (mode=='rtsp' and (health!='recording' or result[str(cid)]['covered_seconds']<DAY-3600)):ready=False
    disk={'free_bytes':None,'total_bytes':None,'days_until_reserve':None,'retention_days':None,'active_bytes_24h':daily,'forecast_ready':False}
    try:
        if guard is not None and not guard():raise ValueError('archive unavailable')
        s=space(root);disk.update(free_bytes=s.free_for_service,total_bytes=s.total)
        if active and ready and daily and minimum>=0 and buffer>=0:
            # The current indexed archive is only reclaimable if it is not protected.
            reclaim=db.execute('SELECT COALESCE(SUM(r.file_size),0) FROM recordings r LEFT JOIN protected_files p ON p.relative_path=r.relative_path WHERE p.relative_path IS NULL').fetchone()[0]
            free=max(0,s.free_for_service-minimum-buffer)
            disk.update(days_until_reserve=round(free/daily,2),retention_days=round((free+reclaim)/daily,2),forecast_ready=True)
    except (OSError,ValueError,RuntimeError):pass
    return {'cameras':result,'disk':disk,'sampled_at':now}
