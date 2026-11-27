"""Small, bounded LAN-only Baichuan TCP adapter for standalone Reolink cameras.

Protocol references: reolink_aio (MIT), Baichuan protocol commands/params
at jsr.io/@hertzg/reolink-api. Only login, version, floodlight read and
manual switch are implemented; no arbitrary command interface to the web.
"""
from __future__ import annotations
import ctypes as C
import hashlib
import socket
import struct
import time
import xml.etree.ElementTree as ET
from .camera_discovery import parse_xml, validate_input


class ProtocolError(ValueError):
    pass


class Rejected(ProtocolError):
    pass


_MAGIC=0x0abcdef0
_MAX=131072
_XOR=bytes.fromhex('1f2d3c4b5a6978ff')


def xor(data,channel):
    return bytes(b ^ _XOR[(channel+i)%8] ^ channel for i,b in enumerate(data))


def aes(data,key,decrypt=False):
    """OpenSSL EVP from the existing OS dependency; no keys in subprocess argv."""
    if not data:return b''
    lib=C.CDLL('libcrypto.so.3')
    ptr=C.c_void_p;integer=C.c_int
    for name,args,result in (
        ('EVP_CIPHER_CTX_new',[],ptr),('EVP_CIPHER_CTX_free',[ptr],None),
        ('EVP_aes_128_cfb128',[],ptr),('EVP_CipherInit_ex',[ptr,ptr,ptr,C.c_char_p,C.c_char_p,integer],integer),
        ('EVP_CipherUpdate',[ptr,ptr,C.POINTER(integer),ptr,integer],integer),
        ('EVP_CipherFinal_ex',[ptr,ptr,C.POINTER(integer)],integer)):
        f=getattr(lib,name);f.argtypes=args;f.restype=result
    ctx=lib.EVP_CIPHER_CTX_new()
    if not ctx:raise ProtocolError('Baichuan: brak kontekstu szyfrowania.')
    try:
        out=C.create_string_buffer(len(data)+32);n=integer();tail=integer();src=C.create_string_buffer(data)
        if lib.EVP_CipherInit_ex(ctx,lib.EVP_aes_128_cfb128(),None,key,b'0123456789abcdef',int(not decrypt))!=1 or lib.EVP_CipherUpdate(ctx,out,C.byref(n),src,len(data))!=1 or lib.EVP_CipherFinal_ex(ctx,C.byref(out,n.value),C.byref(tail))!=1:
            raise ProtocolError('Baichuan: błąd szyfrowania.')
        return out.raw[:n.value+tail.value]
    finally:lib.EVP_CIPHER_CTX_free(ctx)


def xml(tag,fields,*,wrap=True):
    node=ET.Element(tag,{'version':'1.1'})
    for name,value in fields.items():ET.SubElement(node,name).text=str(value)
    if wrap:
        root=ET.Element('body');root.append(node);node=root
    return ET.tostring(node,encoding='utf-8',xml_declaration=True)


def safe_text(value):
    return value.strip()[:120] if isinstance(value,str) and all(ord(c)>=32 for c in value) else ''


class Baichuan:
    def __init__(self,ip,username,password):
        self.ip=validate_input(ip,username,password);self.username=username;self.password=password
        self.sock=None;self.key=None;self.seq=0;self.deadline=time.monotonic()+10;self.device={};self.light_available=False

    def remaining(self):
        t=self.deadline-time.monotonic()
        if t<=0:raise ProtocolError('Baichuan: przekroczono czas odpowiedzi.')
        return min(2.5,t)

    def receive(self,n):
        result=bytearray()
        while len(result)<n:
            self.sock.settimeout(self.remaining());part=self.sock.recv(n-len(result))
            if not part:raise ProtocolError('Baichuan: kamera zamknęła połączenie.')
            result.extend(part)
        return bytes(result)

    def read_frame(self):
        h=self.receive(20);magic,returned,length,returned_id,status,kind=struct.unpack('<IIIIHH',h)
        if magic!=_MAGIC or length>_MAX or kind not in (0x6414,0x6514,0x6614,0x0000):raise ProtocolError('Baichuan: nieprawidłowa ramka.')
        offset=struct.unpack('<I',self.receive(4))[0] if kind in (0x6414,0x0000) else 0
        if offset>length:raise ProtocolError('Baichuan: nieprawidłowy offset.')
        return returned,returned_id,status,kind,self.receive(length)[offset:]

    def decode_body(self,raw,mid):
        if not raw:return None
        for decode in (lambda b:aes(b,self.key,True),lambda b:xor(b,mid&255)):
            try:return parse_xml(decode(raw))
            except (ValueError,ET.ParseError,UnicodeError):continue
        raise ProtocolError('Baichuan: nieprawidłowe XML zdarzenia.')

    def request(self,cmd,body=b'',*,channel=None,nonce=False,login=False,push_channel=False,on_push=None,binary=False):
        # A new sequence per request, low byte encodes host/channel.
        self.seq=(self.seq+1)%0x1000000;ch=251 if push_channel else (250 if channel is None else channel+1)
        mid=(self.seq<<8)|ch
        if binary and (cmd!=202 or channel!=0 or not isinstance(body,bytes) or not 0<len(body)<=8192):raise ProtocolError('Invalid binary talk frame')
        extension=xml('Extension',{'channelId':channel,**({'binaryData':1} if binary else {})},wrap=False) if channel is not None else b''
        encode=(lambda b:xor(b,ch)) if nonce or login else (lambda b:aes(b,self.key))
        payload=encode(extension)+(body if binary else encode(body))
        header=struct.pack('<IIII',_MAGIC,cmd,len(payload),mid)
        header+=b'\x12\xdc\x14\x65' if nonce else b'\x00\x00\x14\x64'+struct.pack('<I',len(extension))
        self.sock.settimeout(self.remaining());self.sock.sendall(header+payload)
        # Unsolicited notifications may arrive between request and response.
        for _ in range(8):
            h=self.receive(20);magic,returned,length,returned_id,status,kind=struct.unpack('<IIIIHH',h)
            if magic!=_MAGIC or length>_MAX or kind not in (0x6414,0x6514,0x6614,0x0000):
                raise ProtocolError(f'Baichuan: nieprawidłowa ramka (cmd={returned}, klasa={h[18:20].hex()}, status={status}, magic={magic:08x}, długość={length}).')
            offset=struct.unpack('<I',self.receive(4))[0] if kind in (0x6414,0x0000) else 0
            if offset>length:raise ProtocolError('Baichuan: nieprawidłowy offset.')
            raw=self.receive(length)
            if returned!=cmd or returned_id!=mid:
                if on_push is not None and returned==33 and status in (0,200):on_push(self.decode_body(raw[offset:],returned_id))
                continue
            if not (status==200 or (nonce and kind in (0x6514,0x6614) and status in (0xdd00,0xdd01,0xdd02,0xdd03,0xdd12))):
                raise Rejected(f'Baichuan: kamera odrzuciła polecenie (cmd={cmd}, kod={status}, klasa={h[18:20].hex()}).')
            raw=raw[offset:]
            if not raw or binary:return None
            # Login replies may be XOR or AES depending on firmware.
            decoders=[lambda b:xor(b,returned_id&255)] if nonce or login else [lambda b:aes(b,self.key,True),lambda b:xor(b,returned_id&255)]
            if nonce and status==0xdd00:decoders.insert(0,lambda b:b)
            if login:decoders.append(lambda b:aes(b,self.key,True))
            for decode in decoders:
                try:return parse_xml(decode(raw))
                except (ValueError,ET.ParseError,UnicodeError):continue
            raise ProtocolError(f'Baichuan: odpowiedź XML jest nieprawidłowa (cmd={cmd}, klasa={h[18:20].hex()}, status={status}).')
        raise ProtocolError('Baichuan: brak odpowiedzi na polecenie.')

    def __enter__(self):
        try:
            self.sock=socket.create_connection((self.ip,9000),timeout=self.remaining())
            root=self.request(1,nonce=True);nonce=root.findtext('.//nonce') if root is not None else None
            if not nonce or len(nonce)>256:raise ProtocolError('Baichuan: kamera nie zwróciła wyzwania logowania.')
            digest=lambda value:hashlib.md5(value.encode(),usedforsecurity=False).hexdigest().upper()[:31]
            self.key=digest(nonce+'-'+self.password)[:16].encode('ascii')
            root=ET.Element('body');user=ET.SubElement(root,'LoginUser',{'version':'1.1'})
            for k,v in {'userName':digest(self.username+nonce),'password':digest(self.password+nonce),'userVer':'1'}.items():ET.SubElement(user,k).text=v
            net=ET.SubElement(root,'LoginNet',{'version':'1.1'});ET.SubElement(net,'type').text='LAN';ET.SubElement(net,'udpPort').text='0'
            root=self.request(1,ET.tostring(root,encoding='utf-8',xml_declaration=True),login=True)
            info=root.find('.//DeviceInfo') if root is not None else None
            if info is None or (info.findtext('type') or '').lower() not in ('ipc','wifi_ipc','wifi_solo_ipc','nxp_ipc','smart_bell') or any(info.findtext(k) not in (None,'0','1') for k in ('channelNum','analogChnNum','digitalChnNum')):
                raise ProtocolError('Baichuan: nie potwierdzono samodzielnej kamery.')
            self.device={'manufacturer':'Reolink','protocol':'Baichuan TCP 9000'}
            try:
                version=self.request(80);v=version.find('.//VersionInfo') if version is not None else None
                if v is not None:
                    model=safe_text(v.findtext('model') or v.findtext('type'))
                    if model and model.lower() not in ('ipc','wifi_ipc','wifi_solo_ipc','nxp_ipc','smart_bell'):self.device['model']=model
                    hardware=safe_text(v.findtext('hardwareVersion'))
                    if hardware:self.device['hardware']=hardware
                    firmware=safe_text(v.findtext('firmwareVersion'))
                    if firmware:self.device['firmware']=firmware
            except Rejected:pass
            return self
        except BaseException:
            self.close();raise

    def close(self):
        if self.sock:self.sock.close();self.sock=None
        self.key=None

    def __exit__(self,*_):self.close()

    def light(self):
        # A successful GetFloodlightTask with channel 0 is positive evidence.
        # Its scheduling flag is NOT the current physical light state.
        root=None
        unknown={}
        for command in (289,438):
            try:root=self.request(command,channel=0)
            except Rejected:continue
            task=root.find('.//FloodlightTask') if root is not None else None
            if task is not None and task.findtext('channel') in ('0',None):
                self.light_available=True
                state=task.findtext('status')
                if state in ('0','1'):return {'light_available':True,'light':state=='1'}
                unknown={'light_available':True}
            if root is not None:
                for state in root.findall('.//FloodlightStatus'):
                    if state.findtext('channel')=='0' and state.findtext('status') in ('0','1'):
                        self.light_available=True;return {'light_available':True,'light':state.findtext('status')=='1'}
        return unknown

    def switch(self,on):
        if type(on) is not bool or not self.light().get('light_available'):raise ProtocolError('Baichuan: kamera nie potwierdziła obsługi reflektora.')
        self.request(288,xml('FloodlightManual',{'channel':0,'status':int(on)}),channel=0)
