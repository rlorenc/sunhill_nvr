"""Database creation, migrations and connection policy for Sunhill services."""
from __future__ import annotations
import os
import sqlite3
import stat
from pathlib import Path

MIGRATIONS = {
1: '''
CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE camera_details (
 camera_id INTEGER PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
 main_url TEXT NOT NULL, sub_url TEXT,
 record_mode TEXT NOT NULL CHECK(record_mode IN ('ftp','rtsp','none')),
 last_file_at INTEGER, last_error TEXT, recording_since INTEGER NOT NULL DEFAULT 0,
 onvif_url TEXT, onvif_username TEXT, onvif_password TEXT, clock_offset REAL, clock_checked INTEGER, clock_error TEXT
);
CREATE TABLE ftp_accounts (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 home_camera_id INTEGER REFERENCES cameras(id) ON DELETE CASCADE
);
CREATE TABLE camera_ftp_accounts (
 camera_id INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
 ftp_account_id INTEGER NOT NULL REFERENCES ftp_accounts(id) ON DELETE RESTRICT,
 PRIMARY KEY(camera_id,ftp_account_id)
);
CREATE TABLE protected_files (
 relative_path TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '',
 created_by INTEGER NOT NULL REFERENCES users(id), created_at INTEGER NOT NULL
);
CREATE TABLE saved_layouts (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL, camera_ids TEXT NOT NULL,
 UNIQUE(user_id,name)
);
CREATE TABLE camera_health (
 camera_id INTEGER PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
 state TEXT NOT NULL, changed_at INTEGER NOT NULL, last_checked INTEGER NOT NULL
);
CREATE TABLE pending_notifications (
 id INTEGER PRIMARY KEY, camera_id INTEGER REFERENCES cameras(id) ON DELETE CASCADE,
 source TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE system_health (
 name TEXT PRIMARY KEY, state TEXT NOT NULL, changed_at INTEGER NOT NULL
);
CREATE TABLE event_markers (
 id INTEGER PRIMARY KEY, camera_id INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
 started_at INTEGER NOT NULL, ended_at INTEGER, event_type TEXT NOT NULL,
 source TEXT NOT NULL, UNIQUE(camera_id,started_at,event_type,source)
);
CREATE TABLE audit_log (
 id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL,
 user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 action TEXT NOT NULL, object_type TEXT NOT NULL, object_id TEXT NOT NULL
);
''',
2: '''
CREATE TABLE rotation_history (
 id INTEGER PRIMARY KEY, created INTEGER NOT NULL, state TEXT NOT NULL,
 before_bytes INTEGER, after_bytes INTEGER, files_count INTEGER NOT NULL,
 protected_count INTEGER NOT NULL, protected_bytes INTEGER NOT NULL
);
CREATE TABLE rotation_preview_jobs (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed')),
 created INTEGER NOT NULL, updated INTEGER NOT NULL, result_json TEXT, error TEXT
);
CREATE INDEX idx_rotation_preview_status ON rotation_preview_jobs(status,created,id);
''',
3: '''
CREATE TABLE stream_health (
 camera_id INTEGER PRIMARY KEY REFERENCES cameras(id) ON DELETE CASCADE,
 state TEXT NOT NULL CHECK(state IN ('unknown','available','unavailable')),
 failures INTEGER NOT NULL DEFAULT 0, checked_at INTEGER NOT NULL, changed_at INTEGER NOT NULL
);
''',
4: '''
CREATE TABLE service_health (
 name TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('unknown','active','inactive')),
 failures INTEGER NOT NULL DEFAULT 0, checked_at INTEGER NOT NULL, changed_at INTEGER NOT NULL
);
''',
5: '''
ALTER TABLE camera_details ADD COLUMN onvif_auth TEXT NOT NULL DEFAULT 'digest' CHECK(onvif_auth IN ('digest','wsse'));
''',
6: '''
CREATE INDEX idx_pending_notifications_source_id ON pending_notifications(source,id);
''',
7: '''
CREATE TABLE IF NOT EXISTS export_cancel_requests (
 job_id INTEGER PRIMARY KEY REFERENCES export_jobs(id) ON DELETE CASCADE,
 requested_at INTEGER NOT NULL
);
''',
8: '''
ALTER TABLE pending_notifications ADD COLUMN origin TEXT NOT NULL DEFAULT '' CHECK(origin IN ('','onvif'));
'''
}

def open_db(path: Path, *, create: bool = False) -> sqlite3.Connection:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('database path must be absolute and not a symlink')
    if create:
        # Use an exclusive create and restrictive initial mode; caller provisions
        # a private directory and is responsible for group ownership if shared.
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
        os.close(fd)
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
        raise ValueError('database must be a private regular file')
    db=sqlite3.connect(str(path),timeout=10)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        if db.execute('PRAGMA journal_mode=WAL').fetchone()!=('wal',):
            raise RuntimeError('database does not support WAL journal mode')
        return db
    except BaseException:
        db.close();raise

def migrate(db: sqlite3.Connection) -> None:
    """Apply ordered idempotent migrations transactionally.

    Core users/cameras tables must be initialized before this migration.
    """
    db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)')
    db.commit()
    completed={r[0] for r in db.execute('SELECT version FROM schema_migrations')}
    unknown=completed-set(MIGRATIONS)
    if unknown:
        raise RuntimeError('database version newer than application')
    for version,sql in sorted(MIGRATIONS.items()):
        if version in completed:continue
        # Each migration consists of static statements shipped in the release.
        statements=[x.strip() for x in sql.split(';') if x.strip()]
        db.execute('BEGIN IMMEDIATE')
        try:
            for statement in statements:db.execute(statement)
            db.execute('INSERT INTO schema_migrations(version) VALUES (?)',(version,))
            db.commit()
        except BaseException:
            db.rollback();raise

def allocate_camera_id(db: sqlite3.Connection) -> int:
    """Never assign a deleted camera's identity to a different camera.

    Historical exports and saved layouts can outlive the original camera.
    Call under a write transaction so concurrent creation is serialized.
    """
    if not db.in_transaction:raise RuntimeError('camera allocation requires a transaction')
    previous=db.execute("SELECT value FROM system_settings WHERE key='camera_id_highwater'").fetchone()
    try:highwater=int(previous[0]) if previous else 0
    except (TypeError,ValueError) as exc:raise RuntimeError('invalid camera identity counter') from exc
    if not 0<=highwater<=9_999_999_999:raise RuntimeError('invalid camera identity counter')
    camera_max=db.execute('SELECT COALESCE(MAX(id),0) FROM cameras').fetchone()[0]
    export_max=db.execute('SELECT COALESCE(MAX(camera_id),0) FROM export_jobs').fetchone()[0]
    audit_max=db.execute("SELECT COALESCE(MAX(CAST(object_id AS INTEGER)),0) FROM audit_log WHERE object_type='camera' AND action IN ('camera.create','camera.delete')").fetchone()[0]
    result=max(highwater,camera_max,export_max,audit_max)+1
    if result>9_999_999_999:raise RuntimeError('camera identity space exhausted')
    db.execute("INSERT INTO system_settings(key,value) VALUES('camera_id_highwater',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(result),))
    return result

def backup(db: sqlite3.Connection, target: Path) -> None:
    """Produce a consistent SQLite backup without replacing an existing file."""
    import contextlib
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
    os.close(fd)
    try:
        with contextlib.closing(sqlite3.connect(str(target))) as dest:
            db.backup(dest)
            # Backups must remain self-contained after the connection closes;
            # otherwise WAL sidecars may be required for restoration.
            if dest.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower()!='delete':
                raise RuntimeError('cannot finalize backup journal')
            if dest.execute('PRAGMA integrity_check').fetchone()!=('ok',):
                raise RuntimeError('backup integrity check failed')
            if dest.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise RuntimeError('backup foreign key check failed')
    except BaseException:
        target.unlink(missing_ok=True)
        raise
