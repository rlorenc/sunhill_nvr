"""Standalone Reolink native talk: negotiated ADPCM over Baichuan TCP.
Protocol reference: Neolink talk.rs and dissector/mediapacket.md (wire format).
No audio persisted; query capabilities before starting any physical action.
"""
import contextlib
import struct
import time
import xml.etree.ElementTree as ET
from . import baichuan_control as bc, camera_control

STEPS=(7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,24623,27086,29794,32767)
ADJUST=(-1,-1,-1,-1,2,4,6,8)


def ability(root):
    a=root.find('.//TalkAbility') if root is not None else None
    if a is None:raise ValueError('No TalkAbility')
    duplex=[n.text for n in a.findall('.//duplex')];modes=[n.text for n in a.findall('.//audioStreamMode')]
    d=next((v for v in ('FDX','HDX') if v in duplex),None)
    m=next((v for v in ('followVideoStream','onlyAudioStream','mixAudioStream') if v in modes),None)
    if not d or not m:raise ValueError('No supported talk mode')
    for node in a.iter():
        fields={n.tag:n.text for n in node if len(n)==0}
        if fields.get('audioType')!='adpcm' or fields.get('soundTrack')!='mono' or fields.get('samplePrecision')!='16':continue
        try:rate=int(fields['sampleRate']);length=int(fields['lengthPerEncoder'])
        except (KeyError,ValueError,TypeError):continue
        if rate not in (8000,16000) or not 128<=length<=4096 or length%2:continue
        return d,m,rate,length
    raise ValueError('No supported ADPCM configuration')


def config_xml(config):
    duplex,mode,rate,length=config
    root=ET.Element('body');node=ET.SubElement(root,'TalkConfig',{'version':'1.1'})
    for k,v in {'channelId':0,'duplex':duplex,'audioStreamMode':mode}.items():ET.SubElement(node,k).text=str(v)
    audio=ET.SubElement(node,'audioConfig')
    for k,v in {'audioType':'adpcm','sampleRate':rate,'samplePrecision':16,'lengthPerEncoder':length,'soundTrack':'mono'}.items():ET.SubElement(audio,k).text=str(v)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def encode_block(samples):
    if len(samples)<3 or len(samples)%2!=1:raise ValueError('Invalid ADPCM block')
    predictor=samples[0];index=0;out=bytearray(struct.pack('<hBB',predictor,index,0));low=None
    for sample in samples[1:]:
        step=STEPS[index];delta=sample-predictor;code=8 if delta<0 else 0;delta=abs(delta);change=step>>3
        for bit,amount in ((4,step),(2,step>>1),(1,step>>2)):
            if delta>=amount:code|=bit;delta-=amount;change+=amount
        predictor=max(-32768,min(32767,predictor-change if code&8 else predictor+change));index=max(0,min(88,index+ADJUST[code&7]))
        if low is None:low=code
        else:out.append(low|(code<<4));low=None
    return bytes(out)


def media_packet(block):
    size=len(block)+4
    packet=b'01wb'+struct.pack('<HHHH',size,size,0x0100,len(block)-4)+block
    return packet+bytes((-len(packet))%8)


def native_client(url):
    c=camera_control.Reolink(url)
    return bc.Baichuan(c.ip,c.username,c.password)


def probe(url):
    try:
        with native_client(url) as client:ability(client.request(10,channel=0))
        return True
    except (OSError,ValueError):return False


class NativeBackchannel:
    def __init__(self,url):self.client=native_client(url);self.started=False;self.samples=[];self.config=None
    def __enter__(self):self.client.__enter__();return self
    def start(self):
        self.client.deadline=time.monotonic()+5
        self.config=ability(self.client.request(10,channel=0))
        self.client.request(201,config_xml(self.config),channel=0);self.started=True
    def send_pcm(self,raw):
        if not self.started or len(raw)!=320:raise ValueError('Invalid PCM')
        values=struct.unpack('<160h',raw)
        # Input is 8 kHz; 16 kHz cameras receive interpolated samples.
        if self.config[2]==16000:
            expanded=[]
            for value in values:
                expanded.extend(((getattr(self,'previous',value)+value)//2,value));self.previous=value
            values=expanded
        self.samples.extend(values);length=self.config[3]+1
        while len(self.samples)>=length:
            block=encode_block(self.samples[:length]);del self.samples[:length]
            self.client.deadline=time.monotonic()+3;self.client.request(202,media_packet(block),channel=0,binary=True)
    def keepalive(self):
        self.client.deadline=time.monotonic()+3;self.client.request(93)
    def finish(self):
        try:
            if self.started:
                self.client.deadline=time.monotonic()+3
                with contextlib.suppress(OSError,ValueError):self.client.request(11,channel=0)
        finally:self.started=False;self.samples.clear();self.client.close()
