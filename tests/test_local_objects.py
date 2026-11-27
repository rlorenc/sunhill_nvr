import unittest
from sunhill_nvr.local_objects import CocoDetector,ForegroundObjects,category,LABELS

class LocalObjectsTests(unittest.TestCase):
    def setUp(self):
        try:import cv2,numpy
        except ImportError:self.skipTest('OpenCV unavailable')
        self.cv,self.np=cv2,numpy
    def test_all_classes_and_class_specific_nms(self):
        np=self.np;raw=np.zeros((1,84,83),np.float32)
        for i in range(80):
            raw[0,:4,i]=[32+(i%10)*60,32+(i//10)*70,24,24];raw[0,4+i,i]=.9
        raw[0,:4,80]=raw[0,:4,0];raw[0,4,80]=.8
        raw[0,:4,81]=raw[0,:4,0];raw[0,5,81]=.9
        raw[0,:4,82]=[0,0,0,0];raw[0,4,82]=.95
        result=CocoDetector.decode(raw,self.cv,np,640,640,1,0,0)
        self.assertEqual(len(result),81)
        self.assertEqual({label for _,_,label in result},set(LABELS))
        self.assertEqual(category('dog'),'animal');self.assertEqual(category('truck'),'vehicle');self.assertEqual(category('backpack'),'package')
        self.assertEqual({k for k,_,_ in result},{'person','vehicle','animal','package','object'})
    def test_letterbox_and_invalid_output(self):
        np=self.np;raw=np.zeros((1,84,1),np.float32);raw[0,:4,0]=[320,320,160,80];raw[0,4+2,0]=.9
        self.assertEqual(CocoDetector.decode(raw,self.cv,np,640,320,1,0,160),[('vehicle',[.375,.375,.25,.25],'car')])
        with self.assertRaises(ValueError):CocoDetector.decode(np.zeros((1,3,4)),self.cv,np,640,320,1,0,160)
    def test_stationary_change_and_scene_reset(self):
        np=self.np;f=ForegroundObjects(self.cv,np);base=np.zeros((180,320),np.uint8)
        self.assertEqual(f.observe(base,[]),[])
        item=base.copy();item[80:110,140:170]=220
        first=f.observe(item,[]);self.assertEqual(len(first),1);self.assertEqual(first,f.observe(item,[]));self.assertEqual(first[0][2],'foreground_change')
        self.assertEqual(f.observe(item,[('person',first[0][1],None)]),[])
        self.assertEqual(f.observe(base,[]),[])
        self.assertEqual(f.observe(np.full_like(base,220),[]),[])
    def test_tracks_do_not_merge_different_semantic_classes(self):
        from sunhill_nvr.local_analytics import Tracks
        t=Tracks();a=t.observe([('object',[.1,.1,.2,.2],'chair')],1000)[0];b=t.observe([('object',[.1,.1,.2,.2],'bottle')],2000)[0]
        self.assertNotEqual(a['id'],b['id']);self.assertEqual(b['label'],'bottle')
