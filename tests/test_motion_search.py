import sqlite3,json,unittest
from unittest.mock import patch
from sunhill_nvr import database,security,motion_search

class MotionSearchTests(unittest.TestCase):
    def test_search_native_boxes_only(self):
        with sqlite3.connect(':memory:') as db:
            security.initialize(db);database.migrate(db)
            db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
            inside=json.dumps([{'id':'1','kind':'person','box':[.1,.1,.2,.2]}]);outside=json.dumps([{'id':'2','kind':'person','box':[.7,.7,.2,.2]}]);
            db.executemany('INSERT INTO camera_metadata_history VALUES(1,?,?,?)',[(101000,inside,'native:s'),(102000,inside,'native:s'),(103000,outside,'native:s'),(104000,inside,'old-local')]);db.commit()
            with patch('subprocess.Popen',side_effect=AssertionError('decoder invoked')):
                found=motion_search.scan(db,1,100,0,5,[0,0,.5,.5]);self.assertEqual(found['hits'],[{'start':1,'end':2}]);self.assertEqual(found['sample_count'],3)
                self.assertEqual(motion_search.scan(db,1,100,0,5,[.5,.5,.5,.5])['hits'],[{'start':3,'end':3}])
                self.assertEqual(motion_search.scan(db,1,100,20,5,[0,0,.5,.5])['sample_count'],0)
