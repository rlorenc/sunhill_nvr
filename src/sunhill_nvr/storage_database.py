"""Unprivileged database update for a recording directory change."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import shutil
import os
import re
import secrets
import stat
import time
from pathlib import Path
from . import database,mount
from .config import load
from .media_layout import validate_media_layout

def switch(db, archive: Path, staging: Path, *, inspect=mount.inspect, allow_rootfs=False) -> None:
    if not archive.is_absolute() or archive.is_symlink() or archive.resolve(strict=True)!=archive:
        raise ValueError('archive must be an existing canonical directory')
    actual=inspect(archive);stage=inspect(staging)
    mount.require_local(actual)
    if (actual.target=='/' and not allow_rootfs) or not mount.same(actual,stage):
        raise ValueError('archive and recording staging must be on the same data filesystem')
    with db:
        db.execute("INSERT INTO system_settings(key,value) VALUES('archive_mountpoint',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(actual.target,))
        db.execute('DELETE FROM recordings')
        db.execute('UPDATE camera_details SET last_file_at=NULL')
        db.execute("UPDATE export_jobs SET status='failed',error='Zmieniono katalog nagrań',worker=NULL,lease_until=NULL,updated=? WHERE status IN ('queued','running')",(int(time.time()),))

def copy_exports(db,old: Path,new: Path) -> None:
    """Preserve completed downloads without deleting their old copies.

    All copies run as sunhill. Conflicts fail closed; retry accepts identical
    files from an earlier interrupted attempt, never overwrites existing ones.
    """
    from .files import open_export
    from .atomic_publish import publish_new_file
    def digest(fd):
        h=hashlib.sha256()
        with os.fdopen(os.dup(fd),'rb') as stream:
            stream.seek(0)
            for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
        return h.digest()
    for job_id,name in db.execute("SELECT id,result_name FROM export_jobs WHERE status='done' AND result_name IS NOT NULL"):
        if not re.fullmatch(r'export-'+str(job_id)+r'-[a-f0-9]{16}\.mp4',name):raise ValueError('invalid export name')
        fd,info=open_export(old,name)
        temp=new/('.migration-'+secrets.token_hex(16))
        try:
            try:existing,_=open_export(new,name)
            except FileNotFoundError:existing=None
            if existing is not None:
                try:
                    if digest(existing)!=digest(fd):raise ValueError('export destination contains a different file')
                finally:os.close(existing)
                continue
            with os.fdopen(os.dup(fd),'rb') as src,temp.open('xb') as dst:
                os.fchmod(dst.fileno(),0o600);shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
            after=os.fstat(fd)
            if (info.st_size,info.st_mtime_ns,info.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):raise ValueError('export changed during copy')
            publish_new_file(temp,new/name)
        finally:os.close(fd);temp.unlink(missing_ok=True)
    with db:
        db.execute("UPDATE export_jobs SET status='failed',error='Zmieniono katalog eksportów',worker=NULL,lease_until=NULL,result_name=NULL,updated=? WHERE status='running'",(int(time.time()),))
        db.execute("UPDATE export_jobs SET result_name=NULL WHERE status='failed'")


def switch_media(db,cfg,archive: Path,staging: Path,exports: Path) -> None:
    validate_media_layout(archive,staging,exports)
    # Run as the service account, not as root. Never follow a directory symlink.
    for path in (archive,exports,staging):
        if not path.is_absolute() or path.resolve(strict=False)!=path:raise ValueError('invalid media directory')
        if not path.exists():path.mkdir(mode=0o750)
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022:raise ValueError('invalid media directory ownership')
        mount.require_local(mount.inspect(path))
    if not mount.same(mount.inspect(archive),mount.inspect(staging)):raise ValueError('archive and staging filesystem mismatch')
    if exports!=cfg.exports:copy_exports(db,cfg.exports,exports)
    if archive!=cfg.archive:switch(db,archive,staging,allow_rootfs=True)


def main(argv=None) -> int:
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('archive',type=Path);parser.add_argument('--staging',type=Path);parser.add_argument('--exports',type=Path)
    args=parser.parse_args(argv);cfg=load(args.config)
    with contextlib.closing(database.open_db(cfg.database)) as db:
        if args.staging is not None or args.exports is not None:switch_media(db,cfg,args.archive,args.staging or cfg.staging,args.exports or cfg.exports)
        else:switch(db,args.archive,cfg.staging)
    return 0

if __name__=='__main__':raise SystemExit(main())
