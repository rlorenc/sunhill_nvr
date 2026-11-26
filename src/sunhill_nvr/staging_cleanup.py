"""Prune old empty recorder session directories without touching their files."""
from __future__ import annotations

import datetime as dt
import os
import re
import stat
import time
from pathlib import Path

from . import fd_mount
from .recorder import SLUG

SESSION = re.compile(r'[a-f0-9]{32}\Z')
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def prune(root: Path, active: set[tuple[str, str]], *, now: float | None = None,
          minimum_age: int = 86400) -> int:
    """Remove only old, empty, validated session trees on the staging mount.

    Existing files, unknown layouts, symlinks and nested mounts are untouched.
    Active sessions are excluded even if all their directories are empty.
    """
    if minimum_age < 86400 or not root.is_absolute() or root.is_symlink():
        raise ValueError('invalid staging cleanup parameters')
    if not isinstance(active, set) or any(
        not isinstance(x, tuple) or len(x) != 2 or not isinstance(x[0], str)
        or not SLUG.fullmatch(x[0]) or not isinstance(x[1], str) or not SESSION.fullmatch(x[1])
        for x in active
    ):
        raise ValueError('invalid active sessions')
    now = time.time() if now is None else now
    rootfd = os.open(root, DIRECTORY_FLAGS)
    removed = 0
    try:
        mount = fd_mount.identity(rootfd)

        def open_child(parent, name):
            fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
            try:
                fd_mount.require_same(mount, fd)
                return fd
            except BaseException:
                os.close(fd)
                raise

        def visit(parent, name, depth, components):
            nonlocal removed
            try:
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISDIR(before.st_mode) or now - before.st_mtime < minimum_age:
                    return
                fd = open_child(parent, name)
                try:
                    opened = os.fstat(fd)
                    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                        return
                    if depth < 3:
                        for child in os.listdir(fd):
                            expected = r'\d{4}' if depth == 0 else r'\d{2}'
                            if not re.fullmatch(expected, child):
                                continue
                            next_parts = components + (child,)
                            try:
                                dt.date(int(next_parts[0]), int(next_parts[1]) if len(next_parts) > 1 else 1,
                                        int(next_parts[2]) if len(next_parts) > 2 else 1)
                            except ValueError:
                                continue
                            visit(fd, child, depth + 1, next_parts)
                    if os.listdir(fd):
                        return
                    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                        return
                    os.rmdir(name, dir_fd=parent)
                    removed += 1
                finally:
                    os.close(fd)
            except (FileNotFoundError, NotADirectoryError, PermissionError, fd_mount.MountBoundary):
                return
            except OSError as error:
                # A concurrent creation makes rmdir fail with ENOTEMPTY; never
                # remove contents or interrupt another camera for a cleanup.
                import errno
                if error.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ELOOP, errno.ENOENT):
                    raise

        for camera in os.listdir(rootfd):
            if not SLUG.fullmatch(camera):
                continue
            try:
                camera_fd = open_child(rootfd, camera)
            except (OSError, fd_mount.MountBoundary):
                continue
            try:
                for session in os.listdir(camera_fd):
                    if SESSION.fullmatch(session) and (camera, session) not in active:
                        visit(camera_fd, session, 0, ())
            finally:
                os.close(camera_fd)
        return removed
    finally:
        os.close(rootfd)
