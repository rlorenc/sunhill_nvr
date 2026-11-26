"""Explicit storage accounting and mount identity checks for retention workers."""
from __future__ import annotations
import os
import stat
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Space:
    total: int
    free_for_service: int
    free_including_reserved: int

def space_for(path: Path) -> Space:
    """Use f_bavail, not f_bfree, so filesystem-reserved space is excluded."""
    values = os.statvfs(path)
    fragment_size = values.f_frsize or values.f_bsize
    return Space(values.f_blocks*fragment_size, values.f_bavail*fragment_size, values.f_bfree*fragment_size)

@dataclass(frozen=True)
class MountIdentity:
    root_device: int
    marker_device: int
    marker_inode: int

def identify(root: Path, marker_name: str = '.sunhill-volume-id') -> MountIdentity:
    """Capture a pre-provisioned marker, rejecting links, malformed markers and mounts.

    The marker must be created by the installer on the intended filesystem and the
    resulting identity persisted. Marker validation is an additional guard; a
    production installer must also bind identity to a persistent filesystem UUID.
    """
    if not marker_name or '/' in marker_name or marker_name in ('.','..'):
        raise ValueError('invalid marker name')
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError('invalid archive root')
    marker = root / marker_name
    info = marker.lstat()
    root_info = root.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_dev != root_info.st_dev:
        raise RuntimeError('invalid archive marker')
    return MountIdentity(root_info.st_dev,info.st_dev,info.st_ino)

@dataclass(frozen=True)
class DirectoryIdentity:
    device: int
    inode: int
    mount_id: int | None


def identify_directory(root: Path) -> DirectoryIdentity:
    from .fd_mount import identity
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        dev,mid=identity(fd)
        return DirectoryIdentity(dev,os.fstat(fd).st_ino,mid)
    finally:os.close(fd)


def matches(root: Path, expected: MountIdentity | DirectoryIdentity, marker_name: str = '.sunhill-volume-id') -> bool:
    try:
        return (identify_directory(root) if isinstance(expected,DirectoryIdentity) else identify(root,marker_name)) == expected
    except (OSError,ValueError,RuntimeError):
        return False
