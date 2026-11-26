#!/usr/bin/env python3
"""Export a clean main branch with a directly cloneable, verified Git bundle."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


PREFIX = 'sunhill-nvr/'
BUNDLE = 'sunhill-nvr-history.bundle'


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE)


def package(repo: Path, output: Path) -> str:
    repo = repo.resolve()
    output = output.absolute()
    if output.suffix != '.zip' or output.exists() or output.is_symlink():
        raise ValueError('output must be a new ZIP file')
    if output.is_relative_to(repo):
        raise ValueError('output must be outside the working tree')
    if git(repo, 'branch', '--show-current').strip() != b'main':
        raise ValueError('package must be built on main')
    if git(repo, 'status', '--porcelain'):
        raise ValueError('commit all changes before packaging')
    head = git(repo, 'rev-parse', 'HEAD').decode().strip()
    # Put the temporary archive on the destination filesystem, so publishing it
    # is atomic. Do not expose the output until both verification passes finish.
    with tempfile.TemporaryDirectory(prefix='.sunhill-handover-', dir=output.parent) as tmp:
        work = Path(tmp)
        archive = work / 'handover.zip'
        bundle = work / BUNDLE
        clone = work / 'clone'
        git(repo, 'archive', '--format=zip', '--prefix=' + PREFIX, '-o', str(archive), head)
        # Including HEAD explicitly makes plain git clone choose main without -b.
        git(repo, 'bundle', 'create', str(bundle), 'main', 'HEAD')
        git(repo, 'bundle', 'verify', str(bundle))
        subprocess.run(['git', 'clone', '--quiet', str(bundle), str(clone)], check=True, capture_output=True)
        if (git(clone, 'rev-parse', 'HEAD').decode().strip() != head
            or git(clone, 'branch', '--show-current').strip() != b'main'
            or git(clone, 'status', '--porcelain')):
            raise RuntimeError('bundle did not reproduce the clean main branch')
        git(clone, 'fsck', '--full')
        with zipfile.ZipFile(archive, 'a', compression=zipfile.ZIP_DEFLATED) as zipped:
            zipped.write(bundle, PREFIX + BUNDLE)
        with zipfile.ZipFile(archive) as zipped:
            if zipped.testzip() is not None:
                raise RuntimeError('invalid ZIP checksum')
            files = [name for name in zipped.namelist() if not name.endswith('/')]
            tracked = git(clone, 'ls-files', '-z').decode('utf-8').rstrip('\0').split('\0')
            expected = [PREFIX + name for name in tracked] + [PREFIX + BUNDLE]
            if sorted(files) != sorted(expected):
                raise RuntimeError('ZIP contents differ from the Git tree')
            for name in tracked:
                # git show checks exact repository bytes, not working-tree
                # conversion filters. git archive represents symlinks as links.
                if zipped.read(PREFIX + name) != git(clone, 'show', head + ':' + name):
                    raise RuntimeError('ZIP content differs: ' + name)
            if zipped.read(PREFIX + BUNDLE) != bundle.read_bytes():
                raise RuntimeError('bundle changed during packaging')
        # Do not overwrite an existing handover, including a concurrent creation.
        with archive.open('rb') as handle:
            os.fsync(handle.fileno())
        os.link(archive, output)
    return head


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(package(args.repo, args.output))


if __name__ == '__main__':
    main()
