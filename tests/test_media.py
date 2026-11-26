from pathlib import Path
from sys import path
import subprocess, tempfile, unittest
path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sunhill_nvr.media import Cut, export, export_many, probe, frame, MediaError

class MediaTests(unittest.TestCase):
    def test_real_ffmpeg_joins_two_files_with_gap_and_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name, color in [('a.mp4','red'),('b.mp4','blue')]:
                subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
                    '-f','lavfi','-i',f'color=c={color}:s=160x90:r=25:d=2',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                    '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(d/name)],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            output = d/'joined.mp4'
            export([Cut(d/'a.mp4',0.5,1.0),Cut(None,0,0.5),Cut(d/'b.mp4',0,1.0)], output)
            info=probe(output)
            self.assertAlmostEqual(float(info['format']['duration']),2.5,delta=.15)
            self.assertTrue(any(s['codec_type']=='audio' for s in info['streams']))
            import struct
            raw=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i',str(output),'-map','0:a:0','-ac','1','-ar','8000','-f','s16le','pipe:1'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
            samples=struct.unpack('<'+str(len(raw)//2)+'h',raw)
            def mean_level(start,end):
                window=samples[int(start*8000):int(end*8000)]
                return sum(abs(value) for value in window)/len(window)
            self.assertGreater(mean_level(.2,.8),100)
            self.assertLess(mean_level(1.15,1.35),30)
            self.assertGreater(mean_level(1.7,2.3),100)
            with self.assertRaises(FileExistsError): export([Cut(d/'a.mp4',0,1)], output)
    def test_export_mixes_audio_and_silent_recordings_with_different_dimensions(self):
        import struct
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);audio=d/'audio.mp4';silent=d/'silent.mp4';output=d/'joined.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                'color=c=red:s=160x90:r=25:d=1','-f','lavfi','-i','sine=frequency=440:duration=1',
                '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(audio)],check=True,stderr=subprocess.PIPE)
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                'color=c=blue:s=320x180:r=30:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(silent)],check=True,stderr=subprocess.PIPE)
            export([Cut(audio,.25,.5),Cut(silent,.25,.5)],output)
            info=probe(output);self.assertAlmostEqual(float(info['format']['duration']),1,delta=.15)
            samples_raw=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i',str(output),'-map','0:a:0','-ac','1','-ar','8000','-f','s16le','pipe:1'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
            samples=struct.unpack('<'+str(len(samples_raw)//2)+'h',samples_raw)
            def mean(start,end):
                window=samples[int(start*8000):int(end*8000)]
                return sum(abs(v) for v in window)/len(window)
            self.assertGreater(mean(.1,.4),100);self.assertLess(mean(.65,.9),30)
    def test_cut_beyond_end_fails_without_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); src=d/'short.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                'color=c=red:s=160x90:r=25:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(src)],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            with self.assertRaises(MediaError):export([Cut(src,.5,1)],d/'out.mp4')
            self.assertFalse((d/'out.mp4').exists())
    def test_gap_longer_than_one_day_can_be_cancelled_without_touching_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);source=d/'source.mp4';fake=d/'slow-ffmpeg';arguments=d/'arguments'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                'color=c=red:s=160x90:r=25:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True,stderr=subprocess.PIPE)
            original=source.read_bytes()
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "'+str(arguments)+'"\nexec sleep 8\n');fake.chmod(0o700)
            calls=[]
            def running(progress):calls.append(progress);return len(calls)==1
            with self.assertRaises(MediaError):export([Cut(source,0,.5),Cut(None,0,86401)],d/'out.mp4',ffmpeg=str(fake),keep_running=running)
            self.assertIn('d=86401.000000',arguments.read_text());self.assertEqual(source.read_bytes(),original)
            self.assertFalse((d/'out.mp4').exists())
    def test_export_from_open_file_descriptor(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);src=d/'source.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                'color=c=red:s=160x90:r=25:d=2','-c:v','libx264','-pix_fmt','yuv420p',str(src)],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            fd=os.open(src,os.O_RDONLY)
            try:export([Cut(fd,.5,1)],d/'out.mp4')
            finally:os.close(fd)
            self.assertAlmostEqual(float(probe(d/'out.mp4')['format']['duration']),1,delta=.15)
    def test_stopped_export_does_not_publish_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);src=d/'source.mp4';slow=d/'slow'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                'color=c=red:s=160x90:r=25:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(src)],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            slow.write_text('#!/bin/sh\nexec sleep 8\n');slow.chmod(0o700)
            calls=[]
            def continue_once(progress):
                calls.append(progress)
                return len(calls)==1
            with self.assertRaises(MediaError):export([Cut(src,0,.5)],d/'out.mp4',ffmpeg=str(slow),keep_running=continue_once)
            self.assertGreaterEqual(len(calls),2)
            self.assertFalse((d/'out.mp4').exists())
            self.assertFalse(any(p.name.startswith('.sunhill-export-') for p in d.iterdir()))
    def test_many_source_slices_are_joined_in_bounded_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);src=d/'source.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                'color=c=red:s=160x90:r=25:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(src)],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            export_many([Cut(src,0,.2) for _ in range(5)],d/'joined.mp4',max_inputs=3)
            self.assertAlmostEqual(float(probe(d/'joined.mp4')['format']['duration']),1,delta=.3)
            self.assertFalse(any(p.name.startswith('.sunhill-batch-') for p in d.iterdir()))
    def test_many_batches_do_not_accumulate_encoder_duration_padding(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);src=d/'source.mp4';out=d/'joined.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                'color=blue:s=64x48:r=25:d=1','-f','lavfi','-i','sine=frequency=440:duration=1',
                '-c:v','libx264','-c:a','aac','-shortest',str(src)],check=True,stderr=subprocess.PIPE)
            cuts=[Cut(src,0,.24) for _ in range(13)]
            export_many(cuts,out,max_inputs=3)
            self.assertAlmostEqual(float(probe(out)['format']['duration']),3.12,delta=.08)
    def test_extract_jpeg_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);src=d/'source.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                'color=c=red:s=160x90:r=25:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(src)],check=True,stderr=subprocess.PIPE)
            self.assertTrue(frame(src,.2).startswith(b'\xff\xd8'))
    def test_no_output_after_probe_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); bad=d/'bad.mp4'; bad.write_text('not video')
            with self.assertRaises(MediaError): export([Cut(bad,0,1)],d/'out.mp4')
            self.assertFalse((d/'out.mp4').exists())
    def test_intermediate_longer_than_a_day_is_checked_against_actual_duration(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);src=d/'intermediate.mp4';src.write_bytes(b'placeholder')
            fake=d/'fake-ffmpeg';arguments=d/'arguments'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "'+str(arguments)+'"\nexit 1\n');fake.chmod(0o700)
            info={'streams':[{'codec_type':'video','width':160,'height':90,'avg_frame_rate':'25/1'}],'format':{'duration':'90000'}}
            with patch('sunhill_nvr.media.probe',return_value=info):
                with self.assertRaises(MediaError):export([Cut(src,0,86401)],d/'out.mp4',ffmpeg=str(fake))
            self.assertIn('86401.000000',arguments.read_text());self.assertFalse((d/'out.mp4').exists())
if __name__ == '__main__': unittest.main()
