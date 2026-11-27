import os,sqlite3,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import security,database
from sunhill_nvr.controller import reconcile_gateway,require_trusted_directory,ControllerError
class ControllerTests(unittest.TestCase):
 def test_readiness_accepts_fragmented_real_protocol_greetings(self):
    import socket,threading,time
    from sunhill_nvr.controller import service_ready
    for service,parts in [('sunhill-ftp.service',[b'2',b'20 Ready\r',b'\n']),('sunhill-ftp.service',[b'220-Welcome\r\n220-',b'Info\r\n220 Ready\r\n']),('sunhill-go2rtc.service',[b'HT',b'TP/1.1 ',b'200 OK\r\n'])]:
        with self.subTest(service=service):
            client,server=socket.socketpair()
            def respond():
                with server:
                    if service=='sunhill-go2rtc.service':server.recv(1024)
                    for part in parts:server.sendall(part);time.sleep(.01)
            worker=threading.Thread(target=respond);worker.start()
            try:
                with patch('sunhill_nvr.controller.socket.create_connection',return_value=client):self.assertTrue(service_ready(service,timeout=.5))
            finally:client.close();worker.join(1)
 def test_readiness_rejects_invalid_http_status_and_incomplete_greeting(self):
    import socket,time
    from sunhill_nvr.controller import service_ready
    for service,payload in [('sunhill-go2rtc.service',b'HTTP/1.1 2000 bogus\r\n'),('sunhill-ftp.service',b'220'),('sunhill-ftp.service',b'220-Welcome\r\n'),('sunhill-ftp.service',b'220 Ready\n')]:
        client,server=socket.socketpair()
        with client,server:
            server.sendall(payload);start=time.monotonic()
            with patch('sunhill_nvr.controller.socket.create_connection',return_value=client):self.assertFalse(service_ready(service,timeout=.1))
            self.assertLess(time.monotonic()-start,.5)
 def test_gateway_rejects_failed_start_and_keeps_pending(self):
    from sunhill_nvr.config_reconcile import ReconcileError
    db=sqlite3.connect(':memory:');self.addCleanup(db.close);security.initialize(db);database.migrate(db)
    with tempfile.TemporaryDirectory() as t:
        root=Path(t);calls=[]
        def run(args,**kw):
            calls.append(args);return subprocess.CompletedProcess(args,1 if args[1]=='restart' else 0)
        with self.assertRaises(ReconcileError):reconcile_gateway(db,root,os.getgid(),run=run,trusted_check=lambda p:None,readiness=lambda unit:True)
        self.assertFalse((root/'current').exists())
        self.assertTrue(all(args[-1]=='sunhill-go2rtc.service' for args in calls))
 def test_untrusted_directory_rejected(self):
    with tempfile.TemporaryDirectory() as t:
        p=Path(t);p.chmod(0o777)
        with self.assertRaises(ControllerError):require_trusted_directory(p)
if __name__=='__main__':unittest.main()
