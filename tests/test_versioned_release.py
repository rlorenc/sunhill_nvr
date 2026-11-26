import io,json,os,tarfile,tempfile,unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from package_release import write_tar,require_release_gate
from sunhill_nvr import approved_update,release

@unittest.skipUnless(os.geteuid()==0,'root-owned release policy')
class VersionedReleaseTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.versions=self.root/'versions';self.versions.mkdir()
    self.inbox=self.root/'inbox';self.inbox.mkdir(mode=0o700);self.approved=self.root/'approved';self.approved.mkdir()
    self.package=self.approved/'sunhill-nvr-0.2.0.tar.gz'
    self.files={'src/sunhill_nvr/__init__.py':b'__version__ = "0.2.0"\n','src/sunhill_nvr/static/index.html':b'hello','install.sh':b'ignored'}
 def tearDown(self):self.temp.cleanup()
 def test_unsigned_package_stages_only_application_and_is_reusable(self):
    write_tar(self.package,self.files,'0.2.0')
    staged=release.stage_unsigned_tar(self.package,self.versions)
    self.assertEqual(staged.name,'0.2.0');self.assertEqual((staged/'app/sunhill_nvr/static/index.html').read_bytes(),b'hello')
    self.assertFalse((staged/'install.sh').exists());self.assertEqual(release.stage_unsigned_tar(self.package,self.versions,reuse_existing=True),staged)
 def test_versioned_root_and_legacy_compatibility(self):
    write_tar(self.package,self.files,'0.2.0')
    with tarfile.open(self.package,'r:gz') as tar:
        self.assertEqual({e.name.split('/')[0] for e in tar}, {'sunhill-nvr-0.2.0'})
    legacy=self.approved/'legacy.tar.gz'
    with tarfile.open(self.package,'r:gz') as source,tarfile.open(legacy,'w:gz') as dest:
        for e in source:
            with source.extractfile(e) as handle:
                e.name=e.name.replace('sunhill-nvr-0.2.0/','sunhill-nvr/',1)
                dest.addfile(e,handle)
    self.assertEqual(release.stage_unsigned_tar(legacy,self.versions).name,'0.2.0')
 def test_mixed_or_mismatched_root_is_rejected(self):
    write_tar(self.package,self.files,'0.2.0')
    for mixed in (True,False):
        invalid=self.approved/'invalid.tar.gz'
        with tarfile.open(self.package,'r:gz') as source,tarfile.open(invalid,'w:gz') as dest:
            for i,e in enumerate(source):
                with source.extractfile(e) as handle:
                    if not mixed or i==0:e.name=e.name.replace('sunhill-nvr-0.2.0/','sunhill-nvr-9.9.9/',1)
                    dest.addfile(e,handle)
        with self.assertRaises(release.ReleaseError):release.stage_unsigned_tar(invalid,self.versions)
        self.assertFalse(list(self.versions.iterdir()))
 def test_version_mismatch_is_rejected(self):
    write_tar(self.package,self.files,'0.2.1')
    with self.assertRaises(release.ReleaseError):release.stage_unsigned_tar(self.package,self.versions)
    self.assertFalse(list(self.versions.iterdir()))
 def test_member_traversal_and_symlinks_rejected(self):
    for name,kind in [('sunhill-nvr/../bad',tarfile.REGTYPE),('sunhill-nvr/link',tarfile.SYMTYPE)]:
        with tarfile.open(self.package,'w:gz') as tar:
            entry=tarfile.TarInfo(name);entry.type=kind;entry.linkname='/etc/passwd';tar.addfile(entry,io.BytesIO())
        with self.assertRaises(release.ReleaseError):release.stage_unsigned_tar(self.package,self.versions)
        self.assertFalse(list(self.versions.iterdir()))
 def test_writable_package_or_unapproved_name_rejected(self):
    write_tar(self.package,self.files,'0.2.0');self.package.chmod(0o666)
    self.assertEqual(approved_update.available(self.approved),[])
    with self.assertRaises(release.ReleaseError):release.stage_unsigned_tar(self.package,self.versions)
    self.package.chmod(0o644)
    self.assertEqual(approved_update.available(self.approved),[self.package.name])
    with self.assertRaises(ValueError):approved_update.submit(self.inbox,'../'+self.package.name,1,self.approved)
    approved_update.submit(self.inbox,self.package.name,1,self.approved)
    self.assertEqual(json.loads((self.inbox/approved_update.REQUEST).read_text())['package'],self.package.name)
    with self.assertRaises(FileExistsError):approved_update.submit(self.inbox,self.package.name,1,self.approved)
 def test_unprivileged_unsigned_staging_denied(self):
    write_tar(self.package,self.files,'0.2.0')
    with patch('sunhill_nvr.release.os.geteuid',return_value=1000):
        with self.assertRaises(release.ReleaseError):release.stage_unsigned_tar(self.package,self.versions)


class ReleaseGateTests(unittest.TestCase):
 def test_requires_ci_gate_for_exact_commit(self):
    with patch('package_release.git',return_value=b'abc123\n'):
        for env in ({},{'GITHUB_ACTIONS':'true','GITHUB_JOB':'verify','GITHUB_SHA':'abc123'},{'GITHUB_ACTIONS':'true','GITHUB_JOB':'package-verified','GITHUB_SHA':'different'}):
            with self.assertRaisesRegex(ValueError,'requires successful Ubuntu'):require_release_gate(Path('.'),candidate=False,environ=env)
        require_release_gate(Path('.'),candidate=False,environ={'GITHUB_ACTIONS':'true','GITHUB_JOB':'package-verified','GITHUB_SHA':'abc123'})
        require_release_gate(Path('.'),candidate=True,environ={})
