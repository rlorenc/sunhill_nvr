import hashlib,json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
root_dir=Path(__file__).resolve().parents[1]
path.insert(0,str(root_dir/'src'));path.insert(0,str(root_dir/'scripts'))
from resume_release import prepare
from sunhill_nvr import __version__
from sunhill_nvr.units import render,systemd_path
from sunhill_nvr.config import load

@unittest.skipUnless(os.geteuid()==0,'requires root-owned fixtures')
class ResumeReleaseTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);base=Path(self.temp.name)
    self.root=base/'root';self.system=base/'system';self.source=base/'source';self.system.mkdir()
    for d in ('config','control/upgrade','data','releases/0.2.1','recovery/app/sunhill_nvr'):(self.root/d).mkdir(parents=True,exist_ok=True)
    (self.root/'current').symlink_to('releases/0.2.1');(self.root/'recovery/app/sunhill_nvr/__init__.py').write_text('__version__ = "0.2.1"\n')
    cfg={'hostname':'nvr.example.com','database':str(self.root/'data/sunhill.sqlite'),'archive':str(self.root/'media/kamery'),'staging':str(self.root/'media/staging'),'exports':str(self.root/'media/exports'),'archive_lock':str(self.root/'data/archive.lock'),'activity_socket':'/run/sunhill-nvr/activity.sock','control_state':str(self.root/'control'),'timezone':'Europe/Warsaw','minimum_free':0,'free_buffer':0}
    (self.root/'config/config.json').write_text(json.dumps(cfg));self.db=self.root/'data/sunhill.sqlite';self.db.write_bytes(b'original database untouched')
    c=load(self.root/'config/config.json');self.new=render(c);self.old=render(c,legacy_policy=True)
    # Exact state reported by the user: WAL and gateway units already patched.
    self.old['sunhill-go2rtc.service']=self.new['sunhill-go2rtc.service'].replace('ExecCondition=', 'ExecStartPre=',1)
    for n,content in self.old.items():(self.system/n).write_text(content)
    for n,v in [('managed-units.json',list(self.old)),('lifecycle.json',{})]:(self.root/'control/upgrade'/n).write_text(json.dumps(v))
    shutil.copytree(root_dir/'src/sunhill_nvr',self.source/'src/sunhill_nvr',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    files={'app/'+p.relative_to(self.source/'src').as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.source/'src').rglob('*') if p.is_file()}
    (self.source/'release-manifest.json').write_text(json.dumps({'version':__version__,'files':files}));self.calls=[]
 def run_cmd(self,args,**kwargs):
    self.calls.append(args)
    if args[1]=='show':return subprocess.CompletedProcess(args,0,stdout=('activating\ndisabled\n' if args[-1]=='sunhill-go2rtc.service' else 'inactive\ndisabled\n'))
    return subprocess.CompletedProcess(args,0)
 def apply(self):
    with patch('resume_release.trusted_directory'):return prepare(self.root,self.system,self.source,run=self.run_cmd)
 def test_deploys_new_code_and_recovery_before_start_and_can_retry(self):
    self.assertEqual(self.apply(),'nvr.example.com');self.assertEqual(os.readlink(self.root/'current'),'releases/'+__version__)
    self.assertEqual((self.root/'current/app/sunhill_nvr/__init__.py').read_bytes(),(self.source/'src/sunhill_nvr/__init__.py').read_bytes())
    self.assertEqual((self.root/'recovery/app/sunhill_nvr/__init__.py').read_bytes(),(self.root/'current/app/sunhill_nvr/__init__.py').read_bytes())
    for name,content in self.new.items():self.assertEqual((self.system/name).read_text(),content)
    self.assertEqual(self.db.read_bytes(),b'original database untouched');self.assertEqual(len(list(self.root.glob('recovery-resume-*'))),1)
    self.assertTrue(any(a[1:] == ['stop','sunhill-go2rtc.service'] for a in self.calls));self.assertFalse(any(a[1]=='start' for a in self.calls))
    self.assertEqual(self.apply(),'nvr.example.com');self.assertEqual(len(list(self.root.glob('recovery-resume-*'))),1)
 def test_custom_units_and_pending_transactions_rejected(self):
    p=self.system/'sunhill-web.service';old=p.read_text();p.write_text('custom')
    with self.assertRaisesRegex(ValueError,'modified unit'):self.apply()
    p.write_text(old);(self.root/'control/upgrade/transaction.json').touch()
    with self.assertRaisesRegex(ValueError,'pending transaction'):self.apply()
    self.assertEqual(os.readlink(self.root/'current'),'releases/0.2.1');self.assertFalse(self.calls)
 def test_bad_manifest_does_not_switch_release(self):
    (self.source/'src/sunhill_nvr/__init__.py').write_text('bad')
    with self.assertRaisesRegex(ValueError,'manifest does not match'):self.apply()
    self.assertEqual(os.readlink(self.root/'current'),'releases/0.2.1');self.assertEqual(self.db.read_bytes(),b'original database untouched')

 def test_resume_after_025_reset_failure(self):
    # 0.2.5 had already published its code and units, then aborted before
    # starting the controller. All services are disabled, DB is intact.
    previous=self.root/'releases/0.2.5';previous.mkdir()
    (self.root/'current').unlink();(self.root/'current').symlink_to('releases/0.2.5')
    for name,content in self.new.items():
        (self.system/name).write_text(content.replace('ExecCondition=', 'ExecStartPre=',1) if name=='sunhill-go2rtc.service' else content)
    (self.root/'recovery/app/sunhill_nvr/__init__.py').write_text('__version__ = "0.2.5"\n')
    self.assertEqual(self.apply(),'nvr.example.com')
    self.assertEqual(os.readlink(self.root/'current'),'releases/'+__version__)
    self.assertTrue(previous.is_dir());self.assertEqual(self.db.read_bytes(),b'original database untouched')
    self.assertIn('ExecCondition=',(self.system/'sunhill-go2rtc.service').read_text())
    self.assertEqual(self.apply(),'nvr.example.com')

 def test_resume_after_026_controller_validation_failure(self):
    previous=self.root/'releases/0.2.6';previous.mkdir()
    (self.root/'current').unlink();(self.root/'current').symlink_to('releases/0.2.6')
    for name,content in self.new.items():(self.system/name).write_text(content)
    (self.root/'recovery/app/sunhill_nvr/__init__.py').write_text('__version__ = "0.2.6"\n')
    self.assertFalse((self.root/'control/current').exists())
    self.assertEqual(self.apply(),'nvr.example.com')
    self.assertEqual(os.readlink(self.root/'current'),'releases/'+__version__)
    self.assertTrue(previous.is_dir());self.assertEqual(self.db.read_bytes(),b'original database untouched')
    self.assertEqual(self.apply(),'nvr.example.com')

 def test_resume_after_027_missing_transfer_module(self):
    previous=self.root/'releases/0.2.7';previous.mkdir()
    (self.root/'current').unlink();(self.root/'current').symlink_to('releases/0.2.7')
    for name,content in self.new.items():(self.system/name).write_text(content)
    (self.root/'recovery/app/sunhill_nvr/__init__.py').write_text('__version__ = "0.2.7"\n')
    self.assertFalse((self.root/'control/current').exists())
    self.assertEqual(self.apply(),'nvr.example.com')
    self.assertEqual(os.readlink(self.root/'current'),'releases/'+__version__)
    self.assertTrue(previous.is_dir());self.assertEqual(self.db.read_bytes(),b'original database untouched')
    self.assertEqual(self.apply(),'nvr.example.com')

 def test_resume_after_028_ftp_readiness_failure(self):
    previous=self.root/'releases/0.2.8';previous.mkdir()
    (self.root/'current').unlink();(self.root/'current').symlink_to('releases/0.2.8')
    for name,content in self.new.items():(self.system/name).write_text(content)
    (self.root/'recovery/app/sunhill_nvr/__init__.py').write_text('__version__ = "0.2.8"\n')
    self.assertFalse((self.root/'control/current').exists())
    self.assertEqual(self.apply(),'nvr.example.com')
    self.assertEqual(os.readlink(self.root/'current'),'releases/'+__version__)
    self.assertTrue(previous.is_dir());self.assertEqual(self.db.read_bytes(),b'original database untouched')
    self.assertEqual(self.apply(),'nvr.example.com')

 def test_resume_real_028_unit_templates_from_checked_fixture(self):
    import tarfile,types
    fixture=root_dir/'tests/fixtures/sunhill-0.2.8-resume.tar.gz'
    self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),'243aa331cd8ef6f1058e40b23fefe24a0b522720d92677ab5593a5f382eb5a6d')
    with tarfile.open(fixture,'r:gz') as tar:
        module=types.ModuleType('sunhill_nvr._ci_old_units');module.__package__='sunhill_nvr'
        exec(compile(tar.extractfile('previous/src/sunhill_nvr/units.py').read(),'fixture:units.py','exec'),module.__dict__)
    cfg=load(self.root/'config/config.json');historical=module.render(cfg)
    previous=self.root/'releases/0.2.8';previous.mkdir()
    (self.root/'current').unlink();(self.root/'current').symlink_to('releases/0.2.8')
    for name,content in historical.items():(self.system/name).write_text(content)
    (self.root/'control/upgrade/managed-units.json').write_text(json.dumps(sorted(historical)))
    (self.root/'recovery/app/sunhill_nvr/__init__.py').write_text('__version__ = "0.2.8"\n')
    self.assertEqual(self.apply(),'nvr.example.com')
    self.assertEqual(self.db.read_bytes(),b'original database untouched')
    self.assertTrue(previous.is_dir())
    self.assertEqual(os.readlink(self.root/'current'),'releases/'+__version__)
    for name,content in self.new.items():self.assertEqual((self.system/name).read_text(),content)
