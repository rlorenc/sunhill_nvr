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
from . import camera_control, camera_settings, talkback, onvif_metadata, reolink_talk

RETRY=120
TARGET_SQL='SELECT main_url,onvif_url,onvif_username,onvif_password,onvif_auth,sub_url FROM camera_details WHERE camera_id=?'


def fingerprint(row):
    return hashlib.sha256(json.dumps([4,*row],separators=(',',':')).encode()).hexdigest()


def snapshot(db,cid):
    return db.execute('SELECT fingerprint,checked,result_json FROM camera_diagnosis WHERE camera_id=?',(cid,)).fetchone()


def cached(db,cid,row,now=None):
    saved=snapshot(db,cid)
    if not saved or saved[0]!=fingerprint(row):return None
    return json.loads(saved[2])


def needs_probe(db,cid,row,now):
    saved=snapshot(db,cid)
    if not saved or saved[0]!=fingerprint(row):return True
    data=json.loads(saved[2])
    return bool(data.get('partial')) and (now<saved[1] or now-saved[1]>=RETRY)


def merge_state(data,previous):
    # A native camera may acknowledge a switch but never report its physical
    # state. Preserve the last acknowledged command, explicitly labelled.
    if previous and data.get('controls',{}).get('light_available') is True and type(data['controls'].get('light')) is not bool:
        old=previous.get('controls',{})
        if type(old.get('light')) is bool and old.get('light_source')=='command':
            data['controls'].update(light=old['light'],light_source='command')
    if type(data.get('controls',{}).get('light')) is bool:
        data['controls'].setdefault('light_source','device')
    return data


def store(db,cid,row,data,now):
    db.execute('INSERT INTO camera_diagnosis VALUES(?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET fingerprint=excluded.fingerprint,checked=excluded.checked,result_json=excluded.result_json',(cid,fingerprint(row),now,json.dumps(data,separators=(',',':'))))


_ANY=object()
def persist(db,cid,row,data,now=None,*,expected=_ANY):
    now=int(time.time()) if now is None else now
    with db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute(TARGET_SQL,(cid,)).fetchone()!=row:return False
        if expected is not _ANY and snapshot(db,cid)!=expected:return False
        from . import native_capabilities
        native_capabilities.record(db,cid,data.get('native_events',[]))
        merge_state(data,cached(db,cid,row))
        store(db,cid,row,data,now)
    return True


def update_control(db,cid,row,diagnosis,control,value,observed=None):
    # Caller owns the transaction. Do not erase capabilities or discovery date.
    if db.execute(TARGET_SQL,(cid,)).fetchone()!=row:return False
    saved=snapshot(db,cid);data=cached(db,cid,row) or diagnosis
    controls=data.setdefault('controls',{})
    if control=='light':
        controls.update(light=value if observed is None else observed,light_source='command' if observed is None else 'device')
    elif control in ('zoom','focus','ir') and isinstance(controls.get(control),dict):controls[control]['value']=value
    elif control in ('brightness','autofocus') and control in controls:controls[control]=value
    for setting in data.get('settings',[]):
        if setting['key']==control:setting['value']=value
    if control=='floodlight_brightness' and 'brightness' in controls:controls['brightness']=value
    if control=='ir_mode' and isinstance(controls.get('ir'),dict):controls['ir']['value']=value
    store(db,cid,row,data,saved[1] if saved and saved[0]==fingerprint(row) else data.get('checked_at',int(time.time())))
    return True


def refresh_missing(db,*,should_stop=lambda:False,clock=time.time,monotonic=time.monotonic,budget=60):
    from . import workers
    candidates=[]
    for (cid,) in db.execute('SELECT id FROM cameras WHERE enabled=1'):
        row=db.execute(TARGET_SQL,(cid,)).fetchone()
        if row and needs_probe(db,cid,row,int(clock())):
            old=snapshot(db,cid)
            candidates.append((old[1] if old and old[0]==fingerprint(row) else 0,cid,row,old))
    start=monotonic()
    for _,cid,row,old in sorted(candidates):
        if should_stop() or not workers.writes_permitted() or monotonic()-start>=budget:return
        result=diagnose(row)
        if should_stop() or not workers.writes_permitted():return
        persist(db,cid,row,result,int(clock()),expected=old)


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
            from . import camera_events
            native_events=set()
            try:native_events.update(camera_events.parse_cgi(client.get('GetAiState',action=0)['value']))
            except (camera_control.ControlError,KeyError,TypeError,AttributeError):pass
            try:
                if client.get('GetMdState',action=0)['value'].get('state') in (0,1):native_events.add('motion')
            except (camera_control.ControlError,KeyError,TypeError,AttributeError):pass
            result=camera_control.capabilities(client)
            settings,partial=camera_settings.read(client)
            result.update(native_events=sorted(native_events),settings=settings,partial=result.get('partial',False) or partial)
            filter_abilities(result,abilities)
    except camera_control.ControlError as exc:
        result={'controls':{},'settings':[],'partial':True,'message':str(exc)}
    result=camera_control.supplement_identity(result,*row[:5])
    # Known absence, not a positive hardware assumption based on model name.
    model=(result.get('device') or {}).get('model','').upper()
    if model=='RLC-510A':
        for key in ('light','light_available','brightness','zoom','focus','autofocus','presets'):result['controls'].pop(key,None)
        result['settings']=[x for x in result['settings'] if x['key'] not in ('speaker_volume','siren','floodlight_brightness')]
        result['talkback']=False
    else:
        result['talkback']=False
        for index,url in enumerate((row[0],row[5] if len(row)>5 else None)):
            if url and probe_talkback(url):
                result.update(talkback=True,talkback_stream=index,talkback_transport='onvif');break
        if not result['talkback'] and (result.get('device') or {}).get('manufacturer','').lower().startswith('reolink') and reolink_talk.probe(row[0]):
            result.update(talkback=True,talkback_stream=0,talkback_transport='baichuan')
    result.update(reboot=False,firmware_upgrade=False)
    if row[1]:
        try:
            endpoint,ip,user,password,mode=target(*row[:5])
            root=soap(endpoint,ip,user,password,DEVICE,'GetServiceCapabilities',mode=mode)
            for node in root.iter():
                if node.tag.rsplit('}',1)[-1]=='GetServiceCapabilitiesResponse':result['reboot']=True
                if node.tag.rsplit('}',1)[-1]=='System' and node.get('HttpFirmwareUpgrade','').lower() in ('true','1'):result['firmware_upgrade']=True
        except (OSError,ValueError,ET.ParseError,http.client.HTTPException):result['partial']=True
    result['checked_at']=int(time.time())
    return result
