"""Cross-process concurrency limits for costly media operations."""
from __future__ import annotations
import contextlib
import errno
import fcntl
import os
import re
import stat
from pathlib import Path

class BusyError(RuntimeError):pass

@contextlib.contextmanager
def slot(directory: Path, name: str, count: int = 4):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',name) or not 1<=count<=16:
        raise ValueError('invalid slot group')
    if directory.is_symlink() or not directory.is_dir():raise ValueError('invalid slot directory')
    selected=None
    try:
        for index in range(count):
            file=directory/f'.{name}-{index}.lock'
            fd=os.open(file,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                os.close(fd);raise ValueError('invalid lock file')
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);selected=fd;break
            except OSError as exc:
                os.close(fd)
                if exc.errno not in (errno.EAGAIN,errno.EACCES):raise
        if selected is None:raise BusyError('media worker busy')
        yield
    finally:
        if selected is not None:os.close(selected)
