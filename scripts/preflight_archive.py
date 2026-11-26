#!/usr/bin/env python3
"""Read-only permission check for the archive tree used by Sunhill NVR.

Run as the application account, not root. Symlinks and nested mounts are not
traversed, matching the archive scanner and retention worker.
"""
from __future__ import annotations
import datetime as dt
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

CAMERA=re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
VIDEO=re.compile(r'(?i)(?:\d{6}|[^/]+_00_\d{14}|\d{8}T\d{6}(?:[+-]\d{4})?(?:[-_]\d+)?)\.mp4\Z')
FLAGS=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW

class PreflightError(RuntimeError):pass

@dataclass
class Report:
    directories: int=0
    recordings: int=0
    skipped_mounts: int=0
    skipped_links: int=0

def mount_identity(fd: int) -> tuple[int,int | None]:
    device=os.fstat(fd).st_dev
    if not sys.platform.startswith('linux'):return device,None
    try:
        text=Path('/proc/self/fdinfo',str(fd)).read_text(encoding='ascii')
        values=[line.split(':',1)[1].strip() for line in text.splitlines() if line.startswith('mnt_id:')]
        if len(values)!=1 or not values[0].isdigit() or int(values[0])<=0:raise ValueError()
        return device,int(values[0])
    except (OSError,UnicodeError,ValueError) as exc:raise PreflightError('Nie można ustalić granicy systemu plików') from exc

def valid_recording(name: str, year: str, month: str, day: str) -> bool:
    if not VIDEO.fullmatch(name):return False
    try:
        if re.fullmatch(r'\d{6}\.mp4',name,re.I):
            timestamp=dt.datetime.strptime(year+month+day+name[:6],'%Y%m%d%H%M%S')
        else:
            camera=re.search(r'_00_(\d{14})\.mp4$',name,re.I)
            if camera:timestamp=dt.datetime.strptime(camera.group(1),'%Y%m%d%H%M%S')
            else:
                segment=re.match(r'(\d{8}T\d{6})([+-]\d{4})?',name)
                timestamp=dt.datetime.strptime(segment.group(1),'%Y%m%dT%H%M%S')
                if segment.group(2):dt.datetime.strptime(segment.group(2),'%z')
        return timestamp.strftime('%Y%m%d')==year+month+day
    except (ValueError,AttributeError):return False

def inspect_archive(root: Path, *, open_fn=os.open, access_fn=os.access, identity=mount_identity, progress=None) -> Report:
    """Verify read/delete permissions only for recognized recordings and dirs."""
    report=Report();descriptors=[]
    try:
        root_fd=open_fn(root,FLAGS);descriptors.append(root_fd);root_mount=identity(root_fd)
        def walk(fd: int, level: int, components: tuple[str,...]) -> None:
            if not access_fn('.',os.R_OK|os.W_OK|os.X_OK,dir_fd=fd,effective_ids=True):
                raise PreflightError('Brak dostępu do katalogu '+repr(str(root.joinpath(*components))))
            directory=os.fstat(fd)
            report.directories+=1
            try:
                with os.scandir(fd) as entries:
                    for entry in entries:
                        name=entry.name
                        try:info=entry.stat(follow_symlinks=False)
                        except FileNotFoundError:continue
                        if stat.S_ISLNK(info.st_mode):
                            report.skipped_links+=1;continue
                        if level<4:
                            matches=(CAMERA.fullmatch(name) if level==0 else
                                     re.fullmatch(r'\d{4}' if level==1 else r'\d{2}',name))
                            if not matches or not stat.S_ISDIR(info.st_mode):continue
                            if level:
                                try:
                                    parts=components[1:]+(name,)
                                    dt.date(int(parts[0]),int(parts[1]) if len(parts)>1 else 1,int(parts[2]) if len(parts)>2 else 1)
                                except ValueError:continue
                            try:child=open_fn(name,FLAGS,dir_fd=fd)
                            except FileNotFoundError:continue
                            try:
                                after=os.fstat(child)
                                if (after.st_dev,after.st_ino)!=(info.st_dev,info.st_ino):
                                    raise PreflightError('Katalog zmienił się podczas sprawdzania')
                                if identity(child)!=root_mount:
                                    report.skipped_mounts+=1;continue
                                walk(child,level+1,components+(name,))
                            finally:os.close(child)
                        elif stat.S_ISREG(info.st_mode) and valid_recording(name,*components[1:4]):
                            if info.st_nlink!=1:
                                report.skipped_links+=1;continue
                            try:file_fd=open_fn(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                            except FileNotFoundError:continue
                            except PermissionError as exc:
                                raise PreflightError('Brak dostępu do pliku '+repr(str(root.joinpath(*components,name)))) from exc
                            try:
                                after=os.fstat(file_fd)
                                if (after.st_dev,after.st_ino)!=(info.st_dev,info.st_ino):
                                    raise PreflightError('Plik zmienił się podczas sprawdzania')
                                if identity(file_fd)!=root_mount:
                                    report.skipped_mounts+=1;continue
                                if after.st_nlink!=1:
                                    report.skipped_links+=1;continue
                                uid=os.geteuid()
                                if directory.st_mode&stat.S_ISVTX and uid not in (0,directory.st_uid,after.st_uid):
                                    raise PreflightError('Nie można usuwać pliku w katalogu z bitem sticky: '+repr(str(root.joinpath(*components,name))))
                                report.recordings+=1
                                if progress and report.recordings%10000==0:progress(report)
                            finally:os.close(file_fd)
            except PermissionError as exc:
                raise PreflightError('Brak dostępu do '+repr(str(root.joinpath(*components)))) from exc
        walk(root_fd,0,())
        return report
    except PermissionError as exc:raise PreflightError('Brak dostępu do archiwum') from exc
    finally:
        for fd in reversed(descriptors):os.close(fd)

def main(argv=None) -> int:
    values=sys.argv[1:] if argv is None else argv
    if len(values)!=1 or not Path(values[0]).is_absolute():
        print('Wymagana bezwzględna ścieżka archiwum',file=sys.stderr);return 2
    try:
        report=inspect_archive(Path(values[0]),progress=lambda r:print(f'Sprawdzono nagrania: {r.recordings}',file=sys.stderr,flush=True))
    except (OSError,PreflightError) as exc:
        print('Nie można sprawdzić archiwum: '+str(exc),file=sys.stderr);return 1
    print(f'Sprawdzono katalogi: {report.directories}, nagrania: {report.recordings}; pominięte montowania: {report.skipped_mounts}, dowiązania: {report.skipped_links}.')
    return 0

if __name__=='__main__':raise SystemExit(main())
