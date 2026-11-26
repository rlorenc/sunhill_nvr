import os,subprocess,tempfile,unittest
from pathlib import Path
from sys import path
from unittest.mock import Mock
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.apparmor_ftp import configure,rules,merged

class PolicyTests(unittest.TestCase):
 def test_rules_are_scoped_and_reject_pattern_injection(self):
    text=rules(Path('/mnt/recordings'),Path('/opt/sunhill-nvr/control'))
    self.assertIn('"/mnt/recordings/**" rw,',text)
    self.assertIn('"/opt/sunhill-nvr/control/config-*/validate-proftpd.conf" r,',text)
    self.assertNotIn('/opt/**',text);self.assertNotIn('/opt/sunhill-nvr/data',text)
    for p in ('/mnt/*','/mnt/{one,two}','/mnt/@{HOME}','/mnt/evil"','/mnt/a\\b'):
        with self.assertRaises(ValueError):rules(Path(p),Path('/opt/sunhill-nvr/control'))
 def test_validation_runtime_rules_do_not_grant_config_write(self):
    text=rules(Path('/mnt/recordings'),Path('/opt/sunhill-nvr/control'))
    for prefix in ('','/att/proftpd'):
        self.assertIn(f'"{prefix}/opt/sunhill-nvr/control/config-*/validate-runtime-*/proftpd.scoreboard*" rwk,',text)
        self.assertIn(f'"{prefix}/opt/sunhill-nvr/control/config-*/validate-runtime-*/proftpd.pid" rwk,',text)
        self.assertIn(f'"{prefix}/opt/sunhill-nvr/control/config-*/ftp.passwd" r,',text)
        self.assertNotIn(f'"{prefix}/opt/sunhill-nvr/control/config-*/**" rw',text)
 def test_module_rules_are_read_and_map_only(self):
    text=rules(Path('/mnt/recordings'),Path('/opt/sunhill-nvr/control'))
    for prefix in ('','/att/proftpd'):
        for module in ('mod_xfer','mod_ls'):
            self.assertIn(f'{prefix}/usr/lib/proftpd/{module}.so mr,',text)
            self.assertIn(f'{prefix}/usr/lib/proftpd/{module}.la r,',text)
        self.assertNotIn(f'{prefix}/usr/lib/proftpd/**',text)
 def test_merge_preserves_local_custom_rules_and_replaces_archive(self):
    a=rules(Path('/mnt/a'),Path('/opt/sunhill-nvr/control'));b=rules(Path('/mnt/b'),Path('/opt/sunhill-nvr/control'))
    old=merged('# custom\n',a);self.assertEqual(merged(old,b),'# custom\n'+b);self.assertNotIn('/mnt/a/',merged(old,b))
 @unittest.skipUnless(os.geteuid()==0,'root ownership fixture')
 def test_reload_and_restore_on_failure(self):
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp);(base/'local').mkdir();(base/'proftpd').write_text('include if exists <local/proftpd>\n');target=base/'local/proftpd';target.write_text('# keep\n');stamp=base/'stamp';run=Mock()
        self.assertTrue(configure(Path('/mnt/a'),Path('/opt/sunhill-nvr/control'),profiles=base,run=run,stamp=stamp));before=target.read_text();run.assert_called_once()
        configure(Path('/mnt/a'),Path('/opt/sunhill-nvr/control'),profiles=base,run=run,stamp=stamp);run.assert_called_once()
        run.side_effect=subprocess.CalledProcessError(1,['parser'])
        with self.assertRaises(RuntimeError):configure(Path('/mnt/b'),Path('/opt/sunhill-nvr/control'),profiles=base,run=run,stamp=stamp)
        self.assertEqual(target.read_text(),before)
 def test_absent_profile_does_not_modify_system(self):
    with tempfile.TemporaryDirectory() as tmp:
        run=Mock();self.assertFalse(configure(Path('/mnt/a'),Path('/opt/sunhill-nvr/control'),profiles=Path(tmp),run=run));run.assert_not_called()
