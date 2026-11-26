import unittest
import tempfile
import os
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.ftp_accounts import *
class FTPTests(unittest.TestCase):
 def test_password_length_boundary(self):
    from sunhill_nvr.security import PasswordValidationError
    for bad in ('12345','x'*129,'12345\n','12345\r','12345\0','12345\ud800'):
        with self.assertRaises(PasswordValidationError):make_hash(bad)
    for good in ('123456','abcdef','x'*128):
        self.assertRegex(make_hash(good),SHA512_CRYPT)
 def test_hash_and_account_modes(self):
    h=make_hash('secret-password-123')
    self.assertNotIn('secret-password-123',h)
    result=render([Account('common',h),Account('brama',h,'brama')],Path('/home/store/kamery'),1200,1200)
    self.assertIn(':/home/store/kamery:/usr/sbin/nologin',result)
    self.assertIn(':/home/store/kamery/brama:/usr/sbin/nologin',result)
 def test_rejects_injection_root_and_duplicates(self):
    h=make_hash('secret-password-123')
    for bad in ('x\nUser root','../x','a:b'):
        with self.assertRaises(ValueError):render([Account(bad,h)],Path('/srv/ftp'),1200,1200)
    with self.assertRaises(ValueError):render([Account('a',h)],Path('/srv/ftp'),0,0)
    with self.assertRaises(ValueError):render([Account('A',h),Account('a',h)],Path('/srv/ftp'),1200,1200)
 def test_config_path_injection_rejected(self):
    with self.assertRaises(ValueError):create_virtual_user_config(Path('/etc/ok\nUser root'),Path('/etc/group'))
 def test_render_group(self):
    h=make_hash('secret-password-123')
    self.assertEqual(render_group('cameras',1200,[Account('a',h),Account('b',h)]),'cameras:x:1200:a,b\n')
    with self.assertRaises(ValueError):render_group('root',0,[])
 def test_server_limits_ftp_to_private_ranges(self):
    config=render_server(Path('/var/lib/sunhill/ftp.passwd'),Path('/var/lib/sunhill/ftp.group'))
    self.assertIn('AuthOrder mod_auth_file.c',config);self.assertIn('DefaultRoot ~',config)
    self.assertIn('User sunhill\nGroup sunhill\n',config)
    self.assertIn('192.168.0.0/16',config);self.assertIn('<Limit DELE RMD',config)
    self.assertIn('SITE_RMDIR SITE_SYMLINK SITE_COPY',config);self.assertIn('RNTO APPE',config)
    self.assertIn('AllowOverride off',config);self.assertIn('HiddenStores .sunhill-upload- .%P',config)
    self.assertIn('DefaultTransferMode binary',config);self.assertIn('TransferOptions IgnoreASCII',config)
    with self.assertRaises(ValueError):render_server(Path('/x'),Path('/y'),passive_start=21)
 def test_login_acl_allows_loopback_and_private_networks_before_denying_others(self):
    conf=render_server(Path('/etc/proftpd/ftp.passwd'),Path('/etc/proftpd/ftp.group'))
    login=conf.split('<Limit LOGIN>\n',1)[1].split('</Limit>',1)[0]
    self.assertEqual(login.strip().splitlines(),['Order allow,deny','  Allow from 127.0.0.1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16','  Deny from all'])
    self.assertNotIn('DenyAll',login)
    self.assertIn('DenyAll',conf.split('<Limit DELE',1)[1])
 def test_unused_controls_socket_is_disabled_for_live_and_parser(self):
    for runtime in (Path('/run/sunhill-ftp'),Path('/opt/sunhill-nvr/control/config-test/validate-runtime-test')):
        conf=render_server(Path('/etc/proftpd/ftp.passwd'),Path('/etc/proftpd/ftp.group'),runtime_dir=runtime)
        self.assertIn('<IfModule mod_ctrls.c>\n  ControlsEngine off\n</IfModule>',conf)
        self.assertNotIn('/run/proftpd.sock',conf)
 def test_required_dso_modules_loaded_before_transfer_directives(self):
    auth=Path('/opt/sunhill-nvr/control/config-test/ftp.passwd')
    for runtime in (Path('/run/sunhill-ftp'),auth.parent/'validate-runtime-test'):
        conf=render_server(auth,auth.with_name('ftp.group'),runtime_dir=runtime)
        for module in ('mod_xfer','mod_ls'):
            stanza=f'<IfModule !{module}.c>\n  LoadModule {module}.c\n</IfModule>\n'
            self.assertIn(stanza,conf)
            self.assertLess(conf.index(stanza),conf.index('AllowOverwrite off'))
        self.assertIn('<IfModule mod_dso.c>\n  ModulePath /usr/lib/proftpd\n</IfModule>',conf)
        self.assertIn('AllowOverwrite off',conf)
        self.assertIn('HiddenStores .sunhill-upload- .%P',conf)
        self.assertNotIn('Include /etc/proftpd/',conf)
 def test_validation_runtime_override_is_scoped_and_validated(self):
    auth=Path('/opt/sunhill-nvr/control/config-test/ftp.passwd');group=auth.with_name('ftp.group')
    runtime=auth.parent/'validate-runtime-test'
    config=render_server(auth,group,runtime_dir=runtime)
    self.assertIn(f'PidFile {runtime}/proftpd.pid',config)
    self.assertIn(f'ScoreboardFile {runtime}/proftpd.scoreboard',config)
    self.assertNotIn('/run/sunhill-ftp/',config)
    for invalid in ('relative','/','/tmp/../etc','/tmp/evil\nUser root','/tmp/with space'):
        with self.assertRaises(ValueError):render_server(auth,group,runtime_dir=Path(invalid))
 def test_prepare_missing_home_preserves_existing_and_rejects_escapes(self):
    h=make_hash('secret-password-123')
    with tempfile.TemporaryDirectory() as t:
        archive=Path(t)/'archive';archive.mkdir();outside=Path(t)/'outside';outside.mkdir()
        existing=archive/'existing';existing.mkdir(mode=0o700)
        accounts=render([Account('common',h),Account('new',h,'new'),Account('old',h,'existing')],archive,1200,1200)
        prepare_homes(archive,accounts,lambda:True,uid=1200,gid=1200)
        self.assertTrue((archive/'new').is_dir());self.assertEqual(existing.stat().st_mode&0o777,0o700)
        (archive/'escape').symlink_to(outside,target_is_directory=True)
        bad=render([Account('bad',h,'escape')],archive,1200,1200)
        with self.assertRaises(OSError):prepare_homes(archive,bad,lambda:True,uid=1200,gid=1200)
        with self.assertRaises(ValueError):prepare_homes(archive,bad.replace(str(archive/'escape'),str(outside)),lambda:True,uid=1200,gid=1200)
        self.assertEqual(list(outside.iterdir()),[])
        new=render([Account('new',h,'later')],archive,1200,1200)
        states=iter([True,True,False])
        with self.assertRaises(RuntimeError):prepare_homes(archive,new,lambda:next(states),uid=1200,gid=1200)
        self.assertFalse((archive/'later').exists())
 @unittest.skipUnless(os.geteuid()==0,'root-owned snapshot fixture requires root')
 def test_read_snapshot_rejects_symlink_and_writable_account_file(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);version=root/'config-test';version.mkdir();(root/'current').symlink_to(version.name)
        account=version/'ftp.passwd';account.write_text('example');account.chmod(0o640)
        self.assertEqual(read_published_accounts(root),'example')
        account.chmod(0o666)
        with self.assertRaises(ValueError):read_published_accounts(root)
        account.unlink();account.symlink_to('/etc/passwd')
        with self.assertRaises(OSError):read_published_accounts(root)
if __name__=='__main__':unittest.main()
