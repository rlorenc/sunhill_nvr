import datetime as dt, tempfile, unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.recorder import prepare_days,publish_closed_segment,recording_command

class RecorderTests(unittest.TestCase):
    def test_credentials_do_not_appear_in_recording_command(self):
        cmd=recording_command('lumus1',Path('/tmp/incoming'))
        self.assertIn('rtsp://127.0.0.1:8554/lumus1',cmd)
        self.assertNotIn('-rw_timeout',cmd)
        self.assertEqual(cmd[cmd.index('-timeout')+1],'30000000')
        with self.assertRaises(ValueError):recording_command('cam',Path('/tmp/incoming'),io_timeout_seconds=0)
        with self.assertRaises(ValueError): recording_command('../etc',Path('/tmp/incoming'))
        with self.assertRaises(ValueError): recording_command('cam',Path('/tmp/incoming'),audio='not-a-codec')
    def test_audio_fallback_converts_pcm_into_aac_without_reencoding_video(self):
        import os,subprocess
        from sunhill_nvr.media import probe
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'camera.mkv'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=blue:s=160x90:r=25:d=1',
                            '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-c:a','pcm_s16le','-shortest',str(source)],check=True,stderr=subprocess.PIPE)
            session='a'*32;prepare_days(root,'cam',dt.datetime.now(dt.timezone.utc).date(),session=session)
            command=recording_command('cam',root,session=session,audio='aac')
            for option in ('-rtsp_transport','-timeout'):
                pos=command.index(option);del command[pos:pos+2]
            command[command.index('rtsp://127.0.0.1:8554/cam')]=str(source)
            result=subprocess.run(command,env={**os.environ,'TZ':'UTC'},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            files=list((root/'cam'/session).rglob('*.mp4'));self.assertEqual(len(files),1);self.assertTrue(files[0].name.endswith('+0000.mp4'))
            codecs={s['codec_type']:s['codec_name'] for s in probe(files[0])['streams']}
            self.assertEqual(codecs.get('video'),'h264');self.assertEqual(codecs.get('audio'),'aac')
    def test_rtsp_stalled_handshake_times_out(self):
        import os,socket,subprocess,threading,time
        with tempfile.TemporaryDirectory() as tmp:
            server=socket.socket();server.settimeout(7)
            try:server.bind(('127.0.0.1',0));server.listen(1)
            except OSError as exc:server.close();self.skipTest('loopback unavailable: '+str(exc))
            done=threading.Event();received=threading.Event()
            def accept():
                try:
                    client,_=server.accept()
                    with client:client.recv(4096);received.set();done.wait(8)
                except OSError:pass
            thread=threading.Thread(target=accept,daemon=True);thread.start()
            root=Path(tmp);prepare_days(root,'cam',dt.datetime.now(dt.timezone.utc).date(),session='a'*32)
            cmd=recording_command('cam',root,session='a'*32,rtsp_port=server.getsockname()[1],io_timeout_seconds=5)
            started=time.monotonic()
            try:
                result=subprocess.run(cmd,env={**os.environ,'TZ':'UTC'},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=8)
                self.assertTrue(received.is_set());self.assertNotEqual(result.returncode,0)
                self.assertLess(time.monotonic()-started,8)
            finally:done.set();server.close();thread.join(timeout=1)
    def test_publish_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';archive=root/'archive';archive.mkdir()
            prepare_days(stage,'cam',dt.date(2026,11,21))
            f=stage/'cam/2026/11/21/20261121T190900.mp4'; f.write_bytes(b'clip1')
            first=publish_closed_segment(stage,archive,'cam',f)
            self.assertEqual(first.read_bytes(),b'clip1');self.assertFalse(f.exists())
            f.write_bytes(b'clip2');second=publish_closed_segment(stage,archive,'cam',f)
            self.assertNotEqual(first,second);self.assertEqual(first.read_bytes(),b'clip1');self.assertEqual(second.read_bytes(),b'clip2')
    def test_interruption_after_link_recovers_without_duplicating_recording(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';archive=root/'archive';archive.mkdir()
            prepare_days(stage,'cam',dt.date(2026,11,21))
            source=stage/'cam/2026/11/21/20261121T190900.mp4';source.write_bytes(b'keep')
            original_unlink=os.unlink
            def interrupted(path,*args,**kwargs):
                if path==source.name and 'dir_fd' in kwargs:raise OSError('simulated power loss')
                return original_unlink(path,*args,**kwargs)
            with patch('sunhill_nvr.recorder.os.unlink',side_effect=interrupted):
                with self.assertRaises(OSError):publish_closed_segment(stage,archive,'cam',source)
            target=archive/'cam/2026/11/21/190900.mp4'
            self.assertEqual(source.stat().st_ino,target.stat().st_ino);self.assertEqual(source.stat().st_nlink,2)
            recovered=publish_closed_segment(stage,archive,'cam',source)
            self.assertEqual(recovered,target);self.assertFalse(source.exists());self.assertEqual(target.read_bytes(),b'keep')
            self.assertEqual(target.stat().st_nlink,1);self.assertEqual(list(target.parent.iterdir()),[target])
    def test_unrelated_hard_link_is_never_removed_during_recovery(self):
        import os
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';archive=root/'archive';archive.mkdir()
            prepare_days(stage,'cam',dt.date(2026,11,21))
            source=stage/'cam/2026/11/21/20261121T190900.mp4';source.write_bytes(b'keep')
            outside=root/'unrelated';os.link(source,outside)
            with self.assertRaisesRegex(ValueError,'unexpected hard link'):publish_closed_segment(stage,archive,'cam',source)
            self.assertEqual(source.read_bytes(),b'keep');self.assertEqual(outside.read_bytes(),b'keep')
    def test_changed_segment_is_not_published_using_old_identity(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';archive=root/'archive';archive.mkdir()
            prepare_days(stage,'cam',dt.date(2026,11,21))
            source=stage/'cam/2026/11/21/20261121T190900.mp4';source.write_bytes(b'before')
            info=source.stat();signature=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
            source.write_bytes(b'changed and longer')
            with self.assertRaisesRegex(ValueError,'changed before publication'):
                publish_closed_segment(stage,archive,'cam',source,expected=signature)
            self.assertEqual(source.read_bytes(),b'changed and longer');self.assertEqual(list(archive.iterdir()),[])
    def test_sync_failure_after_link_retains_recoverable_source(self):
        import os,stat
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';archive=root/'archive';archive.mkdir()
            prepare_days(stage,'cam',dt.date(2026,11,21))
            source=stage/'cam/2026/11/21/20261121T190900.mp4';source.write_bytes(b'keep')
            original_link=os.link;original_sync=os.fsync;linked=[False]
            def link(*args,**kwargs):
                original_link(*args,**kwargs);linked[0]=True
            def sync(fd):
                if linked[0] and stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('simulated sync failure')
                original_sync(fd)
            with patch('sunhill_nvr.recorder.os.link',side_effect=link),patch('sunhill_nvr.recorder.os.fsync',side_effect=sync):
                with self.assertRaises(OSError):publish_closed_segment(stage,archive,'cam',source)
            self.assertEqual(source.read_bytes(),b'keep');self.assertEqual(source.stat().st_nlink,2)
            target=publish_closed_segment(stage,archive,'cam',source)
            self.assertEqual(target.read_bytes(),b'keep');self.assertFalse(source.exists())
    def test_staging_parent_symlink_is_not_followed_for_publication(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';archive=root/'archive';archive.mkdir();(stage/'cam').mkdir(parents=True)
            outside=root/'outside';day=outside/'11/21';day.mkdir(parents=True)
            source=day/'20261121T190900.mp4';source.write_bytes(b'keep')
            (stage/'cam/2026').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(OSError):publish_closed_segment(stage,archive,'cam',stage/'cam/2026/11/21'/source.name)
            self.assertEqual(source.read_bytes(),b'keep');self.assertEqual(list(archive.iterdir()),[])
    def test_day_creation_rejects_staging_symlinks_and_other_mounts(self):
        from unittest.mock import patch
        from sunhill_nvr import fd_mount
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';(stage/'cam').mkdir(parents=True);outside=root/'outside';outside.mkdir()
            (stage/'cam/2026').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(OSError):prepare_days(stage,'cam',dt.date(2026,11,21))
            self.assertEqual(list(outside.iterdir()),[])
            (stage/'cam/2026').unlink()
            with patch('sunhill_nvr.recorder.fd_mount.require_same',side_effect=fd_mount.MountBoundary()):
                with self.assertRaises(fd_mount.MountBoundary):prepare_days(stage,'cam',dt.date(2026,11,21))
            self.assertEqual(list(outside.iterdir()),[])
    def test_repeated_hour_offsets_survive_publication_and_indexing(self):
        import sqlite3
        from sunhill_nvr import archive
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';destination=root/'archive';destination.mkdir()
            prepare_days(stage,'cam',dt.date(2026,10,25))
            for offset in ('+0200','+0100'):
                file=stage/'cam/2026/10/25'/('20261025T023000'+offset+'.mp4');file.write_bytes(offset.encode())
                published=publish_closed_segment(stage,destination,'cam',file)
                self.assertEqual(published.name,'20261025T023000'+offset+'.mp4')
            db=sqlite3.connect(':memory:');archive.initialize(db)
            report=archive.refresh(db,destination,'Europe/Warsaw',inspect_duration=lambda p:60,is_open=lambda p:False,mount_check=lambda:True,min_age_seconds=0)
            self.assertEqual(report.accepted,2)
            rows=db.execute('SELECT utc_first,utc_second FROM recordings ORDER BY utc_first').fetchall()
            self.assertEqual(rows,[('2026-10-25T00:30:00+00:00',None),('2026-10-25T01:30:00+00:00',None)]);db.close()
    def test_offset_filename_collision_is_retained_and_indexable(self):
        from sunhill_nvr.rotation import recording_info
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';destination=root/'archive';destination.mkdir()
            for session in ('a'*32,'b'*32):
                prepare_days(stage,'cam',dt.date(2026,10,25),session=session)
                source=stage/'cam'/session/'2026/10/25/20261025T023000+0200.mp4';source.write_bytes(session.encode())
                result=publish_closed_segment(stage,destination,'cam',source)
                self.assertIsNotNone(recording_info(destination,result))
            names=sorted(p.name for p in (destination/'cam/2026/10/25').iterdir())
            self.assertEqual(names,['20261025T023000+0200-001.mp4','20261025T023000+0200.mp4'])
    def test_invalid_offset_is_not_published(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';destination=root/'archive';destination.mkdir()
            prepare_days(stage,'cam',dt.date(2026,10,25));source=stage/'cam/2026/10/25/20261025T023000+2500.mp4';source.write_bytes(b'keep')
            with self.assertRaises(ValueError):publish_closed_segment(stage,destination,'cam',source)
            self.assertTrue(source.exists());self.assertEqual(list(destination.iterdir()),[])
    def test_wrong_path_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';archive=root/'archive';archive.mkdir()
            prepare_days(stage,'cam',dt.date(2026,11,21)); f=stage/'cam/2026/11/21/20261120T190900.mp4'; f.write_bytes(b'x')
            with self.assertRaises(ValueError): publish_closed_segment(stage,archive,'cam',f)
            target=stage/'cam/2026/11/21/20261121T190900.mp4';target.symlink_to(f)
            with self.assertRaises(ValueError): publish_closed_segment(stage,archive,'cam',target)
    def test_archive_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as t, tempfile.TemporaryDirectory() as outside:
            root=Path(t);stage=root/'staging';archive=root/'archive';archive.mkdir()
            prepare_days(stage,'cam',dt.date(2026,11,21))
            f=stage/'cam/2026/11/21/20261121T190900.mp4'; f.write_bytes(b'x')
            (archive/'cam').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError): publish_closed_segment(stage,archive,'cam',f)
            self.assertEqual(list(Path(outside).iterdir()),[]);self.assertTrue(f.exists())
    def test_distinct_recording_sessions_do_not_collide(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);stage=root/'staging';archive=root/'archive';archive.mkdir()
            a='a'*32;b='b'*32
            for session in (a,b):
                prepare_days(stage,'cam',dt.date(2026,11,21),session=session)
                f=stage/'cam'/session/'2026/11/21/20261121T190900.mp4';f.write_bytes(session.encode())
                target=publish_closed_segment(stage,archive,'cam',f)
                self.assertEqual(target.read_bytes(),session.encode())
            self.assertEqual(len(list((archive/'cam/2026/11/21').iterdir())),2)
if __name__=='__main__':unittest.main()
