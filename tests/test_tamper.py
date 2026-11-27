import sqlite3
import unittest
from unittest.mock import patch
from sunhill_nvr import tamper, database, security, camera_events

class TamperTests(unittest.TestCase):
    def test_uniform_and_hysteresis(self):
        flat=bytes([120])*tamper.WIDTH*tamper.HEIGHT
        textured=bytes([0,255])*(tamper.WIDTH*tamper.HEIGHT//2)
        self.assertTrue(tamper.uniform(flat));self.assertFalse(tamper.uniform(textured))
        d=tamper.Detector()
        self.assertEqual([d.observe(flat) for _ in range(3)],[False,False,True])
        self.assertIsNone(d.observe(None))
        self.assertTrue(d.observe(textured));self.assertFalse(d.observe(textured))
        with self.assertRaises(ValueError):tamper.uniform(b'')

    def test_persistence_default_enabled_gap_and_identity(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        security.initialize(db);database.migrate(db)
        identity=('cam','rtsp://192.168.1.2/main',None)
        with db:
            db.execute("INSERT INTO cameras VALUES(1,'cam','Camera',1)")
            db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,?,'rtsp')",(identity[1],))
        with patch('sunhill_nvr.workers.writes_permitted',return_value=True):
            self.assertTrue(camera_events.options(db,1)['enabled']);self.assertIn('tamper',camera_events.options(db,1)['types'])
            with db:camera_events.save_options(db,1,{'enabled':False,'types':[]})
            tamper.persist(db,1,identity,True,1)
            self.assertEqual(db.execute('SELECT count(*) FROM event_markers').fetchone()[0],0)
            with db:camera_events.save_options(db,1,{'enabled':True,'types':['tamper']})
            tamper.persist(db,1,identity,True,100);tamper.persist(db,1,identity,True,110)
            tamper.persist(db,1,identity,None,250);tamper.persist(db,1,identity,True,260)
            tamper.persist(db,1,('cam','different',None),True,270)
            self.assertEqual(db.execute('SELECT started_at,ended_at,last_seen FROM event_markers ORDER BY id').fetchall(),[(100,110,110),(260,260,260)])

    def test_failed_decode_is_not_obstruction(self):
        with patch('sunhill_nvr.tamper.subprocess.run',side_effect=OSError):self.assertIsNone(tamper.sample('cam'))
        with self.assertRaises(ValueError):tamper.sample('cam?src=other')
