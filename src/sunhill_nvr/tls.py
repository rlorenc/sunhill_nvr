"""Validate a LAN TLS certificate and private key before configuring nginx."""
from __future__ import annotations
import hmac
import os
import tempfile
import subprocess
from pathlib import Path
from .nginx_config import HOST

class TLSError(RuntimeError):pass

def _run(arguments: list[str], *, data: bytes | None = None) -> bytes:
    try:
        result=subprocess.run(arguments,input=data,stdin=subprocess.DEVNULL if data is None else None,
                              stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=20,check=True)
        return result.stdout
    except (OSError,subprocess.SubprocessError) as exc:raise TLSError('certificate verification failed') from exc

def validate(certificate: Path, key: Path, hostname: str, *, ca_bundle: Path = Path('/etc/ssl/certs/ca-certificates.crt'), openssl: str = '/usr/bin/openssl') -> None:
    if not isinstance(hostname,str) or not HOST.fullmatch(hostname):raise ValueError('invalid hostname')
    for file in (certificate,key,ca_bundle):
        if file.is_symlink() or not file.is_file() or file.stat().st_size>2_000_000:
            raise TLSError('invalid certificate input')
    _run([openssl,'x509','-in',str(certificate),'-noout','-checkhost',hostname])
    _run([openssl,'x509','-in',str(certificate),'-noout','-checkend','86400'])
    cert_pub=_run([openssl,'x509','-in',str(certificate),'-pubkey','-noout'])
    cert_der=_run([openssl,'pkey','-pubin','-outform','DER'],data=cert_pub)
    key_der=_run([openssl,'pkey','-in',str(key),'-passin','pass:','-pubout','-outform','DER'])
    if not hmac.compare_digest(cert_der,key_der):raise TLSError('certificate and private key do not match')
    _run([openssl,'verify','-purpose','sslserver','-verify_hostname',hostname,'-no-CApath','-no-CAstore','-CAfile',str(ca_bundle),'-untrusted',str(certificate),str(certificate)])


def generate_local(hostname: str, directory: Path, *, next_hostname: str | None = None) -> tuple[Path, Path, Path]:
    """Create a per-installation CA and server leaf in an empty private directory.

    The CA signing key is discarded. Trust the public CA on clients explicitly;
    the server's private key cannot be used to issue additional certificates.
    """
    if not isinstance(hostname, str) or not HOST.fullmatch(hostname):
        raise ValueError('invalid hostname')
    if next_hostname is not None and (not isinstance(next_hostname,str) or not HOST.fullmatch(next_hostname)):
        raise ValueError('invalid next hostname')
    names = sorted({hostname.lower(), next_hostname.lower()} if next_hostname else {hostname.lower()})
    if directory.is_symlink() or not directory.is_dir() or directory.stat().st_mode & 0o077:
        raise TLSError('local TLS directory must be private')
    cert, key, ca = (directory / name for name in ('fullchain.pem', 'key.pem', 'local-ca.pem'))
    if any(p.exists() or p.is_symlink() for p in (cert, key, ca)):
        raise TLSError('local TLS files already exist')
    with tempfile.TemporaryDirectory(prefix='.ca-', dir=directory) as name:
        root = Path(name)
        ca_key, csr, leaf, extensions = (root / n for n in ('ca.key', 'leaf.csr', 'leaf.pem', 'extensions.cnf'))
        _run(['/usr/bin/openssl','req','-x509','-newkey','rsa:3072','-nodes','-days','3650',
              '-subj','/CN=Sunhill NVR local CA','-addext','basicConstraints=critical,CA:TRUE,pathlen:0',
              '-addext','keyUsage=critical,keyCertSign,cRLSign','-keyout',str(ca_key),'-out',str(root/'ca.pem')])
        _run(['/usr/bin/openssl','req','-new','-newkey','rsa:2048','-nodes','-subj','/CN=Sunhill NVR',
              '-keyout',str(root/'leaf.key'),'-out',str(csr)])
        extensions.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName='+','.join('DNS:'+name for name in names)+'\n', encoding='ascii')
        _run(['/usr/bin/openssl','x509','-req','-in',str(csr),'-CA',str(root/'ca.pem'),'-CAkey',str(ca_key),
              '-CAcreateserial','-days','397','-sha256','-extfile',str(extensions),'-out',str(leaf)])
        for host in names:validate(leaf,root/'leaf.key',host,ca_bundle=root/'ca.pem')
        created=[]
        try:
            for target,source,mode in ((cert,leaf,0o644),(key,root/'leaf.key',0o600),(ca,root/'ca.pem',0o644)):
                fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
                created.append(target)
                with os.fdopen(fd,'wb') as output:
                    output.write(source.read_bytes());output.flush();os.fsync(output.fileno())
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)
        except BaseException:
            for target in created:target.unlink(missing_ok=True)
            raise
    return cert,key,ca
