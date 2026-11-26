"""Bounded, private H.265 playback cache; originals are never modified."""
from __future__ import annotations
import contextlib
import fcntl
import hashlib
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from . import media,limits
from .files import acquire_shared

MAX_SOURCE=1024**3
MAX_CACHE=2*1024**3
MAX_ENTRIES=128
RESERVE=512*1024**2
TTL=3600
NAME=re.compile(r'[a-f0-9]{64}\.(?:mp4|direct)\Z')


def signature(fd):
    s=os.fstat(fd)
    return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


@contextlib.contextmanager
def locked(root: Path, wait=20):
    root.mkdir(mode=0o700,exist_ok=True)
    info=root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
        raise ValueError('unsafe compatibility cache')
    fd=os.open(root/'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('invalid cache lock')
        deadline=time.monotonic()+wait
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise limits.BusyError('compatibility cache busy')
                time.sleep(.05)
        yield
    finally:os.close(fd)


def _entry(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid():raise ValueError('unsafe cache entry')
        return fd,info
    except BaseException:os.close(fd);raise


def _remove(path):
    fd,info=_entry(path)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return False
        path.unlink();return True
    finally:os.close(fd)


def _prune(root,needed=0,extra=0):
    entries=[];now=time.time()
    for path in root.iterdir():
        if path.name.startswith('.part-') and path.suffix=='.mp4':
            _remove(path);continue
        if not NAME.fullmatch(path.name):continue
        fd,info=_entry(path);os.close(fd)
        if now-info.st_mtime>TTL and _remove(path):continue
        entries.append((info.st_mtime,info.st_size,path))
    total=sum(e[1] for e in entries);count=len(entries)
    for _,size,path in sorted(entries):
        if total+needed<=MAX_CACHE and count+extra<=MAX_ENTRIES and shutil.disk_usage(root).free>=needed+RESERVE:break
        if _remove(path):total-=size;count-=1
    if needed and (total+needed>MAX_CACHE or count+extra>MAX_ENTRIES or shutil.disk_usage(root).free<needed+RESERVE):
        raise media.MediaError('compatibility cache space unavailable')


def cleanup(root: Path):
    """Called periodically by the health worker; skip if a remux is in progress."""
    if not root.exists():return
    try:
        with locked(root,wait=0):_prune(root)
    except (OSError,ValueError,limits.BusyError):pass


def open_compatible(root: Path, source: int) -> tuple[int,os.stat_result] | None:
    """Return a locked hvc1 copy, or None to serve the unchanged source.

    Caller retains the confined, shared-locked source descriptor throughout.
    Same source signature always gives the same representation for byte ranges.
    """
    identity=signature(source)
    key=hashlib.sha256(repr(('hvc1-v1',identity)).encode()).hexdigest()
    output=root/(key+'.mp4');direct=root/(key+'.direct')
    with locked(root):
        _prune(root)
        if direct.exists():
            fd,_=_entry(direct);os.close(fd);os.utime(direct,None,follow_symlinks=False)
            if signature(source)!=identity:raise media.MediaError('source changed during compatibility request')
            return None
        if not output.exists():
            info=media.probe(source,timeout=8)
            video=next(s for s in info['streams'] if s.get('codec_type')=='video')
            if video.get('codec_name')!='hevc' or video.get('codec_tag_string')=='hvc1':
                if signature(source)!=identity:raise media.MediaError('source changed during compatibility probe')
                _prune(root,needed=1,extra=1)
                fd=os.open(direct,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
                return None
            if identity[2]>MAX_SOURCE:raise media.MediaError('segment exceeds compatibility cache limit')
            budget=identity[2]+max(16*1024**2,identity[2]//10)
            _prune(root,needed=budget,extra=1)
            fd,name=tempfile.mkstemp(prefix='.part-',suffix='.mp4',dir=root);os.close(fd)
            temporary=Path(name)
            try:
                args=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y','-protocol_whitelist','file,pipe',
                      '-i',media._source_argument(source),'-map','0:v:0','-map','0:a:0?',
                      '-c','copy','-bsf:v','hevc_mp4toannexb','-tag:v','hvc1','-movflags','+faststart','-fs',str(budget),str(temporary)]
                try:subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,pass_fds=(source,),timeout=20,check=True)
                except (OSError,subprocess.SubprocessError) as exc:raise media.MediaError('HEVC remux failed') from exc
                verified=media.probe(temporary,timeout=8)
                target=next(s for s in verified['streams'] if s.get('codec_type')=='video')
                if target.get('codec_tag_string')!='hvc1' or target.get('codec_name')!='hevc' or temporary.stat().st_size>=budget:
                    raise media.MediaError('invalid compatible segment')
                try:
                    original_duration=float(info['format']['duration']);new_duration=float(verified['format']['duration'])
                    if not (math.isfinite(original_duration) and math.isfinite(new_duration) and original_duration>0 and new_duration>0) or abs(new_duration-original_duration)>0.15:raise ValueError('duration changed')
                except (ValueError,KeyError,TypeError) as exc:raise media.MediaError('compatible duration mismatch') from exc
                if signature(source)!=identity:raise media.MediaError('source changed during remux')
                os.replace(temporary,output)
            finally:temporary.unlink(missing_ok=True)
        fd,info=_entry(output)
        try:
            acquire_shared(fd);os.utime(output,None,follow_symlinks=False)
            if signature(source)!=identity:raise media.MediaError('source changed during compatibility request')
            return fd,info
        except BaseException:os.close(fd);raise
