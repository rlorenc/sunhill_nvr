import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from sys import path
from unittest.mock import patch

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.release import ReleaseError
from sunhill_nvr.upgrade_system import SystemAdapter, SERVICES, _copy_private


class UpgradeSystemTests(unittest.TestCase):
    def modern_fixture(self,root,*,conflict=False,unsafe_dependency=False):
        from sunhill_nvr import lifecycle
        state=root/'state';state.mkdir();units=root/'units';units.mkdir();proxy=root/'nginx.conf';proxy.write_text('old proxy')
        old_names=sorted(set(lifecycle.ACTIVE)|{'sunhill-controller.service','sunhill-upgrade-recover.service'})
        new_names=sorted((set(old_names)-{'sunhill-clock.service'})|{'sunhill-motion.service'})
        (state/'managed-units.json').write_text(json.dumps(old_names));(state/'lifecycle.json').write_text(json.dumps(lifecycle.default()))
        for name in old_names:(units/name).write_text('stable recovery' if name.startswith('sunhill-upgrade-') else 'old '+name)
        if conflict:(units/'sunhill-motion.service').write_text('unmanaged')
        wanted={'version':1,'active':[name for name in lifecycle.ACTIVE if name!='sunhill-clock.service']+['sunhill-motion.service'],
                'enabled':[name for name in lifecycle.ENABLED if name!='sunhill-clock.service']+['sunhill-motion.service']}
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if 'sunhill_nvr.deployment_cli' in args:
                generated=Path(args[args.index('--output')+1]);(generated/'units').mkdir()
                for name in new_names:
                    if name=='sunhill-motion.service':
                        body='[Unit]\nRequires=sunhill-upgrade-recover.service\nAfter=sunhill-upgrade-recover.service\n' if not unsafe_dependency else '# Requires=sunhill-upgrade-recover.service\nAfter=network.target\n'
                    else:body='stable recovery' if name.startswith('sunhill-upgrade-') else '[Unit]\nRequires=sunhill-upgrade-recover.service\nAfter=sunhill-upgrade-recover.service\n# new '+name
                    (generated/'units'/name).write_text(body)
                (generated/'nginx.conf').write_text('if (-f /opt/sunhill-nvr/config/maintenance.flag) { return 503; }\nnew proxy')
                (generated/'lifecycle.json').write_text(json.dumps(wanted))
            disabled={'sunhill-go2rtc.service','sunhill-ftp.service','sunhill-export.service'}
            if args[:2]==['/usr/bin/systemctl','is-enabled']:
                disabled_unit=args[-1] in disabled
                return subprocess.CompletedProcess(args,1 if disabled_unit else 0,stdout=b'disabled\n' if disabled_unit else b'enabled\n')
            return subprocess.CompletedProcess(args,0,stdout=b'inactive\n')
        adapter=SystemAdapter(root/'config',root/'current',state,root/'maintenance',1000,1000,run,0,units,proxy,root/'missing-cert',root/'missing-key')
        return adapter,root/'backup',units,proxy,calls,old_names,new_names

    def test_unit_addition_removal_and_enabled_state_are_reversible(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter,backup,units,proxy,calls,old_names,new_names=self.modern_fixture(Path(tmp))
            adapter.backup_system(Path(tmp)/'candidate',backup);adapter.apply_system(backup)
            self.assertEqual(adapter._managed_names(),new_names);self.assertFalse((units/'sunhill-clock.service').exists())
            self.assertTrue((units/'sunhill-motion.service').exists());self.assertIn('sunhill-motion.service',adapter._runtime()['active'])
            self.assertNotIn('sunhill-export.service',adapter._runtime()['enabled'])
            self.assertTrue(any(call[:2]==['/usr/bin/systemctl','enable'] and 'sunhill-motion.service' in call for call in calls))
            calls.clear();adapter.stop();adapter.start()
            self.assertTrue(any(call[:2]==['/usr/bin/systemctl','start'] and 'sunhill-motion.service' in call for call in calls))
            self.assertFalse(any(call[:2]==['/usr/bin/systemctl','start'] and 'sunhill-clock.service' in call for call in calls))
            adapter.restore_system(backup)
            self.assertEqual(adapter._managed_names(),old_names);self.assertFalse((units/'sunhill-motion.service').exists())
            self.assertEqual((units/'sunhill-clock.service').read_text(),'old sunhill-clock.service');self.assertEqual(proxy.read_text(),'old proxy')
            self.assertNotIn('sunhill-export.service',adapter._runtime()['enabled'])

    def test_interrupted_unit_change_restores_both_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter,backup,units,proxy,calls,old_names,new_names=self.modern_fixture(Path(tmp))
            adapter.backup_system(Path(tmp)/'candidate',backup)
            original=SystemAdapter._replace
            def interrupted(path,data):
                original(path,data)
                if path.name=='managed-units.json':raise KeyboardInterrupt()
            with patch.object(SystemAdapter,'_replace',side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):adapter.apply_system(backup)
            adapter.restore_system_offline(backup)
            self.assertEqual(adapter._managed_names(),old_names);self.assertFalse((units/'sunhill-motion.service').exists())
            self.assertEqual(proxy.read_text(),'old proxy');self.assertIn('sunhill-clock.service',adapter._runtime()['active'])

    def test_recovery_after_each_atomic_replacement_of_new_units(self):
        from sunhill_nvr import lifecycle
        steps=len(lifecycle.ACTIVE)+5
        for crash_at in range(1,steps+1):
            with self.subTest(crash_at=crash_at),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);adapter,_,units,proxy,calls,old_names,new_names=self.modern_fixture(root)
                backup=adapter.state/('config-'+'a'*32);adapter.backup_system(root/'candidate',backup)
                (adapter.state/'transaction.json').write_text(json.dumps({'old':'1.0.0','new':'1.1.0','phase':'migrating','backup':'backup-'+'b'*32+'.sqlite','system_backup':backup.name}))
                original=SystemAdapter._replace;count=[0]
                def interrupted(path,data):
                    original(path,data);count[0]+=1
                    if count[0]==crash_at:raise KeyboardInterrupt()
                with patch.object(SystemAdapter,'_replace',side_effect=interrupted):
                    with self.assertRaises(KeyboardInterrupt):adapter.apply_system(backup)
                runtime=adapter._runtime()
                self.assertTrue(all((units/name).is_file() for name in runtime['active']))
                adapter.restore_system_offline(backup)
                self.assertEqual(adapter._managed_names(),old_names);self.assertEqual(proxy.read_text(),'old proxy')
                self.assertFalse((units/'sunhill-motion.service').exists());self.assertTrue((units/'sunhill-clock.service').exists())

    def test_partial_backup_during_stopping_does_not_prevent_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);adapter,_,units,proxy,calls,old_names,new_names=self.modern_fixture(root)
            name='config-'+'a'*32;backup=adapter.state/name;backup.mkdir();(backup/'units.json').write_text('{')
            journal={'old':'1.0.0','new':'1.1.0','phase':'stopping','backup':'backup-'+'b'*32+'.sqlite','system_backup':name}
            path=adapter.state/'transaction.json';path.write_text(json.dumps(journal))
            self.assertIn('sunhill-clock.service',adapter._runtime()['active'])
            journal['phase']='migrating';path.write_text(json.dumps(journal))
            with self.assertRaises(ReleaseError):adapter._runtime()

    def test_recovery_after_interrupted_enable_disable_and_removal(self):
        from dataclasses import replace
        for interruption in ('disable','enable','unlink'):
            with self.subTest(interruption=interruption),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);adapter,_,units,proxy,calls,old_names,new_names=self.modern_fixture(root)
                backup=adapter.state/('config-'+'a'*32);adapter.backup_system(root/'candidate',backup)
                (adapter.state/'transaction.json').write_text(json.dumps({'old':'1.0.0','new':'1.1.0','phase':'migrating','backup':'backup-'+'b'*32+'.sqlite','system_backup':backup.name}))
                run_original=adapter.run;unlink_original=Path.unlink;triggered=[False]
                def run(args,**kwargs):
                    result=run_original(args,**kwargs)
                    if not triggered[0] and args[:2]==['/usr/bin/systemctl',interruption]:
                        triggered[0]=True;raise KeyboardInterrupt()
                    return result
                def unlink(path,*args,**kwargs):
                    result=unlink_original(path,*args,**kwargs)
                    if interruption=='unlink' and path==units/'sunhill-clock.service' and not triggered[0]:
                        triggered[0]=True;raise KeyboardInterrupt()
                    return result
                adapter=replace(adapter,run=run)
                with patch.object(Path,'unlink',unlink):
                    with self.assertRaises(KeyboardInterrupt):adapter.apply_system(backup)
                self.assertTrue(triggered[0]);self.assertTrue(all((units/name).is_file() for name in adapter._runtime()['active']))
                adapter.restore_system_offline(backup)
                self.assertEqual(adapter._managed_names(),old_names);self.assertFalse((units/'sunhill-motion.service').exists())
                self.assertTrue((units/'sunhill-clock.service').exists());self.assertEqual(proxy.read_text(),'old proxy')

    def test_failed_enable_restores_old_units_and_boot_configuration(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as tmp:
            adapter,backup,units,proxy,calls,old_names,new_names=self.modern_fixture(Path(tmp))
            adapter.backup_system(Path(tmp)/'candidate',backup)
            original=adapter.run;failed=[False]
            def run(args,**kwargs):
                if args[:2]==['/usr/bin/systemctl','enable'] and 'sunhill-motion.service' in args and not failed[0]:
                    failed[0]=True;return subprocess.CompletedProcess(args,1,stdout=b'')
                return original(args,**kwargs)
            adapter=replace(adapter,run=run)
            with self.assertRaises(ReleaseError):adapter.apply_system(backup)
            adapter.restore_system(backup)
            self.assertEqual(adapter._managed_names(),old_names);self.assertFalse((units/'sunhill-motion.service').exists())
            self.assertTrue(any(call[:2]==['/usr/bin/systemctl','disable'] and 'sunhill-motion.service' in call for call in calls))
            self.assertEqual(proxy.read_text(),'old proxy')

    def test_unit_addition_refuses_conflicts_and_missing_recovery_dependency(self):
        for kwargs in ({'conflict':True},{'unsafe_dependency':True}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as tmp:
                adapter,backup,units,proxy,calls,old_names,new_names=self.modern_fixture(Path(tmp),**kwargs)
                with self.assertRaises(ReleaseError):adapter.backup_system(Path(tmp)/'candidate',backup)
                self.assertEqual(proxy.read_text(),'old proxy');self.assertEqual(adapter._managed_names(),old_names)
                if kwargs.get('conflict'):self.assertEqual((units/'sunhill-motion.service').read_text(),'unmanaged')

    def test_upgrade_refuses_unrecognized_boot_enablement(self):
        from dataclasses import replace
        for state in (b'enabled-runtime\n',b'indirect\n'):
            with self.subTest(state=state),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);adapter,backup,units,proxy,calls,old_names,new_names=self.modern_fixture(root)
                original=adapter.run
                def run(args,**kwargs):
                    if args[:2]==['/usr/bin/systemctl','is-enabled']:
                        return subprocess.CompletedProcess(args,0,stdout=state)
                    return original(args,**kwargs)
                adapter=replace(adapter,run=run)
                with self.assertRaises(ReleaseError):adapter.backup_system(root/'candidate',backup)
                self.assertEqual(proxy.read_text(),'old proxy');self.assertEqual(adapter._managed_names(),old_names)

    def test_unit_transaction_refuses_outside_configuration_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter,backup,units,proxy,calls,old_names,new_names=self.modern_fixture(Path(tmp))
            adapter.backup_system(Path(tmp)/'candidate',backup)
            proxy.write_text('administrator changed this')
            with self.assertRaises(ReleaseError):adapter.apply_system(backup)
            self.assertEqual(proxy.read_text(),'administrator changed this');self.assertFalse((units/'sunhill-motion.service').exists())

    def test_unit_transaction_rejects_symlinked_backup_directory(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);adapter,backup,units,proxy,calls,old_names,new_names=self.modern_fixture(root)
            adapter.backup_system(root/'candidate',backup)
            moved=root/'outside';shutil.move(str(backup/'old'),moved);(backup/'old').symlink_to(moved,target_is_directory=True)
            with self.assertRaises(ReleaseError):adapter.restore_system(backup)
            self.assertEqual(proxy.read_text(),'old proxy');self.assertEqual(adapter._managed_names(),old_names)

    def test_unit_transaction_rejects_nonprivate_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);adapter,backup,units,proxy,calls,old_names,new_names=self.modern_fixture(root)
            adapter.backup_system(root/'candidate',backup);(backup/'new').chmod(0o755)
            with self.assertRaises(ReleaseError):adapter.apply_system(backup)
            self.assertEqual(proxy.read_text(),'old proxy');self.assertEqual(adapter._managed_names(),old_names)

    def test_unit_set_and_database_recover_together_after_power_loss(self):
        from sunhill_nvr.upgrade_transaction import Operations,activate,recover_offline,finalize_offline
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);adapter,_,units,proxy,calls,old_names,new_names=self.modern_fixture(root)
            versions=root/'releases';versions.mkdir()
            for name in ('1.0.0','1.1.0'):
                directory=versions/name;directory.mkdir();(directory/'manifest.json').write_text('{}')
            current=root/'current';current.symlink_to('releases/1.0.0')
            state={'running':True,'fenced':False,'database':'old'}
            def fence():state['fenced']=True
            def unfence():state['fenced']=False
            def stop():adapter.stop();state['running']=False
            def assert_stopped():self.assertFalse(state['running'])
            def start():adapter.start();state['running']=True
            def healthy():return state['running'] and state['fenced']
            def backup(path):path.write_text(state['database'])
            def restore(path):state['database']=path.read_text()
            def migrate(candidate):state['database']='new'
            def crash_after_apply(path):adapter.apply_system(path);raise KeyboardInterrupt()
            ops=Operations(fence,unfence,stop,assert_stopped,start,healthy,backup,restore,migrate,
                           adapter.backup_system,crash_after_apply,adapter.restore_system,adapter.restore_system_offline,adapter.finalize_system)
            with self.assertRaises(KeyboardInterrupt):activate(versions,current,adapter.state,'1.1.0',ops)
            self.assertEqual(adapter._managed_names(),new_names);self.assertEqual(state['database'],'new');self.assertTrue(state['fenced'])
            self.assertTrue(recover_offline(versions,current,adapter.state,ops))
            self.assertEqual(adapter._managed_names(),old_names);self.assertEqual(state['database'],'old');self.assertEqual(current.resolve().name,'1.0.0')
            self.assertFalse(state['running']);self.assertTrue(finalize_offline(versions,current,adapter.state,ops))
            self.assertTrue(state['running']);self.assertFalse(state['fenced']);self.assertFalse((units/'sunhill-motion.service').exists())
    def test_recovery_uses_old_release_even_after_current_was_switched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();versions=root/'releases';versions.mkdir()
            (versions/'1.0.0').mkdir();(versions/'1.1.0').mkdir()
            current=root/'current';current.symlink_to('releases/1.1.0')
            (state/'transaction.json').write_text(json.dumps({'old':'1.0.0','new':'1.1.0','phase':'migrating','backup':'backup-'+'0'*32+'.sqlite'}))
            adapter=SystemAdapter(root/'config',current,state,root/'maintenance',1000,1000)
            self.assertEqual(adapter._old_release(),versions/'1.0.0')
    def test_backup_copy_refuses_symlinks_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'db';source.write_bytes(b'database');destination=root/'copy'
            _copy_private(source,destination);self.assertEqual(destination.read_bytes(),b'database')
            with self.assertRaises(FileExistsError):_copy_private(source,destination)
            self.assertEqual(destination.read_bytes(),b'database')
            symlink=root/'link';symlink.symlink_to(source)
            with self.assertRaises(OSError):_copy_private(symlink,root/'copy2')

    def test_stops_controller_first_and_checks_every_service(self):
        calls=[];fail=set()
        def run(args,**kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args,1 if args[-1] in fail else 0)
        adapter=SystemAdapter(Path('/config'),Path('/current'),Path('/state'),Path('/maintenance'),1000,1000,run,0)
        adapter.stop()
        self.assertEqual(calls[0][-1],'sunhill-controller.timer');self.assertEqual(calls[1][-1],'sunhill-controller.service')
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return b'{"status":"ok"}'
        class Opener:
            def open(self,*args,**kwargs):return Response()
        calls.clear()
        with patch('sunhill_nvr.upgrade_system.urllib.request.build_opener',return_value=Opener()), \
             patch('sunhill_nvr.upgrade_system.service_ready',return_value=True) as ready:
            self.assertTrue(adapter.healthy())
            self.assertEqual([call[-1] for call in calls],list(SERVICES)+['nginx.service'])
            self.assertEqual([call.args[0] for call in ready.call_args_list],['sunhill-go2rtc.service','sunhill-ftp.service'])
            fail.add('sunhill-recorder.service');self.assertFalse(adapter.healthy());fail.clear()
            fail.add('nginx.service');self.assertFalse(adapter.healthy());fail.clear()
            for unavailable in ('sunhill-go2rtc.service','sunhill-ftp.service'):
                ready.side_effect=lambda service,**kwargs:service!=unavailable
                self.assertFalse(adapter.healthy())

    def test_managed_units_and_proxy_are_restored_after_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();units=root/'units';units.mkdir();proxy=root/'nginx.conf'
            names=['sunhill-upgrade-apply.service','sunhill-web.service']
            (state/'managed-units.json').write_text(json.dumps(names))
            (units/names[0]).write_text('stable recovery unit');(units/names[1]).write_text('old web unit');proxy.write_text('old proxy')
            calls=[]
            def run(args,**kwargs):
                calls.append(args)
                if 'sunhill_nvr.deployment_cli' in args:
                    generated=Path(args[args.index('--output')+1]);(generated/'units').mkdir()
                    (generated/'units'/names[0]).write_text('stable recovery unit');(generated/'units'/names[1]).write_text('new web unit')
                    (generated/'nginx.conf').write_text('if (-f /opt/sunhill-nvr/config/maintenance.flag) { return 503; }\nnew proxy')
                return subprocess.CompletedProcess(args,0)
            adapter=SystemAdapter(root/'config',root/'current',state,root/'maintenance',1000,1000,run,0,units,proxy,root/'missing-cert',root/'missing-key')
            backup=root/'backup';adapter.backup_system(root/'candidate',backup)
            adapter.apply_system(backup)
            self.assertEqual((units/names[1]).read_text(),'new web unit');self.assertIn('new proxy',proxy.read_text())
            adapter.restore_system(backup)
            self.assertEqual((units/names[1]).read_text(),'old web unit');self.assertEqual(proxy.read_text(),'old proxy')
            self.assertTrue(any(call[:2]==['/usr/bin/systemd-analyze','verify'] for call in calls))
            calls.clear();adapter.restore_system_offline(backup)
            self.assertFalse(any(call[:3]==['/usr/bin/systemctl','reload','nginx.service'] for call in calls))
            adapter.finalize_system()
            self.assertTrue(any(call[:3]==['/usr/bin/systemctl','reload','nginx.service'] for call in calls))

    def test_update_rejects_new_unit_without_modifying_installed_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();units=root/'units';units.mkdir();proxy=root/'nginx.conf'
            (state/'managed-units.json').write_text(json.dumps(['sunhill-web.service']))
            (units/'sunhill-web.service').write_text('old web unit');proxy.write_text('old proxy')
            def run(args,**kwargs):
                generated=Path(args[args.index('--output')+1]);(generated/'units').mkdir()
                (generated/'units/sunhill-web.service').write_text('new');(generated/'units/sunhill-new.service').write_text('new')
                return subprocess.CompletedProcess(args,0)
            adapter=SystemAdapter(root/'config',root/'current',state,root/'maintenance',1000,1000,run,0,units,proxy,root/'missing-cert',root/'missing-key')
            with self.assertRaises(ReleaseError):adapter.backup_system(root/'candidate',root/'backup')
            self.assertEqual((units/'sunhill-web.service').read_text(),'old web unit');self.assertEqual(proxy.read_text(),'old proxy')

    def test_update_rejects_modification_of_recovery_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();units=root/'units';units.mkdir();proxy=root/'nginx.conf'
            (state/'managed-units.json').write_text(json.dumps(['sunhill-upgrade-recover.service']))
            (units/'sunhill-upgrade-recover.service').write_text('old recovery');proxy.write_text('old proxy')
            def run(args,**kwargs):
                generated=Path(args[args.index('--output')+1]);(generated/'units').mkdir()
                (generated/'units/sunhill-upgrade-recover.service').write_text('changed recovery')
                return subprocess.CompletedProcess(args,0)
            adapter=SystemAdapter(root/'config',root/'current',state,root/'maintenance',1000,1000,run,0,units,proxy,root/'missing-cert',root/'missing-key')
            with self.assertRaises(ReleaseError):adapter.backup_system(root/'candidate',root/'backup')
            self.assertEqual((units/'sunhill-upgrade-recover.service').read_text(),'old recovery')


if __name__=='__main__':unittest.main()
