"""Password, session and camera authorization primitives."""
from __future__ import annotations
import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass

# SHA-256 PBKDF2 work factor; each password has an independently generated salt.
PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 1024
USERNAME = re.compile(r'[A-Za-z0-9_.-]{1,64}\Z')
SESSION_LIFETIME = 12 * 3600
SETUP_LIFETIME = 24 * 3600

class AuthError(Exception):
    pass

class PasswordValidationError(ValueError):
    """Safe, fixed messages suitable for the CLI and authenticated API."""


def validate_password(password: str, *, maximum: int = MAX_PASSWORD_LENGTH) -> bytes:
    if not isinstance(password, str) or not MIN_PASSWORD_LENGTH <= len(password) <= maximum:
        raise PasswordValidationError(f'Hasło musi mieć od {MIN_PASSWORD_LENGTH} do {maximum} znaków.')
    try:return password.encode('utf-8')
    except UnicodeEncodeError as exc:raise PasswordValidationError('Hasło zawiera nieprawidłowe znaki Unicode.') from exc


def password_hash(password: str) -> str:
    encoded=validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', encoded, salt, PBKDF2_ITERATIONS)
    return f'pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}'

def password_verify(password: str, stored: str) -> bool:
    if not isinstance(password, str) or len(password) > 1024:
        return False
    try:
        name, iterations, salt_hex, value_hex = stored.split('$')
        rounds = int(iterations)
        salt, value = bytes.fromhex(salt_hex), bytes.fromhex(value_hex)
        if name != 'pbkdf2_sha256' or not 100_000 <= rounds <= 2_000_000 or len(salt) != 16 or len(value) != 32:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, rounds)
        return hmac.compare_digest(actual, value)
    except (ValueError, TypeError, AttributeError, UnicodeError):
        return False

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('ascii')).hexdigest()

SCHEMA = '''
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','viewer')),
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);
CREATE TABLE IF NOT EXISTS cameras (
 id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);
CREATE TABLE IF NOT EXISTS camera_permissions (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 camera_id INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
 may_download INTEGER NOT NULL DEFAULT 0 CHECK(may_download IN (0,1)),
 PRIMARY KEY(user_id,camera_id)
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 csrf_hash TEXT NOT NULL, expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_attempts (
 key_hash TEXT PRIMARY KEY, failures INTEGER NOT NULL, next_allowed INTEGER NOT NULL,
 last_attempt INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_last_attempt ON auth_attempts(last_attempt);
'''

def initialize(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)
    db.commit()

def create_user(db: sqlite3.Connection, username: str, password: str, role: str) -> int:
    if not isinstance(username,str) or not USERNAME.fullmatch(username) or role not in ('admin','viewer'):
        raise ValueError('invalid username or role')
    digest = password_hash(password)
    if db.in_transaction:
        return int(db.execute('INSERT INTO users(username,password_hash,role) VALUES (?,?,?)', (username,digest,role)).lastrowid)
    with db:
        return int(db.execute('INSERT INTO users(username,password_hash,role) VALUES (?,?,?)', (username,digest,role)).lastrowid)

def issue_setup_token(db: sqlite3.Connection, *, now: int | None = None) -> str:
    """Issue a console-only one-time token, only while no user exists."""
    now=int(time.time()) if now is None else now
    token=secrets.token_urlsafe(32)
    db.execute('BEGIN IMMEDIATE')
    try:
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():raise ValueError('setup is already complete')
        db.executemany('INSERT INTO system_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       [('setup_token_hash',token_hash(token)),('setup_expires',str(now+SETUP_LIFETIME))])
        db.commit();return token
    except BaseException:db.rollback();raise

def setup_available(db: sqlite3.Connection, *, now: int | None = None) -> bool:
    now=int(time.time()) if now is None else now
    if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():return False
    values=dict(db.execute("SELECT key,value FROM system_settings WHERE key IN ('setup_token_hash','setup_expires')"))
    try:return bool(re.fullmatch(r'[0-9a-f]{64}',values.get('setup_token_hash','')) and int(values.get('setup_expires','0'))>now)
    except (TypeError,ValueError):return False

def reserve_setup_attempt(db: sqlite3.Connection, client: str, *, now: int | None = None) -> None:
    now=int(time.time()) if now is None else now
    if not isinstance(client,str) or len(client)>255:raise AuthError('invalid client')
    key=hashlib.sha256(('setup:'+client).encode('utf-8')).hexdigest()
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('DELETE FROM auth_attempts WHERE last_attempt<?',(now-86400,))
        row=db.execute('SELECT failures,next_allowed FROM auth_attempts WHERE key_hash=?',(key,)).fetchone()
        if row and row[1]>now:raise AuthError('try again later')
        failures=min((row[0] if row else 0)+1,15)
        delay=min(300,2**min(failures-1,8))
        db.execute('INSERT INTO auth_attempts VALUES (?,?,?,?) ON CONFLICT(key_hash) DO UPDATE SET failures=excluded.failures,next_allowed=excluded.next_allowed,last_attempt=excluded.last_attempt',
                   (key,failures,now+delay,now))
        db.commit()
    except BaseException:db.rollback();raise

def complete_setup(db: sqlite3.Connection, token: str, username: str, password: str, *, now: int | None = None) -> int:
    """Consume a one-time token and create the only initial administrator."""
    now=int(time.time()) if now is None else now
    if not isinstance(token,str) or not token.isascii() or not 1<=len(token)<=128:
        raise AuthError('invalid setup token')
    db.execute('BEGIN IMMEDIATE')
    try:
        values=dict(db.execute("SELECT key,value FROM system_settings WHERE key IN ('setup_token_hash','setup_expires')"))
        if (db.execute('SELECT 1 FROM users LIMIT 1').fetchone() or
            not re.fullmatch(r'[0-9a-f]{64}',values.get('setup_token_hash','')) or
            int(values.get('setup_expires','0'))<=now or
            not hmac.compare_digest(values['setup_token_hash'],token_hash(token))):
            raise AuthError('invalid setup token')
        if not isinstance(username,str):raise ValueError('invalid username')
        uid=create_user(db,username,password,'admin')
        _finish_setup(db,uid,now)
        db.commit();return uid
    except BaseException:db.rollback();raise

def _finish_setup(db: sqlite3.Connection, uid: int, now: int) -> None:
    db.execute("DELETE FROM system_settings WHERE key IN ('setup_token_hash','setup_expires')")
    db.execute('INSERT INTO audit_log(created_at,user_id,action,object_type,object_id) VALUES (?,?,?,?,?)',
               (now,uid,'setup.complete','user',str(uid)))

def complete_console_setup(db: sqlite3.Connection, username: str, password: str) -> int:
    """Finish an existing empty installation from its trusted local console."""
    db.execute('BEGIN IMMEDIATE')
    try:
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():raise ValueError('setup is already complete')
        uid=create_user(db,username,password,'admin')
        _finish_setup(db,uid,int(time.time()))
        db.commit();return uid
    except BaseException:db.rollback();raise

@dataclass(frozen=True)
class Session:
    token: str
    csrf: str
    expires: int

def authenticate(db: sqlite3.Connection, username: str, password: str, client: str, *, now: int | None = None, audit: bool = False) -> Session:
    fixed_now=now is not None
    now = int(time.time()) if now is None else now
    if not isinstance(username, str) or len(username) > 64 or not isinstance(client, str) or len(client) > 255:
        raise AuthError('Invalid credentials')
    # One limiter per client, independent of username: changing username cannot avoid the limit.
    key = hashlib.sha256(client.encode('utf-8')).hexdigest()
    # Reserve an attempt transactionally before expensive password hashing.
    # Otherwise parallel requests could all pass the rate limit before any
    # failed attempt is recorded.
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('DELETE FROM auth_attempts WHERE last_attempt<?',(now-86400,))
        row = db.execute('SELECT failures,next_allowed FROM auth_attempts WHERE key_hash=?',(key,)).fetchone()
        if row and row[1] > now:
            db.rollback()
            raise AuthError('Try again later')
        failures = min((row[0] if row else 0) + 1, 15)
        delay = min(300, 2 ** min(failures - 1, 8))
        db.execute('INSERT INTO auth_attempts VALUES (?,?,?,?) ON CONFLICT(key_hash) DO UPDATE SET failures=excluded.failures,next_allowed=excluded.next_allowed,last_attempt=excluded.last_attempt', (key,failures,now+delay,now))
        db.commit()
    except BaseException:
        db.rollback();raise
    # Account names are ASCII-only; avoid sending malformed Unicode to SQLite.
    user = db.execute('SELECT id,password_hash,enabled FROM users WHERE username=?',(username,)).fetchone() if username.isascii() else None
    # Uniform computational work for unknown accounts.
    dummy = 'pbkdf2_sha256$600000$00000000000000000000000000000000$0000000000000000000000000000000000000000000000000000000000000000'
    verified = password_verify(password, user[1] if user else dummy)
    if not user or not user[2] or not verified:
        raise AuthError('Invalid credentials')
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    # An administrator can disable the account or change its password while
    # PBKDF2 is running. Serialize and revalidate before issuing a session.
    db.execute('BEGIN IMMEDIATE')
    try:
        current=db.execute('SELECT password_hash,enabled FROM users WHERE id=?',(user[0],)).fetchone()
        if not current or not current[1] or current[0]!=user[1]:raise AuthError('Invalid credentials')
        db.execute('DELETE FROM auth_attempts WHERE key_hash=? AND failures=? AND last_attempt=?',(key,failures,now))
        issued_at=now if fixed_now else int(time.time())
        db.execute('DELETE FROM sessions WHERE expires<=?',(issued_at,))
        db.execute('INSERT INTO sessions(token_hash,user_id,csrf_hash,expires) VALUES (?,?,?,?)',
                   (token_hash(token),user[0],token_hash(csrf),issued_at+SESSION_LIFETIME))
        if audit:db.execute("INSERT INTO audit_log(created_at,user_id,action,object_type,object_id) VALUES (?,?,'session.login','user',?)",(issued_at,user[0],str(user[0])))
        db.commit()
    except BaseException:
        db.rollback();raise
    return Session(token,csrf,issued_at+SESSION_LIFETIME)

def get_user(db: sqlite3.Connection, token: str, *, now: int | None = None):
    now = int(time.time()) if now is None else now
    if not isinstance(token,str) or len(token)>128 or not token.isascii():
        return None
    return db.execute('SELECT users.id,users.username,users.role,sessions.csrf_hash FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires>? AND users.enabled=1', (token_hash(token),now)).fetchone()

def change_own_password(db: sqlite3.Connection, user_id: int, session_token: str, current: str, new: str, *, now: int | None=None) -> None:
    """Verify the old password and atomically revoke every session on success."""
    fixed_now=now is not None
    now=int(time.time()) if now is None else now
    if type(user_id) is not int or user_id<1 or not isinstance(session_token,str) or not session_token.isascii() or len(session_token)>128:
        raise AuthError('invalid credentials')
    validate_password(new)
    attempt=hashlib.sha256(('password-change:'+str(user_id)).encode('ascii')).hexdigest()
    db.execute('BEGIN IMMEDIATE')
    try:
        row=db.execute('SELECT failures,next_allowed FROM auth_attempts WHERE key_hash=?',(attempt,)).fetchone()
        if row and row[1]>now:raise AuthError('try again later')
        failures=min((row[0] if row else 0)+1,15);delay=min(300,2**min(failures-1,8))
        db.execute('INSERT INTO auth_attempts VALUES (?,?,?,?) ON CONFLICT(key_hash) DO UPDATE SET failures=excluded.failures,next_allowed=excluded.next_allowed,last_attempt=excluded.last_attempt',(attempt,failures,now+delay,now))
        db.commit()
    except BaseException:db.rollback();raise
    row=db.execute('SELECT password_hash,enabled FROM users WHERE id=?',(user_id,)).fetchone()
    if not row or not row[1] or not password_verify(current,row[0]):raise AuthError('invalid credentials')
    new_hash=password_hash(new)
    db.execute('BEGIN IMMEDIATE')
    try:
        checked_at=now if fixed_now else int(time.time())
        session=db.execute('SELECT 1 FROM sessions WHERE user_id=? AND token_hash=? AND expires>?',(user_id,token_hash(session_token),checked_at)).fetchone()
        latest=db.execute('SELECT password_hash,enabled FROM users WHERE id=?',(user_id,)).fetchone()
        if not session or not latest or not latest[1] or latest[0]!=row[0]:raise AuthError('session or credentials changed')
        db.execute('UPDATE users SET password_hash=? WHERE id=?',(new_hash,user_id))
        db.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
        db.execute('DELETE FROM auth_attempts WHERE key_hash=? AND failures=? AND last_attempt=?',(attempt,failures,now))
        db.execute("INSERT INTO audit_log(created_at,user_id,action,object_type,object_id) VALUES (?,?,'user.password.change','user',?)",(now,user_id,str(user_id)))
        db.commit()
    except BaseException:db.rollback();raise

def verify_csrf(user, token: str) -> bool:
    return bool(user and isinstance(token,str) and token.isascii() and len(token)<=128 and hmac.compare_digest(user[3], token_hash(token)))

def may_access(db: sqlite3.Connection, user, camera_id: int, *, download: bool = False, live: bool = False) -> bool:
    if not user:
        return False
    camera = db.execute('SELECT enabled FROM cameras WHERE id=?',(camera_id,)).fetchone()
    if not camera or (live and not camera[0]):
        return False
    if user[2] == 'admin':
        return True
    p = db.execute('SELECT may_download FROM camera_permissions WHERE user_id=? AND camera_id=?',(user[0],camera_id)).fetchone()
    return bool(p and (not download or p[0]))

def logout(db: sqlite3.Connection, token: str, *, audit: bool = False) -> None:
    if isinstance(token,str) and token.isascii() and len(token)<=128:
        with db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT user_id FROM sessions WHERE token_hash=?',(token_hash(token),)).fetchone()
            db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(token),))
            if row and audit:db.execute("INSERT INTO audit_log(created_at,user_id,action,object_type,object_id) VALUES (?,?,'session.logout','user',?)",(int(time.time()),row[0],str(row[0])))
