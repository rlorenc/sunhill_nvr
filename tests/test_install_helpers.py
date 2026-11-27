import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallerServiceTests(unittest.TestCase):
    def run_helper(self, present, fail=None, function="start_initial_services"):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            executable = root / 'systemctl'
            executable.write_text('''#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as f:f.write(' '.join(args)+'\\n')
if args[0]=='cat':sys.exit(0 if args[1] in os.environ['PRESENT'].split(',') else 1)
if args[0] in ('disable','start'):sys.exit(1 if args[-1]==os.environ.get('FAIL') else 0)
if args[0] in ('--failed','status'):sys.exit(0)
sys.exit(2)
''')
            executable.chmod(0o755)
            calls = root / 'calls'
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ.get('PATH',''),
                       CALLS=str(calls), PRESENT=','.join(present), FAIL=fail or '')
            script = '. "$1"; '+function
            result = subprocess.run(['bash','-e','-c',script,'bash',str(ROOT/'scripts/install_helpers.sh')],env=env,capture_output=True,text=True)
            return result.returncode,calls.read_text().splitlines() if calls.exists() else []

    def test_initial_services_start_recovery_first_and_fail_closed(self):
        expected=['start sunhill-upgrade-recover.service','start sunhill-activity.service','start sunhill-controller.service']
        code,calls=self.run_helper([],function='start_initial_services')
        self.assertEqual(code,0);self.assertEqual(calls,expected)
        code,calls=self.run_helper([],fail='sunhill-upgrade-recover.service',function='start_initial_services')
        self.assertNotEqual(code,0);self.assertEqual([x for x in calls if x.startswith('start ')],expected[:1])
        self.assertTrue(any(x.startswith('status sunhill-upgrade-recover.service') for x in calls))

    def test_existing_or_dangling_install_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);target=root/'existing'
            command=['bash','-e','-c','. "$1"; refuse_existing_paths "$2"','bash',str(ROOT/'scripts/install_helpers.sh'),str(target)]
            self.assertEqual(subprocess.run(command,capture_output=True).returncode,0)
            target.symlink_to(root/'absent')
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
            self.assertTrue(target.is_symlink())
            target.unlink();target.mkdir()
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

    def test_storage_preflight_rejects_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            def check():return subprocess.run(['bash','-e','-c','. "$1"; validate_storage_targets "$2"','bash',str(ROOT/'scripts/install_helpers.sh'),str(root)],capture_output=True).returncode
            self.assertEqual(check(),0)
            (root/'kamery').mkdir();self.assertEqual(check(),0)
            (root/'.sunhill-exports').write_text('data');self.assertNotEqual(check(),0)
            (root/'.sunhill-exports').unlink();(root/'.sunhill-staging').symlink_to(root/'absent')
            self.assertNotEqual(check(),0)

    def test_root_filesystem_requires_explicit_choice_and_never_accepts_root_directory(self):
        def check(storage,target,option):
            return subprocess.run(['bash','-e','-c','. "$1"; validate_storage_mount "$2" "$3" "$4"',
                                   'bash',str(ROOT/'scripts/install_helpers.sh'),storage,target,option],capture_output=True,text=True)
        self.assertEqual(check('/data','/data','0').returncode,0)
        self.assertNotEqual(check('/srv/cameras','/','0').returncode,0)
        self.assertEqual(check('/srv/cameras','/','1').returncode,0)
        self.assertNotEqual(check('/','/','1').returncode,0)
        self.assertNotEqual(check('/srv/cameras','','1').returncode,0)
        self.assertNotEqual(check('/srv/cameras','/','yes').returncode,0)

    def test_optional_storage_group_must_be_unprivileged_and_exist(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);binary=root/'bin';binary.mkdir();getent=binary/'getent'
            getent.write_text('#!/bin/sh\n[ "$2" = cameras ] || exit 2\nprintf "cameras:x:%s:\\n" "$FAKE_GID"\n')
            getent.chmod(0o755)
            def check(group,gid='1001'):
                env=dict(os.environ,PATH=str(binary)+os.pathsep+os.environ.get('PATH',''),FAKE_GID=gid)
                return subprocess.run(['bash','-e','-c','. "$1"; validate_storage_group "$2"','bash',str(ROOT/'scripts/install_helpers.sh'),group],env=env,capture_output=True)
            self.assertEqual(check('').returncode,0)
            self.assertEqual(check('cameras').returncode,0)
            for group,gid in [('cameras','999'),('cameras','65534'),('cameras','bad'),('docker','1001'),('missing','1001'),('-bad','1001'),('cameras,other','1001')]:
                with self.subTest(group=group,gid=gid):self.assertNotEqual(check(group,gid).returncode,0)

    def test_storage_access_is_checked_without_modifying_existing_data(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);storage=root/'storage';storage.mkdir();archive=storage/'kamery';archive.mkdir();recording=archive/'original.mp4';recording.write_bytes(b'keep')
            binary=root/'bin';binary.mkdir();runuser=binary/'runuser'
            runuser.write_text('''#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as log:log.write(' '.join(args)+'\\n')
sys.exit(1 if os.environ.get('DENY') in args else 0)
''');runuser.chmod(0o755);calls=root/'calls'
            def check(deny):
                env=dict(os.environ,PATH=str(binary)+os.pathsep+os.environ.get('PATH',''),CALLS=str(calls),DENY=deny)
                return subprocess.run(['bash','-e','-c','. "$1"; validate_storage_access "$2" sunhill','bash',str(ROOT/'scripts/install_helpers.sh'),str(storage)],env=env,capture_output=True,text=True)
            self.assertEqual(check('never').returncode,0)
            result=check('-w');self.assertNotEqual(result.returncode,0);self.assertIn(str(archive),result.stderr)
            self.assertEqual(recording.read_bytes(),b'keep');self.assertFalse((storage/'.sunhill-staging').exists())
            self.assertIn('-x '+str(storage),calls.read_text())

    def test_preflight_identity_cleanup_checks_uid_and_gid_before_deleting(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);binary=root/'bin';binary.mkdir();calls=root/'calls'
            scripts={
                'id':'#!/bin/sh\n[ "$1" = -u ] && { echo "$FAKE_UID"; exit; }; echo "$FAKE_GID"\n',
                'userdel':'#!/bin/sh\necho "userdel $*" >> "$CALLS"\n',
                'getent':'#!/bin/sh\necho "sunhill:x:$FAKE_GROUP_GID:"\n',
                'groupdel':'#!/bin/sh\necho "groupdel $*" >> "$CALLS"\n',
            }
            for filename,body in scripts.items():
                target=binary/filename;target.write_text(body);target.chmod(0o755)
            def cleanup(uid,gid,group_gid):
                env=dict(os.environ,PATH=str(binary)+os.pathsep+os.environ.get('PATH',''),CALLS=str(calls),FAKE_UID=uid,FAKE_GID=gid,FAKE_GROUP_GID=group_gid)
                return subprocess.run(['bash','-e','-c','. "$1"; rollback_fresh_identity sunhill 1001 1002','bash',str(ROOT/'scripts/install_helpers.sh')],env=env,capture_output=True,text=True)
            self.assertNotEqual(cleanup('9999','1002','1002').returncode,0);self.assertFalse(calls.exists())
            self.assertEqual(cleanup('1001','1002','1002').returncode,0)
            self.assertEqual(calls.read_text().splitlines(),['userdel sunhill','groupdel sunhill'])

    def test_tls_paths_require_a_pair_of_regular_files(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);cert=root/'cert';key=root/'key';cert.write_text('cert');key.write_text('key')
            def check(first,second):
                return subprocess.run(['bash','-e','-c','. "$1"; validate_tls_inputs "$2" "$3"','bash',str(ROOT/'scripts/install_helpers.sh'),str(first),str(second)],capture_output=True).returncode
            self.assertEqual(check('',''),0);self.assertEqual(check(cert,key),0)
            self.assertNotEqual(check('',key),0);self.assertNotEqual(check(cert,''),0)
            link=root/'link';link.symlink_to(cert)
            self.assertNotEqual(check(link,key),0)

    def test_runtime_dependency_checks_report_missing_or_non_executable_files(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);dependency=root/'binary';dependency.write_text('#!/bin/sh\n')
            def check():
                return subprocess.run(['bash','-e','-c','. "$1"; validate_executable_dependencies "$2"',
                                       'bash',str(ROOT/'scripts/install_helpers.sh'),str(dependency)],capture_output=True,text=True)
            self.assertNotEqual(check().returncode,0)
            dependency.chmod(0o755)
            self.assertEqual(check().returncode,0)
            dependency.unlink()
            result=check();self.assertNotEqual(result.returncode,0);self.assertIn(str(dependency),result.stderr)


    @unittest.skipUnless(os.geteuid() == 0, 'requires root and real unprivileged account')
    def test_actual_group_access_to_existing_archive(self):
        import grp
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);root.chmod(0o755)
            archive=root/'kamery';archive.mkdir();os.chown(archive,0,grp.getgrnam('nogroup').gr_gid)
            archive.chmod(0o770)
            command=['bash','-e','-c','. "$1"; validate_storage_access "$2" nobody','bash',str(ROOT/'scripts/install_helpers.sh'),str(root)]
            self.assertEqual(subprocess.run(command,capture_output=True).returncode,0)
            archive.chmod(0o700)
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

    @unittest.skipUnless(os.geteuid() == 0, 'requires root and real unprivileged account')
    def test_directory_layout_under_restrictive_umask(self):
        with tempfile.TemporaryDirectory() as name:
            parent=Path(name);parent.chmod(0o755);root=parent/'sunhill-nvr'
            command=['bash','-e','-c','umask 077; . "$1"; prepare_application_directories "$2" 0.2.1 nobody nogroup 1; install -d -o nobody -g nogroup -m 0750 "$2/media/kamery"','bash',str(ROOT/'scripts/install_helpers.sh'),str(root)]
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            for relative in ('','releases','releases/0.2.1','releases/0.2.1/app','recovery','recovery/app','config'):
                path=root/relative
                self.assertEqual(path.stat().st_mode & 0o777,0o755)
                self.assertEqual(path.stat().st_uid,0)
                self.assertEqual(subprocess.run(['runuser','-u','nobody','--','test','-x',str(path)]).returncode,0)
                self.assertNotEqual(subprocess.run(['runuser','-u','nobody','--','test','-w',str(path)]).returncode,0)
            for relative in ('data','data/upgrade-inbox','media','media/kamery'):
                self.assertEqual(subprocess.run(['runuser','-u','nobody','--','test','-w',str(root/relative)]).returncode,0)
            self.assertEqual((root/'data').stat().st_mode & 0o777,0o700)
            self.assertEqual((root/'control').stat().st_mode & 0o777,0o750)
            self.assertNotEqual(subprocess.run(['runuser','-u','nobody','--','test','-w',str(root/'control')]).returncode,0)
            subprocess.run(['runuser','-u','nobody','--','touch',str(root/'media/kamery/probe')],check=True)

    @unittest.skipUnless(os.geteuid() == 0, 'requires root-owned fixture')
    def test_retry_accepts_only_empty_early_install_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as name:
            parent=Path(name);root=parent/'sunhill-nvr';units=parent/'units';units.mkdir()
            for sub in ('releases/0.2.0/app/sunhill_nvr','recovery/app/sunhill_nvr','media/kamery','data/upgrade-inbox','control/upgrade','config','bin'):
                (root/sub).mkdir(parents=True,exist_ok=True)
            (root/'current').symlink_to('releases/0.2.0')
            (root/'releases/0.2.0/manifest.json').write_text('{}')
            binary=parent/'fake';binary.mkdir()
            scripts={'id':'#!/bin/sh\necho 1001\n','getent':'#!/bin/sh\nif [ "$1" = passwd ]; then echo sunhill:x:1001:1001::/home/sunhill:/usr/sbin/nologin; else echo sunhill:x:1001:; fi\n'}
            for key,script in scripts.items():
                path=binary/key;path.write_text(script);path.chmod(0o755)
            env=dict(os.environ,PATH=str(binary)+os.pathsep+os.environ['PATH'])
            command=['bash','-e','-c','. "$1"; validate_incomplete_install "$2" sunhill "$3"','bash',str(ROOT/'scripts/install_helpers.sh'),str(root),str(units)]
            def check(): return subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(check().returncode,0,check().stderr)
            for path in (root/'data/sunhill.sqlite',root/'media/kamery/original.mp4',root/'config/config.json',units/'sunhill-web.service'):
                path.write_bytes(b'keep')
                self.assertNotEqual(check().returncode,0,str(path))
                self.assertEqual(path.read_bytes(),b'keep')
                path.unlink()
            (root/'media/kamery/link').symlink_to('/tmp')
            self.assertNotEqual(check().returncode,0)
            self.assertTrue((root/'media/kamery/link').is_symlink())


if __name__ == '__main__':
    unittest.main()
