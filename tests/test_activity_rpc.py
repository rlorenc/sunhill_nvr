import json,tempfile,unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.activity_rpc import read_message,handle_connection
class FakeSocket:
 def __init__(self,data):self.data=data;self.written=b''
 def recv(self,n):part=self.data[:n];self.data=self.data[n:];return part
 def sendall(self,data):self.written+=data
 def settimeout(self,t):pass
class RPCTests(unittest.TestCase):
 def test_bounded_json_message(self):
    self.assertEqual(read_message(FakeSocket(b'{"x":1}\n'))['x'],1)
    with self.assertRaises(ValueError):read_message(FakeSocket(b'x'*20000))
 def test_unauthorized_peer_gets_no_detail(self):
    c=FakeSocket(b'{}\n')
    with patch('sunhill_nvr.activity_rpc.peer_uid',return_value=123):handle_connection(c,allowed_uid=1000,archive=Path('/a'),staging=Path('/s'))
    self.assertFalse(json.loads(c.written)['ok'])
 def test_authenticated_caller_excluded_but_other_activity_is_kept(self):
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);file=root/'cam/2026/11/21/190900.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'data');info=file.stat()
        request=b'{"scope":"archive","paths":["cam/2026/11/21/190900.mp4"]}\n'
        for active in (False,True):
            c=FakeSocket(request)
            with patch('sunhill_nvr.activity_rpc.peer_uid',return_value=1000),patch('sunhill_nvr.activity_rpc.peer_pid',return_value=1234),patch('sunhill_nvr.activity_rpc.snapshot',return_value={(info.st_dev,info.st_ino)} if active else set()) as scan:
                handle_connection(c,allowed_uid=1000,archive=root,staging=root)
            scan.assert_called_once_with(ignore={1234})
            self.assertEqual(json.loads(c.written),{'ok':True,'active':[active]})
if __name__=='__main__':unittest.main()
