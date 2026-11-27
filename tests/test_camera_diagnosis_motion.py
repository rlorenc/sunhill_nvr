import sqlite3
import unittest
from unittest.mock import patch
from sunhill_nvr import device_diagnosis as diag, motion_boxes as motion, database, security

class DiagnosisTests(unittest.TestCase):
    def test_false_default_led_is_not_a_capability(self):
        result={'device':{'protocol':'CGI HTTP'},'controls':{'light':False,'brightness':85,'ir':{}},'settings':[{'key':'speaker_volume'},{'key':'hdr'}]}
        diag.filter_abilities(result,{'supportFLswitch':0,'supportAoAdjust':0,'supportIspHdr':1})
        self.assertEqual(result['controls'],{'ir':{}})
        self.assertEqual(result['settings'],[{'key':'hdr'}])
    def test_cache_bound_to_config_not_time(self):
        with sqlite3.connect(':memory:') as db:
            security.initialize(db);database.migrate(db)
            db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
            db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://host/live','rtsp')")
            db.commit()
            row=db.execute(diag.TARGET_SQL,(1,)).fetchone()
            self.assertTrue(diag.persist(db,1,row,{'talkback':False},100))
            self.assertEqual(diag.cached(db,1,row,101),{'talkback':False})
            self.assertEqual(diag.cached(db,1,row,100+86400*365),{'talkback':False})
            self.assertFalse(diag.needs_probe(db,1,row,100+86400*365))
            self.assertIsNone(diag.cached(db,1,('rtsp://other/live',*row[1:]),101))
            self.assertFalse(diag.persist(db,1,('rtsp://other/live',*row[1:]),{},102))

    def test_worker_discovers_missing_once_and_retries_partial(self):
        with sqlite3.connect(':memory:') as db:
            security.initialize(db);database.migrate(db)
            for cid in (1,2):
                db.execute('INSERT INTO cameras VALUES(?,?,?,1)',(cid,'cam'+str(cid),'Cam'))
                db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(?,?,'rtsp')",(cid,'rtsp://host/'+str(cid)))
            db.commit()
            with patch.object(diag,'diagnose',side_effect=[{'controls':{'light_available':True},'partial':False},{'controls':{},'partial':True},{'controls':{'ir':{}},'partial':False}]) as inspect,patch('sunhill_nvr.workers.writes_permitted',return_value=True):
                diag.refresh_missing(db,clock=lambda:100);self.assertEqual(inspect.call_count,2)
                diag.refresh_missing(db,clock=lambda:101);self.assertEqual(inspect.call_count,2)
                diag.refresh_missing(db,clock=lambda:300);self.assertEqual(inspect.call_count,3)
                diag.refresh_missing(db,clock=lambda:1000000);self.assertEqual(inspect.call_count,3)

    def test_acknowledged_led_preserves_diagnosis_and_survives_refresh(self):
        with sqlite3.connect(':memory:') as db:
            security.initialize(db);database.migrate(db)
            db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
            db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://host/live','rtsp')");db.commit()
            row=db.execute(diag.TARGET_SQL,(1,)).fetchone()
            data={'controls':{'light_available':True},'talkback':True,'checked_at':100}
            diag.persist(db,1,row,data,100);old=diag.snapshot(db,1)
            with db:self.assertTrue(diag.update_control(db,1,row,data,'light',True))
            saved=diag.cached(db,1,row);self.assertTrue(saved['controls']['light']);self.assertEqual(saved['controls']['light_source'],'command');self.assertTrue(saved['talkback']);self.assertEqual(saved['checked_at'],100)
            self.assertFalse(diag.persist(db,1,row,{'controls':{}},101,expected=old))
            diag.persist(db,1,row,{'controls':{'light_available':True},'talkback':True},102)
            self.assertTrue(diag.cached(db,1,row)['controls']['light'])
            with db:diag.update_control(db,1,row,data,'light',False,observed=False)
            self.assertEqual(diag.cached(db,1,row)['controls'],{'light_available':True,'light':False,'light_source':'device'})

class MotionTests(unittest.TestCase):
    def test_local_region_and_global_exposure(self):
        import numpy as np
        d=motion.Detector();blank=np.zeros((motion.HEIGHT,motion.WIDTH),np.uint8)
        self.assertEqual(d.observe(blank.tobytes()),[])
        self.assertEqual(d.observe(blank.tobytes()),[])
        changed=blank.copy();changed[40:80,100:140]=220
        boxes=d.observe(changed.tobytes());self.assertEqual(len(boxes),1)
        x,y,w,h=boxes[0];self.assertLess(x,100/motion.WIDTH);self.assertGreater(x+w,139/motion.WIDTH)
        self.assertLess(y,40/motion.HEIGHT);self.assertGreater(y+h,79/motion.HEIGHT)
        self.assertEqual(d.observe(np.full_like(blank,200).tobytes()),[])
    def test_persist_regions_and_clear(self):
        with sqlite3.connect(':memory:') as db:
            security.initialize(db);database.migrate(db)
            db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
            db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,'rtsp://host/live','rtsp')");db.commit()
            objects=[{'id':'motion-1','kind':'motion','box':[.1,.2,.3,.4]}]
            with patch('sunhill_nvr.workers.writes_permitted',return_value=True):
                self.assertTrue(motion.persist(db,1,'rtsp://host/live',100000,objects,'session'))
                self.assertTrue(motion.persist(db,1,'rtsp://host/live',100500,[],'session'))
                self.assertFalse(motion.persist(db,1,'rtsp://other/live',101000,objects,'session'))
            self.assertEqual([x['objects'] for x in motion.history(db,1,100000)],[objects,[]])
            self.assertEqual(db.execute("SELECT event_type,source FROM event_markers").fetchall(),[('motion','nvr-motion')])
