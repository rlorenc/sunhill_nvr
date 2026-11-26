import os,sqlite3,tempfile,time,unittest,subprocess,shutil,contextlib
from pathlib import Path
from datetime import date
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import archive,index_control,rotation,database,security,workers

class IncrementalIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'archive';self.root.mkdir();self.dbfile=Path(self.tmp.name)/'db.sqlite'
        self.db=database.open_db(self.dbfile,create=True);self.addCleanup(self.db.close)
        security.initialize(self.db);database.migrate(self.db);archive.initialize(self.db)
        self.db.execute("INSERT INTO cameras(id,slug,name) VALUES(1,'cam','Camera')");self.db.commit()
    def make(self,n,cam='cam',day='13'):
        file=self.root/cam/'2026'/'11'/day/f'{n//3600:02d}{n//60%60:02d}{n%60:02d}.mp4'
        file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'video');os.utime(file,(1,1));return file
    def scan(self,**kwargs):
        args=dict(inspect_duration=lambda _:47.973,is_open=lambda _:False,mount_check=lambda:True)
        args.update(kwargs);return archive.refresh(self.db,self.root,'Europe/Warsaw',**args)
    def count(self):
        with contextlib.closing(sqlite3.connect(self.dbfile)) as reader:return reader.execute('SELECT count(*) FROM recordings').fetchone()[0]
    def test_batches_are_visible_and_resume_without_reprobing(self):
        for i in range(160):self.make(i)
        groups=[];visible=[]
        def batch(files):groups.append(len(files));return [False]*len(files)
        def progress(report):visible.append(self.count())
        report=self.scan(is_open_batch=batch,progress=progress,should_stop=lambda:bool(visible))
        self.assertTrue(report.interrupted);self.assertEqual(visible,[64]);self.assertEqual(groups,[64]);self.assertEqual(self.count(),64)
        old=self.db.execute('SELECT id,relative_path FROM recordings').fetchall()
        probed=[];groups.clear()
        result=self.scan(is_open_batch=batch,inspect_duration=lambda p:probed.append(p) or 47.973)
        self.assertEqual((result.accepted,result.unchanged,len(probed)),(160,64,96));self.assertEqual(sum(groups),96);self.assertLessEqual(max(groups),64)
        self.assertEqual(self.count(),160)
        self.assertTrue(set(old)<=set(self.db.execute('SELECT id,relative_path FROM recordings').fetchall()))
    def test_interrupted_full_scan_does_not_prune_old_index(self):
        old=self.make(0);self.scan();old.unlink()
        for i in range(1,100):self.make(i)
        self.scan(progress=lambda report:None,should_stop=lambda:self.count()>1)
        self.assertIsNotNone(self.db.execute('SELECT id FROM recordings WHERE relative_path=?',(str(old.relative_to(self.root)),)).fetchone())
        self.assertEqual(self.scan().removed,1)
    def test_scope_and_failed_activity_are_safe(self):
        self.make(0);self.make(0,'cam2');self.make(0,day='14')
        self.scan(camera='cam',dates={date(2026,11,13)});self.assertEqual(self.count(),1)
        with self.assertRaises(RuntimeError):self.scan(is_open_batch=lambda _:[])
        self.assertEqual(self.count(),1)
        self.scan();self.assertEqual(self.count(),3)
    def test_real_legacy_mp4_batch(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):self.skipTest('ffmpeg unavailable')
        source=Path(self.tmp.name)/'source.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=160x90:r=10:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
        for i in range(66):
            p=self.make(i);shutil.copyfile(source,p);os.utime(p,(1,1))
        from types import SimpleNamespace
        cfg=SimpleNamespace(archive=self.root,archive_lock=Path(self.tmp.name)/'lock',timezone='Europe/Warsaw',activity_socket=Path('/unused'))
        observed=[];groups=[]
        def active_batch(socket,scope,files):groups.append(len(files));return [False]*len(files)
        with patch('sunhill_nvr.workers.activity_rpc.query',side_effect=active_batch),patch('sunhill_nvr.workers.storage_guard',return_value=lambda:True):
            result=workers.index_once(cfg,self.db,progress=lambda r:observed.append(self.count()))
        self.assertEqual((result.accepted,result.failed), (66,0));self.assertEqual(groups,[64,2]);self.assertEqual(observed,[64,66]);self.assertEqual(self.db.execute('SELECT MIN(local_time) FROM recordings').fetchone()[0],'2026-11-13T00:00:00')
    def test_request_pause_and_replace(self):
        with self.db:job=index_control.request(self.db,'cam','2026-11-13','2026-11-14')
        self.assertEqual(index_control.read(self.db)['state'],'queued')
        self.assertTrue(index_control.update(self.db,job['id'],state='running'))
        with self.db:index_control.pause(self.db)
        self.assertFalse(index_control.update(self.db,job['id'],state='done'))
        with self.db:new=index_control.request(self.db)
        self.assertNotEqual(job['id'],new['id']);self.assertFalse(index_control.update(self.db,job['id'],state='done'))
        for args in [('missing',None,None),('cam','2026-11-14','2026-11-13'),('cam','2026-11-13',None)]:
            with self.assertRaises(ValueError):index_control.scope(self.db,*args)
    def test_job_runs_scope_and_persists_counts(self):
        self.make(0);self.make(0,day='14');self.make(0,'other')
        with self.db:job=index_control.request(self.db,'cam','2026-11-13','2026-11-13')
        from types import SimpleNamespace
        cfg=SimpleNamespace(archive=self.root,archive_lock=Path(self.tmp.name)/'lock',timezone='Europe/Warsaw')
        data={'format':{'duration':'47.973'},'streams':[{'codec_type':'video'}]}
        with patch('sunhill_nvr.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.workers.detector',return_value=lambda p:False),patch('sunhill_nvr.workers.probe',return_value=data),patch('sunhill_nvr.workers.writes_permitted',return_value=True):
            index_control.run(cfg,self.db,automatic=False,should_stop=lambda:False)
        state=index_control.read(self.db)
        self.assertEqual((state['id'],state['state'],state['accepted']),(job['id'],'done',1))
        self.assertEqual(self.count(),1)

    def test_empty_cleanup_only_verified_old_regular_files(self):
        from types import SimpleNamespace
        from sunhill_nvr import files,retention
        cfg=SimpleNamespace(archive=self.root,archive_lock=Path(self.tmp.name)/'lock',timezone='Europe/Warsaw')
        empty=self.make(1);empty.write_bytes(b'');os.utime(empty,(1,1))
        bad=self.make(2);fresh=self.make(3);fresh.write_bytes(b'')
        opened=self.make(4);opened.write_bytes(b'');os.utime(opened,(1,1))
        protected=self.make(5);protected.write_bytes(b'');os.utime(protected,(1,1))
        replacement=self.make(6);replacement.write_bytes(b'');os.utime(replacement,(1,1))
        locked=self.make(7);locked.write_bytes(b'');os.utime(locked,(1,1))
        link=self.make(8);link.unlink();link.symlink_to(empty)
        staging=self.make(9,'.sunhill-staging');staging.write_bytes(b'');os.utime(staging,(1,1))
        self.scan()  # seed a stale index entry for the empty file
        relative=str(protected.relative_to(self.root))
        uid=security.create_user(self.db,'cleanupadmin','password-for-tests','admin')
        self.db.execute("INSERT INTO protected_files(relative_path,reason,created_by,created_at) VALUES(?,?,?,?)",(relative,'test',uid,1));self.db.commit()
        fd=os.open(locked,os.O_RDONLY);self.addCleanup(os.close,fd);files.acquire_shared(fd)
        calls=[]
        def active(p):
            calls.append(p)
            if p==replacement and calls.count(p)==2:p.write_bytes(b'new video')
            return p==opened
        now=time.time()+600
        with patch('sunhill_nvr.workers.storage_guard',return_value=lambda:True),patch('sunhill_nvr.workers.detector',return_value=active),patch('sunhill_nvr.workers.rotation_permitted',return_value=True),patch('sunhill_nvr.workers.probe',side_effect=ValueError('corrupt')) as probe:
            result=workers.index_once(cfg,self.db,now=now)
        self.assertFalse(empty.exists());self.assertIsNone(self.db.execute('SELECT id FROM recordings WHERE relative_path=?',(str(empty.relative_to(self.root)),)).fetchone())
        for p in (bad,fresh,opened,protected,replacement,locked,staging):self.assertTrue(p.exists(),p)
        self.assertTrue(link.is_symlink());self.assertEqual(replacement.read_bytes(),b'new video')
        self.assertEqual(result.empty_removed,1);self.assertEqual(result.skipped,1)
        self.assertGreaterEqual(result.empty_deferred,2)

    def test_empty_cleanup_fails_closed_on_activity_or_maintenance(self):
        from types import SimpleNamespace
        cfg=SimpleNamespace(archive=self.root,archive_lock=Path(self.tmp.name)/'lock')
        p=self.make(1);p.write_bytes(b'');os.utime(p,(1,1));now=time.time()+600
        def remove(**kw):return workers.remove_empty_recording(cfg,self.db,p,p.stat(),now=now,**kw)
        with patch('sunhill_nvr.workers.rotation_permitted',return_value=False):
            self.assertFalse(remove(guard=lambda:True,active=lambda _:False))
        with patch('sunhill_nvr.workers.rotation_permitted',return_value=True):
            self.assertFalse(remove(guard=lambda:False,active=lambda _:False))
            with self.assertRaises(RuntimeError):remove(guard=lambda:True,active=lambda _:(_ for _ in ()).throw(RuntimeError('RPC failed')))
        self.assertTrue(p.exists())

    def test_duplicate_start_keeps_existing_job(self):
        with self.db:
            job=index_control.request(self.db,'cam')
            self.assertEqual(index_control.request(self.db),job)
        self.assertTrue(index_control.update(self.db,job['id'],state='running',scanned=64))
        with self.db:self.assertEqual(index_control.request(self.db)['id'],job['id'])
        self.assertEqual(index_control.read(self.db)['scanned'],64)
