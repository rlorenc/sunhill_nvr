import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import subprocess
import hashlib,json,tarfile,os

spec=importlib.util.spec_from_file_location('update_cli',Path(__file__).resolve().parents[1]/'scripts/update_cli.py')
u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)

class UpdateCLI(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'installed';self.root.mkdir()
        (self.root/'releases/0.2.12').mkdir(parents=True);(self.root/'releases/0.2.13').mkdir()
        (self.root/'current').symlink_to('releases/0.2.12')
        (self.root/'updates').mkdir();(self.root/'config').mkdir()
        (self.root/'config/config.json').write_text(json.dumps(dict(hostname='nvr.example',archive='/home/store/kamery',exports='/home/store/exports',staging='/home/store/staging')))
        self.source=self.base/'sunhill-nvr-0.2.13';self.source.mkdir()
        self.archive=self.base/'sunhill-nvr-0.2.13.tar.gz';self.archive.touch()
        self.inbox_trust=patch.object(u,'trusted_inbox');self.inbox_trust.start();self.addCleanup(self.inbox_trust.stop)
        self.trust=patch.object(u,'trusted_package',side_effect=lambda p:p);self.trust.start();self.addCleanup(self.trust.stop)
    def test_rollback_cli_requires_explicit_confirmation(self):
        run=Mock(return_value=subprocess.CompletedProcess([],0))
        with patch.object(u.sys.stdin,'isatty',return_value=False):
            with self.assertRaises(ValueError):u.restore_cli(self.root,['--rollback'],run=run)
        run.assert_not_called()
        self.assertEqual(u.restore_cli(self.root,['--downgrade','1.0.2','--yes'],run=run),0)
        self.assertEqual(run.call_args.args[0][-3:],['rollback','1.0.2','--yes'])
        self.assertEqual(u.restore_cli(self.root,['--rollback-history'],run=run),0)
        self.assertEqual(run.call_args.args[0][-1],'rollback-history')
    def test_sibling_and_explicit(self):
        self.assertEqual(u.discover(self.source,self.root,'0.2.12'),self.archive)
        for explicit in (None,str(self.archive)):
            current=self.root/'current';current.unlink();current.symlink_to('releases/0.2.12')
            def apply(command,**kwargs):
                self.assertEqual(command[-1],str(self.archive));self.assertIn('/recovery/app',kwargs['env']['PYTHONPATH'])
                current.unlink();current.symlink_to('releases/0.2.13');return subprocess.CompletedProcess(command,0)
            lines=[];check=Mock();self.assertEqual(u.update(self.source,self.root,explicit,run=apply,check=check,emit=lines.append),0)
            check.assert_called_once_with(self.root);self.assertEqual(lines[-1],'DONE');self.assertIn('[OK] Storage paths unchanged',lines);self.assertIn('Storage staging: /home/store/staging',lines)
    def test_refresh_recovery_explicit_and_restores_timer(self):
        self.source.rename(self.base/'sunhill-nvr-0.2.12');self.source=self.base/'sunhill-nvr-0.2.12'
        selected=self.base/'sunhill-nvr-0.2.12.tar.gz';selected.touch()
        marker=self.root/'recovery/app/sunhill_nvr/media_layout.py';marker.parent.mkdir(parents=True)
        calls=[]
        def run(command,**kw):
            calls.append(command)
            if 'sunhill_nvr.recovery_migration' in command:marker.touch()
            return subprocess.CompletedProcess(command,0,stdout='active\n')
        with patch.object(u,'extracted_manifest',return_value=(b'{}',{'version':'0.2.12'})):
            self.assertEqual(u.refresh_recovery(self.source,self.root,run=run,emit=lambda x:None),0)
            self.assertEqual(u.refresh_recovery(self.source,self.root,run=run,emit=lambda x:None),0)
        self.assertEqual(len(calls),4);self.assertIn('stop',calls[1]);self.assertEqual(calls[2][-1],str(selected));self.assertIn('start',calls[3])
    def test_refresh_recovery_restores_timer_on_failure(self):
        selected=self.base/'sunhill-nvr-0.2.12.tar.gz';selected.touch();calls=[]
        def run(command,**kw):
            calls.append(command)
            if 'sunhill_nvr.recovery_migration' in command:raise subprocess.CalledProcessError(1,command)
            return subprocess.CompletedProcess(command,0,stdout='active\n')
        with patch.object(u,'extracted_manifest',return_value=(b'{}',{'version':'0.2.12'})):
            with self.assertRaises(subprocess.CalledProcessError):u.refresh_recovery(self.source,self.root,run=run)
        self.assertIn('start',calls[-1])
    @unittest.skipUnless(os.geteuid()==0,'root-owned extracted release required')
    def test_extract_only_stages_exact_package(self):
        self.archive.unlink()
        src=self.source/'src/sunhill_nvr';src.mkdir(parents=True);content=b'__version__ = "0.2.13"\n';(src/'__init__.py').write_bytes(content)
        (self.source/'release-manifest.json').write_text(json.dumps({'version':'0.2.13','files':{'app/sunhill_nvr/__init__.py':hashlib.sha256(content).hexdigest()}}))
        selected=u.discover(self.source,self.root,'0.2.12')
        self.assertEqual(selected,self.root/'updates'/self.archive.name)
        with tarfile.open(selected,'r:gz') as tar:self.assertEqual(tar.extractfile('sunhill-nvr-0.2.13/src/sunhill_nvr/__init__.py').read(),content)
        self.assertEqual(selected.stat().st_mode&0o777,0o600)
    @unittest.skipUnless(os.geteuid()==0,'root-owned extracted release required')
    def test_extract_tampering_rejected(self):
        self.archive.unlink();src=self.source/'src/sunhill_nvr';src.mkdir(parents=True);(src/'__init__.py').write_text('changed')
        (self.source/'release-manifest.json').write_text(json.dumps({'version':'0.2.13','files':{'app/sunhill_nvr/__init__.py':'0'*64}}))
        with self.assertRaisesRegex(ValueError,'Manifest mismatch'):u.discover(self.source,self.root,'0.2.12')
        self.assertEqual(list((self.root/'updates').iterdir()),[])
    def test_missing_and_no_new_release(self):
        self.archive.unlink()
        with self.assertRaisesRegex(ValueError,'archive missing'):u.discover(self.source,self.root,'0.2.12')
        self.assertIsNone(u.discover(self.source,self.root,'0.2.13'))
    def test_inbox_numeric_order(self):
        self.archive.unlink()
        for name in ('0.2.9','0.2.14','0.2.100'):(self.root/'updates'/f'sunhill-nvr-{name}.tar.gz').touch()
        self.assertEqual(u.discover(self.source,self.root,'0.2.12').name,'sunhill-nvr-0.2.100.tar.gz')
    def test_failed_update_no_done(self):
        lines=[];check=Mock();run=Mock(return_value=subprocess.CompletedProcess([],7))
        self.assertEqual(u.update(self.source,self.root,run=run,check=check,emit=lines.append),7)
        self.assertNotIn('DONE',lines);check.assert_not_called()
    def test_changed_storage_path_not_reported_as_success(self):
        def apply(*args,**kw):
            current=self.root/'current';current.unlink();current.symlink_to('releases/0.2.13')
            p=self.root/'config/config.json';cfg=json.loads(p.read_text());cfg['staging']='/unexpected';p.write_text(json.dumps(cfg))
            return subprocess.CompletedProcess([],0)
        lines=[]
        with self.assertRaisesRegex(RuntimeError,'Storage paths changed'):u.update(self.source,self.root,run=apply,check=Mock(),emit=lines.append)
        self.assertNotIn('DONE',lines);self.assertIn('Storage staging: /unexpected',lines)
    def test_postcheck_failure_no_done(self):
        def apply(*args,**kw):
            current=self.root/'current';current.unlink();current.symlink_to('releases/0.2.13');return subprocess.CompletedProcess([],0)
        lines=[]
        with self.assertRaises(RuntimeError):u.update(self.source,self.root,run=apply,check=Mock(side_effect=RuntimeError('redirect')),emit=lines.append)
        self.assertNotIn('DONE',lines);self.assertTrue(any('No automatic rollback' in x for x in lines))
    def test_access_and_reject_redirect(self):
        def probe(ip,port,host):
            if port==8080:return (200,None,'text/html','')
            if host=='nvr.example':return (301,'https://nvr.example/','','')
            return (200,None,'text/html',f"connect-src 'self' ws://{ip};")
        lines=[];ips=lambda:['127.0.0.1','192.168.0.7']
        u.access(self.root,probe=probe,ips=ips,emit=lines.append)
        self.assertTrue(any('http://192.168.0.7' in x for x in lines))
        def bad(ip,port,host):return (302,'https://nvr.example/','','') if host=='192.168.0.7' else probe(ip,port,host)
        with self.assertRaisesRegex(RuntimeError,'HTTP 302'):u.access(self.root,probe=bad,ips=ips,emit=lambda x:None)
    def test_address_filter(self):
        run=Mock(return_value=subprocess.CompletedProcess([],0,'192.168.0.7 8.8.8.8 172.16.1.1 192.168.0.7 ::1',''))
        self.assertEqual(u.addresses(run),['127.0.0.1','172.16.1.1','192.168.0.7'])
