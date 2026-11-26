"""Storage availability, not persistent device identity.

A configured mount *location* is a guard against writing to the system disk.
Device names, UUIDs and old volume markers are not authorization credentials.
"""
from __future__ import annotations
import json
import os
import re
import stat
from pathlib import Path
from . import fd_mount, mount


def mounted(path: Path):
    # fdinfo and mountinfo work inside systemd's mount namespace, without
    # probing block devices or requiring access to filesystem UUID metadata.
    if not path.is_absolute() or path.resolve(strict=True)!=path:
        raise ValueError('storage path must be a real canonical directory')
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        dev,mid=fd_mount.identity(fd)
        info=os.fstat(fd)
        with open('/proc/self/mountinfo',encoding='utf-8') as f:
            for line in f:
                left,sep,right=line.partition(' - ')
                fields=left.split();rest=right.split()
                if sep and fields and fields[0]==str(mid) and len(fields)>=6 and len(rest)>=2:
                    target=re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),fields[4])
                    if rest[0] not in mount.LOCAL_FILESYSTEMS:raise ValueError('unsupported local storage filesystem')
                    return Path(target),dev,info.st_ino,mid
        raise RuntimeError('storage mount not found in current namespace')
    finally:os.close(fd)


def required_location(db, root: Path) -> Path | None:
    # Old versions may have stored the system disk or a different archive.
    # Use only a non-root ancestor as a location hint, never its identity.
    row=db.execute("SELECT value FROM system_settings WHERE key='archive_mountpoint'").fetchone()
    if row is not None:
        value=row[0]
    else:
        row=db.execute("SELECT value FROM system_settings WHERE key='archive_mount'").fetchone()
        try:value=json.loads(row[0]).get('target') if row else None
        except (ValueError,TypeError,AttributeError):value=None
    if not isinstance(value,str):return None
    p=Path(value)
    return p if p!=Path('/') and p.is_absolute() and (p==root or p in root.parents) else None


def check_location(path: Path, required: Path | None):
    snapshot=mounted(path)
    if required is not None:
        location=mounted(required)
        # A systemd bind mount of the archive is fine, but a directory on
        # the underlying system disk after unmounting the data disk is not.
        if location[0]!=required or location[1]==os.stat('/').st_dev or snapshot[1]!=location[1]:
            raise RuntimeError('configured data mount is unavailable')
    return snapshot


def guard(db,root: Path):
    location=required_location(db,root)
    initial=check_location(root,location)
    def valid():
        try:return check_location(root,location)==initial
        except (OSError,ValueError,RuntimeError):return False
    return valid


def remember_location(db,root: Path, *, allow_rootfs=True):
    target,dev,_,_=mounted(root)
    if target==Path('/') and not allow_rootfs:raise ValueError('refusing system root filesystem')
    with db:
        db.execute("INSERT INTO system_settings(key,value) VALUES('archive_mountpoint',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(target),))


def open_directory(path: Path):
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    fd=os.open('/',flags)
    try:
        for part in path.parts[1:]:
            child=os.open(part,flags,dir_fd=fd)
            os.close(fd);fd=child
        return fd
    except BaseException:
        os.close(fd);raise


def prepare(db,cfg,uid:int,gid:int):
    """Create missing configured directories, never chown existing content.

    Called as root by the controller pre-start outside its read-only sandbox.
    The root-owned configuration already restricts media to non-private paths.
    Only a missing leaf under a real parent is created. Never create a mount.
    """
    location=required_location(db,cfg.archive)
    if location is not None:check_location(location,location)
    for p in (cfg.archive,cfg.staging,cfg.exports):
        protected=(Path('/etc'),Path('/usr'),Path('/bin'),Path('/sbin'),Path('/lib'),Path('/lib64'),Path('/boot'),Path('/proc'),Path('/sys'),Path('/dev'),Path('/run'),Path('/root'))
        if any(p==x or x in p.parents or p in x.parents for x in protected):raise ValueError('protected system path')
        app=Path('/opt/sunhill-nvr');media=app/'media'
        if (p==app or p in app.parents or app in p.parents) and media not in p.parents:raise ValueError('protected application path')
        required=location if location is not None and (p==location or location in p.parents) else None
        if p.resolve(strict=False)!=p:raise ValueError('storage symlink is not allowed')
        try:info=p.lstat()
        except FileNotFoundError:
            parent=p.parent
            check_location(parent,required)
            fd=open_directory(parent)
            try:
                # Only create the explicitly configured leaf. Do not take over
                # existing directories, even if a concurrent process creates it.
                parent_info=os.fstat(fd)
                # Only the trusted root or service owner may control a parent
                # in which the privileged helper creates a configured leaf.
                if parent_info.st_uid not in (0,uid) or parent_info.st_mode&0o022:
                    raise ValueError('unsafe media parent')
                if (parent_info.st_dev,parent_info.st_ino)!=tuple(check_location(parent,required)[1:3]):
                    raise RuntimeError('media parent changed')
                if parent_info.st_uid==0:
                    os.mkdir(p.name,0o750,dir_fd=fd)
                    child=os.open(p.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                    try:
                        fd_mount.require_same(fd_mount.identity(fd),child)
                        os.fchown(child,uid,gid);os.fchmod(child,0o750)
                    finally:os.close(child)
                else:
                    # Never perform privileged chown in a service-writable
                    # directory. Create nested directories as the service UID.
                    if os.geteuid()==uid:
                        os.mkdir(p.name,0o750,dir_fd=fd)
                    else:
                        pid=os.fork()
                        if pid==0:
                            try:
                                os.setgroups([gid]);os.setgid(gid);os.setuid(uid)
                                os.umask(0o027);os.mkdir(p.name,0o750,dir_fd=fd)
                            except BaseException:os._exit(1)
                            os._exit(0)
                        _,status=os.waitpid(pid,0)
                        if status!=0:raise OSError('cannot create directory as service account')
            finally:os.close(fd)
            info=p.lstat()
        if not stat.S_ISDIR(info.st_mode):raise ValueError('invalid media directory')
        check_location(p,required)
    if mounted(cfg.archive)[1]!=mounted(cfg.staging)[1]:raise ValueError('archive and staging must share a filesystem')
