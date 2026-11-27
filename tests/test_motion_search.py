import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.motion_search import scan
from sunhill_nvr.media import MediaError

class MotionSearchTests(unittest.TestCase):
    def test_real_motion_within_selected_roi_only(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'motion.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=black:s=160x90:r=10:d=4','-vf',"drawbox=x=10:y=10:w=50:h=60:color=white:t=fill:enable='between(t,1,2.9)'",'-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
            before=source.read_bytes()
            fd=os.open(source,os.O_RDONLY)
            try:
                moving=scan(fd,0,4,[0,0,.5,1]);still=scan(fd,0,4,[.5,0,.5,1])
                self.assertEqual(moving['sample_count'],8);self.assertEqual(moving['sample_fps'],2)
                self.assertEqual(len(moving['hits']),2)
                self.assertAlmostEqual(moving['hits'][0]['start'],1,delta=.5)
                self.assertAlmostEqual(moving['hits'][1]['start'],3,delta=.5)
                self.assertEqual(still['hits'],[])
                with self.assertRaises(MediaError):scan(fd,0,20,[0,0,.5,1])
                with self.assertRaises(ValueError):scan(fd,0,121,[0,0,.5,1])
            finally:os.close(fd)
            self.assertEqual(source.read_bytes(),before)
