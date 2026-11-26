#!/usr/bin/env python3
"""Build a versioned tar.gz containing the verified Git tree and history."""
from __future__ import annotations
import argparse,hashlib,io,json,re,subprocess,tarfile,tempfile,os
from pathlib import Path
from package_handover import git

def write_tar(output: Path,files: dict[str,bytes],version: str):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version):raise ValueError('invalid version')
    prefix=f'sunhill-nvr-{version}/'
    manifest={'version':version,'files':{'app/'+name[4:]:hashlib.sha256(data).hexdigest() for name,data in files.items() if name.startswith('src/sunhill_nvr/')}}
    if not manifest['files']:raise ValueError('missing application')
    files=dict(files);files['release-manifest.json']=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    with tarfile.open(output,'x:gz') as tar:
        for name,data in sorted(files.items()):
            member=tarfile.TarInfo(prefix+name);member.size=len(data);member.mode=0o755 if name.endswith('.sh') else 0o644
            tar.addfile(member,io.BytesIO(data))
    with tarfile.open(output,'r:gz') as tar:
        entries=tar.getmembers()
        if len(entries)!=len(files) or {e.name for e in entries}!={prefix+n for n in files}:raise RuntimeError('tar contents mismatch')
        for name,data in files.items():
            with tar.extractfile(prefix+name) as f:
                if f.read()!=data:raise RuntimeError('tar content mismatch: '+name)

def package(repo: Path,output: Path):
    repo=repo.resolve();output=output.absolute()
    if output.exists() or output.is_symlink() or output.is_relative_to(repo):raise ValueError('invalid output location')
    if git(repo,'branch','--show-current').strip()!=b'main' or git(repo,'status','--porcelain'):raise ValueError('require clean main branch')
    head=git(repo,'rev-parse','HEAD').decode().strip()
    version=re.search(r'^__version__ = "([0-9.]+)"$',git(repo,'show',head+':src/sunhill_nvr/__init__.py').decode(),re.M).group(1)
    if output.name!=f'sunhill-nvr-{version}.tar.gz':raise ValueError('filename must match application version')
    names=git(repo,'ls-files','-z').decode().rstrip('\0').split('\0')
    files={name:git(repo,'show',head+':'+name) for name in names}
    with tempfile.TemporaryDirectory(prefix='.sunhill-release-',dir=output.parent) as directory:
        work=Path(directory);bundle=work/'sunhill-nvr-history.bundle';clone=work/'clone';temp=work/'release.tar.gz'
        git(repo,'bundle','create',str(bundle),'main','HEAD');git(repo,'bundle','verify',str(bundle))
        subprocess.run(['git','clone','--quiet',str(bundle),str(clone)],check=True,capture_output=True)
        if git(clone,'rev-parse','HEAD').decode().strip()!=head or git(clone,'status','--porcelain'):raise RuntimeError('Git bundle mismatch')
        git(clone,'fsck','--full');files[bundle.name]=bundle.read_bytes()
        write_tar(temp,files,version)
        with temp.open('rb') as f:os.fsync(f.fileno())
        os.link(temp,output)
    return head

def require_release_gate(repo: Path, *, candidate: bool, environ=None):
    if candidate:return
    environ=os.environ if environ is None else environ
    head=git(repo,'rev-parse','HEAD').decode().strip()
    if not (environ.get('GITHUB_ACTIONS')=='true' and environ.get('GITHUB_JOB')=='package-verified' and environ.get('GITHUB_SHA')==head):
        raise ValueError('verified release requires successful Ubuntu integration jobs for this commit; use --candidate for a test-only package')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--candidate',action='store_true',help='package an UNVERIFIED test candidate, not an accepted release')
    args=parser.parse_args()
    try:require_release_gate(args.repo,candidate=args.candidate)
    except ValueError as exc:parser.error(str(exc))
    if args.candidate:print('UNVERIFIED TEST CANDIDATE: Ubuntu integration acceptance not established',file=__import__('sys').stderr)
    print(package(args.repo,args.output))
