import os,sqlite3,tempfile,time,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.archive import initialize,refresh,index_published

class ArchiveTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.db=sqlite3.connect(':memory:');initialize(self.db)
 def tearDown(self):self.db.close();self.temp.cleanup()
 def make(self,name):
    p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('video');os.utime(p,(1,1));return p
 def test_indexes_original_and_legacy_with_dst_ambiguity(self):
    self.make('brama/2026/10/25/023000.mp4');self.make('brama/2026/11/21/Brama_00_20261121190900.mp4')
    r=refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True,now=time.time())
    self.assertEqual(r.accepted,2)
    rows=self.db.execute('SELECT local_time,utc_first,utc_second FROM recordings ORDER BY local_time').fetchall()
    self.assertIsNotNone(rows[0][2]);self.assertIsNone(rows[1][2])
 def test_nested_auxiliary_not_indexed(self):
    from sunhill_nvr.files import open_confined
    good=self.make('cam/2026/11/21/190900.mp4')
    others=[self.make(f'{name}/2026/11/21/190900.mp4') for name in ('.sunhill-staging','.sunhill-exports')]
    checked=[]
    refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:checked.append(p) or 10,is_open=lambda p:False,mount_check=lambda:True,now=time.time())
    self.assertEqual(checked,[good]);self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],1)
    for p in others:
        with self.assertRaises(ValueError):open_confined(self.root,str(p.relative_to(self.root)))
 def test_offset_timestamp_is_rendered_in_configured_timezone(self):
    self.make('brama/2026/11/21/20261121T190900+0200.mp4')
    refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True)
    self.assertEqual(self.db.execute('SELECT local_time,utc_first,utc_second FROM recordings').fetchone(),('2026-11-21T18:09:00','2026-11-21T17:09:00+00:00',None))
 def test_open_file_skipped_and_disconnected_mount_fails(self):
    self.make('brama/2026/11/21/190900.mp4')
    r=refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:True,mount_check=lambda:True)
    self.assertEqual(r.accepted,0)
    with self.assertRaises(RuntimeError):refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:False)
 def test_probe_failure_does_not_remove_existing_entry(self):
    self.make('brama/2026/11/21/190900.mp4')
    args=dict(inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True)
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    file=self.root/'brama/2026/11/21/190900.mp4';file.write_text('changed');os.utime(file,(1,1))
    args['inspect_duration']=lambda p:float('nan')
    report=refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.assertEqual(report.failed,1);self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],1)
 def test_unchanged_files_are_not_reprobed(self):
    self.make('brama/2026/11/21/190900.mp4')
    calls=[]
    def check(p):calls.append(p);return 10
    args=dict(inspect_duration=check,is_open=lambda p:False,mount_check=lambda:True)
    refresh(self.db,self.root,'Europe/Warsaw',**args);refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.assertEqual(len(calls),1)
 def test_replaced_inode_is_reprobed_even_with_same_size_and_mtime(self):
    original=self.make('brama/2026/11/21/190900.mp4');calls=[]
    def probe(p):calls.append(p.read_text());return 10 if len(calls)==1 else 20
    args=dict(inspect_duration=probe,is_open=lambda p:False,mount_check=lambda:True)
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    replacement=original.with_name('replacement.tmp');replacement.write_text('other');os.utime(replacement,ns=(original.stat().st_atime_ns,original.stat().st_mtime_ns));os.replace(replacement,original)
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.assertEqual(calls,['video','other'])
    self.assertEqual(self.db.execute('SELECT duration FROM recordings').fetchone()[0],20)
 def test_existing_entries_are_reprobed_after_identity_table_is_added(self):
    self.make('brama/2026/11/21/190900.mp4');calls=[]
    args=dict(inspect_duration=lambda p:calls.append(p) or 10,is_open=lambda p:False,mount_check=lambda:True)
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.db.execute('DROP TABLE recording_identity');self.db.commit();initialize(self.db)
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.assertEqual(len(calls),2)
 def test_modified_file_with_restored_mtime_is_reprobed(self):
    original=self.make('brama/2026/11/21/190900.mp4');calls=[]
    args=dict(inspect_duration=lambda p:calls.append(p.read_text()) or 10,is_open=lambda p:False,mount_check=lambda:True)
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    before=original.stat();original.write_text('other');os.utime(original,ns=(before.st_atime_ns,before.st_mtime_ns))
    self.assertEqual(original.stat().st_ino,before.st_ino)
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.assertEqual(calls,['video','other'])
 def test_replaced_open_file_waits_until_it_is_closed(self):
    original=self.make('brama/2026/11/21/190900.mp4');calls=[]
    args=dict(inspect_duration=lambda p:calls.append(p.read_text()) or 10,is_open=lambda p:False,mount_check=lambda:True)
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    replacement=original.with_name('replacement.tmp');replacement.write_text('other');os.utime(replacement,ns=(original.stat().st_atime_ns,original.stat().st_mtime_ns));os.replace(replacement,original)
    args['is_open']=lambda p:True;refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.assertEqual(calls,['video'])
    args['is_open']=lambda p:False;refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.assertEqual(calls,['video','other'])
 def test_removed_file_is_removed_from_index(self):
    p=self.make('brama/2026/11/21/190900.mp4')
    args=dict(inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True)
    refresh(self.db,self.root,'Europe/Warsaw',**args);p.unlink()
    result=refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.assertEqual(result.removed,1);self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],0)
    self.assertEqual(self.db.execute('SELECT count(*) FROM recording_identity').fetchone()[0],0)
 def test_recording_id_is_never_reassigned_after_rotation_or_archive_switch(self):
    first=self.make('brama/2026/11/21/190900.mp4')
    args=dict(inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True)
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    old_id=self.db.execute('SELECT id FROM recordings').fetchone()[0]
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.assertEqual(self.db.execute('SELECT id FROM recordings').fetchone()[0],old_id)
    first.unlink();refresh(self.db,self.root,'Europe/Warsaw',**args)
    second=self.make('brama/2026/11/21/191000.mp4')
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    second_id=self.db.execute('SELECT id FROM recordings').fetchone()[0]
    self.assertGreater(second_id,old_id)
    self.assertIsNone(self.db.execute('SELECT id FROM recordings WHERE id=?',(old_id,)).fetchone())
    # Storage switch clears the index but must not reset its public identifiers.
    with self.db:
        self.db.execute('DELETE FROM recording_identity')
        self.db.execute('DELETE FROM recordings')
    second.unlink();self.make('brama/2026/11/21/191100.mp4')
    initialize(self.db);refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.assertGreater(self.db.execute('SELECT id FROM recordings').fetchone()[0],second_id)
 def test_legacy_database_seeds_recording_id_counter_without_changing_existing_ids(self):
    with self.db:
        self.db.execute('DROP TRIGGER archive_recording_sequence_insert')
        self.db.execute('DROP TABLE archive_recording_sequence')
        self.db.execute("INSERT INTO recordings(id,relative_path,camera,local_time,duration,file_size,mtime_ns,generation) VALUES(500,'brama/2026/11/21/190900.mp4','brama','2026-11-21T19:09:00',10,5,1,1)")
    initialize(self.db)
    self.assertEqual(self.db.execute('SELECT highwater FROM archive_recording_sequence').fetchone()[0],500)
    self.assertEqual(self.db.execute('SELECT id FROM recordings').fetchone()[0],500)
    with self.db:self.db.execute('DELETE FROM recordings')
    self.make('brama/2026/11/21/191000.mp4')
    refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True)
    self.assertEqual(self.db.execute('SELECT id FROM recordings').fetchone()[0],501)
 def test_rotation_between_scan_and_commit_does_not_reinsert_deleted_file(self):
    p=self.make('brama/2026/11/21/190900.mp4')
    args=dict(inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True)
    refresh(self.db,self.root,'Europe/Warsaw',**args)
    calls=[];verified=[]
    def guard():
        calls.append(1)
        if len(calls)==2:
            p.unlink()
            with self.db:self.db.execute('DELETE FROM recordings')
        return True
    result=refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=guard,on_verified=lambda p,s:verified.append(p))
    self.assertEqual((result.accepted,result.failed),(0,1));self.assertEqual(verified,[])
    self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],0)
 def test_replaced_file_after_probe_is_not_indexed_with_old_metadata(self):
    p=self.make('brama/2026/11/21/190900.mp4');replacement=self.root/'replacement.tmp';replacement.write_text('other')
    calls=[];verified=[]
    def guard():
        calls.append(1)
        if len(calls)==2:os.replace(replacement,p)
        return True
    result=refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=guard,on_verified=lambda p,s:verified.append(p))
    self.assertEqual((result.accepted,result.failed),(0,1));self.assertEqual(verified,[])
    self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],0)
 def test_rotation_during_index_commit_removes_index_after_commit(self):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    file=self.make('brama/2026/11/21/190900.mp4');database=self.root/'index.sqlite'
    db=sqlite3.connect(database,timeout=5);db.execute('PRAGMA foreign_keys=ON');initialize(db)
    deleted=threading.Event()
    def rotate():
        file.unlink();deleted.set()
        other=sqlite3.connect(database,timeout=5);other.execute('PRAGMA foreign_keys=ON')
        try:
            with other:other.execute('DELETE FROM recordings WHERE relative_path=?',('brama/2026/11/21/190900.mp4',))
        finally:other.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        def verified(path,info):
            nonlocal future
            future=pool.submit(rotate)
            self.assertTrue(deleted.wait(timeout=3))
        future=None
        refresh(db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True,on_verified=verified)
        future.result(timeout=5)
    self.assertEqual(db.execute('SELECT count(*) FROM recordings').fetchone()[0],0)
    self.assertEqual(db.execute('SELECT count(*) FROM recording_identity').fetchone()[0],0);db.close()
 def test_mount_change_before_commit_rolls_back_index(self):
    self.make('brama/2026/11/21/190900.mp4');calls=[]
    def guard():calls.append(1);return len(calls)<4
    with self.assertRaisesRegex(RuntimeError,'mount changed'):
        refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=guard)
    self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],0)
    self.assertEqual(self.db.execute('SELECT generation FROM archive_scan_state').fetchone()[0],0)
 def test_rotation_lock_prevents_index_commit_and_index_blocks_rotation(self):
    from sunhill_nvr import retention
    self.make('brama/2026/11/21/190900.mp4');lock=self.root/'archive.lock';checks=[]
    def verified(path,info):
        with self.assertRaises(retention.RetentionBusy):
            with retention.exclusive_worker_lock(lock):pass
        checks.append(path)
    args=dict(inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True,
              commit_guard=lambda:retention.shared_archive_lock(lock),on_verified=verified)
    with retention.exclusive_worker_lock(lock):
        with self.assertRaises(retention.RetentionBusy):refresh(self.db,self.root,'Europe/Warsaw',**args)
    self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],0)
    self.assertEqual(refresh(self.db,self.root,'Europe/Warsaw',**args).accepted,1)
    self.assertEqual(len(checks),1)
 def test_metadata_persistence_rolls_back_with_index(self):
    self.make('brama/2026/11/21/190900.mp4');seen=[]
    def persist(db):
        db.execute("INSERT INTO recording_stream_info VALUES('brama/2026/11/21/190900.mp4',0)")
        raise RuntimeError('simulated failure')
    with self.assertRaises(RuntimeError):
        refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True,
                on_verified=lambda p,info:seen.append(p),on_persist=persist)
    self.assertEqual(len(seen),1);self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],0)
    self.assertEqual(self.db.execute('SELECT count(*) FROM recording_stream_info').fetchone()[0],0)
 def test_failed_verified_callback_does_not_commit_a_partial_scan(self):
    self.make('brama/2026/11/21/190900.mp4')
    def failed(path,info):raise RuntimeError('observation failed')
    with self.assertRaisesRegex(RuntimeError,'observation failed'):
        refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True,on_verified=failed)
    self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],0)
 def test_incremental_scan_indexes_selected_days_and_preserves_old_rows(self):
    from datetime import date
    old=self.make('brama/2026/10/20/190900.mp4');recent=self.make('brama/2026/11/21/190900.mp4')
    args=dict(inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True)
    refresh(self.db,self.root,'Europe/Warsaw',**args);old.unlink()
    new=self.make('brama/2026/11/21/191000.mp4')
    seen=[]
    report=refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:seen.append(p) or 10,is_open=lambda p:False,mount_check=lambda:True,dates={date(2026,11,21)})
    self.assertEqual(report.removed,0);self.assertEqual(seen,[new])
    self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone()[0],3)
    self.assertEqual(refresh(self.db,self.root,'Europe/Warsaw',**args).removed,1)
if __name__=='__main__':unittest.main()


class PublishedIndexTests(unittest.TestCase):
 setUp=ArchiveTests.setUp
 tearDown=ArchiveTests.tearDown
 make=ArchiveTests.make
 def test_immediate_closed_rtsp_index_and_identity(self):
    p=self.make('cam/2026/11/25/20261125T182623+0100.mp4');os.utime(p,None)
    info=p.stat();sig=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
    data={'format':{'duration':'30.0'},'streams':[{'codec_type':'video'},{'codec_type':'audio'}]}
    index_published(self.db,self.root,p,'Europe/Warsaw',sig,data,mount_check=lambda:True)
    first=self.db.execute('SELECT id,local_time,utc_first,duration FROM recordings').fetchone()
    self.assertEqual(first[1:],('2026-11-25T18:26:23','2026-11-25T17:26:23+00:00',30.0))
    self.assertEqual(self.db.execute('SELECT has_audio FROM recording_stream_info').fetchone(),(1,))
    index_published(self.db,self.root,p,'Europe/Warsaw',sig,data,mount_check=lambda:True)
    self.assertEqual(self.db.execute('SELECT id FROM recordings').fetchone(),(first[0],))
    refresh(self.db,self.root,'Europe/Warsaw',inspect_duration=lambda p:30,is_open=lambda p:False,mount_check=lambda:True)
    self.assertEqual(self.db.execute('SELECT id FROM recordings').fetchone(),(first[0],))
 def test_immediate_rejects_changed_and_linked_files(self):
    p=self.make('cam/2026/11/25/20261125T182623+0100.mp4');info=p.stat();sig=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
    data={'format':{'duration':'30.0'},'streams':[{'codec_type':'video'}]}
    p.write_bytes(b'different')
    with self.assertRaises(ValueError):index_published(self.db,self.root,p,'Europe/Warsaw',sig,data,mount_check=lambda:True)
    info=p.stat();sig=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns);os.link(p,self.root/'other')
    with self.assertRaises(ValueError):index_published(self.db,self.root,p,'Europe/Warsaw',sig,data,mount_check=lambda:True)
    self.assertEqual(self.db.execute('SELECT count(*) FROM recordings').fetchone(),(0,))
