"""Validation and rendering of isolated ProFTPD virtual account data.

Installation, ownership, locking and a privileged configuration helper are separate.
FTP traffic is plaintext unless the device and server negotiate FTPS; restrict FTP
and passive ports to the camera LAN and never publish them through the web proxy.
"""
from __future__ import annotations
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from . import fd_mount, security

USERNAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')
SHA512_CRYPT = re.compile(r'\$6\$(?:rounds=\d+\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}\Z')

@dataclass(frozen=True)
class Account:
    username: str
    password_hash: str
    camera: str | None = None  # None is the shared account.

def make_hash(password: str, *, openssl: str = 'openssl') -> str:
    encoded=security.validate_password(password, maximum=128)
    if any(x in password for x in ('\r','\n','\0')):
        raise security.PasswordValidationError('Hasło FTP nie może zawierać znaków CR, LF ani NUL.')
    try:
        proc=subprocess.run([openssl,'passwd','-6','-stdin'],input=encoded+b'\n',
                            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,check=True)
        result=proc.stdout.decode('ascii').strip()
    except (OSError,UnicodeError,subprocess.SubprocessError) as exc:
        raise RuntimeError('could not hash FTP password') from exc
    if not SHA512_CRYPT.fullmatch(result):
        raise RuntimeError('unsupported FTP password hash')
    return result

def render(accounts: list[Account], archive: Path, uid: int, gid: int) -> str:
    if not archive.is_absolute() or ':' in str(archive) or '\n' in str(archive) or '\r' in str(archive):
        raise ValueError('invalid archive path')
    if uid < 1000 or gid < 1000 or uid > 2**31-1 or gid > 2**31-1:
        raise ValueError('FTP users must be unprivileged')
    names=set();lines=[]
    for a in accounts:
        if not USERNAME.fullmatch(a.username) or a.username.lower() in names:
            raise ValueError('invalid or duplicate FTP username')
        if not SHA512_CRYPT.fullmatch(a.password_hash):
            raise ValueError('invalid FTP password hash')
        if a.camera is not None and not SLUG.fullmatch(a.camera):
            raise ValueError('invalid FTP camera')
        names.add(a.username.lower())
        home=archive if a.camera is None else archive/a.camera
        lines.append(f'{a.username}:{a.password_hash}:{uid}:{gid}:Sunhill FTP:{home}:/usr/sbin/nologin')
    return '\n'.join(lines) + ('\n' if lines else '')

def create_virtual_user_config(auth_file: Path, group_file: Path) -> str:
    paths=(str(auth_file),str(group_file))
    if any(not p.startswith('/') or not re.fullmatch(r'/[A-Za-z0-9_./-]+',p) or '..' in Path(p).parts for p in paths):
        raise ValueError('invalid ProFTPD configuration path')
    return (f'AuthOrder mod_auth_file.c\nAuthUserFile {paths[0]}\nAuthGroupFile {paths[1]}\n'
            'RequireValidShell off\nDefaultRoot ~\nUseFtpUsers off\n')


def render_group(group: str, gid: int, accounts: list[Account]) -> str:
    if not USERNAME.fullmatch(group) or not 1000 <= gid <= 2**31-1:
        raise ValueError('invalid group')
    seen=set()
    for account in accounts:
        if not USERNAME.fullmatch(account.username) or account.username.lower() in seen:
            raise ValueError('invalid or duplicate account')
        seen.add(account.username.lower())
    return f"{group}:x:{gid}:{','.join(a.username for a in accounts)}\n"


def render_server(auth_file: Path, group_file: Path, *, passive_start: int = 50000,
                  passive_end: int = 50100, runtime_dir: Path = Path('/run/sunhill-ftp')) -> str:
    if not (1024 <= passive_start < passive_end <= 65535):
        raise ValueError('invalid passive port range')
    auth=create_virtual_user_config(auth_file,group_file)
    if not runtime_dir.is_absolute() or runtime_dir==Path('/') or not re.fullmatch(r'/[A-Za-z0-9_./-]+',str(runtime_dir)) or '..' in runtime_dir.parts:
        raise ValueError('invalid ProFTPD runtime path')
    # Debian/Ubuntu ProFTPD 1.3.9 moved transfer and listing support into
    # DSOs. We use a standalone config, not the distribution modules.conf.
    # Load only the required modules, and avoid double-loading static builds.
    modules = ('<IfModule mod_dso.c>\n  ModulePath /usr/lib/proftpd\n</IfModule>\n'
               '<IfModule !mod_xfer.c>\n  LoadModule mod_xfer.c\n</IfModule>\n'
               '<IfModule !mod_ls.c>\n  LoadModule mod_ls.c\n</IfModule>\n')
    return (modules+'ServerName "Sunhill NVR FTP"\nServerType standalone\nDefaultServer on\n'
            'Port 21\nUseIPv6 off\nDefaultAddress 0.0.0.0\nUser sunhill\nGroup sunhill\nUmask 027\n'
            f'PidFile {runtime_dir}/proftpd.pid\nScoreboardFile {runtime_dir}/proftpd.scoreboard\n'
            'RootLogin off\nAllowOverwrite off\nAllowEmptyPasswords off\nAllowOverride off\n'
            'DefaultTransferMode binary\nTransferOptions IgnoreASCII\n'
            'HiddenStores .sunhill-upload- .%P\n'
            f'PassivePorts {passive_start} {passive_end}\n'+auth+
            '<IfModule mod_auth_pam.c>\n  AuthPAM off\n</IfModule>\n'
            '<IfModule mod_ctrls.c>\n  ControlsEngine off\n</IfModule>\n'
            # ProFTPD evaluates Allow before Deny in this order. DenyAll is
            # an unconditional veto, including for loopback health probes.
            '<Limit LOGIN>\n  Order allow,deny\n'
            '  Allow from 127.0.0.1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16\n  Deny from all\n</Limit>\n'
            '<Limit DELE RMD XRMD RNFR RNTO APPE SITE_CHMOD SITE_CHOWN SITE_CHGRP SITE_RMDIR SITE_SYMLINK SITE_COPY SITE_CPFR SITE_CPTO SITE_UTIME>\n  DenyAll\n</Limit>\n')


def read_published_accounts(state: Path) -> str:
    """Read one root-owned immutable configuration snapshot, not arbitrary paths."""
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    root=os.open(state,flags)
    try:
        info=os.fstat(root)
        if info.st_uid!=0 or info.st_mode & 0o022:raise ValueError('untrusted FTP configuration root')
        target=os.readlink('current',dir_fd=root)
        if not re.fullmatch(r'config-[A-Za-z0-9_-]+',target):raise ValueError('invalid FTP configuration pointer')
        directory=os.open(target,flags,dir_fd=root)
        try:
            info=os.fstat(directory)
            if info.st_uid!=0 or info.st_mode & 0o022:raise ValueError('untrusted FTP configuration directory')
            fd=os.open('ftp.passwd',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
            with os.fdopen(fd,'r',encoding='utf-8') as file:
                info=os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode & 0o022:
                    raise ValueError('untrusted FTP account file')
                contents=file.read(4*1024*1024+1)
                if len(contents)>4*1024*1024:raise ValueError('FTP account file exceeds limit')
                return contents
        finally:os.close(directory)
    finally:os.close(root)


def prepare_homes(archive: Path, accounts: str, guard, *, uid: int, gid: int) -> None:
    """Provision only validated direct camera children on the enrolled mount.

    Run as the recording user. Never follow symlinks, change ownership or
    permissions of an existing directory, or create paths outside the archive.
    """
    if not archive.is_absolute() or uid<1000 or gid<1000:raise ValueError('invalid FTP home configuration')
    homes=set();names=set()
    for line in accounts.splitlines():
        fields=line.split(':')
        if len(fields)!=7:raise ValueError('invalid FTP account row')
        name,password,user,group,description,home,shell=fields
        if (not USERNAME.fullmatch(name) or name.lower() in names or not SHA512_CRYPT.fullmatch(password)
            or user!=str(uid) or group!=str(gid) or description!='Sunhill FTP' or shell!='/usr/sbin/nologin'):
            raise ValueError('invalid FTP account row')
        names.add(name.lower())
        if home==str(archive):homes.add('');continue
        slug=Path(home).name
        if not SLUG.fullmatch(slug) or home!=str(archive/slug):raise ValueError('invalid FTP home path')
        homes.add(slug)
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    if guard() is not True:raise RuntimeError('archive unavailable before FTP home preparation')
    root=os.open(archive,flags)
    try:
        expected=fd_mount.identity(root);root_stat=os.fstat(root)
        def verify():
            if guard() is not True:raise RuntimeError('archive unavailable during FTP home preparation')
            current=os.open(archive,flags)
            try:
                fd_mount.require_same(expected,current)
                if os.fstat(current).st_ino!=root_stat.st_ino:raise RuntimeError('archive directory changed')
            finally:os.close(current)
        verify()
        for slug in sorted(homes):
            verify()
            if not slug:
                if not os.access('.',os.W_OK|os.X_OK,dir_fd=root,effective_ids=True):raise PermissionError('FTP archive not writable')
                continue
            try:os.mkdir(slug,0o750,dir_fd=root)
            except FileExistsError:pass
            child=os.open(slug,flags,dir_fd=root)
            try:
                fd_mount.require_same(expected,child)
                if not os.access('.',os.W_OK|os.X_OK,dir_fd=child,effective_ids=True):raise PermissionError('FTP camera home not writable')
            finally:os.close(child)
        verify()
    finally:os.close(root)
