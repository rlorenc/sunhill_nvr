import sqlite3,struct,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sunhill_nvr import baichuan_metadata as m,baichuan_control as bc,object_analytics as a,database,security,archive,native_capabilities as caps
from test_baichuan_control import FakeCamera

# Native capture from nodelink-js test/lib/detection-decoder.test.ts (MIT,
# see docs/baichuan-motion-boxes.md): SDK-confirmed person [189,267,332,296,34].
CAPTURE=bytes.fromhex('ffb400010b00010800057c864ff000000002a3000104006c7a342002990004224d186440a786000000f009016d00032a00010800a5b8854ff0000000050800d17d9d000100ff070304008003e001040a00bd000b014c0128012200011d2d000b1f0220000d4f7100032e43000b27040e70004f6265000274002d4f0360000397000c0f63002d50040b00fc08340101020011056e0031057c865201f0030208000cf03a010000000003040000000000000000004995ebbafb040001010000f9040001010000000000')
EMPTY=bytes.fromhex('ff7800010b0001080ec0da4462a9b5919580927835ef19b249eb018173a8345d40e424968d5c8e74f3baad11c7275a75db5e2cb53fcd3adb48e83fcf660c89bc1b7a7ee335a6e93efc3d692e8b79f233e062e31a79d670fdbd19e67b2597ba313ec7423fc966f4b593e74c721c60c025717556119de7fc44b1f02f3613ade947')


def frame(extra,tick=1000,iframe=False):
    payload=b'\x00\x00\x00\x01'+b'x'*19
    return struct.pack('<I4sIIII',0x63643030 if iframe else 0x63643130,b'H264',len(payload),len(extra),tick,0)+extra+payload+b'\0'*(-len(payload)%8)


class PreviewCamera(FakeCamera):
    def sendall(self,data):
        magic,cmd,n,mid,status,kind,off=struct.unpack('<IIIIHHI',data[:24]) if len(data)>=24 else (*struct.unpack('<IIIIHH',data[:20]),0)
        if cmd!=3:return super().sendall(data)
        self.commands.append(cmd)
        assert mid&255==0 and (mid>>8)&255==1
        assert b'<channelId>0</channelId>' in bc.aes(data[24:24+off],self.key,True)
        xml=bc.aes(data[24+off:],self.key,True)
        assert b'<handle>256</handle>' in xml and b'<streamType>subStream</streamType>' in xml
        stamp=int(time.time())
        wire=frame(struct.pack('<II',stamp,0)+CAPTURE,iframe=True)
        def send(body):self.buffer+=struct.pack('<IIIIHHI',magic,3,len(body),mid,200,0,0)+body
        send(b'');send(wire[:15]);send(wire[15:])


class MotionBoxesTests(unittest.TestCase):
    def test_captured_metadata(self):
        state,obs=m.boxes(CAPTURE,False);self.assertEqual(state,'boxes');self.assertEqual(len(obs),1)
        self.assertEqual(obs[0]['kind'],'person');self.assertFalse(obs[0]['tracked']);self.assertAlmostEqual(obs[0]['confidence'],.34)
        for actual,expected in zip(obs[0]['box'],[189/896,267/480,143/896,29/480]):self.assertAlmostEqual(actual,expected)
        self.assertEqual(m.boxes(EMPTY,False),('empty',[]));self.assertIsNone(m.boxes(b'garbage',False))
        self.assertEqual(m.boxes(b'\0'*8+CAPTURE,True)[0],'boxes')
    def test_fragmented_media_and_timestamp(self):
        stamp=1700000000000;parser=m.Media()
        data=frame(struct.pack('<II',stamp//1000,0)+CAPTURE,1000,True)+frame(EMPTY,1001000)
        out=[]
        for i in range(0,len(data),7):out.extend(parser.feed(data[i:i+7],stamp+1000))
        self.assertEqual([(x[0],x[1]) for x in out],[(stamp,'boxes'),(stamp+1000,'empty')])
        self.assertEqual(parser.buffer,b'')
    def test_bounds_and_corrupt_lz4(self):
        with self.assertRaises(ValueError):m.decompress(b'\x04\x22\x4d\x18'+b'\0'*20)
        with self.assertRaises(ValueError):list(m.Media().feed(struct.pack('<I4sIIII',0x63643130,b'H264',m.MAX_FRAME+1,0,0,0),0))
        with self.assertRaises(ValueError):list(m.Media().feed(b'garbage',0))
    def test_real_framing_adapter(self):
        fake=PreviewCamera();stop=threading.Event();results=[];ready=[]
        def emit(stamp,objects):results.extend(objects);stop.set()
        with patch.object(bc.socket,'create_connection',return_value=fake),patch('subprocess.Popen',side_effect=AssertionError('no FFmpeg')):
            m.run('rtsp://user:pass@192.168.1.30/h264Preview_01_main',stop,emit,lambda:ready.append(True))
        self.assertTrue(fake.closed);self.assertEqual(fake.commands,[1,1,80,3]);self.assertEqual(len(ready),1);self.assertEqual(results[0]['kind'],'person')
    def test_worker_persists_native_capability_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'db.sqlite';path.touch(mode=0o600);db=database.open_db(path);self.addCleanup(db.close)
            security.initialize(db);database.migrate(db);archive.initialize(db)
            url='rtsp://user:pass@192.168.1.30/h264Preview_01_main'
            db.execute("INSERT INTO cameras VALUES(1,'cam','Cam',1)")
            db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(1,?,'rtsp')",(url,))
            db.execute("INSERT INTO camera_event_status VALUES(1,'baichuan','active',0,'[]')");db.commit()
            stop=threading.Event();fingerprint=caps.key(db,1)
            def run(url,stop,emit,ready,profile):
                self.assertEqual(profile,'sub');ready();emit(int(time.time()*1000),m.boxes(CAPTURE,False)[1]);stop.set()
            with patch('sunhill_nvr.workers.writes_permitted',return_value=True),patch.object(m,'run',side_effect=run),patch('subprocess.Popen',side_effect=AssertionError('no local decoding')):
                a.run_camera(SimpleNamespace(database=path),1,url,stop,fingerprint)
            self.assertTrue({'boxes','person','baichuan_boxes','baichuan_sub_boxes'}<=caps.supported(db,1))
            self.assertEqual(a.history(db,1,int(time.time()*1000))[0]['objects'][0]['kind'],'person')
            db.execute("UPDATE camera_details SET main_url=? WHERE camera_id=1",(url+'2',));db.commit();self.assertEqual(caps.supported(db,1),set())

if __name__=='__main__':unittest.main()
