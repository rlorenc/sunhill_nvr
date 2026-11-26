"""ONVIF camera clock comparison with conservative XML parsing."""
from __future__ import annotations
import datetime as dt
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass

REQUEST=b'''<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope"><Body>
<GetSystemDateAndTime xmlns="http://www.onvif.org/ver10/device/wsdl"/>
</Body></Envelope>'''

@dataclass(frozen=True)
class ClockResult:
    camera_utc: dt.datetime
    offset_seconds: float
    measurement_seconds: float

def parse_response(body: bytes) -> dt.datetime:
    if not isinstance(body,bytes) or len(body)>65536:
        raise ValueError('invalid ONVIF XML')
    # Check declarations after decoding: scanning raw ASCII bytes misses a
    # UTF-16 DOCTYPE, which Expat otherwise accepts and expands.
    try:
        if body.startswith((b'\xff\xfe',b'\xfe\xff')):text=body.decode('utf-16')
        elif body.startswith(b'<\x00'):text=body.decode('utf-16-le')
        elif body.startswith(b'\x00<'):text=body.decode('utf-16-be')
        else:
            if not body.lstrip(b'\xef\xbb\xbf \t\r\n').startswith(b'<'):raise ValueError('invalid ONVIF XML encoding')
            # XML parsers also accept ASCII-compatible single-byte encodings.
            # Latin-1 preserves the ASCII declaration bytes for the safety scan;
            # the parser still interprets the actual declared encoding below.
            text=body.decode('latin-1')
    except UnicodeError as exc:raise ValueError('invalid ONVIF XML encoding') from exc
    if '\0' in text or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError('invalid ONVIF XML')
    try:root=ET.fromstring(body)
    except (ET.ParseError,LookupError,ValueError) as exc:raise ValueError('invalid ONVIF XML') from exc
    def local(tag):return tag.rsplit('}',1)[-1]
    times=[x for x in root.iter() if local(x.tag)=='UTCDateTime']
    if len(times)!=1:raise ValueError('ONVIF UTC clock unavailable')
    values={}
    for node in times[0].iter():
        key=local(node.tag)
        if key in ('Year','Month','Day','Hour','Minute','Second'):
            if key in values or node.text is None:raise ValueError('invalid ONVIF clock')
            try:values[key]=int(node.text)
            except ValueError as exc:raise ValueError('invalid ONVIF clock') from exc
    if set(values)!={'Year','Month','Day','Hour','Minute','Second'}:raise ValueError('incomplete ONVIF clock')
    try:return dt.datetime(values['Year'],values['Month'],values['Day'],values['Hour'],values['Minute'],values['Second'],tzinfo=dt.timezone.utc)
    except (ValueError,OverflowError) as exc:raise ValueError('invalid ONVIF clock') from exc

def compare(body: bytes, started: float, completed: float, *, max_latency: float = 5.0) -> ClockResult:
    if not all(math.isfinite(x) for x in (started,completed,max_latency)) or completed<started or completed-started>max_latency:
        raise ValueError('clock measurement is unreliable')
    camera=parse_response(body);midpoint=(started+completed)/2
    return ClockResult(camera,camera.timestamp()-midpoint,completed-started)


def wsse_request(username: str, password: str, *, nonce: bytes | None=None, created: str | None=None) -> bytes:
    """Generate the legacy ONVIF UsernameToken PasswordDigest SOAP header."""
    import base64,hashlib,secrets
    if not isinstance(username,str) or not username or len(username)>128 or not isinstance(password,str) or len(password)>512:
        raise ValueError('invalid ONVIF credentials')
    if any(ord(c)<32 for c in username+password):raise ValueError('invalid ONVIF credentials')
    try:username.encode('utf-8')
    except UnicodeEncodeError as exc:raise ValueError('invalid ONVIF credentials') from exc
    nonce=secrets.token_bytes(16) if nonce is None else nonce
    created=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if created is None else created
    if not isinstance(nonce,bytes) or not 1<=len(nonce)<=64 or not isinstance(created,str) or len(created)>32:
        raise ValueError('invalid ONVIF token parameters')
    try:digest=base64.b64encode(hashlib.sha1(nonce+created.encode('utf-8')+password.encode('utf-8')).digest()).decode('ascii')
    except UnicodeError as exc:raise ValueError('invalid ONVIF credentials') from exc
    soap='http://www.w3.org/2003/05/soap-envelope'
    security='http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd'
    utility='http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd'
    env=ET.Element('{%s}Envelope'%soap);header=ET.SubElement(env,'{%s}Header'%soap)
    sec=ET.SubElement(header,'{%s}Security'%security);token=ET.SubElement(sec,'{%s}UsernameToken'%security)
    ET.SubElement(token,'{%s}Username'%security).text=username
    ET.SubElement(token,'{%s}Password'%security,{'Type':'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest'}).text=digest
    ET.SubElement(token,'{%s}Nonce'%security,{'EncodingType':'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary'}).text=base64.b64encode(nonce).decode('ascii')
    ET.SubElement(token,'{%s}Created'%utility).text=created
    body=ET.SubElement(env,'{%s}Body'%soap);ET.SubElement(body,'{http://www.onvif.org/ver10/device/wsdl}GetSystemDateAndTime')
    return ET.tostring(env,encoding='utf-8',xml_declaration=True)

def validate_endpoint(endpoint: str) -> None:
    from urllib.parse import urlsplit
    if not isinstance(endpoint,str) or not 1<=len(endpoint)<=2048 or any(ord(c)<=32 or ord(c)==127 for c in endpoint):
        raise ValueError('invalid ONVIF endpoint')
    try:parts=urlsplit(endpoint);port=parts.port
    except ValueError as exc:raise ValueError('invalid ONVIF endpoint') from exc
    if (parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password
            or parts.path!='/onvif/device_service' or parts.query or parts.fragment or '\\' in parts.netloc
            or (port is not None and not 1<=port<=65535)):
        raise ValueError('invalid ONVIF endpoint')

def query(endpoint: str, username: str = '', password: str = '', *, timeout: int = 5, opener_factory=None, auth_mode: str = 'digest') -> ClockResult:
    """Query the standard ONVIF device service using optional HTTP Digest.

    Redirects and environment proxies are disabled. HTTPS certificates are always
    verified. Legacy WS-Security is used only when explicitly selected.
    """
    import http.client,time,urllib.request
    from .home_assistant import _NoRedirect
    validate_endpoint(endpoint)
    if auth_mode not in ('digest','wsse'):raise ValueError('invalid ONVIF authentication mode')
    body=wsse_request(username,password) if auth_mode=='wsse' else REQUEST
    manager=urllib.request.HTTPPasswordMgrWithDefaultRealm()
    if username:manager.add_password(None,endpoint,username,password)
    factory=opener_factory or urllib.request.build_opener
    handlers=[urllib.request.ProxyHandler({}),_NoRedirect()]
    if auth_mode=='digest':handlers.append(urllib.request.HTTPDigestAuthHandler(manager))
    opener=factory(*handlers)
    request=urllib.request.Request(endpoint,data=body,method='POST',headers={'Content-Type':'application/soap+xml; charset=utf-8'})
    started=time.time()
    try:
        with opener.open(request,timeout=timeout) as response:
            body=response.read(65537)
    except (OSError,http.client.HTTPException) as exc:raise ValueError('ONVIF query failed') from exc
    return compare(body,started,time.time(),max_latency=timeout+1)
