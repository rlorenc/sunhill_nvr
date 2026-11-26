"""Root-managed local AppArmor extension for the isolated FTP service."""
from __future__ import annotations
import hashlib,os,re,stat,subprocess,tempfile
from pathlib import Path
from .config import load

BEGIN='# BEGIN SUNHILL NVR FTP\n'
END='# END SUNHILL NVR FTP\n'


def literal(path: Path) -> str:
    value=str(path)
    # Do not interpolate AppArmor glob expressions, variables or escapes from
    # paths selected in the administration panel.
    if not path.is_absolute() or path==Path('/') or not re.fullmatch(r'/[\w./ -]+',value) or '..' in path.parts:
        raise ValueError('FTP path contains unsupported AppArmor characters')
    return value


def rules(archive: Path, control: Path) -> str:
    a,c=literal(archive),literal(control)
    lines=[]
    for prefix in ('','/att/proftpd'):
        for directory in ('config-*','current'):
            for name in ('validate-proftpd.conf','proftpd.conf','ftp.passwd','ftp.group'):
                lines.append(f'  "{prefix}{c}/{directory}/{name}" r,')
        # Only the short-lived parser's own runtime files may be written
        # under a candidate; published account/configuration files stay RO.
        lines.extend((f'  "{prefix}{c}/config-*/validate-runtime-*/" r,',
                      f'  "{prefix}{c}/config-*/validate-runtime-*/proftpd.pid" rwk,',
                      f'  "{prefix}{c}/config-*/validate-runtime-*/proftpd.scoreboard*" rwk,'))
        lines.extend((f'  "{prefix}{a}/" r,',f'  "{prefix}{a}/**" rw,',
                      f'  "{prefix}/run/sunhill-ftp/" r,',f'  "{prefix}/run/sunhill-ftp/**" rwk,'))
    # Permit only package-provided DSO files required by the isolated server.
    for prefix in ('','/att/proftpd'):
        lines.append(f'  {prefix}/usr/lib/proftpd/ r,')
        for module in ('mod_xfer','mod_ls'):
            lines.extend((f'  {prefix}/usr/lib/proftpd/{module}.so mr,',
                          f'  {prefix}/usr/lib/proftpd/{module}.la r,'))
    lines.extend(('  /att/proftpd/run/systemd/journal/dev-log w,','  /run/systemd/journal/dev-log w,',
                  '  capability net_bind_service,','  network inet stream,','  network inet6 stream,'))
    return BEGIN+'\n'.join(lines)+'\n'+END


def merged(old: str, block: str) -> str:
    if old.count(BEGIN)!=old.count(END) or old.count(BEGIN)>1:raise ValueError('invalid managed AppArmor block')
    if BEGIN in old:
        start=old.index(BEGIN);end=old.index(END)
        if end<start:raise ValueError('invalid managed AppArmor block')
        return old[:start]+block+old[end+len(END):]
    return old+('\n' if old and not old.endswith('\n') else '')+block


def _trusted(path: Path, *, directory=False):
    info=path.lstat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink==1) or info.st_uid!=0 or info.st_mode&0o022:
        raise ValueError('unsafe AppArmor path: '+str(path))


def _replace(path: Path, content: str):
    fd,name=tempfile.mkstemp(prefix='.sunhill-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            os.fchmod(f.fileno(),0o644);f.write(content);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:Path(name).unlink(missing_ok=True)


def configure(archive: Path, control: Path, *, profiles=Path('/etc/apparmor.d'), run=subprocess.run, stamp: Path | None=None):
    profile=profiles/'proftpd'
    if not os.path.lexists(profile):return False
    _trusted(profiles,directory=True);_trusted(profile)
    if not re.search(r'^\s*#?\s*include\s+(?:if exists\s+)?<local/proftpd>\s*$',profile.read_text(),re.M):
        raise ValueError('ProFTPD AppArmor profile lacks local/proftpd include')
    local=profiles/'local';local.mkdir(mode=0o755,exist_ok=True);_trusted(local,directory=True)
    target=local/'proftpd';existed=os.path.lexists(target)
    if existed:_trusted(target)
    old=target.read_text() if existed else '';new=merged(old,rules(archive,control))
    digest=hashlib.sha256((profile.read_text()+'\n'+new).encode()).hexdigest()+'\n'
    if new==old and stamp is not None and stamp.exists():
        _trusted(stamp)
        if stamp.read_text()==digest:return True
    if new!=old:_replace(target,new)
    try:
        # Reload unless the exact policy was already loaded in this boot.
        run(['/usr/sbin/apparmor_parser','-r','-T',str(profile)],check=True,timeout=60)
    except (OSError,subprocess.SubprocessError):
        if new!=old:
            if existed:_replace(target,old)
            else:target.unlink(missing_ok=True)
        raise RuntimeError('ProFTPD AppArmor reload failed; previous local policy restored') from None
    if stamp is not None:_replace(stamp,digest)
    return True


if __name__=='__main__':
    import sys
    cfg=load(Path(sys.argv[1]));configure(cfg.archive,cfg.control_state,stamp=Path('/run/sunhill-ftp-policy.sha256'))
