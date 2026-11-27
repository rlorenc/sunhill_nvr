import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sunhill_nvr import face_profiles as f, analytics_models as m, database, security, local_analytics as a

def vector(i):return [int(j==i) for j in range(f.DIM)]

class FaceProfilesTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.execute('PRAGMA foreign_keys=ON');security.initialize(self.db);database.migrate(self.db);self.addCleanup(self.db.close)
    def test_enrollment_model_ambiguity_and_removal(self):
        model='a'*64
        one=f.enroll(self.db,'sample A',[vector(0)],model_id=model)
        two=f.enroll(self.db,'sample B',[vector(1)],model_id=model)
        templates=f.active_templates(self.db,model)
        self.assertEqual(f.best_match(vector(0),templates),one)
        self.assertIsNone(f.best_match(vector(2),templates))
        mixed=[x+y for x,y in zip(vector(0),vector(1))];self.assertIsNone(f.best_match(mixed,templates))
        self.assertEqual(f.active_templates(self.db,'b'*64),[])
        with self.assertRaises(ValueError):f.enroll(self.db,'bad',[vector(2)],model_id='b'*64)
        self.assertTrue(f.remove(self.db,one));self.assertEqual(f.labels(self.db),{two:'sample B'})
        self.assertEqual(self.db.execute('SELECT count(*) FROM face_templates').fetchone(),(1,))
    def test_labels_are_administrator_only_and_revocable(self):
        ident=f.enroll(self.db,'sample',[vector(0)],model_id='a'*64)
        raw=[{'id':'1','kind':'face','box':[0,0,.1,.1],'face_profile':ident}]
        self.assertNotIn('face_profile',f.public_objects(raw)[0]);self.assertNotIn('face_match',f.public_objects(raw)[0]);self.assertEqual(f.public_objects(raw,f.labels(self.db))[0]['face_match'],'sample')
        f.remove(self.db,ident);self.assertNotIn('face_match',f.public_objects(raw,f.labels(self.db))[0]);self.assertIn('face_profile',raw[0])
    def test_face_match_requires_two_consecutive_frames(self):
        t=a.Tracks();box=[.1,.1,.2,.2];first=t.observe([('face',box,'a')],1000)[0];self.assertNotIn('face_profile',first)
        second=t.observe([('face',box,'a')],2000)[0];self.assertEqual(first['id'],second['id']);self.assertEqual(second['face_profile'],'a')
        self.assertNotIn('face_profile',t.observe([('face',box,'b')],3000)[0]);self.assertNotIn('face_profile',t.observe([('face',box,None)],4000)[0])
    def test_inventory_and_authorized_removal(self):
        ident=f.enroll(self.db,'sample',[vector(0),vector(1)],model_id='a'*64)
        self.assertEqual(f.inventory(self.db),[{'id':ident,'label':'sample','samples':2}])
        def denied(db):raise PermissionError('revoked')
        with self.assertRaises(PermissionError):f.remove(self.db,ident,authorize=denied)
        self.assertEqual(len(f.inventory(self.db)),1)
        uid=security.create_user(self.db,'administrator','password-for-tests','admin')
        self.assertTrue(f.remove(self.db,ident,authorize=lambda db:uid))
        self.assertEqual(f.inventory(self.db),[])
        self.assertEqual(self.db.execute("SELECT user_id FROM audit_log WHERE action='face.remove'").fetchone(),(uid,))

    def test_web_image_headers_and_limits(self):
        import base64
        png=lambda w,h:b'\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR'+w.to_bytes(4,'big')+h.to_bytes(4,'big')+b'\x00'*9
        jpg=b'\xff\xd8\xff\xc0\x00\x0b\x08\x00\x64\x00\x80\x01\x01\x11\x00'
        self.assertEqual(f.image_dimensions(jpg),(128,100));self.assertEqual(f.image_dimensions(png(4096,32)),(4096,32))
        for raw in (b'not an image',png(5000,100),png(100,0),jpg[:7]):
            with self.assertRaises(ValueError):f.image_dimensions(raw)
        good=base64.b64encode(png(100,100)).decode()
        self.assertEqual(f.web_images([good]),[png(100,100)])
        for bad in ([],[good]*6,['!'],[42],None):
            with self.assertRaises(ValueError):f.web_images(bad)

    def test_update_preserves_profile_on_revoked_authorization(self):
        ident=f.enroll(self.db,'first',[vector(0)],model_id='a'*64)
        def denied(db):raise PermissionError('revoked')
        with self.assertRaises(PermissionError):f.enroll(self.db,'second',[vector(1)],model_id='a'*64,profile_id=ident,authorize=denied)
        self.assertEqual(f.labels(self.db),{ident:'first'});self.assertEqual(f.active_templates(self.db,'a'*64),[(ident,vector(0))])

    def test_invalid_features(self):
        for raw in ([0]*128,[1]*127,[float('nan')]*128):
            with self.assertRaises(ValueError):f.normalized(raw)

class ModelInstallTests(unittest.TestCase):
    def test_bad_hash_and_failed_inference_keep_old_model(self):
        # /tmp is writable, so test only the transactional part with path-policy
        # stub. Actual production validates every ancestor, including /tmp.
        with tempfile.TemporaryDirectory() as d,patch.object(m,'trusted_directory'),patch.object(m,'trusted_model'),patch.object(m.os,'geteuid',return_value=0):
            root=Path(d)/'models';source=Path(d)/'input';source.write_bytes(b'first');digest=hashlib.sha256(b'first').hexdigest();seen=[]
            installed=m.install('coco',source,digest,root=root,verify=lambda kind,path:seen.append(path.read_bytes()))
            self.assertEqual(installed.read_bytes(),b'first');self.assertEqual(seen,[b'first'])
            source.write_bytes(b'second')
            with self.assertRaises(ValueError):m.install('coco',source,digest,root=root,verify=lambda *a:None)
            def fail(*args):raise RuntimeError('bad network')
            with self.assertRaises(RuntimeError):m.install('coco',source,hashlib.sha256(b'second').hexdigest(),root=root,verify=fail)
            self.assertEqual(installed.read_bytes(),b'first');self.assertEqual(list(root.iterdir()),[installed])
    def test_untrusted_directory(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):m.trusted_directory(Path(d))

    def test_model_status_missing_untrusted_and_verified(self):
        with tempfile.TemporaryDirectory() as d,patch.object(m,'trusted_directory'):
            root=Path(d);model=root/m.FILES['coco'];model.write_bytes(b'test');model.chmod(0o644)
            bad=root/m.FILES['face-detector'];bad.symlink_to(model)
            with patch.object(m,'trusted_model',wraps=m.trusted_model) as trust,patch.object(m,'verify_network') as verify:
                # The test may run non-root in CI; fake only model ownership.
                def validate(path):
                    if path.is_symlink():raise ValueError('symlink')
                    if not path.exists():raise FileNotFoundError(path)
                    return path
                trust.side_effect=validate
                result=m.status(root=root)
                self.assertEqual(result,{'coco':{'state':'present','bytes':4},'face-detector':{'state':'untrusted'},'face-recognizer':{'state':'missing'}})
                verify.assert_not_called()
                self.assertEqual(m.status(root=root,verify=True)['coco']['inference'],'passed')
                verify.assert_called_once_with('coco',model)
                verify.side_effect=RuntimeError('invalid network')
                self.assertEqual(m.status(root=root,verify=True)['coco']['inference'],'failed')

    def test_generation_detects_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as d,patch.object(m,'trusted_model',side_effect=lambda p:p):
            root=Path(d);path=root/m.FILES['coco'];initial=m.generation(root)
            path.write_bytes(b'old');old=m.generation(root)
            new=root/'new';new.write_bytes(b'new');new.replace(path)
            self.assertNotEqual(initial,old);self.assertNotEqual(old,m.generation(root))

    def test_pinned_fetch_validates_both_before_install(self):
        files={'face-detector':b'first model','face-recognizer':b'second model'}
        sources={kind:(kind+'.onnx',len(data),hashlib.sha256(data).hexdigest()) for kind,data in files.items()}
        with tempfile.TemporaryDirectory() as d,patch.object(m,'trusted_directory'),patch.object(m,'trusted_model'),patch.object(m.os,'geteuid',return_value=0),patch.object(m,'FACE_SOURCES',sources):
            root=Path(d)/'models';downloads=[]
            def download(url,path,size):
                downloads.append(url);kind=next(k for k in files if url.endswith('/'+k+'.onnx'));path.write_bytes(files[kind] if kind=='face-detector' else b'wrong')
            with self.assertRaises(ValueError):m.fetch_face_models(accept_licenses=True,root=root,download=download,verify=lambda *a:None)
            self.assertEqual(list(root.iterdir()),[])
            def good(url,path,size):
                kind=next(k for k in files if url.endswith('/'+k+'.onnx'));path.write_bytes(files[kind]);downloads.append(url)
            result=m.fetch_face_models(accept_licenses=True,root=root,download=good,verify=lambda *a:None)
            self.assertEqual({k:p.read_bytes() for k,p in result.items()},files)
            self.assertTrue(all(m.FACE_REVISION in url for url in downloads))
            result['face-detector'].write_bytes(b'custom')
            with self.assertRaises(ValueError):m.fetch_face_models(accept_licenses=True,root=root,download=good,verify=lambda *a:None)
            with self.assertRaises(ValueError):m.fetch_face_models(accept_licenses=False,root=root,download=good,verify=lambda *a:None)
            self.assertEqual(result['face-detector'].read_bytes(),b'custom')
