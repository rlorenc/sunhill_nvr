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
from . import archive,database,jobs,security,workers,activity_rpc,controller
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
            database.retire_ftp(db,now=int(time.time()))
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

def migrate_database(config, *, rtsp_only: bool=False) -> None:
    """Migrate an existing database; the caller must first stop all writers."""
    with contextlib.closing(database.open_db(config.database)) as db:
        result=db.execute('PRAGMA integrity_check').fetchone()
        if not result or result[0]!='ok':raise RuntimeError('database integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise RuntimeError('database foreign key check failed')
        security.initialize(db);archive.initialize(db);jobs.initialize(db);database.migrate(db)
        database.retire_ftp(db,now=int(time.time()))
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

def verify_rtsp_recorders(db, *, probe):
    """Do not retire FTP while an enabled recording camera has no video."""
    from concurrent.futures import ThreadPoolExecutor
    rows=db.execute("SELECT c.slug FROM cameras c JOIN camera_details d ON d.camera_id=c.id WHERE c.enabled=1 AND d.record_mode IN ('rtsp','ftp') ORDER BY c.slug").fetchall()
    slugs=[row[0] for row in rows]
    with ThreadPoolExecutor(max_workers=4) as pool:
        failed=[slug for slug,ok in zip(slugs,pool.map(probe,slugs)) if not ok]
    if failed:raise RuntimeError('Brak działającego RTSP dla kamer: '+', '.join(failed))
    return len(slugs)

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

def control_rtsp_once(config, service_user: str='sunhill') -> None:
    account=pwd.getpwnam(service_user)
    uri='file:'+quote(str(config.database),safe='/')+'?mode=ro'
    with contextlib.closing(sqlite3.connect(uri,uri=True,timeout=10)) as db:
        if not database.rtsp_only(db):raise RuntimeError('RTSP-only database migration required')
        controller.reconcile_gateway(db,config.control_state,account.pw_gid)


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(prog='sunhill-nvr')
    parser.add_argument('--config',default='/opt/sunhill-nvr/config/config.json')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    sub.add_parser('init-setup')
    sub.add_parser('complete-setup',help='Finish initial setup on an existing empty database')
    sub.add_parser('renew-setup')
    migration=sub.add_parser('migrate');migration.add_argument('--rtsp-only',action='store_true')
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
    sub.add_parser('control-rtsp-once')
    sub.add_parser('verify-rtsp-recorders',help='Decode one actual frame from every enabled recording camera; no configuration changes')
    sub.add_parser('check-storage')
    sub.add_parser('prepare-storage')
    firmware=sub.add_parser('camera-firmware',help='Local administrator: ONVIF firmware upload with explicit vendor identity and SHA-256')
    firmware.add_argument('camera_id',type=int);firmware.add_argument('file',type=Path);firmware.add_argument('--sha256',required=True);firmware.add_argument('--manufacturer',required=True);firmware.add_argument('--model',required=True);firmware.add_argument('--hardware',required=True);firmware.add_argument('--confirm',action='store_true');firmware.add_argument('--expected-version',help='Exact target firmware version for read-only verification after upload')
    model=sub.add_parser('analytics-model-install',help='Install a trusted local ONNX model after SHA-256 and inference checks')
    model.add_argument('kind',choices=('coco','face-detector','face-recognizer'));model.add_argument('file',type=Path);model.add_argument('--sha256',required=True)
    check=sub.add_parser('analytics-model-status',help='Inspect local model availability and optionally run actual inference checks')
    check.add_argument('--verify',action='store_true')
    fetch=sub.add_parser('analytics-model-fetch-faces',help='Download pinned OpenCV Zoo YuNet/SFace models (MIT/Apache-2.0)')
    fetch.add_argument('--accept-licenses',action='store_true');fetch.add_argument('--replace',action='store_true')
    faces=sub.add_parser('face-enroll',help='Enroll a consenting known subject; no source image is copied into NVR')
    faces.add_argument('label');faces.add_argument('images',nargs='+',type=Path);faces.add_argument('--consent',action='store_true')
    sub.add_parser('face-list',help='List enrolled profiles (local administrator only)')
    forget=sub.add_parser('face-remove',help='Remove an enrolled profile and suppress its label in historical results');forget.add_argument('profile_id')
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
    if args.command=='migrate':migrate_database(config,rtsp_only=args.rtsp_only);return 0
    if args.command=='verify-rtsp-recorders':
        from . import stream_health
        with contextlib.closing(database.open_db(config.database)) as db:
            verify_rtsp_recorders(db,probe=stream_health.probe)
        return 0
    if args.command=='analytics-model-fetch-faces':
        if os.geteuid()!=0 or not args.accept_licenses:raise ValueError('Wymagany lokalny root i akceptacja licencji modeli')
        if not workers.writes_permitted():raise ValueError('Trwa konserwacja NVR')
        from . import analytics_models
        with analytics_models.provisioning_lock():
            with contextlib.closing(database.open_db(config.database)) as db:
                if db.execute('SELECT count(*) FROM face_profiles').fetchone()[0]:raise ValueError('Usuń profile twarzy przed wymianą modelu')
            for kind,path in analytics_models.fetch_face_models(accept_licenses=True,replace=args.replace).items():print(kind,path)
        return 0
    if args.command=='analytics-model-status':
        if os.geteuid()!=0:raise ValueError('Wymagany lokalny root')
        import json
        from . import analytics_models
        result=analytics_models.status(verify=args.verify)
        print(json.dumps(result,ensure_ascii=False))
        return int(args.verify and any(v.get('inference')!='passed' for v in result.values()))
    if args.command in ('analytics-model-install','face-enroll','face-list','face-remove'):
        if os.geteuid()!=0:raise ValueError('Wymagany lokalny root')
        if args.command!='face-list' and not workers.writes_permitted():raise ValueError('Trwa konserwacja NVR')
        if args.command=='analytics-model-install':
            from . import analytics_models
            # Existing embeddings are model-specific. Do not silently invalidate them.
            with analytics_models.provisioning_lock():
                if args.kind!='coco':
                    with contextlib.closing(database.open_db(config.database)) as db:
                        if db.execute('SELECT count(*) FROM face_profiles').fetchone()[0]:raise ValueError('Usuń profile twarzy przed wymianą modelu')
                print(analytics_models.install(args.kind,args.file,args.sha256));return 0
        from . import face_profiles
        with contextlib.closing(database.open_db(config.database)) as db:
            if args.command=='face-list':
                import json
                print(json.dumps(face_profiles.labels(db),ensure_ascii=False));return 0
            if args.command=='face-remove':
                if not face_profiles.remove(db,args.profile_id):raise ValueError('Nieznany profil')
                return 0
            if not args.consent or not 1<=len(args.images)<=face_profiles.MAX_TEMPLATES:raise ValueError('Wymagana zgoda osoby i od 1 do 5 zdjęć')
            import cv2,numpy
            from . import analytics_models
            with analytics_models.provisioning_lock():
                recognizer=face_profiles.Recognizer(cv2,numpy)
                features=[recognizer.enrollment_image(image) for image in args.images]
                print(face_profiles.enroll(db,args.label,features,model_id=recognizer.model_id));return 0
    if args.command=='camera-firmware':
        from . import camera_firmware,limits
        if os.geteuid()!=0 or not args.confirm:raise ValueError('Wymagany lokalny root i --confirm')
        if not workers.writes_permitted():raise ValueError('Trwa konserwacja NVR')
        if args.expected_version is not None and (not 1<=len(args.expected_version)<=256 or any(ord(c)<32 for c in args.expected_version)):raise ValueError("Nieprawidłowa oczekiwana wersja")
        data=camera_firmware.image(args.file,args.sha256)
        expected={'Manufacturer':args.manufacturer,'Model':args.model,'HardwareId':args.hardware}
        with limits.slot(config.archive_lock.parent,'camera-firmware',count=1):
            with contextlib.closing(database.open_db(config.database)) as db:
                row=db.execute('SELECT d.main_url,d.onvif_url,d.onvif_username,d.onvif_password,d.onvif_auth FROM camera_details d JOIN cameras c ON c.id=d.camera_id WHERE c.id=? AND c.enabled=1',(args.camera_id,)).fetchone()
                if not row:raise ValueError('Kamera nieaktywna lub nie istnieje')
                with db:db.execute('INSERT INTO audit_log(created_at,user_id,action,object_type,object_id) VALUES(?,NULL,?,?,?)',(int(time.time()),'camera.firmware.request','camera',str(args.camera_id)))
                try:camera_firmware.upload(row,data,expected)
                except camera_firmware.FirmwareError:
                    with db:db.execute('INSERT INTO audit_log(created_at,user_id,action,object_type,object_id) VALUES(?,NULL,?,?,?)',(int(time.time()),'camera.firmware.unconfirmed','camera',str(args.camera_id)))
                    raise
                with db:db.execute('INSERT INTO audit_log(created_at,user_id,action,object_type,object_id) VALUES(?,NULL,?,?,?)',(int(time.time()),'camera.firmware.uploaded','camera',str(args.camera_id)))
                if args.expected_version:
                    verified=camera_firmware.verify_installed(row,expected,args.expected_version)
                    with db:db.execute('INSERT INTO audit_log(created_at,user_id,action,object_type,object_id) VALUES(?,NULL,?,?,?)',(int(time.time()),'camera.firmware.version_verified' if verified else 'camera.firmware.version_unconfirmed','camera',str(args.camera_id)))
                    if not verified:print('Upload potwierdzony; oczekiwana wersja niepotwierdzona. Nie ponawiaj aktualizacji.');return 2
                    print('Potwierdzona wersja firmware: '+verified['FirmwareVersion']);return 0
        print('Kamera potwierdziła upload. Sprawdź wersję firmware po restarcie; nie ponawiaj automatycznie.');return 0
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
        from . import camera_events,object_analytics,tamper,motion_boxes
        stop=threading.Event();threads=[threading.Thread(target=worker.supervise,args=(config,stop),daemon=True) for worker in (camera_events,object_analytics,tamper,motion_boxes)]
        for thread in threads:thread.start()
        try:periodic(config,workers.health_once,60)
        finally:
            stop.set()
            for thread in threads:thread.join(timeout=6)
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
    if args.command=='control-rtsp-once':control_rtsp_once(config);return 0
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
