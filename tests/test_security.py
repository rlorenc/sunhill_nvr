import sqlite3, unittest
from pathlib import Path
from sys import path
path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sunhill_nvr.security import *

class PasswordFormTests(unittest.TestCase):
    def test_password_forms_match_backend_limits(self):
        from html.parser import HTMLParser
        class Parser(HTMLParser):
            form=None
            fields=[]
            def handle_starttag(self,tag,attrs):
                a=dict(attrs)
                if tag=='form':self.form=a.get('id')
                if self.form in ('setup-form','own-password-form','user-form','ftp-form') and tag=='input' and a.get('type')=='password' and a.get('autocomplete')=='new-password':
                    self.fields.append((self.form,a.get('minlength'),a.get('maxlength')))
            def handle_endtag(self,tag):
                if tag=='form':self.form=None
        parser=Parser();parser.feed((Path(__file__).resolve().parents[1]/'src/sunhill_nvr/static/index.html').read_text())
        self.assertCountEqual(parser.fields,[('setup-form','6','1024')]*2+[('own-password-form','6','1024')]*2+[('user-form','6','1024')])

class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:'); initialize(self.db)
        self.uid=create_user(self.db,'rafal','very-long-test-password','viewer')
        self.db.execute("INSERT INTO cameras VALUES (1,'brama','Brama',1)")
        self.db.execute("INSERT INTO cameras VALUES (2,'dom','Dom',1)")
        self.db.execute('INSERT INTO camera_permissions VALUES (?,1,0)',(self.uid,)); self.db.commit()
    def tearDown(self): self.db.close()
    def test_invalid_unicode_password_is_rejected_cleanly(self):
        from sunhill_nvr import security
        with self.assertRaises(ValueError):security.password_hash('x'*12+'\ud800')
        self.assertFalse(security.password_verify('x'*12+'\ud800','pbkdf2_sha256$600000$'+'00'*16+'$'+'00'*32))
    def test_account_change_during_password_check_cannot_issue_session(self):
        from unittest.mock import patch
        def verify_then_disable(password,stored):
            self.db.execute('UPDATE users SET enabled=0 WHERE id=?',(self.uid,));self.db.commit();return True
        with patch('sunhill_nvr.security.password_verify',side_effect=verify_then_disable):
            with self.assertRaises(AuthError):authenticate(self.db,'rafal','very-long-test-password','client',now=100)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
    def test_password_length_boundary_and_legacy_hash(self):
        for value in ('', '12345', 'x'*1025, None):
            with self.assertRaisesRegex(PasswordValidationError,'6 do 1024'):password_hash(value)
        for value in ('123456','abcdef','żółćąę','x'*1024):
            digest=password_hash(value)
            self.assertTrue(password_verify(value,digest))
            self.assertIn('$600000$',digest)
        self.assertTrue(password_verify('very-long-test-password',self.db.execute('SELECT password_hash FROM users WHERE id=?',(self.uid,)).fetchone()[0]))
    def test_password_salts_and_verify(self):
        a=password_hash('correct horse battery staple');b=password_hash('correct horse battery staple')
        self.assertNotEqual(a,b);self.assertTrue(password_verify('correct horse battery staple',a))
        self.assertFalse(password_verify('wrong',a));self.assertFalse(password_verify('x','bogus'))
    def test_session_csrf_permissions_and_expiry(self):
        s=authenticate(self.db,'rafal','very-long-test-password','192.0.2.1',now=1000)
        user=get_user(self.db,s.token,now=1001)
        self.assertTrue(verify_csrf(user,s.csrf));self.assertFalse(verify_csrf(user,'wrong'))
        self.assertTrue(may_access(self.db,user,1));self.assertFalse(may_access(self.db,user,1,download=True))
        self.assertFalse(may_access(self.db,user,2));self.assertIsNone(get_user(self.db,s.token,now=s.expires))
        logout(self.db,s.token);self.assertIsNone(get_user(self.db,s.token,now=1001))
    def test_login_rate_limit_and_disabled_user(self):
        with self.assertRaises(AuthError): authenticate(self.db,'rafal','bad','client',now=100)
        with self.assertRaisesRegex(AuthError,'later'): authenticate(self.db,'rafal','very-long-test-password','client',now=100)
        self.db.execute('UPDATE users SET enabled=0 WHERE id=?',(self.uid,));self.db.commit()
        with self.assertRaises(AuthError): authenticate(self.db,'rafal','very-long-test-password','client',now=102)
    def test_setup_attempts_are_rate_limited_by_client(self):
        reserve_setup_attempt(self.db,'client',now=100)
        with self.assertRaises(AuthError):reserve_setup_attempt(self.db,'client',now=100)
        reserve_setup_attempt(self.db,'other-client',now=100)
        reserve_setup_attempt(self.db,'client',now=101)
    def test_parallel_attempt_is_rate_limited_before_hash_finishes(self):
        import tempfile,threading
        from unittest.mock import patch
        import sunhill_nvr.security as sec
        with tempfile.TemporaryDirectory() as directory:
            filename=str(Path(directory)/'test.sqlite')
            setup=sqlite3.connect(filename);sec.initialize(setup);sec.create_user(setup,'user','password-for-testing','viewer');setup.close()
            hashing=threading.Event();release=threading.Event();errors=[]
            def slow_verify(password,stored):
                hashing.set();release.wait(timeout=5);return False
            def attempt():
                connection=sqlite3.connect(filename)
                try:sec.authenticate(connection,'user','incorrect','client',now=100)
                except sec.AuthError as e:errors.append(str(e))
                finally:connection.close()
            with patch.object(sec,'password_verify',slow_verify):
                thread=threading.Thread(target=attempt);thread.start();self.assertTrue(hashing.wait(timeout=3))
                connection=sqlite3.connect(filename)
                try:
                    with self.assertRaisesRegex(sec.AuthError,'later'):sec.authenticate(connection,'user','incorrect','client',now=100)
                finally:connection.close();release.set();thread.join(timeout=5)
            self.assertEqual(errors,['Invalid credentials'])
if __name__=='__main__': unittest.main()
