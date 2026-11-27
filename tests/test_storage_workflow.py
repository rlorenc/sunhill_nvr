"""Local integration of media, archive, protection, rotation and alerts."""
import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from sys import path
from zoneinfo import ZoneInfo

path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import archive,database,health_service,media,protection,retention,rotation_service,security
from sunhill_nvr.files import acquire_shared,open_confined
from sunhill_nvr.storage import identify


class StorageWorkflowTests(unittest.TestCase):
    def test_real_media_protection_rotation_and_alert_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'archive';root.mkdir();(root/'.sunhill-volume-id').write_text('test')
            db=sqlite3.connect(':memory:');security.initialize(db);database.migrate(db);archive.initialize(db)
            try:
                user=security.create_user(db,'admin','password-for-tests','admin')
                db.execute("INSERT INTO cameras VALUES(1,'cam','Camera',1)")
                db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode,recording_since) VALUES(1,'rtsp://cam','rtsp',1)")
                db.executemany('INSERT INTO system_settings VALUES(?,?)',[('ha_url','http://127.0.0.1:8123'),('ha_token','test-token'),('ha_enabled','1')]);db.commit()
                timestamp=datetime.now(ZoneInfo('Europe/Warsaw')).replace(microsecond=0)-timedelta(hours=1)
                files=[]
                for offset,color in ((0,'blue'),(60,'red')):
                    stamp=timestamp+timedelta(seconds=offset);file=root/'cam'/stamp.strftime('%Y/%m/%d')/(stamp.strftime('%H%M%S')+'.mp4')
                    file.parent.mkdir(parents=True,exist_ok=True)
                    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',f'color={color}:s=160x90:r=25:d=1',
                                    '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(file)],check=True,stderr=subprocess.PIPE)
                    os.utime(file,(time.time()-3600,time.time()-3600));files.append(file)
                def duration(file):return float(media.probe(file)['format']['duration'])
                report=archive.refresh(db,root,'Europe/Warsaw',inspect_duration=duration,is_open=lambda p:False,mount_check=lambda:True,
                                       commit_guard=lambda:retention.shared_archive_lock(Path(temporary)/'archive.lock'))
                self.assertEqual(report.accepted,2)
                self.assertTrue(all(any(stream.get('codec_type')=='audio' for stream in media.probe(file)['streams']) for file in files))
                older=str(files[0].relative_to(root));newer=str(files[1].relative_to(root))
                self.assertEqual(protection.protect(db,[older],user),1)
                identity=identify(root);fd,_=open_confined(root,newer);acquire_shared(fd)
                options=dict(minimum=10,target=11,lock_file=Path(temporary)/'archive.lock',
                             protected=lambda name:protection.is_protected(db,name),active=lambda p:False,
                             free=lambda p:8 if files[1].exists() else 11)
                try:
                    preview=retention.run(root,identity,**options)
                    self.assertEqual(preview.selected,());self.assertFalse(preview.reached_target)
                    result=retention.run(root,identity,preview=False,**options)
                    self.assertEqual(result.selected,());self.assertTrue(all(f.exists() for f in files))
                    rotation_service.record(db,'rotation_blocked',before=result.before,after=result.after,protected_count=result.protected_count)
                finally:os.close(fd)
                def removed(name):
                    with db:db.execute('DELETE FROM recordings WHERE relative_path=?',(name,))
                result=retention.run(root,identity,preview=False,on_remove=removed,**options)
                self.assertEqual(result.selected,(newer,));self.assertTrue(result.reached_target)
                self.assertTrue(files[0].exists());self.assertFalse(files[1].exists())
                self.assertEqual(db.execute('SELECT relative_path FROM recordings').fetchall(),[(older,)])
                rotation_service.record(db,'rotation_ok',before=result.before,after=result.after,files=len(result.selected))
                sent=[];self.assertEqual(health_service.deliver(db,send=lambda *args,**kwargs:sent.append((kwargs['camera_id'],kwargs['state']))),2)
                self.assertEqual(sent,[('rotation','rotation_blocked'),('rotation','rotation_ok')])
            finally:db.close()


if __name__=='__main__':unittest.main()
