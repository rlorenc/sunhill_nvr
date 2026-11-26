"""Persist protected archive paths. Protected fragments protect their source files."""
from __future__ import annotations
import contextlib
import time
from pathlib import PurePosixPath
import sqlite3

MAX_BATCH = 5000

def validate_relative(value: str) -> None:
    if not isinstance(value,str) or not value or '\\' in value or '\0' in value:
        raise ValueError('invalid recording path')
    path=PurePosixPath(value)
    if path.is_absolute() or len(path.parts)!=5 or any(p in ('','.','..') for p in path.parts):
        raise ValueError('invalid recording path')
    if str(path)!=value:
        raise ValueError('non-canonical recording path')

def protect(db: sqlite3.Connection, paths: list[str], user_id: int, reason: str = '', *, now: int | None = None) -> int:
    if not 1 <= len(paths) <= MAX_BATCH or user_id <= 0 or len(reason)>512 or '\0' in reason:
        raise ValueError('invalid protection request')
    for p in paths:validate_relative(p)
    now=int(time.time()) if now is None else now
    unique=tuple(dict.fromkeys(paths))
    count=0
    with contextlib.nullcontext() if db.in_transaction else db:
        for p in unique:
            # Only files already indexed as recordings may be protected.
            if not db.execute('SELECT 1 FROM recordings WHERE relative_path=?',(p,)).fetchone():
                raise ValueError('unknown recording')
            c=db.execute('INSERT OR IGNORE INTO protected_files(relative_path,reason,created_by,created_at) VALUES (?,?,?,?)',(p,reason,user_id,now))
            count+=c.rowcount
    return count

def is_protected(db: sqlite3.Connection, relative: str) -> bool:
    validate_relative(relative)
    return db.execute('SELECT 1 FROM protected_files WHERE relative_path=?',(relative,)).fetchone() is not None

def unprotect(db: sqlite3.Connection, paths: list[str]) -> int:
    if len(paths)>MAX_BATCH:raise ValueError('too many recordings')
    for p in paths:validate_relative(p)
    with contextlib.nullcontext() if db.in_transaction else db:
        count=0
        for p in set(paths):count+=db.execute('DELETE FROM protected_files WHERE relative_path=?',(p,)).rowcount
        return count
