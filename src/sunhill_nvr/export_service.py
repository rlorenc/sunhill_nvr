"""Plan and execute a queued archive export with source locks and permissions."""
from __future__ import annotations
import os
import fcntl
import tempfile
import secrets
import sqlite3
import stat
import re
import time
import math
from datetime import datetime,timezone,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from . import jobs,security,fd_mount
from .export_plan import Clip,Gap,Slice,create_plan
from .files import acquire_shared,open_confined,recorded_identity
from .media import Cut,export,export_many,probe,MediaError
from .storage import space_for
from .timeline import resolve_local

class ExportError(RuntimeError):pass
class AmbiguousRecordingError(ExportError):pass
class NoRecordingsError(ExportError):pass

def _cleanup_identity(info: os.stat_result) -> tuple:
    return (info.st_dev,info.st_ino,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _same_mount_file(parent_fd: int, name: str, expected: os.stat_result, mount) -> bool:
    """Reject replaced files and file-level bind mounts before cleanup."""
    try:
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent_fd)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or _cleanup_identity(info)!=_cleanup_identity(expected):return False
            fd_mount.require_same(mount,fd)
            return True
        finally:os.close(fd)
    except (OSError,fd_mount.MountBoundary):return False

def _cleanup_interrupted_output(rootfd: int, root: Path, name: str, info, names, mount, active, now: float, min_age: int) -> bool:
    """Clean the two links left by a crash during publication of a failed job."""
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=2 or info.st_dev!=mount[0]:return False
    for temporary in names:
        if not re.fullmatch(r'\.sunhill-export-[A-Za-z0-9_-]+\.mp4',temporary):continue
        try:other=os.stat(temporary,dir_fd=rootfd,follow_symlinks=False)
        except FileNotFoundError:continue
        if (not stat.S_ISREG(other.st_mode) or other.st_nlink!=2 or now-other.st_mtime<min_age
            or (other.st_dev,other.st_ino)!=(info.st_dev,info.st_ino)):continue
        if active(root/name) or active(root/temporary):return False
        if not _same_mount_file(rootfd,name,info,mount) or not _same_mount_file(rootfd,temporary,other,mount):return False
        current=os.stat(name,dir_fd=rootfd,follow_symlinks=False)
        alternate=os.stat(temporary,dir_fd=rootfd,follow_symlinks=False)
        if any(_cleanup_identity(item)!=_cleanup_identity(info) for item in (current,alternate)):
            return False
        # If interrupted here, the remaining failed output has one link and is
        # safely handled by the normal cleanup path on the next pass.
        os.unlink(temporary,dir_fd=rootfd)
        current=os.stat(name,dir_fd=rootfd,follow_symlinks=False)
        if ((current.st_dev,current.st_ino,current.st_nlink,current.st_size,current.st_mtime_ns)!=(info.st_dev,info.st_ino,1,info.st_size,info.st_mtime_ns)
            or active(root/name) or not _same_mount_file(rootfd,name,current,mount)):return False
        os.unlink(name,dir_fd=rootfd)
        return True
    return False

def cleanup_stale_temporary(db: sqlite3.Connection, export_root: Path, *, active, now: float | None=None, min_age: int=86400) -> int:
    """Remove only abandoned application-owned temporary files, never completed exports."""
    if min_age<86400 or export_root.is_symlink() or not export_root.is_dir():raise ValueError('invalid cleanup parameters')
    now=time.time() if now is None else now
    rootfd=os.open(export_root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    removed=0
    try:
        mount=fd_mount.identity(rootfd)
        device=mount[0]
        db.execute('BEGIN IMMEDIATE')
        try:
            # Serialize against job claims; a running job might still be writing.
            if db.execute("SELECT 1 FROM export_jobs WHERE status='running' LIMIT 1").fetchone():
                db.commit();return 0
            names_in_root=os.listdir(rootfd);temporary_links=None
            for name in names_in_root:
                final=re.fullmatch(r'export-([1-9][0-9]{0,12})-[a-f0-9]{16}\.mp4',name)
                if final:
                    row=db.execute("SELECT id,updated FROM export_jobs WHERE id=? AND status='failed' AND result_name=?",(int(final.group(1)),name)).fetchone()
                    if not row or now-row[1]<min_age:continue
                    try:
                        info=os.stat(name,dir_fd=rootfd,follow_symlinks=False)
                        old=stat.S_ISREG(info.st_mode) and info.st_dev==device and now-info.st_mtime>=min_age
                        if old and info.st_nlink==2:
                            if temporary_links is None:
                                temporary_links={}
                                for candidate in names_in_root:
                                    if not re.fullmatch(r'\.sunhill-export-[A-Za-z0-9_-]+\.mp4',candidate):continue
                                    try:other=os.stat(candidate,dir_fd=rootfd,follow_symlinks=False)
                                    except FileNotFoundError:continue
                                    if stat.S_ISREG(other.st_mode) and other.st_nlink==2:
                                        temporary_links.setdefault((other.st_dev,other.st_ino),[]).append(candidate)
                            candidates=temporary_links.get((info.st_dev,info.st_ino),())
                            deleted=_cleanup_interrupted_output(rootfd,export_root,name,info,candidates,mount,active,now,min_age)
                        elif old and info.st_nlink==1 and not active(export_root/name) and _same_mount_file(rootfd,name,info,mount):
                            os.unlink(name,dir_fd=rootfd);deleted=True
                        else:deleted=False
                        if deleted:
                            removed+=1
                            db.execute("UPDATE export_jobs SET result_name=NULL WHERE id=? AND status='failed' AND result_name=?",(row[0],name))
                    except FileNotFoundError:pass
                    continue
                if re.fullmatch(r'\.sunhill-export-[A-Za-z0-9_-]+\.mp4',name):
                    try:
                        info=os.stat(name,dir_fd=rootfd,follow_symlinks=False)
                        if (stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_dev==device
                            and now-info.st_mtime>=min_age and not active(export_root/name)
                            and _same_mount_file(rootfd,name,info,mount)):
                            os.unlink(name,dir_fd=rootfd);removed+=1
                    except FileNotFoundError:pass
                    continue
                if not re.fullmatch(r'\.sunhill-(?:job|batch)-[A-Za-z0-9_-]+',name):continue
                try:
                    info=os.stat(name,dir_fd=rootfd,follow_symlinks=False)
                    if not stat.S_ISDIR(info.st_mode) or info.st_dev!=device or now-info.st_mtime<min_age:continue
                    fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd)
                    try:
                        if os.fstat(fd).st_ino!=info.st_ino or os.fstat(fd).st_dev!=device:continue
                        try:fd_mount.require_same(mount,fd)
                        except fd_mount.MountBoundary:continue
                        names=os.listdir(fd);safe=True;children={};counts={}
                        for child in names:
                            child_info=os.stat(child,dir_fd=fd,follow_symlinks=False)
                            if (not re.fullmatch(r'(?:part-\d+|pass-\d+-\d+|\.sunhill-export-[A-Za-z0-9_-]+)\.mp4',child)
                                or not stat.S_ISREG(child_info.st_mode) or not 1<=child_info.st_nlink<=2
                                or child_info.st_dev!=device or now-child_info.st_mtime<min_age
                                or active(export_root/name/child) or not _same_mount_file(fd,child,child_info,mount)):safe=False;break
                            identity=(child_info.st_dev,child_info.st_ino);children[child]=child_info
                            counts[identity]=counts.get(identity,0)+1
                        if safe and any(counts[(item.st_dev,item.st_ino)]!=item.st_nlink for item in children.values()):safe=False
                        if safe:
                            for child,item in children.items():
                                current=os.stat(child,dir_fd=fd,follow_symlinks=False)
                                if (active(export_root/name/child) or _cleanup_identity(current)!=_cleanup_identity(item)
                                    or not _same_mount_file(fd,child,item,mount)):safe=False;break
                        if not safe:continue
                        # Removing one of two internal hard links changes ctime
                        # and link count of its sibling. Revalidate the stable
                        # content fields and expected remaining link count per
                        # unlink, never treating an external link as abandoned.
                        for child,item in children.items():
                            identity=(item.st_dev,item.st_ino)
                            current=os.stat(child,dir_fd=fd,follow_symlinks=False)
                            if ((current.st_dev,current.st_ino,current.st_nlink,current.st_size,current.st_mtime_ns)
                                !=(item.st_dev,item.st_ino,counts[identity],item.st_size,item.st_mtime_ns)
                                or active(export_root/name/child) or not _same_mount_file(fd,child,current,mount)):
                                safe=False;break
                            os.unlink(child,dir_fd=fd);counts[identity]-=1
                        if not safe:continue
                    finally:os.close(fd)
                    current=os.stat(name,dir_fd=rootfd,follow_symlinks=False)
                    if (current.st_dev,current.st_ino)!=(info.st_dev,info.st_ino):continue
                    os.rmdir(name,dir_fd=rootfd);removed+=1
                except FileNotFoundError:continue
            db.commit()
        except BaseException:
            db.rollback();raise
    finally:os.close(rootfd)
    return removed

def parse_instant(value: str) -> datetime:
    if not isinstance(value,str) or len(value)>40:raise ValueError('invalid date')
    try:
        instant=datetime.fromisoformat(value.replace('Z','+00:00'))
        if instant.utcoffset() is None:raise ValueError('timezone required')
        return instant.astimezone(timezone.utc)
    except (ValueError,OverflowError) as exc:raise ValueError('invalid date') from exc

def parse_local(value: str, timezone_name: str, fold=None) -> datetime:
    if not isinstance(value,str) or len(value)>32 or (fold is not None and (type(fold) is not int or fold not in (0,1))):raise ValueError('invalid local time')
    try:
        local=datetime.fromisoformat(value)
        if local.tzinfo is not None:raise ValueError('local time must not contain an offset')
        return resolve_local(local,ZoneInfo(timezone_name),fold=fold)
    except (ValueError,OverflowError) as exc:raise ValueError('invalid or ambiguous local time') from exc


def plan(db: sqlite3.Connection, camera_id: int, start: datetime, end: datetime):
    if start.utcoffset() is None or end.utcoffset() is None:
        raise ValueError('invalid export range')
    start=start.astimezone(timezone.utc);end=end.astimezone(timezone.utc)
    if end<=start:raise ValueError('invalid export range')
    row=db.execute('SELECT slug FROM cameras WHERE id=?',(camera_id,)).fetchone()
    if not row:raise ExportError('camera unavailable')
    # Indexed recordings are at most 24 h long. Include a one-second margin
    # around the range to account for ISO timestamps with/without fractions.
    try:lower=(start-timedelta(days=1,seconds=1)).isoformat()
    except OverflowError:lower=datetime.min.replace(tzinfo=timezone.utc).isoformat()
    try:upper=(end+timedelta(seconds=1)).isoformat()
    except OverflowError:upper=datetime.max.replace(tzinfo=timezone.utc).isoformat()
    records=db.execute('''SELECT r.relative_path,r.utc_first,r.utc_second,r.duration,r.file_size,r.mtime_ns,i.device,i.inode,i.ctime_ns
                          FROM recordings r LEFT JOIN recording_identity i ON i.relative_path=r.relative_path WHERE r.camera=? AND r.utc_first>=? AND r.utc_first<=?
                          UNION
                          SELECT r.relative_path,r.utc_first,r.utc_second,r.duration,r.file_size,r.mtime_ns,i.device,i.inode,i.ctime_ns
                          FROM recordings r LEFT JOIN recording_identity i ON i.relative_path=r.relative_path WHERE r.camera=? AND r.utc_second>=? AND r.utc_second<=?''',
                       (row[0],lower,upper,row[0],lower,upper)).fetchall()
    clips=[];meta={}
    for rel,first,second,duration,size,mtime,device,inode,ctime_ns in records:
        if first is None:continue
        first_time=parse_instant(first)
        if second is not None:
            alt=parse_instant(second)
            if any(t<end and t+timedelta(seconds=duration)>start for t in (first_time,alt)):
                raise AmbiguousRecordingError('recording time is ambiguous after a clock change')
        p=Path(rel);meta[p]=(size,mtime,recorded_identity(device,inode,ctime_ns))
        clips.append(Clip(p,first_time,duration))
    result=create_plan(clips,start,end)
    if not result.segments:raise NoRecordingsError('no recordings in selected range')
    return result,meta

def run_one(db: sqlite3.Connection, archive_root: Path, export_root: Path, *,
            archive_lock: Path, minimum_free: int=10_000_000_000, free=None, mount_guard=None) -> int | None:
    """Process one export while retaining a shared archive lock and bounded FDs.

    Retention must use the same lock path in exclusive mode. Recorders and other
    workers additionally coordinate writes; input identity is checked after use.
    """
    mount_guard=mount_guard or (lambda:True)
    if not mount_guard():raise ExportError('archive mount unavailable')
    job=jobs.claim(db)
    if job is None:return None
    output=None;published=False;lockfd=None;failure_reason=['generic']
    try:
        def authorized():
            row=db.execute('SELECT id,username,role,enabled FROM users WHERE id=?',(job.user_id,)).fetchone()
            return bool(row and row[3] and security.may_access(db,(row[0],row[1],row[2],''),job.camera_id,download=True))
        if not authorized():
            failure_reason[0]='permission';raise ExportError('user no longer has access')
        if jobs.cancel_requested(db,job.id):
            failure_reason[0]='cancelled';raise ExportError('export cancelled')
        start=parse_instant(job.request.get('start'));end=parse_instant(job.request.get('end'))
        policy=job.request.get('gaps')
        if policy not in ('skip','black'):raise ValueError('invalid gap policy')
        if export_root.is_symlink() or not export_root.is_dir():raise ExportError('export directory unavailable')
        free=free or (lambda path:space_for(path).free_for_service)
        if free(export_root)<=minimum_free:
            failure_reason[0]='disk_space';raise ExportError('insufficient free space')
        lockfd=os.open(archive_lock,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
        fcntl.flock(lockfd,fcntl.LOCK_SH)
        if not mount_guard():
            failure_reason[0]='storage';raise ExportError('archive mount unavailable')
        result,metadata=plan(db,job.camera_id,start,end)
        events=list(result.segments)+([] if policy=='skip' else list(result.gaps))
        events.sort(key=lambda e:e.start)
        groups=[events[i:i+16] for i in range(0,len(events),16)]
        # A trailing gap needs a real clip in the same encoder batch.
        if len(groups)>1 and all(isinstance(event,Gap) for event in groups[-1]):
            if len(groups[-2])<=1 or not isinstance(groups[-2][-1],Slice):
                raise ExportError('cannot join missing segment')
            groups[-1].insert(0,groups[-2].pop())
        output=export_root/f'export-{job.id}-{secrets.token_hex(8)}.mp4'
        if not jobs.reserve_result(db,job,output.name):raise ExportError('job lease expired')
        total_operations=len(groups)+(1 if len(groups)>1 else 0)
        completed=[0]
        def keep_running(progress):
            global_progress=min(.99,(completed[0]+progress)/total_operations)
            if not authorized():failure_reason[0]='permission';return False
            if not mount_guard():failure_reason[0]='storage';return False
            if free(export_root)<=minimum_free:failure_reason[0]='disk_space';return False
            live=jobs.heartbeat(db,job,global_progress)
            if not live and jobs.cancel_requested(db,job.id):failure_reason[0]='cancelled'
            return live
        with tempfile.TemporaryDirectory(prefix='.sunhill-job-',dir=export_root) as temp:
            intermediate=[]
            for index,group in enumerate(groups):
                sources={};fds=[];before={};cuts=[]
                try:
                    for event in group:
                        if isinstance(event,Gap):
                            cuts.append(Cut(None,0,(event.end-event.start).total_seconds()));continue
                        relative=str(event.path)
                        if relative not in sources:
                            size,mtime,identity=metadata[event.path]
                            try:fd,info=open_confined(archive_root,relative,expected_size=size,expected_mtime_ns=mtime,expected_identity=identity)
                            except (FileNotFoundError,ValueError):
                                failure_reason[0]='source_changed';raise
                            try:acquire_shared(fd)
                            except BaseException:os.close(fd);raise
                            fds.append(fd);sources[relative]=fd;before[fd]=(info.st_size,info.st_mtime_ns,info.st_ino,info.st_dev,info.st_ctime_ns)
                        cuts.append(Cut(sources[relative],event.offset,event.duration))
                    destination=output if len(groups)==1 else Path(temp)/f'part-{index}.mp4'
                    export(cuts,destination,keep_running=keep_running)
                    if len(groups)==1:published=True
                    for fd in fds:
                        after=os.fstat(fd)
                        if before[fd]!=(after.st_size,after.st_mtime_ns,after.st_ino,after.st_dev,after.st_ctime_ns):
                            failure_reason[0]='source_changed';destination.unlink(missing_ok=True);raise ExportError('source recording changed')
                    if len(groups)==1:published=True
                    else:
                        intermediate.append(Cut(destination,0,math.fsum(c.duration for c in cuts)))
                    completed[0]+=1
                    if not keep_running(0):raise ExportError('export stopped')
                finally:
                    for fd in fds:os.close(fd)
            if len(groups)>1:
                export_many(intermediate,output,keep_running=keep_running)
                published=True
        # The final encoder can return after its last periodic check. Do not
        # mark output ready if the disk or archive changed in that window.
        if not keep_running(0):raise ExportError('export stopped before publication')
        if not jobs.complete(db,job,output.name,require_access=True,require_reserved=True):
            if not authorized():failure_reason[0]='permission'
            elif jobs.cancel_requested(db,job.id):failure_reason[0]='cancelled'
            output.unlink(missing_ok=True);published=False;raise ExportError('job lease expired')
        return job.id
    except Exception as error:
        if output is not None and published:output.unlink(missing_ok=True)
        if failure_reason[0]=='generic':
            if isinstance(error,AmbiguousRecordingError):failure_reason[0]='ambiguous_time'
            elif isinstance(error,NoRecordingsError):failure_reason[0]='no_recordings'
            elif isinstance(error,MediaError):failure_reason[0]='media'
            try:
                if jobs.cancel_requested(db,job.id):failure_reason[0]='cancelled'
            except sqlite3.Error:
                pass
        jobs.fail(db,job,reason=failure_reason[0])
        return job.id
    finally:
        if lockfd is not None:os.close(lockfd)
