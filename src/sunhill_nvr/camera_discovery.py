"""Administrator-initiated, bounded IPv4 camera discovery without new dependencies."""
from __future__ import annotations
import concurrent.futures
import ipaddress
import json
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit, quote
from .cameras import validate_rtsp
from .home_assistant import _NoRedirect
from .onvif_time import wsse_request

SOAP='http://www.w3.org/2003/05/soap-envelope'
DEVICE='http://www.onvif.org/ver10/device/wsdl'
MEDIA='http://www.onvif.org/ver10/media/wsdl'
SCHEMA='http://www.onvif.org/ver10/schema'
NETWORKS=tuple(map(ipaddress.ip_network,('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')))

class DiscoveryError(ValueError):
    pass

@dataclass(frozen=True)
class Streams:
    main: str=field(repr=False)
    sub: str | None=field(default=None,repr=False)
    method: str='onvif'


def validate_input(ip,username='',password=''):
    if not isinstance(ip,str) or not isinstance(username,str) or not isinstance(password,str):
        raise DiscoveryError('Nieprawidłowy adres IP lub dane logowania kamery.')
    try:address=ipaddress.IPv4Address(ip)
    except ValueError:raise DiscoveryError('Podaj prywatny adres IPv4 kamery.') from None
    if not any(address in n for n in NETWORKS) or address.packed[-1] in (0,255):
        raise DiscoveryError('Podaj prywatny adres IPv4 kamery.')
    if len(username)>128 or len(password)>512 or (password and not username) or any(ord(c)<32 or ord(c)==127 for c in username+password):
        raise DiscoveryError('Nieprawidłowe dane logowania kamery.')
    return str(address)


def same_camera(url,ip,schemes):
    if not isinstance(url,str) or len(url)>2048 or any(ord(c)<=32 or ord(c)==127 for c in url):raise ValueError()
    p=urlsplit(url)
    if p.scheme not in schemes or p.hostname!=ip or p.fragment or '\\' in p.netloc or (p.port is not None and not 1<=p.port<=65535):raise ValueError()
    if p.username is not None or p.password is not None:raise ValueError()
    return p


def authenticated_rtsp(url,ip,username,password):
    p=same_camera(url,ip,('rtsp','rtsps'))
    auth=quote(username,safe='')+':'+quote(password,safe='')+'@' if username else ''
    result=urlunsplit((p.scheme,auth+p.netloc,p.path,p.query,''));validate_rtsp(result)
    return result


def parse_xml(raw):
    # Only UTF-8 SOAP accepted; reject DTD/entity declarations and alternate encodings.
    if len(raw)>262144:raise ValueError()
    s=raw.decode('utf-8-sig')
    if '\x00' in s or '<!DOCTYPE' in s.upper() or '<!ENTITY' in s.upper():raise ValueError()
    root=ET.fromstring(s)
    if root.find('.//{%s}Fault'%SOAP) is not None:raise ValueError()
    return root


def soap(endpoint,ip,username,password,namespace,operation,fill=None,mode='digest'):
    same_camera(endpoint,ip,('http','https'))
    if mode=='wsse':
        env=ET.fromstring(wsse_request(username,password));body=env.find('{%s}Body'%SOAP);body.clear()
    else:
        env=ET.Element('{%s}Envelope'%SOAP);body=ET.SubElement(env,'{%s}Body'%SOAP)
    action=ET.SubElement(body,'{%s}%s'%(namespace,operation))
    if fill:fill(action)
    manager=urllib.request.HTTPPasswordMgrWithDefaultRealm()
    if username:manager.add_password(None,endpoint,username,password)
    handlers=[urllib.request.ProxyHandler({}),_NoRedirect()]
    if username:handlers.append(urllib.request.HTTPDigestAuthHandler(manager))
    opener=urllib.request.build_opener(*handlers)
    req=urllib.request.Request(endpoint,data=ET.tostring(env,encoding='utf-8',xml_declaration=True),headers={'Content-Type':f'application/soap+xml; charset=utf-8; action="{namespace}/{operation}"'},method='POST')
    with opener.open(req,timeout=2) as response:return parse_xml(response.read(262145))


def local(node,name):
    return next((x for x in node.iter() if x.tag.rsplit('}',1)[-1]==name),None)


def profiles(ip,username,password,mode,port=80):
    device=f'http://{ip}:{port}/onvif/device_service'
    def capabilities(node):ET.SubElement(node,'{%s}Category'%DEVICE).text='Media'
    root=soap(device,ip,username,password,DEVICE,'GetCapabilities',capabilities,mode)
    media=local(root,'Media');xaddr=local(media,'XAddr') if media is not None else None
    if xaddr is None or not xaddr.text:raise ValueError()
    endpoint=xaddr.text.strip();same_camera(endpoint,ip,('http','https'))
    root=soap(endpoint,ip,username,password,MEDIA,'GetProfiles',mode=mode)
    ranked=[]
    for profile in root.iter():
        if profile.tag.rsplit('}',1)[-1]!='Profiles':continue
        token=profile.get('token','');encoder=local(profile,'VideoEncoderConfiguration')
        if not token or len(token)>256 or encoder is None:continue
        try:
            w=int(local(encoder,'Width').text);h=int(local(encoder,'Height').text)
        except (AttributeError,TypeError,ValueError):continue
        if 0<w<=16384 and 0<h<=16384:ranked.append((w*h,token))
    ranked.sort(reverse=True)
    if not ranked:raise ValueError()
    def uri(token):
        def fill(node):
            setup=ET.SubElement(node,'{%s}StreamSetup'%MEDIA)
            ET.SubElement(setup,'{%s}Stream'%SCHEMA).text='RTP-Unicast'
            transport=ET.SubElement(setup,'{%s}Transport'%SCHEMA)
            ET.SubElement(transport,'{%s}Protocol'%SCHEMA).text='RTSP'
            ET.SubElement(node,'{%s}ProfileToken'%MEDIA).text=token
        result=soap(endpoint,ip,username,password,MEDIA,'GetStreamUri',fill,mode)
        found=local(result,'Uri')
        if found is None or not found.text:raise ValueError()
        return authenticated_rtsp(found.text.strip(),ip,username,password)
    main=uri(ranked[0][1]);sub=None
    if ranked[-1][0]<ranked[0][0]:
        try:sub=uri(ranked[-1][1])
        except (OSError,ValueError,ET.ParseError):pass
    return Streams(main,sub if sub!=main else None)


def probe(url):
    try:
        p=subprocess.run(['/usr/bin/ffprobe','-v','error','-rtsp_transport','tcp','-timeout','2500000','-analyzeduration','1000000','-probesize','131072','-select_streams','v:0','-show_entries','stream=codec_name,width,height','-of','json',url],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=4,check=True)
        streams=json.loads(p.stdout).get('streams',[])
        return bool(streams and streams[0].get('codec_name') and streams[0].get('width',0)>0 and streams[0].get('height',0)>0)
    except (OSError,ValueError,subprocess.SubprocessError,TypeError):return False

# Each pair is only a candidate, never saved unless a real video stream is found.
PATHS=(('/Preview_01_main','/Preview_01_sub'),('/h264Preview_01_main','/h264Preview_01_sub'),('/h265Preview_01_main','/h264Preview_01_sub'),('/Streaming/Channels/101','/Streaming/Channels/102'),('/cam/realmonitor?channel=1&subtype=0','/cam/realmonitor?channel=1&subtype=1'))


def discover(ip,username='',password=''):
    ip=validate_input(ip,username,password)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for port in (80,8000):
            for mode in ('digest','wsse') if username else ('digest',):
                try:result=profiles(ip,username,password,mode,port)
                except (OSError,ValueError,ET.ParseError):
                    try:
                        from .onvif_capabilities import streams
                        result=streams((f'rtsp://{ip}/',f'http://{ip}:{port}/onvif/device_service',username,password,mode))
                    except (OSError,ValueError,ET.ParseError):continue
                main=pool.submit(probe,result.main);sub=pool.submit(probe,result.sub) if result.sub else None
                if main.result():return Streams(result.main,result.sub if sub and sub.result() else None,result.method)
        pairs=[tuple(authenticated_rtsp(f'rtsp://{ip}:554{path}',ip,username,password) for path in pair) for pair in PATHS]
        # Deduplicate paths shared by vendors, prioritize main streams first.
        main_probes={u:pool.submit(probe,u) for u in dict.fromkeys(x[0] for x in pairs)}
        for main,sub in pairs:
            if main_probes[main].result():
                return Streams(main,sub if probe(sub) else None,'rtsp')
    raise DiscoveryError('Nie wykryto strumienia wideo. Sprawdź IP, dane logowania i czy ONVIF lub RTSP są włączone. Możesz użyć konfiguracji ręcznej.')


def discover_bounded(ip,username='',password=''):
    ip=validate_input(ip,username,password)
    # Isolate discovery so a slow camera cannot occupy a web worker indefinitely.
    try:
        p=subprocess.run([sys.executable,'-m','sunhill_nvr.camera_discovery'],input=json.dumps([ip,username,password]).encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=27,check=False)
        if p.returncode!=0:raise ValueError()
        data=json.loads(p.stdout)
        if 'error' in data:raise DiscoveryError(data['error'])
        result=Streams(data['main'],data.get('sub'),data['method']);validate_rtsp(result.main)
        if result.sub:validate_rtsp(result.sub)
        return result
    except DiscoveryError:raise
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):raise DiscoveryError('Nie udało się wykryć strumieni w wyznaczonym czasie. Sprawdź kamerę lub użyj konfiguracji ręcznej.') from None


if __name__=='__main__':
    try:
        result=discover(*json.load(sys.stdin));print(json.dumps({'main':result.main,'sub':result.sub,'method':result.method}))
    except DiscoveryError as exc:print(json.dumps({'error':str(exc)}))
