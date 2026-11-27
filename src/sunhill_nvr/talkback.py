"""Admin-only ONVIF RTSP G.711 backchannel, via authenticated WebSocket.

Audio is PCM16LE mono 8 kHz, 20 ms/frame, never persisted. Only a camera's
explicit sendonly G.711 audio track is used. No alternate hosts or codecs.
"""
from __future__ import annotations
import asyncio
import base64
import contextlib
import hashlib
import ipaddress
import re
import secrets
import struct
import time
from . import database, security, workers, onvif_metadata as rtsp

REQUIRE={'Require':'www.onvif.org/ver20/backchannel'}
FRAME_SAMPLES=160
FRAME_BYTES=FRAME_SAMPLES*2
MAX_SECONDS=120


def audio_track(sdp,base,ip,port):
    sections=[];current=None
    for line in sdp.decode('utf-8').splitlines():
        if line.startswith('m='):
            current=(line[2:].split(),[]);sections.append(current)
        elif current is not None and line.startswith('a='):current[1].append(line[2:].strip())
    choices=[]
    for media,attrs in sections:
        if len(media)<4 or media[0]!='audio' or media[2].upper() not in ('RTP/AVP','RTP/AVP/TCP') or 'sendonly' not in attrs:continue
        control=next((v[8:] for v in attrs if v.startswith('control:')),None)
        if not control or control=='*':continue
        payloads={int(x) for x in media[3:] if re.fullmatch(r'\d{1,3}',x) and int(x)<=127}
        maps={0:'PCMU',8:'PCMA'}
        for a in attrs:
            if not a.lower().startswith('rtpmap:'):continue
            match=re.fullmatch(r'rtpmap:(\d{1,3})\s+(PCMU|PCMA)/8000(?:/1)?',a,re.I)
            ident=re.match(r'rtpmap:(\d{1,3})\s',a,re.I)
            if ident:maps.pop(int(ident[1]),None)
            if match:maps[int(match[1])]=match[2].upper()
        for pt in payloads:
            if pt in maps:choices.append((maps[pt]!='PCMU',pt,rtsp.control_uri(base,control,ip,port),maps[pt]))
    if not choices:raise ValueError('No ONVIF G.711 backchannel')
    _,pt,uri,codec=min(choices);return uri,pt,codec


def mulaw(sample):
    sign=0x80 if sample<0 else 0
    value=min(abs(sample),32635)+132
    exponent=max(0,value.bit_length()-8)
    mantissa=(value>>(exponent+3))&15
    return (~(sign|exponent<<4|mantissa))&255


def alaw(sample):
    sign=0x80 if sample>=0 else 0
    value=sample if sample>=0 else -sample-1
    exponent=max(0,value.bit_length()-8)
    mantissa=(value>>(exponent+3 if exponent else 4))&15
    return (sign|exponent<<4|mantissa)^0x55


class Backchannel(rtsp.Client):
    def __init__(self,url):
        super().__init__(url);self.base=None;self.pt=None;self.codec=None
        self.seq=secrets.randbits(16);self.timestamp=secrets.randbits(32);self.ssrc=secrets.randbits(32);self.packets=0;self.octets=0
    def start(self):
        reply,sdp=self.request('DESCRIBE',self.uri,REQUIRE)
        self.base=rtsp.control_uri(self.uri,reply.get('content-base') or reply.get('content-location') or '*',self.ip,self.port)
        uri,self.pt,self.codec=audio_track(sdp,self.base,self.ip,self.port)
        reply,_=self.request('SETUP',uri,{**REQUIRE,'Transport':'RTP/AVP/TCP;unicast;interleaved=0-1'})
        session=reply.get('session','').split(';',1)[0];transport=reply.get('transport','')
        match=re.search(r'(?:^|;)\s*interleaved=(\d{1,3})-(\d{1,3})(?:;|$)',transport,re.I)
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',session) or not match or 'RTP/AVP/TCP' not in transport.upper() or int(match[1])>254 or int(match[2])!=int(match[1])+1:raise ValueError('Invalid backchannel transport')
        self.session=session;self.channel=int(match[1]);self.request('PLAY',self.base,REQUIRE)
    def interleaved(self,channel,packet):
        self.sock.settimeout(2);self.sock.sendall(struct.pack('!BBH',36,channel,len(packet))+packet)
    def send_pcm(self,raw):
        if self.codec not in ('PCMU','PCMA') or len(raw)!=FRAME_BYTES:raise ValueError('Invalid PCM frame')
        encoder=mulaw if self.codec=='PCMU' else alaw
        payload=bytes(encoder(sample) for sample in struct.unpack('<160h',raw))
        packet=struct.pack('!BBHII',0x80,self.pt|(0x80 if not self.packets else 0),self.seq,self.timestamp,self.ssrc)+payload
        self.interleaved(self.channel,packet)
        self.seq=(self.seq+1)&65535;self.timestamp=(self.timestamp+FRAME_SAMPLES)&0xffffffff;self.packets+=1;self.octets+=len(payload)
    def keepalive(self):
        ntp=time.time()+2208988800;seconds=int(ntp);fraction=int((ntp-seconds)*2**32)
        packet=struct.pack('!BBHIIIIII',0x80,200,6,self.ssrc,seconds&0xffffffff,fraction,self.timestamp,self.packets&0xffffffff,self.octets&0xffffffff)
        self.interleaved(self.channel+1,packet)
        self.request('GET_PARAMETER',self.base,REQUIRE)
    def finish(self):
        if self.session and self.base:
            with contextlib.suppress(Exception):self.request('TEARDOWN',self.base,REQUIRE)
        self.__exit__()


def authorize(path,token,slug):
    if not workers.writes_permitted() or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',slug):return None
    try:
        with contextlib.closing(database.open_db(path)) as db:
            user=security.get_user(db,token)
            if not user or user[2]!='admin':return None
            row=db.execute('SELECT c.id,d.main_url FROM cameras c JOIN camera_details d ON d.camera_id=c.id WHERE c.slug=? AND c.enabled=1',(slug,)).fetchone()
            if not row or not security.may_access(db,user,row[0],live=True):return None
            rtsp.endpoint(row[1]);return row
    except (OSError,ValueError):return None


async def frame(reader):
    first,second=await reader.readexactly(2)
    if first&0xf0!=0x80 or not second&0x80:raise ValueError('Invalid WebSocket frame')
    opcode=first&15;n=second&127
    if n==126:n=struct.unpack('!H',await reader.readexactly(2))[0]
    elif n==127:raise ValueError('Oversized WebSocket frame')
    if opcode==2 and n!=FRAME_BYTES or opcode in (8,9,10) and n>125 or opcode not in (2,8,9,10):raise ValueError('Invalid audio frame')
    mask=await reader.readexactly(4);raw=await reader.readexactly(n)
    return opcode,bytes(v^mask[i%4] for i,v in enumerate(raw))


def server_frame(opcode,body):
    if len(body)>125:raise ValueError('Invalid control frame')
    return bytes((0x80|opcode,len(body)))+body


async def handle(reader,writer,*,origin,authorize_fn,active,semaphore,client_factory=Backchannel):
    from . import live_proxy
    camera=None;slug=None;accepted=False;acquired=False;reserved=False
    try:
        peer=writer.get_extra_info('peername')
        if not peer or not ipaddress.ip_address(peer[0]).is_loopback:raise ValueError('Invalid peer')
        if semaphore.locked():raise ValueError('Talkback busy')
        await semaphore.acquire();acquired=True
        slug,token,key=live_proxy._parse_request(await live_proxy._header(reader),origin,path='/api/talk')
        identity=await asyncio.to_thread(authorize_fn,token,slug)
        if not identity or identity[0] in active:raise ValueError('Talkback unavailable')
        active.add(identity[0]);reserved=True
        accept=base64.b64encode(hashlib.sha1((key+live_proxy.GUID).encode('ascii')).digest()).decode('ascii')
        writer.write(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+'\r\n\r\n').encode('ascii'));await writer.drain();accepted=True
        camera=client_factory(identity[1]);await asyncio.to_thread(camera.__enter__);await asyncio.to_thread(camera.start)
        if await asyncio.to_thread(authorize_fn,token,slug)!=identity:raise ValueError('Talkback revoked')
        writer.write(server_frame(1,b'ready'));await writer.drain()
        start=time.monotonic();last_check=start;last_ping=start;next_audio=start
        while time.monotonic()-start<MAX_SECONDS:
            opcode,data=await asyncio.wait_for(frame(reader),timeout=3)
            if opcode==8:break
            if opcode==9:writer.write(server_frame(10,data));await writer.drain();continue
            if opcode==10:continue
            now=time.monotonic()
            if now-last_check>=2:
                if await asyncio.to_thread(authorize_fn,token,slug)!=identity:raise ValueError('Talkback revoked')
                last_check=now
            if now-last_ping>=15:
                await asyncio.to_thread(camera.keepalive);last_ping=time.monotonic()
            # Rate-limit bursty clients; do not send compressed-time audio.
            now=time.monotonic();next_audio=max(now-.1,next_audio)
            if next_audio>now:await asyncio.sleep(next_audio-now)
            await asyncio.to_thread(camera.send_pcm,data);next_audio+=.02
    except (Exception,asyncio.CancelledError):
        if accepted:
            with contextlib.suppress(Exception):writer.write(server_frame(1,b'unavailable'));await writer.drain()
        else:
            with contextlib.suppress(Exception):writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');await writer.drain()
    finally:
        if camera is not None:await asyncio.to_thread(camera.finish)
        if reserved:active.discard(identity[0])
        if accepted:
            with contextlib.suppress(Exception):writer.write(server_frame(8,b'\x03\xe8'));await writer.drain()
        writer.close()
        with contextlib.suppress(Exception):await writer.wait_closed()
        if acquired:semaphore.release()
