"""Linux file-activity detector used by retention and archive workers."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

def file_in_use(path: Path, *, fuser: str = '/usr/bin/fuser', timeout: int = 10) -> bool:
    """Detect open file handles. Fail closed if system visibility is insufficient.

    Requires a local Linux filesystem, root visibility of /proc, and fuser.
    The installer must reject hidepid configurations that hide processes from root.
    This check is advisory: new writers must also be coordinated by the service.
    """
    if not sys.platform.startswith('linux') or os.geteuid()!=0 or not Path(fuser).is_file():
        raise RuntimeError('file activity cannot be inspected safely')
    try:
        proc=subprocess.run([fuser,'--silent','--',str(path)],stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=timeout)
    except (OSError,subprocess.SubprocessError) as exc:
        raise RuntimeError('file activity check failed') from exc
    if proc.stderr.strip() or proc.returncode not in (0,1):
        raise RuntimeError('file activity check was inconclusive')
    return proc.returncode==0
