"""Reolink BcMedia AI Mark reader: parse camera bytes, never decode pixels.

Wire-format research: nodelink-js (MIT), detection.ts, BcMediaParser.ts and
startVideoStream. See docs/baichuan-motion-boxes.md for provenance and limits.
Standalone cameras, channel zero, one preview session; discard media payloads.
"""
from __future__ import annotations
import ctypes as C
import functools
import struct
import time
import xml.etree.ElementTree as ET
from . import baichuan_control as bc, camera_control

MAX_FRAME=4*1024*1024
MAX_HEADER=65536
MAX_EXPANDED=256*1024
CLASSES={1:'person',2:'vehicle',3:'animal'}
PRIORITY={'vehicle':1,'person':2,'animal':3}


@functools.lru_cache(maxsize=1)
def lz4_library():
    lib=C.CDLL('liblz4.so.1')
    size=C.c_size_t;ptr=C.c_void_p
    for name,args,restype in (
        ('LZ4F_createDecompressionContext',[C.POINTER(ptr),C.c_uint],size),
        ('LZ4F_freeDecompressionContext',[ptr],size),
        ('LZ4F_decompress',[ptr,ptr,C.POINTER(size),ptr,C.POINTER(size),ptr],size),
        ('LZ4F_isError',[size],C.c_uint)):
        fn=getattr(lib,name);fn.argtypes=args;fn.restype=restype
    return lib


def decompress(data):
    lib=lz4_library();ctx=C.c_void_p()
    if lib.LZ4F_isError(lib.LZ4F_createDecompressionContext(C.byref(ctx),100)):
        raise ValueError('LZ4 context')
    try:
        src=C.create_string_buffer(data);dst=C.create_string_buffer(MAX_EXPANDED)
        n=C.c_size_t(len(data));m=C.c_size_t(MAX_EXPANDED)
        result=lib.LZ4F_decompress(ctx,dst,C.byref(m),src,C.byref(n),None)
        if lib.LZ4F_isError(result) or result or n.value!=len(data):raise ValueError('invalid or oversized LZ4 frame')
        return dst.raw[:m.value]
    finally:lib.LZ4F_freeDecompressionContext(ctx)


def boxes(raw,iframe):
    """Return None for unknown headers; (state, observations) otherwise.

    No native stable ID has been verified for this wire variant. IDs in the
    result are observation-local only; the UI must not draw tracking trails.
    """
    block=raw[8 if iframe else 0:]
    if len(block)<12 or block[0]!=255 or block[2:8]!=b'\x00\x01\x0b\x00\x01\x08':return None
    found=[];dimensions=[];budget=4096
    def walk(data,a=0,b=0,depth=0):
        nonlocal budget
        if depth>12:return
        pos=0
        while pos+3<=len(data) and budget>0:
            budget-=1;t,n=struct.unpack_from('<BH',data,pos);pos+=3
            if not t or pos+n>len(data):return
            value=data[pos:pos+n];pos+=n
            if t==3 and n==4:
                w,h=struct.unpack('<HH',value)
                if 64<=w<=8192 and 64<=h<=8192:dimensions.append((w,h))
            if a in CLASSES and b in (3,5) and ((t==4 and n in (10,13,14)) or (a==3 and t==2 and n==10)):
                x,y,r,bottom,confidence=struct.unpack_from('<5H',value)
                if r>x and bottom>y:found.append((x,y,r,bottom,CLASSES[a],confidence))
            elif a==255 and b==2 and t==2 and value.startswith(b'\x04\x22\x4d\x18'):
                try:walk(decompress(value),0,0,depth+1)
                except (OSError,ValueError):continue
            elif value:walk(value,a or t,b if a==0 else b or t,depth+1)
    walk(block)
    # Some firmware does not include the AI processing size; this format's
    # documented default is 896x480, NOT the encoded video resolution.
    width,height=dimensions[0] if dimensions else (896,480)
    if any(d!=(width,height) for d in dimensions):return ('undecoded',[])
    unique={}
    for x,y,r,bottom,kind,confidence in found:
        if r>width or bottom>height:continue
        coords=(x,y,r,bottom)
        if coords not in unique or PRIORITY[kind]>PRIORITY[unique[coords][0]]:unique[coords]=(kind,confidence)
    result=[]
    for i,((x,y,r,bottom),(kind,conf)) in enumerate(unique.items()):
        if i>=256:break
        item={'id':str(i),'tracked':False,'kind':kind,'box':[x/width,y/height,(r-x)/width,(bottom-y)/height]}
        if 0<conf<=100:item['confidence']=conf/100
        result.append(item)
    return ('boxes' if result else 'empty' if len(block)==128 else 'undecoded',result)


class Media:
    """Incremental BcMedia framing. Skip compressed video/audio, not pixels."""
    def __init__(self):self.buffer=bytearray();self.anchor=None
    def feed(self,data,received):
        if len(self.buffer)+len(data)>2*MAX_FRAME:raise bc.ProtocolError('BcMedia buffer limit')
        self.buffer.extend(data)
        while len(self.buffer)>=4:
            magic=struct.unpack_from('<I',self.buffer)[0]
            if magic in (0x31303031,0x32303031):
                if len(self.buffer)<32:return
                if struct.unpack_from('<I',self.buffer,4)[0]!=32:raise bc.ProtocolError('BcMedia info size')
                del self.buffer[:32];continue
            iframe=0x63643030<=magic<=0x63643039
            pframe=0x63643130<=magic<=0x63643139
            if iframe or pframe:
                if len(self.buffer)<24:return
                if self.buffer[4:8] not in (b'H264',b'H265'):raise bc.ProtocolError('BcMedia codec')
                size,extra,tick=struct.unpack_from('<III',self.buffer,8)
                if size>MAX_FRAME or extra>MAX_HEADER:raise bc.ProtocolError('BcMedia frame size')
                total=24+extra+size+(-size%8)
                if len(self.buffer)<total:return
                header=bytes(self.buffer[24:24+extra]);del self.buffer[:total]
                if iframe and len(header)>=4:
                    stamp=struct.unpack_from('<I',header)[0]*1000
                    if abs(stamp-received)<=30000:self.anchor=(tick,stamp)
                    else:self.anchor=None
                stamp=received;clock='received'
                if self.anchor:
                    delta=(tick-self.anchor[0])&0xffffffff
                    if delta<=30000000 and abs(self.anchor[1]+delta//1000-received)<=30000:stamp=self.anchor[1]+delta//1000;clock='camera'
                decoded=boxes(header,iframe)
                if decoded is not None:
                    state,objects=decoded
                    for obj in objects:obj['clock']=clock
                    yield stamp,state,objects
                continue
            if magic in (0x62773530,0x62773130):
                if len(self.buffer)<8:return
                size,duplicate=struct.unpack_from('<HH',self.buffer,4)
                if size!=duplicate:raise bc.ProtocolError('BcMedia audio size')
                total=8+size+(-size%8)
                if len(self.buffer)<total:return
                del self.buffer[:total];continue
            raise bc.ProtocolError('unknown BcMedia packet')


def preview_request(client,profile):
    stream=1 if profile=='sub' else 0
    client.seq=(client.seq+1)&0xffff
    # Preview uses a zero-based channel and separate stream selector byte.
    mid=(client.seq<<16)|(stream<<8)
    root=ET.Element('body');node=ET.SubElement(root,'Preview',version='1.0')
    for k,v in {'channelId':'0','handle':str(256 if stream else 0),'streamType':profile+'Stream'}.items():ET.SubElement(node,k).text=v
    ext=bc.xml('Extension',{'channelId':0},wrap=False)
    body=bc.aes(ext,client.key)+bc.aes(ET.tostring(root,encoding='utf-8',xml_declaration=True),client.key)
    client.sock.settimeout(client.remaining())
    client.sock.sendall(struct.pack('<IIIIHHI',bc._MAGIC,3,len(body),mid,0,0x6414,len(ext))+body)
    return mid


def read_preview(client,mid):
    magic,cmd,length,ident,status,kind=struct.unpack('<IIIIHH',client.receive(20))
    if magic!=bc._MAGIC or length>MAX_FRAME or kind not in (0x6414,0x6514,0x6614,0):raise bc.ProtocolError('invalid preview frame')
    offset=struct.unpack('<I',client.receive(4))[0] if kind in (0x6414,0) else 0
    if offset>length:raise bc.ProtocolError('invalid preview offset')
    raw=client.receive(length)[offset:]
    if cmd!=3 or ident>>16!=mid>>16 or ident&0xffff!=mid&0xffff:return b''
    if status not in (0,200):raise bc.Rejected('preview rejected')
    # Ack may carry XML. Only skip recognisable XML on an unfragmented start.
    return raw


def run(url,stop,emit,ready,profile='sub'):
    target=camera_control.Reolink(url)
    with bc.Baichuan(target.ip,target.username,target.password) as client:
        client.deadline=time.monotonic()+8
        mid=preview_request(client,profile);parser=Media();confirmed=False;last_valid=time.monotonic();last_emit=0
        while not stop.is_set():
            if time.monotonic()-last_valid>15:raise bc.ProtocolError('no native AI Mark data')
            client.deadline=time.monotonic()+5
            raw=read_preview(client,mid)
            if not raw:continue
            if not parser.buffer:
                # Some cameras encrypt each frame header/body with AES-CFB.
                # Do not guess on continuation packets; unsupported encryption
                # fails closed, leaving ONVIF available.
                if raw.startswith(b'<?xml') or raw.startswith(b'<body'):continue
                magic=struct.unpack_from('<I',raw)[0] if len(raw)>=4 else None
                known=lambda m:m in (0x31303031,0x32303031,0x62773530,0x62773130) or (m is not None and 0x63643030<=m<=0x63643139)
                if not known(magic) and len(raw)>=4:
                    decrypted=bc.aes(raw,client.key,True)
                    if decrypted.startswith((b'<?xml',b'<body')):continue
                    if known(struct.unpack_from('<I',decrypted)[0]):raw=decrypted
            for stamp,state,objects in parser.feed(raw,int(time.time()*1000)):
                if state not in ('boxes','empty'):continue
                last_valid=time.monotonic()
                if not confirmed:ready();confirmed=True
                if last_valid-last_emit>=.2:
                    emit(stamp,objects);last_emit=last_valid
        # Dedicated socket closes on exit; no change of camera configuration.
