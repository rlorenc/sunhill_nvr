"""Linux filesystem identity guard using findmnt JSON output."""
from __future__ import annotations
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Mount:
    target: str
    source: str
    fstype: str
    uuid: str | None

# The activity detector observes this host's processes. It cannot rule out
# writers on other hosts or in a separate filesystem server. Unknown types are
# intentionally excluded until their activity and locking semantics are tested.
LOCAL_FILESYSTEMS=frozenset({'ext2','ext3','ext4','xfs','btrfs','zfs','f2fs'})

def require_local(info: Mount) -> None:
    if info.fstype not in LOCAL_FILESYSTEMS:
        raise ValueError('unsupported archive filesystem; a verified local filesystem is required')

def inspect(path: Path, *, findmnt: str = '/usr/bin/findmnt') -> Mount:
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError('invalid storage path')
    try:
        p=subprocess.run([findmnt,'--json','--output','TARGET,SOURCE,FSTYPE','--target',str(path)],
                         stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                         check=True,timeout=10)
        items=json.loads(p.stdout)['filesystems']
        if len(items)!=1:raise ValueError('unexpected mount result')
        item=items[0]
        target,source,fstype,uuid=(item.get(k) for k in ('target','source','fstype','uuid'))
        if not all(isinstance(x,str) and x for x in (target,source,fstype)):
            raise ValueError('incomplete mount identity')
        if uuid is not None and (not isinstance(uuid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',uuid)):
            raise ValueError('invalid mount UUID')
        return Mount(target,source,fstype,uuid)
    except (OSError,subprocess.SubprocessError,KeyError,TypeError,ValueError) as exc:
        raise RuntimeError('could not verify storage mount') from exc

def same(expected: Mount, current: Mount) -> bool:
    if expected.fstype!=current.fstype:return False
    if expected.uuid is not None and expected.uuid!=current.uuid:return False
    def source_parts(value: str):
        found=re.fullmatch(r'(.*?)\[(/[^\]]*)\]',value)
        return (found.group(1),Path(found.group(2))) if found else (value,Path('/'))
    # A filesystem UUID identifies the volume, not a btrfs subvolume or
    # bind-mounted subtree. Device renaming is allowed with a stable UUID,
    # but replacement of the mounted subtree at the same target is not.
    if expected.target==current.target:
        old_device,old_subtree=source_parts(expected.source)
        new_device,new_subtree=source_parts(current.source)
        return old_subtree==new_subtree and (expected.uuid is not None or old_device==new_device)
    # systemd can create a private bind mount for ReadWritePaths. Accept it only
    # when its source is exactly the same underlying subtree, not merely the
    # same block device or UUID. The per-install marker is checked separately.
    try:
        base=Path(expected.target);target=Path(current.target)
        relative=target.relative_to(base)
        if not base.is_absolute() or not target.is_absolute() or not relative.parts:return False
        old_device,old_subtree=source_parts(expected.source)
        new_device,new_subtree=source_parts(current.source)
        return old_device==new_device and old_subtree.joinpath(relative)==new_subtree
    except (TypeError,ValueError):return False
