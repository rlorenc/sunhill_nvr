"""Explicit Reolink settings registry. Only typed, confirmed fields are exposed.
No arbitrary CGI commands, credential changes, network changes or raw payloads.
"""
from . import camera_control as c

# key: getter, response object, path, kind, allowed values, group, label.
# Numeric settings use the advertised range, not guessed firmware limits.
FIELDS = {
    'day_night': ('GetIsp','Isp',('dayNight',),'select',('Auto','Color','Black&White'),'Obraz','Tryb dzień/noc'),
    'backlight': ('GetIsp','Isp',('backLight',),'select',('Off','BackLightControl','DynamicRangeControl'),'Obraz','Kompensacja podświetlenia'),
    'mirror': ('GetIsp','Isp',('mirror',),'bool',None,'Obraz','Odbicie lustrzane'),
    'flip': ('GetIsp','Isp',('flip',),'bool',None,'Obraz','Obrót obrazu'),
    'hdr': ('GetIsp','Isp',('hdr',),'bool',None,'Obraz','HDR'),
    'image_brightness': ('GetImage','Image',('bright',),'range',None,'Obraz','Jasność obrazu'),
    'contrast': ('GetImage','Image',('contrast',),'range',None,'Obraz','Kontrast'),
    'saturation': ('GetImage','Image',('saturation',),'range',None,'Obraz','Nasycenie'),
    'sharpness': ('GetImage','Image',('sharpen',),'range',None,'Obraz','Ostrość cyfrowa'),
    'status_led': ('GetPowerLed','PowerLed',('state',),'select',('On','Off','Auto'),'Oświetlenie','Dioda statusu'),
    'audio_record': ('GetEnc','Enc',('audio',),'bool',None,'Dźwięk','Dźwięk strumienia kamery'),
    'speaker_volume': ('GetAudioCfg','AudioCfg',('volume',),'range',None,'Dźwięk','Głośność głośnika'),
    'motion_detection': ('GetMdAlarm','MdAlarm',('enable',),'bool',None,'Detekcja','Detekcja ruchu'),
    'sd_record': ('GetRecV20','Rec',('enable',),'bool',None,'Funkcje kamery','Nagrywanie lokalne'),
    'push': ('GetPushV20','Push',('enable',),'bool',None,'Funkcje kamery','Powiadomienia push'),
    'siren': ('GetAudioAlarmV20','Audio',('enable',),'bool',None,'Funkcje kamery','Automatyczny alarm dźwiękowy'),
    'osd_name': ('GetOsd','Osd',('osdChannel','enable'),'bool',None,'Obraz','Nazwa kamery na obrazie'),
    'osd_time': ('GetOsd','Osd',('osdTime','enable'),'bool',None,'Obraz','Data i czas na obrazie'),
}

def descend(value,path):
    for key in path:
        if not isinstance(value,dict):return None
        value=value.get(key)
    return value

def descriptor(key,row):
    _,root,path,kind,allowed,group,label=FIELDS[key]
    state=c.obj(c.obj(row,'value'),root)
    if state.get('channel',0)!=0:return None
    value=descend(state,path)
    limits=descend(c.obj(c.obj(row,'range'),root),path)
    result={'key':key,'group':group,'label':label,'type':kind,'value':value}
    if kind=='bool':
        if not c.numeric(value,0,1):return None
        result['value']=bool(value)
    elif kind=='select':
        if isinstance(limits,list):options=[x for x in allowed if x in limits]
        else:options=list(allowed)
        if value not in options or len(options)<2:return None
        result['options']=options
    else:
        if not isinstance(limits,dict):return None
        lo,hi=limits.get('min'),limits.get('max')
        if not c.numeric(lo,0,65535) or not c.numeric(hi,lo+1,65535) or not c.numeric(value,lo,hi):return None
        result.update(min=lo,max=hi)
    return result

def read(client):
    if isinstance(client,c.Baichuan):return native_read(client)
    rows={};partial=False;out=[]
    for key,(cmd,*_) in FIELDS.items():
        if cmd not in rows:
            try:rows[cmd]=client.get(cmd)
            except c.Unsupported:rows[cmd]={}
            except c.ControlError:rows[cmd]={};partial=True
        item=descriptor(key,rows[cmd])
        if item:out.append(item)
    return out,partial

def valid(key,value):
    if key in ('ir_mode','floodlight_brightness'):
        return (key=='ir_mode' and isinstance(value,str) and value in ('Auto','Off','On')) or (key=='floodlight_brightness' and c.numeric(value,0,100))
    if key not in FIELDS:return False
    kind,allowed=FIELDS[key][3:5]
    return (kind=='bool' and type(value) is bool or kind=='select' and isinstance(value,str) and value in allowed or kind=='range' and c.numeric(value,0,65535))

def apply(client,key,value):
    if not valid(key,value):raise c.Unsupported('Ustawienie nie jest dostępne dla tej kamery.')
    if isinstance(client,c.Baichuan):return native_apply(client,key,value)
    if key not in FIELDS:raise c.Unsupported('Ustawienie nie jest dostępne dla tej kamery.')
    cmd,root,path,kind,*_=FIELDS[key]
    row=client.get(cmd);info=descriptor(key,row)
    if not info or (kind=='select' and value not in info['options']) or (kind=='range' and not c.numeric(value,info['min'],info['max'])):
        raise c.Unsupported('Kamera nie potwierdziła ustawienia lub zakresu.')
    # Preserve unrelated configuration: for schedule/encoding/OSD use the
    # current object, modify exactly one field. Never expose it in the API.
    import copy
    state=copy.deepcopy(c.obj(c.obj(row,'value'),root));state['channel']=0
    target=state
    for part in path[:-1]:target=target[part]
    target[path[-1]]=int(value) if kind=='bool' else value
    client.set('Set'+cmd[3:],{root:state})


# Verified Baichuan command pairs. Preserve existing schedules and other XML
# fields in writes; never send a generic command or a user-provided XML tree.
NATIVE={
    'ir_mode':(208,209,'LedState','state','Oświetlenie','Podczerwień',{'auto':'Auto','close':'Off','open':'On'}),
    'status_led':(208,209,'LedState','lightState','Oświetlenie','Dioda statusu',{'close':'Off','open':'On'}),
    'floodlight_brightness':(289,290,'FloodlightTask','brightness_cur','Oświetlenie','Jasność reflektora',None),
    'motion_detection':(46,47,'MD','enable','Detekcja','Detekcja ruchu',{'0':False,'1':True}),
    'siren':(232,233,'AudioTask','enable','Funkcje kamery','Automatyczny alarm dźwiękowy',{'0':False,'1':True}),
}

def native_node(client,cmd,tag):
    root=client.request(cmd,channel=0)
    node=root.find('.//'+tag) if root is not None else None
    if node is not None and all(node.findtext(k) in (None,'0') for k in ('channel','channelId')):return node
    return None

def native_descriptor(key,node):
    if node is None:return None
    _,_,_,field,group,label,mapping=NATIVE[key]
    current=node.findtext(field)
    result={'key':key,'group':group,'label':label}
    if mapping is not None:
        if current not in mapping:return None
        value=mapping[current]
        result.update(value=value,type='bool' if type(value) is bool else 'select')
        if type(value) is not bool:result['options']=list(mapping.values())
    else:
        try:lo,hi,value=(int(node.findtext(k,'')) for k in ('brightness_min','brightness_max',field))
        except ValueError:return None
        if not 0<=lo<=value<=hi<=100 or lo==hi:return None
        result.update(type='range',min=lo,max=hi,value=value)
    return result

def native_read(client):
    from .baichuan_control import Rejected,ProtocolError
    rows={};out=[];partial=False
    for key,(cmd,_,tag,*_) in NATIVE.items():
        if cmd not in rows:
            try:rows[cmd]=native_node(client,cmd,tag)
            except Rejected:rows[cmd]=None
            except (ProtocolError,OSError,ValueError):rows[cmd]=None;partial=True
        info=native_descriptor(key,rows[cmd])
        if info:out.append(info)
    return out,partial

def native_apply(client,key,value):
    import copy
    import xml.etree.ElementTree as ET
    from .baichuan_control import ProtocolError
    if key not in NATIVE:raise c.Unsupported('Ustawienie nie jest dostępne przez Baichuan.')
    cmd,setter,tag,field,_,_,mapping=NATIVE[key]
    try:
        node=native_node(client,cmd,tag);info=native_descriptor(key,node)
        if not info:raise c.Unsupported('Kamera nie potwierdziła ustawienia.')
        if mapping is None:
            if not c.numeric(value,info['min'],info['max']):raise c.Unsupported('Poza zakresem kamery.')
            wire=str(value)
        else:
            wire=next((k for k,v in mapping.items() if type(value) is type(v) and value==v),None)
            if wire is None:raise c.Unsupported('Nieobsługiwany tryb kamery.')
        node=copy.deepcopy(node);node.find(field).text=wire
        body=ET.Element('body');body.append(node)
        client.request(setter,ET.tostring(body,encoding='utf-8',xml_declaration=True),channel=0)
    except (ProtocolError,OSError) as exc:raise c.ControlError('Baichuan: nie udało się odczytać lub zapisać ustawienia.') from None
