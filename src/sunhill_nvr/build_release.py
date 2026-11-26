"""Create a signed release on a trusted development machine.

The private signing key must never be copied to an NVR installation. This tool
does not publish releases or modify a running server.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .release import MAX_BYTES, MAX_FILES, MAX_MANIFEST_BYTES, VERSION, ReleaseError


def build(source: Path, version: str, private_key: Path, archive: Path, signature: Path,
          *, openssl: str = 'openssl', passphrase: str | None = None) -> None:
    """Build and sign an immutable package without overwriting existing outputs."""
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError('invalid release version')
    if passphrase is not None and (not isinstance(passphrase,str) or not passphrase or
                                   len(passphrase.encode('utf-8'))>1024 or any(c in passphrase for c in '\r\n\0')):
        raise ValueError('invalid signing key passphrase')
    if source.is_symlink() or not source.is_dir() or private_key.is_symlink() or not private_key.is_file():
        raise ValueError('invalid source or signing key')
    if archive == signature or any(p.exists() or p.is_symlink() for p in (archive, signature)):
        raise FileExistsError('release output already exists')
    if archive.parent != signature.parent or archive.parent.is_symlink() or not archive.parent.is_dir():
        raise ValueError('release outputs require a common real directory')
    package = source / 'sunhill_nvr'
    if package.is_symlink() or not package.is_dir() or not (package / '__init__.py').is_file():
        raise ValueError('source does not contain the application package')
    files = []
    for directory, dirs, names in os.walk(package, followlinks=False):
        base = Path(directory)
        dirs.sort(); names.sort()
        if any((base / name).is_symlink() for name in dirs):
            raise ValueError('source contains a symbolic link')
        dirs[:] = [name for name in dirs if name != '__pycache__']
        for name in names:
            path = base / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('source contains an unsafe file')
            if path.suffix == '.pyc':
                continue
            if path.suffix not in ('.py', '.js', '.css', '.html'):
                raise ValueError('unexpected file in application source')
            files.append((path, 'app/' + str(path.relative_to(source)).replace(os.sep, '/')))
    if not files or len(files) + 1 > MAX_FILES:
        raise ValueError('invalid application file count')
    if sum(path.stat().st_size for path, _ in files) > MAX_BYTES:
        raise ValueError('application source is too large')
    # Private temporary files prevent a failed build from leaving a usable-looking
    # release. Hard-link publishing refuses an existing output even under races.
    with tempfile.TemporaryDirectory(prefix='.sunhill-build-', dir=archive.parent) as temp:
        temp_root = Path(temp); package_path = temp_root / 'release.zip'; signature_path = temp_root / 'signature'
        manifest = {'version': version, 'files': {}}
        total = 0
        with zipfile.ZipFile(package_path, 'x', compression=zipfile.ZIP_DEFLATED) as output:
            for path, name in files:
                # No-follow open and bounded reads prevent a swapped file or a
                # growing source from exposing arbitrary files or exhausting RAM.
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                        raise ValueError('source contains an unsafe file')
                    parts = []
                    while True:
                        chunk = os.read(fd, min(1024 * 1024, MAX_BYTES - total + 1))
                        if not chunk:break
                        total += len(chunk)
                        if total > MAX_BYTES:raise ValueError('application source is too large')
                        parts.append(chunk)
                    after = os.fstat(fd)
                    if (before.st_size,before.st_mtime_ns,before.st_ino,before.st_dev)!=(after.st_size,after.st_mtime_ns,after.st_ino,after.st_dev):
                        raise ValueError('application source changed during packaging')
                    data = b''.join(parts)
                finally:
                    os.close(fd)
                manifest['files'][name] = hashlib.sha256(data).hexdigest()
                info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                output.writestr(info, data)
            info = zipfile.ZipInfo('manifest.json', (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            manifest_data = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
            if len(manifest_data)>MAX_MANIFEST_BYTES or total + len(manifest_data) > MAX_BYTES:raise ValueError('application source is too large')
            output.writestr(info, manifest_data)
        if package_path.stat().st_size > MAX_BYTES:raise ValueError('release archive is too large')
        try:
            with signature_path.open('xb') as dst:
                command=[openssl, 'dgst', '-sha256', '-sign', str(private_key)]
                if passphrase is not None:command+=['-passin','stdin']
                command.append(str(package_path))
                result = subprocess.run(command,input=(passphrase+'\n').encode('utf-8') if passphrase is not None else None,
                                        stdin=subprocess.DEVNULL if passphrase is None else None,
                                        stdout=dst, stderr=subprocess.DEVNULL, timeout=30)
            if result.returncode != 0 or not signature_path.stat().st_size:
                raise ReleaseError('release signing failed')
        except (OSError, subprocess.SubprocessError) as exc:
            raise ReleaseError('release signing failed') from exc
        os.link(package_path, archive)
        try:
            os.link(signature_path, signature)
        except BaseException:
            archive.unlink(missing_ok=True)
            raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Build a signed Sunhill NVR release')
    parser.add_argument('--source', type=Path, required=True, help='directory containing sunhill_nvr/')
    parser.add_argument('--version', required=True)
    parser.add_argument('--private-key', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--signature', type=Path, required=True)
    parser.add_argument('--ask-passphrase', action='store_true', help='prompt for the encrypted signing key passphrase')
    args = parser.parse_args(argv)
    passphrase=getpass.getpass('Hasło klucza podpisującego: ') if args.ask_passphrase else None
    build(args.source, args.version, args.private_key, args.archive, args.signature,passphrase=passphrase)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
