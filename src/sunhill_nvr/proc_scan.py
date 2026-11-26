"""Linux /proc regular-file activity inspection for a privileged helper."""
from __future__ import annotations
import errno
import os
import stat
import sys
from pathlib import Path
from . import fd_mount

class IncompleteScan(RuntimeError):pass

def validate_proc_mountinfo(data: str, mount_id: int) -> None:
    """Reject procfs options that can hide processes before scanning them."""
    matches=[]
    for line in data.splitlines():
        left,separator,right=line.partition(' - ')
        fields=left.split();details=right.split()
        if not fields or fields[0]!=str(mount_id):continue
        if not separator or len(fields)<6 or len(details)<3:
            raise IncompleteScan('invalid proc mount information')
        matches.append((fields,details))
    if len(matches)!=1:raise IncompleteScan('proc mount is not visible')
    fields,details=matches[0]
    if fields[4]!='/proc' or details[0]!='proc':raise IncompleteScan('unexpected proc filesystem')
    options=fields[5].split(',')+details[2].split(',')
    for option in options:
        key,sep,value=option.partition('=')
        if key=='hidepid' and (not sep or value not in ('0','off')):
            raise IncompleteScan('proc hidepid prevents a reliable activity scan')

def require_proc_visibility() -> None:
    if not sys.platform.startswith('linux'):raise IncompleteScan('Linux proc filesystem required')
    try:
        fd=os.open('/proc',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            _,mount_id=fd_mount.identity(fd)
            if mount_id is None:raise IncompleteScan('proc mount identity unavailable')
            data=Path('/proc/self/mountinfo').read_text(encoding='utf-8')
            validate_proc_mountinfo(data,mount_id)
        finally:os.close(fd)
    except (OSError,UnicodeError,fd_mount.MountUnavailable) as exc:
        raise IncompleteScan('cannot verify proc visibility') from exc

def snapshot(proc_root: Path = Path('/proc'), *, ignore: set[int] | None = None) -> set[tuple[int,int]]:
    """Return (device,inode) for open regular files; fail closed on permission errors.

    Processes and descriptors may disappear while scanning; ENOENT/ESRCH are safe
    to ignore. A caller must have access to every process in its PID namespace and
    account for writers that can open files after the snapshot.
    """
    if proc_root==Path('/proc'):require_proc_visibility()
    ignored=set(ignore or ())|{os.getpid()}
    opened=set()
    try:processes=list(proc_root.iterdir())
    except OSError as exc:raise IncompleteScan('cannot enumerate processes') from exc
    def vanished(entry: Path) -> bool:
        try:entry.stat();return False
        except OSError as exc:
            if exc.errno in (errno.ENOENT,errno.ESRCH):return True
            raise IncompleteScan('cannot verify process visibility') from exc

    for process in processes:
        if not process.name.isdigit() or int(process.name) in ignored:continue
        # A Linux thread can unshare its descriptor table. Inspect each task,
        # not just the thread-group leader. If a surviving process has no
        # visible task directory (e.g. a terminated leader), fail closed.
        try:threads=list((process/'task').iterdir())
        except OSError as exc:
            if exc.errno in (errno.ENOENT,errno.ESRCH) and vanished(process):continue
            raise IncompleteScan('cannot enumerate process tasks') from exc
        if not threads and not vanished(process):raise IncompleteScan('process tasks not visible')
        for thread in threads:
            if not thread.name.isdigit():continue
            try:descriptors=list((thread/'fd').iterdir())
            except OSError as exc:
                if exc.errno in (errno.ENOENT,errno.ESRCH) and vanished(thread):continue
                raise IncompleteScan('cannot inspect task descriptors') from exc
            for descriptor in descriptors:
                try:info=descriptor.stat()
                except OSError as exc:
                    if exc.errno in (errno.ENOENT,errno.ESRCH):continue
                    raise IncompleteScan('cannot inspect a descriptor') from exc
                if stat.S_ISREG(info.st_mode):opened.add((info.st_dev,info.st_ino))
    return opened

def in_snapshot(file: Path, opened: set[tuple[int,int]]) -> bool:
    info=file.lstat()
    if not stat.S_ISREG(info.st_mode):raise ValueError('not a regular file')
    return (info.st_dev,info.st_ino) in opened
