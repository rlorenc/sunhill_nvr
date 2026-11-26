"""Verify and stage signed immutable application releases.

Activation and database migrations intentionally belong to the deployment layer.
Never store the release signing private key on the application server.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

class ReleaseError(RuntimeError): pass

VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-.][A-Za-z0-9]+)*\Z')
MAX_FILES = 5000
MAX_BYTES = 200 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024

def _snapshot(source: Path, destination: Path, maximum: int, *, owner_uid: int | None=None) -> None:
    """Copy an untrusted upload to a private regular file without following links."""
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>maximum:
            raise ReleaseError('invalid release input')
        if owner_uid is not None and (before.st_uid!=owner_uid or before.st_mode&0o022):raise ReleaseError('release input must be administrator-owned')
        total=0
        with os.fdopen(os.dup(fd),'rb') as src,destination.open('xb') as dst:
            while True:
                chunk=src.read(min(1024*1024,maximum-total+1))
                if not chunk:break
                total+=len(chunk)
                if total>maximum:raise ReleaseError('release input too large')
                dst.write(chunk)
        after=os.fstat(fd)
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
            raise ReleaseError('release input changed during verification')
    finally:os.close(fd)

def verify_signature(archive: Path, signature: Path, public_key: Path, *, openssl: str = 'openssl') -> None:
    for p in (archive, signature, public_key):
        if p.is_symlink() or not p.is_file():
            raise ReleaseError('invalid release verification file')
    try:
        p = subprocess.run([openssl, 'dgst', '-sha256', '-verify', str(public_key), '-signature', str(signature), str(archive)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError('signature verification unavailable') from exc
    if p.returncode != 0:
        raise ReleaseError('release signature is invalid')

def _matches_existing(destination: Path, manifest: dict) -> bool:
    """Reuse only a complete, identical release in the trusted versions tree."""
    if destination.is_symlink() or not destination.is_dir():return False
    try:
        manifest_file=destination/'manifest.json'
        if manifest_file.is_symlink() or json.loads(manifest_file.read_text(encoding='utf-8'))!=manifest:return False
        expected=manifest['files'];seen=set()
        for directory,dirs,files in os.walk(destination,followlinks=False):
            base=Path(directory)
            if any((base/name).is_symlink() for name in dirs):return False
            for name in files:
                file=base/name;relative=str(file.relative_to(destination)).replace(os.sep,'/')
                if relative=='manifest.json':continue
                info=file.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or relative not in expected:return False
                digest=hashlib.sha256()
                with file.open('rb') as source:
                    for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
                if digest.hexdigest()!=expected[relative]:return False
                seen.add(relative)
        return seen==set(expected)
    except (OSError,ValueError,TypeError,KeyError):return False

def stage_verified(archive: Path, signature: Path, public_key: Path, versions_dir: Path,
                   *, openssl: str = 'openssl', reuse_existing: bool = False) -> Path:
    """Verify signature and manifest then stage release without modifying live state."""
    if versions_dir.is_symlink() or not versions_dir.is_dir():
        raise ReleaseError('invalid versions directory')
    temp = Path(tempfile.mkdtemp(prefix='.staging-', dir=versions_dir))
    try:
        # Verify and extract the same private snapshot, so a writable uploaded file
        # cannot be swapped after signature verification.
        snapshot = temp / '.release-input.zip'
        snapshot_signature=temp/'.release-input.sig'
        _snapshot(archive,snapshot,MAX_BYTES)
        _snapshot(signature,snapshot_signature,65536)
        verify_signature(snapshot, snapshot_signature, public_key, openssl=openssl)
        with zipfile.ZipFile(snapshot) as z:
            entries = z.infolist()
            if not 1 <= len(entries) <= MAX_FILES or sum(x.file_size for x in entries) > MAX_BYTES:
                raise ReleaseError('invalid archive size')
            seen = set()
            for entry in entries:
                name = entry.filename
                path = PurePosixPath(name)
                mode = (entry.external_attr >> 16) & 0xffff
                # All files must be under app/ except the root manifest.
                if (name in seen or not name or '\\' in name or '\x00' in name or path.is_absolute()
                        or any(part in ('', '.', '..') for part in path.parts)
                        or (name != 'manifest.json' and (not path.parts or path.parts[0] != 'app'))
                        or stat.S_ISLNK(mode) or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
                    raise ReleaseError('unsafe archive member')
                seen.add(name)
            if 'manifest.json' not in seen:
                raise ReleaseError('missing manifest')
            if z.getinfo('manifest.json').file_size>MAX_MANIFEST_BYTES:
                raise ReleaseError('release manifest is too large')
            try:
                meta = json.loads(z.read('manifest.json'))
                version, files = meta['version'], meta['files']
            except (ValueError, KeyError, TypeError) as exc:
                raise ReleaseError('invalid manifest') from exc
            if not isinstance(version,str) or not VERSION.fullmatch(version) or not isinstance(files,dict):
                raise ReleaseError('invalid version or file manifest')
            archive_files = {e.filename for e in entries if not e.is_dir() and e.filename != 'manifest.json'}
            if set(files) != archive_files:
                raise ReleaseError('manifest does not match archive')
            destination = versions_dir / version
            if (destination.exists() or destination.is_symlink()) and not reuse_existing:
                raise ReleaseError('release already exists')
            for entry in entries:
                if entry.is_dir() or entry.filename == 'manifest.json':
                    continue
                expected = files[entry.filename]
                if not isinstance(expected,str) or not re.fullmatch(r'[a-f0-9]{64}', expected):
                    raise ReleaseError('invalid file digest')
                out = temp.joinpath(*PurePosixPath(entry.filename).parts)
                out.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
                digest = hashlib.sha256()
                # Manifest files start non-executable. Any executable entry points are
                # invoked through their interpreters, not by an archive-supplied mode.
                with z.open(entry) as src, out.open('xb') as dst:
                    for chunk in iter(lambda: src.read(1024*1024), b''):
                        digest.update(chunk);dst.write(chunk)
                os.chmod(out,0o644)
                if digest.hexdigest() != expected:
                    raise ReleaseError('release file digest mismatch')
            (temp/'manifest.json').write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
        snapshot.unlink()
        snapshot_signature.unlink()
        if destination.exists() or destination.is_symlink():
            if not reuse_existing or not _matches_existing(destination,meta):
                raise ReleaseError('existing release does not match the signed package')
            return destination
        # The updater runs as root while all application workers run as the
        # unprivileged service account. Do not publish an unreadable release.
        # Only verified code and the public manifest are present at this point.
        for directory,_,_ in os.walk(temp):os.chmod(directory,0o755)
        os.chmod(temp/'manifest.json',0o644)
        os.rename(temp,destination)
        return destination
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ReleaseError('unable to stage release') from exc
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def activate(versions_dir: Path, current_link: Path, version: str, *, restart_and_check) -> None:
    """Atomically select a staged release, restoring the old link on failed health check.

    The calling privileged updater must stop writers and handle database migrations
    and restoration separately. Paths must be in an administrator-owned directory.
    """
    if not VERSION.fullmatch(version) or versions_dir.is_symlink() or not versions_dir.is_dir():
        raise ReleaseError('invalid release location')
    target=versions_dir/version
    if target.is_symlink() or not target.is_dir() or not (target/'manifest.json').is_file():
        raise ReleaseError('release has not been staged')
    if current_link.parent.is_symlink() or not current_link.parent.is_dir():
        raise ReleaseError('invalid current link parent')
    if current_link.exists() and not current_link.is_symlink():
        raise ReleaseError('current path is not a symbolic link')
    old = os.readlink(current_link) if current_link.is_symlink() else None
    # Require old and new releases to stay in the controlled versions directory.
    if old is not None:
        old_path=(current_link.parent/old).resolve()
        if old_path.parent != versions_dir.resolve() or not old_path.is_dir():
            raise ReleaseError('current link points outside release directory')
    def swap(value: str) -> None:
        import secrets
        pending=current_link.parent/('.sunhill-next-'+secrets.token_hex(12))
        try:
            os.symlink(value,pending)
            os.replace(pending,current_link)
        finally:
            if pending.is_symlink():pending.unlink()
    new_value=os.path.relpath(target,current_link.parent)
    swap(new_value)
    try:
        if restart_and_check() is not True:
            raise ReleaseError('new release failed its health check')
    except BaseException as failure:
        if old is not None:
            swap(old)
            try:
                if restart_and_check() is not True:
                    raise ReleaseError('previous release failed its health check')
            except Exception as rollback_failure:
                raise ReleaseError('release failed and previous release could not be restarted') from rollback_failure
        else:
            current_link.unlink(missing_ok=True)
        raise ReleaseError('release failed; previous link restored') from failure


def stage_unsigned_tar(archive: Path, versions_dir: Path, *, reuse_existing: bool=False) -> Path:
    """Stage a root-approved distribution tar.gz. Hashes detect corruption, not publisher identity.

    No untrusted web upload can reach this entry point. Only application source
    listed in the manifest is extracted, never the installer or Git bundle.
    """
    import tarfile
    if os.geteuid()!=0:raise ReleaseError('unsigned update requires root approval')
    if versions_dir.is_symlink() or not versions_dir.is_dir():raise ReleaseError('invalid versions directory')
    temp=Path(tempfile.mkdtemp(prefix='.staging-',dir=versions_dir))
    try:
        snapshot=temp/'.release-input.tar.gz'
        # Snapshot the same root-owned regular file we will verify and extract.
        _snapshot(archive,snapshot,MAX_BYTES,owner_uid=0)
        root_name=None
        with tarfile.open(snapshot,'r:gz') as tar:
            entries={};total=0;count=0
            for entry in tar:
                count+=1;total+=entry.size
                name=entry.name;parts=PurePosixPath(name).parts
                if (count>MAX_FILES or total>MAX_BYTES or entry.size<0 or not parts or
                    not re.fullmatch(r'sunhill-nvr(?:-[0-9]+\.[0-9]+\.[0-9]+)?',parts[0]) or
                    name in entries or name.startswith('/') or '\\' in name or '\x00' in name or
                    '..' in parts or str(PurePosixPath(name))!=name or not (entry.isfile() or entry.isdir())):
                    raise ReleaseError('unsafe distribution member')
                if root_name is None:root_name=parts[0]
                if parts[0]!=root_name:raise ReleaseError('mixed distribution roots')
                entries[name]=entry
            prefix=(root_name or '')+'/'
            manifest=entries.get(prefix+'release-manifest.json')
            if manifest is None or not manifest.isfile() or manifest.size>MAX_MANIFEST_BYTES:raise ReleaseError('missing or invalid release manifest')
            with tar.extractfile(manifest) as f:meta=json.load(f)
            if not isinstance(meta,dict) or set(meta)!={'version','files'}:raise ReleaseError('invalid manifest')
            version,files=meta['version'],meta['files']
            if not isinstance(version,str) or not VERSION.fullmatch(version) or not isinstance(files,dict):raise ReleaseError('invalid manifest')
            if root_name not in ('sunhill-nvr','sunhill-nvr-'+version):raise ReleaseError('distribution root and manifest versions differ')
            app_entries={ 'app/'+name[len(prefix+'src/'):]:entry for name,entry in entries.items()
                         if name.startswith(prefix+'src/sunhill_nvr/') and entry.isfile() }
            if not app_entries or set(app_entries)!=set(files):raise ReleaseError('manifest does not match application files')
            for name,entry in app_entries.items():
                expected=files[name]
                if not isinstance(expected,str) or not re.fullmatch(r'[a-f0-9]{64}',expected):raise ReleaseError('invalid file digest')
                out=temp.joinpath(*PurePosixPath(name).parts);out.parent.mkdir(parents=True,exist_ok=True)
                digest=hashlib.sha256()
                with tar.extractfile(entry) as src,out.open('xb') as dst:
                    for chunk in iter(lambda:src.read(1024*1024),b''):digest.update(chunk);dst.write(chunk)
                if digest.hexdigest()!=expected:raise ReleaseError('release file digest mismatch')
                os.chmod(out,0o644)
            import ast
            version_file=temp/'app/sunhill_nvr/__init__.py'
            tree=ast.parse(version_file.read_text(encoding='utf-8'))
            declarations=[node.value.value for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__version__' for t in node.targets) and isinstance(node.value,ast.Constant)]
            if declarations!=[version]:raise ReleaseError('application and manifest versions differ')
            (temp/'manifest.json').write_text(json.dumps(meta,sort_keys=True),encoding='utf-8')
        snapshot.unlink()
        destination=versions_dir/version
        if destination.exists() or destination.is_symlink():
            if not reuse_existing or not _matches_existing(destination,meta):raise ReleaseError('release already exists or differs')
            return destination
        for directory,_,_ in os.walk(temp):os.chmod(directory,0o755)
        os.chmod(temp/'manifest.json',0o644)
        os.rename(temp,destination)
        return destination
    except (OSError,ValueError,TypeError,SyntaxError,tarfile.TarError) as exc:raise ReleaseError('unable to stage distribution') from exc
    finally:
        if temp.exists():shutil.rmtree(temp)
