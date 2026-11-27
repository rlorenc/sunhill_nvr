"""Explicit offline model provisioning. Never download or trust camera URLs."""
from __future__ import annotations
import contextlib
import fcntl
import hashlib
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT=Path('/opt/sunhill-nvr/config/models')
FILES={'coco':'coco-yolov8.onnx','face-detector':'face-yunet.onnx','face-recognizer':'face-sface.onnx'}
MAX_BYTES=256*1024*1024


def trusted_directory(path):
    for p in (path,*path.parents):
        s=p.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:
            raise ValueError('untrusted model directory')


def trusted_model(path):
    trusted_directory(path.parent)
    s=path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022 or s.st_nlink!=1 or not 0<s.st_size<=MAX_BYTES:
        raise ValueError('untrusted ONNX model')
    return path


def prepare_directory(root):
    # Explicitly set the mode of a newly created directory even under umask 077.
    trusted_directory(root.parent)
    try:root.mkdir(mode=0o755)
    except FileExistsError:pass
    else:root.chmod(0o755)
    trusted_directory(root)


def generation(root=ROOT):
    """Cheap file identity for reloading optional models after atomic replacement."""
    values=[]
    for name in FILES.values():
        try:
            path=trusted_model(root/name);info=path.stat()
            values.append((info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns))
        except (OSError,ValueError):values.append(None)
    return tuple(values)


@contextlib.contextmanager
def provisioning_lock(root=ROOT,*,create=True,nonblocking=False):
    """Serialize local enrollment and model replacements across CLI processes."""
    if create:prepare_directory(root)
    else:trusted_directory(root)
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|(fcntl.LOCK_NB if nonblocking else 0))
        yield
    finally:os.close(fd)


def status(*,root=ROOT,verify=False):
    """No model bytes, source paths, or feature vectors in the web response.

    A present/trusted file is not proof of successful inference. The expensive
    optional check is for the local operator, never automatic on HTTP GET.
    """
    result={}
    for kind,name in FILES.items():
        path=root/name
        try:
            trusted_model(path)
            result[kind]={'state':'present','bytes':path.stat().st_size}
        except FileNotFoundError:
            result[kind]={'state':'missing'}
        except (OSError,ValueError):
            result[kind]={'state':'untrusted'}
        if verify and result[kind]['state']=='present':
            try:verify_network(kind,path)
            except Exception:result[kind]['inference']='failed'
            else:result[kind]['inference']='passed'
    return result


def verify_network(kind,path):
    import cv2 as cv
    import numpy as np
    if kind=='coco':
        from .local_objects import CocoDetector
        detector=CocoDetector(cv,np,path)
        detector.detect(np.zeros((540,960,3),np.uint8))
    elif kind=='face-detector':
        detector=cv.FaceDetectorYN.create(str(path),'',(320,320),.9,.3,500)
        detector.detect(np.zeros((320,320,3),np.uint8))
    elif kind=='face-recognizer':
        recognizer=cv.FaceRecognizerSF.create(str(path),'')
        feature=recognizer.feature(np.zeros((112,112,3),np.uint8))
        if np.asarray(feature).size!=128 or not np.isfinite(feature).all():raise ValueError('unexpected SFace output')
    else:raise ValueError('unknown model')


def install(kind,source,sha256,*,root=ROOT,verify=verify_network):
    """Verify a vendor-pinned file and perform an atomic root-only install.

    The caller must independently trust the origin and license. A checksum
    supplied alongside a malicious model does not make that model trustworthy.
    """
    if os.geteuid()!=0 or kind not in FILES or not re.fullmatch(r'[0-9a-fA-F]{64}',sha256):raise ValueError('root and SHA-256 required')
    prepare_directory(root)
    dest=root/FILES[kind]
    if dest.exists() or dest.is_symlink():trusted_model(dest)
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    name=None
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=MAX_BYTES:raise ValueError('invalid model file')
        with os.fdopen(fd,'rb',closefd=False) as incoming, tempfile.NamedTemporaryFile(dir=root,prefix='.model-',suffix='.onnx',delete=False) as outgoing:
            name=Path(outgoing.name);digest=hashlib.sha256();size=0
            while block:=incoming.read(1024*1024):
                size+=len(block)
                if size>MAX_BYTES:raise ValueError('model too large')
                digest.update(block);outgoing.write(block)
            if size!=info.st_size or digest.hexdigest()!=sha256.lower():raise ValueError('model checksum mismatch')
            outgoing.flush();os.fsync(outgoing.fileno())
        verify(kind,name)
        name.chmod(0o644);os.replace(name,dest);name=None
        dirfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
        return dest
    finally:
        os.close(fd)
        if name is not None:name.unlink(missing_ok=True)


# OpenCV Zoo Git LFS pointers at the immutable upstream revision below.
FACE_REVISION='48b10fa505aeb51fbd127fc60a5c660040e823b7'
FACE_SOURCES={
    'face-detector':('models/face_detection_yunet/face_detection_yunet_2023mar.onnx',232589,'8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'),
    'face-recognizer':('models/face_recognition_sface/face_recognition_sface_2021dec.onnx',38696353,'0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'),
}


def _checksum(path,expected_size,expected_hash):
    if path.stat().st_size!=expected_size:raise ValueError('model size mismatch')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=expected_hash:raise ValueError('model checksum mismatch')


def _download(url,path,size):
    subprocess.run(['/usr/bin/curl','--fail','--silent','--show-error','--location','--max-redirs','3',
        '--proto','=https','--proto-redir','=https','--connect-timeout','15','--max-time','180',
        '--max-filesize',str(size),'--output',str(path),url],check=True,timeout=190)


def fetch_face_models(*,accept_licenses,replace=False,root=ROOT,download=_download,verify=verify_network):
    """Root-only opt-in download of two pinned official models. Caller holds
    provisioning_lock and confirms there are no enrolled profiles. No automatic
    fetch at boot; no arbitrary URL or response-derived checksum is accepted.
    """
    if os.geteuid()!=0 or accept_licenses is not True:raise ValueError('root and license acceptance required')
    prepare_directory(root)
    planned={}
    for kind,(remote,size,sha) in FACE_SOURCES.items():
        dest=root/FILES[kind]
        if dest.exists() or dest.is_symlink():
            trusted_model(dest)
            try:_checksum(dest,size,sha)
            except ValueError:
                if not replace:raise ValueError('different local model; explicit --replace required')
            else:
                verify(kind,dest);continue
        planned[kind]=(remote,size,sha)
    # Validate every new file before changing any installed file. Each final
    # rename is atomic; the pair is not a single filesystem transaction.
    with tempfile.TemporaryDirectory(prefix='.face-fetch-',dir=root) as work:
        for kind,(remote,size,sha) in planned.items():
            path=Path(work)/FILES[kind]
            download('https://media.githubusercontent.com/media/opencv/opencv_zoo/'+FACE_REVISION+'/'+remote,path,size)
            _checksum(path,size,sha);verify(kind,path)
        for kind,(_,_,sha) in planned.items():
            install(kind,Path(work)/FILES[kind],sha,root=root,verify=verify)
    return {kind:root/FILES[kind] for kind in FACE_SOURCES}
