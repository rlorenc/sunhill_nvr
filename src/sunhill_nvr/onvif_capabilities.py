"""Read-only ONVIF service and Media2 profile interrogation.

Advertised services are not certification or proof of optional capabilities.
Only endpoints on the configured camera IP are queried.
"""
from __future__ import annotations
import xml.etree.ElementTree as ET
import http.client
from .camera_discovery import DEVICE, SCHEMA, same_camera, soap, local, authenticated_rtsp
from .camera_reboot import target

MEDIA2='http://www.onvif.org/ver20/media/wsdl'
ANALYTICS='http://www.onvif.org/ver20/analytics/wsdl'
EVENTS='http://www.onvif.org/ver10/events/wsdl'
MEDIA1='http://www.onvif.org/ver10/media/wsdl'


def service_map(config):
    endpoint,ip,user,password,mode=target(*config)
    def fill(node):ET.SubElement(node,'{%s}IncludeCapability'%DEVICE).text='true'
    try:root=soap(endpoint,ip,user,password,DEVICE,'GetServices',fill,mode)
    except (OSError,ValueError,ET.ParseError,http.client.HTTPException):raise ValueError('ONVIF services unavailable') from None
    result={}
    for service in root.iter():
        if service.tag.rsplit('}',1)[-1]!='Service':continue
        ns,addr=local(service,'Namespace'),local(service,'XAddr')
        if ns is None or addr is None or not ns.text or not addr.text:continue
        ns=ns.text.strip();addr=addr.text.strip()
        if ns not in (DEVICE,MEDIA1,MEDIA2,ANALYTICS,EVENTS):continue
        try:same_camera(addr,ip,('http','https'))
        except ValueError:continue
        result[ns]=addr
    return result


def media2_profiles(config,endpoint):
    _,ip,user,password,mode=target(*config)
    def fill(node):ET.SubElement(node,'{%s}Type'%MEDIA2).text='All'
    root=soap(endpoint,ip,user,password,MEDIA2,'GetProfiles',fill,mode)
    profiles=[]
    for profile in root.iter():
        if profile.tag.rsplit('}',1)[-1]!='Profiles':continue
        token=profile.get('token','')
        if not token or len(token)>256 or any(ord(c)<32 for c in token):continue
        configs=local(profile,'Configurations')
        if configs is None:continue
        enc=local(configs,'VideoEncoder');source=local(configs,'VideoSource')
        source_token=source.get('token','') if source is not None else ''
        if len(source_token)>256 or any(ord(c)<32 for c in source_token):source_token=''
        metadata=local(configs,'Metadata') is not None
        encoding=local(enc,'Encoding') if enc is not None else None
        width=height=0
        if enc is not None:
            w=local(enc,'Width');h=local(enc,'Height')
            try:width,height=int(w.text),int(h.text)
            except (AttributeError,TypeError,ValueError):continue
            if not (0<width<=16384 and 0<height<=16384):continue
        elif not metadata:continue
        profiles.append({'token':token,'encoding':encoding.text if encoding is not None and encoding.text in ('H264','H265','JPEG') else 'unknown','width':width,'height':height,'metadata':metadata,'video_source_config':source_token,'audio_input':local(configs,'AudioSource') is not None,'audio_output':local(configs,'AudioOutput') is not None,'audio_decoder':local(configs,'AudioDecoder') is not None})
        if len(profiles)>=64:break
    return profiles


def inspect(config):
    services=service_map(config)
    result={'media1':MEDIA1 in services,'media2':MEDIA2 in services,'analytics':ANALYTICS in services,'events':EVENTS in services,'profiles':[],'media2_error':False}
    if MEDIA2 in services:
        try:result['profiles']=media2_profiles(config,services[MEDIA2])
        except (OSError,ValueError,ET.ParseError,http.client.HTTPException):result['media2_error']=True
    return result


def streams(config):
    from .camera_discovery import Streams
    services=service_map(config);endpoint=services.get(MEDIA2)
    if not endpoint:raise ValueError('Media2 unavailable')
    profiles=sorted((p for p in media2_profiles(config,endpoint) if p['width'] and p['height']),key=lambda p:p['width']*p['height'],reverse=True)
    if not profiles:raise ValueError('no Media2 profile')
    _,ip,user,password,mode=target(*config)
    def uri(profile):
        def fill(node):
            ET.SubElement(node,'{%s}Protocol'%MEDIA2).text='RTSP'
            ET.SubElement(node,'{%s}ProfileToken'%MEDIA2).text=profile['token']
        root=soap(endpoint,ip,user,password,MEDIA2,'GetStreamUri',fill,mode);node=local(root,'Uri')
        if node is None or not node.text:raise ValueError('no stream URI')
        return authenticated_rtsp(node.text.strip(),ip,user,password)
    main=uri(profiles[0]);sub=None
    if profiles[-1]['width']*profiles[-1]['height']<profiles[0]['width']*profiles[0]['height']:
        try:sub=uri(profiles[-1])
        except (OSError,ValueError,ET.ParseError,http.client.HTTPException):pass
    return Streams(main,sub if sub!=main else None,'onvif-media2')


def metadata_streams(config):
    """Find a separate metadata profile only if it shares the exact video
    source configuration with the configured primary RTSP stream. Different
    source configurations may crop/rotate another view; do not overlay them.
    """
    from urllib.parse import urlsplit
    def key(url):
        p=urlsplit(url);return p.scheme,p.hostname,p.port or 554,p.path,p.query
    services=service_map(config);endpoint=services.get(MEDIA2)
    if not endpoint:return []
    _,ip,user,password,mode=target(*config)
    profiles=media2_profiles(config,endpoint)
    if not any(p['metadata'] and p['video_source_config'] for p in profiles):return []
    resolved=[]
    for profile in profiles:
        if not profile['video_source_config']:continue
        def fill(node):
            ET.SubElement(node,'{%s}Protocol'%MEDIA2).text='RTSP'
            ET.SubElement(node,'{%s}ProfileToken'%MEDIA2).text=profile['token']
        try:
            root=soap(endpoint,ip,user,password,MEDIA2,'GetStreamUri',fill,mode);node=local(root,'Uri')
            if node is None or not node.text:continue
            uri=authenticated_rtsp(node.text.strip(),ip,user,password)
            resolved.append((profile,uri))
        except (OSError,ValueError,ET.ParseError,http.client.HTTPException):continue
    primary=next((p for p,u in resolved if key(u)==key(config[0]) and p['width'] and p['height']),None)
    if primary is None:return []
    result=[];seen={key(config[0])}
    for profile,uri in resolved:
        if profile['metadata'] and profile['video_source_config']==primary['video_source_config'] and key(uri) not in seen:
            seen.add(key(uri));result.append(uri)
    return result[:2]
