"""Fail-closed Linux mount identity checks for already-open file descriptors."""
from __future__ import annotations
import os
import sys
from pathlib import Path

class MountUnavailable(RuntimeError):pass
class MountBoundary(RuntimeError):pass

def identity(fd: int, *, platform: str | None=None, fdinfo: Path=Path('/proc/self/fdinfo')) -> tuple[int,int | None]:
    device=os.fstat(fd).st_dev
    platform=sys.platform if platform is None else platform
    if not platform.startswith('linux'):return device,None
    try:
        with (fdinfo/str(fd)).open('r',encoding='ascii') as file:data=file.read(8192)
        values=[line.split(':',1)[1].strip() for line in data.splitlines() if line.startswith('mnt_id:')]
        if len(values)!=1 or not values[0].isdigit() or int(values[0])<=0:raise ValueError()
        return device,int(values[0])
    except (OSError,UnicodeError,ValueError) as exc:
        raise MountUnavailable('cannot verify filesystem mount identity') from exc


def require_same(expected: tuple[int,int | None], fd: int) -> None:
    if identity(fd)!=expected:raise MountBoundary('filesystem mount boundary encountered')
