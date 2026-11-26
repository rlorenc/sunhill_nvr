"""Offline recovery contract migration, real signed tree and atomic disk changes."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import recovery_units as units
from sunhill_nvr.build_release import build
from sunhill_nvr.release import ReleaseError
from sunhill_nvr.upgrade_system import SystemAdapter


@unittest.skipUnless(os.geteuid()==0,'root-owned unit and recovery fixtures required')
class RecoveryUnitsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.state=self.root/'state';self.state.mkdir()
        self.versions=self.root/'releases';(self.versions/'1.0.0').mkdir(parents=True)
        self.current=self.root/'current';self.current.symlink_to('releases/1.0.0')
        self.recovery=self.root/'recovery';(self.recovery/'app').mkdir(parents=True)
        (self.recovery/'app/old.py').write_text('old')
        self.system=self.root/'units';self.system.mkdir()
        for name in units.PROTECTED:(self.system/name).write_bytes(b'old '+name.encode())
        (self.state/'managed-units.json').write_text(json.dumps(sorted(units.PROTECTED)))
        self.default='rescue.target';self.state_name='maintenance';self.active=set();self.calls=[]
        self.adapter=SystemAdapter(self.root/'config',self.current,self.state,self.root/'maintenance',1000,1000,
                                   run=self.run_command,system_dir=self.system,tls_certificate=self.root/'cert')
        source=self.root/'src/sunhill_nvr';source.mkdir(parents=True);(source/'__init__.py').write_text('VERSION=1\n')
        self.key=self.root/'private';self.public=self.root/'public';self.package=self.root/'signed.zip';self.signature=self.root/'signed.sig'
        subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(self.key)],check=True,capture_output=True)
        subprocess.run(['openssl','pkey','-in',str(self.key),'-pubout','-out',str(self.public)],check=True,capture_output=True)
        self.source=source.parent;self.make_package('1.0.0')

    def make_package(self,version):
        self.package.unlink(missing_ok=True);self.signature.unlink(missing_ok=True)
        build(self.source,version,self.key,self.package,self.signature)

    def run_command(self,args,**kwargs):
        self.calls.append(args)
        if args[:2]==['/usr/bin/systemctl','get-default']:
            return subprocess.CompletedProcess(args,0,stdout=(self.default+'\n').encode())
        if args[:2]==['/usr/bin/systemctl','is-system-running']:
            return subprocess.CompletedProcess(args,1,stdout=(self.state_name+'\n').encode())
        if args[:2]==['/usr/bin/systemctl','show']:
            return subprocess.CompletedProcess(args,0,stdout=b'active\n' if args[-1] in self.active else b'inactive\n')
        if 'sunhill_nvr.deployment_cli' in args:
            output=Path(args[args.index('--output')+1])/'units';output.mkdir()
            for name in units.PROTECTED:(output/name).write_bytes(b'new '+name.encode())
        return subprocess.CompletedProcess(args,0,stdout=b'')

    def migrate(self,**kwargs):
        return units.migrate_units(self.adapter,self.root,self.versions,self.current,self.public,self.package,self.signature,**kwargs)

    def assert_contract(self,side):
        for name in units.PROTECTED:self.assertEqual((self.system/name).read_bytes(),side+b' '+name.encode())
        self.assertEqual((self.recovery/'app/old.py').exists(),side==b'old')
        self.assertEqual(self.current.resolve(),self.versions/'1.0.0')

    def test_success_and_console_rollback_preserve_current_release(self):
        previous=self.migrate();self.assert_contract(b'new');self.assertTrue((previous/'app/old.py').exists())
        self.assertEqual(units._read(self.adapter)['phase'],'applied');units.require_ready(self.adapter)
        self.assertFalse(any(c[:2] in (['/usr/bin/systemctl','start'],['/usr/bin/systemctl','set-default']) for c in self.calls))
        units.rollback_units(self.adapter,self.root,self.versions,self.current);self.assert_contract(b'old')
        self.assertEqual(units._read(self.adapter)['phase'],'rolled_back')
        receipt=units.archive_journal(self.adapter);self.assertTrue(receipt.is_dir());self.assertFalse((self.state/units.JOURNAL).exists())

    def test_power_loss_between_unit_writes_and_exchange_can_be_rolled_back(self):
        replace=SystemAdapter._replace
        calls=[]
        def interrupted(path,data):
            replace(path,data);calls.append(path)
            if len(calls)==1:raise KeyboardInterrupt('power loss')
        with patch.object(SystemAdapter,'_replace',side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):self.migrate()
        self.assertEqual(units._read(self.adapter)['phase'],'prepared')
        with self.assertRaises(ReleaseError):units.require_ready(self.adapter)
        with self.assertRaises(ReleaseError):units.archive_journal(self.adapter)
        units.rollback_units(self.adapter,self.root,self.versions,self.current);self.assert_contract(b'old')
        # Repeating rollback is safe and does not toggle the trees again.
        units.rollback_units(self.adapter,self.root,self.versions,self.current);self.assert_contract(b'old')

    def test_power_loss_after_tree_exchange_restores_old_tree(self):
        exchange=units._exchange
        def interrupted(a,b):exchange(a,b);raise KeyboardInterrupt('power loss')
        with patch.object(units,'_exchange',side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):self.migrate()
        self.assert_contract(b'new');self.assertEqual(units._read(self.adapter)['phase'],'prepared')
        units.rollback_units(self.adapter,self.root,self.versions,self.current);self.assert_contract(b'old')

    def test_online_or_nonpersistent_rescue_is_refused(self):
        self.default='multi-user.target'
        with self.assertRaisesRegex(ReleaseError,'persistent rescue'):self.migrate()
        self.default='rescue.target';self.state_name='running'
        with self.assertRaisesRegex(ReleaseError,'persistent rescue'):self.migrate()
        self.state_name='maintenance';self.active.add('nginx.service')
        with self.assertRaisesRegex(ReleaseError,'must be stopped'):self.migrate()
        self.assert_contract(b'old');self.assertFalse((self.state/units.JOURNAL).exists())

    def test_explicit_newer_contract_only_and_reject_rollback_after_activation(self):
        self.make_package('2.0.0')
        with self.assertRaisesRegex(ReleaseError,'explicitly prepare'):self.migrate()
        self.migrate(prepare_next=True);self.assert_contract(b'new')
        (self.versions/'2.0.0').mkdir();self.current.unlink();self.current.symlink_to('releases/2.0.0')
        with self.assertRaisesRegex(ReleaseError,'active release changed'):
            units.rollback_units(self.adapter,self.root,self.versions,self.current)

    def test_unsigned_package_and_outside_edit_are_refused(self):
        original=self.signature.read_bytes();self.signature.write_bytes(b'bad signature')
        with self.assertRaises(ReleaseError):self.migrate()
        self.assert_contract(b'old');self.signature.write_bytes(original);self.migrate()
        path=self.system/next(iter(units.PROTECTED));path.write_bytes(b'outside change')
        with self.assertRaisesRegex(ReleaseError,'changed outside'):
            units.rollback_units(self.adapter,self.root,self.versions,self.current)
        self.assertEqual(path.read_bytes(),b'outside change');self.assertTrue((self.recovery/'app/sunhill_nvr').is_dir())


if __name__=='__main__':unittest.main()
