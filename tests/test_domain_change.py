import json,os,tempfile,unittest
from pathlib import Path
from sys import path
from types import SimpleNamespace
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import config,domain_change,domain_inbox,tls_apply,tls
from sunhill_nvr.release import ReleaseError
from sunhill_nvr.upgrade_system import SystemAdapter

class DomainChangeTests(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();base=Path(self.tmp.name)
    self.state=base/'state';self.state.mkdir();self.root=base/'tls';self.root.mkdir();(self.root/'versions').mkdir()
    self.inbox=base/'inbox';self.inbox.mkdir(mode=0o700)
    self.proxy=base/'nginx.conf';self.proxy.write_text('previous proxy\n')
    self.conf=base/'config.json'
    self.conf.write_text(json.dumps({'hostname':'nvr.example.com','database':'/var/lib/sunhill/db.sqlite','archive':'/home/store/kamery','staging':'/home/store/staging','exports':'/home/store/exports','archive_lock':'/var/lib/sunhill/archive.lock','activity_socket':'/run/sunhill/activity.sock','control_state':'/opt/sunhill-nvr/control','timezone':'Europe/Warsaw','minimum_free':1,'free_buffer':1}))
    self.commands=[]
    def runner(args,**kwargs):self.commands.append(args);return SimpleNamespace(returncode=0)
    self.adapter=SystemAdapter(self.conf,base/'current',self.state,base/'maintenance',os.geteuid(),os.getegid(),run=runner,nginx_file=self.proxy)
 def tearDown(self):self.tmp.cleanup()
 def test_success_switches_config_proxy_and_services(self):
    with patch.object(domain_change,'_check') as check:
        domain_change.install(self.adapter,self.root,'New.Example.COM')
    self.assertEqual(config.load(self.conf).hostname,'new.example.com')
    self.assertIn('server_name new.example.com;',self.proxy.read_text())
    self.assertIn('X-Sunhill-LAN-Origin http://$host;',self.proxy.read_text())
    self.assertNotIn('return 302',self.proxy.read_text())
    self.assertNotIn('https://nvr.example.com$request_uri',self.proxy.read_text())
    self.assertIn(['/usr/bin/systemctl','restart','sunhill-web.service','sunhill-live-proxy.service'],self.commands)
    check.assert_called_once_with(self.adapter,'new.example.com')
    self.assertIsNone(domain_change._journal(self.adapter))
    self.assertEqual(self.conf.stat().st_mode&0o777,0o640)
 def test_invalid_and_unchanged_names_do_not_touch_config(self):
    for name in ('nvr.example.com','bad; name','http://example.com'):
        with self.subTest(name=name),self.assertRaises(ValueError):domain_change.install(self.adapter,self.root,name)
    self.assertEqual(self.proxy.read_text(),'previous proxy\n')
    self.assertEqual(config.load(self.conf).hostname,'nvr.example.com')
 def test_health_failure_rolls_back_both_configurations(self):
    with patch.object(domain_change,'_check',side_effect=[ReleaseError('bad new host'),None]):
        with self.assertRaises(ReleaseError):domain_change.install(self.adapter,self.root,'new.example.com')
    self.assertEqual(config.load(self.conf).hostname,'nvr.example.com')
    self.assertEqual(self.proxy.read_text(),'previous proxy\n')
    self.assertIsNone(domain_change._journal(self.adapter))
 def test_failed_rollback_keeps_journal_for_recovery(self):
    with patch.object(domain_change,'_check',side_effect=ReleaseError('offline')):
        with self.assertRaises(ReleaseError):domain_change.install(self.adapter,self.root,'new.example.com')
    self.assertEqual(domain_change._journal(self.adapter)['phase'],'switching')
    self.assertTrue(domain_change.recover(self.adapter))
    self.assertEqual(config.load(self.conf).hostname,'nvr.example.com')
    self.assertIsNone(domain_change._journal(self.adapter))
 def test_crash_rolls_back_before_services_start(self):
    with patch.object(domain_change,'_restart',side_effect=KeyboardInterrupt):
        with self.assertRaises(KeyboardInterrupt):domain_change.install(self.adapter,self.root,'new.example.com')
    self.assertEqual(domain_change._journal(self.adapter)['phase'],'switching')
    self.assertTrue(domain_change.recover(self.adapter))
    self.assertEqual(config.load(self.conf).hostname,'nvr.example.com')
    self.assertEqual(self.proxy.read_text(),'previous proxy\n')
 def test_committed_change_survives_interruption(self):
    with patch.object(domain_change,'_check'),patch.object(domain_change,'_clean',side_effect=KeyboardInterrupt):
        with self.assertRaises(KeyboardInterrupt):domain_change.install(self.adapter,self.root,'new.example.com')
    self.assertEqual(domain_change._journal(self.adapter)['phase'],'committed')
    self.assertTrue(domain_change.recover(self.adapter))
    self.assertEqual(config.load(self.conf).hostname,'new.example.com')
 def test_error_after_committed_journal_keeps_new_configuration_for_recovery(self):
    real_save=domain_change._save
    def save_then_fail(adapter,journal):
        real_save(adapter,journal)
        if journal['phase']=='committed':raise OSError('simulated sync error')
    with patch.object(domain_change,'_check'),patch.object(domain_change,'_save',side_effect=save_then_fail):
        with self.assertRaises(ReleaseError):domain_change.install(self.adapter,self.root,'new.example.com')
    self.assertEqual(domain_change._journal(self.adapter)['phase'],'committed')
    self.assertEqual(config.load(self.conf).hostname,'new.example.com')
    self.assertTrue(domain_change.recover(self.adapter))
    self.assertEqual(config.load(self.conf).hostname,'new.example.com')
 def test_existing_tls_must_match_new_domain_before_any_change(self):
    version='versions/'+'a'*32;directory=self.root/version;directory.mkdir()
    (directory/'fullchain.pem').write_text('cert');(directory/'key.pem').write_text('key')
    tls_apply._set_current(self.root,version)
    def reject(cert,key,hostname):self.assertEqual(hostname,'new.example.com');raise ValueError('certificate mismatch')
    with self.assertRaises(ValueError):domain_change.install(self.adapter,self.root,'new.example.com',verify=reject)
    self.assertEqual(config.load(self.conf).hostname,'nvr.example.com')
    self.assertEqual(self.proxy.read_text(),'previous proxy\n')
    self.assertIsNone(domain_change._journal(self.adapter))
 def test_local_ca_dual_hostname_allows_domain_change(self):
    with tempfile.TemporaryDirectory() as d:
        cert,key,ca=tls.generate_local('nvr.example.com',Path(d),next_hostname='new.example.com')
        tls_apply.install(self.adapter,self.root,cert.read_bytes(),key.read_bytes(),local_ca=ca.read_bytes())
        with patch.object(domain_change,'_check'):
            domain_change.install(self.adapter,self.root,'new.example.com')
        self.assertEqual(config.load(self.conf).hostname,'new.example.com')
        with self.assertRaises(tls.TLSError):domain_change.install(self.adapter,self.root,'other.example.com')
        self.assertEqual(config.load(self.conf).hostname,'new.example.com')
 def test_private_inbox_is_processed_once(self):
    domain_inbox.submit(self.inbox,'New.Example.com',1)
    self.assertEqual((self.inbox/'domain-request.json').stat().st_mode&0o777,0o600)
    with self.assertRaises(FileExistsError):domain_inbox.submit(self.inbox,'other.example.com',1)
    def verify_consumed(adapter,hostname):self.assertFalse((self.inbox/'domain-request.json').exists())
    with patch.object(domain_change,'_check',side_effect=verify_consumed):
        self.assertTrue(domain_change.process(self.adapter,self.inbox,self.root))
        self.assertFalse(domain_change.process(self.adapter,self.inbox,self.root))
    self.assertEqual(config.load(self.conf).hostname,'new.example.com')
    self.assertEqual(json.loads((self.state/'domain-status.json').read_text())['state'],'done')
 def test_health_check_uses_new_hostname_and_loopback(self):
    calls=[]
    class Response:
        status=200;headers={'Content-Type':'text/html; charset=utf-8'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
    class Opener:
        def open(self,request,timeout):calls.append((request.full_url,request.get_header('Host'),timeout));return Response()
    with patch('sunhill_nvr.domain_change.urllib.request.build_opener',return_value=Opener()):
        domain_change._check(self.adapter,'new.example.com')
    self.assertEqual(calls,[('http://127.0.0.1:8080/','new.example.com',5)])
    self.assertTrue(any(command[-1]=='sunhill-live-proxy.service' for command in self.commands))
 def test_abandoned_request_status_is_reconciled(self):
    domain_change.status(self.adapter,'applying','working')
    self.assertTrue(domain_change.process(self.adapter,self.inbox,self.root))
    self.assertEqual(json.loads((self.state/'domain-status.json').read_text())['state'],'failed')
    self.assertFalse(domain_change.process(self.adapter,self.inbox,self.root))

if __name__=='__main__':unittest.main()
