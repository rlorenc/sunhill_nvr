"""Crash-safe no-overwrite publication of a private file on Linux."""
from __future__ import annotations
import ctypes
import os
from pathlib import Path

def publish_new_file(temporary: Path, destination: Path) -> None:
    """Atomically publish a durable single-link file, without overwrite.

    Linux renameat2 is required on the supported platform. A hard-link followed
    by unlink leaves a two-link file after a crash, which consumers reject.
    """
    if temporary.parent!=destination.parent or temporary==destination:
        raise ValueError('publication requires distinct files in one directory')
    fd=os.open(temporary,os.O_RDONLY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    try:rename=ctypes.CDLL(None,use_errno=True).renameat2
    except AttributeError as exc:raise RuntimeError('atomic file publication unavailable') from exc
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype=ctypes.c_int
    directory=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        if rename(directory,os.fsencode(temporary.name),directory,os.fsencode(destination.name),1)!=0:
            error=ctypes.get_errno();raise OSError(error,os.strerror(error))
        os.fsync(directory)
    finally:os.close(directory)
