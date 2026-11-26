import tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.limits import slot,BusyError
class LimitTests(unittest.TestCase):
 def test_slots_are_bounded_and_released(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t)
        with slot(root,'thumbnail',1):
            with self.assertRaises(BusyError):
                with slot(root,'thumbnail',1):pass
        with slot(root,'thumbnail',1):pass
 def test_invalid_names_rejected(self):
    with tempfile.TemporaryDirectory() as t:
        with self.assertRaises(ValueError):
            with slot(Path(t),'../evil',1):pass
if __name__=='__main__':unittest.main()
