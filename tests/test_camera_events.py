import contextlib
import sqlite3
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from sunhill_nvr import metadata_retention, camera_events as e, database, security, archive, jobs, cli, baichuan_control as b
from test_baichuan_control import FakeCamera

URL='rtsp://user:pass@192.168.1.30/main'

class EventsTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');security.initialize(self.db);database.migrate(self.db);archive.initialize(self.db);jobs.initialize(self.db)
        with self.db:
            self.db.execute("INSERT INTO cameras VALUES(1,'cam','Camera',1)")
            self.db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,?,'rtsp')",(URL,))
        self.guard=patch('sunhill_nvr.workers.writes_permitted',return_value=True);self.guard.start();self.addCleanup(self.guard.stop);self.addCleanup(self.db.close)
    def test_parse_push_only_channel_zero_and_explicit_states(self):
        root=ET.fromstring('<body><AlarmEventList><AlarmEvent><channelId>1</channelId><AItype>face</AItype></AlarmEvent><AlarmEvent><channelId>0</channelId><status>MD,visitor</status><AItype>people,vehicle,dog_cat</AItype></AlarmEvent></AlarmEventList></body>')
        self.assertEqual(e.parse_push(root),{'motion':True,'visitor':True,'tamper':False,'person':True,'vehicle':True,'animal':True,'face':False,'package':False})
        self.assertEqual(e.parse_push(ET.fromstring('<body><AlarmEvent><channelId>0</channelId><AItype>none</AItype></AlarmEvent></body>'))['person'],False)
        self.assertEqual(e.parse_push(ET.fromstring('<body/>')), {})
    def test_parse_cgi_support_and_off(self):
        self.assertEqual(e.parse_cgi({'channel':0,'people':{'support':1,'alarm_state':1},'vehicle':{'support':1,'alarm_state':0},'dog_cat':{'support':0,'alarm_state':1}}),{'person':True,'vehicle':False})
        self.assertEqual(e.parse_cgi({'channel':1,'people':{'support':1,'alarm_state':1}}),{})
    def test_edges_duration_dedup_and_filter(self):
        for t,state in [(100,True),(101,True),(120,True),(130,False),(140,True)]:
            e.snapshot(self.db,1,URL,{'person':state,'motion':True},'cgi',t)
        self.assertEqual(self.db.execute("SELECT event_type,started_at,ended_at,last_seen FROM event_markers WHERE event_type='person' ORDER BY started_at").fetchall(),[('person',100,130,120),('person',140,None,140)])
        e.snapshot(self.db,1,URL,{'person':True},'cgi',250)
        self.assertEqual(self.db.execute("SELECT started_at,ended_at FROM event_markers WHERE event_type='person' ORDER BY started_at").fetchall(),[(100,130),(140,140),(250,None)])
    def test_disabled_and_identity_race(self):
        self.assertFalse(e.snapshot(self.db,1,'rtsp://other',{'person':True},'cgi',100))
        with self.db:e.save_options(self.db,1,{'enabled':False,'types':['person'],'retention_days':3})
        self.assertFalse(e.snapshot(self.db,1,URL,{'person':True},'cgi',100))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM event_markers').fetchone()[0],0)
    def test_retention_only_native(self):
        e.snapshot(self.db,1,URL,{'person':True},'cgi',100);e.snapshot(self.db,1,URL,{'person':False},'cgi',120)
        with self.db:
            self.db.execute("INSERT INTO event_markers(camera_id,started_at,event_type,source) VALUES(1,100,'person','home_assistant')")
            e.save_options(self.db,1,{'enabled':True,'types':['person'],'retention_days':1})
        metadata_retention.reconcile(self.db,guard=lambda:True,complete=True,now=100000)
        self.assertEqual(self.db.execute('SELECT source FROM event_markers').fetchall(),[('home_assistant',)])
    def test_box_validation(self):
        self.assertEqual(e.normalize_box([0.1,0.2,0.3,0.4]),[0.1,0.2,0.3,0.4])
        for bad in ([0,0,0,1],[0.9,0.9,0.2,0.2],[True,0,1,1],[0,0,float('nan'),1],['0',0,1,1]):
            with self.assertRaises(ValueError):e.normalize_box(bad)
    def test_disconnect_closes_last_seen(self):
        e.snapshot(self.db,1,URL,{'animal':True},'baichuan',100);e.snapshot(self.db,1,URL,{'animal':True},'baichuan',120)
        e.offline(self.db,1,URL)
        self.assertEqual(self.db.execute('SELECT ended_at FROM event_markers').fetchone()[0],120)
    def test_migration_existing_event_and_fresh(self):
        old=sqlite3.connect(':memory:');security.initialize(old)
        with patch.dict(database.MIGRATIONS,{k:v for k,v in database.MIGRATIONS.items() if k<9},clear=True):database.migrate(old)
        with old:
            old.execute("INSERT INTO cameras VALUES(1,'old','old',1)")
            old.execute("INSERT INTO event_markers(camera_id,started_at,event_type,source) VALUES(1,100,'person','home_assistant')")
        database.migrate(old);database.migrate(old)
        self.assertEqual(old.execute('SELECT id,started_at,box_json FROM event_markers').fetchall(),[(1,100,None)])
        self.assertEqual(old.execute('PRAGMA integrity_check').fetchone(),('ok',));old.close()
    def test_push_interleaved_with_subscribe_response(self):
        class Camera(FakeCamera):
            def sendall(self,data):
                cmd=int.from_bytes(data[4:8],'little');mid=int.from_bytes(data[12:16],'little')
                if cmd!=31:return super().sendall(data)
                self.assert_channel=mid&255
                def frame(cmd,mid,raw):
                    wire=b.aes(raw,self.key)
                    return struct.pack('<IIIIHHI',0x0abcdef0,cmd,len(wire),mid,200,0x6414,0)+wire
                self.buffer+=frame(33,251,b'<body><AlarmEvent><channelId>0</channelId><AItype>people</AItype></AlarmEvent></body>')+frame(31,mid,b'')
        fake=Camera();observed=[]
        with patch.object(b.socket,'create_connection',return_value=fake):
            with b.Baichuan('192.168.1.30','user','pass') as native:native.request(31,push_channel=True,on_push=lambda root:observed.append(e.parse_push(root)))
        self.assertEqual(fake.assert_channel,251);self.assertTrue(observed[0]['person'])
