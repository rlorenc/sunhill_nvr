"""Unprivileged index, recorder and retention worker entry points."""
from __future__ import annotations
import contextlib
import json
import os
import logging
import sqlite3
import time
from pathlib import Path
from . import activity_rpc,archive,database,mount,protection,retention,health_service,clock_service,rotation_service,stream_health,service_monitor
from .media import probe
from .files import open_confined,acquire_shared
from .storage import identify_directory as identify,space_for
from .supervisor import Supervisor,probe_rtsp_codec
from .cameras import SLUG
from . import fd_mount

class WorkerError(RuntimeError):pass

MAINTENANCE_FLAG=Path('/opt/sunhill-nvr/config/maintenance.flag')

def writes_permitted() -> bool:
    """Fail closed during activation, including an unreadable maintenance flag."""
    try:MAINTENANCE_FLAG.lstat()
    except FileNotFoundError:return True
    except OSError:return False
    return False

def rotation_permitted() -> bool:
    """No irreversible archive deletion while activation can still roll back."""
    return writes_permitted()

def enroll_storage(db,root: Path, *, inspect=mount.inspect, allow_rootfs: bool=False) -> mount.Mount:
    """Remember a mount location, without binding to a particular disk."""
    info=inspect(root)
    if info.target=='/' and not allow_rootfs:raise WorkerError('refusing system root filesystem')
    try:mount.require_local(info)
    except ValueError as exc:raise WorkerError(str(exc)) from exc
    with db:
        db.execute("INSERT INTO system_settings(key,value) VALUES('archive_mountpoint',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(info.target,))
    return info

def expected_mount(db) -> mount.Mount:
    row=db.execute("SELECT value FROM system_settings WHERE key='archive_mount'").fetchone()
    if not row:raise WorkerError('archive storage has not been enrolled')
    try:
        value=json.loads(row[0])
        if set(value)!={'target','source','fstype','uuid'}:raise ValueError()
        result=mount.Mount(**value)
        mount.require_local(result)
        return result
    except (TypeError,ValueError):raise WorkerError('invalid archive mount configuration')

def storage_guard(db,root: Path):
    from .storage_paths import guard
    return guard(db,root)

def detector(config,scope):
    root=config.archive if scope=='archive' else config.staging
    def active(file):
        relative=str(file.relative_to(root))
        return activity_rpc.query(config.activity_socket,scope,[relative])[0]
    def batch(files):
        # The helper limits both the number of paths and JSON payload size.
        # Split exceptionally long legacy names without weakening its checks.
        result=[];group=[]
        for file in files:
            relative=str(file.relative_to(root))
            proposed=group+[relative]
            raw=json.dumps({'scope':scope,'paths':proposed},separators=(',',':')).encode()+b'\n'
            if len(proposed)>64 or len(raw)>activity_rpc.MAX_REQUEST:
                result.extend(activity_rpc.query(config.activity_socket,scope,group));group=[]
            group.append(relative)
        if group:result.extend(activity_rpc.query(config.activity_socket,scope,group))
        return result
    active.batch=batch
    return active

def storage_limits(db,config) -> tuple[int,int]:
    values=dict(db.execute("SELECT key,value FROM system_settings WHERE key IN ('minimum_free','free_buffer')"))
    try:
        minimum=int(values.get('minimum_free',config.minimum_free))
        buffer=int(values.get('free_buffer',config.free_buffer))
    except (ValueError,TypeError):raise WorkerError('invalid storage limits')
    if minimum<0 or buffer<0 or minimum+buffer>2**63-1:raise WorkerError('invalid storage limits')
    return minimum,buffer

def recording_space_available(db,config, *, inspect=space_for) -> bool:
    """Pause new recording writes at the configured reserve until space returns."""
    minimum,_=storage_limits(db,config)
    free=inspect(config.archive).free_for_service
    if type(free) is not int or free<0:raise WorkerError('invalid free space')
    return free>=minimum

def ensure_camera_directories(db,root: Path,guard) -> None:
    """Create FTP camera roots under the verified archive without following links."""
    if not guard():raise WorkerError('archive mount mismatch')
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    root_fd=os.open(root,flags)
    try:
        root_mount=fd_mount.identity(root_fd)
        for (slug,) in db.execute('SELECT slug FROM cameras WHERE enabled=1'):
            if not isinstance(slug,str) or not SLUG.fullmatch(slug):raise WorkerError('invalid camera directory')
            if not guard():raise WorkerError('archive mount changed')
            try:os.mkdir(slug,0o750,dir_fd=root_fd)
            except FileExistsError:pass
            child=os.open(slug,flags,dir_fd=root_fd)
            try:
                fd_mount.require_same(root_mount,child)
            finally:os.close(child)
    finally:os.close(root_fd)

def index_once(config,db, *, full: bool=True, now: float | None=None, camera=None, dates=None, should_stop=lambda:False, progress=None) -> archive.ScanReport:
    guard=storage_guard(db,config.archive)
    ensure_camera_directories(db,config.archive,guard)
    from datetime import datetime,timedelta
    from zoneinfo import ZoneInfo
    now=time.time() if now is None else now
    today=datetime.fromtimestamp(now,ZoneInfo(config.timezone)).date()
    if not full and dates is None:dates={today+timedelta(days=offset) for offset in (-2,-1,0,1)}
    probed={};verified_streams={}
    def duration(file):
        # Pass the verified descriptor to FFprobe rather than reopening a
        # mutable path. Reject symlinks, nested mounts and concurrent rotation.
        if not guard():raise WorkerError('archive mount mismatch')
        fd,before=open_confined(config.archive,str(file.relative_to(config.archive)))
        try:
            acquire_shared(fd)
            info=probe(fd)
            after=os.fstat(fd)
            if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                raise WorkerError('recording changed during probe')
        finally:os.close(fd)
        streams=info.get('streams')
        if isinstance(streams,list) and all(isinstance(item,dict) for item in streams):
            probed[file]=any(item.get('codec_type')=='audio' for item in streams)
        return float(info['format']['duration'])
    def verified(file,info):
        verified_streams[str(file.relative_to(config.archive))]=probed.pop(file,None)
    def persist_streams(connection):
        connection.executemany('DELETE FROM recording_stream_info WHERE relative_path=?',
                               ((name,) for name,has_audio in verified_streams.items() if has_audio is None))
        connection.executemany('''INSERT INTO recording_stream_info(relative_path,has_audio) VALUES(?,?)
            ON CONFLICT(relative_path) DO UPDATE SET has_audio=excluded.has_audio''',
            ((name,int(has_audio)) for name,has_audio in verified_streams.items() if has_audio is not None))
        verified_streams.clear();probed.clear()
    active=detector(config,'archive')
    report=archive.refresh(db,config.archive,config.timezone,inspect_duration=duration,
                           is_open=active,is_open_batch=getattr(active,'batch',None),mount_check=guard,min_age_seconds=30,now=now,dates=dates,on_verified=verified,on_persist=persist_streams,
                           commit_guard=lambda:retention.shared_archive_lock(config.archive_lock),camera=camera,should_stop=should_stop,progress=progress)
    if report.interrupted:return report
    observations={}
    max_mtime=int(now+300)*1_000_000_000
    for (slug,) in db.execute("SELECT cameras.slug FROM cameras JOIN camera_details ON camera_details.camera_id=cameras.id WHERE cameras.enabled=1 AND camera_details.record_mode IN ('rtsp','ftp')"):
        latest=db.execute('''SELECT r.mtime_ns,s.has_audio FROM recordings r LEFT JOIN recording_stream_info s ON s.relative_path=r.relative_path
            WHERE r.camera=? AND r.mtime_ns<=? ORDER BY r.mtime_ns DESC,r.id DESC LIMIT 1''',(slug,max_mtime)).fetchone()
        if latest is not None and latest[1] is not None:observations[slug]=(latest[0],bool(latest[1]))
    try:update_recorder_audio(db,observations,now=int(now))
    except (sqlite3.Error,ValueError) as error:logging.error('Sunhill audio health error: %s',type(error).__name__)
    return report

def retention_once(config,db,*,preview: bool=True) -> retention.RetentionResult:
    removed=[]
    if not preview and not rotation_permitted():raise retention.RetentionBusy('rotation paused during maintenance')
    try:
        storage_check=storage_guard(db,config.archive)
        def guard():
            if not preview and not rotation_permitted():raise retention.RetentionBusy('rotation paused during maintenance')
            return storage_check()
        if not guard():raise WorkerError('archive mount mismatch')
        marker=identify(config.archive)
        def is_protected(relative):return protection.is_protected(db,relative)
        minimum,buffer=storage_limits(db,config)
        # Start reclaiming halfway through the buffer to avoid repeatedly
        # interrupting continuous recording at the hard reserve.
        trigger=minimum+buffer//2
        def note_remove(relative):
            removed.append(relative)
            with db:db.execute('DELETE FROM recordings WHERE relative_path=?',(relative,))
        result=retention.run(config.archive,marker,minimum=trigger,
                             target=minimum+buffer,
                             lock_file=config.archive_lock,protected=is_protected,
                             active=detector(config,'archive'),preview=preview,mount_guard=guard,timezone_name=config.timezone,
                             on_remove=note_remove if not preview else None)
        if not preview:
            state='rotation_ok' if result.reached_target else 'rotation_blocked'
            previous=db.execute("SELECT state FROM system_health WHERE name='rotation'").fetchone()
            if result.before<trigger or (previous and previous[0]!=state):
                rotation_service.record(db,state,before=result.before,after=result.after,files=len(removed),
                                        protected_count=result.protected_count,protected_bytes=result.protected_bytes)
        return result
    except retention.RetentionBusy:raise
    except Exception:
        if not preview:
            rotation_service.record(db,'rotation_error',files=len(removed))
        raise


def retention_preview_once(config,db) -> None:
    # A separate process-level lock prevents a second worker from declaring a
    # legitimately long scan dead; after a crash the OS releases this lock.
    try:
        with retention.exclusive_worker_lock(config.archive_lock.with_name(config.archive_lock.name+'.preview-worker')):
            rotation_service.run_preview(db,lambda:retention_once(config,db,preview=True),recover_running=True)
    except retention.RetentionBusy:
        return

def recorder_supervisor(config) -> Supervisor:
    return Supervisor(config.staging,config.archive,timezone=config.timezone,active=detector(config,'staging'),archive_lock=config.archive_lock,codec_probe=probe_rtsp_codec)

def update_recorder_faults(db, corrupt: set[str], *, blocked: set[str] | None=None,
                           staging: set[str] | None=None, process: set[str] | None=None, now: int | None=None) -> None:
    """Persist separate faults for damaged segments, publication and staging."""
    groups=[('recorder_corrupt','recording_corrupt','recording_corrupt_cleared',corrupt)]
    if blocked is not None:groups.append(('recorder_publish','recording_publish_error','recording_publish_cleared',blocked))
    if staging is not None:groups.append(('recorder_staging','recording_staging_error','recording_staging_cleared',staging))
    if process is not None:groups.append(('recorder_process','recording_process_error','recording_process_cleared',process))
    if any(not isinstance(slugs,set) or any(not isinstance(slug,str) or not SLUG.fullmatch(slug) for slug in slugs) for _,_,_,slugs in groups):
        raise ValueError('invalid recorder faults')
    now=int(time.time()) if now is None else now
    def existing(prefix,active):
        return {name.split(':',1)[1] for name,state in db.execute('SELECT name,state FROM system_health WHERE name GLOB ?',(prefix+':*',)) if state==active}
    if all(existing(prefix,active)==slugs for prefix,active,_,slugs in groups):return
    db.execute('BEGIN IMMEDIATE')
    try:
        for prefix,active,cleared,slugs in groups:
            old=existing(prefix,active)
            for slug,state in [(slug,active) for slug in sorted(slugs-old)]+[(slug,cleared) for slug in sorted(old-slugs)]:
                key=prefix+':'+slug
                db.execute('INSERT INTO system_health(name,state,changed_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET state=excluded.state,changed_at=excluded.changed_at',(key,state,now))
                row=db.execute('SELECT id FROM cameras WHERE slug=?',(slug,)).fetchone()
                db.execute('INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(?,?,?,?)',(row[0] if row else None,slug,state,now))
        db.commit()
    except BaseException:db.rollback();raise

def update_recorder_audio(db, observations: dict, *, now: int | None=None) -> None:
    """Track whether the most recently observed camera segment has an audio track."""
    if not isinstance(observations,dict):raise ValueError('invalid audio observations')
    for slug,value in observations.items():
        if (not isinstance(slug,str) or not SLUG.fullmatch(slug) or not isinstance(value,tuple) or len(value)!=2
            or type(value[0]) is not int or value[0]<0 or type(value[1]) is not bool):
            raise ValueError('invalid audio observation')
    if not observations:return
    now=int(time.time()) if now is None else now
    if now<0:raise ValueError('invalid observation time')
    maximum=(now+300)*1_000_000_000
    db.execute('BEGIN IMMEDIATE')
    try:
        for slug,(mtime,has_audio) in sorted(observations.items()):
            row=db.execute("SELECT cameras.id FROM cameras JOIN camera_details ON camera_details.camera_id=cameras.id WHERE cameras.slug=? AND cameras.enabled=1 AND camera_details.record_mode IN ('rtsp','ftp')",(slug,)).fetchone()
            if not row:continue
            key='recorder_audio_seen:'+slug
            previous=db.execute('SELECT value FROM system_settings WHERE key=?',(key,)).fetchone()
            try:last=int(previous[0]) if previous else -1
            except (TypeError,ValueError) as exc:raise ValueError('invalid audio state') from exc
            if last>maximum:last=-1  # A corrected clock must not suppress future observations.
            timestamp=min(mtime,maximum)
            if timestamp<last:continue
            state='recording_audio_ok' if has_audio else 'recording_audio_missing'
            health_key='recorder_audio:'+slug
            old=db.execute('SELECT state FROM system_health WHERE name=?',(health_key,)).fetchone()
            db.execute('INSERT INTO system_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(timestamp)))
            db.execute('''INSERT INTO system_health(name,state,changed_at) VALUES(?,?,?)
                ON CONFLICT(name) DO UPDATE SET changed_at=CASE WHEN state<>excluded.state THEN excluded.changed_at ELSE changed_at END,state=excluded.state''',(health_key,state,now))
            if (not old and not has_audio) or (old and old[0]!=state):
                db.execute('INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(?,?,?,?)',(row[0],slug,state,now))
        db.commit()
    except BaseException:db.rollback();raise

def selected_recorders(db) -> set[str]:
    return {row[0] for row in db.execute("SELECT cameras.slug FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id WHERE cameras.enabled=1 AND camera_details.record_mode='rtsp'")}


def health_once(config,db) -> None:
    from . import archive_compat
    archive_compat.cleanup(config.database.parent/'.archive-hvc1-cache')
    try:stale_after=health_service.stale_threshold(db)
    except ValueError:
        logging.error('Sunhill recording health threshold invalid; using default')
        stale_after=health_service.DEFAULT_STALE_AFTER
    health_service.update(db,stale_after=stale_after)
    service_monitor.check(db)
    try:
        guard=storage_guard(db,config.archive)
        if not guard():raise WorkerError('archive mount mismatch')
        minimum,_=storage_limits(db,config)
        health_service.update_storage(db,space_for(config.archive).free_for_service,minimum)
    except (OSError,ValueError,RuntimeError):
        health_service.storage_unavailable(db)
    health_service.deliver(db)

def clocks_once(config,db, *, should_stop=lambda:False) -> None:
    """Check ONVIF clocks separately so slow cameras do not delay health checks."""
    clock_service.check(db,should_stop=should_stop)

def streams_once(config,db, *, should_stop=lambda:False) -> None:
    stream_health.check(db,should_stop=should_stop)
    if not should_stop() and stream_health.gateway_available():
        from . import camera_statistics
        camera_statistics.refresh(db,should_stop=should_stop)
