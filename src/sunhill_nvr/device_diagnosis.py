"""Persistent, read-only camera diagnosis. No passwords or raw CGI payloads returned.
Unknown capability is not advertised. Control values are refreshed before writes.
"""
import hashlib
import json
import time
import http.client
import xml.etree.ElementTree as ET
from .camera_reboot import target
from .camera_discovery import DEVICE, soap
from . import camera_control, camera_settings, talkback, onvif_metadata

TTL=86400
RETRY=120
TARGET_SQL='SELECT main_url,onvif_url,onvif_username,onvif_password,onvif_auth FROM camera_details WHERE camera_id=?'


def fingerprint(row):
    return hashlib.sha256(json.dumps(list(row),separators=(',',':')).encode()).hexdigest()


def cached(db,cid,row,now=None):
    now=int(time.time()) if now is None else now
    saved=db.execute('SELECT fingerprint,checked,result_json FROM camera_diagnosis WHERE camera_id=?',(cid,)).fetchone()
    if not saved or saved[0]!=fingerprint(row):return None
    data=json.loads(saved[2]);age=now-saved[1]
    if not 0<=age<(RETRY if data.get('partial') else TTL):return None
    return data


def persist(db,cid,row,data,now=None):
    now=int(time.time()) if now is None else now
    with db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute(TARGET_SQL,(cid,)).fetchone()!=row:return False
        db.execute('INSERT INTO camera_diagnosis VALUES(?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET fingerprint=excluded.fingerprint,checked=excluded.checked,result_json=excluded.result_json',(cid,fingerprint(row),now,json.dumps(data,separators=(',',':'))))
    return True


def probe_talkback(url):
    # DESCRIBE only: no SETUP/PLAY or audio transmitted during diagnosis.
    try:
        with onvif_metadata.Client(url) as client:
            headers,sdp=client.request('DESCRIBE',client.uri,talkback.REQUIRE)
            base=onvif_metadata.control_uri(client.uri,headers.get('content-base') or headers.get('content-location') or '*',client.ip,client.port)
            talkback.audio_track(sdp,base,client.ip,client.port)
            return True
    except (OSError,ValueError,UnicodeError):return False


def reported_abilities(client):
    """Read channel 0 capability declarations, not the default values of setters."""
    try:
        value=client.get('GetAbility',{'User':{'userName':client.username}},action=0)
        ability=value['value']['Ability'];channels=ability.get('abilityChn',[])
        if not isinstance(ability,dict):return {}
        channel=channels[0] if isinstance(channels,list) and len(channels)==1 and isinstance(channels[0],dict) else {}
        return {key:entry['ver'] for scope in (ability,channel) for key,entry in scope.items() if isinstance(entry,dict) and type(entry.get('ver')) is int and entry['ver']>=0}
    except (AttributeError,KeyError,TypeError,IndexError,camera_control.ControlError):return {}


def filter_abilities(result,abilities):
    c=result['controls'];device=result.get('device') or {}
    # Explicit CGI ability is required for the white LED. Native floodlight
    # status is a separate positive observation, not a default CGI value.
    if device.get('protocol')=='CGI HTTP':
        if not abilities.get('supportFLswitch',0):
            for key in ('light','light_available','brightness'):c.pop(key,None)
        elif not abilities.get('supportFLBrightness',0):c.pop('brightness',None)
    if 'ptzType' in abilities and abilities['ptzType']==0:
        for key in ('zoom','focus','autofocus','presets'):c.pop(key,None)
    checks={'siren':('supportAudioAlarm','alarmAudio'),'speaker_volume':('supportAoAdjust',),'hdr':('supportIspHdr',),'sd_record':('sdCard',),'status_led':('powerLed',),'floodlight_brightness':('supportFLBrightness',)}
    def valid(item):
        flags=checks.get(item['key'],())
        values=[abilities[k] for k in flags if k in abilities]
        if item['key'] in ('speaker_volume','siren','floodlight_brightness') and device.get('protocol')=='CGI HTTP':return any(values)
        return not values or any(values)
    result['settings']=[item for item in result['settings'] if valid(item)]
    return result


def diagnose(row):
    try:
        with camera_control.Reolink(row[0]) as client:
            abilities=reported_abilities(client)
            result=camera_control.capabilities(client)
            settings,partial=camera_settings.read(client)
            result.update(settings=settings,partial=result.get('partial',False) or partial)
            filter_abilities(result,abilities)
    except camera_control.ControlError as exc:
        result={'controls':{},'settings':[],'partial':True,'message':str(exc)}
    result=camera_control.supplement_identity(result,*row)
    # Known absence, not a positive hardware assumption based on model name.
    model=(result.get('device') or {}).get('model','').upper()
    if model=='RLC-510A':
        for key in ('light','light_available','brightness','zoom','focus','autofocus','presets'):result['controls'].pop(key,None)
        result['settings']=[x for x in result['settings'] if x['key'] not in ('speaker_volume','siren','floodlight_brightness')]
        result['talkback']=False
    else:result['talkback']=probe_talkback(row[0])
    result.update(reboot=False,firmware_upgrade=False)
    if row[1]:
        try:
            endpoint,ip,user,password,mode=target(*row)
            root=soap(endpoint,ip,user,password,DEVICE,'GetServiceCapabilities',mode=mode)
            for node in root.iter():
                if node.tag.rsplit('}',1)[-1]=='GetServiceCapabilitiesResponse':result['reboot']=True
                if node.tag.rsplit('}',1)[-1]=='System' and node.get('HttpFirmwareUpgrade','').lower() in ('true','1'):result['firmware_upgrade']=True
        except (OSError,ValueError,ET.ParseError,http.client.HTTPException):result['partial']=True
    result['checked_at']=int(time.time())
    return result
