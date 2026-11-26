"""Index finished camera recordings without changing their paths or contents."""
from __future__ import annotations
import math
import os
import sqlite3
import stat
import time
import contextlib
from dataclasses import dataclass
from datetime import timedelta,timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo
from .rotation import iter_recordings,recording_info
from itertools import islice
from .files import open_confined
from .timeline import utc_candidates

SCHEMA='''
CREATE TABLE IF NOT EXISTS recordings (
 id INTEGER PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE, camera TEXT NOT NULL,
 local_time TEXT NOT NULL, utc_first TEXT, utc_second TEXT, duration REAL NOT NULL,
 file_size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, generation INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recordings_camera_local ON recordings(camera,local_time);
CREATE INDEX IF NOT EXISTS idx_recordings_camera_utc_first ON recordings(camera,utc_first);
CREATE INDEX IF NOT EXISTS idx_recordings_camera_utc_second ON recordings(camera,utc_second);
CREATE INDEX IF NOT EXISTS idx_recordings_camera_mtime ON recordings(camera,mtime_ns DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_recordings_local_time ON recordings(local_time);
CREATE TABLE IF NOT EXISTS recording_identity (
 relative_path TEXT PRIMARY KEY REFERENCES recordings(relative_path) ON DELETE CASCADE,
 device TEXT NOT NULL, inode TEXT NOT NULL, ctime_ns TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recording_stream_info (
 relative_path TEXT PRIMARY KEY REFERENCES recordings(relative_path) ON DELETE CASCADE,
 has_audio INTEGER NOT NULL CHECK(has_audio IN (0,1))
);
CREATE TABLE IF NOT EXISTS archive_scan_state (id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL);
INSERT OR IGNORE INTO archive_scan_state(id,generation) VALUES (1,0);
CREATE TABLE IF NOT EXISTS archive_recording_sequence (
 id INTEGER PRIMARY KEY CHECK(id=1), highwater INTEGER NOT NULL CHECK(highwater>=0)
);
INSERT OR IGNORE INTO archive_recording_sequence(id,highwater) SELECT 1,COALESCE(MAX(id),0) FROM recordings;
-- Keep the high-water mark even when rotation or a storage switch removes
-- the last indexed recording. Existing IDs and table layout remain intact.
CREATE TRIGGER IF NOT EXISTS archive_recording_sequence_insert AFTER INSERT ON recordings
BEGIN
 UPDATE archive_recording_sequence SET highwater=MAX(highwater,NEW.id) WHERE id=1;
END;
'''

def initialize(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA);db.commit()

@dataclass(frozen=True)
class ScanReport:
    accepted: int
    failed: int
    removed: int
    scanned: int = 0
    unchanged: int = 0
    skipped: int = 0
    interrupted: bool = False
    empty_removed: int = 0
    empty_deferred: int = 0

def refresh(db: sqlite3.Connection, root: Path, timezone_name: str, *,
            inspect_duration: Callable[[Path], float], is_open: Callable[[Path], bool],
            mount_check: Callable[[], bool], min_age_seconds: int = 900,
            now: float | None = None, dates: set | None = None,
            on_verified: Callable[[Path,os.stat_result],None] | None = None,
            on_persist: Callable[[sqlite3.Connection],None] | None = None,
            commit_guard=None, is_open_batch=None, should_stop=lambda:False,
            progress=None, camera=None, batch_size: int=64, remove_empty=None) -> ScanReport:
    """Stream bounded batches; commit verified records before traversing more.

    Full reconciliation only follows a complete scan. Cancellation preserves
    prior commits and never prunes. File identity and the archive lock remain
    mandatory for every transaction.
    """
    if root.is_symlink() or not root.is_dir() or not mount_check():
        raise RuntimeError('archive mount unavailable')
    zone=ZoneInfo(timezone_name)
    now=time.time() if now is None else now
    if min_age_seconds < 0 or not math.isfinite(now) or not 1<=batch_size<=64:
        raise ValueError('invalid scan parameters')
    recordings=iter_recordings(root,min_age_seconds=min_age_seconds,now=now,dates=dates,timezone_name=timezone_name,camera=camera)
    accepted=failed=removed=scanned=unchanged_count=skipped=empty_removed=empty_deferred=0
    gen=None
    def report(interrupted=False):
        return ScanReport(accepted,failed,removed,scanned,unchanged_count,skipped,interrupted,empty_removed,empty_deferred)
    def commit(items):
        nonlocal gen,accepted,failed,unchanged_count
        if not mount_check():raise RuntimeError('archive mount changed during scan')
        with (commit_guard() if commit_guard is not None else contextlib.nullcontext()):
            db.execute('BEGIN IMMEDIATE')
            try:
                if not mount_check():raise RuntimeError('archive mount changed during reconciliation')
                if gen is None:
                    gen=db.execute('SELECT generation FROM archive_scan_state WHERE id=1').fetchone()[0]+1
                    db.execute('UPDATE archive_scan_state SET generation=? WHERE id=1',(gen,))
                highwater=db.execute('SELECT MAX(highwater,(SELECT COALESCE(MAX(id),0) FROM recordings)) FROM archive_recording_sequence WHERE id=1').fetchone()[0]
                for item in items:
                    try:
                        verified_info=(root/item[0]).lstat()
                        if (not stat.S_ISREG(verified_info.st_mode) or verified_info.st_nlink!=1 or
                                (verified_info.st_size,verified_info.st_mtime_ns,str(verified_info.st_dev),str(verified_info.st_ino),str(verified_info.st_ctime_ns))!=item[6:11]):
                            raise ValueError('recording changed before index commit')
                    except (OSError,ValueError):
                        failed+=1;continue
                    existing_id=db.execute('SELECT id FROM recordings WHERE relative_path=?',(item[0],)).fetchone()
                    if existing_id is None:
                        if highwater>=9_999_999_999_999:raise RuntimeError('recording identity limit reached')
                        highwater+=1
                    rid=existing_id[0] if existing_id else highwater
                    db.execute('''INSERT INTO recordings(id,relative_path,camera,local_time,utc_first,utc_second,duration,file_size,mtime_ns,generation)
                        VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(relative_path) DO UPDATE SET camera=excluded.camera,
                        local_time=excluded.local_time,utc_first=excluded.utc_first,utc_second=excluded.utc_second,
                        duration=excluded.duration,file_size=excluded.file_size,mtime_ns=excluded.mtime_ns,generation=excluded.generation''',(rid,*item[:8],gen))
                    db.execute('''INSERT INTO recording_identity(relative_path,device,inode,ctime_ns) VALUES (?,?,?,?)
                        ON CONFLICT(relative_path) DO UPDATE SET device=excluded.device,inode=excluded.inode,ctime_ns=excluded.ctime_ns''',(item[0],item[8],item[9],item[10]))
                    if on_verified is not None and item[11]:on_verified(root/item[0],verified_info)
                    accepted+=1
                    if not item[11]:unchanged_count+=1
                if on_persist is not None:on_persist(db)
                if not mount_check():raise RuntimeError('archive mount changed during reconciliation')
                db.commit()
            except BaseException:
                db.rollback();raise
        if progress is not None:progress(report())
    while not should_stop():
        group=list(islice(recordings,batch_size))
        if not group:break
        names=[str(r.path.relative_to(root)) for r in group]
        marks=','.join('?' for _ in names)
        rows=db.execute('SELECT r.relative_path,r.file_size,r.mtime_ns,r.duration,i.device,i.inode,i.ctime_ns FROM recordings r LEFT JOIN recording_identity i ON i.relative_path=r.relative_path WHERE r.relative_path IN ('+marks+')',names)
        existing={r[0]:r[1:] for r in rows}
        candidates=[];changed=[]
        for r,relative in zip(group,names):
            try:
                info=r.path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('invalid file')
                cached=existing.get(relative)
                unchanged=bool(info.st_size>0 and cached and cached[0]==info.st_size and cached[1]==info.st_mtime_ns
                               and cached[3]==str(info.st_dev) and cached[4]==str(info.st_ino)
                               and cached[5]==str(info.st_ctime_ns))
                candidates.append((r,relative,info,cached,unchanged))
                if not unchanged:changed.append(r.path)
            except (OSError,ValueError):failed+=1;scanned+=1
        if should_stop():return report(True)
        active={}
        if changed and is_open_batch is not None:
            # A failed batch is not permission to probe a potentially open file.
            # Surface the RPC failure and retry on the next worker pass.
            states=is_open_batch(changed)
            if len(states)!=len(changed) or any(type(x) is not bool for x in states):raise RuntimeError('invalid activity response')
            active=dict(zip(changed,states))
        items=[]
        for r,relative,info,cached,unchanged in candidates:
            if should_stop():break
            scanned+=1
            try:
                if not unchanged and (active[r.path] if is_open_batch is not None else is_open(r.path)):
                    skipped+=1;continue
                if info.st_size==0 and remove_empty is not None:
                    if remove_empty(r.path,info):empty_removed+=1
                    else:empty_deferred+=1
                    continue
                duration=float(cached[2] if unchanged else inspect_duration(r.path))
                if not math.isfinite(duration) or duration <= 0 or duration > 86400:raise ValueError('invalid duration')
                after=r.path.lstat()
                if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise RuntimeError('file changed')
                times=(r.utc_timestamp,) if r.utc_timestamp is not None else utc_candidates(r.timestamp,zone)
                local_time=r.utc_timestamp.astimezone(zone).replace(tzinfo=None) if r.utc_timestamp is not None else r.timestamp
                items.append((relative,r.path.relative_to(root).parts[0],local_time.isoformat(),times[0].isoformat() if times else None,
                              times[1].isoformat() if len(times)>1 else None,duration,info.st_size,info.st_mtime_ns,
                              str(info.st_dev),str(info.st_ino),str(info.st_ctime_ns),not unchanged))
            except (OSError,RuntimeError,ValueError,TypeError,KeyError):failed+=1
        commit(items)
    if should_stop():return report(True)
    if gen is None:commit([])
    # Only an exhaustive traversal may reconcile missing paths. Use keyset
    # batches; never remove a row merely because a probe failed or was skipped.
    if dates is None and camera is None:
        last=0
        while not should_stop():
            with (commit_guard() if commit_guard is not None else contextlib.nullcontext()):
                db.execute('BEGIN IMMEDIATE')
                try:
                    if not mount_check():raise RuntimeError('archive mount changed during reconciliation')
                    stale=db.execute('SELECT id,relative_path FROM recordings WHERE generation<>? AND id>? ORDER BY id LIMIT ?',(gen,last,batch_size)).fetchall()
                    for row_id,relative in stale:
                        last=row_id
                        try:(root/relative).lstat()
                        except FileNotFoundError:
                            db.execute('DELETE FROM recording_identity WHERE relative_path=?',(relative,))
                            db.execute('DELETE FROM recording_stream_info WHERE relative_path=?',(relative,))
                            db.execute('DELETE FROM recordings WHERE id=?',(row_id,));removed+=1
                        except OSError:continue
                    if not mount_check():raise RuntimeError('archive mount changed during reconciliation')
                    db.commit()
                except BaseException:db.rollback();raise
            if not stale:break
    return report(should_stop())


def index_published(db: sqlite3.Connection, root: Path, path: Path, timezone_name: str,
                    signature: tuple[int,int,int,int], inspection: dict, *, mount_check) -> None:
    """Index only a supervisor-verified, already atomically published RTSP file.

    Caller holds the shared archive lock. FTP and normal scan policies are unchanged.
    """
    if not mount_check():raise RuntimeError('archive mount unavailable')
    relative=str(path.relative_to(root))
    duration=float(inspection['format']['duration'])
    if not math.isfinite(duration) or not 0<duration<=86400:raise ValueError('invalid duration')
    streams=inspection.get('streams')
    if not isinstance(streams,list) or not all(isinstance(x,dict) for x in streams) or not any(x.get('codec_type')=='video' for x in streams):raise ValueError('invalid streams')
    fd,info=open_confined(root,relative)
    try:
        if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=signature:raise ValueError('published file changed')
        recording=recording_info(root,path,strict=True)
        if recording is None or (recording.device,recording.inode,recording.size,recording.mtime_ns,recording.ctime_ns)!=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns):raise ValueError('invalid published recording')
        zone=ZoneInfo(timezone_name)
        times=(recording.utc_timestamp,) if recording.utc_timestamp is not None else utc_candidates(recording.timestamp,zone)
        local=recording.utc_timestamp.astimezone(zone).replace(tzinfo=None) if recording.utc_timestamp is not None else recording.timestamp
        db.execute('BEGIN IMMEDIATE')
        try:
            if not mount_check():raise RuntimeError('archive mount changed')
            final=path.lstat()
            if (final.st_dev,final.st_ino,final.st_size,final.st_mtime_ns,final.st_ctime_ns,final.st_nlink)!=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,1):raise ValueError('published recording changed')
            gen=db.execute('SELECT generation FROM archive_scan_state WHERE id=1').fetchone()[0]
            existing=db.execute('SELECT id FROM recordings WHERE relative_path=?',(relative,)).fetchone()
            rid=existing[0] if existing else db.execute('SELECT MAX(highwater,(SELECT COALESCE(MAX(id),0) FROM recordings)) FROM archive_recording_sequence WHERE id=1').fetchone()[0]+1
            if rid>9_999_999_999_999:raise RuntimeError('recording identity limit reached')
            db.execute("""INSERT INTO recordings(id,relative_path,camera,local_time,utc_first,utc_second,duration,file_size,mtime_ns,generation)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(relative_path) DO UPDATE SET camera=excluded.camera,local_time=excluded.local_time,utc_first=excluded.utc_first,utc_second=excluded.utc_second,duration=excluded.duration,file_size=excluded.file_size,mtime_ns=excluded.mtime_ns,generation=excluded.generation""",
                (rid,relative,path.relative_to(root).parts[0],local.isoformat(),times[0].isoformat() if times else None,times[1].isoformat() if len(times)>1 else None,duration,info.st_size,info.st_mtime_ns,gen))
            db.execute("""INSERT INTO recording_identity(relative_path,device,inode,ctime_ns) VALUES(?,?,?,?) ON CONFLICT(relative_path) DO UPDATE SET device=excluded.device,inode=excluded.inode,ctime_ns=excluded.ctime_ns""",(relative,str(info.st_dev),str(info.st_ino),str(info.st_ctime_ns)))
            db.execute("""INSERT INTO recording_stream_info(relative_path,has_audio) VALUES(?,?) ON CONFLICT(relative_path) DO UPDATE SET has_audio=excluded.has_audio""",(relative,int(any(x.get('codec_type')=='audio' for x in streams))))
            if not mount_check():raise RuntimeError('archive mount changed')
            db.commit()
        except BaseException:db.rollback();raise
    finally:os.close(fd)
