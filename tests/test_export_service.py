from contextlib import closing
import os,sqlite3,subprocess,tempfile,time,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import archive,database,jobs,security
from sunhill_nvr.export_service import run_one,parse_local,cleanup_stale_temporary,plan,ExportError
from sunhill_nvr.media import probe
class ExportServiceTests(unittest.TestCase):
 def test_planning_uses_time_indexes_and_includes_long_and_ambiguous_recordings(self):
    from datetime import datetime,timezone
    db=sqlite3.connect(':memory:');security.initialize(db);archive.initialize(db)
    db.execute("INSERT INTO cameras VALUES(1,'cam','Camera',1)")
    for name,first,second,duration in [('old','2026-11-18T00:00:00+00:00',None,60),('cross','2026-11-20T12:00:00+00:00',None,86400),('ambiguous','2026-10-25T00:30:00+00:00','2026-10-25T01:30:00+00:00',600)]:
        db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,utc_second,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?,?)',(name,'cam','2026-11-21T00:00:00',first,second,duration,10,1,1))
    db.commit()
    result,metadata=plan(db,1,datetime(2026,11,21,11,59,tzinfo=timezone.utc),datetime(2026,11,21,12,1,tzinfo=timezone.utc))
    self.assertEqual([str(s.path) for s in result.segments],['cross']);self.assertEqual(set(map(str,metadata)),{'cross'})
    with self.assertRaises(ExportError):plan(db,1,datetime(2026,10,25,1,31,tzinfo=timezone.utc),datetime(2026,10,25,1,32,tzinfo=timezone.utc))
    details=' '.join(row[-1] for row in db.execute("EXPLAIN QUERY PLAN SELECT relative_path FROM recordings WHERE camera=? AND utc_first>=? AND utc_first<=?",('cam','2026-11-01','2026-12-01')))
    self.assertIn('idx_recordings_camera_utc_first',details);db.close()
 def test_plan_accepts_real_elapsed_time_during_repeated_dst_hour(self):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    db=sqlite3.connect(':memory:');security.initialize(db);archive.initialize(db)
    db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
    db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES ('cam/2026/10/25/023000.mp4','cam','2026-10-25T02:30:00','2026-10-25T00:30:00+00:00',3600,1,1,1)");db.commit()
    zone=ZoneInfo('Europe/Warsaw');start=datetime(2026,10,25,2,30,fold=0,tzinfo=zone);end=datetime(2026,10,25,2,30,fold=1,tzinfo=zone)
    result,_=plan(db,1,start,end);self.assertEqual(result.segments[0].duration,3600);db.close()
 def test_failed_export_reports_missing_or_ambiguous_recordings_without_paths(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.commit()
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:10:00Z','gaps':'skip'})
        run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12)
        state,error=db.execute('SELECT status,error FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual((state,error),('failed',jobs.FAILURE_MESSAGES['no_recordings']))
        with db:db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,utc_second,duration,file_size,mtime_ns,generation) VALUES ('cam/2026/10/25/023000.mp4','cam','2026-10-25T02:30:00','2026-10-25T00:30:00+00:00','2026-10-25T01:30:00+00:00',60,1,1,1)")
        jid=jobs.enqueue(db,uid,1,{'start':'2026-10-25T01:30:00Z','end':'2026-10-25T01:30:30Z','gaps':'skip'})
        run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12)
        state,error=db.execute('SELECT status,error FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual((state,error),('failed',jobs.FAILURE_MESSAGES['ambiguous_time']));self.assertNotIn(str(root),error);db.close()
 def test_failed_media_export_reports_safe_reason(self):
    from unittest.mock import patch
    from sunhill_nvr.media import MediaError
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        file=storage/'cam/2026/11/21/190900.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'not media');st=file.stat()
        db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',1,?,?,1)",(st.st_size,st.st_mtime_ns));db.commit()
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:01Z','gaps':'skip'})
        with patch('sunhill_nvr.export_service.export',side_effect=MediaError('secret path '+str(root))):
            run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12)
        state,error=db.execute('SELECT status,error FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual((state,error),('failed',jobs.FAILURE_MESSAGES['media']));self.assertNotIn(str(root),error);db.close()
 def test_cleanup_only_removes_old_known_temporary_contents_when_idle(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=sqlite3.connect(':memory:');jobs.initialize(db)
        old=root/'.sunhill-job-old';old.mkdir();part=old/'part-0.mp4';part.write_bytes(b'x')
        partial=root/'.sunhill-export-old.mp4';partial.write_bytes(b'x')
        batch=root/'.sunhill-batch-old';batch.mkdir();batchfile=batch/'pass-0-0.mp4';batchfile.write_bytes(b'x')
        unknown=root/'.sunhill-job-unknown';unknown.mkdir();(unknown/'my-recording.mp4').write_bytes(b'keep')
        recent=root/'.sunhill-job-recent';recent.mkdir();(recent/'part-0.mp4').write_bytes(b'keep')
        outside=root/'outside';outside.mkdir();(outside/'important').write_bytes(b'keep');(root/'.sunhill-job-link').symlink_to(outside,target_is_directory=True)
        for p in (part,old,partial,batchfile,batch,unknown/'my-recording.mp4',unknown):os.utime(p,(1,1))
        jid=jobs.enqueue(db,1,1,{},now=10);jobs.claim(db,now=11)
        self.assertEqual(cleanup_stale_temporary(db,root,active=lambda p:False,now=time.time()),0);self.assertTrue(old.exists())
        db.execute("UPDATE export_jobs SET status='failed' WHERE id=?",(jid,));db.commit()
        self.assertEqual(cleanup_stale_temporary(db,root,active=lambda p:p==part,now=time.time()),2)
        self.assertTrue(old.exists());self.assertFalse(partial.exists())
        self.assertEqual(cleanup_stale_temporary(db,root,active=lambda p:False,now=time.time()),1)
        self.assertFalse(old.exists());self.assertFalse(partial.exists());self.assertTrue(unknown.exists());self.assertTrue(recent.exists());self.assertTrue((outside/'important').exists());db.close()
 def test_cleanup_refuses_directory_across_mount_boundary(self):
    from unittest.mock import patch
    from sunhill_nvr import fd_mount
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=sqlite3.connect(':memory:');jobs.initialize(db)
        directory=root/'.sunhill-job-old';directory.mkdir();part=directory/'part-0.mp4';part.write_bytes(b'keep')
        os.utime(part,(1,1));os.utime(directory,(1,1))
        with patch('sunhill_nvr.export_service.fd_mount.require_same',side_effect=fd_mount.MountBoundary()):
            self.assertEqual(cleanup_stale_temporary(db,root,active=lambda p:False),0)
        self.assertEqual(part.read_bytes(),b'keep');db.close()
 def test_cleanup_only_removes_old_failed_reserved_outputs(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=sqlite3.connect(':memory:');jobs.initialize(db);names=[]
        for status in ('failed','done','failed','failed'):
            jid=jobs.enqueue(db,1,1,{},now=1);name=f'export-{jid}-0123456789abcdef.mp4';names.append(name)
            (root/name).write_bytes(status.encode());os.utime(root/name,(1,1))
            with db:db.execute('UPDATE export_jobs SET status=?,result_name=?,updated=? WHERE id=?',(status,name,1 if jid!=4 else int(time.time()),jid))
        untracked=root/'export-99-0123456789abcdef.mp4';untracked.write_bytes(b'keep');os.utime(untracked,(1,1))
        self.assertEqual(cleanup_stale_temporary(db,root,active=lambda p:p.name==names[2]),1)
        self.assertFalse((root/names[0]).exists());self.assertTrue(all((root/name).exists() for name in names[1:]))
        self.assertTrue(untracked.exists());self.assertIsNone(db.execute('SELECT result_name FROM export_jobs WHERE id=1').fetchone()[0])
        self.assertEqual(cleanup_stale_temporary(db,root,active=lambda p:False),1)
        self.assertFalse((root/names[2]).exists());self.assertTrue((root/names[1]).exists());self.assertTrue((root/names[3]).exists());db.close()
 def test_cleanup_refuses_file_on_other_mount(self):
    from unittest.mock import patch
    from sunhill_nvr import fd_mount
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=sqlite3.connect(':memory:');jobs.initialize(db)
        part=root/'.sunhill-export-old.mp4';part.write_bytes(b'keep');os.utime(part,(1,1))
        with patch('sunhill_nvr.export_service.fd_mount.require_same',side_effect=fd_mount.MountBoundary()):
            self.assertEqual(cleanup_stale_temporary(db,root,active=lambda p:False),0)
        self.assertEqual(part.read_bytes(),b'keep');db.close()
 def test_cleanup_recovers_hard_link_pair_only_for_failed_reserved_output(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=sqlite3.connect(':memory:');jobs.initialize(db)
        pairs=[]
        for status in ('failed','done'):
            jid=jobs.enqueue(db,1,1,{},now=1);name=f'export-{jid}-0123456789abcdef.mp4'
            output=root/name;output.write_bytes(status.encode());temporary=root/f'.sunhill-export-test{jid}.mp4';os.link(output,temporary)
            os.utime(output,(1,1));pairs.append((output,temporary))
            with db:db.execute('UPDATE export_jobs SET status=?,result_name=?,updated=1 WHERE id=?',(status,name,jid))
        self.assertEqual(cleanup_stale_temporary(db,root,active=lambda p:p==pairs[0][1]),0)
        self.assertTrue(all(p.exists() for pair in pairs for p in pair))
        self.assertEqual(cleanup_stale_temporary(db,root,active=lambda p:False),1)
        self.assertFalse(pairs[0][0].exists());self.assertFalse(pairs[0][1].exists())
        self.assertTrue(pairs[1][0].exists());self.assertTrue(pairs[1][1].exists())
        self.assertIsNone(db.execute('SELECT result_name FROM export_jobs WHERE id=1').fetchone()[0]);db.close()
 def test_cleanup_resumes_if_output_becomes_active_between_unlinks(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=sqlite3.connect(':memory:');jobs.initialize(db);jid=jobs.enqueue(db,1,1,{},now=1)
        name=f'export-{jid}-0123456789abcdef.mp4';output=root/name;output.write_bytes(b'keep')
        temporary=root/'.sunhill-export-test.mp4';os.link(output,temporary);os.utime(output,(1,1))
        with db:db.execute("UPDATE export_jobs SET status='failed',result_name=?,updated=1 WHERE id=?",(name,jid))
        calls=[]
        def active(path):calls.append(path);return len(calls)==3
        self.assertEqual(cleanup_stale_temporary(db,root,active=active),0)
        self.assertTrue(output.exists());self.assertFalse(temporary.exists());self.assertEqual(output.stat().st_nlink,1)
        self.assertEqual(cleanup_stale_temporary(db,root,active=lambda p:False),1);self.assertFalse(output.exists());db.close()
 def test_cleanup_handles_internal_hard_links_without_touching_external_links(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=sqlite3.connect(':memory:');jobs.initialize(db)
        internal=root/'.sunhill-job-abandoned';internal.mkdir();part=internal/'part-0.mp4';part.write_bytes(b'old')
        temporary=internal/'.sunhill-export-test.mp4';os.link(part,temporary)
        external_dir=root/'.sunhill-job-external';external_dir.mkdir();other=external_dir/'part-0.mp4';other.write_bytes(b'keep')
        outside=root/'important.mp4';os.link(other,outside)
        for item in (part,temporary,internal,other,external_dir):os.utime(item,(1,1))
        self.assertEqual(cleanup_stale_temporary(db,root,active=lambda p:False),1)
        self.assertFalse(internal.exists());self.assertTrue(other.exists());self.assertEqual(outside.read_bytes(),b'keep');db.close()
 def test_cleanup_does_not_remove_a_file_modified_during_activity_check(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=sqlite3.connect(':memory:');jobs.initialize(db)
        direct=root/'.sunhill-export-old.mp4';direct.write_bytes(b'old')
        directory=root/'.sunhill-job-old';directory.mkdir();part=directory/'part-0.mp4';part.write_bytes(b'old')
        for p in (direct,part,directory):os.utime(p,(1,1))
        def active(p):
            p.write_bytes(b'new recording');return False
        self.assertEqual(cleanup_stale_temporary(db,root,active=active),0)
        self.assertEqual(direct.read_bytes(),b'new recording');self.assertEqual(part.read_bytes(),b'new recording');db.close()
 def test_cleanup_rechecks_activity_before_unlinking_directory_contents(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=sqlite3.connect(':memory:');jobs.initialize(db)
        directory=root/'.sunhill-job-old';directory.mkdir();part=directory/'part-0.mp4';part.write_bytes(b'keep')
        for p in (part,directory):os.utime(p,(1,1))
        calls=[]
        def active(p):calls.append(p);return len(calls)>=2
        self.assertEqual(cleanup_stale_temporary(db,root,active=active),0)
        self.assertEqual(part.read_bytes(),b'keep');db.close()
 def test_queue_to_single_mp4_with_gap(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'brama','Brama',1)")
        for i,second in enumerate((0,3)):
            f=storage/f'brama/2026/11/21/19090{second}.mp4';f.parent.mkdir(parents=True,exist_ok=True)
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=red:s=160x90:r=25:d=2','-c:v','libx264','-pix_fmt','yuv420p',str(f)],check=True,stderr=subprocess.PIPE)
            info=f.stat();db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',(str(f.relative_to(storage)),'brama',f'2026-11-21T19:09:0{second}',f'2026-11-21T18:09:0{second}+00:00',2,info.st_size,info.st_mtime_ns,1))
        db.commit();jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:05Z','gaps':'black'})
        self.assertEqual(run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12),jid)
        status,name=db.execute('SELECT status,result_name FROM export_jobs WHERE id=?',(jid,)).fetchone();self.assertEqual(status,'done')
        self.assertAlmostEqual(float(probe(exports/name)['format']['duration']),5,delta=.2);db.close()
 def test_export_across_repeated_hour_from_offset_named_files(self):
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir();template=root/'template.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=red:s=160x90:r=25:d=1','-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(template)],check=True,stderr=subprocess.PIPE)
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.commit()
        for offset in ('+0200','+0100'):
            file=storage/'cam/2026/10/25'/('20261025T023000'+offset+'.mp4');file.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(template,file)
        archive.refresh(db,storage,'Europe/Warsaw',inspect_duration=lambda p:1,is_open=lambda p:False,mount_check=lambda:True,min_age_seconds=0)
        jid=jobs.enqueue(db,uid,1,{'start':'2026-10-25T00:30:00Z','end':'2026-10-25T01:30:01Z','gaps':'skip'})
        run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12)
        status,name=db.execute('SELECT status,result_name FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(status,'done');info=probe(exports/name);self.assertAlmostEqual(float(info['format']['duration']),2,delta=.2)
        self.assertTrue(any(stream.get('codec_type')=='audio' for stream in info['streams']));db.close()
 def test_export_more_than_one_batch(self):
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir();template=root/'template.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=blue:s=160x90:r=25:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(template)],check=True,stderr=subprocess.PIPE)
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'brama','Brama',1)")
        for second in range(17):
            f=storage/f'brama/2026/11/21/1909{second:02}.mp4';f.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(template,f);st=f.stat()
            db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',(str(f.relative_to(storage)),'brama',f'2026-11-21T19:09:{second:02}',f'2026-11-21T18:09:{second:02}+00:00',1,st.st_size,st.st_mtime_ns,1))
        db.commit();jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:17Z','gaps':'skip'})
        run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12)
        status,name=db.execute('SELECT status,result_name FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(status,'done');self.assertAlmostEqual(float(probe(exports/name)['format']['duration']),17,delta=.3);db.close()
 def test_multi_day_sparse_export_preserves_logical_duration_and_audio(self):
    import shutil
    from datetime import datetime,timedelta,timezone
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        template=root/'source.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
            'color=blue:s=64x48:r=25:d=1','-f','lavfi','-i','sine=frequency=440:duration=1',
            '-c:v','libx264','-c:a','aac','-shortest',str(template)],check=True,stderr=subprocess.PIPE)
        db=database.open_db(root/'db.sqlite',create=True);self.addCleanup(db.close)
        security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin')
        db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        start=datetime(2026,11,20,12,tzinfo=timezone.utc)
        for index in range(17):
            instant=start+timedelta(hours=4*index)
            file=storage/'cam'/instant.strftime('%Y/%m/%d/%H%M%S.mp4');file.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(template,file);info=file.stat()
            db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',
                (str(file.relative_to(storage)),'cam',instant.replace(tzinfo=None).isoformat(),instant.isoformat(),1,info.st_size,info.st_mtime_ns,1))
        db.commit();end=start+timedelta(hours=64,seconds=.8);begin=start+timedelta(seconds=.2)
        jid=jobs.enqueue(db,uid,1,{'start':begin.isoformat(),'end':end.isoformat(),'gaps':'skip'})
        run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12)
        status,name=db.execute('SELECT status,result_name FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(status,'done');info=probe(exports/name)
        self.assertAlmostEqual(float(info['format']['duration']),16.6,delta=.15)
        self.assertTrue(any(stream.get('codec_type')=='audio' for stream in info['streams']))
        self.assertEqual(len(list(storage.glob('cam/*/*/*/*.mp4'))),17)
        self.assertTrue(all(f.read_bytes()==template.read_bytes() for f in storage.glob('cam/*/*/*/*.mp4')))
 def test_seventeen_segments_across_midnight_with_audio_and_black_gaps(self):
    import shutil
    from datetime import datetime,timedelta,timezone
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir();template=root/'template.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=blue:s=160x90:r=25:d=1','-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(template)],check=True,stderr=subprocess.PIPE)
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        start=datetime(2026,11,21,22,59,50,tzinfo=timezone.utc)
        from zoneinfo import ZoneInfo
        for index in range(17):
            instant=start+timedelta(seconds=index*2);local=instant.astimezone(ZoneInfo('Europe/Warsaw'))
            file=storage/'cam'/local.strftime('%Y/%m/%d/%H%M%S.mp4');file.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(template,file);info=file.stat()
            db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',
                (str(file.relative_to(storage)),'cam',local.replace(tzinfo=None).isoformat(),instant.isoformat(),1,info.st_size,info.st_mtime_ns,1))
        db.commit();begin=start+timedelta(seconds=.2);end=start+timedelta(seconds=32.8)
        jid=jobs.enqueue(db,uid,1,{'start':begin.isoformat(),'end':end.isoformat(),'gaps':'black'})
        run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12)
        status,name=db.execute('SELECT status,result_name FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(status,'done');info=probe(exports/name)
        self.assertAlmostEqual(float(info['format']['duration']),32.6,delta=.5)
        self.assertTrue(any(stream.get('codec_type')=='audio' for stream in info['streams']))
        self.assertEqual(len(list(storage.glob('cam/*/*/*/*.mp4'))),17)
        self.assertEqual(template.read_bytes(),next(storage.glob('cam/*/*/*/*.mp4')).read_bytes());db.close()
 def test_cancel_during_encoding_stops_worker_without_publishing_or_changing_source(self):
    from unittest.mock import patch
    from sunhill_nvr import export_service
    from sunhill_nvr.media import MediaError
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        source=storage/'cam/2026/11/21/190900.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'original video');st=source.stat()
        db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',1,st.st_size,st.st_mtime_ns,1));db.commit()
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:01Z','gaps':'skip'})
        def encode(cuts,destination,keep_running):
            self.assertTrue(keep_running(.1))
            with closing(database.open_db(root/'db.sqlite')) as other, other:
                other.execute('BEGIN IMMEDIATE');self.assertTrue(jobs.request_cancel(other,jid));other.commit()
            self.assertFalse(keep_running(.2))
            raise MediaError('stopped')
        with patch.object(export_service,'export',side_effect=encode):run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12)
        self.assertEqual(db.execute('SELECT status,error FROM export_jobs WHERE id=?',(jid,)).fetchone(),('failed','Anulowano eksport'))
        self.assertFalse(list(exports.glob('export-*.mp4')));self.assertEqual(source.read_bytes(),b'original video');db.close()
 def test_camera_local_time_is_explicit_at_dst_transition(self):
    from datetime import datetime,timezone
    self.assertEqual(parse_local('2026-11-21T19:09:00','Europe/Warsaw'),datetime(2026,11,21,18,9,tzinfo=timezone.utc))
    with self.assertRaises(ValueError):parse_local('2026-10-25T02:30:00','Europe/Warsaw')
    self.assertNotEqual(parse_local('2026-10-25T02:30:00','Europe/Warsaw',0),parse_local('2026-10-25T02:30:00','Europe/Warsaw',1))
 def test_export_reports_insufficient_space_without_exposing_paths(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.commit()
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:00:00Z','end':'2026-11-21T18:05:00Z','gaps':'skip'})
        run_one(db,root,root,archive_lock=root/'archive.lock',free=lambda p:9,minimum_free=10)
        status,error=db.execute('SELECT status,error FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(status,'failed');self.assertIn('miejsca',error);self.assertNotIn(str(root),error);db.close()
 def test_real_filesystem_free_space_gate_refuses_export_without_filling_disk(self):
    from sunhill_nvr.storage import space_for
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.commit()
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:00:00Z','end':'2026-11-21T18:05:00Z','gaps':'skip'})
        # The reserve exceeds total filesystem capacity; no need to consume disk.
        minimum=space_for(exports).total+1
        run_one(db,storage,exports,archive_lock=root/'archive.lock',minimum_free=minimum)
        self.assertEqual(db.execute('SELECT status,error FROM export_jobs WHERE id=?',(jid,)).fetchone(),('failed',jobs.FAILURE_MESSAGES['disk_space']))
        self.assertEqual(list(exports.iterdir()),[]);db.close()
 def test_export_rejects_replaced_indexed_source_with_same_size_and_mtime(self):
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.commit()
        source=storage/'cam/2026/11/21/190900.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'old1')
        archive.refresh(db,storage,'Europe/Warsaw',inspect_duration=lambda p:1,is_open=lambda p:False,mount_check=lambda:True,min_age_seconds=0)
        before=source.stat();replacement=source.with_name('replacement.tmp');replacement.write_bytes(b'new2')
        os.utime(replacement,ns=(before.st_atime_ns,before.st_mtime_ns));os.replace(replacement,source)
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:01Z','gaps':'skip'})
        with patch('sunhill_nvr.export_service.export') as encoder:
            self.assertEqual(run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12),jid)
            encoder.assert_not_called()
        status,error=db.execute('SELECT status,error FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(status,'failed');self.assertIn('zmienił',error)
        self.assertEqual(list(exports.glob('export-*.mp4')),[]);db.close()
 def test_export_reports_space_exhausted_during_encoding(self):
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        source=storage/'cam/2026/11/21/190900.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'clip');info=source.stat()
        db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',1,info.st_size,info.st_mtime_ns,1));db.commit()
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:01Z','gaps':'skip'});available=iter([100,9])
        def stopped(cuts,destination,keep_running):
            self.assertFalse(keep_running(.5));raise RuntimeError('stopped')
        with patch('sunhill_nvr.export_service.export',side_effect=stopped):run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:next(available),minimum_free=10)
        status,error=db.execute('SELECT status,error FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(status,'failed');self.assertIn('miejsca',error);db.close()
 def test_final_space_check_rejects_output_if_reserve_lost_after_encoding(self):
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        source=storage/'cam/2026/11/21/190900.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'clip');st=source.stat()
        db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',1,st.st_size,st.st_mtime_ns,1));db.commit()
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:01Z','gaps':'skip'})
        available=iter([100,100,9])
        def encode(cuts,destination,keep_running):destination.write_bytes(b'output')
        with patch('sunhill_nvr.export_service.export',side_effect=encode):
            run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:next(available),minimum_free=10)
        status,error=db.execute('SELECT status,error FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(status,'failed');self.assertIn('miejsca',error);self.assertFalse(list(exports.glob('export-*.mp4')));self.assertEqual(source.read_bytes(),b'clip');db.close()
 def test_export_cleans_final_output_if_source_stat_fails(self):
    from unittest.mock import patch
    from datetime import datetime,timezone
    from sunhill_nvr.export_plan import ExportPlan,Slice
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.commit()
        source=storage/'cam/2026/11/21/190900.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'clip');st=source.stat()
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:01Z','gaps':'skip'})
        def fake_export(cuts,destination,**kwargs):
            destination.write_bytes(b'output')
            os.close(cuts[0].source)
        with patch('sunhill_nvr.export_service.export',side_effect=fake_export):
            self.assertEqual(run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12),jid)
        self.assertFalse(any(exports.glob('export-*.mp4')))
        self.assertEqual(db.execute('SELECT status FROM export_jobs WHERE id=?',(jid,)).fetchone()[0],'failed');db.close()
 def test_interruption_after_output_creation_is_recovered_by_cleanup(self):
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'admin','password-for-tests','admin');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        source=storage/'cam/2026/11/21/190900.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'clip');info=source.stat()
        db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',1,info.st_size,info.st_mtime_ns,1));db.commit()
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:01Z','gaps':'skip'})
        def crash(cuts,destination,keep_running):destination.write_bytes(b'incomplete');raise KeyboardInterrupt()
        with patch('sunhill_nvr.export_service.export',side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12)
        status,name=db.execute('SELECT status,result_name FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(status,'running');self.assertTrue((exports/name).exists())
        future=int(time.time())+121;self.assertIsNone(jobs.claim(db,now=future))
        self.assertEqual(db.execute('SELECT status FROM export_jobs WHERE id=?',(jid,)).fetchone()[0],'failed')
        self.assertEqual(cleanup_stale_temporary(db,exports,active=lambda p:False,now=future+86401),1)
        self.assertFalse((exports/name).exists());self.assertIsNone(db.execute('SELECT result_name FROM export_jobs WHERE id=?',(jid,)).fetchone()[0]);db.close()
 def test_revoked_access_fails_job(self):
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'viewer','password-for-tests','viewer');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)");db.commit()
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:00:00Z','end':'2026-11-21T18:05:00Z','gaps':'skip'})
        run_one(db,root,root,archive_lock=root/'archive.lock',free=lambda p:10**12)
        status,error=db.execute('SELECT status,error FROM export_jobs WHERE id=?',(jid,)).fetchone();self.assertEqual(status,'failed');self.assertIn('uprawnienia',error);db.close()
 def test_permission_revocation_during_export_prevents_publication(self):
    from unittest.mock import patch
    from sunhill_nvr import export_service
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);storage=root/'archive';storage.mkdir();exports=root/'exports';exports.mkdir()
        db=database.open_db(root/'db.sqlite',create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
        uid=security.create_user(db,'viewer','password-for-tests','viewer');db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
        db.execute('INSERT INTO camera_permissions VALUES(?,?,1)',(uid,1))
        source=storage/'cam/2026/11/21/190900.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'clip');info=source.stat()
        db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',1,info.st_size,info.st_mtime_ns,1));db.commit()
        jid=jobs.enqueue(db,uid,1,{'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:01Z','gaps':'skip'})
        real_complete=jobs.complete
        def revoke_at_commit(*args,**kwargs):
            with db:db.execute('UPDATE camera_permissions SET may_download=0 WHERE user_id=?',(uid,))
            return real_complete(*args,**kwargs)
        def rendered(cuts,destination,keep_running):destination.write_bytes(b'rendered')
        with patch('sunhill_nvr.export_service.export',side_effect=rendered),patch('sunhill_nvr.export_service.jobs.complete',side_effect=revoke_at_commit):
            export_service.run_one(db,storage,exports,archive_lock=root/'archive.lock',free=lambda p:10**12)
        status,error=db.execute('SELECT status,error FROM export_jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(status,'failed');self.assertIn('uprawnienia',error);self.assertEqual(list(exports.glob('export-*.mp4')),[]);db.close()
if __name__=='__main__':unittest.main()
