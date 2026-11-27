import unittest,tempfile
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import object_analytics as a,database,security,archive,metadata_retention

def rule(typ,kind='person',dwell=0):return {'id':'r1','name':'Gate','type':typ,'points':[[.5,0],[.5,1]] if typ=='line_cross' else [[.3,.3],[.8,.3],[.8,.8],[.3,.8]],'kind':kind,'direction':'both','dwell':dwell}
def obj(x,y=.5,kind='person',oid='1'):return {'id':oid,'kind':kind,'box':[x-.01,y-.01,.02,.02]}
class AnalyticsTests(unittest.TestCase):
 def test_rules_geometry_and_gaps(self):
    r=rule('line_cross');cfg={'enabled':True,'retention_days':7,'rules':[r]};a.validate(cfg)
    e=a.RuleEngine();self.assertFalse(e.process([r],1000,[obj(.2)]));self.assertEqual(len(e.process([r],2000,[obj(.7)])),1);self.assertFalse(e.process([r],3000,[obj(.8)]));self.assertFalse(e.process([r],4000,[]));self.assertFalse(e.process([r],5000,[obj(.2)]))
    r=rule('intrusion',dwell=1);e=a.RuleEngine();self.assertFalse(e.process([r],1000,[obj(.2)]));self.assertFalse(e.process([r],2000,[obj(.4)]));self.assertEqual(len(e.process([r],3000,[obj(.4)])),1);self.assertFalse(e.process([r],4000,[obj(.4)]));r['points']=[[.1,.1],[.9,.9],[.1,.9],[.9,.1]]
    with self.assertRaises(ValueError):a.validate({'enabled':True,'retention_days':7,'rules':[r]})
 def test_abandoned_requires_observed_person_and_package(self):
    r=rule('abandoned','package',10);a.validate({'enabled':True,'retention_days':7,'rules':[r]});e=a.RuleEngine();box=obj(.5,kind='package',oid='2')
    self.assertFalse(e.process([r],1000,[box,obj(.55)]));events=[]
    for t in range(2000,14000,1000):events+=e.process([r],t,[box])
    self.assertEqual(len(events),1)
    e=a.RuleEngine();self.assertFalse(any(e.process([r],t,[box]) for t in range(1000,14000,1000)))
 def test_persistence_acl_and_retention(self):
    with tempfile.TemporaryDirectory() as folder:
      db=database.open_db(Path(folder)/'db',create=True);security.initialize(db);database.migrate(db);database.migrate(db);archive.initialize(db)
      try:
        db.execute("INSERT INTO cameras(id,slug,name,enabled) VALUES(1,'cam','Cam',1)");url='rtsp://192.168.1.2/main';db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,?,'rtsp')",(url,));admin=security.create_user(db,'admin','password-long','admin');viewer=security.create_user(db,'viewer','password-long','viewer')
        with self.assertRaises(ValueError):a.save_options(db,1,{'enabled':True,'retention_days':7,'rules':[rule('line_cross')]})
        a.save_options(db,1,{'enabled':True,'retention_days':7,'rules':[]});db.execute('UPDATE camera_metadata_options SET rules_json=? WHERE camera_id=1',(__import__('json').dumps([rule('line_cross')]),));db.commit();self.assertEqual(a.options(db,1)['rules'],[]);e=a.RuleEngine();self.assertTrue(a.snapshot(db,1,url,1000,[obj(.2)],'a',e,received=1000,source='local'));self.assertTrue(a.snapshot(db,1,url,2000,[obj(.7),obj(.8,kind='package',oid='package')],'a',e,received=2000,source='local'));self.assertEqual(db.execute('SELECT event_type,source FROM event_markers').fetchall(),[]);self.assertEqual(a.options(db,1)['status']['state'],'local');self.assertEqual(len(a.history(db,1,2000)),2);self.assertEqual([o['kind'] for o in a.history(db,1,2000)[-1]['objects']],['person']);self.assertEqual(a.live(db,(viewer,'viewer','viewer'),now=2000),{});self.assertIn('1',a.live(db,(admin,'admin','admin'),now=2000));self.assertEqual(a.live(db,(admin,'admin','admin'),now=6000),{});plate=obj(.5,kind='plate');plate['plate_number']='KR12345';a.snapshot(db,1,url,3000,[plate],'a',e,received=3000);self.assertEqual(db.execute('SELECT event_type,source FROM event_markers').fetchall(),[('plate','nvr-plate')]);metadata_retention.reconcile(db,guard=lambda:True,complete=True,now=8*86400000);self.assertEqual(a.history(db,1,2000),[]);self.assertEqual(db.execute('SELECT COUNT(*) FROM event_markers').fetchone(),(0,))
      finally:db.close()
