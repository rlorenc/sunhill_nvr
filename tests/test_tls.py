import os,subprocess,tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.tls import validate,TLSError,generate_local
class TLSTests(unittest.TestCase):
 def test_certificate_hostname_key_and_trust(self):
    with tempfile.TemporaryDirectory() as t:
        d=Path(t);cert=d/'cert.pem';key=d/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','2','-subj','/CN=nvr.example.com','-addext','subjectAltName=DNS:nvr.example.com'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        validate(cert,key,'nvr.example.com',ca_bundle=cert,openssl='openssl')
        with self.assertRaises(TLSError):validate(cert,key,'other.example.com',ca_bundle=cert,openssl='openssl')
 def test_symlink_input_rejected(self):
    with tempfile.TemporaryDirectory() as t:
        d=Path(t);f=d/'file';f.write_text('x');link=d/'link';link.symlink_to(f)
        with self.assertRaises(TLSError):validate(link,f,'nvr.example.com',ca_bundle=f,openssl='openssl')
 def test_local_ca_generation_has_separate_key_and_explicit_trust(self):
    with tempfile.TemporaryDirectory() as t:
        d=Path(t);cert,key,ca=generate_local('nvr.example.com',d,next_hostname='new.example.com')
        self.assertEqual({p.name for p in d.iterdir()},{'fullchain.pem','key.pem','local-ca.pem'})
        self.assertEqual(key.stat().st_mode & 0o777,0o600)
        validate(cert,key,'nvr.example.com',ca_bundle=ca)
        validate(cert,key,'new.example.com',ca_bundle=ca)
        with self.assertRaises(TLSError):validate(cert,key,'other.example.com',ca_bundle=ca)
        with self.assertRaises(TLSError):validate(cert,key,'nvr.example.com')
        with self.assertRaises(TLSError):generate_local('nvr.example.com',d)
        self.assertIn('CA:FALSE',subprocess.check_output(['openssl','x509','-in',str(cert),'-noout','-text'],text=True))
 def test_rejects_nonprivate_directory_and_invalid_next_host(self):
    with tempfile.TemporaryDirectory() as t:
        d=Path(t)
        with self.assertRaises(ValueError):generate_local('nvr.example.com',d,next_hostname='bad,name')
        os.chmod(d,0o755)
        with self.assertRaises(TLSError):generate_local('nvr.example.com',d)
if __name__=='__main__':unittest.main()
