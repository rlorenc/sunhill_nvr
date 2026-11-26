"""Repair the exact 0.2.1 systemd generator bug without touching application data."""
from __future__ import annotations
import os
import stat
import tempfile
from pathlib import Path
from sunhill_nvr.config import load
from sunhill_nvr.units import render, systemd_path
from sunhill_nvr.upgrade_admin import trusted_directory, trusted_file

NAMES = ('sunhill-controller.service', 'sunhill-ftp.service')


def repair(root: Path, system: Path) -> tuple[str, Path | None]:
    if os.geteuid() != 0:
        raise PermissionError('root required')
    for directory in (root, root/'config', root/'control', root/'control/upgrade', system):
        trusted_directory(directory)
    if not (root/'current').is_symlink() or os.readlink(root/'current') != 'releases/0.2.1':
        raise ValueError('only the incomplete 0.2.1 installation is supported')
    if any(os.path.lexists(root/'control/upgrade'/name) for name in ('transaction.json','domain-transaction.json','tls-transaction.json','storage-transaction.json','recovery-unit-migration')) or os.path.lexists(root/'config/maintenance.flag'):
        raise ValueError('pending transaction; refusing resume')
    conf=root/'config/config.json';trusted_file(conf);config=load(conf)
    if config.database != root/'data/sunhill.sqlite' or not config.database.is_file() or config.database.is_symlink():
        raise ValueError('existing default database required')
    expected=render(config,legacy_policy=True);changes={}
    for name in NAMES:
        path=system/name;trusted_file(path)
        # Do not overwrite custom unit drop-ins, including runtime overrides.
        if any((base/(name+'.d')).exists() or (base/(name+'.d')).is_symlink() for base in (system, Path('/run/systemd/system'))):
            raise ValueError('custom unit drop-ins require manual inspection: '+name)
        new=expected[name].encode();old=new.replace(('ReadWritePaths='+systemd_path(config.database.parent)+' ').encode(),b'ReadWritePaths=',1)
        if old==new:raise ValueError('unexpected generated unit')
        actual=path.read_bytes()
        if actual==new:continue
        if actual!=old:raise ValueError('modified unit requires manual inspection: '+name)
        changes[name]=(actual,new)
    if not changes:return config.hostname,None
    backup=Path(tempfile.mkdtemp(prefix='resume-0.2.1-',dir=root/'control/upgrade'))
    for name,(old,new) in changes.items():
        with (backup/name).open('xb') as handle:
            handle.write(old);handle.flush();os.fsync(handle.fileno())
    for directory in (backup, backup.parent):
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    # Both originals are saved before any replacement. Each unit replacement
    # is atomic and the operation can be retried if interrupted between units.
    for name,(old,new) in changes.items():
        fd,temp=tempfile.mkstemp(prefix='.sunhill-resume-',dir=system)
        try:
            with os.fdopen(fd,'wb') as handle:
                os.fchmod(handle.fileno(),0o644);handle.write(new);handle.flush();os.fsync(handle.fileno())
            os.replace(temp,system/name)
        finally:
            Path(temp).unlink(missing_ok=True)
    fd=os.open(system,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return config.hostname,backup


if __name__=='__main__':
    import sys
    host,backup=repair(Path('/opt/sunhill-nvr'),Path('/etc/systemd/system'))
    if backup is not None:print('Original units preserved: '+str(backup),file=sys.stderr)
    print(host)
