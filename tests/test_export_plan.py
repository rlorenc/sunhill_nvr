import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sys import path
path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sunhill_nvr.export_plan import Clip, create_plan

class ExportPlanTests(unittest.TestCase):
    def setUp(self): self.t = datetime(2026, 11, 21, tzinfo=timezone.utc)
    def clip(self, name, offset, duration):
        return Clip(Path(name), self.t + timedelta(seconds=offset), duration)
    def test_spans_multiple_files_exact_offsets(self):
        clips = [self.clip('a', 0, 60), self.clip('b', 60, 60), self.clip('c', 120, 60)]
        p = create_plan(clips, self.t+timedelta(seconds=30), self.t+timedelta(seconds=150))
        self.assertEqual([(s.path.name, s.offset, s.duration) for s in p.segments],
                         [('a',30.0,30.0), ('b',0.0,60.0), ('c',0.0,30.0)])
        self.assertFalse(p.gaps)
    def test_gaps_are_reported_never_hidden(self):
        p = create_plan([self.clip('a', 10, 10), self.clip('b', 30, 10)], self.t, self.t+timedelta(seconds=50))
        self.assertEqual([(g.start-self.t, g.end-self.t) for g in p.gaps],
                         [(timedelta(), timedelta(seconds=10)), (timedelta(seconds=20), timedelta(seconds=30)),
                          (timedelta(seconds=40), timedelta(seconds=50))])
    def test_overlap_does_not_duplicate_time(self):
        p = create_plan([self.clip('a', 0, 70), self.clip('b', 60, 60)], self.t, self.t+timedelta(seconds=120))
        self.assertEqual([(s.path.name, s.offset, s.duration) for s in p.segments], [('a',0,70), ('b',10,50)])
    def test_invalid_interval_fails(self):
        with self.assertRaises(ValueError): create_plan([], self.t, self.t)
    def test_dst_fall_back_is_real_elapsed_time(self):
        from zoneinfo import ZoneInfo
        zone = ZoneInfo('Europe/Warsaw')
        start = datetime(2026, 10, 25, 2, 30, fold=0, tzinfo=zone)
        end = datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=zone)
        p = create_plan([Clip(Path('a'),start,3600)],start,end)
        self.assertEqual(p.segments[0].duration,3600)
        self.assertFalse(p.gaps)
    def test_mixed_timezones_and_nan_are_rejected(self):
        with self.assertRaises(ValueError): create_plan([], self.t, self.t.replace(tzinfo=None))
        with self.assertRaises(ValueError): create_plan([self.clip('a',0,float('nan'))],self.t,self.t+timedelta(seconds=2))
if __name__ == '__main__': unittest.main()
