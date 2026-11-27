"""System adapter for the update transaction; not exposed to the web server.

Operations are deliberately fixed to Sunhill services and a trusted configuration.
The administrator must provision the state directory and public signing key.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .release import ReleaseError
from .upgrade_transaction import Operations, _read_journal
from . import lifecycle
from .controller import service_ready


SERVICES = lifecycle.ACTIVE
STOP_UNITS = ('sunhill-controller.timer','sunhill-controller.service') + tuple(s for s in SERVICES if s!='sunhill-controller.timer')
START_FIRST = ('sunhill-activity.service','sunhill-controller.service')
START_REST = tuple(s for s in SERVICES if s not in ('sunhill-activity.service','sunhill-go2rtc.service'))
MAX_DATABASE_BYTES = 10 * 1024 * 1024 * 1024


def _copy_private(source: Path, destination: Path, *, uid: int | None = None, gid: int | None = None) -> None:
    """Copy a regular, single-link file to a new file without following symlinks."""
    source_fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    destination_fd=None;created=False
    try:
        before=os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>MAX_DATABASE_BYTES:
            raise ReleaseError('invalid database backup')
        destination_fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);created=True
        copied=0
        with os.fdopen(os.dup(source_fd),'rb') as src,os.fdopen(os.dup(destination_fd),'wb') as dst:
            while True:
                chunk=src.read(1024*1024)
                if not chunk:break
                copied+=len(chunk)
                if copied>MAX_DATABASE_BYTES:raise ReleaseError('database backup is too large')
                dst.write(chunk)
            dst.flush();os.fsync(dst.fileno())
        after=os.fstat(source_fd)
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
            raise ReleaseError('database backup changed during copy')
        if uid is not None and gid is not None:os.fchown(destination_fd,uid,gid)
    except BaseException:
        if created:destination.unlink(missing_ok=True)
        raise
    finally:
        if destination_fd is not None:os.close(destination_fd)
        os.close(source_fd)


@dataclass(frozen=True)
class SystemAdapter:
    config_file: Path
    current: Path
    state: Path
    maintenance: Path
    uid: int
    gid: int
    run: object = subprocess.run
    health_timeout: float = 60.0
    system_dir: Path = Path('/etc/systemd/system')
    nginx_file: Path = Path('/etc/nginx/conf.d/sunhill-nvr.conf')
    tls_certificate: Path = Path('/opt/sunhill-nvr/config/tls/current/fullchain.pem')
    tls_key: Path = Path('/opt/sunhill-nvr/config/tls/current/key.pem')

    def _command(self,args,timeout=60):
        try:
            is_systemctl=args[0]=='/usr/bin/systemctl'
            result=self.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=None if is_systemctl else subprocess.DEVNULL,timeout=timeout)
            if result.returncode!=0:
                detail=' '.join(args[1:]) if is_systemctl else Path(args[0]).name
                raise ReleaseError(f'upgrade system command failed: {detail} (exit {result.returncode})')
        except (OSError,subprocess.SubprocessError) as exc:raise ReleaseError('upgrade system command failed') from exc

    def _as_service(self,package: Path,module: str,*args: str) -> None:
        if module not in ('sunhill_nvr.cli','sunhill_nvr.upgrade_database','sunhill_nvr.storage_database'):
            raise ValueError('invalid upgrade operation')
        self._command(['/usr/sbin/runuser','-u','sunhill','--','/usr/bin/env','-i',
                       'PATH=/usr/bin:/bin',f'PYTHONPATH={package / "app"}',
                       '/usr/bin/python3','-m',module,*args],timeout=300)

    def fence(self) -> None:
        try:
            fd=os.open(self.maintenance,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
        except FileExistsError:
            info=self.maintenance.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1:
                raise ReleaseError('untrusted maintenance flag')
            return
        try:os.fsync(fd)
        finally:os.close(fd)
        self._sync_parent(self.maintenance)

    def unfence(self) -> None:
        try:info=self.maintenance.lstat()
        except FileNotFoundError:return
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1:
            raise ReleaseError('untrusted maintenance flag')
        self.maintenance.unlink();self._sync_parent(self.maintenance)

    @staticmethod
    def _sync_parent(path: Path) -> None:
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)

    def stop(self) -> None:
        # Stop the timer and its current invocation first so it cannot restart
        # the gateway while the remaining services are being quiesced.
        self._command(['/usr/bin/systemctl','stop','sunhill-controller.timer'],timeout=60)
        self._command(['/usr/bin/systemctl','stop','sunhill-controller.service'],timeout=60)
        services=[name for name in self._runtime()['active'] if name!='sunhill-controller.timer']
        if (self.state/'lifecycle.json').exists():
            services=[name for name in services if (self.system_dir/name).exists() or self._unit_state(name) not in ('inactive','failed')]
        if services:self._command(['/usr/bin/systemctl','stop',*services],timeout=180)

    def start(self) -> None:
        self._command(['/usr/bin/systemctl','start',*START_FIRST],timeout=180)
        services=[name for name in self._runtime()['active'] if name not in ('sunhill-activity.service','sunhill-go2rtc.service')]
        if services:self._command(['/usr/bin/systemctl','start',*services],timeout=180)

    def assert_stopped(self) -> None:
        for unit in ('sunhill-controller.timer','sunhill-controller.service',*[name for name in self._runtime()['active'] if name!='sunhill-controller.timer']):
            if self._unit_state(unit) not in ('inactive','failed'):
                raise ReleaseError('application service is still active')

    def _unit_state(self,unit: str) -> str:
        try:
            result=self.run(['/usr/bin/systemctl','show','--property=ActiveState','--value',unit],
                            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=30)
            if result.returncode!=0:raise ReleaseError('cannot verify application service state')
            value=result.stdout.decode('ascii').strip()
            if value not in ('inactive','failed','active','activating','deactivating','reloading'):
                raise ReleaseError('cannot verify application service state')
            return value
        except (OSError,UnicodeError,AttributeError,subprocess.SubprocessError) as exc:
            raise ReleaseError('cannot verify application service state') from exc

    def _healthy_once(self) -> bool:
        try:
            # systemctl with several units may succeed when only one is active.
            active=self._runtime()['active']
            for service in active:
                self._command(['/usr/bin/systemctl','is-active','--quiet',service],timeout=30)
            # Active systemd processes are not necessarily accepting requests yet.
            self._command(['/usr/bin/systemctl','is-active','--quiet','nginx.service'],timeout=30)
            for service in ('sunhill-go2rtc.service',):
                if service in active and not service_ready(service,timeout=3):return False
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:8088/internal/health',timeout=5) as response:
                return response.status==200 and response.read(1024)==b'{"status":"ok"}'
        except (ReleaseError,OSError,urllib.error.URLError):return False

    def healthy(self) -> bool:
        deadline=time.monotonic()+max(0,self.health_timeout)
        while True:
            if self._healthy_once():
                return True
            if time.monotonic()>=deadline:return False
            time.sleep(min(1,max(0,deadline-time.monotonic())))

    def _old_release(self) -> Path:
        journal=_read_journal(self.state)
        if not journal:raise ReleaseError('upgrade journal unavailable')
        release=self.current.parent/'releases'/journal['old']
        if release.is_symlink() or not release.is_dir():raise ReleaseError('previous release unavailable')
        return release

    def backup(self,destination: Path) -> None:
        with tempfile.TemporaryDirectory(prefix='.db-backup-',dir=self.state) as name:
            temp=Path(name);os.chown(temp,self.uid,self.gid)
            raw=temp/'backup.sqlite'
            self._as_service(self._old_release(),'sunhill_nvr.upgrade_database','--config',str(self.config_file),'backup',str(raw))
            _copy_private(raw,destination)

    def restore(self,source: Path) -> None:
        with tempfile.TemporaryDirectory(prefix='.db-restore-',dir=self.state) as name:
            temp=Path(name);raw=temp/'backup.sqlite'
            _copy_private(source,raw,uid=self.uid,gid=self.gid)
            os.chown(temp,self.uid,self.gid)
            self._as_service(self._old_release(),'sunhill_nvr.upgrade_database','--config',str(self.config_file),'restore',str(raw))

    def restore_retained(self,source: Path) -> None:
        # Use the pinned manager, not the potentially older application, to
        # invalidate restored session tokens before any old writer starts.
        with tempfile.TemporaryDirectory(prefix='.db-restore-',dir=self.state) as name:
            temp=Path(name);raw=temp/'backup.sqlite'
            _copy_private(source,raw,uid=self.uid,gid=self.gid)
            os.chown(temp,self.uid,self.gid)
            self._as_service(self.current.parent/'recovery','sunhill_nvr.upgrade_database','--config',str(self.config_file),'restore-retained',str(raw))

    def migrate(self,candidate: Path) -> None:
        args=['--config',str(self.config_file),'migrate']
        journal=_read_journal(self.state)
        if journal and journal.get('system_backup'):
            backup=self.state/journal['system_backup']
            self._trusted_backup_directory(backup)
            if self._read_trusted(backup/'complete',limit=64)!=b'complete\n':raise ReleaseError('incomplete system backup')
            saved=json.loads(self._read_trusted(backup/'units.json',limit=32768))
            if isinstance(saved,dict):
                _,new_names=self._backup_unit_sets(saved)
                manifest=lifecycle.validate(json.loads(self._read_trusted(backup/'new/lifecycle.json',limit=32768)),new_names)
                if manifest['version']==2:args.append('--rtsp-only')
        self._as_service(candidate,'sunhill_nvr.cli',*args)

    @staticmethod
    def _read_trusted(path: Path, *, limit: int=131072) -> bytes:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            before=os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid() or before.st_nlink!=1
                or before.st_mode&0o022 or before.st_size>limit):raise ReleaseError('untrusted system configuration')
            chunks=[];total=0
            while True:
                chunk=os.read(fd,min(65536,limit-total+1))
                if not chunk:break
                total+=len(chunk)
                if total>limit:raise ReleaseError('system configuration too large')
                chunks.append(chunk)
            after=os.fstat(fd)
            if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
                raise ReleaseError('system configuration changed')
            return b''.join(chunks)
        finally:os.close(fd)

    @staticmethod
    def _trusted_backup_directory(path: Path) -> None:
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
            raise ReleaseError('untrusted system backup directory')

    @staticmethod
    def _write_private(path: Path,data: bytes, *, mode: int=0o600) -> None:
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,mode)
        with os.fdopen(fd,'wb') as output:
            output.write(data);output.flush();os.fsync(output.fileno())

    @staticmethod
    def _replace(path: Path,data: bytes) -> None:
        temp=path.parent/('.sunhill-config-'+secrets.token_hex(16))
        try:
            SystemAdapter._write_private(temp,data,mode=0o644)
            os.chmod(temp,0o644)
            os.replace(temp,path)
            SystemAdapter._sync_parent(path)
        finally:temp.unlink(missing_ok=True)

    def _managed_names(self) -> list[str]:
        try:value=json.loads(self._read_trusted(self.state/'managed-units.json',limit=32768))
        except (ValueError,TypeError) as exc:raise ReleaseError('invalid managed unit manifest') from exc
        if (not isinstance(value,list) or not value or len(value)>100
            or any(not isinstance(name,str) or not re.fullmatch(r'sunhill-[a-z0-9-]+\.(?:service|timer)',name) for name in value)
            or len(set(value))!=len(value)):
            raise ReleaseError('invalid managed unit manifest')
        return sorted(value)

    @staticmethod
    def _backup_unit_sets(value) -> tuple[list[str],list[str]]:
        if not isinstance(value,dict) or set(value)!={'version','old','new'} or type(value['version']) is not int or value['version']!=2:
            raise ReleaseError('invalid system backup')
        for names in (value['old'],value['new']):
            if (not isinstance(names,list) or not names or len(names)>100
                or any(not isinstance(name,str) or not lifecycle.NAME.fullmatch(name) for name in names)
                or names!=sorted(set(names))):raise ReleaseError('invalid backed up unit list')
        return value['old'],value['new']

    def _runtime(self) -> dict:
        path=self.state/'lifecycle.json'
        if not path.exists() and not path.is_symlink():raise ReleaseError('missing lifecycle manifest; a clean installation is required')
        try:
            value=json.loads(self._read_trusted(path,limit=32768))
            # While recovering an interrupted unit-set change, the two manifests
            # can reflect different sides of the same transaction.
            names=self._managed_names()
            transitional=False
            journal=_read_journal(self.state)
            if journal and journal.get('system_backup') and journal['phase'] in ('backed_up','migrating'):
                backup=self.state/journal['system_backup']
                self._trusted_backup_directory(backup)
                if self._read_trusted(backup/'complete',limit=64)!=b'complete\n':raise ReleaseError('incomplete system backup')
                saved=json.loads(self._read_trusted(backup/'units.json',limit=32768))
                if isinstance(saved,dict):
                    old_names,new_names=self._backup_unit_sets(saved)
                    if names not in (old_names,new_names):raise ReleaseError('managed unit list changed')
                    names=list(set(old_names)|set(new_names));transitional=True
            return lifecycle.validate(value,names,transitional=transitional)
        except (OSError,KeyError,TypeError,ValueError) as exc:raise ReleaseError('invalid lifecycle manifest') from exc

    def _enabled(self, names) -> set[str]:
        enabled=set()
        for name in names:
            try:
                result=self.run(['/usr/bin/systemctl','is-enabled',name],stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10)
                state=result.stdout.decode('ascii').strip()
                if state=='enabled' and result.returncode==0:enabled.add(name)
                elif state not in ('disabled','static') or result.returncode not in (0,1):
                    raise ReleaseError('unsupported service enablement state')
            except (OSError,UnicodeError,AttributeError,subprocess.SubprocessError) as exc:raise ReleaseError('cannot inspect enabled service') from exc
        return enabled

    def _restore_units_v2(self, backup: Path, kind: str, saved: dict, *, offline: bool) -> None:
        old_names,new_names=self._backup_unit_sets(saved)
        if self._managed_names() not in (old_names,new_names):raise ReleaseError('managed unit list changed')
        for side in ('old','new'):
            directory=backup/side
            self._trusted_backup_directory(directory)
        try:
            old_runtime=lifecycle.validate(json.loads(self._read_trusted(backup/'old/lifecycle.json',limit=32768)),old_names)
            new_runtime=lifecycle.validate(json.loads(self._read_trusted(backup/'new/lifecycle.json',limit=32768)),new_names)
        except (ValueError,TypeError) as exc:raise ReleaseError('invalid backed up lifecycle') from exc
        old_contents={name:self._read_trusted(backup/'old'/name) for name in old_names}
        new_contents={name:self._read_trusted(backup/'new'/name) for name in new_names}
        protected={name for name in old_names if name.startswith('sunhill-upgrade-')}
        if protected!={name for name in new_names if name.startswith('sunhill-upgrade-')} or any(old_contents[name]!=new_contents[name] for name in protected):
            raise ReleaseError('recovery units changed in backup')
        desired=old_contents if kind=='old' else new_contents
        desired_runtime=old_runtime if kind=='old' else new_runtime
        old_proxy=self._read_trusted(backup/'old/nginx.conf');new_proxy=self._read_trusted(backup/'new/nginx.conf')
        if self._read_trusted(self.nginx_file) not in (old_proxy,new_proxy):
            raise ReleaseError('proxy configuration changed outside update')
        proxy=old_proxy if kind=='old' else new_proxy
        # Reject files changed outside the transaction, including symlinks.
        for name in set(old_names)|set(new_names):
            target=self.system_dir/name
            if not target.exists() and not target.is_symlink():continue
            current=self._read_trusted(target)
            if current not in (old_contents.get(name),new_contents.get(name)):
                raise ReleaseError('managed unit changed outside update')
        to_disable=(set(old_runtime['enabled'])|set(new_runtime['enabled']))-set(desired_runtime['enabled'])
        to_disable=sorted(name for name in to_disable if (self.system_dir/name).exists())
        if to_disable:self._command(['/usr/bin/systemctl','disable',*to_disable],timeout=120)
        for name,data in desired.items():self._replace(self.system_dir/name,data)
        self._replace(self.nginx_file,proxy)
        self._replace(self.state/'managed-units.json',json.dumps(sorted(desired),separators=(',',':')).encode())
        self._replace(self.state/'lifecycle.json',json.dumps(desired_runtime,separators=(',',':')).encode())
        # The active lifecycle always references existing unit files, including
        # after power loss between these independently atomic replacements.
        for name in (set(old_names)|set(new_names))-set(desired):
            target=self.system_dir/name
            if target.exists():target.unlink();self._sync_parent(target)
        self._command(['/usr/bin/systemctl','daemon-reload'],timeout=60)
        self._command(['/usr/bin/systemd-analyze','verify',*(str(self.system_dir/name) for name in sorted(desired))],timeout=120)
        self._command(['/usr/sbin/nginx','-t'],timeout=60)
        if desired_runtime['enabled']:
            self._command(['/usr/bin/systemctl','enable',*desired_runtime['enabled']],timeout=120)
        if not offline:self._command(['/usr/bin/systemctl','reload','nginx.service'],timeout=60)

    def _save_rollback_context(self,destination: Path) -> None:
        self._write_private(destination/'instance.json',self._read_trusted(self.config_file,limit=16384))
        self._write_private(destination/'tls-mode',b'1' if self.tls_certificate.exists() else b'0')

    def check_rollback_context(self,folder: Path) -> None:
        self._trusted_backup_directory(folder)
        if self._read_trusted(folder/'instance.json',limit=16384)!=self._read_trusted(self.config_file,limit=16384):
            raise ReleaseError('instance configuration changed since restore point')
        if self.tls_certificate.exists()!=self.tls_key.exists() or self._read_trusted(folder/'tls-mode',limit=4)!=(b'1' if self.tls_certificate.exists() else b'0'):
            raise ReleaseError('TLS mode changed since restore point')

    def backup_rollback_system(self,folder: Path,destination: Path) -> None:
        """Build a fresh reversible transaction from an earlier system snapshot."""
        self.check_rollback_context(folder)
        saved=json.loads(self._read_trusted(folder/'units.json',limit=32768))
        if not isinstance(saved,dict):raise ReleaseError('legacy restore point has no lifecycle manifest')
        wanted_names,_=self._backup_unit_sets(saved)
        names=self._managed_names();source=folder/'old';self._trusted_backup_directory(source)
        wanted=lifecycle.validate(json.loads(self._read_trusted(source/'lifecycle.json',limit=32768)),wanted_names)
        runtime=lifecycle.validate(self._runtime(),names)
        actual=self._enabled(runtime['active']);runtime['enabled']=[n for n in runtime['active'] if n in actual]
        old_contents={n:self._read_trusted(self.system_dir/n) for n in names}
        new_contents={n:self._read_trusted(source/n) for n in wanted_names}
        protected={n for n in names if n.startswith('sunhill-upgrade-')}
        if protected!={n for n in wanted_names if n.startswith('sunhill-upgrade-')} or any(old_contents[n]!=new_contents[n] for n in protected):
            raise ReleaseError('restore point changes recovery units')
        for n in set(wanted_names)-set(names):
            p=self.system_dir/n
            if p.exists() or p.is_symlink():raise ReleaseError('restore unit conflicts with existing configuration')
        proxy=self._read_trusted(source/'nginx.conf')
        if b'if (-f /opt/sunhill-nvr/config/maintenance.flag) { return 503; }' not in proxy:
            raise ReleaseError('restore proxy lacks maintenance fence')
        destination.mkdir(mode=0o700)
        for side,contents,life,nginx in (('old',old_contents,runtime,self._read_trusted(self.nginx_file)),('new',new_contents,wanted,proxy)):
            directory=destination/side;directory.mkdir(mode=0o700)
            for n,body in contents.items():self._write_private(directory/n,body)
            self._write_private(directory/'lifecycle.json',json.dumps(life,separators=(',',':')).encode())
            self._write_private(directory/'nginx.conf',nginx)
            self._sync_parent(directory/'nginx.conf')
        self._write_private(destination/'units.json',json.dumps({'version':2,'old':names,'new':wanted_names},separators=(',',':')).encode())
        self._save_rollback_context(destination)
        self._write_private(destination/'complete',b'complete\n');self._sync_parent(destination/'complete')

    def retain_rollback(self,journal: dict) -> bool:
        from .rollback_history import retain
        # A terminal journal written by a pre-1.1.3 manager has no instance snapshot.
        if not journal.get('system_backup') or not (self.state/journal['system_backup']/'instance.json').is_file():return False
        return retain(self,journal)

    def backup_system(self,candidate: Path,destination: Path) -> None:
        """Capture active files and generate candidate files in root-only storage."""
        names=self._managed_names()
        cert=self.tls_certificate.exists();key=self.tls_key.exists()
        if cert!=key or self.tls_certificate.is_symlink() or self.tls_key.is_symlink():
            raise ReleaseError('incomplete LAN TLS configuration')
        destination.mkdir(mode=0o700)
        old=destination/'old';new=destination/'new';old.mkdir(mode=0o700);new.mkdir(mode=0o700)
        with tempfile.TemporaryDirectory(prefix='.generated-',dir=self.state) as temp_name:
            generated=Path(temp_name)/'generated';generated.mkdir(mode=0o700)
            command=['/usr/bin/env','-i','PATH=/usr/bin:/bin',f'PYTHONPATH={candidate / "app"}',
                     '/usr/bin/python3','-m','sunhill_nvr.deployment_cli','--config',str(self.config_file),'--output',str(generated)]
            if cert:command.append('--lan-tls')
            self._runtime() # validate installed lifecycle before generating candidate
            command.append('--rtsp-only')
            self._command(command,timeout=60)
            unit_dir=generated/'units'
            if unit_dir.is_symlink() or not unit_dir.is_dir():raise ReleaseError('generated units unavailable')
            next_names=sorted(path.name for path in unit_dir.iterdir())
            if (not next_names or len(next_names)>100 or len(set(next_names))!=len(next_names)
                or any(not lifecycle.NAME.fullmatch(name) for name in next_names)):
                raise ReleaseError('invalid generated unit list')
            manifest=generated/'lifecycle.json'
            modern=manifest.exists() or manifest.is_symlink()
            if not modern:
                raise ReleaseError('update changes managed unit list without lifecycle manifest')
            if {n for n in names if n.startswith('sunhill-upgrade-')}!={n for n in next_names if n.startswith('sunhill-upgrade-')}:
                raise ReleaseError('update changes recovery unit list; manual migration required')
            old_contents={name:self._read_trusted(self.system_dir/name) for name in names}
            new_contents={name:self._read_trusted(unit_dir/name) for name in next_names}
            for name in set(names)&set(next_names):
                if name.startswith('sunhill-upgrade-') and old_contents[name]!=new_contents[name]:
                    raise ReleaseError('update changes recovery units; manual migration required')
            for name in set(next_names)-set(names):
                target=self.system_dir/name
                if target.exists() or target.is_symlink():raise ReleaseError('new unit conflicts with existing configuration')
            for name in next_names if modern else ():
                if name.startswith('sunhill-upgrade-'):continue
                body=new_contents[name]
                lines=[line.strip() for line in body.splitlines() if not line.lstrip().startswith(b'#')]
                requires=any(line.startswith(b'Requires=') and b'sunhill-upgrade-recover.service' in line.split(b'=',1)[1].split() for line in lines)
                after=any(line.startswith(b'After=') and b'sunhill-upgrade-recover.service' in line.split(b'=',1)[1].split() for line in lines)
                if not requires or not after:
                    raise ReleaseError('new unit lacks boot recovery dependency')
            for name,previous in old_contents.items():self._write_private(old/name,previous)
            for name,following in new_contents.items():self._write_private(new/name,following)
            if modern:
                try:
                    old_runtime=lifecycle.validate(self._runtime(),names)
                    wanted=lifecycle.validate(json.loads(self._read_trusted(manifest,limit=32768)),next_names)
                except (TypeError,ValueError) as exc:raise ReleaseError('invalid lifecycle manifest') from exc
                actual=self._enabled(old_runtime['active'])
                old_runtime['enabled']=[name for name in old_runtime['active'] if name in actual]
                wanted['enabled']=[name for name in wanted['active'] if (name in actual and name in names) or (name not in names and name in wanted['enabled'])]
                self._write_private(old/'lifecycle.json',json.dumps(old_runtime,separators=(',',':')).encode())
                self._write_private(new/'lifecycle.json',json.dumps(wanted,separators=(',',':')).encode())
            previous=self._read_trusted(self.nginx_file)
            following=self._read_trusted(generated/'nginx.conf')
            if b'if (-f /opt/sunhill-nvr/config/maintenance.flag) { return 503; }' not in following:
                raise ReleaseError('generated proxy lacks maintenance fence')
            self._write_private(old/'nginx.conf',previous);self._write_private(new/'nginx.conf',following)
        saved={'version':2,'old':names,'new':next_names} if modern else names
        self._write_private(destination/'units.json',json.dumps(saved,separators=(',',':')).encode())
        if modern:self._save_rollback_context(destination)
        self._write_private(destination/'complete',b'complete\n')
        for directory in (old,new,destination):
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)

    def _restore_files(self,backup: Path,kind: str, *, offline: bool=False) -> None:
        if kind not in ('old','new'):raise ReleaseError('invalid system backup')
        self._trusted_backup_directory(backup)
        if self._read_trusted(backup/'complete',limit=64)!=b'complete\n':raise ReleaseError('incomplete system backup')
        try:names=json.loads(self._read_trusted(backup/'units.json',limit=32768))
        except (ValueError,TypeError) as exc:raise ReleaseError('invalid system backup') from exc
        if isinstance(names,dict):
            self._restore_units_v2(backup,kind,names,offline=offline);return
        if names!=self._managed_names():raise ReleaseError('managed unit list changed')
        directory=backup/kind
        if directory.is_symlink() or not directory.is_dir():raise ReleaseError('invalid system backup')
        # Read everything before replacing any file, so invalid backups fail closed.
        contents={name:self._read_trusted(directory/name) for name in names}
        proxy=self._read_trusted(directory/'nginx.conf')
        for name,data in contents.items():self._replace(self.system_dir/name,data)
        self._replace(self.nginx_file,proxy)
        self._command(['/usr/bin/systemd-analyze','verify',*(str(self.system_dir/name) for name in names)],timeout=120)
        self._command(['/usr/bin/systemctl','daemon-reload'],timeout=60)
        self._command(['/usr/sbin/nginx','-t'],timeout=60)
        if not offline:self._command(['/usr/bin/systemctl','reload','nginx.service'],timeout=60)

    def apply_system(self,backup: Path) -> None:self._restore_files(backup,'new')
    def restore_system(self,backup: Path) -> None:self._restore_files(backup,'old')
    def restore_system_offline(self,backup: Path) -> None:self._restore_files(backup,'old',offline=True)
    def finalize_system(self) -> None:
        self._command(['/usr/sbin/nginx','-t'],timeout=60)
        self._command(['/usr/bin/systemctl','reload','nginx.service'],timeout=60)

    def operations(self) -> Operations:
        return Operations(self.fence,self.unfence,self.stop,self.assert_stopped,self.start,self.healthy,self.backup,self.restore,self.migrate,self.backup_system,self.apply_system,self.restore_system,self.restore_system_offline,self.finalize_system,self.retain_rollback)
