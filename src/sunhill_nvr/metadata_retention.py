"""Keep native AI metadata only where indexed video survives.

A complete successful scan permits reconciliation of gaps. Rotation permits
reconciliation only of the verified removed intervals. No age or row quota.
Do not touch externally supplied Home Assistant event history.
"""
from __future__ import annotations
import math
import time
from datetime import datetime

# In-flight segments may be as long as one hour; allow publication/index lag.
PUBLISH_GRACE = 2 * 3600
NATIVE = "(source IN ('reolink','nvr-tamper') OR source LIKE 'onvif-rule:%' OR source LIKE 'local-rule:%')"


def intervals(rows):
    result=[]
    for first,second,duration in rows:
        if not first or not math.isfinite(duration) or duration<=0:
            return None
        for value in (first,second):
            if value is None:continue
            try:
                instant=datetime.fromisoformat(value)
                if instant.utcoffset() is None:return None
                start=instant.timestamp()
            except (ValueError,OverflowError,OSError):return None
            result.append((math.floor(start*1000),math.ceil((start+duration)*1000)))
    merged=[]
    for a,b in sorted(result):
        if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    return merged


def gaps(coverage,start,end):
    cursor=start
    for a,b in coverage:
        if b<=cursor:continue
        if a>=end:break
        if a>cursor:yield cursor,min(a,end)
        cursor=max(cursor,b)
        if cursor>=end:return
    if cursor<end:yield cursor,end


def reconcile(db, *, guard, complete=False, removed=(), now=None):
    """Caller holds archive lock; rollback if mount/maintenance changes."""
    from . import workers
    if not workers.writes_permitted() or not guard():return 0
    now=time.time() if now is None else now
    cutoff=math.floor((now-PUBLISH_GRACE)*1000)
    deleted=0
    with db:
        db.execute('BEGIN IMMEDIATE')
        for cid,slug in db.execute('SELECT id,slug FROM cameras').fetchall():
            if not complete and not any(r[0]==slug for r in removed):continue
            covered=intervals(db.execute('SELECT utc_first,utc_second,duration FROM recordings WHERE camera=?',(slug,)))
            if covered is None:continue
            if complete:
                scope=[(-2**62,cutoff)]
            else:
                scope=intervals(r[1:] for r in removed if r[0]==slug)
                if scope is None:continue
            for start,end in scope:
                for lo,hi in gaps(covered,start,min(end,cutoff)):
                    deleted+=db.execute('DELETE FROM camera_metadata_history WHERE camera_id=? AND stamp_ms>=? AND stamp_ms<?',(cid,lo,hi)).rowcount
                    # Keep events touching any surviving footage, or still open.
                    deleted+=db.execute(f'''DELETE FROM event_markers WHERE camera_id=? AND {NATIVE}
                        AND ended_at IS NOT NULL AND started_at>=? AND ended_at<?''',
                        (cid,math.ceil(lo/1000),math.ceil(hi/1000))).rowcount
        if not workers.writes_permitted() or not guard():raise RuntimeError('archive changed during AI metadata reconciliation')
    return deleted
