"""Candidate unit-set migration tests: no real services or recording files touched."""
import json,os,sqlite3,subprocess,tempfile,unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import lifecycle,security,database,controller,config_reconcile
from sunhill_nvr.config import parse
from sunhill_nvr.deployment import generate
from sunhill_nvr.units import render
from sunhill_nvr.upgrade_system import SystemAdapter
from sunhill_nvr.install_health import check
from test_install_health import Opener

CFG=parse({'hostname':'nvr.example.com','database':'/var/lib/sunhill/db.sqlite','archive':'/home/store/kamery','staging':'/home/store/staging','exports':'/home/store/exports','archive_lock':'/var/lib/sunhill/archive.lock','activity_socket':'/run/sunhill/activity.sock','control_state':'/opt/sunhill-nvr/control','timezone':'Europe/Warsaw','minimum_free':10_000_000_000,'free_buffer':5_000_000_000})

class RTSPOnly(unittest.TestCase):
    def test_no_ftp_units_and_unchanged_recovery_contract(self):
        old=render(CFG);new=render(CFG,rtsp_only=True)
        self.assertEqual(set(old)-set(new),lifecycle.FTP_UNITS)
        self.assertTrue(all(old[n]==new[n] for n in old if n.startswith('sunhill-upgrade-')))
        self.assertIn('control-rtsp-once',new['sunhill-controller.service'])
        self.assertNotIn('ftp',new['sunhill-controller.service'])
        v2=lifecycle.default(rtsp_only=True)
        self.assertEqual(lifecycle.validate(v2,set(new)),v2)
        for names,manifest in [(set(new),lifecycle.default()),(set(old),v2),(set(new)-{'sunhill-controller.service'},v2)]:
            with self.assertRaises(ValueError):lifecycle.validate(manifest,names)
        self.assertEqual(lifecycle.validate(v2,set(old),transitional=True),v2)
        illegal=dict(v2,active=[*v2['active'],lifecycle.FTP])
        with self.assertRaises(ValueError):lifecycle.validate(illegal,set(old),transitional=True)

    def test_gateway_without_ftp_validation_or_disk_dependency(self):
        db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
        db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://host/live','ftp')")
        # Even missing FTP tables must not block the RTSP-only controller.
        db.execute('DROP TABLE camera_ftp_accounts');db.execute('DROP TABLE ftp_accounts')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);gateway=config_reconcile.desired_gateway(db)
            config_reconcile.apply(root,dict(gateway,**{'ftp.passwd':'old','ftp.group':'old'}),validate=lambda p:True,restart=lambda changed:True,service_gid=os.getgid())
            previous=(root/'current').resolve();calls=[]
            def run(args,**kw):
                calls.append(args);return subprocess.CompletedProcess(args,0)
            controller.reconcile_gateway(db,root,os.getgid(),run=run,trusted_check=lambda p:None,readiness=lambda unit:True)
            self.assertEqual({p.name for p in (root/'current').iterdir()},{'go2rtc.json'})
            self.assertTrue((previous/'ftp.passwd').is_file())
            self.assertEqual(calls,[['/usr/bin/systemctl','restart','sunhill-go2rtc.service'],['/usr/bin/systemctl','start','sunhill-go2rtc.service']])
            calls.clear();controller.reconcile_gateway(db,root,os.getgid(),run=run,trusted_check=lambda p:None,readiness=lambda unit:True)
            self.assertEqual(calls,[['/usr/bin/systemctl','start','sunhill-go2rtc.service']])
        db.close()

    def test_health_checks_skip_only_ftp(self):
        calls=[]
        def run(args,**kw):
            calls.append(args);return subprocess.CompletedProcess(args,0 if 'ftp' not in args[-1] else 1)
        probes=[]
        check('nvr.example.com',run=run,opener=Opener(),readiness=lambda unit,timeout:probes.append(unit) or True,rtsp_only=True)
        self.assertEqual(probes,['sunhill-go2rtc.service'])
        self.assertFalse(any('ftp' in a[-1] for a in calls))
        self.assertTrue(any(a[-1]=='sunhill-recorder.service' for a in calls))

    def test_database_retirement_and_monitor_preserve_unrelated_data(self):
        from sunhill_nvr import service_monitor,workers
        db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
        db.execute('PRAGMA foreign_keys=ON')
        with db:
            for i,mode in enumerate(('ftp','rtsp','none'),1):
                db.execute('INSERT INTO cameras VALUES(?,?,?,1)',(i,'cam'+str(i),'Cam'))
                db.execute('INSERT INTO camera_details(camera_id,main_url,record_mode,recording_since) VALUES(?,?,?,?)',(i,'rtsp://host/live',mode,10))
            db.execute("INSERT INTO ftp_accounts VALUES(1,'legacy','hash',1,1)")
            db.execute('INSERT INTO camera_ftp_accounts VALUES(1,1)')
            db.execute("INSERT INTO service_health VALUES('sunhill-ftp.service','inactive',2,1,1)")
            db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,'sunhill-ftp.service','service_down',1),(1,'cam1','offline',1)")
        self.assertEqual(database.retire_ftp(db,now=100),1)
        self.assertTrue(database.rtsp_only(db))
        self.assertEqual(db.execute('SELECT record_mode,recording_since FROM camera_details ORDER BY camera_id').fetchall(),[('rtsp',100),('rtsp',10),('none',10)])
        self.assertEqual(workers.selected_recorders(db),{'cam1','cam2'})
        self.assertEqual(db.execute('SELECT count(*) FROM ftp_accounts').fetchone()[0],0)
        self.assertEqual(db.execute('SELECT source FROM pending_notifications').fetchall(),[('cam1',)])
        service_monitor.check(db,now=110,inspect=lambda:{name:'active' for name in lifecycle.RTSP_ACTIVE})
        self.assertEqual(db.execute('SELECT count(*) FROM service_health').fetchone()[0],len(lifecycle.RTSP_ACTIVE))
        self.assertEqual(database.retire_ftp(db,now=200),0)
        self.assertEqual(db.execute('SELECT recording_since FROM camera_details WHERE camera_id=1').fetchone()[0],100)
        db.close()

    def test_invalid_legacy_stream_rolls_back_every_change(self):
        db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
        with db:
            db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
            db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'http://host','ftp')")
            db.execute("INSERT INTO ftp_accounts VALUES(1,'legacy','hash',1,1)")
        with self.assertRaises(ValueError):database.retire_ftp(db,now=100)
        self.assertFalse(database.rtsp_only(db));self.assertEqual(db.execute('SELECT count(*) FROM ftp_accounts').fetchone()[0],1)
        self.assertEqual(db.execute('SELECT record_mode FROM camera_details').fetchone()[0],'ftp')
        db.close()

    @unittest.skipUnless(os.geteuid()==0,'trusted system files require root')
    def test_backup_apply_and_rollback_from_real_generated_unit_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();units=root/'units';units.mkdir();proxy=root/'nginx.conf';proxy.write_text('old proxy')
            old=render(CFG);new=render(CFG,rtsp_only=True)
            for name,text in old.items():(units/name).write_text(text)
            (state/'managed-units.json').write_text(json.dumps(sorted(old)))
            (state/'lifecycle.json').write_text(json.dumps(lifecycle.default()))
            calls=[]
            def run(args,**kw):
                calls.append(args)
                if 'sunhill_nvr.deployment_cli' in args:
                    generate(CFG,Path(args[args.index('--output')+1]),rtsp_only=True)
                if args[:2]==['/usr/bin/systemctl','is-enabled']:
                    enabled=args[-1] in lifecycle.ENABLED
                    return subprocess.CompletedProcess(args,0 if enabled else 1,stdout=b'enabled\n' if enabled else b'disabled\n')
                return subprocess.CompletedProcess(args,0,stdout=b'inactive\n')
            adapter=SystemAdapter(root/'config',root/'current',state,root/'maintenance',1000,1000,run,0,units,proxy,root/'missing-cert',root/'missing-key')
            backup=state/'backup';adapter.backup_system(root/'candidate',backup)
            with patch('sunhill_nvr.upgrade_system._read_journal',return_value={'system_backup':'backup','phase':'migrating'}),patch.object(SystemAdapter,'_as_service') as migrate:
                adapter.migrate(root/'candidate');self.assertEqual(migrate.call_args.args[-2:],('migrate','--rtsp-only'))
            adapter.stop();adapter.apply_system(backup)
            self.assertEqual(adapter._managed_names(),sorted(new));self.assertEqual(adapter._runtime()['version'],2)
            self.assertTrue(all(not (units/name).exists() for name in lifecycle.FTP_UNITS))
            calls.clear();adapter.start();adapter.stop()
            self.assertFalse(any(any(name in call for name in lifecycle.FTP_UNITS) for call in calls))
            adapter.restore_system_offline(backup)
            self.assertEqual(adapter._runtime()['version'],1)
            self.assertEqual(adapter._managed_names(),sorted(old));self.assertEqual(proxy.read_text(),'old proxy')
            for name,text in old.items():self.assertEqual((units/name).read_text(),text)

    @unittest.skipUnless(os.geteuid()==0,'trusted system files require root')
    def test_crash_after_manifest_switch_uses_union_only_during_transaction(self):
        from sunhill_nvr.upgrade_transaction import _read_journal
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();units=root/'units';units.mkdir()
            old=sorted(render(CFG));new=sorted(render(CFG,rtsp_only=True))
            (state/'managed-units.json').write_text(json.dumps(old))
            (state/'lifecycle.json').write_text(json.dumps(lifecycle.default(rtsp_only=True)))
            backup=state/'backup';backup.mkdir(mode=0o700)
            (backup/'complete').write_bytes(b'complete\n');(backup/'units.json').write_text(json.dumps({'version':2,'old':old,'new':new}))
            adapter=SystemAdapter(root/'config',root/'current',state,root/'maintenance',1000,1000,system_dir=units)
            from sunhill_nvr.release import ReleaseError
            with self.assertRaises(ReleaseError):adapter._runtime()
            with patch('sunhill_nvr.upgrade_system._read_journal',return_value={'system_backup':'backup','phase':'migrating'}):
                self.assertEqual(adapter._runtime()['version'],2)

if __name__=='__main__':unittest.main()

class RetirementSafetyTests(unittest.TestCase):
    def test_probe_only_enabled_recording_cameras(self):
        from sunhill_nvr.cli import verify_rtsp_recorders
        db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db)
        for i,(mode,enabled) in enumerate((('ftp',1),('rtsp',1),('none',1),('ftp',0)),1):
            db.execute('INSERT INTO cameras VALUES(?,?,?,?)',(i,'cam'+str(i),'Cam',enabled))
            db.execute('INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(?,?,?)',(i,'rtsp://host/live',mode))
        calls=[]
        self.assertEqual(verify_rtsp_recorders(db,probe=lambda slug:calls.append(slug) or True),2)
        self.assertEqual(set(calls),{'cam1','cam2'})
        with self.assertRaisesRegex(RuntimeError,'cam1'):verify_rtsp_recorders(db,probe=lambda slug:slug!='cam1')
        self.assertEqual(db.execute('SELECT record_mode FROM camera_details WHERE camera_id=1').fetchone(),('ftp',))
        db.close()
    def test_health_blocks_commit_of_failed_migrated_stream(self):
        from sunhill_nvr.release import ReleaseError
        adapter=SystemAdapter(Path('/config'),Path('/current'),Path('/state'),Path('/maintenance'),1000,1000,retire_ftp=True)
        with patch.object(SystemAdapter,'_healthy_once',return_value=True),patch.object(SystemAdapter,'_runtime',return_value=lifecycle.default(rtsp_only=True)),patch.object(SystemAdapter,'_as_service',side_effect=ReleaseError('offline')):
            self.assertFalse(adapter.healthy())
        with patch.object(SystemAdapter,'_healthy_once',return_value=True),patch.object(SystemAdapter,'_runtime',return_value=lifecycle.default()),patch.object(SystemAdapter,'_as_service') as check:
            self.assertTrue(adapter.healthy());check.assert_not_called()
