import json,os,tempfile,unittest
from pathlib import Path
from sys import path
from types import SimpleNamespace
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import tls_apply,tls_inbox,tls
from sunhill_nvr.upgrade_system import SystemAdapter

class TLSApplyTests(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();base=Path(self.tmp.name)
    self.state=base/'state';self.state.mkdir();self.root=base/'tls';self.root.mkdir();(self.root/'versions').mkdir()
    self.proxy=base/'nginx.conf';self.proxy.write_text('old proxy\n');self.conf=base/'config.json'
    self.conf.write_text(json.dumps({'hostname':'nvr.example.com','database':'/var/lib/sunhill/db.sqlite','archive':'/home/store/kamery','staging':'/home/store/staging','exports':'/home/store/exports','archive_lock':'/var/lib/sunhill/archive.lock','activity_socket':'/run/sunhill/activity.sock','control_state':'/opt/sunhill-nvr/control','timezone':'Europe/Warsaw','minimum_free':1,'free_buffer':1}))
    self.commands=[]
    def runner(args,**kwargs):self.commands.append(args);return SimpleNamespace(returncode=0)
    self.adapter=SystemAdapter(self.conf,base/'current',self.state,base/'maintenance',os.getuid(),os.getgid(),run=runner,nginx_file=self.proxy)
 def tearDown(self):self.tmp.cleanup()
 def verify(self,cert,key,host):
    self.assertEqual(cert.read_bytes(),b'certificate');self.assertEqual(key.read_bytes(),b'private-key');self.assertEqual(host,'nvr.example.com')
 def test_install_and_rotation_use_an_atomic_version_pointer(self):
    tls_apply.install(self.adapter,self.root,b'certificate',b'private-key',verify=self.verify)
    first=tls_apply._current(self.root);self.assertIsNotNone(first)
    self.assertEqual((self.root/'current'/'key.pem').read_bytes(),b'private-key')
    self.assertEqual((self.root/first/'key.pem').stat().st_mode&0o777,0o600)
    self.assertIn('listen 443 ssl;',self.proxy.read_text())
    self.assertIn('X-Sunhill-LAN-Origin http://$host;',self.proxy.read_text())
    self.assertNotIn('return 302',self.proxy.read_text())
    tls_apply.install(self.adapter,self.root,b'certificate',b'private-key',verify=self.verify)
    self.assertNotEqual(first,tls_apply._current(self.root))
    self.assertIsNone(tls_apply._read_journal(self.state))
 def test_local_ca_can_be_installed_and_rollback_retains_trust(self):
    with tempfile.TemporaryDirectory() as d:
        cert,key,ca=tls.generate_local('nvr.example.com',Path(d))
        tls_apply.install(self.adapter,self.root,cert.read_bytes(),key.read_bytes(),local_ca=ca.read_bytes())
        first=tls_apply._current(self.root)
        self.assertEqual((self.root/'current/local-ca.pem').read_bytes(),ca.read_bytes())
        with self.assertRaises(tls.TLSError):
            tls_apply.install(self.adapter,self.root,cert.read_bytes(),key.read_bytes())
        self.assertEqual(tls_apply._current(self.root),first)
        self.assertEqual((self.root/'current/local-ca.pem').read_bytes(),ca.read_bytes())
 def test_invalid_certificate_does_not_change_proxy_or_pointer(self):
    def reject(*args):raise ValueError('bad certificate')
    with self.assertRaises(ValueError):tls_apply.install(self.adapter,self.root,b'x',b'y',verify=reject)
    self.assertEqual(self.proxy.read_text(),'old proxy\n');self.assertIsNone(tls_apply._current(self.root))
    self.assertEqual(list((self.root/'versions').iterdir()),[])
 def test_failure_restores_previous_proxy_and_certificate(self):
    tls_apply.install(self.adapter,self.root,b'certificate',b'private-key',verify=self.verify)
    first=tls_apply._current(self.root);original=self.proxy.read_bytes();commands=[]
    def runner(args,**kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=1 if args[-2:]==['reload','nginx.service'] and sum(x[-2:]==['reload','nginx.service'] for x in commands)==1 else 0)
    adapter=SystemAdapter(self.conf,self.adapter.current,self.state,self.adapter.maintenance,os.getuid(),os.getgid(),run=runner,nginx_file=self.proxy)
    with self.assertRaises(Exception):tls_apply.install(adapter,self.root,b'certificate',b'private-key',verify=self.verify)
    self.assertEqual(tls_apply._current(self.root),first);self.assertEqual(self.proxy.read_bytes(),original)
    self.assertIsNone(tls_apply._read_journal(self.state))
 def test_crash_recovers_before_nginx_start(self):
    def crash(args,**kwargs):raise KeyboardInterrupt()
    adapter=SystemAdapter(self.conf,self.adapter.current,self.state,self.adapter.maintenance,os.getuid(),os.getgid(),run=crash,nginx_file=self.proxy)
    with self.assertRaises(KeyboardInterrupt):tls_apply.install(adapter,self.root,b'certificate',b'private-key',verify=self.verify)
    self.assertIsNotNone(tls_apply._read_journal(self.state))
    self.assertTrue(tls_apply.recover(self.adapter,self.root))
    self.assertEqual(self.proxy.read_text(),'old proxy\n');self.assertIsNone(tls_apply._current(self.root))
    self.assertIsNone(tls_apply._read_journal(self.state))
 def test_inbox_is_private_and_only_one_request_can_be_queued(self):
    inbox=Path(self.tmp.name)/'inbox';inbox.mkdir(mode=0o700)
    tls_inbox.submit(inbox,'certificate','private-key',1)
    self.assertEqual((inbox/'tls-request.json').stat().st_mode&0o777,0o600)
    with self.assertRaises(FileExistsError):tls_inbox.submit(inbox,'certificate','private-key',1)
    self.assertNotIn('private-key',self.proxy.read_text())
 def test_admin_request_installs_once_and_removes_private_input(self):
    inbox=Path(self.tmp.name)/'inbox';inbox.mkdir(mode=0o700)
    tls_inbox.submit(inbox,'certificate','private-key',1)
    install=tls_apply.install
    def run_install(adapter,root,certificate,key):return install(adapter,root,certificate,key,verify=self.verify)
    # Status ownership is root:service on Ubuntu. This unprivileged test uses
    # the test user's ownership while still exercising the full request flow.
    with patch.object(tls_apply,'install',side_effect=run_install),patch.object(tls_apply.os,'chown'):
        self.assertTrue(tls_apply.process(self.adapter,inbox,self.root))
        self.assertFalse(tls_apply.process(self.adapter,inbox,self.root))
    self.assertFalse((inbox/'tls-request.json').exists())
    self.assertEqual(json.loads((self.state/'tls-status.json').read_text())['state'],'done')
    self.assertEqual((self.root/'current'/'key.pem').read_text(),'private-key')

if __name__=='__main__':unittest.main()
