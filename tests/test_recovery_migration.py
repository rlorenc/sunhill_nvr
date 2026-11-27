import os
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sys import path
path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sunhill_nvr import recovery_migration as migration
from sunhill_nvr.build_release import build
from sunhill_nvr.release import ReleaseError


@unittest.skipUnless(os.geteuid() == 0, 'recovery migration requires root-owned test files')
class RecoveryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.state = self.root / 'state'; self.state.mkdir()
        self.recovery = self.root / 'recovery'; (self.recovery / 'app').mkdir(parents=True)
        (self.recovery / 'app/old.py').write_text('old')
        self.versions = self.root / 'releases'; (self.versions / '1.0.0').mkdir(parents=True)
        self.current = self.root / 'current'; self.current.symlink_to('releases/1.0.0')
        self.units = self.root / 'units'; self.units.mkdir()
        self.unit_name = 'sunhill-upgrade-recover.service'; (self.units / self.unit_name).write_bytes(b'unit')
        self.generated_content = b'unit'
        self.adapter = SimpleNamespace(state=self.state, maintenance=self.root / 'maintenance',
            config_file=self.root / 'config', tls_certificate=self.root / 'cert', system_dir=self.units,
            _managed_names=lambda: [self.unit_name], _read_trusted=lambda p:p.read_bytes(), _command=self.command,
            _unit_state=lambda unit: "inactive")
        source = self.root / 'src/sunhill_nvr'; source.mkdir(parents=True)
        (source / '__init__.py').write_text('VERSION=1\n')
        key = self.root / 'private'; self.public = self.root / 'public'
        self.package = self.root / 'signed.zip'; self.signature = self.root / 'signed.sig'
        subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(key)], check=True, capture_output=True)
        subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(self.public)], check=True, capture_output=True)
        build(source.parent, '1.0.0', key, self.package, self.signature)

    def command(self, args, timeout):
        if 'sunhill_nvr.deployment_cli' in args:
            units = Path(args[args.index('--output') + 1]) / 'units'; units.mkdir()
            (units / self.unit_name).write_bytes(self.generated_content)

    def run_migration(self):
        return migration.migrate(self.adapter, self.root, self.versions, self.current, self.public, self.package, self.signature)

    def test_signed_tree_exchanges_atomically_and_retains_previous(self):
        backup = self.run_migration()
        self.assertEqual((backup / 'app/old.py').read_text(), 'old')
        self.assertTrue((self.recovery / 'app/sunhill_nvr/__init__.py').is_file())
        self.assertEqual(self.current.resolve().name, '1.0.0')
        self.assertEqual((self.units / self.unit_name).read_bytes(), b'unit')

    def test_rejects_pending_transactions_and_changed_boot_contract(self):
        for name in ('transaction.json','domain-transaction.json','tls-transaction.json','storage-transaction.json'):
            with self.subTest(name=name):
                journal = self.state / name; journal.write_text('{}')
                try:
                    with self.assertRaisesRegex(ReleaseError, 'pending'): self.run_migration()
                finally: journal.unlink()
        self.generated_content = b'changed'
        with self.assertRaisesRegex(ReleaseError, 'offline unit migration'): self.run_migration()
        self.assertTrue((self.recovery / 'app/old.py').exists())

    def test_invalid_signature_and_different_version_leave_old_tree(self):
        (self.versions / '2.0.0').mkdir(); self.current.unlink(); self.current.symlink_to('releases/2.0.0')
        with self.assertRaisesRegex(ReleaseError, 'match active'): self.run_migration()
        self.signature.write_bytes(b'bad signature')
        with self.assertRaisesRegex(ReleaseError, 'signature'): self.run_migration()
        self.assertTrue((self.recovery / 'app/old.py').exists())

    def test_exchange_failure_keeps_both_complete_trees(self):
        with patch.object(migration, '_exchange', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError): self.run_migration()
        self.assertTrue((self.recovery / 'app/old.py').is_file())
        backups = list(self.root.glob('recovery-previous-*')); self.assertEqual(len(backups),1)
        self.assertTrue((backups[0] / 'app/sunhill_nvr/__init__.py').is_file())

    def test_real_signed_application_renders_its_own_boot_contract_and_migrates(self):
        from sunhill_nvr.config import parse
        from sunhill_nvr.units import render
        from sunhill_nvr.upgrade_system import SystemAdapter
        raw={'hostname':'nvr.example.com','database':str(self.root/'data/db.sqlite'),
             'archive':str(self.root/'data/archive'),'staging':str(self.root/'data/staging'),
             'exports':str(self.root/'data/exports'),'archive_lock':str(self.root/'data/archive.lock'),
             'activity_socket':'/run/sunhill-activity/activity.sock','control_state':str(self.state),
             'timezone':'Europe/Warsaw','minimum_free':10_000_000_000,'free_buffer':5_000_000_000}
        self.adapter.config_file.write_text(json.dumps(raw))
        rendered=render(parse(raw),rtsp_only=True)
        for name,content in rendered.items():(self.units/name).write_text(content)
        (self.state/'managed-units.json').write_text(json.dumps(sorted(rendered)))
        self.package.unlink();self.signature.unlink()
        build(Path(__file__).resolve().parents[1]/'src','1.0.0',self.root/'private',self.package,self.signature)
        self.adapter=SystemAdapter(self.adapter.config_file,self.current,self.state,self.adapter.maintenance,
                                   1000,1000,system_dir=self.units,tls_certificate=self.root/'cert',tls_key=self.root/'key')
        # Only systemd process states are simulated: the signed release is
        # built, verified, renders units and imports the real manager in subprocesses.
        with patch.object(SystemAdapter,'_unit_state',return_value='inactive'):
            previous=self.run_migration()
        self.assertTrue((previous/'app/old.py').is_file())
        self.assertTrue((self.recovery/'app/sunhill_nvr/domain_change.py').is_file())
        self.assertTrue((self.recovery/'app/sunhill_nvr/upgrade_admin.py').is_file())
        self.assertEqual((self.units/'sunhill-upgrade-recover.service').read_text(),rendered['sunhill-upgrade-recover.service'])

    def test_refuses_running_manager_timer(self):
        self.adapter._unit_state=lambda unit:'active' if unit.endswith('.timer') else 'inactive'
        with self.assertRaisesRegex(ReleaseError,'quiescent'):self.run_migration()
        self.assertTrue((self.recovery/'app/old.py').exists())

    def test_refuses_symlink_or_writable_recovery_tree(self):
        bad = self.recovery / 'app/bad'; bad.symlink_to(self.package)
        with self.assertRaisesRegex(ReleaseError, 'untrusted'): self.run_migration()
        bad.unlink(); os.chmod(self.recovery / 'app',0o777)
        with self.assertRaisesRegex(ReleaseError, 'untrusted'): self.run_migration()

if __name__ == '__main__': unittest.main()
