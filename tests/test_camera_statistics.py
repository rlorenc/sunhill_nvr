import json, sqlite3, tempfile, unittest, subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from sunhill_nvr import camera_statistics as stats, database, security, archive, storage

class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=database.open_db(self.root/'db.sqlite',create=True);self.addCleanup(self.db.close)
        security.initialize(self.db);database.migrate(self.db);archive.initialize(self.db)
        self.now=1_800_000_000
        self.db.execute("INSERT INTO cameras(id,slug,name,enabled) VALUES(1,'cam','Camera',1)")
        self.db.execute("INSERT INTO camera_details(camera_id,main_url,sub_url,record_mode,recording_since) VALUES(1,'rtsp://u:secret@cam/main','rtsp://cam/sub','rtsp',?)",(self.now-100000,))
        self.db.execute("INSERT INTO camera_health(camera_id,state,changed_at,last_checked) VALUES(1,'recording',?,?)",(self.now,self.now));self.db.commit()
    def test_packet_measurement(self):
        payload={'streams':[{'codec_name':'h264','width':2560,'height':1440,'avg_frame_rate':'30/1'}], 'packets':[{'pts_time':str(i/30),'duration_time':str(1/30),'size':'13125'} for i in range(60)]}
        run=Mock(return_value=SimpleNamespace(returncode=0,stdout=json.dumps(payload).encode()))
        result=stats.sample('cam',run=run)
        self.assertEqual(result['fps'],30);self.assertEqual(result['video_bitrate'],3150000)
        self.assertEqual(run.call_args.args[0][-1],'rtsp://127.0.0.1:8554/cam')
        self.assertIsNone(stats.sample('cam',run=Mock(return_value=SimpleNamespace(returncode=1,stdout=b''))))
        with self.assertRaises(ValueError):stats.sample('cam?x=1',run=run)
    def test_cache_no_credentials_and_invalidation(self):
        inspect=Mock(return_value={'codec':'h264','width':1920,'height':1080,'fps':25,'video_bitrate':3_000_000})
        stats.refresh(self.db,inspect=inspect,clock=lambda:self.now)
        self.assertEqual([x.args[0] for x in inspect.call_args_list],['cam','cam_sub'])
        row=self.db.execute('SELECT value FROM system_settings WHERE key=?',(stats.PREFIX+'1',)).fetchone()[0]
        self.assertNotIn('secret',row);self.assertEqual(json.loads(row)['checked_at'],self.now)
        stats.clear(self.db,1);self.assertFalse(stats._cached(self.db,1))
    def test_coverage_overlap_and_gaps(self):
        self.assertEqual(stats.coverage([(250,350),(0,100),(50,120),(130,180)],0,300),{'covered_seconds':220,'gaps':1,'gap_seconds':70})
    def add(self,i,start,duration,size,second=None):
        stamp=datetime.fromtimestamp(start,timezone.utc).isoformat()
        self.db.execute('INSERT INTO recordings(id,relative_path,camera,local_time,utc_first,utc_second,duration,file_size,mtime_ns,generation) VALUES(?,?,?,?,?,?,?,?,?,1)',(i,f'cam/{i}.mp4','cam',stamp,stamp,second,duration,size,(start+duration)*10**9))
    def test_daily_bytes_retention_and_protection(self):
        for i in range(24):self.add(i+1,self.now-86400+i*3600,3600,1000)
        self.add(25,self.now-90000,100,3000)
        uid=security.create_user(self.db,'admin','password-for-tests','admin')
        self.db.execute("INSERT INTO protected_files(relative_path,created_by,created_at) VALUES('cam/25.mp4',?,?)",(uid,self.now));self.db.commit()
        report=stats.inventory(self.db,self.root,minimum=1000,buffer=1000,now=self.now,space=lambda p:storage.Space(200000,50000,50000),guard=lambda:True)
        cam=report['cameras']['1'];self.assertEqual(cam['files'],25);self.assertEqual(cam['bytes_24h'],24000);self.assertEqual(cam['covered_seconds'],86400)
        self.assertEqual(report['disk']['retention_days'],3);self.assertEqual(report['disk']['days_until_reserve'],2)
        self.db.execute('UPDATE camera_details SET recording_since=?',(self.now-300,));self.db.commit()
        report=stats.inventory(self.db,self.root,minimum=0,buffer=0,now=self.now,space=lambda p:storage.Space(200000,50000,50000));self.assertFalse(report['disk']['forecast_ready'])
    def test_no_disk_forecast_without_mount(self):
        report=stats.inventory(self.db,self.root,minimum=0,buffer=0,now=self.now,guard=lambda:False)
        self.assertIsNone(report['disk']['free_bytes']);self.assertFalse(report['disk']['forecast_ready'])
    def test_changed_configuration_during_probe_not_persisted(self):
        def changed(alias):
            self.db.execute("UPDATE camera_details SET main_url='rtsp://cam/new' WHERE camera_id=1");self.db.commit();return None
        stats.refresh(self.db,inspect=changed,clock=lambda:self.now)
        self.assertFalse(stats._cached(self.db,1))

    def test_metadata_fallback_after_packet_timeout(self):
        payload={'streams':[{'codec_name':'h264','width':1920,'height':1080,'avg_frame_rate':'25/1'}]}
        run=Mock(side_effect=[subprocess.TimeoutExpired('ffprobe',8),SimpleNamespace(returncode=0,stdout=json.dumps(payload).encode())])
        self.assertEqual(stats.sample('cam',run=run),{'codec':'h264','width':1920,'height':1080,'fps':25,'video_bitrate':None})
        self.assertNotIn('-show_packets',run.call_args.args[0])
    def test_independent_profile_budget_and_last_good(self):
        profile={'codec':'h264','width':1920,'height':1080,'fps':25,'video_bitrate':3000000}
        inspect=Mock(return_value=profile)
        ticks=iter([0,0,20]);stats.refresh(self.db,inspect=inspect,clock=lambda:self.now,monotonic=lambda:next(ticks),budget=30)
        cached=stats._cached(self.db,1);self.assertEqual(cached['main'],profile)
        self.assertEqual(stats._profile_status(cached,'sub',self.now,1800)[1],'pending')
        self.assertEqual([call.args[0] for call in inspect.call_args_list],['cam'])
        inspect.reset_mock();inspect.return_value=None
        stats.refresh(self.db,inspect=inspect,clock=lambda:self.now+300,monotonic=lambda:0)
        self.assertEqual([call.args[0] for call in inspect.call_args_list],['cam_sub','cam'])
        cached=stats._cached(self.db,1);self.assertEqual(stats._profile_status(cached,'main',self.now+300,1800),(profile,'last_known',self.now))
        self.assertEqual(stats._profile_status(cached,'sub',self.now+300,1800)[1],'failed')
        self.assertEqual(cached['main_checked_at'],self.now)
    def test_legacy_cache_does_not_renew_other_profile(self):
        profile={'codec':'h264','width':1920,'height':1080,'fps':25,'video_bitrate':3000000}
        self.db.execute('INSERT INTO system_settings(key,value) VALUES(?,?)',(stats.PREFIX+'1',json.dumps({'main':profile,'sub':profile,'checked_at':self.now})));self.db.commit()
        ticks=iter([0,0,20]);stats.refresh(self.db,inspect=lambda alias:profile,clock=lambda:self.now+2000,monotonic=lambda:next(ticks),budget=30)
        cached=stats._cached(self.db,1);self.assertEqual(cached['sub_checked_at'],self.now)
        self.assertEqual(stats._profile_status(cached,'sub',self.now+2000,1800)[1],'last_known')
