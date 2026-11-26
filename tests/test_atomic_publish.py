import io,json,os,stat,tempfile,unittest
from pathlib import Path
from sys import path
from unittest.mock import patch
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import domain_inbox,tls_inbox,storage_inbox,upgrade_inbox

class InboxPublicationTests(unittest.TestCase):
    def test_crash_after_publish_never_leaves_two_links_or_unreadable_request(self):
        for kind in ('domain','tls','storage','upgrade'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                if kind=='domain':
                    destination=root/'domain-request.json';submit=lambda:domain_inbox.submit(root,'nvr.example.com',1)
                elif kind=='tls':
                    destination=root/'tls-request.json';submit=lambda:tls_inbox.submit(root,'certificate','private-key',1)
                elif kind=='storage':
                    destination=root/'storage-request.json';submit=lambda:storage_inbox.submit(root,'/data/archive',1)
                else:
                    destination=root/'request.json';a=upgrade_inbox.store(root,'archive',io.BytesIO(b'a'),1);s=upgrade_inbox.store(root,'signature',io.BytesIO(b's'),1)
                    submit=lambda:upgrade_inbox.request(root,a,s,1)
                original=os.fsync
                def interrupt(fd):
                    if stat.S_ISDIR(os.fstat(fd).st_mode):
                        # Assert the actual state at the interruption, not only
                        # after Python finally blocks have had time to clean up.
                        self.assertEqual(destination.stat().st_nlink,1)
                        self.assertFalse(any(p.name.startswith(('.request-','.domain-request-','.tls-request-','.storage-request-')) for p in root.iterdir()))
                        raise KeyboardInterrupt('interrupted directory sync')
                    original(fd)
                with patch('sunhill_nvr.atomic_publish.os.fsync',side_effect=interrupt):
                    with self.assertRaises(KeyboardInterrupt):submit()
                self.assertEqual(destination.stat().st_nlink,1)
                content=destination.read_bytes();self.assertEqual(json.loads(content)['user_id'],1)
                with self.assertRaises(FileExistsError):submit()
                self.assertEqual(destination.read_bytes(),content)
                if kind=='upgrade':self.assertEqual(upgrade_inbox.read_request(root)['user_id'],1)

if __name__=='__main__':unittest.main()
