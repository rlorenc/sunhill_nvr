"""Generate an encrypted release-signing key on a trusted workstation."""
from __future__ import annotations
import argparse
import getpass
import os
import subprocess
import tempfile
from pathlib import Path

def generate(private: Path, public: Path, passphrase: str, *, openssl: str='openssl') -> None:
    if not private.is_absolute() or not public.is_absolute() or private==public:raise ValueError('absolute separate paths required')
    if (private.parent.is_symlink() or public.parent.is_symlink() or not private.parent.is_dir() or not public.parent.is_dir()):
        raise ValueError('invalid key directory')
    info=private.parent.stat()
    if info.st_uid!=os.geteuid() or info.st_mode&0o077:raise ValueError('private key directory must be private')
    project=Path(__file__).resolve().parents[2]
    if private.resolve(strict=False).is_relative_to(project):raise ValueError('private key must be outside the project')
    if private.exists() or private.is_symlink() or public.exists() or public.is_symlink():raise FileExistsError('key output already exists')
    if (not isinstance(passphrase,str) or len(passphrase)<12 or len(passphrase.encode('utf-8'))>1024 or
        any(c in passphrase for c in '\r\n\0')):raise ValueError('invalid key passphrase')
    with tempfile.TemporaryDirectory(prefix='.sunhill-key-',dir=private.parent) as name:
        root=Path(name);secret=root/'private.pem';pub=root/'public.pem'
        try:
            subprocess.run([openssl,'genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072',
                            '-aes-256-cbc','-pass','stdin','-out',str(secret)],input=(passphrase+'\n').encode(),
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120,check=True)
            os.chmod(secret,0o600)
            subprocess.run([openssl,'pkey','-in',str(secret),'-passin','stdin','-pubout','-out',str(pub)],
                           input=(passphrase+'\n').encode(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,check=True)
            os.chmod(pub,0o644)
            for file in (secret,pub):
                with file.open('rb') as item:os.fsync(item.fileno())
            # Public and private outputs may be on different filesystems.
            fd,name=tempfile.mkstemp(prefix='.sunhill-public-',dir=public.parent)
            staged=Path(name)
            try:
                with os.fdopen(fd,'wb') as output:
                    output.write(pub.read_bytes());output.flush();os.fsync(output.fileno())
                os.chmod(staged,0o644)
                os.link(secret,private)
                try:os.link(staged,public)
                except BaseException:
                    private.unlink(missing_ok=True);raise
            finally:staged.unlink(missing_ok=True)
            for directory in {private.parent,public.parent}:
                fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try:os.fsync(fd)
                finally:os.close(fd)
        except (OSError,subprocess.SubprocessError) as exc:
            raise RuntimeError('key generation failed') from exc

def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description='Generate encrypted Sunhill release signing keys')
    parser.add_argument('--private-key',type=Path,required=True);parser.add_argument('--public-key',type=Path,required=True)
    args=parser.parse_args(argv)
    password=getpass.getpass('Hasło klucza podpisującego: ')
    if password!=getpass.getpass('Powtórz hasło: '):raise ValueError('passphrases do not match')
    generate(args.private_key,args.public_key,password);return 0

if __name__=='__main__':raise SystemExit(main())
