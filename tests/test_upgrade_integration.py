from contextlib import closing
import sqlite3
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from sys import path
from types import SimpleNamespace
from unittest.mock import patch

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import upgrade_admin
from sunhill_nvr.build_release import build
from sunhill_nvr.release import ReleaseError
from sunhill_nvr.upgrade_transaction import Operations
from sunhill_nvr import upgrade_inbox


class UpgradeIntegrationTests(unittest.TestCase):
    def test_upgrade_messages_are_specific_and_never_expose_exception_data(self):
        try:
            try:raise ReleaseError('new unit conflicts with existing configuration')
            except ReleaseError as cause:raise ReleaseError('upgrade failed; previous version restored') from cause
        except ReleaseError as error:
            self.assertIn('koliduje',upgrade_admin.failure_message(error))
        private='rtsp://admin:secret@camera/path'
        self.assertNotIn(private,upgrade_admin.failure_message(ReleaseError(private)))
        self.assertNotIn(private,upgrade_admin.failure_message(RuntimeError(private)))

    def test_signed_inbox_update_migrates_and_cleans_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();inbox=root/'inbox';inbox.mkdir(mode=0o700)
            versions=root/'releases';versions.mkdir();old=versions/'1.0.0';old.mkdir();(old/'manifest.json').write_text('{}')
            current=root/'current';current.symlink_to('releases/1.0.0')
            source=root/'src/sunhill_nvr';source.mkdir(parents=True);(source/'__init__.py').write_text('VERSION=2\n')
            key=root/'private';public=root/'public';package=root/'update.zip';signature=root/'update.sig'
            subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(key)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(public)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            build(source.parent,'1.1.0',key,package,signature)
            aid=upgrade_inbox.store(inbox,'archive',io.BytesIO(package.read_bytes()),package.stat().st_size)
            sid=upgrade_inbox.store(inbox,'signature',io.BytesIO(signature.read_bytes()),signature.stat().st_size)
            upgrade_inbox.request(inbox,aid,sid,1)
            state_flags={'fenced':False,'running':True,'value':1}
            def fence():state_flags['fenced']=True
            def unfence():state_flags['fenced']=False
            def stop():state_flags['running']=False
            def assert_stopped():self.assertFalse(state_flags['running'])
            def start():state_flags['running']=True
            def healthy():return state_flags['running'] and state_flags['fenced']
            def backup(destination):destination.write_text(str(state_flags['value']))
            def restore(source):state_flags['value']=int(source.read_text())
            def migrate(candidate):state_flags['value']=2
            ops=Operations(fence,unfence,stop,assert_stopped,start,healthy,backup,restore,migrate)
            adapter=SimpleNamespace(uid=os.geteuid(),gid=os.getegid(),state=state,operations=lambda:ops);statuses=[]
            with patch.multiple(upgrade_admin,STATE=state,INBOX=inbox,VERSIONS=versions,CURRENT=current,PUBLIC_KEY=public),patch.object(upgrade_admin,'trusted_file'),patch.object(upgrade_admin,'_status',side_effect=lambda a,s,m:statuses.append(s)):
                self.assertTrue(upgrade_admin.process_inbox(adapter))
            self.assertEqual(statuses,['verifying','applying','done'])
            self.assertEqual(current.resolve().name,'1.1.0');self.assertEqual(state_flags['value'],2)
            self.assertFalse(state_flags['fenced']);self.assertFalse((inbox/'request.json').exists());self.assertEqual(list(inbox.glob('upload-*')),[])
    @unittest.skipUnless(os.geteuid()==0,'root-owned release policy')
    def test_unsigned_approved_package_uses_existing_transaction_and_rolls_back(self):
        from sunhill_nvr import approved_update
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from package_release import write_tar
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();inbox=root/'inbox';inbox.mkdir(mode=0o700);approved=root/'approved';approved.mkdir()
            versions=root/'releases';versions.mkdir();old=versions/'0.2.0';old.mkdir();(old/'manifest.json').write_text('{}')
            current=root/'current';current.symlink_to('releases/0.2.0')
            flags={'fenced':False,'running':True,'value':1,'fail':False}
            def migrate(candidate):flags['value']=2
            def restore(src):flags['value']=int(src.read_text())
            def health():return flags['running'] and flags['fenced'] and not (flags['fail'] and current.resolve().name=='0.2.2')
            ops=Operations(lambda:flags.update(fenced=True),lambda:flags.update(fenced=False),lambda:flags.update(running=False),lambda:self.assertFalse(flags['running']),lambda:flags.update(running=True),health,lambda dest:dest.write_text(str(flags['value'])),restore,migrate)
            adapter=SimpleNamespace(uid=0,gid=0,state=state,operations=lambda:ops);statuses=[]
            with patch.multiple(upgrade_admin,STATE=state,INBOX=inbox,VERSIONS=versions,CURRENT=current),patch.object(approved_update,'ROOT',approved),patch.object(upgrade_admin,'trusted_directory'),patch.object(upgrade_admin,'_status',side_effect=lambda a,s,m:statuses.append(s)):
                for version in ('0.2.1','0.2.2'):
                    package=approved/('sunhill-nvr-'+version+'.tar.gz')
                    write_tar(package,{'src/sunhill_nvr/__init__.py':('__version__ = "'+version+'"\n').encode()},version)
                    approved_update.submit(inbox,package.name,1)
                    flags['fail']=version=='0.2.2'
                    self.assertTrue(upgrade_admin.process_approved(adapter))
                    self.assertFalse((inbox/approved_update.REQUEST).exists())
            self.assertEqual(current.resolve().name,'0.2.1');self.assertFalse(flags['fenced']);self.assertEqual(statuses,['verifying','applying','done','verifying','applying','failed'])

    def test_inbox_rejects_bad_signature_and_cleans_only_its_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();inbox=root/'inbox';inbox.mkdir(mode=0o700)
            aid=upgrade_inbox.store(inbox,'archive',io.BytesIO(b'zip'),3)
            sid=upgrade_inbox.store(inbox,'signature',io.BytesIO(b'sig'),3)
            upgrade_inbox.request(inbox,aid,sid,1);statuses=[]
            adapter=SimpleNamespace(uid=os.geteuid(),gid=os.getegid(),state=state,operations=lambda:None)
            with patch.multiple(upgrade_admin,STATE=state,INBOX=inbox),patch.object(upgrade_admin,'trusted_file'),patch.object(upgrade_admin,'stage_verified',side_effect=ReleaseError('invalid signature')),patch.object(upgrade_admin,'_status',side_effect=lambda a,s,m:statuses.append(s)):
                self.assertTrue(upgrade_admin.process_inbox(adapter))
            self.assertEqual(statuses,['verifying','failed'])
            self.assertFalse((inbox/'request.json').exists());self.assertEqual(list(inbox.glob('upload-*')),[])

    def test_inbox_reports_invalid_signature_without_echoing_uploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();inbox=root/'inbox';inbox.mkdir(mode=0o700)
            aid=upgrade_inbox.store(inbox,'archive',io.BytesIO(b'zip'),3);sid=upgrade_inbox.store(inbox,'signature',io.BytesIO(b'sig'),3)
            upgrade_inbox.request(inbox,aid,sid,1);statuses=[]
            adapter=SimpleNamespace(uid=os.geteuid(),gid=os.getegid(),state=state,operations=lambda:None)
            with patch.multiple(upgrade_admin,STATE=state,INBOX=inbox),patch.object(upgrade_admin,'trusted_file'),patch.object(upgrade_admin,'stage_verified',side_effect=ReleaseError('release signature is invalid')),patch.object(upgrade_admin,'_status',side_effect=lambda a,s,m:statuses.append((s,m))):
                upgrade_admin.process_inbox(adapter)
            self.assertEqual(statuses[-1][0],'failed');self.assertIn('Podpis',statuses[-1][1]);self.assertNotIn(str(root),statuses[-1][1])

    def test_inbox_does_not_remove_a_replaced_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();inbox=root/'inbox';inbox.mkdir(mode=0o700)
            aid=upgrade_inbox.store(inbox,'archive',io.BytesIO(b'zip'),3);sid=upgrade_inbox.store(inbox,'signature',io.BytesIO(b'sig'),3)
            upgrade_inbox.request(inbox,aid,sid,1)
            adapter=SimpleNamespace(uid=os.geteuid(),gid=os.getegid(),state=state,operations=lambda:None)
            def replace_request(*args,**kwargs):
                replacement=inbox/'new-request';replacement.write_text(json.dumps({'archive':aid,'signature':sid,'user_id':2}));os.replace(replacement,inbox/'request.json')
                raise ReleaseError('invalid signature')
            with patch.multiple(upgrade_admin,STATE=state,INBOX=inbox),patch.object(upgrade_admin,'trusted_file'),patch.object(upgrade_admin,'stage_verified',side_effect=replace_request),patch.object(upgrade_admin,'_status'):
                upgrade_admin.process_inbox(adapter)
            self.assertEqual(upgrade_inbox.read_request(inbox)['user_id'],2)
    def test_signed_package_migration_and_bad_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir();versions=root/'releases';versions.mkdir()
            old=versions/'1.0.0';old.mkdir();(old/'manifest.json').write_text('{}')
            current=root/'current';current.symlink_to('releases/1.0.0')
            source=root/'src/sunhill_nvr';source.mkdir(parents=True);(source/'__init__.py').write_text('VERSION=2\n')
            key=root/'private';public=root/'public';package=root/'update.zip';signature=root/'update.sig'
            subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(key)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(public)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            build(source.parent,'1.1.0',key,package,signature)
            db=root/'database.sqlite'
            with closing(sqlite3.connect(db)) as connection, connection:connection.execute('CREATE TABLE settings (value INTEGER)');connection.execute('INSERT INTO settings VALUES(1)')
            flags={'fenced':False,'running':True}
            def fence():flags['fenced']=True
            def unfence():flags['fenced']=False
            def stop():flags['running']=False
            def assert_stopped():
                if flags['running']:raise RuntimeError('service still running')
            def start():flags['running']=True
            def healthy():return flags['running'] and flags['fenced']
            def backup(destination):
                with closing(sqlite3.connect(db)) as src, src,closing(sqlite3.connect(destination)) as dst, dst:src.backup(dst)
            def restore(srcpath):
                with closing(sqlite3.connect(srcpath)) as src, src,closing(sqlite3.connect(db)) as dst, dst:src.backup(dst)
            def migrate(candidate):
                if not flags['fenced'] or flags['running']:raise RuntimeError('unsafe migration')
                with closing(sqlite3.connect(db)) as connection, connection:connection.execute('UPDATE settings SET value=2')
            ops=Operations(fence,unfence,stop,assert_stopped,start,healthy,backup,restore,migrate)
            adapter=SimpleNamespace(state=state,operations=lambda:ops)
            with patch.multiple(upgrade_admin,STATE=state,VERSIONS=versions,CURRENT=current,PUBLIC_KEY=public),patch.object(upgrade_admin,'context',return_value=adapter),patch.object(upgrade_admin,'trusted_file'):
                bad=root/'bad.zip';bad.write_bytes(package.read_bytes()+b'bad')
                with self.assertRaises(ReleaseError):upgrade_admin.main(['apply',str(bad),str(signature)])
                self.assertEqual(current.resolve().name,'1.0.0');self.assertTrue(flags['running']);self.assertFalse(flags['fenced'])
                self.assertEqual(upgrade_admin.main(['apply',str(package),str(signature)]),0)
            with closing(sqlite3.connect(db)) as connection, connection:self.assertEqual(connection.execute('SELECT value FROM settings').fetchone()[0],2)
            self.assertEqual(current.resolve().name,'1.1.0');self.assertTrue(flags['running']);self.assertFalse(flags['fenced'])


if __name__=='__main__':unittest.main()
