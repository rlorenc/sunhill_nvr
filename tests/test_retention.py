import os,tempfile,time,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.retention import run
from sunhill_nvr.storage import identify
from sunhill_nvr.files import open_confined,acquire_shared
class RetentionTests(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.d=Path(self.tmp.name);self.root=self.d/'archive';self.root.mkdir()
    (self.root/'.sunhill-volume-id').write_text('id');self.identity=identify(self.root);self.lock=self.d/'retention.lock'
 def tearDown(self):self.tmp.cleanup()
 def make(self,relative):
    p=self.root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'abc');os.utime(p,(1,1));return p
 def test_mount_loss_before_space_probe_stops_rotation(self):
    calls=[]
    def guard():calls.append(1);return len(calls)<2
    with self.assertRaisesRegex(RuntimeError,'identity changed'):
        run(self.root,self.identity,minimum=10,target=20,lock_file=self.lock,protected=lambda p:False,
            active=lambda p:False,preview=False,free=lambda p:self.fail('must not inspect wrong volume'),mount_guard=guard)
 def test_mount_loss_during_space_probe_cannot_report_success(self):
    present=True
    def free(_):
        nonlocal present
        present=False
        return 100
    with self.assertRaisesRegex(RuntimeError,'identity changed'):
        run(self.root,self.identity,minimum=10,target=20,lock_file=self.lock,protected=lambda p:False,
            active=lambda p:False,preview=False,free=free,mount_guard=lambda:present)
 def test_preview_keeps_files_and_respects_protection(self):
    a=self.make('a/2026/11/20/100000.mp4');b=self.make('b/2026/11/21/100000.mp4')
    result=run(self.root,self.identity,minimum=10,target=11,lock_file=self.lock,protected=lambda p:p.startswith('a/'),active=lambda p:False,free=lambda p:8)
    self.assertEqual(result.selected,('b/2026/11/21/100000.mp4',));self.assertTrue(a.exists());self.assertTrue(b.exists())
    self.assertEqual((result.protected_count,result.protected_bytes),(1,3))
 def test_preview_reports_protection_and_unknown_time_after_target_is_met(self):
    old=self.make('a/2026/03/28/100000.mp4')
    newer=self.make('a/2026/11/21/100000.mp4')
    unknown=self.make('a/2026/03/29/023000.mp4')
    result=run(self.root,self.identity,minimum=10,target=11,lock_file=self.lock,
               protected=lambda p:p.endswith('2026/11/21/100000.mp4'),active=lambda p:False,free=lambda p:8)
    self.assertEqual(result.selected,('a/2026/03/28/100000.mp4',))
    self.assertEqual((result.protected_count,result.protected_bytes),(1,3))
    self.assertEqual((result.unknown_time_count,result.unknown_time_bytes),(1,3))
    self.assertTrue(old.exists() and newer.exists() and unknown.exists())
 def test_preview_reports_protected_when_no_rotation_is_needed(self):
    self.make('a/2026/11/21/100000.mp4')
    result=run(self.root,self.identity,minimum=10,target=20,lock_file=self.lock,
               protected=lambda p:True,active=lambda p:self.fail('no file should be selected'),free=lambda p:15)
    self.assertEqual(result.selected,());self.assertTrue(result.reached_target)
    self.assertEqual((result.before,result.after,result.protected_count,result.protected_bytes),(15,15,1,3))
 def test_preview_rechecks_mount_after_full_report(self):
    self.make('a/2026/11/21/100000.mp4');calls=[]
    def guard():calls.append(1);return len(calls)<4
    with self.assertRaisesRegex(RuntimeError,'identity changed'):
        run(self.root,self.identity,minimum=10,target=20,lock_file=self.lock,protected=lambda p:True,
            active=lambda p:False,free=lambda p:15,mount_guard=guard)
 def test_preview_does_not_count_sparse_length_as_reclaimed_space(self):
    sparse=self.make('a/2026/11/20/100000.mp4')
    with sparse.open('wb') as output:output.truncate(20_000_000)
    os.utime(sparse,(1,1));allocated=sparse.stat().st_blocks*512
    if allocated>=10_000_000:self.skipTest('filesystem did not create a sparse file')
    result=run(self.root,self.identity,minimum=10_000_000,target=10_000_000,lock_file=self.lock,
               protected=lambda p:False,active=lambda p:False,free=lambda p:0)
    self.assertEqual(result.selected,('a/2026/11/20/100000.mp4',))
    self.assertLess(result.after,10_000_000);self.assertFalse(result.reached_target);self.assertTrue(sparse.exists())
 def test_unknown_spring_gap_time_is_never_selected_for_deletion(self):
    unknown=self.make('a/2026/03/29/023000.mp4')
    result=run(self.root,self.identity,minimum=10,target=11,lock_file=self.lock,protected=lambda p:False,active=lambda p:False,preview=False,free=lambda p:8)
    self.assertEqual(result.selected,());self.assertFalse(result.reached_target);self.assertTrue(unknown.exists());self.assertEqual((result.unknown_time_count,result.unknown_time_bytes),(1,3))
 def test_protected_file_with_unknown_time_is_counted_in_both_reports(self):
    self.make('a/2026/03/29/023000.mp4')
    result=run(self.root,self.identity,minimum=10,target=11,lock_file=self.lock,
               protected=lambda p:True,active=lambda p:False,free=lambda p:8)
    self.assertEqual((result.protected_count,result.protected_bytes),(1,3))
    self.assertEqual((result.unknown_time_count,result.unknown_time_bytes),(1,3))
 def test_oldest_recording_uses_actual_utc_during_repeated_hour(self):
    earlier=self.make('cam/2026/10/25/20261025T025900+0200.mp4')
    later=self.make('cam/2026/10/25/20261025T020000+0100.mp4')
    result=run(self.root,self.identity,minimum=10,target=11,lock_file=self.lock,protected=lambda p:False,active=lambda p:False,free=lambda p:8)
    self.assertEqual(result.selected,('cam/2026/10/25/20261025T025900+0200.mp4',));self.assertTrue(earlier.exists());self.assertTrue(later.exists())
 def test_running_export_lock_skipped(self):
    a=self.make('a/2026/11/20/100000.mp4');fd,_=open_confined(self.root,'a/2026/11/20/100000.mp4');acquire_shared(fd)
    try:
        result=run(self.root,self.identity,minimum=10,target=11,lock_file=self.lock,protected=lambda p:False,active=lambda p:False,preview=False,free=lambda p:8)
        self.assertEqual(result.selected,());self.assertFalse(result.reached_target);self.assertTrue(a.exists())
    finally:os.close(fd)
 def test_activity_uncertainty_stops_deletion(self):
    a=self.make('a/2026/11/20/100000.mp4')
    def unknown(p):raise RuntimeError('cannot inspect')
    with self.assertRaises(RuntimeError):run(self.root,self.identity,minimum=10,target=11,lock_file=self.lock,protected=lambda p:False,active=unknown,preview=False,free=lambda p:8)
    self.assertTrue(a.exists())
 def test_file_replaced_after_scan_is_not_deleted(self):
    a=self.make('a/2026/11/20/100000.mp4');replacement=self.d/'replacement';replacement.write_bytes(b'new recording')
    def protected(_):
        if replacement.exists():os.replace(replacement,a)
        return False
    result=run(self.root,self.identity,minimum=10,target=11,lock_file=self.lock,protected=protected,active=lambda p:False,preview=False,free=lambda p:8)
    self.assertEqual(result.selected,());self.assertFalse(result.reached_target);self.assertEqual(a.read_bytes(),b'new recording')
 def test_file_modified_during_activity_check_is_not_deleted(self):
    a=self.make('a/2026/11/20/100000.mp4')
    def active(path):
        with path.open('ab') as file:file.write(b'new data')
        return False
    result=run(self.root,self.identity,minimum=10,target=11,lock_file=self.lock,protected=lambda p:False,active=active,preview=False,free=lambda p:8)
    self.assertEqual(result.selected,());self.assertFalse(result.reached_target);self.assertEqual(a.read_bytes(),b'abcnew data')
 def test_actual_rotation_reclaims_oldest_and_preserves_unrelated_and_protected_files(self):
    protected=self.make('a/2026/11/19/100000.mp4')
    oldest=self.make('a/2026/11/20/100000.mp4')
    newer=self.make('a/2026/11/21/100000.mp4')
    unrelated=self.make('a/2026/11/18/notes.txt');outside=self.d/'outside.mp4';outside.write_bytes(b'outside')
    link=oldest.parent/'090000.mp4';link.symlink_to(outside)
    removed=[]
    result=run(self.root,self.identity,minimum=10,target=11,lock_file=self.lock,
               protected=lambda p:p.endswith('2026/11/19/100000.mp4'),active=lambda p:False,
               preview=False,free=lambda p:8 if oldest.exists() else 11,on_remove=removed.append)
    self.assertEqual(result.selected,('a/2026/11/20/100000.mp4',));self.assertEqual(result.selected,tuple(removed))
    self.assertTrue(result.reached_target);self.assertEqual(result.after,11);self.assertFalse(oldest.exists())
    self.assertTrue(protected.exists() and newer.exists() and unrelated.exists() and link.is_symlink());self.assertEqual(outside.read_bytes(),b'outside')
 def test_mount_loss_after_first_deletion_stops_before_next_file(self):
    oldest=self.make('a/2026/11/20/100000.mp4');newer=self.make('a/2026/11/21/100000.mp4')
    def mount_guard():return oldest.exists()
    with self.assertRaisesRegex(RuntimeError,'identity changed'):
        run(self.root,self.identity,minimum=10,target=20,lock_file=self.lock,protected=lambda p:False,
            active=lambda p:False,preview=False,free=lambda p:8,mount_guard=mount_guard)
    self.assertFalse(oldest.exists());self.assertTrue(newer.exists())
if __name__=='__main__':unittest.main()
