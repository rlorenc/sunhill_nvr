import hashlib,os,subprocess,tempfile,time,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import archive_compat,media
from sunhill_nvr.files import acquire_shared
from sunhill_nvr.recorder import recording_command
from sunhill_nvr.supervisor import Supervisor

class CompatTests(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.cache=self.root/'cache'
 def tearDown(self):self.tmp.cleanup()
 def make(self,codec='libx265',tag='hev1'):
    target=self.root/(tag+'.mp4')
    args=['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x96:rate=10:duration=1','-f','lavfi','-i','sine=frequency=440:duration=1','-c:v',codec]
    if codec=='libx265':args+=['-x265-params','pools=1:frame-threads=1:log-level=error']
    subprocess.run(args+['-c:a','aac','-tag:v',tag,'-movflags','+frag_keyframe+empty_moov+default_base_moof',str(target)],stderr=subprocess.PIPE,check=True)
    return target
 def frames(self,file):
    return subprocess.check_output(['ffmpeg','-v','error','-i',str(file),'-map','0:v:0','-f','framemd5','-']).splitlines()[-10:]
 def test_hevc_remux_original_and_ranges_cache(self):
    source=self.make();before=hashlib.sha256(source.read_bytes()).digest();fd=os.open(source,os.O_RDONLY);acquire_shared(fd)
    try:
        out,st=archive_compat.open_compatible(self.cache,fd)
        try:
            result=media.probe(out);video=next(s for s in result['streams'] if s['codec_type']=='video');self.assertEqual(video['codec_tag_string'],'hvc1')
            self.assertEqual(next(s for s in result['streams'] if s['codec_type']=='audio')['codec_name'],'aac')
            f=next(self.cache.glob('*.mp4'));self.assertEqual(self.frames(source),self.frames(f));self.assertEqual(before,hashlib.sha256(source.read_bytes()).digest())
            from unittest.mock import patch
            with patch('sunhill_nvr.media.probe',side_effect=AssertionError('must use cache')):
                again,other=archive_compat.open_compatible(self.cache,fd);os.close(again);self.assertEqual(st.st_ino,other.st_ino)
            os.utime(f,(time.time()-archive_compat.TTL-10,)*2);archive_compat.cleanup(self.cache);self.assertTrue(f.exists())
        finally:os.close(out)
        archive_compat.cleanup(self.cache);self.assertFalse(f.exists())
    finally:os.close(fd)
 def test_h264_unchanged_and_cached(self):
    source=self.make('libx264','avc1');fd=os.open(source,os.O_RDONLY)
    try:
        self.assertIsNone(archive_compat.open_compatible(self.cache,fd));self.assertFalse(list(self.cache.glob('*.mp4')))
        from unittest.mock import patch
        with patch('sunhill_nvr.media.probe',side_effect=AssertionError('must use cache')):self.assertIsNone(archive_compat.open_compatible(self.cache,fd))
    finally:os.close(fd)
 def test_recording_command_conditional_tag_real_segment(self):
    source=self.make();import datetime as dt
    from sunhill_nvr.recorder import prepare_days
    stage=self.root/'stage';stage.mkdir();prepare_days(stage,'cam',dt.datetime.now(dt.timezone.utc).date())
    cmd=recording_command('cam',stage,hevc=True)
    for opt in ('-rtsp_transport','-timeout'):
        pos=cmd.index(opt);del cmd[pos:pos+2]
    cmd[cmd.index('rtsp://127.0.0.1:8554/cam')]=str(source)
    subprocess.run(cmd,env={**os.environ,'TZ':'UTC'},check=True,stderr=subprocess.PIPE)
    segment=next(stage.rglob('*.mp4'));self.assertEqual(media.probe(segment)['streams'][0]['codec_tag_string'],'hvc1');self.assertEqual(self.frames(source),self.frames(segment))
    self.assertNotIn('-tag:v',recording_command('cam',stage))
 def test_supervisor_fallback_after_wrong_codec_hint(self):
    class Process:
        code=None
        def poll(self):return self.code
    stage=self.root/'stage';stage.mkdir();archive=self.root/'archive';archive.mkdir();commands=[]
    def spawn(cmd):commands.append(cmd);return Process()
    worker=Supervisor(stage,archive,active=lambda p:False,spawn=spawn,codec_probe=lambda slug:'hevc' if slug=='hevc' else 'h264')
    worker.tick({'hevc','avc'},100);self.assertTrue(worker.running['hevc'].hevc);self.assertFalse(worker.running['avc'].hevc)
    worker.running['hevc'].process.code=1;worker.tick({'hevc','avc'},101);worker.tick({'hevc','avc'},104)
    self.assertFalse(worker.running['hevc'].hevc);self.assertNotIn('-tag:v',commands[-1]);self.assertIn('copy',commands[-1])
