"""Interactive initialization and unprivileged background workers."""
from __future__ import annotations
import argparse
import contextlib
import getpass
import logging
from .config_reconcile import ReconcileError
import os
import signal
import sys
import tempfile
import time
import pwd
import sqlite3
from urllib.parse import quote
from pathlib import Path
from . import archive,database,jobs,security,workers,activity_rpc,controller,ftp_accounts
from .atomic_publish import publish_new_file
from .config import load
from .export_service import run_one,cleanup_stale_temporary

def _publish_database(temporary: Path, destination: Path) -> None:
    """Keep the initialization entry point; use the shared Linux primitive."""
    publish_new_file(temporary,destination)

def _initialize(config, credentials: tuple[str,str] | None) -> str | None:
    if config.database.exists() or config.database.is_symlink():
        raise RuntimeError('database already exists; refusing to overwrite')
    fd,name=tempfile.mkstemp(prefix='.sunhill-init-',suffix='.sqlite',dir=config.database.parent)
    os.close(fd);temporary=Path(name)
    try:
        with contextlib.closing(database.open_db(temporary)) as db:
            security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
            if credentials is not None:security.complete_console_setup(db,*credentials)
            with db:
                db.executemany('INSERT INTO system_settings(key,value) VALUES (?,?)',
                               [('minimum_free',str(config.minimum_free)),('free_buffer',str(config.free_buffer))])
            token=security.issue_setup_token(db) if credentials is None else None
            if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()!=(0,0,0):
                raise RuntimeError('cannot finalize initial database')
        # Atomic no-overwrite publish prevents an interrupted initialization from
        # leaving a half-created primary database.
        _publish_database(temporary,config.database)
        return token
    finally:
        temporary.unlink(missing_ok=True)
        for suffix in ('-wal','-shm'):
            Path(str(temporary)+suffix).unlink(missing_ok=True)

def initialize(config, username: str, password: str) -> None:
    _initialize(config,(username,password))

def initialize_setup(config) -> str:
    return _initialize(config,None)

def migrate_database(config) -> None:
    """Migrate an existing database; the caller must first stop all writers."""
    with contextlib.closing(database.open_db(config.database)) as db:
        result=db.execute('PRAGMA integrity_check').fetchone()
        if not result or result[0]!='ok':raise RuntimeError('database integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise RuntimeError('database foreign key check failed')
        security.initialize(db);archive.initialize(db);jobs.initialize(db);database.migrate(db)
        # Preserve existing external mount location, or learn it from the
        # currently accessible archive. Never bind the database to its UUID.
        from .storage_paths import required_location,remember_location
        location=required_location(db,config.archive)
        if location is None:
            try:remember_location(db,config.archive)
            except (OSError,ValueError,RuntimeError):pass
        else:
            with db:
                db.execute("INSERT INTO system_settings(key,value) VALUES('archive_mountpoint',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(location),))
        result=db.execute('PRAGMA integrity_check').fetchone()
        if not result or result[0]!='ok':raise RuntimeError('database integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise RuntimeError('database foreign key check failed')

def sleep_interruptibly(interval: float, stopping: list[bool], *, monotonic=time.monotonic, sleep=time.sleep) -> None:
    """Keep timer workers responsive to SIGTERM while waiting between cycles."""
    deadline=monotonic()+interval
    while not stopping[0]:
        remaining=deadline-monotonic()
        if remaining<=0:return
        sleep(min(1.0,remaining))

def export_loop(config, *, interval: float=3.0, once: bool=False) -> None:
    stopping=[False]
    next_cleanup=0.0
    def stop(signum,frame):stopping[0]=True
    if not once:
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while not stopping[0]:
        # A candidate may start before its health check commits the update.
        # Never claim jobs or delete output while that database can roll back.
        if not workers.writes_permitted():
            if once:return
            sleep_interruptibly(1,stopping);continue
        with contextlib.closing(database.open_db(config.database)) as db:
            minimum,_=workers.storage_limits(db,config)
            try:guard=workers.storage_guard(db,config.archive)
            except (OSError,ValueError,RuntimeError):
                if once:raise
                sleep_interruptibly(interval,stopping);continue
            result=run_one(db,config.archive,config.exports,archive_lock=config.archive_lock,minimum_free=minimum,mount_guard=guard)
            if result is None and time.monotonic()>=next_cleanup and workers.writes_permitted():
                cleanup_stale_temporary(db,config.exports,active=lambda path:activity_rpc.query(config.activity_socket,'exports',[str(path.relative_to(config.exports))])[0])
                next_cleanup=time.monotonic()+3600
        if once:return
        if result is None:sleep_interruptibly(interval,stopping)

def periodic(config, action, interval: float, *, once: bool=False, cancellable: bool=False) -> None:
    stopping=[False]
    def stop(signum,frame):stopping[0]=True
    if not once:
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while not stopping[0]:
        # Do not deliver external notifications, adjust camera clocks or mutate
        # index/preview state before the candidate release is committed.
        if not workers.writes_permitted():
            if once:return
            sleep_interruptibly(1,stopping);continue
        try:
            with contextlib.closing(database.open_db(config.database)) as db:
                if cancellable:action(config,db,should_stop=lambda:stopping[0])
                else:action(config,db)
        except Exception as error:
            logging.error('Sunhill worker error: %s',type(error).__name__)
            if once:raise
        if once:return
        sleep_interruptibly(interval,stopping)

def recorder_loop(config, *, once: bool=False) -> None:
    worker=workers.recorder_supervisor(config)
    restored_process_faults=False
    from .recording_settings import segment_seconds
    duration=300
    stopping=[False]
    def stop(signum,frame):stopping[0]=True
    if not once:
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        while not stopping[0]:
            # Keep the service active for upgrade health checks, but do not
            # spawn recorders, publish staging files or mutate candidate DB state
            # before the release has committed. Staged segments remain intact.
            if not workers.writes_permitted():
                worker.stop_all()
                if once:return
                sleep_interruptibly(1,stopping);continue
            try:
                with contextlib.closing(database.open_db(config.database)) as db:
                    guard=workers.storage_guard(db,config.archive)
                    if not guard():raise workers.WorkerError('archive unavailable')
                    configured=workers.selected_recorders(db)
                    if not restored_process_faults:
                        try:
                            worker.process_failures.update(name.split(':',1)[1] for (name,) in db.execute("SELECT name FROM system_health WHERE name GLOB 'recorder_process:*' AND state='recording_process_error'") if name.split(':',1)[1] in configured)
                            restored_process_faults=True
                        except sqlite3.Error as error:
                            logging.error('Sunhill recorder health restore error: %s',type(error).__name__)
                    cameras=configured if workers.recording_space_available(db,config) else set()
                    try:duration=segment_seconds(db)
                    except sqlite3.Error as error:
                        logging.error('Sunhill recording settings read error: %s',type(error).__name__)
                    worker.tick(cameras,time.time(),configured_cameras=configured,segment_seconds=duration,
                                on_published=lambda path,signature,inspection:archive.index_published(db,config.archive,path,config.timezone,signature,inspection,mount_check=guard))
                    try:
                        workers.update_recorder_faults(db,worker.corrupt_cameras,blocked=worker.blocked_cameras,
                                                       staging=worker.preparation_failures,process=worker.process_failures if restored_process_faults else None)
                        workers.update_recorder_audio(db,worker.audio_observations)
                        worker.audio_observations.clear()
                    except (sqlite3.Error,ValueError) as error:
                        # A monitoring write failure must not stop healthy cameras.
                        logging.error('Sunhill recorder health error: %s',type(error).__name__)
            except Exception as error:
                logging.error('Sunhill recorder error: %s',type(error).__name__)
                worker.stop_all()
                if once:raise
            if once:return
            sleep_interruptibly(5,stopping)
    finally:
        worker.stop_all()

def control_once(config, service_user: str='sunhill') -> None:
    try:
        account=pwd.getpwnam(service_user)
        supplementary_gids=tuple(os.getgrouplist(service_user,account.pw_gid))
        # Read-only access: privileged controller must not create root-owned SQLite WAL files.
        uri='file:'+quote(str(config.database),safe='/')+'?mode=ro'
        with contextlib.closing(sqlite3.connect(uri,uri=True,timeout=10)) as db:
            def reconcile_with_groups(db,state,archive,uid,gid):
                controller.reconcile(db,state,archive,uid,gid,supplementary_gids=supplementary_gids)
            controller.guarded_reconcile(db,config,account.pw_uid,account.pw_gid,guard_factory=workers.storage_guard,reconcile_config=reconcile_with_groups,
                                         writer_check=lambda:workers.recording_space_available(db,config))
    except Exception:
        # If the guard itself cannot run, keeping FTP available would allow
        # writes without knowing which filesystem backs the archive path.
        try:controller.set_ftp_running(False)
        except controller.ControllerError:logging.exception('Failed to stop FTP after controller error')
        raise


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(prog='sunhill-nvr')
    parser.add_argument('--config',default='/opt/sunhill-nvr/config/config.json')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    sub.add_parser('init-setup')
    sub.add_parser('complete-setup',help='Finish initial setup on an existing empty database')
    sub.add_parser('renew-setup')
    sub.add_parser('migrate')
    worker=sub.add_parser('export-worker');worker.add_argument('--once',action='store_true')
    index=sub.add_parser('index-worker');index.add_argument('--once',action='store_true')
    health=sub.add_parser('health-worker');health.add_argument('--once',action='store_true')
    clock=sub.add_parser('clock-worker');clock.add_argument('--once',action='store_true')
    stream=sub.add_parser('stream-health-worker');stream.add_argument('--once',action='store_true')
    recorder=sub.add_parser('recorder-worker');recorder.add_argument('--once',action='store_true')
    retention=sub.add_parser('retention-worker');retention.add_argument('--once',action='store_true');retention.add_argument('--apply',action='store_true')
    preview=sub.add_parser('retention-preview-worker');preview.add_argument('--once',action='store_true')
    enroll=sub.add_parser('enroll-storage');enroll.add_argument('--allow-rootfs',action='store_true')
    sub.add_parser('activity-helper')
    sub.add_parser('control-once')
    sub.add_parser('check-storage')
    sub.add_parser('prepare-storage')
    sub.add_parser('prepare-ftp-homes')
    args=parser.parse_args(argv)
    config=load(Path(args.config))
    if args.command in ('init','complete-setup'):
        name=input('Nazwa administratora: ').strip()
        password=getpass.getpass(f'Hasło administratora (min. {security.MIN_PASSWORD_LENGTH} znaków): ')
        confirmation=getpass.getpass('Powtórz hasło: ')
        if password!=confirmation:raise security.PasswordValidationError('Hasła nie są identyczne.')
        if args.command=='init':initialize(config,name,password)
        else:
            if not config.database.is_file() or config.database.is_symlink():raise ValueError('existing database required')
            with contextlib.closing(database.open_db(config.database)) as db:security.complete_console_setup(db,name,password)
        return 0
    if args.command=='init-setup':
        print(initialize_setup(config));return 0
    if args.command=='renew-setup':
        with contextlib.closing(database.open_db(config.database)) as db:print(security.issue_setup_token(db))
        return 0
    if args.command=='migrate':migrate_database(config);return 0
    if args.command=='export-worker':export_loop(config,once=args.once);return 0
    if args.command=='index-worker':
        next_full=[0.0]
        from . import index_control
        def index_action(cfg,db,*,should_stop):
            with workers.retention.exclusive_worker_lock(cfg.archive_lock.with_name('index-worker.lock')):
                full=time.monotonic()>=next_full[0]
                state=index_control.read(db)
                if state['state'] in ('queued','running') or (full and state['state']!='paused'):
                    result=index_control.run(cfg,db,automatic=full,should_stop=should_stop)
                    if result is not None and not result.interrupted and full and not any(index_control.read(db).get(k) for k in ('camera','start','end')):next_full[0]=time.monotonic()+6*3600
                else:workers.index_once(cfg,db,full=False,should_stop=should_stop)
        periodic(config,index_action,60,once=args.once,cancellable=True);return 0
    if args.command=='health-worker':
        if args.once:periodic(config,workers.health_once,60,once=True);return 0
        import threading
        from . import camera_events
        stop=threading.Event();thread=threading.Thread(target=camera_events.supervise,args=(config,stop),daemon=True);thread.start()
        try:periodic(config,workers.health_once,60)
        finally:stop.set();thread.join(timeout=6)
        return 0
    if args.command=='clock-worker':periodic(config,workers.clocks_once,900,once=args.once,cancellable=True);return 0
    if args.command=='stream-health-worker':periodic(config,workers.streams_once,300,once=args.once,cancellable=True);return 0
    if args.command=='recorder-worker':recorder_loop(config,once=args.once);return 0
    if args.command=='retention-worker':
        periodic(config,lambda c,d:workers.retention_once(c,d,preview=not args.apply),60,once=args.once);return 0
    if args.command=='retention-preview-worker':periodic(config,workers.retention_preview_once,10,once=args.once);return 0
    if args.command=='enroll-storage':
        from .storage_paths import remember_location
        with contextlib.closing(database.open_db(config.database)) as db:remember_location(db,config.archive,allow_rootfs=args.allow_rootfs)
        return 0
    if args.command=='activity-helper':
        account=pwd.getpwnam('sunhill')
        activity_rpc.serve(config.activity_socket,allowed_uid=account.pw_uid,socket_gid=account.pw_gid,archive=config.archive,staging=config.staging,exports=config.exports)
        return 0
    if args.command=='prepare-storage':
        from .storage_paths import prepare
        account=pwd.getpwnam('sunhill')
        if os.geteuid()!=0:raise ValueError('storage preparation requires root')
        try:
            with contextlib.closing(sqlite3.connect('file:'+quote(str(config.database),safe='/')+'?mode=ro',uri=True,timeout=10)) as db:
                prepare(db,config,account.pw_uid,account.pw_gid)
        except (OSError,ValueError,RuntimeError) as exc:
            logging.warning('Storage preparation unavailable: %s',exc)
        return 0
    if args.command=='control-once':control_once(config);return 0
    if args.command=='prepare-ftp-homes':
        account=pwd.getpwnam('sunhill')
        if os.geteuid()!=account.pw_uid or os.getegid()!=account.pw_gid or os.geteuid()==0:
            raise ValueError('FTP home preparation must run as sunhill')
        uri='file:'+quote(str(config.database),safe='/')+'?mode=ro'
        with contextlib.closing(sqlite3.connect(uri,uri=True,timeout=10)) as db:
            storage_check=workers.storage_guard(db,config.archive)
            def guard():return storage_check() and workers.recording_space_available(db,config)
            ftp_accounts.prepare_homes(config.archive,ftp_accounts.read_published_accounts(config.control_state),guard,uid=account.pw_uid,gid=account.pw_gid)
        return 0
    if args.command=='check-storage':
        uri='file:'+quote(str(config.database),safe='/')+'?mode=ro'
        with contextlib.closing(sqlite3.connect(uri,uri=True,timeout=10)) as db:
            if not workers.storage_guard(db,config.archive)():raise workers.WorkerError('archive storage unavailable')
            if not workers.recording_space_available(db,config):raise workers.WorkerError('recording reserve reached')
        return 0
    return 2

if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError,RuntimeError) as error:
        logging.error('Sunhill command failed: %s',type(error).__name__)
        if isinstance(error,security.PasswordValidationError):
            logging.error('%s',error)
        elif isinstance(error,(controller.ControllerError,ReconcileError)):
            logging.error('Controller: %s',error)
        raise SystemExit(1)
