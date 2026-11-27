"""Root-side adapter for the recording directory transaction."""
from __future__ import annotations
import json
import os
import secrets
import stat
import tempfile
from dataclasses import asdict,dataclass
from pathlib import Path

from . import config,mount,units
from .media_layout import nested_auxiliary,overlap
from .release import ReleaseError
from .storage_transaction import Operations
from .upgrade_system import SystemAdapter,_copy_private

@dataclass(frozen=True)
class MediaSelection:
    archive: Path
    exports: Path
    staging: Path | None = None

class StorageSystem:
    def __init__(self, adapter: SystemAdapter):self.adapter=adapter

    def candidate(self,path: Path | MediaSelection) -> config.Config:
        a=self.adapter;old=config.load(a.config_file)
        if isinstance(path,MediaSelection):
            return self._media_candidate(old,path)
        if not path.is_absolute() or path.is_symlink() or not path.is_dir() or path.resolve(strict=True)!=path:
            raise ReleaseError('archive must be an existing canonical directory')
        if path==old.archive:return old
        if path in old.archive.parents or old.archive in path.parents:
            raise ReleaseError('new and old archive paths overlap')
        try:path.relative_to(old.staging.parent)
        except ValueError as exc:raise ReleaseError('archive must be inside the configured data directory') from exc
        info=path.stat()
        if info.st_uid!=a.uid or info.st_mode&0o022:raise ReleaseError('archive must be owned by the service account and not writable by others')
        actual=mount.inspect(path);staging=mount.inspect(old.staging)
        try:mount.require_local(actual)
        except ValueError as exc:raise ReleaseError(str(exc)) from exc
        if actual.target=='/' or not mount.same(actual,staging):
            raise ReleaseError('archive must be on the same data filesystem as recording staging')
        raw=asdict(old)
        for key,value in raw.items():
            if isinstance(value,Path):raw[key]=str(value)
        raw['archive']=str(path)
        result=config.parse(raw)
        units.render(result)
        a._command(['/usr/sbin/runuser','-u','sunhill','--','/usr/bin/test','-r',str(path),'-a','-w',str(path),'-a','-x',str(path)])
        return result

    def _media_candidate(self,old: config.Config,selection: MediaSelection) -> config.Config:
        a=self.adapter
        archive,exports=selection.archive,selection.exports
        staging=selection.staging or (old.staging if archive==old.archive else archive.parent/'.sunhill-staging')
        if (archive,exports,staging)==(old.archive,old.exports,old.staging):return old
        raw=asdict(old)
        for key,value in raw.items():
            if isinstance(value,Path):raw[key]=str(value)
        raw.update(archive=str(archive),exports=str(exports),staging=str(staging))
        result=config.parse(raw);units.render(result)
        for path in (archive,exports,staging):
            # A web request must never make the root coordinator write to system
            # or executable paths. All directory creation is done as sunhill.
            if not path.is_absolute() or path.resolve(strict=False)!=path:
                raise ReleaseError('media path must be canonical')
            protected=(Path('/etc'),Path('/usr'),Path('/bin'),Path('/sbin'),Path('/lib'),Path('/lib64'),Path('/boot'),Path('/proc'),Path('/sys'),Path('/dev'),Path('/run'),Path('/root'))
            if any(path==p or p in path.parents or path in p.parents for p in protected):raise ReleaseError('protected system path')
            root=Path('/opt/sunhill-nvr');media=root/'media'
            if (root==path or path in root.parents or root in path.parents) and not (media in path.parents):
                raise ReleaseError('application code and private data are not media directories')
            if not path.exists():
                # Only create a leaf under an existing, writable canonical parent.
                parent=archive.parent if nested_auxiliary(archive,path) and not archive.exists() else path.parent
                if not parent.is_dir() or parent.resolve(strict=True)!=parent:raise ReleaseError('media parent missing')
                try:a._command(['/usr/sbin/runuser','-u','sunhill','--','/usr/bin/test','-w',str(parent),'-a','-x',str(parent)])
                except ReleaseError as exc:raise ReleaseError('media parent not writable') from exc
                mount.require_local(mount.inspect(parent))
                continue
            info=path.stat()
            if not path.is_dir() or path.is_symlink() or info.st_uid!=a.uid or info.st_mode&0o022:
                raise ReleaseError('media directory must belong to sunhill and not be writable by others')
            a._command(['/usr/sbin/runuser','-u','sunhill','--','/usr/bin/test','-r',str(path),'-a','-w',str(path),'-a','-x',str(path)])
            mount.require_local(mount.inspect(path))
        actual=mount.inspect(archive if archive.exists() else archive.parent)
        stage_probe=staging if staging.exists() else (archive.parent if nested_auxiliary(archive,staging) and not archive.exists() else staging.parent)
        stage=mount.inspect(stage_probe)
        if not mount.same(actual,stage):raise ReleaseError('archive and staging must be on the same filesystem')
        if archive!=old.archive and (archive in old.archive.parents or old.archive in archive.parents):raise ReleaseError('old and new archive overlap')
        if exports!=old.exports and (exports in old.exports.parents or old.exports in exports.parents):raise ReleaseError('old and new export directories overlap')
        if exports!=old.exports and overlap(exports,old.archive) and not nested_auxiliary(old.archive,exports):raise ReleaseError('new exports overlap old archive')
        if archive!=old.archive and overlap(archive,old.exports) and not nested_auxiliary(archive,old.exports):raise ReleaseError('new archive overlaps old exports')
        if staging!=old.staging and overlap(staging,old.archive) and not nested_auxiliary(old.archive,staging):raise ReleaseError('archive, staging and exports must be separate directories')
        if archive!=old.archive and overlap(archive,old.staging) and not nested_auxiliary(archive,old.staging):raise ReleaseError('archive, staging and exports must be separate directories')
        if overlap(exports,old.staging) or overlap(staging,old.exports):raise ReleaseError('staging and exports must be separate directories')
        return result

    def _db_copy(self, destination: Path, *, restore: bool=False) -> None:
        a=self.adapter
        with tempfile.TemporaryDirectory(prefix='.storage-db-',dir=a.state) as name:
            temp=Path(name);raw=temp/'database.sqlite'
            if restore:_copy_private(destination,raw,uid=a.uid,gid=a.gid)
            os.chown(temp,a.uid,a.gid)
            a._as_service(a.current.resolve(strict=True),'sunhill_nvr.upgrade_database','--config',str(a.config_file),'restore' if restore else 'backup',str(raw))
            if not restore:_copy_private(raw,destination)

    def backup(self, destination: Path) -> None:
        a=self.adapter;destination.mkdir(mode=0o700)
        names=a._managed_names();directory=destination/'units';directory.mkdir(mode=0o700)
        SystemAdapter._write_private(destination/'config.json',SystemAdapter._read_trusted(a.config_file))
        for name in names:SystemAdapter._write_private(directory/name,SystemAdapter._read_trusted(a.system_dir/name))
        SystemAdapter._write_private(destination/'units.json',json.dumps(names,separators=(',',':')).encode())
        self._db_copy(destination/'database.sqlite')
        SystemAdapter._write_private(destination/'complete',b'complete\n')
        for directory_path in (directory,destination):
            fd=os.open(directory_path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)

    def _replace_config(self,data: bytes) -> None:
        a=self.adapter;temp=a.config_file.parent/('.storage-config-'+secrets.token_hex(16))
        try:
            SystemAdapter._write_private(temp,data,mode=0o640)
            os.chown(temp,os.geteuid(),a.gid);os.chmod(temp,0o640)
            os.replace(temp,a.config_file);a._sync_parent(a.config_file)
        finally:temp.unlink(missing_ok=True)

    def _install_units(self,contents: dict[str,bytes]) -> None:
        a=self.adapter
        for name,data in contents.items():SystemAdapter._replace(a.system_dir/name,data)
        a._command(['/usr/bin/systemd-analyze','verify',*(str(a.system_dir/name) for name in sorted(contents))],timeout=120)
        a._command(['/usr/bin/systemctl','daemon-reload'])

    def apply(self,path: Path) -> None:
        a=self.adapter;cfg=self.candidate(path);names=a._managed_names();generated=units.render(cfg,rtsp_only=a._runtime()['version']==2)
        if sorted(generated)!=names:raise ReleaseError('managed units changed unexpectedly')
        contents={name:generated[name].encode() for name in names}
        for name in names:
            if name.startswith('sunhill-upgrade-') and contents[name]!=SystemAdapter._read_trusted(a.system_dir/name):
                raise ReleaseError('storage change alters recovery unit')
        # The helper runs as the service user, never root, and verifies the mount
        # again before replacing its marker and invalidating the old index.
        if isinstance(path,MediaSelection):
            a._as_service(a.current.resolve(strict=True),'sunhill_nvr.storage_database','--config',str(a.config_file),str(cfg.archive),'--staging',str(cfg.staging),'--exports',str(cfg.exports))
        else:
            a._as_service(a.current.resolve(strict=True),'sunhill_nvr.storage_database','--config',str(a.config_file),str(path))
        raw=asdict(cfg)
        for key,value in raw.items():
            if isinstance(value,Path):raw[key]=str(value)
        self._replace_config((json.dumps(raw,indent=2)+'\n').encode())
        self._install_units(contents)

    def restore(self, backup: Path) -> None:
        a=self.adapter
        if backup.is_symlink() or not backup.is_dir() or SystemAdapter._read_trusted(backup/'complete',limit=64)!=b'complete\n':
            raise ReleaseError('incomplete storage backup')
        try:names=json.loads(SystemAdapter._read_trusted(backup/'units.json',limit=32768))
        except (ValueError,TypeError) as exc:raise ReleaseError('invalid storage backup') from exc
        if names!=a._managed_names():raise ReleaseError('managed unit list changed')
        directory=backup/'units'
        if directory.is_symlink() or not directory.is_dir():raise ReleaseError('invalid storage backup')
        contents={name:SystemAdapter._read_trusted(directory/name) for name in names}
        previous=SystemAdapter._read_trusted(backup/'config.json')
        self._replace_config(previous);self._db_copy(backup/'database.sqlite',restore=True)
        self._install_units(contents)

    def operations(self) -> Operations:
        a=self.adapter
        return Operations(a.fence,a.unfence,a.stop,a.assert_stopped,a.start,a.healthy,self.backup,self.apply,self.restore)
