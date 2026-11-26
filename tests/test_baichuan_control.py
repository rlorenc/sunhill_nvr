import hashlib
import struct
import unittest
from unittest.mock import patch
from sunhill_nvr import baichuan_control as b
from sunhill_nvr import camera_control as c

class FakeCamera:
    def __init__(self,device='wifi_ipc',light=True,nonce_class=b'\x14\x66',modern_class=b'\x00\x00'):
        self.device=device;self.light=light;self.buffer=b'';self.commands=[];self.closed=False
        self.nonce_class=nonce_class;self.modern_class=modern_class
        self.key=hashlib.md5(b'abc-pass',usedforsecurity=False).hexdigest().upper()[:16].encode()
    def settimeout(self,t):pass
    def close(self):self.closed=True
    def recv(self,n):
        data=self.buffer[:min(n,7)];self.buffer=self.buffer[len(data):];return data
    def sendall(self,data):
        magic,cmd,length,mid,status,kind=struct.unpack('<IIIIHH',data[:20]);assert magic==0x0abcdef0
        self.commands.append(cmd);ch=mid&255
        nonce=kind==0x6514
        if nonce:
            assert cmd==1 and length==0
            reply=b'<body><Encryption><nonce>abc</nonce></Encryption></body>';code=0xdd12
        else:
            off=struct.unpack('<I',data[20:24])[0];payload=data[24:];assert len(payload)==length
            raw=b.xor(payload[off:],ch) if cmd==1 else b.aes(payload[off:],self.key,True)
            if cmd==1:
                assert hashlib.md5(b'userabc',usedforsecurity=False).hexdigest().upper()[:31].encode() in raw
                assert hashlib.md5(b'passabc',usedforsecurity=False).hexdigest().upper()[:31].encode() in raw
                reply=('<body><DeviceInfo><type>'+self.device+'</type><channelNum>1</channelNum></DeviceInfo></body>').encode();code=200
            elif cmd==80:reply=b'<body><VersionInfo><type>Reolink Lumus</type><hardwareVersion>IPC_TEST</hardwareVersion></VersionInfo></body>';code=200
            elif cmd in (289,438):reply=b'<body><FloodlightTask><channel>0</channel><enable>1</enable></FloodlightTask></body>' if self.light else b'<body/>';code=200
            elif cmd==288:
                assert b'<status>1</status>' in raw and b'<channel>0</channel>' in raw
                reply=b'';code=200
            else:raise AssertionError(cmd)
        # Independent response framing; do not echo request class.
        key=(31,45,60,75,90,105,120,255)
        wire=bytes(x ^ ch ^ key[(i+ch)%8] for i,x in enumerate(reply)) if cmd==1 else b.aes(reply,self.key)
        cls=self.nonce_class if nonce else self.modern_class
        self.buffer+=bytes.fromhex('f0debc0a')+cmd.to_bytes(4,'little')+len(wire).to_bytes(4,'little')+mid.to_bytes(4,'little')+code.to_bytes(2,'little')+cls+(b'' if nonce else bytes(4))+wire

class NativeTests(unittest.TestCase):
    def test_login_identify_light_and_switch(self):
        fake=FakeCamera()
        with patch.object(b.socket,'create_connection',return_value=fake):
            with b.Baichuan('192.168.1.30','user','pass') as client:
                self.assertEqual(client.device['model'],'Reolink Lumus')
                self.assertEqual(c.capabilities(client)['controls'],{'light_available':True})
                c.apply(client,'light',True)
        self.assertTrue(fake.closed);self.assertEqual(fake.commands,[1,1,80,289,289,288])
    def test_unsupported_never_switch(self):
        fake=FakeCamera(light=False)
        with patch.object(b.socket,'create_connection',return_value=fake):
            with b.Baichuan('192.168.1.30','user','pass') as client:
                with self.assertRaises(c.ControlError):c.apply(client,'light',True)
        self.assertNotIn(288,fake.commands)
    def test_reject_nvr(self):
        fake=FakeCamera(device='nvr')
        with patch.object(b.socket,'create_connection',return_value=fake):
            with self.assertRaises(b.ProtocolError):
                with b.Baichuan('192.168.1.30','user','pass'):pass
        self.assertTrue(fake.closed)
    def test_fallback(self):
        fake=FakeCamera()
        with patch.object(c.Reolink,'_call',side_effect=c.TransportUnavailable('HTTP unavailable')),patch.object(b.socket,'create_connection',return_value=fake):
            with c.Reolink('rtsp://user:pass@192.168.1.30/a') as client:self.assertEqual(client.device['protocol'],'Baichuan TCP 9000')
        self.assertTrue(fake.closed)
    def test_auth_rejected_no_fallback(self):
        with patch.object(c.Reolink,'_call',side_effect=c.Unsupported('auth')),patch.object(b.socket,'create_connection') as conn:
            with self.assertRaises(c.ControlError):
                with c.Reolink('rtsp://user:pass@192.168.1.30/a'):pass
            conn.assert_not_called()
    def test_legacy_and_modern_class_compatibility(self):
        for nonce_cls in (bytes.fromhex('1465'),bytes.fromhex('1466')):
            for modern_cls in (bytes.fromhex('1464'),bytes.fromhex('0000')):
                fake=FakeCamera(nonce_class=nonce_cls,modern_class=modern_cls)
                with patch.object(b.socket,'create_connection',return_value=fake):
                    with b.Baichuan('192.168.1.30','user','pass') as client:
                        self.assertEqual(client.device['model'],'Reolink Lumus')
                        self.assertTrue(client.light()['light_available'])
    def test_invalid_frame_has_safe_diagnostics(self):
        fake=FakeCamera(nonce_class=bytes.fromhex('abcd'))
        with patch.object(b.socket,'create_connection',return_value=fake):
            with self.assertRaisesRegex(b.ProtocolError,'cmd=1, klasa=abcd') as err:
                with b.Baichuan('192.168.1.30','user','pass'):pass
        self.assertNotIn('pass',str(err.exception));self.assertTrue(fake.closed)
    def test_public_ip_rejected(self):
        with self.assertRaises(ValueError):b.Baichuan('8.8.8.8','user','pass')

if __name__=='__main__':unittest.main()
