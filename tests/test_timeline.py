import unittest
from datetime import datetime,timezone
from pathlib import Path
from sys import path
from zoneinfo import ZoneInfo
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.timeline import *

class TimeZoneTests(unittest.TestCase):
 def setUp(self):self.zone=ZoneInfo('Europe/Warsaw')
 def test_ordinary(self):
    self.assertEqual(resolve_local(datetime(2026,11,21,19,9),self.zone),datetime(2026,11,21,18,9,tzinfo=timezone.utc))
 def test_repeated_hour_requires_explicit_selection(self):
    local=datetime(2026,10,25,2,30)
    self.assertEqual(len(utc_candidates(local,self.zone)),2)
    with self.assertRaises(AmbiguousLocalTime):resolve_local(local,self.zone)
    self.assertEqual((resolve_local(local,self.zone,fold=1)-resolve_local(local,self.zone,fold=0)).total_seconds(),3600)
 def test_spring_gap_rejected(self):
    with self.assertRaises(NonexistentLocalTime):resolve_local(datetime(2026,3,29,2,30),self.zone)
if __name__=='__main__':unittest.main()
