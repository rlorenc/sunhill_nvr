import threading
import unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import local_analytics as a

class LocalAnalyticsTests(unittest.TestCase):
    def test_track_confirm_and_gap(self):
        track=a.Tracks(); first=('plate',[.1,.1,.2,.06],'KR12345'); second=('plate',[.11,.1,.2,.06],'KR12345')
        x=track.observe([first],1000)[0]; self.assertNotIn('plate_number',x)
        y=track.observe([second],2000)[0]; self.assertEqual(x['id'],y['id']);self.assertEqual(y['plate_number'],'KR12345')
        z=track.observe([second],6000)[0];self.assertNotEqual(z['id'],y['id']);self.assertNotIn('plate_number',z)
        self.assertNotIn('plate_number',track.observe([('plate',second[1],'KR12346')],7000)[0])
        self.assertEqual(track.observe([],8000),[])
        track.reset();new=track.observe([first],9000)[0];self.assertNotEqual(new["id"],z["id"])
    def test_tracks_do_not_merge_two_objects(self):
        t=a.Tracks();box=[.1,.1,.2,.06];x=t.observe([('plate',box,None),('plate',box,None)],1000);self.assertEqual(len({o['id'] for o in x}),2)
        y=t.observe([('plate',box,None),('plate',box,None)],2000);self.assertEqual(len({o['id'] for o in y}),2)
    def test_missing_dependencies_and_cancel(self):
        stop=threading.Event()
        def missing():raise ImportError()
        self.assertFalse(a.fallback('cam',stop,lambda *args:self.fail(),factory=missing))
        stop.set();self.assertFalse(a.fallback('cam',stop,lambda *args:self.fail(),factory=lambda:None))
    def test_actual_ocr(self):
        try:import cv2,numpy as np
        except ImportError:self.skipTest('OpenCV is not installed')
        if not Path('/usr/bin/tesseract').exists():self.skipTest('Tesseract not installed')
        detector=a.Detector();self.assertIn('plate',detector.models);self.assertIn('face',detector.models)
        x=np.full((80,300),255,np.uint8);cv2.putText(x,'KR12345',(8,57),cv2.FONT_HERSHEY_SIMPLEX,1.9,0,3,cv2.LINE_AA)
        self.assertEqual(a.read_plate(x,cv2),'KR12345');self.assertIsNone(a.read_plate(np.full((80,300),255,np.uint8),cv2))
        self.assertEqual(detector.detect(bytes([128])*(a.WIDTH*a.HEIGHT)),[])
    def test_fallback_emits_real_detector_output(self):
        stop=threading.Event();frames=[]
        class D:
            def detect(self,frame):return [('face',[.1,.1,.2,.2],None)]
        def emit(stamp,objects):frames.append(objects);stop.set()
        self.assertTrue(a.fallback('cam',stop,emit,factory=D,sampler=lambda slug:b'frame'))
        self.assertEqual(frames[0],[{'id':'1','kind':'face','box':[.1,.1,.2,.2]}])
    def test_model_change_ends_session_before_publishing_with_old_weights(self):
        stop=threading.Event()
        with patch.object(a.time,'monotonic',return_value=1):
            fingerprints=iter([('old',),('new',)])
            self.assertFalse(a.fallback('cam',stop,lambda *args:self.fail('stale model emitted'),factory=lambda:object(),sampler=lambda slug:self.fail('stale sample'),generation=lambda:next(fingerprints)))

    def test_invalid_stream_slug(self):
        with self.assertRaises(ValueError):a.sample('../../bad')
