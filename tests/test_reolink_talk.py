import struct,unittest
from unittest.mock import patch,Mock,MagicMock
from xml.etree import ElementTree as ET
from sunhill_nvr import reolink_talk as t, device_diagnosis as d, talkback, baichuan_control as bc
from test_baichuan_control import FakeCamera

XML=b'<body><TalkAbility><duplexList><duplex>FDX</duplex></duplexList><audioStreamModeList><audioStreamMode>followVideoStream</audioStreamMode></audioStreamModeList><audioConfigList><audioConfig><audioType>adpcm</audioType><sampleRate>16000</sampleRate><samplePrecision>16</samplePrecision><lengthPerEncoder>1024</lengthPerEncoder><soundTrack>mono</soundTrack></audioConfig></audioConfigList></TalkAbility></body>'
URL='rtsp://user:pass@192.168.1.2/main'
class NativeTalkTests(unittest.TestCase):
    def test_negotiation_strict(self):
        self.assertEqual(t.ability(ET.fromstring(XML)),('FDX','followVideoStream',16000,1024))
        for bad in (XML.replace(b'adpcm',b'aac'),XML.replace(b'mono',b'stereo'),XML.replace(b'1024',b'99999'),b'<body/>'):
            with self.assertRaises(ValueError):t.ability(ET.fromstring(bad))
    def test_encoder_and_media_size(self):
        block=t.encode_block([0]*1025);self.assertEqual(block,bytes(516))
        packet=t.media_packet(block);self.assertEqual(packet[:4],b'01wb');self.assertEqual(struct.unpack('<HHHH',packet[4:12]),(520,520,256,512));self.assertEqual(len(packet)%8,0)
        # Nonzero predictor, sign and low-nibble-first order.
        self.assertEqual(t.encode_block([0,7,-7]),b'\x00\x00\x00\x00\xe4')
    def test_start_stream_stop(self):
        client=MagicMock();client.request.side_effect=lambda cmd,*args,**kw:ET.fromstring(XML) if cmd==10 else None
        with patch.object(t,'native_client',return_value=client):
            session=t.NativeBackchannel(URL);session.__enter__();session.start()
            for _ in range(4):session.send_pcm(bytes(320))
            session.finish()
        calls=client.request.call_args_list
        self.assertEqual([c.args[0] for c in calls],[10,201,202,11]);self.assertTrue(calls[2].kwargs['binary']);self.assertEqual(calls[2].args[1][:4],b'01wb');client.close.assert_called_once()
    def test_native_wire_commands_and_binary_extension(self):
        class Camera(FakeCamera):
            def sendall(self,data):
                magic,cmd,length,mid,status,kind=struct.unpack('<IIIIHH',data[:20])
                if cmd not in (10,201,202,11):return super().sendall(data)
                self.commands.append(cmd);off=struct.unpack('<I',data[20:24])[0]
                extension=ET.fromstring(bc.aes(data[24:24+off],self.key,True))
                assert extension.findtext('channelId')=='0'
                payload=data[24+off:]
                if cmd==201:
                    assert ET.fromstring(bc.aes(payload,self.key,True)).findtext('.//sampleRate')=='16000'
                if cmd==202:
                    assert extension.findtext('binaryData')=='1' and payload[:4]==b'01wb'
                    assert len(payload)==528
                reply=bc.aes(XML,self.key) if cmd==10 else b''
                self.buffer+=struct.pack('<IIIIHHI',0x0abcdef0,cmd,len(reply),mid,200,0x6414,0)+reply
        fake=Camera()
        with patch.object(bc.socket,'create_connection',return_value=fake):
            session=t.NativeBackchannel(URL);session.__enter__();session.start()
            for _ in range(4):session.send_pcm(bytes(320))
            session.finish()
        self.assertEqual(fake.commands,[1,1,80,10,201,202,11]);self.assertTrue(fake.closed)
    def test_diagnosis_native_and_sub_stream(self):
        row=(URL,None,None,None,None,URL+'/sub')
        result={'controls':{},'settings':[],'device':{'manufacturer':'Reolink','model':'Lumus'},'partial':False}
        with patch.object(d.camera_control,'Reolink'),patch.object(d.camera_control,'capabilities',return_value=result),patch.object(d.camera_settings,'read',return_value=([],False)),patch.object(d.camera_control,'supplement_identity',side_effect=lambda r,*a:r),patch.object(d,'probe_talkback',side_effect=[False,True]),patch.object(d.reolink_talk,'probe') as native:
            self.assertEqual(d.diagnose(row)['talkback_stream'],1);native.assert_not_called()
        with patch.object(d.camera_control,'Reolink'),patch.object(d.camera_control,'capabilities',return_value=result),patch.object(d.camera_settings,'read',return_value=([],False)),patch.object(d.camera_control,'supplement_identity',side_effect=lambda r,*a:r),patch.object(d,'probe_talkback',return_value=False),patch.object(d.reolink_talk,'probe',return_value=True):
            self.assertEqual(d.diagnose(row)['talkback_transport'],'baichuan')
    def test_session_level_sdp(self):
        sdp=b'v=0\r\na=sendonly\r\nm=audio 0 RTP/AVP 8\r\na=control:talk\r\n'
        self.assertEqual(talkback.audio_track(sdp,'rtsp://192.168.1.2:554/main/','192.168.1.2',554)[1:],(8,'PCMA'))
