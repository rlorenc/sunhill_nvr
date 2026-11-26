"""Bounded Reolink LAN CGI controls. No generic proxy or client-supplied target.

Only standalone cameras (channel 0), explicit administrator requests, and
capabilities confirmed by the camera. The HTTP API must be enabled on the
trusted camera network. No redirects, DNS, proxy environment or TLS bypass.
"""
from __future__ import annotations
import http.client
import json
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, unquote, urlencode
from .camera_discovery import validate_input, DiscoveryError, soap, same_camera, local, DEVICE
from .onvif_time import validate_endpoint
from .baichuan_control import Baichuan, ProtocolError, safe_text


class ControlError(ValueError):
    pass


class Unsupported(ControlError):
    pass


class TransportUnavailable(ControlError):
    pass


class Reolink:
    def __init__(self, rtsp: str):
        try:
            p = urlsplit(rtsp)
            if p.scheme not in ('rtsp', 'rtsps'):
                raise ValueError()
            self.username = unquote(p.username or '')
            self.password = unquote(p.password or '')
            self.ip = validate_input(p.hostname, self.username, self.password)
            if not self.username:
                raise ValueError()
        except (ValueError, TypeError, DiscoveryError):
            raise ControlError('Sterowanie wymaga prywatnego IP oraz danych logowania zapisanych w konfiguracji RTSP.') from None
        self.token = None
        self.native = None
        self.device = {}
        self.path = '/api.cgi'
        self.deadline = time.monotonic() + 14

    def _call(self, command, param, *, action=0, token=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ControlError('Przekroczono czas odpowiedzi kamery.')
        query = {'cmd': command}
        if token is not None or command=='Login':
            query['token'] = token if token is not None else 'null'
        conn = http.client.HTTPConnection(self.ip, 80, timeout=min(2.5, remaining))
        try:
            conn.request('POST', self.path + '?' + urlencode(query),
                         json.dumps([{'cmd': command, 'action': action, 'param': param}]),
                         {'Content-Type': 'application/json', 'Connection': 'close'})
            response = conn.getresponse()
            if response.status in (401,403):
                raise ControlError('Kamera odrzuciła dostęp do API HTTP. Sprawdź uprawnienia konta.')
            if response.status in (404, 405):
                raise TransportUnavailable('API HTTP kamery jest niedostępne.')
            if response.status != 200:
                raise TransportUnavailable('Kamera odrzuciła połączenie HTTP z API.')
            data = response.read(131073)
            if len(data) > 131072:
                raise ValueError()
            rows = json.loads(data)
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get('cmd') != command:
                raise ValueError()
            row = rows[0]
            if row.get('code') != 0:
                raise Unsupported('Kamera nie obsługuje polecenia lub konto nie ma uprawnień.')
            if not isinstance(row.get('value'), dict):
                raise ValueError()
            return row
        except ControlError:
            raise
        except (OSError, ValueError, http.client.HTTPException):
            raise TransportUnavailable('Brak odpowiedzi CGI na porcie 80.') from None
        finally:
            conn.close()

    def __enter__(self):
        for path in ('/api.cgi', '/cgi-bin/api.cgi'):
            self.path = path
            try:
                result = self._call('Login', {'User': {'userName': self.username, 'password': self.password}}, action=0)
                break
            except TransportUnavailable:
                if path == '/cgi-bin/api.cgi':
                    try:
                        self.native=Baichuan(self.ip,self.username,self.password)
                        return self.native.__enter__()
                    except ProtocolError as exc:
                        raise ControlError('CGI (80) niedostępne. '+str(exc)) from None
                    except (OSError,ValueError):
                        raise ControlError('Nie udało się połączyć przez CGI (80) ani Baichuan (9000). Sprawdź dostępność portu 9000 i lokalne dane logowania.') from None
        token = result['value'].get('Token', {}).get('name') if isinstance(result['value'].get('Token'), dict) else None
        if not isinstance(token, str) or not token or len(token) > 256 or not token.isascii():
            raise ControlError('Kamera nie zwróciła poprawnego tokenu API.')
        self.token = token
        try:
            info = self.get('GetDevInfo', {}, action=0)['value'].get('DevInfo', {})
            # Never target the first camera of a multichannel NVR by mistake.
            if not isinstance(info, dict) or info.get('channelNum') != 1:
                raise ControlError('Sterowanie obsługuje samodzielne kamery Reolink (jeden kanał).')
            self.device={'manufacturer':'Reolink','protocol':'CGI HTTP'}
            for source,target in [('model','model'),('hardVer','hardware'),('firmVer','firmware')]:
                value=safe_text(info.get(source))
                if value:self.device[target]=value
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        if self.native:
            self.native.close();self.native=None
        if self.token:
            try:
                self._call('Logout', {}, token=self.token)
            except ControlError:
                pass
            self.token = None

    def get(self, command, param=None, *, action=None):
        if action is None:action=0 if command in ('GetPtzPreset','GetAutoFocus') else 1
        return self._call(command, {'channel': 0} if param is None else param, action=action, token=self.token)

    def set(self, command, param):
        result = self._call(command, param, token=self.token)
        if result['value'].get('rspCode') != 200:
            raise ControlError('Kamera nie potwierdziła wykonania polecenia.')


def identify_onvif(rtsp, endpoint=None, username=None, password=None, auth_mode=None):
    """Read-only identity; never interprets ONVIF identity as control capability.

    Use explicitly configured credentials only with the configured camera IP.
    Automatic fallback uses the saved RTSP login on the same private IPv4.
    """
    try:
        parts=urlsplit(rtsp)
        if parts.scheme not in ('rtsp','rtsps'):return {}
        user=unquote(parts.username or '');secret=unquote(parts.password or '')
        ip=validate_input(parts.hostname,user,secret)
    except (ValueError,TypeError):return {}
    targets=[]
    if endpoint:
        try:
            validate_endpoint(endpoint);same_camera(endpoint,ip,('http','https'))
            u=user if username is None else username;p=secret if password is None else password
            validate_input(ip,u,p)
            if auth_mode in ('digest','wsse') and (auth_mode!='wsse' or u):targets.append((endpoint,u,p,auth_mode))
        except (ValueError,TypeError):pass
    for port in (8000,80):
        for mode in ('digest','wsse') if user else ('digest',):
            target=(f'http://{ip}:{port}/onvif/device_service',user,secret,mode)
            if target not in targets:targets.append(target)
    deadline=time.monotonic()+6
    for url,u,p,mode in targets:
        if time.monotonic()>=deadline:break
        try:
            root=soap(url,ip,u,p,DEVICE,'GetDeviceInformation',mode=mode)
            response=local(root,'GetDeviceInformationResponse')
            if response is None:continue
            identity={}
            for tag,key in (('Manufacturer','manufacturer'),('Model','model'),('FirmwareVersion','firmware'),('HardwareId','hardware')):
                node=local(response,tag);value=safe_text(node.text if node is not None else None)
                if value:identity[key]=value
            if identity.get('model'):return {**identity,'identity_source':'ONVIF'}
        except (OSError,ValueError,ET.ParseError,http.client.HTTPException):continue
    return {}


def supplement_identity(result,rtsp,endpoint=None,username=None,password=None,auth_mode=None):
    """Identity lookup does not authorize physical commands."""
    device=result.get('device') or {}
    if not device.get('model'):
        identity=identify_onvif(rtsp,endpoint,username,password,auth_mode)
        if identity:result['device']={**identity,**{k:v for k,v in device.items() if v},'identity_source':'ONVIF'}
    return result


def obj(data, key):
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def numeric(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def position(row, key):
    current = obj(obj(obj(row, 'value'), 'ZoomFocus'), key).get('pos')
    limit = obj(obj(obj(row, 'range'), 'ZoomFocus'), key)
    # Reolink firmwares return range.zoom.pos or range.zoom directly.
    bounds = obj(limit, 'pos') or limit
    lo, hi = bounds.get('min'), bounds.get('max')
    if numeric(lo, 0, 65535) and numeric(hi, lo+1, 65535) and numeric(current, lo, hi):
        return {'value': current, 'min': lo, 'max': hi}
    return None


def capabilities(client):
    """A rejected getter is not evidence of a capability. Never guess bounds."""
    if isinstance(client,Baichuan):
        try:return {'controls':client.light(),'partial':False,'device':client.device}
        except ProtocolError as exc:return {'controls':{},'partial':True,'device':client.device,'message':str(exc)}
        except (OSError,ValueError):return {'controls':{},'partial':True,'device':client.device,'message':'Baichuan: brak odpowiedzi podczas odczytu ustawień reflektora.'}
    out = {}
    errors = 0
    def read(command):
        nonlocal errors
        try:
            return client.get(command)
        except Unsupported:
            return {}
        except ControlError:
            errors += 1
            return {}
    led = read('GetWhiteLed')
    white = obj(obj(led, 'value'), 'WhiteLed')
    if numeric(white.get('state'), 0, 1):
        out['light'] = bool(white['state'])
        if numeric(white.get('bright'), 0, 100):
            out['brightness'] = white['bright']
    ir = read('GetIrLights')
    state = obj(obj(ir, 'value'), 'IrLights').get('state')
    if state in ('Auto', 'Off', 'On'):
        options = obj(obj(ir, 'range'), 'IrLights').get('state', [])
        out['ir'] = {'value': state, 'options': [s for s in ('Auto', 'Off', 'On') if s in options] if isinstance(options, list) and options else ['Auto', 'Off']}
    zoom = read('GetZoomFocus')
    for key in ('zoom', 'focus'):
        data = position(zoom, key)
        if data:
            out[key] = data
    if 'focus' in out:
        af=obj(obj(read('GetAutoFocus'),'value'),'AutoFocus').get('disable')
        if numeric(af,0,1):out['autofocus']=not bool(af)
    # Preset recall is finite. Continuous pan/tilt is deliberately not sent
    # until a safe device-side timeout is available.
    presets = obj(read('GetPtzPreset'), 'value').get('PtzPreset')
    if isinstance(presets, list):
        valid = [{'id': p['id'], 'name': p.get('name', '')[:80] if isinstance(p.get('name'), str) else ''}
                 for p in presets[:64] if isinstance(p, dict) and p.get('enable') == 1 and numeric(p.get('id'), 0, 255)]
        if valid:
            out['presets'] = valid
    return {'controls': out, 'partial': bool(errors), **({'device':client.device} if isinstance(getattr(client,'device',None),dict) and client.device else {})}


def validate_command(data):
    if not isinstance(data, dict) or set(data) != {'control', 'value'}:
        raise ControlError('Nieprawidłowe polecenie sterowania.')
    control, value = data['control'], data['value']
    if not isinstance(control, str) or not (
        (control in ('light','autofocus') and type(value) is bool) or
        (control == 'brightness' and numeric(value, 0, 100)) or
        (control == 'ir' and value in ('Auto', 'Off', 'On')) or
        (control in ('zoom', 'focus', 'preset') and numeric(value, 0, 65535))):
        raise ControlError('Nieprawidłowe polecenie sterowania.')
    return control, value


def apply(client, control, value):
    if isinstance(client,Baichuan):
        if control!='light':raise Unsupported('Ta funkcja nie jest jeszcze obsługiwana przez Baichuan.')
        try:client.switch(value)
        except ProtocolError as exc:raise ControlError(str(exc)) from None
        except (OSError,ValueError):raise ControlError('Baichuan: kamera nie potwierdziła wykonania polecenia reflektora.') from None
        return
    # Recheck only the affected capability to avoid excess requests, and
    # reject arbitrary values even if the browser supplied its own bounds.
    if control in ('light', 'brightness'):
        row = obj(obj(client.get('GetWhiteLed'), 'value'), 'WhiteLed')
        if not numeric(row.get('state'), 0, 1) or (control == 'brightness' and not numeric(row.get('bright'), 0, 100)):
            raise Unsupported('Funkcja światła nie jest dostępna.')
        client.set('SetWhiteLed', {'WhiteLed': {'channel': 0, ('state' if control == 'light' else 'bright'): int(value)}})
    elif control == 'ir':
        row = client.get('GetIrLights')
        state = obj(obj(row, 'value'), 'IrLights').get('state')
        options = obj(obj(row, 'range'), 'IrLights').get('state', [])
        allowed = options if isinstance(options, list) and options else ['Auto', 'Off']
        if state not in ('Auto', 'Off', 'On') or value not in allowed:
            raise Unsupported('Tryb podczerwieni nie jest dostępny.')
        client.set('SetIrLights', {'IrLights': {'channel': 0, 'state': value}})
    elif control in ('zoom', 'focus'):
        bounds = position(client.get('GetZoomFocus'), control)
        if not bounds or not numeric(value, bounds['min'], bounds['max']):
            raise Unsupported('Pozycja nie mieści się w zakresie zgłoszonym przez kamerę.')
        client.set('StartZoomFocus', {'ZoomFocus': {'channel': 0, 'op': 'ZoomPos' if control == 'zoom' else 'FocusPos', 'pos': value}})
    elif control == 'autofocus':
        state=obj(obj(client.get('GetAutoFocus'),'value'),'AutoFocus').get('disable')
        if not numeric(state,0,1):raise Unsupported('Autofocus nie jest dostępny.')
        client.set('SetAutoFocus',{'AutoFocus':{'channel':0,'disable':0 if value else 1}})
    elif control == 'preset':
        rows = obj(client.get('GetPtzPreset'), 'value').get('PtzPreset')
        if not isinstance(rows, list) or not any(isinstance(p, dict) and p.get('enable') == 1 and p.get('id') == value for p in rows[:64]):
            raise Unsupported('Pozycja PTZ nie jest dostępna.')
        client.set('PtzCtrl', {'channel': 0, 'op': 'ToPos', 'id': value})
    else:
        raise ControlError('Nieprawidłowe polecenie sterowania.')
