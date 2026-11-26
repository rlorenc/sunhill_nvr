"""Offline, retryable repair of the interrupted first installation of 0.2.1 / 0.2.5 / 0.2.6 / 0.2.7.

The 0.2.1 schema is unchanged in this release. No database, media or TLS
configuration is recreated. Never use this entry point for a running NVR.
"""
from __future__ import annotations
import json,os,shutil,subprocess,tempfile,sys
from pathlib import Path
from sunhill_nvr import __version__,lifecycle
from sunhill_nvr.config import load
from sunhill_nvr.units import render,systemd_path
from sunhill_nvr.upgrade_admin import trusted_directory,trusted_file
from sunhill_nvr.upgrade_system import SystemAdapter
from sunhill_nvr.upgrade_transaction import _lock,_sync_dir
from sunhill_nvr.recovery_migration import _trusted_tree,_exchange
from sunhill_nvr.release import _matches_existing


def prepare(root: Path,system: Path,source: Path, *, run=subprocess.run) -> str:
    for p in (root,root/'config',root/'control',root/'control/upgrade',root/'releases',system):trusted_directory(p)
    state=root/'control/upgrade';current=root/'current';version=__version__
    if not current.is_symlink() or os.readlink(current) not in ('releases/0.2.1','releases/0.2.5','releases/0.2.6','releases/0.2.7','releases/0.2.8',f'releases/{version}'):
        raise ValueError('resume only supports the interrupted 0.2.1/0.2.5/0.2.6/0.2.7/0.2.8 installation or this repair')
    fd=_lock(state)
    try:
        if any(os.path.lexists(state/name) for name in ('transaction.json','domain-transaction.json','tls-transaction.json','storage-transaction.json','recovery-unit-migration')) or os.path.lexists(root/'config/maintenance.flag'):
            raise ValueError('pending transaction; refusing resume')
        conf=root/'config/config.json';trusted_file(conf);cfg=load(conf)
        if cfg.database!=root/'data/sunhill.sqlite' or cfg.control_state!=root/'control' or cfg.database.is_symlink() or not cfg.database.is_file():raise ValueError('unexpected database or control path')
        old=render(cfg,legacy_policy=True);new=render(cfg)
        # Every pre-existing unit must be exactly one of the known generated
        # variants. Never replace local modifications or an arbitrary service.
        existing={p.name for p in system.glob('sunhill-*')}
        if not set(old)<=existing or not existing<=set(new):raise ValueError('unexpected Sunhill unit set')
        for name in new:
            p=system/name
            if any(os.path.lexists(base/(name+'.d')) for base in (system,Path('/run/systemd/system'))):raise ValueError('custom unit drop-in: '+name)
            if not os.path.lexists(p):continue
            trusted_file(p)
            variants={new[name],old.get(name,'')}
            if name=='sunhill-go2rtc.service':
                # 0.2.4/0.2.5 used a failing pre-start check, which can loop.
                variants.add(new[name].replace('ExecCondition=', 'ExecStartPre=',1))
            if name in ('sunhill-controller.service','sunhill-ftp.service'):
                variants.add(old[name].replace('ReadWritePaths='+systemd_path(cfg.database.parent)+' ','ReadWritePaths=',1))
            if p.read_text() not in variants:raise ValueError('modified unit: '+name)
        # No enabled or running instance may be repaired. The disabled gateway
        # can be in its restart loop after the known failure.
        for name in existing:
            if name=='sunhill-upgrade-recover.service':continue
            status=run(['/usr/bin/systemctl','show','-p','UnitFileState','-p','ActiveState','--value',name],check=True,capture_output=True,text=True).stdout.splitlines()
            if len(status)!=2 or len(set(status)&{'disabled','static'})!=1 or len(set(status)&{'inactive','failed','activating'})!=1:
                raise ValueError('unit is not quiescent and disabled: '+name)
            if 'activating' in status and name not in ('sunhill-go2rtc.service','sunhill-ftp.service'):raise ValueError('unit is activating: '+name)
        for name in ('sunhill-go2rtc.service','sunhill-ftp.service','sunhill-upgrade-recover.service'):
            run(['/usr/bin/systemctl','stop',name],check=True)
        # Check source against manifest before publishing code. The distribution
        # was explicitly extracted and invoked by root, not uploaded by the web.
        meta=json.loads((source/'release-manifest.json').read_text())
        if meta.get('version')!=version:raise ValueError('release manifest version mismatch')
        with tempfile.TemporaryDirectory(prefix='.resume-stage-',dir=root/'releases') as tmp:
            staged=Path(tmp);app=staged/'app';app.mkdir()
            shutil.copytree(source/'src/sunhill_nvr',app/'sunhill_nvr',ignore=shutil.ignore_patterns('__pycache__','*.pyc'),symlinks=True)
            (staged/'manifest.json').write_text(json.dumps(meta,sort_keys=True))
            for directory,dirs,files in os.walk(staged,followlinks=False):
                os.chmod(directory,0o755)
                for name in dirs+files:
                    p=Path(directory)/name
                    if p.is_symlink():raise ValueError('symlink in release source')
                    if p.is_file():os.chmod(p,0o644)
            _trusted_tree(staged,sync=True)
            if not _matches_existing(staged,meta):raise ValueError('release manifest does not match source')
            target=root/'releases'/version
            if os.path.lexists(target):
                _trusted_tree(target)
                if not _matches_existing(target,meta):raise ValueError('existing candidate differs from package')
            else:
                os.rename(staged,target);_sync_dir(target.parent)
        # All previous metadata is retained. If a crash happens between writes,
        # the next invocation accepts both old and new generated unit variants.
        backup=Path(tempfile.mkdtemp(prefix='resume-release-',dir=state))
        for name in existing:SystemAdapter._write_private(backup/name,(system/name).read_bytes())
        for name in ('managed-units.json','lifecycle.json'):
            p=state/name;trusted_file(p);SystemAdapter._write_private(backup/name,p.read_bytes())
        _sync_dir(backup);_sync_dir(state)
        recovery=root/'recovery';_trusted_tree(recovery)
        if not _matches_existing(recovery,meta):
            previous=Path(tempfile.mkdtemp(prefix='recovery-resume-',dir=root))
            # Recovery has the same stable boot unit contract as 0.2.1.
            shutil.copytree(target/'app',previous/'app')
            shutil.copy2(target/'manifest.json',previous/'manifest.json');os.chmod(previous,0o755)
            _trusted_tree(previous,sync=True);_sync_dir(root)
            _exchange(recovery,previous);_sync_dir(root)
            print('Previous recovery preserved: '+str(previous),file=sys.stderr)
        for name,content in new.items():SystemAdapter._replace(system/name,content.encode())
        SystemAdapter._replace(state/'managed-units.json',(json.dumps(sorted(new),separators=(',',':'))+'\n').encode())
        SystemAdapter._replace(state/'lifecycle.json',json.dumps(lifecycle.validate(lifecycle.default(),set(new)),separators=(',',':')).encode())
        # Publish current only after code, recovery and units are ready.
        temp=root/('.resume-current-'+backup.name)
        try:
            temp.symlink_to('releases/'+version);os.replace(temp,current);_sync_dir(root)
        finally:temp.unlink(missing_ok=True)
        print('Original unit files preserved: '+str(backup),file=sys.stderr)
        return cfg.hostname
    finally:os.close(fd)

if __name__=='__main__':
    print(prepare(Path('/opt/sunhill-nvr'),Path('/etc/systemd/system'),Path(__file__).resolve().parents[1]))
