import datetime as dt,tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.supervisor import Supervisor
class FakeProcess:
 def __init__(self):self.code=None;self.stopped=False
 def poll(self):return self.code
 def terminate(self):self.stopped=True;self.code=0
class SupervisorTests(unittest.TestCase):
 def test_callback_only_after_closed_file_publication(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();opened=set();published=[]
        worker=Supervisor(stage,archive,active=lambda p:p in opened,spawn=lambda cmd:FakeProcess(),inspect=lambda p:{'format':{'duration':30},'streams':[{'codec_type':'video'}]})
        now=dt.datetime(2026,11,25,17,tzinfo=dt.timezone.utc).timestamp();worker.tick({'cam'},now,segment_seconds=30)
        source=stage/'cam'/worker.running['cam'].session/'2026/11/25/20261125T180000+0100.mp4';source.write_bytes(b'video');opened.add(source)
        callback=lambda p,s,i:published.append((p,p.exists(),source.exists(),s,i))
        worker.tick({'cam'},now+1,segment_seconds=30,on_published=callback);self.assertEqual(published,[])
        opened.clear();worker.tick({'cam'},now+2,segment_seconds=30,on_published=callback)
        self.assertEqual(len(published),1);self.assertEqual(published[0][:3],(archive/'cam/2026/11/25/20261125T180000+0100.mp4',True,False))
 def test_segment_change_restarts_with_new_duration_and_extends_watchdog(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();commands=[];clock=[0]
        def spawn(command):commands.append(command);return FakeProcess()
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=spawn,monotonic=lambda:clock[0])
        worker.tick({'cam'},100);old=worker.running['cam'];self.assertEqual(commands[-1][commands[-1].index('-segment_time')+1],'300')
        worker.tick({'cam'},105,segment_seconds=3600);self.assertTrue(old.process.stopped)
        worker.tick({'cam'},110,segment_seconds=3600);new=worker.running['cam'];self.assertEqual(commands[-1][commands[-1].index('-segment_time')+1],'3600')
        clock[0]=1201;worker.tick({'cam'},1300,segment_seconds=3600);self.assertFalse(new.process.stopped)
        clock[0]=3721;worker.tick({'cam'},3820,segment_seconds=3600);self.assertTrue(new.process.stopped)
 def test_restarts_and_publishes_only_closed_segments(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();started=[];active=set()
        def spawn(cmd):p=FakeProcess();started.append((cmd,p));return p
        worker=Supervisor(stage,archive,active=lambda p:p in active,spawn=spawn,inspect=lambda p:{})
        now=dt.datetime(2026,11,21,12,tzinfo=dt.timezone.utc).timestamp()
        worker.tick({'cam'},now);self.assertEqual(len(started),1)
        state=worker.running['cam'];f=stage/'cam'/state.session/'2026/11/21/20261121T130000.mp4';f.write_bytes(b'video');active.add(f)
        worker.tick({'cam'},now+1);self.assertFalse((archive/'cam/2026/11/21/130000.mp4').exists())
        active.clear();worker.tick({'cam'},now+2);self.assertEqual((archive/'cam/2026/11/21/130000.mp4').read_bytes(),b'video')
        state.process.code=1;worker.tick({'cam'},now+3);self.assertEqual(len(started),1)
        worker.tick({'cam'},now+6);self.assertEqual(len(started),2)
        worker.tick(set(),now+7);self.assertTrue(started[-1][1].stopped)
 def test_live_but_stalled_recorder_restarts_without_affecting_other_camera(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();clock=[0];started=[]
        def spawn(command):
            p=FakeProcess();started.append(p);return p
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=spawn,inspect=lambda p:{},
                          monotonic=lambda:clock[0],segment_watchdog_seconds=30)
        now=dt.datetime(2026,11,21,12,tzinfo=dt.timezone.utc).timestamp()
        worker.tick({'good','stalled'},now)
        good=worker.running['good'];stalled=worker.running['stalled']
        clock[0]=25
        f=stage/'good'/good.session/'2026/11/21/20261121T130000.mp4';f.write_bytes(b'video')
        worker.tick({'good','stalled'},now+25)
        clock[0]=31
        worker.tick({'good','stalled'},now-100)
        self.assertTrue(stalled.process.stopped);self.assertFalse(good.process.stopped)
        self.assertEqual(worker.process_failures,{'stalled'})
        clock[0]=32;worker.tick({'good','stalled'},now-99)
        clock[0]=35;worker.tick({'good','stalled'},now-96)
        self.assertNotEqual(worker.running['stalled'].session,stalled.session)
        self.assertEqual(worker.running['stalled'].audio,'copy')
        self.assertEqual(worker.process_failures,{'stalled'})
        self.assertIs(worker.running['good'],good)
 def test_disabled_process_is_killed_if_termination_hangs(self):
    class HangingProcess(FakeProcess):
        def __init__(self):super().__init__();self.terminations=0;self.killed=False
        def terminate(self):self.terminations+=1
        def kill(self):self.killed=True;self.code=-9
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();p=HangingProcess()
        worker=Supervisor(stage,archive,active=lambda path:False,spawn=lambda command:p,inspect=lambda path:{})
        worker.tick({'cam'},100);worker.tick(set(),101);worker.tick(set(),105)
        self.assertEqual(p.terminations,1);self.assertFalse(p.killed)
        worker.tick(set(),111);self.assertTrue(p.killed);self.assertNotIn('cam',worker.running)
 def test_stop_all_uses_one_grace_period_for_all_recorders(self):
    import subprocess
    events=[]
    class Hanging:
        def __init__(self,name):self.name=name;self.code=None
        def poll(self):return self.code
        def terminate(self):events.append(('terminate',self.name))
        def kill(self):events.append(('kill',self.name));self.code=-9
        def wait(self,timeout):
            events.append(('wait',self.name))
            if self.code is None:raise subprocess.TimeoutExpired(self.name,timeout)
            return self.code
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();processes=[Hanging('a'),Hanging('b')]
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=lambda command:processes.pop(0),inspect=lambda p:{})
        worker.tick({'a','b'},100);worker.stop_all()
        self.assertEqual({name for action,name in events[:2] if action=='terminate'},{'a','b'})
        self.assertEqual({name for action,name in events if action=='kill'},{'a','b'});self.assertEqual(worker.running,{})
 def test_old_staging_segment_is_recovered_with_activity_validation(self):
    from sunhill_nvr.activity_helper import handle
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir()
        source=stage/'cam/2026/11/21/20261121T130000.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'legacy')
        def active(file):
            req={'scope':'staging','paths':[str(file.relative_to(stage))]}
            return handle(req,peer_uid=1000,allowed_uid=1000,archive=archive,staging=stage,scan=lambda:set())['active'][0]
        worker=Supervisor(stage,archive,active=active,spawn=lambda c:self.fail('must not spawn'),inspect=lambda p:{})
        worker.tick(set(),dt.datetime(2026,11,21,12,tzinfo=dt.timezone.utc).timestamp())
        self.assertFalse(source.exists());self.assertEqual((archive/'cam/2026/11/21/130000.mp4').read_bytes(),b'legacy')
 def test_supervisor_recovers_segment_interrupted_after_linking(self):
    import os
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir()
        source=stage/'cam/2026/11/21/20261121T130000.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'legacy')
        target=archive/'cam/2026/11/21/130000.mp4';target.parent.mkdir(parents=True);os.link(source,target)
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=lambda c:self.fail('must not spawn'),inspect=lambda p:{})
        worker.tick(set(),dt.datetime(2026,11,21,12,tzinfo=dt.timezone.utc).timestamp())
        self.assertFalse(source.exists());self.assertEqual(target.read_bytes(),b'legacy');self.assertEqual(target.stat().st_nlink,1)
        self.assertEqual(list(target.parent.iterdir()),[target])
 def test_unknown_open_status_fails_closed(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir()
        def unknown(p):raise RuntimeError('unknown')
        worker=Supervisor(stage,archive,active=unknown,spawn=lambda c:FakeProcess(),inspect=lambda p:{})
        now=dt.datetime(2026,11,21,12,tzinfo=dt.timezone.utc).timestamp();worker.tick({'cam'},now)
        f=stage/'cam'/worker.running['cam'].session/'2026/11/21/20261121T130000.mp4';f.write_bytes(b'x')
        with self.assertRaises(RuntimeError):worker.tick({'cam'},now+1)
        self.assertTrue(f.exists())
 def test_changed_mount_stops_tick_even_on_same_device(self):
    import os
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();attempts=[]
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=lambda c:attempts.append(c),inspect=lambda p:{})
        stage_inode=stage.stat().st_ino
        def identity(fd):
            info=os.fstat(fd);return info.st_dev,1 if info.st_ino==stage_inode else 2
        with patch('sunhill_nvr.supervisor.fd_mount.identity',side_effect=identity):
            with self.assertRaisesRegex(RuntimeError,'storage changed'):worker.tick({'cam'},100)
        self.assertEqual(attempts,[])
 def test_unrelated_staging_files_do_not_interrupt_recording(self):
    from sunhill_nvr.activity_helper import handle
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();checked=[]
        invalid=[stage/'cam/unrelated.mp4',stage/'cam/2026/11/21/20261122T130000.mp4',stage/'cam/2026/11/21/20261121T130000+9960.mp4']
        for f in invalid:f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b'untouched')
        valid=stage/'cam/2026/11/21/20261121T130000.mp4';valid.write_bytes(b'valid')
        def active(file):
            checked.append(file)
            return handle({'scope':'staging','paths':[str(file.relative_to(stage))]},peer_uid=1000,allowed_uid=1000,archive=archive,staging=stage,scan=lambda:set())['active'][0]
        process=FakeProcess();worker=Supervisor(stage,archive,active=active,spawn=lambda c:process,inspect=lambda p:{})
        worker.tick({'cam'},dt.datetime(2026,11,21,12,tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(checked,[valid,valid]);self.assertTrue(all(f.read_bytes()==b'untouched' for f in invalid))
        self.assertIs(worker.running['cam'].process,process);self.assertEqual((archive/'cam/2026/11/21/130000.mp4').read_bytes(),b'valid')
 def test_damaged_segments_are_retried_with_backoff_and_rechecked_after_change(self):
    from sunhill_nvr.media import MediaError
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();clock=[0.0];inspections=[];active=set()
        broken=stage/'cam/2026/11/21/20261121T130000.mp4';broken.parent.mkdir(parents=True);broken.write_bytes(b'broken')
        valid=stage/'cam/2026/11/21/20261121T130500.mp4';valid.write_bytes(b'valid')
        def inspect(path):
            inspections.append(path)
            if path.read_bytes()==b'broken':raise MediaError('corrupt')
            return {}
        worker=Supervisor(stage,archive,active=lambda p:p in active,spawn=lambda c:self.fail('unexpected process'),inspect=inspect,monotonic=lambda:clock[0])
        now=dt.datetime(2026,11,21,12,tzinfo=dt.timezone.utc).timestamp()
        worker.tick(set(),now);self.assertEqual(inspections.count(broken),1);self.assertTrue((archive/'cam/2026/11/21/130500.mp4').exists());self.assertEqual(worker.corrupt_cameras,{'cam'})
        clock[0]=1;worker.tick(set(),now+1);self.assertEqual(inspections.count(broken),1)
        clock[0]=16;worker.tick(set(),now+16);self.assertEqual(inspections.count(broken),2)
        broken.write_bytes(b'repaired file');clock[0]=17;active.add(broken);worker.tick(set(),now+17)
        self.assertEqual(worker.corrupt_cameras,{'cam'});self.assertEqual(inspections.count(broken),2)
        active.clear();clock[0]=18;worker.tick(set(),now+18)
        self.assertEqual(inspections.count(broken),3);self.assertEqual((archive/'cam/2026/11/21/130000.mp4').read_bytes(),b'repaired file')
        self.assertFalse(worker.probe_failures);self.assertEqual(worker.corrupt_cameras,set())
 def test_segment_changed_during_probe_is_not_published_until_rechecked(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();calls=[]
        segment=stage/'cam/2026/11/21/20261121T130000.mp4';segment.parent.mkdir(parents=True);segment.write_bytes(b'old')
        def inspect(path):
            calls.append(path.read_bytes())
            if len(calls)==1:path.write_bytes(b'new content')
            return {}
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=lambda c:self.fail('unexpected process'),inspect=inspect)
        now=dt.datetime(2026,11,21,12,tzinfo=dt.timezone.utc).timestamp()
        worker.tick(set(),now);self.assertTrue(segment.exists());self.assertFalse((archive/'cam/2026/11/21/130000.mp4').exists())
        worker.tick(set(),now+1);self.assertEqual(calls,[b'old',b'new content'])
        self.assertEqual((archive/'cam/2026/11/21/130000.mp4').read_bytes(),b'new content')
 def test_publication_failure_is_reported_retried_and_cleared(self):
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();clock=[0.0]
        segment=stage/'cam/2026/11/21/20261121T130000.mp4';segment.parent.mkdir(parents=True);segment.write_bytes(b'keep')
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=lambda c:self.fail('unexpected process'),inspect=lambda p:{},monotonic=lambda:clock[0])
        now=dt.datetime(2026,11,21,12,tzinfo=dt.timezone.utc).timestamp()
        with patch('sunhill_nvr.supervisor.publish_closed_segment',side_effect=OSError('disk unavailable')) as publish:
            worker.tick(set(),now);worker.tick(set(),now+1)
            self.assertEqual(publish.call_count,1);self.assertEqual(worker.blocked_cameras,{'cam'})
            self.assertEqual(segment.read_bytes(),b'keep');self.assertEqual(worker.corrupt_cameras,set())
        clock[0]=16;worker.tick(set(),now+16)
        self.assertEqual(worker.blocked_cameras,set());self.assertEqual((archive/'cam/2026/11/21/130000.mp4').read_bytes(),b'keep')
 def test_published_segment_reports_audio_without_discarding_video(self):
    import os
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir()
        def inspect(path):
            streams=[{'codec_type':'video'}]
            if path.read_bytes()==b'with sound':streams.append({'codec_type':'audio'})
            return {'streams':streams}
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=lambda c:FakeProcess(),inspect=inspect)
        now=dt.datetime(2026,11,21,12,tzinfo=dt.timezone.utc).timestamp();worker.tick({'cam'},now)
        session=worker.running['cam'].session;day=stage/'cam'/session/'2026/11/21'
        silent=day/'20261121T130000.mp4';silent.write_bytes(b'no sound');os.utime(silent,(100,100))
        worker.tick({'cam'},now+1);self.assertFalse(worker.audio_observations['cam'][1])
        self.assertEqual((archive/'cam/2026/11/21/130000.mp4').read_bytes(),b'no sound')
        sound=day/'20261121T130500.mp4';sound.write_bytes(b'with sound');os.utime(sound,(200,200))
        worker.tick({'cam'},now+2);self.assertTrue(worker.audio_observations['cam'][1])
 def test_spawn_failure_retries_with_backoff(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();attempts=[]
        def fail(command):attempts.append(command);raise OSError('unavailable')
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=fail,inspect=lambda p:{})
        worker.tick({'cam'},100);worker.tick({'cam'},101);self.assertEqual(len(attempts),1)
        worker.tick({'cam'},102);self.assertEqual(len(attempts),2)
        self.assertEqual(worker.process_failures,{'cam'})
 def test_recorder_fault_clears_only_when_new_session_publishes(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();attempts=[]
        def spawn(command):
            attempts.append(1)
            if len(attempts)==1:raise OSError('ffmpeg unavailable')
            return FakeProcess()
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=spawn,inspect=lambda p:{})
        now=dt.datetime(2026,11,21,12,tzinfo=dt.timezone.utc).timestamp();worker.tick({'cam'},now)
        self.assertEqual(worker.process_failures,{'cam'})
        worker.tick(set(),now+1,configured_cameras={'cam'});self.assertEqual(worker.process_failures,{'cam'})
        old=stage/'cam/2026/11/21/20261121T125900.mp4';old.parent.mkdir(parents=True,exist_ok=True);old.write_bytes(b'old')
        worker.tick({'cam'},now+3);self.assertEqual(worker.process_failures,{'cam'})
        state=worker.running['cam'];new=stage/'cam'/state.session/'2026/11/21/20261121T130000.mp4';new.write_bytes(b'new')
        worker.tick({'cam'},now+4);self.assertEqual(worker.process_failures,set())
 def test_bad_staging_directory_does_not_interrupt_other_cameras_and_recovers(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();started=[]
        bad=stage/'bad';bad.write_bytes(b'preserve this unrelated file')
        def spawn(command):
            process=FakeProcess();started.append((command,process));return process
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=spawn,inspect=lambda p:{})
        worker.tick({'bad','good'},100)
        self.assertEqual(set(worker.running),{'good'});self.assertEqual(worker.preparation_failures,{'bad'})
        good=worker.running['good'].process
        worker.tick({'bad','good'},101)
        self.assertFalse(good.stopped);self.assertEqual(bad.read_bytes(),b'preserve this unrelated file')
        bad.unlink();worker.tick({'bad','good'},102)
        self.assertEqual(set(worker.running),{'bad','good'});self.assertFalse(worker.preparation_failures)
        self.assertIs(worker.running['good'].process,good)
 def test_failure_of_running_camera_stops_only_that_camera_without_audio_fallback(self):
    from unittest.mock import patch
    from sunhill_nvr import supervisor
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir()
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=lambda c:FakeProcess(),inspect=lambda p:{})
        worker.tick({'bad','good'},100);bad=worker.running['bad'].process;good=worker.running['good'].process
        original=supervisor.prepare_days
        def prepare(root,slug,*args,**kwargs):
            if slug=='bad':raise PermissionError('camera directory')
            return original(root,slug,*args,**kwargs)
        with patch('sunhill_nvr.supervisor.prepare_days',side_effect=prepare):worker.tick({'bad','good'},101)
        self.assertTrue(bad.stopped);self.assertFalse(good.stopped);self.assertEqual(worker.preparation_failures,{'bad'})
        worker.tick({'bad','good'},102);worker.tick({'bad','good'},104)
        self.assertEqual(worker.running['bad'].audio,'copy');self.assertFalse(worker.preparation_failures)
 def test_clock_moving_back_does_not_delay_recording_restart(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();started=[]
        def spawn(command):
            process=FakeProcess();started.append(process);return process
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=spawn,inspect=lambda p:{})
        worker.tick({'cam'},100);started[-1].code=1;worker.tick({'cam'},101)
        worker.tick({'cam'},10);self.assertEqual(len(started),1)
        worker.tick({'cam'},12);self.assertEqual(len(started),2)
 def test_early_failure_tries_aac_and_keeps_video_stream_copy(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);stage=root/'stage';archive=root/'archive';stage.mkdir();archive.mkdir();started=[]
        def spawn(command):
            process=FakeProcess();started.append((command,process));return process
        worker=Supervisor(stage,archive,active=lambda p:False,spawn=spawn,inspect=lambda p:{})
        worker.tick({'cam'},100);self.assertEqual(started[-1][0][started[-1][0].index('-c:a')+1],'copy')
        started[-1][1].code=1;worker.tick({'cam'},101);worker.tick({'cam'},103)
        command=started[-1][0]
        self.assertEqual(command[command.index('-c:a')+1],'aac');self.assertEqual(command[command.index('-c:v')+1],'copy')
        started[-1][1].code=1;worker.tick({'cam'},104);worker.tick({'cam'},108)
        self.assertEqual(started[-1][0][started[-1][0].index('-c:a')+1],'copy')
if __name__=='__main__':unittest.main()
