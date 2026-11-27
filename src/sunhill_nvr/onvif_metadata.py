"""Read camera-native ONVIF RTP metadata on a dedicated RTSP/TCP session.

No video decoding or camera configuration changes. Supports uncompressed
vnd.onvif.metadata, private IPv4, RTSP 1.0, Digest MD5/SHA-256 and Basic on LAN.
Only the configured IP and port are used, including SDP control URIs.
"""
from __future__ import annotations
import base64
import hashlib
import math
import re
import secrets
import socket
import struct
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from .camera_discovery import validate_input, same_camera

SCHEMA='http://www.onvif.org/ver10/schema'
MAX_XML=512*1024
MAX_HEADERS=32*1024
CLASSES={'human':'person','person':'person','humanbody':'person','vehicle':'vehicle','car':'vehicle','truck':'vehicle','animal':'animal','face':'face','humanface':'face','licenseplate':'plate','package':'package','bag':'package'}

class MetadataError(ValueError):pass


def tag(name):return '{%s}%s'%(SCHEMA,name)


def transform(node, previous=(1.,1.,0.,0.)):
    sx,sy,tx,ty=previous
    t=node.find(tag('Transformation'))
    if t is None:return previous
    scale=t.find(tag('Scale'));translate=t.find(tag('Translate'))
    a,b=(float(scale.attrib['x']),float(scale.attrib['y'])) if scale is not None else (1.,1.)
    c,d=(float(translate.attrib['x']),float(translate.attrib['y'])) if translate is not None else (0.,0.)
    result=sx*a,sy*b,tx+sx*c,ty+sy*d
    if not all(map(math.isfinite,result)) or not result[0] or not result[1]:raise ValueError('invalid transform')
    return result


def parse_document(raw):
    """Yield frames with real positions in top-left-origin normalized [0,1]."""
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_XML:raise MetadataError('metadata size')
    try:
        text=raw.decode('utf-8-sig')
        if '\x00' in text or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError()
        root=ET.fromstring(text)
        if root.tag!=tag('MetadataStream'):raise ValueError()
    except (ValueError,ET.ParseError,UnicodeError):raise MetadataError('invalid metadata XML') from None
    for frame in root.findall('./'+tag('VideoAnalytics')+'/'+tag('Frame')):
        try:
            dt=datetime.fromisoformat(frame.attrib['UtcTime'].replace('Z','+00:00'))
            if dt.utcoffset() is None:continue
            ts=round(dt.timestamp()*1000);coords=transform(frame)
        except (KeyError,ValueError,OverflowError,OSError):continue
        objects=[];seen=set()
        for obj in frame.findall(tag('Object')):
            oid=obj.get('ObjectId','')
            if not re.fullmatch(r'-?[0-9]{1,18}',oid) or oid in seen:continue
            app=obj.find(tag('Appearance'))
            if app is None:continue
            shape=app.find(tag('Shape'))
            if shape is None:continue
            box=shape.find(tag('BoundingBox'))
            if box is None:continue
            try:
                sx,sy,tx,ty=transform(shape,transform(app,coords))
                left,right=(float(box.attrib[k])*sx+tx for k in ('left','right'))
                top,bottom=(float(box.attrib[k])*sy+ty for k in ('top','bottom'))
                if not all(map(math.isfinite,(left,right,top,bottom))):continue
                x0,x1=sorted(((left+1)/2,(right+1)/2));y0,y1=sorted(((1-top)/2,(1-bottom)/2))
                if x0<-.02 or y0<-.02 or x1>1.02 or y1>1.02:continue
                x0,y0,x1,y1=max(0,x0),max(0,y0),min(1,x1),min(1,y1)
                if x1<=x0 or y1<=y0:continue
            except (KeyError,ValueError,OverflowError):continue
            kind='object';confidence=None
            for typ in app.findall('.//'+tag('Class')+'//'+tag('Type')):
                name=CLASSES.get((typ.text or '').strip().lower())
                try:likelihood=float(typ.get('Likelihood','1'))
                except ValueError:continue
                if name and 0<=likelihood<=1 and (confidence is None or likelihood>confidence):kind,confidence=name,likelihood
            observation={'id':oid,'kind':kind,'box':[round(x0,6),round(y0,6),round(x1-x0,6),round(y1-y0,6)]}
            # Native ONVIF ANPR descriptor; never infer a number from a box.
            best=None
            for plate in app.findall('.//'+tag('LicensePlateInfo')+'/'+tag('PlateNumber')):
                number=(plate.text or '').strip()
                try:prob=float(plate.get('Likelihood','1'))
                except ValueError:continue
                if not re.fullmatch(r'[A-Za-z0-9 -]{2,24}',number) or not 0<=prob<=1:continue
                if best is None or prob>best[1]:best=(number.upper(),prob)
            if best:observation.update(plate_number=best[0],plate_confidence=best[1])
            objects.append(observation);seen.add(oid)
        yield ts,objects


def endpoint(value):
    try:
        p=urllib.parse.urlsplit(value);user=urllib.parse.unquote(p.username or '');password=urllib.parse.unquote(p.password or '')
        if p.scheme!='rtsp' or p.fragment:raise ValueError()
        ip=validate_input(p.hostname,user,password);port=554 if p.port is None else p.port
        if not 1<=port<=65535:raise ValueError()
        uri=urllib.parse.urlunsplit(('rtsp',f'{ip}:{port}',p.path or '/',p.query,''));same_camera(uri,ip,('rtsp',))
        if not uri.isascii():raise ValueError()
        return ip,port,uri,user,password
    except (ValueError,TypeError,UnicodeError):raise MetadataError('unsupported RTSP endpoint') from None


def control_uri(base,value,ip,port):
    if not value or value=='*':return base
    if '://' in value:result=value
    else:
        p=urllib.parse.urlsplit(base)
        if value.startswith('/'):path=value
        else:path=p.path.rstrip('/')+'/'+value
        result=urllib.parse.urlunsplit(('rtsp',p.netloc,path,'',''))
    try:
        p=same_camera(result,ip,('rtsp',))
        if (p.port or 554)!=port or not result.isascii():raise ValueError()
    except ValueError:raise MetadataError('foreign SDP control endpoint') from None
    return result


def metadata_track(sdp,base,ip,port):
    """Return control URI and dynamic payload type from the actual SDP."""
    try:lines=sdp.decode('utf-8').splitlines()
    except UnicodeError:raise MetadataError('invalid SDP') from None
    sections=[];current=None
    for line in lines:
        if line.startswith('m='):
            current={'media':line[2:].split(),'attrs':[]};sections.append(current)
        elif current is not None and line.startswith('a='):current['attrs'].append(line[2:].strip())
    for part in sections:
        media=part['media'];attrs=part['attrs']
        if len(media)<4 or media[0]!='application':continue
        payloads={int(v) for v in media[3:] if v.isascii() and v.isdecimal() and len(v)<=3}
        for attr in attrs:
            m=re.fullmatch(r'rtpmap:(\d{2,3})\s+vnd\.onvif\.metadata/90000(?:/1)?',attr,re.I)
            if not m:continue
            pt=int(m[1])
            if not 96<=pt<=127 or pt not in payloads:continue
            ctl=next((v[8:] for v in attrs if v.startswith('control:')),None)
            if ctl and ctl!='*':return control_uri(base,ctl,ip,port),pt
    raise MetadataError('no uncompressed ONVIF metadata track')


class RTPXML:
    def __init__(self,pt):self.pt=pt;self.data=bytearray();self.sequence=None;self.ssrc=None;self.damaged=False
    def feed(self,packet):
        if len(packet)<12 or packet[0]>>6!=2 or packet[1]&127!=self.pt:return None
        seq=struct.unpack_from('!H',packet,2)[0];ssrc=struct.unpack_from('!I',packet,8)[0]
        if self.sequence is not None and ((self.sequence+1)&65535)!=seq or self.ssrc is not None and self.ssrc!=ssrc:self.damaged=True
        self.sequence,self.ssrc=seq,ssrc
        start=12+4*(packet[0]&15);end=len(packet)
        if start>end:self.damaged=True
        elif packet[0]&16:
            if start+4>end:self.damaged=True
            else:start+=4+4*struct.unpack_from('!H',packet,start+2)[0]
        if packet[0]&32:
            pad=packet[-1]
            if pad==0:self.damaged=True
            else:end-=pad
        if end<start:self.damaged=True
        if not self.damaged:
            self.data.extend(packet[start:end])
            if len(self.data)>MAX_XML:self.damaged=True;self.data.clear()
        if packet[1]&128:
            result=bytes(self.data) if not self.damaged else None
            self.data.clear();self.damaged=False
            return result
        return None


class Client:
    def __init__(self,url):
        self.ip,self.port,self.uri,self.user,self.password=endpoint(url);self.sock=None;self.buf=bytearray();self.cseq=0;self.session=None;self.auth=None;self.nc=0;self.channel=0;self.decoder=None
    def __enter__(self):
        self.sock=socket.create_connection((self.ip,self.port),timeout=3);return self
    def __exit__(self,*exc):
        if self.sock:self.sock.close();self.sock=None
    def _fill(self,n,deadline):
        while len(self.buf)<n:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError()
            self.sock.settimeout(min(remaining,2));chunk=self.sock.recv(min(65536,max(4096,n-len(self.buf))))
            if not chunk:raise OSError('RTSP connection closed')
            self.buf.extend(chunk)
    def receive(self,deadline):
        self._fill(1,deadline)
        if self.buf[0]==36:
            self._fill(4,deadline);channel=self.buf[1];n=struct.unpack_from('!H',self.buf,2)[0];self._fill(4+n,deadline)
            packet=bytes(self.buf[4:4+n]);del self.buf[:4+n];return ('rtp',channel,packet)
        while (i:=self.buf.find(b'\r\n\r\n'))<0:
            if len(self.buf)>MAX_HEADERS:raise MetadataError('RTSP header limit')
            self._fill(len(self.buf)+1,deadline)
        if i>MAX_HEADERS:raise MetadataError('RTSP header limit')
        lines=bytes(self.buf[:i]).decode('latin-1').split('\r\n');match=re.fullmatch(r'RTSP/1\.0 (\d{3})(?: .*)?',lines[0])
        if not match:raise MetadataError('invalid RTSP response')
        headers={}
        for line in lines[1:]:
            k,sep,v=line.partition(':');k=k.strip().lower()
            if not sep or k in headers:raise MetadataError('invalid RTSP header')
            headers[k]=v.strip()
        length=headers.get('content-length','0')
        if not re.fullmatch(r'[0-9]{1,6}',length) or int(length)>MAX_XML:raise MetadataError('RTSP response size')
        n=int(length);self._fill(i+4+n,deadline);body=bytes(self.buf[i+4:i+4+n]);del self.buf[:i+4+n]
        return ('response',int(match[1]),headers,body)
    def authorization(self,method,uri):
        if self.auth is None:return None
        scheme,params=self.auth
        if scheme=='basic':return 'Basic '+base64.b64encode((self.user+':'+self.password).encode()).decode()
        algorithm=params.get('algorithm','MD5').upper();h=hashlib.md5 if algorithm=='MD5' else hashlib.sha256 if algorithm=='SHA-256' else None
        if h is None:raise MetadataError('unsupported Digest algorithm')
        realm,nonce=params.get('realm'),params.get('nonce');qop=params.get('qop','');opts=[v.strip().lower() for v in qop.split(',')]
        if realm is None or not nonce or qop and 'auth' not in opts:raise MetadataError('unsupported Digest challenge')
        def H(s):return h(s.encode('utf-8')).hexdigest()
        self.nc+=1;nc=f'{self.nc:08x}';cnonce=secrets.token_hex(12);ha1=H(self.user+':'+realm+':'+self.password);ha2=H(method+':'+uri)
        response=H(f'{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}') if qop else H(f'{ha1}:{nonce}:{ha2}')
        def quoted(v):
            if any(ord(c)<32 or ord(c)>126 for c in v):raise MetadataError('unsupported Digest parameter')
            return '"'+v.replace('\\','\\\\').replace('"','\\"')+'"'
        fields={k:quoted(v) for k,v in {'username':self.user,'realm':realm,'nonce':nonce,'uri':uri,'response':response}.items()};fields['algorithm']=algorithm
        if 'opaque' in params:fields['opaque']=quoted(params['opaque'])
        if qop:fields.update(qop='auth',nc=nc,cnonce=quoted(cnonce))
        return 'Digest '+', '.join(k+'='+v for k,v in fields.items())
    def request(self,method,uri,headers=None,emit=None):
        for attempt in range(2):
            self.cseq+=1;fields={'CSeq':str(self.cseq),'User-Agent':'Sunhill-NVR','Accept':'application/sdp' if method=='DESCRIBE' else '*/*'}
            if self.session:fields['Session']=self.session
            fields.update(headers or {});auth=self.authorization(method,uri)
            if auth:fields['Authorization']=auth
            request=method+' '+uri+' RTSP/1.0\r\n'+''.join(k+': '+v+'\r\n' for k,v in fields.items())+'\r\n'
            self.sock.settimeout(3);self.sock.sendall(request.encode('ascii'));deadline=time.monotonic()+5
            while True:
                try:message=self.receive(deadline)
                except socket.timeout:
                    if time.monotonic()>=deadline:raise
                    continue
                if message[0]=='rtp':
                    if emit:self.on_packet(message,emit)
                    continue
                _,status,reply,body=message
                if reply.get('cseq')!=str(self.cseq):raise MetadataError('RTSP CSeq mismatch')
                break
            if status==401 and attempt==0 and self.user:
                challenge=reply.get('www-authenticate','');scheme,_,raw=challenge.partition(' ');scheme=scheme.lower()
                if scheme not in ('digest','basic'):raise MetadataError('unsupported RTSP authentication')
                try:params=urllib.request.parse_keqv_list(urllib.request.parse_http_list(raw))
                except (ValueError,IndexError):raise MetadataError('invalid authentication challenge') from None
                self.auth=(scheme,{k.lower():v for k,v in params.items()});self.nc=0;continue
            if status!=200:raise MetadataError('RTSP request rejected')
            return reply,body
        raise MetadataError('RTSP authentication failed')
    def on_packet(self,message,emit):
        if message[1]!=self.channel or not self.decoder:return
        xml=self.decoder.feed(message[2])
        if xml:
            try:
                for stamp,objects in parse_document(xml):emit(stamp,objects)
            except MetadataError:pass
    def run(self,stop,emit,ready):
        reply,sdp=self.request('DESCRIBE',self.uri)
        base=control_uri(self.uri,reply.get('content-base') or reply.get('content-location') or '*',self.ip,self.port)
        track,pt=metadata_track(sdp,base,self.ip,self.port);self.decoder=RTPXML(pt)
        reply,_=self.request('SETUP',track,{'Transport':'RTP/AVP/TCP;unicast;interleaved=0-1'})
        session=reply.get('session','').split(';',1)[0];transport=reply.get('transport','');m=re.search(r'(?:^|;)\s*interleaved=(\d{1,3})-(\d{1,3})(?:;|$)',transport,re.I)
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',session) or not m or 'RTP/AVP/TCP' not in transport.upper() or int(m[1])>254 or int(m[2])!=int(m[1])+1:raise MetadataError('invalid RTSP transport')
        self.session=session;self.channel=int(m[1]);self.request('PLAY',base,emit=emit);ready()
        next_ping=time.monotonic()+15
        while not stop.is_set():
            if time.monotonic()>=next_ping:
                self.request('GET_PARAMETER',base,emit=emit);next_ping=time.monotonic()+15
            try:message=self.receive(time.monotonic()+1)
            except socket.timeout:continue
            if message[0]=='rtp':self.on_packet(message,emit)
