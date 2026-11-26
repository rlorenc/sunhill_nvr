"""Unprivileged SQLite operations for the privileged upgrade coordinator."""
from __future__ import annotations

import argparse
import contextlib
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

from . import database
from .config import load


def backup(config, destination: Path) -> None:
    with contextlib.closing(database.open_db(config.database)) as db:
        database.backup(db,destination)


def restore(config, source: Path) -> None:
    info=source.lstat()
    if source.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o077:
        raise ValueError('backup must be a private regular file')
    uri='file:'+quote(str(source),safe='/')+'?mode=ro'
    with contextlib.closing(sqlite3.connect(uri,uri=True,timeout=10)) as saved:
        if saved.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise RuntimeError('backup integrity check failed')
        if saved.execute('PRAGMA foreign_key_check').fetchone() is not None:raise RuntimeError('backup foreign key check failed')
        with contextlib.closing(database.open_db(config.database)) as target:
            saved.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise RuntimeError('restored database integrity check failed')
            if target.execute('PRAGMA foreign_key_check').fetchone() is not None:raise RuntimeError('restored database foreign key check failed')


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(prog='sunhill-upgrade-database')
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('command',choices=('backup','restore'))
    parser.add_argument('file',type=Path)
    args=parser.parse_args(argv);config=load(args.config)
    if args.command=='backup':backup(config,args.file)
    else:restore(config,args.file)
    return 0


if __name__=='__main__':raise SystemExit(main())
