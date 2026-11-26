"""Download and verify the pinned stream gateway before system changes."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

MAX_SIZE = 32 * 1024 * 1024


class HttpsRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != 'https':
            raise urllib.error.URLError('refusing a non-HTTPS download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, digest: str, target: Path, *, opener=None,
             limit: int = MAX_SIZE, deadline: float = 180.0) -> None:
    if urlsplit(url).scheme != 'https' or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('invalid pinned download')
    if limit <= 0 or deadline <= 0:
        raise ValueError('invalid download limits')
    opener = opener or urllib.request.build_opener(HttpsRedirects())
    end = time.monotonic() + deadline
    created = False
    try:
        with opener.open(url, timeout=15) as response:
            if response.status != 200 or urlsplit(response.geturl()).scheme != 'https':
                raise ValueError('unexpected download response')
            raw_length = response.headers.get('Content-Length')
            if raw_length is not None:
                if not raw_length.isascii() or not raw_length.isdigit() or int(raw_length) > limit:
                    raise ValueError('invalid download size')
            total = 0
            checksum = hashlib.sha256()
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            created = True
            with os.fdopen(fd, 'wb') as output:
                while True:
                    if time.monotonic() > end:
                        raise TimeoutError('download timed out')
                    block = response.read(min(65536, limit - total + 1))
                    if not block:
                        break
                    total += len(block)
                    if total > limit:
                        raise ValueError('download exceeds size limit')
                    checksum.update(block)
                    output.write(block)
                if raw_length is not None and total != int(raw_length):
                    raise ValueError('incomplete download')
                if not hmac.compare_digest(checksum.hexdigest(), digest):
                    raise ValueError('download checksum mismatch')
                output.flush()
                os.fsync(output.fileno())
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise


def copy_verified(source: Path, digest: str, target: Path, *, limit: int = MAX_SIZE) -> None:
    """Stage an offline gateway using the same pinned digest as HTTPS.

    Verify the actual private copy, not the caller's mutable source. Never
    accept symlinks, devices or a partially copied executable.
    """
    if not re.fullmatch(r'[0-9a-f]{64}', digest) or limit <= 0:
        raise ValueError('invalid pinned download')
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    created = False
    try:
        with os.fdopen(fd, 'rb') as original:
            before = os.fstat(original.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
                raise ValueError('invalid offline binary')
            out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            created = True
            with os.fdopen(out, 'wb') as output:
                checksum = hashlib.sha256(); total = 0
                while block := original.read(min(65536, limit - total + 1)):
                    total += len(block)
                    if total > limit:
                        raise ValueError('offline binary exceeds size limit')
                    checksum.update(block); output.write(block)
                after = os.fstat(original.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or total != before.st_size:
                    raise ValueError('offline binary changed while copying')
                if not hmac.compare_digest(checksum.hexdigest(), digest):
                    raise ValueError('offline binary checksum mismatch')
                output.flush(); os.fsync(output.fileno())
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    import sys
    if len(sys.argv) not in (4, 5) or (len(sys.argv) == 5 and sys.argv[1] != '--local'):
        raise SystemExit('usage: python3 -m sunhill_nvr.install_download [--local] URL_OR_FILE SHA256 TARGET')
    try:
        if len(sys.argv) == 5:
            copy_verified(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]))
        else:
            download(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise SystemExit('Cannot download verified go2rtc: ' + str(error)) from error
