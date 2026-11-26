"""WSGI application API. Deployment requires an authenticated reverse proxy.

The application intentionally exposes no streaming implementation directly; the
reverse proxy must authorize its streaming endpoint through /internal/live-auth.
"""
from __future__ import annotations
import io
import http.client
import ipaddress
import contextlib
import fcntl
import os
import json
import logging
import re
import sqlite3
import stat
import time
import hmac
import secrets
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from . import security,database,workers
from .media_layout import validate_media_layout,nested_auxiliary
from .cameras import Camera, validate, with_credentials
from . import camera_discovery,player_assets,camera_statistics,camera_control
from .nginx_config import HOST
from .fd_mount import MountBoundary,MountUnavailable
from .files import FileBody, InvalidRange, acquire_shared, open_confined, open_export, delete_export, parse_range, recorded_identity
from . import archive_compat, __version__, approved_update, ftp_accounts, jobs, export_service, protection, media, home_assistant, health_service, limits, upgrade_inbox, rotation_service, tls_inbox, storage_inbox, domain_inbox, monitor, stream_health, clock_service

MAX_BODY=16*1024
ARCHIVE_PAGE_SIZE=10000

def archive_cursor(value, *, event=False):
    """Validate the position of a keyset page without trusting it as SQL."""
    from datetime import datetime
    if value is None:return None
    if not isinstance(value,str) or len(value)>80:raise ValueError('invalid cursor')
    parts=value.split('|')
    if len(parts)!=2 or not re.fullmatch(r'[1-9][0-9]{0,12}',parts[1]):raise ValueError('invalid cursor')
    if event:
        if not re.fullmatch(r'-?[0-9]{1,12}',parts[0]):raise ValueError('invalid cursor')
        first=int(parts[0])
    else:
        if not re.fullmatch(r'[0-9T:.+Z-]{1,40}',parts[0]):raise ValueError('invalid cursor')
        first=parts[0]
        if datetime.fromisoformat(first.replace('Z','+00:00')).utcoffset() is None:raise ValueError('invalid cursor')
    return first,int(parts[1])

class APIError(Exception):
    def __init__(self,status,message):super().__init__(message);self.status=status;self.message=message

class ImageBody:
    def __init__(self,data):self.data=data

def clear_audio_status(db,slug: str) -> None:
    db.execute('DELETE FROM system_health WHERE name=?',('recorder_audio:'+slug,))
    db.execute('DELETE FROM system_settings WHERE key=?',('recorder_audio_seen:'+slug,))
    if db.execute("DELETE FROM pending_notifications WHERE source=? AND state IN ('recording_audio_missing','recording_audio_ok')",(slug,)).rowcount:
        health_service.invalidate_delivery(db)

def domain_status(path: Path) -> dict:
    result={'state':'idle','message':'','updated':None}
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>4096:raise ValueError()
            value=json.loads(os.read(fd,4097))
            if (isinstance(value,dict) and value.get('state') in ('applying','done','failed','recovery_required')
                and isinstance(value.get('message'),str) and type(value.get('updated')) is int):return value
        finally:os.close(fd)
    except (OSError,ValueError,TypeError):pass
    return result

class Application:
    def __init__(self, db_path: Path, archive_root: Path = Path('/home/store/kamery'), export_root: Path = Path('/opt/sunhill-nvr/data/exports'), *, allowed_origin: str, archive_lock: Path = Path('/opt/sunhill-nvr/data/archive.lock'), timezone: str = 'Europe/Warsaw', control_state: Path = Path('/opt/sunhill-nvr/control'), upgrade_public_key: Path = Path('/opt/sunhill-nvr/config/release-public.pem'), trust_client_header: bool = False, minimum_free: int = 10_000_000_000, free_buffer: int = 5_000_000_000, staging_root: Path | None=None):
        self.db_path=db_path
        self.archive_root=archive_root
        self.export_root=export_root
        self.staging_root=staging_root
        self.archive_lock=archive_lock
        self.timezone=timezone
        self.upgrade_inbox=db_path.parent/'upgrade-inbox'
        self.upgrade_status=control_state/'upgrade'/'status.json'
        self.tls_status=control_state/'upgrade'/'tls-status.json'
        self.domain_status=control_state/'upgrade'/'domain-status.json'
        self.storage_status=control_state/'upgrade'/'storage-status.json'
        self.upgrade_public_key=upgrade_public_key
        self.trust_client_header=trust_client_header
        if type(minimum_free) is not int or type(free_buffer) is not int or minimum_free<0 or free_buffer<0 or minimum_free+free_buffer>2**63-1:raise ValueError('invalid storage defaults')
        self.default_minimum_free=minimum_free;self.default_free_buffer=free_buffer
        origin=urlsplit(allowed_origin)
        try:port=origin.port
        except ValueError as exc:raise ValueError('invalid origin port') from exc
        if origin.scheme!='https' or not origin.hostname or not HOST.fullmatch(origin.hostname) or (port is not None and not 1<=port<=65535) or origin.username or origin.password or origin.path or origin.query or origin.fragment or any(ord(c)<33 or ord(c)==127 for c in allowed_origin):
            raise ValueError('allowed origin must be a single HTTPS origin')
        authority=origin.hostname+((':'+str(port)) if port is not None and port!=443 else '')
        self.allowed_origin='https://'+authority
        self.websocket_origin='wss://'+authority

    def request_origin(self,environ):
        """Accept the LAN origin only from the loopback proxy and private IPv4 peers."""
        supplied=environ.get('HTTP_X_SUNHILL_LAN_ORIGIN','')
        if self.trust_client_header and supplied:
            try:
                host=environ.get('HTTP_HOST','')
                address=ipaddress.IPv4Address(host)
                client=ipaddress.IPv4Address(environ.get('HTTP_X_SUNHILL_CLIENT_IP',''))
                networks=tuple(ipaddress.IPv4Network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8'))
                if (ipaddress.ip_address(environ.get('REMOTE_ADDR','')).is_loopback
                    and supplied=='http://'+str(address)
                    and any(address in n for n in networks) and any(client in n for n in networks)):
                    return supplied
            except (ValueError,TypeError):pass
        return self.allowed_origin

    def __call__(self,environ,start_response):
        origin=self.request_origin(environ)
        websocket_origin='ws://'+origin[7:] if origin.startswith('http://') else self.websocket_origin
        headers=[('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),
                 ('Referrer-Policy','no-referrer'),('Cache-Control','no-store'),
                 ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self' "+websocket_origin+"; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")]
        db=None
        try:
            # Nginx also fences requests, but local callers can reach Gunicorn
            # directly. Permit only the read-only activation health probe.
            if not workers.writes_permitted() and not (environ.get('REQUEST_METHOD','GET').upper()=='GET' and environ.get('PATH_INFO')=='/internal/health'):
                raise APIError('503 Service Unavailable','Trwa konserwacja systemu')
            db=database.open_db(self.db_path)
            status,payload,extra=self.dispatch(db,environ)
            if isinstance(payload,FileBody):
                try:start_response(status,headers+extra)
                except BaseException:
                    # Until the iterable is handed to the WSGI server, its
                    # descriptor and archive lock are still our responsibility.
                    payload.close();raise
                return payload
            if isinstance(payload,ImageBody):
                start_response(status,headers+[('Content-Type','image/jpeg'),('Content-Length',str(len(payload.data)))]+extra)
                return [payload.data]
            if url := environ.get('PATH_INFO',''):
                mime={'/':'text/html; charset=utf-8','/assets/app.css':'text/css; charset=utf-8','/assets/app.js':'text/javascript; charset=utf-8'}.get(url)
                if player_assets.ASSET_PATH.fullmatch(url):mime='text/javascript; charset=utf-8'
                if mime and isinstance(payload,bytes):
                    start_response(status,headers+[('Content-Type',mime),('Content-Length',str(len(payload)))]+extra)
                    return [b''] if environ.get('REQUEST_METHOD','').upper()=='HEAD' else [payload]
            if isinstance(payload,bytes):body=payload
            else:body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
            headers += [('Content-Type','application/json; charset=utf-8'),('Content-Length',str(len(body)))] + extra
            start_response(status,headers)
            return [body]
        except APIError as exc:
            body=json.dumps({'error':exc.message},ensure_ascii=False).encode('utf-8')
            start_response(exc.status,headers+[('Content-Type','application/json; charset=utf-8'),('Content-Length',str(len(body)))])
            return [b''] if environ.get('REQUEST_METHOD','').upper()=='HEAD' else [body]
        except Exception as exc:
            # Exception values may contain camera credentials or user input.
            logging.error('Sunhill request failed: %s',type(exc).__name__)
            body=b'{\"error\":\"Blad serwera\"}'
            start_response('500 Internal Server Error',headers+[('Content-Type','application/json; charset=utf-8'),('Content-Length',str(len(body)))])
            return [b''] if environ.get('REQUEST_METHOD','').upper()=='HEAD' else [body]
        finally:
            if db is not None:db.close()

    def dispatch(self,db,environ):
        method=environ.get('REQUEST_METHOD','GET').upper()
        url=environ.get('PATH_INFO','/')
        cookie=SimpleCookie()
        try:cookie.load(environ.get('HTTP_COOKIE',''))
        except Exception:cookie=SimpleCookie()
        token=cookie['sunhill_session'].value if 'sunhill_session' in cookie else ''
        user=security.get_user(db,token)
        if method=='GET' and url=='/internal/health':
            # Gunicorn binds to loopback; nginx never forwards this endpoint.
            # Exercise the database and essential tables before accepting an update.
            db.execute('SELECT id FROM users LIMIT 1').fetchone()
            db.execute('SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1').fetchone()
            db.execute('SELECT id FROM recordings LIMIT 1').fetchone()
            db.execute('SELECT id FROM export_jobs LIMIT 1').fetchone()
            return '200 OK',{'status':'ok'},[]
        static={'/':'index.html','/assets/app.css':'app.css','/assets/app.js':'app.js'}
        if method in ('GET','HEAD') and url in static:
            data=(Path(__file__).parent/'static'/static[url]).read_bytes()
            if url=='/':
                data=data.replace(b'__SUNHILL_VERSION__',__version__.encode('ascii'))
            return '200 OK',data,[]
        player_asset=player_assets.ASSET_PATH.fullmatch(url)
        if method in ('GET','HEAD') and player_asset:
            try:data=player_assets.load_player_asset(player_asset.group(1))
            except (OSError,ValueError,http.client.HTTPException):
                raise APIError('503 Service Unavailable','Odtwarzacz bramki chwilowo niedostępny')
            return '200 OK',data,[]
        if url=='/api/integrations/events' and method=='POST':
            authorization=environ.get('HTTP_AUTHORIZATION','')
            if not authorization.startswith('Bearer ') or len(authorization)>135:
                raise APIError('401 Unauthorized','Nieprawidłowy token integracji')
            supplied=authorization[7:]
            row=db.execute("SELECT value FROM system_settings WHERE key='event_token_hash'").fetchone()
            if not supplied.isascii() or not row or not hmac.compare_digest(security.token_hash(supplied),row[0]):
                raise APIError('401 Unauthorized','Nieprawidłowy token integracji')
            data=self.json_body(environ)
            try:
                if set(data)!={'camera','type','time'} or not isinstance(data['camera'],str) or data['type'] not in ('motion','person','vehicle','animal'):
                    raise ValueError()
                instant=export_service.parse_instant(data['time']);timestamp=int(instant.timestamp());now=int(time.time())
                if timestamp>now+300 or timestamp<now-7*86400:raise ValueError()
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    # Revocation or camera removal can race body parsing. Check
                    # both again in the transaction that publishes the marker.
                    current=db.execute("SELECT value FROM system_settings WHERE key='event_token_hash'").fetchone()
                    if not current or not hmac.compare_digest(security.token_hash(supplied),current[0]):
                        raise APIError('401 Unauthorized','Nieprawidłowy token integracji')
                    camera=db.execute('SELECT id FROM cameras WHERE slug=? AND enabled=1',(data['camera'],)).fetchone()
                    if not camera:raise ValueError()
                    db.execute('INSERT OR IGNORE INTO event_markers(camera_id,started_at,event_type,source) VALUES (?,?,?,?)',(camera[0],timestamp,data['type'],'home_assistant'))
                return '202 Accepted',{'ok':True},[]
            except (ValueError,TypeError,OverflowError):raise APIError('400 Bad Request','Nieprawidłowe zdarzenie')
        origin=self.request_origin(environ)
        secure_cookie='; Secure' if origin.startswith('https://') else ''
        if method not in ('GET','HEAD') or url=='/internal/live-auth':
            if environ.get('HTTP_ORIGIN')!=origin:
                raise APIError('403 Forbidden','Nieprawidłowe źródło żądania')
        if url=='/api/setup' and method=='GET':
            return '200 OK',{'available':security.setup_available(db)},[]
        if url=='/api/setup' and method=='POST':
            data=self.json_body(environ)
            if set(data)!={'token','username','password'}:raise APIError('400 Bad Request','Nieprawidłowe dane konfiguracji')
            try:
                security.reserve_setup_attempt(db,self.client_address(environ))
                security.complete_setup(db,data['token'],data['username'],data['password'])
                return '201 Created',{'ok':True},[]
            except security.PasswordValidationError as error:raise APIError('400 Bad Request',str(error))
            except (security.AuthError,ValueError,TypeError,sqlite3.IntegrityError):
                raise APIError('400 Bad Request','Nieprawidłowy lub wygasły kod, nazwa albo hasło')
        if url=='/api/login' and method=='POST':
            data=self.json_body(environ)
            client=self.client_address(environ)
            try:session=security.authenticate(db,data.get('username',''),data.get('password',''),client,audit=True)
            except security.AuthError:raise APIError('401 Unauthorized','Nieprawidłowe dane lub zbyt wiele prób logowania')
            # Strict, host-only cookies; HTTPS retains Secure, explicit LAN HTTP cannot use it.
            value=f'sunhill_session={session.token}; Path=/; HttpOnly{secure_cookie}; SameSite=Strict; Max-Age={security.SESSION_LIFETIME}'
            csrf_cookie=f'sunhill_csrf={session.csrf}; Path=/{secure_cookie}; SameSite=Strict; Max-Age={security.SESSION_LIFETIME}'
            return '200 OK',{'csrf':session.csrf},[('Set-Cookie',value),('Set-Cookie',csrf_cookie)]
        if not user:
            raise APIError('401 Unauthorized','Wymagane logowanie')
        if method not in ('GET','HEAD'):
            if not security.verify_csrf(user,environ.get('HTTP_X_CSRF_TOKEN','')):
                raise APIError('403 Forbidden','Błędny token zabezpieczający')
        if url=='/api/account/password' and method=='POST':
            data=self.json_body(environ)
            if set(data)!={'current','new'}:raise APIError('400 Bad Request','Nieprawidłowe dane')
            try:security.change_own_password(db,user[0],token,data['current'],data['new'])
            except security.AuthError:raise APIError('401 Unauthorized','Nieprawidłowe hasło lub zbyt wiele prób')
            except security.PasswordValidationError as error:raise APIError('400 Bad Request',str(error))
            except (ValueError,TypeError):raise APIError('400 Bad Request','Nieprawidłowe dane hasła')
            return '200 OK',{'ok':True},[('Set-Cookie',f'sunhill_session=; Path=/; HttpOnly{secure_cookie}; SameSite=Strict; Max-Age=0'),('Set-Cookie',f'sunhill_csrf=; Path=/{secure_cookie}; SameSite=Strict; Max-Age=0')]
        if url=='/api/settings/domain' and method=='GET':
            self.admin(user)
            status=domain_status(self.domain_status)
            return '200 OK',{'origin':self.allowed_origin,'status':status,'supported':domain_inbox.recovery_available(),'pending':(self.upgrade_inbox/'domain-request.json').is_file()},[]
        if url=='/api/settings/domain' and method=='POST':
            self.admin(user);data=self.json_body(environ)
            if not domain_inbox.recovery_available():raise APIError('503 Service Unavailable','Ta instalacja wymaga migracji mechanizmu odzyskiwania przed zmianą domeny')
            if domain_status(self.domain_status)['state'] in ('applying','recovery_required'):
                raise APIError('409 Conflict','Trwa zmiana domeny lub jej odzyskiwanie')
            if set(data)!={'hostname'}:raise APIError('400 Bad Request','Wymagana nazwa domeny')
            try:
                hostname=data['hostname']
                if not isinstance(hostname,str) or not HOST.fullmatch(hostname) or hostname.lower()==urlsplit(self.allowed_origin).hostname:raise ValueError()
                domain_inbox.submit(self.upgrade_inbox,hostname,user[0])
                with db:self.audit(db,user[0],'domain.change','system',hostname.lower())
                return '202 Accepted',{'ok':True},[]
            except FileExistsError:raise APIError('409 Conflict','Zmiana domeny już oczekuje')
            except (OSError,ValueError):raise APIError('400 Bad Request','Nieprawidłowa nazwa domeny lub niedostępny katalog')
        if url=='/api/settings/tls' and method=='GET':
            self.admin(user)
            status={'state':'idle','message':'','updated':None}
            try:
                fd=os.open(self.tls_status,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size>4096:raise ValueError()
                    value=json.loads(os.read(fd,4097))
                    if (isinstance(value,dict) and value.get('state') in ('applying','done','failed','recovery_required')
                        and isinstance(value.get('message'),str) and type(value.get('updated')) is int):status=value
                finally:os.close(fd)
            except (OSError,ValueError,TypeError):pass
            return '200 OK',{'status':status,'pending':(self.upgrade_inbox/'tls-request.json').is_file()},[]
        if url=='/api/settings/tls' and method=='POST':
            self.admin(user);data=self.json_body(environ,maximum=128*1024)
            if set(data)!={'certificate','key'}:raise APIError('400 Bad Request','Wymagany certyfikat i klucz')
            try:
                tls_inbox.submit(self.upgrade_inbox,data['certificate'],data['key'],user[0])
                with db:self.audit(db,user[0],'tls.install','system','certificate')
                return '202 Accepted',{'ok':True},[]
            except FileExistsError:raise APIError('409 Conflict','Zmiana certyfikatu już oczekuje')
            except (OSError,ValueError):raise APIError('400 Bad Request','Nieprawidłowy plik lub niedostępny katalog')
        if url=='/api/updates' and method=='GET':
            self.admin(user)
            status={'state':'idle','message':'','updated':None}
            try:
                fd=os.open(self.upgrade_status,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size>4096:raise ValueError()
                    value=json.loads(os.read(fd,4097))
                    if (isinstance(value,dict) and value.get('state') in ('verifying','applying','done','failed','recovery_required')
                        and isinstance(value.get('message'),str) and type(value.get('updated')) is int):status=value
                finally:os.close(fd)
            except (OSError,ValueError,TypeError):pass
            return '200 OK',{'status':status,'pending':(self.upgrade_inbox/'request.json').is_file() or (self.upgrade_inbox/approved_update.REQUEST).is_file(),
                             'packages':approved_update.available(),'version':__version__,
                             'key_configured':self.upgrade_public_key.is_file() and not self.upgrade_public_key.is_symlink()},[]
        match=re.fullmatch(r'/api/update-uploads/(archive|signature)',url)
        if match and method=='POST':
            self.admin(user)
            try:
                raw=environ.get('CONTENT_LENGTH','')
                if not isinstance(raw,str) or not raw.isdigit() or len(raw)>10:raise ValueError()
                token=upgrade_inbox.store(self.upgrade_inbox,match.group(1),environ['wsgi.input'],int(raw))
                return '201 Created',{'id':token},[]
            except BlockingIOError:raise APIError('409 Conflict','Trwa już przesyłanie aktualizacji')
            except (OSError,ValueError,KeyError):raise APIError('400 Bad Request','Nieprawidłowy plik, brak miejsca lub niedostępny katalog aktualizacji')
        match=re.fullmatch(r'/api/update-uploads/(archive|signature)/([0-9a-f]{32})',url)
        if match and method=='DELETE':
            self.admin(user)
            try:upgrade_inbox.remove(self.upgrade_inbox,match.group(1),match.group(2))
            except (OSError,ValueError):raise APIError('409 Conflict','Nie można usunąć pliku aktualizacji')
            return '200 OK',{'ok':True},[]
        if url=='/api/updates' and method=='POST':
            self.admin(user);data=self.json_body(environ)
            if set(data)=={'package'}:
                try:
                    approved_update.submit(self.upgrade_inbox,data['package'],user[0])
                    with db:self.audit(db,user[0],'upgrade.request','system','application')
                    return '202 Accepted',{'ok':True},[]
                except BlockingIOError:raise APIError('409 Conflict','Trwa już operacja aktualizacji')
                except FileExistsError:raise APIError('409 Conflict','Aktualizacja oczekuje już na uruchomienie')
                except (OSError,TypeError,ValueError):raise APIError('400 Bad Request','Paczka nie została zatwierdzona przez administratora systemu')
            if set(data)!={'archive','signature'}:raise APIError('400 Bad Request','Nieprawidłowe żądanie aktualizacji')
            if self.upgrade_public_key.is_symlink() or not self.upgrade_public_key.is_file():
                raise APIError('409 Conflict','Brak zaufanego klucza podpisu aktualizacji')
            try:
                upgrade_inbox.request(self.upgrade_inbox,data['archive'],data['signature'],user[0])
                with db:self.audit(db,user[0],'upgrade.request','system','application')
                return '202 Accepted',{'ok':True},[]
            except BlockingIOError:raise APIError('409 Conflict','Trwa już operacja na plikach aktualizacji')
            except FileExistsError:raise APIError('409 Conflict','Aktualizacja oczekuje już na uruchomienie')
            except (OSError,TypeError,ValueError):raise APIError('400 Bad Request','Nieprawidłowe pliki aktualizacji')
        if url=='/api/time/resolve' and method=='POST':
            data=self.json_body(environ)
            if set(data)!={'local','fold'}:raise APIError('400 Bad Request','Nieprawidłowy czas')
            try:instant=export_service.parse_local(data['local'],self.timezone,data['fold'])
            except (ValueError,TypeError,OverflowError):raise APIError('400 Bad Request','Czas nie istnieje lub wymaga wyboru wystąpienia po zmianie czasu')
            return '200 OK',{'utc':instant.isoformat()},[]
        if url=='/api/me' and method=='GET':
            return '200 OK',{'id':user[0],'username':user[1],'role':user[2],'timezone':self.timezone},[]
        if url=='/api/logout' and method=='POST':
            security.logout(db,token,audit=True)
            return '200 OK',{'ok':True},[('Set-Cookie',f'sunhill_session=; Path=/; HttpOnly{secure_cookie}; SameSite=Strict; Max-Age=0'),('Set-Cookie',f'sunhill_csrf=; Path=/{secure_cookie}; SameSite=Strict; Max-Age=0')]
        if url=='/api/integrations/events/token' and method in ('POST','DELETE'):
            self.admin(user)
            with db:
                db.execute('BEGIN IMMEDIATE');self.current_admin(db,token)
                if method=='POST':
                    token=secrets.token_urlsafe(32)
                    db.execute("INSERT INTO system_settings(key,value) VALUES('event_token_hash',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(security.token_hash(token),))
                    self.audit(db,user[0],'integration.token.rotate','integration','events')
                    return '201 Created',{'token':token},[]
                db.execute("DELETE FROM system_settings WHERE key='event_token_hash'")
                self.audit(db,user[0],'integration.token.revoke','integration','events')
            return '200 OK',{'ok':True},[]
        if url=='/api/settings/home-assistant' and method=='GET':
            self.admin(user);data=dict(db.execute("SELECT key,value FROM system_settings WHERE key IN ('ha_url','ha_token','ha_enabled')"))
            return '200 OK',{'url':data.get('ha_url',''),'enabled':data.get('ha_enabled')=='1','token_configured':bool(data.get('ha_token'))},[]
        if url=='/api/settings/home-assistant' and method=='PATCH':
            self.admin(user);data=self.json_body(environ)
            if set(data)-{'url','token','enabled'} or type(data.get('enabled')) is not bool or not isinstance(data.get('url'),str) or ('token' in data and not isinstance(data['token'],str)):raise APIError('400 Bad Request','Nieprawidłowe ustawienia Home Assistant')
            with db:
                db.execute('BEGIN IMMEDIATE')
                self.current_admin(db,token)
                existing=health_service._settings(db)
                token=data.get('token') or existing.get('ha_token','')
                try:
                    if data.get('token'):home_assistant.validate_token(data['token'])
                    if data['enabled'] or data['url']:
                        home_assistant.build_request(data['url'],token,camera_id='test',state='stale')
                except (ValueError,TypeError):raise APIError('400 Bad Request','Nieprawidłowy adres lub token')
                for key,value in [('ha_url',data['url']),('ha_token',token),('ha_enabled','1' if data['enabled'] else '0'),('ha_generation',secrets.token_hex(16))]:
                    db.execute('INSERT INTO system_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
                if data['enabled'] and (existing.get('ha_enabled')!='1' or existing.get('ha_url','')!=data['url']):
                    db.execute('DELETE FROM pending_notifications')
                    health_service.enqueue_active_faults(db)
                self.audit(db,user[0],'integration.update','integration','home_assistant')
            return '200 OK',{'ok':True},[]
        if url=='/api/settings/monitoring' and method=='GET':
            self.admin(user)
            try:threshold=health_service.stale_threshold(db);invalid=False
            except ValueError:threshold=health_service.DEFAULT_STALE_AFTER;invalid=True
            return '200 OK',{'stale_after':threshold,'invalid':invalid},[]
        if url=='/api/settings/monitoring' and method=='PATCH':
            self.admin(user);data=self.json_body(environ)
            if set(data)!={'stale_after'} or type(data['stale_after']) is not int or not 60<=data['stale_after']<=86400:
                raise APIError('400 Bad Request','Czas alarmu musi wynosić od 1 minuty do 24 godzin')
            with db:
                db.execute('BEGIN IMMEDIATE');self.current_admin(db,token)
                db.execute("INSERT INTO system_settings(key,value) VALUES('recording_stale_after',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(data['stale_after']),))
                self.audit(db,user[0],'monitoring.update','system','recording_threshold')
            return '200 OK',{'ok':True},[]
        if url=='/api/settings/index' and method in ('GET','POST'):
            self.admin(user)
            from . import index_control
            if method=='GET':return '200 OK',index_control.read(db),[]
            data=self.json_body(environ)
            with db:
                db.execute('BEGIN IMMEDIATE');self.current_admin(db,token)
                try:
                    if data=={'action':'pause'}:value=index_control.pause(db)
                    elif set(data)=={'action','camera','start','end'} and data['action']=='start':value=index_control.request(db,data['camera'],data['start'],data['end'])
                    else:raise ValueError()
                except (ValueError,TypeError,OverflowError):raise APIError('400 Bad Request','Nieprawidłowy zakres indeksowania')
                self.audit(db,user[0],'index.'+data['action'],'archive','index')
            return '200 OK',value,[]
        if url=='/api/settings/recording' and method=='GET':
            self.admin(user)
            from .recording_settings import segment_seconds, SEGMENT_SECONDS
            return '200 OK',{'segment_seconds':segment_seconds(db),'allowed':list(SEGMENT_SECONDS)},[]
        if url=='/api/settings/recording' and method=='PATCH':
            self.admin(user);data=self.json_body(environ)
            from .recording_settings import SEGMENT_SECONDS
            if set(data)!={'segment_seconds'} or type(data['segment_seconds']) is not int or data['segment_seconds'] not in SEGMENT_SECONDS:
                raise APIError('400 Bad Request','Nieprawidłowa długość segmentu RTSP')
            with db:
                db.execute('BEGIN IMMEDIATE');self.current_admin(db,token)
                db.execute("INSERT INTO system_settings(key,value) VALUES('recording_segment_seconds',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(data['segment_seconds']),))
                self.audit(db,user[0],'recording.settings','system','segment_seconds')
            return '200 OK',{'ok':True},[]
        if url=='/api/settings/storage' and method=='GET':
            self.admin(user)
            data=dict(db.execute("SELECT key,value FROM system_settings WHERE key IN ('minimum_free','free_buffer')"))
            try:
                minimum=int(data.get('minimum_free',self.default_minimum_free));buffer=int(data.get('free_buffer',self.default_free_buffer))
                if minimum<0 or buffer<0 or minimum+buffer>2**63-1:raise ValueError('invalid storage limits')
                invalid=False
            except (ValueError,TypeError):minimum=buffer=None;invalid=True
            status={'state':'idle','message':'','updated':None}
            try:
                fd=os.open(self.storage_status,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size>4096:raise ValueError()
                    value=json.loads(os.read(fd,4097))
                    if (isinstance(value,dict) and value.get('state') in ('applying','done','failed','recovery_required')
                        and isinstance(value.get('message'),str) and type(value.get('updated')) is int):status=value
                finally:os.close(fd)
            except (OSError,ValueError,TypeError):pass
            return '200 OK',{'archive':str(self.archive_root),'exports':str(self.export_root),'staging':str(self.staging_root) if self.staging_root else None,'minimum_free':minimum,'free_buffer':buffer,'limits_invalid':invalid,
                             'change_status':status,'correlation_supported':storage_inbox.recovery_available(),'change_pending':(self.upgrade_inbox/'storage-request.json').is_file()},[]
        if url=='/api/settings/storage/archive' and method=='POST':
            self.admin(user);data=self.json_body(environ)
            if 'archive' not in data or not set(data)<={'archive','exports','staging','request_id'}:raise APIError('400 Bad Request','Wymagana ścieżka katalogu')
            try:
                archive=storage_inbox.normalize(data['archive'])
                exports=storage_inbox.normalize(data['exports']) if data.get('exports') is not None else str(self.export_root)
                staging=storage_inbox.normalize(data['staging']) if data.get('staging') is not None else (str(self.staging_root) if self.staging_root and archive==str(self.archive_root) else None)
                if (self.upgrade_inbox/'storage-request.json').exists() or (self.storage_status.parent/'storage-transaction.json').exists():raise FileExistsError()
                if 'exports' in data or 'staging' in data:
                    effective_stage=staging or str(Path(archive).parent/'.sunhill-staging')
                    try:validate_media_layout(Path(archive),Path(effective_stage),Path(exports))
                    except ValueError:
                        raise APIError('400 Bad Request','Wybierz oddzielne katalogi. W katalogu nagrań można użyć osobnych ukrytych podkatalogów, np. .sunhill-staging i .sunhill-exports. Wpisane ścieżki nie zostały zmienione.')
                    if any(nested_auxiliary(Path(archive),Path(p)) for p in (effective_stage,exports)) and not storage_inbox.nested_recovery_available():
                        raise APIError('503 Service Unavailable','Zaktualizuj mechanizm recovery poleceniem bash install.sh --update --refresh-recovery, a następnie ponów zapis katalogów.')
                if archive==str(self.archive_root) and exports==str(self.export_root) and (data.get('staging') is None or staging==str(self.staging_root)):
                    return '200 OK',{'ok':True,'unchanged':True},[]
                if data.get('request_id') is not None and not storage_inbox.recovery_available():
                    raise APIError('503 Service Unavailable','Najpierw zaktualizuj przypięty mechanizm recovery do 0.2.19.')
                storage_inbox.submit(self.upgrade_inbox,archive,user[0],exports=exports if 'exports' in data or 'staging' in data else None,staging=staging if 'staging' in data else None,request_id=data.get('request_id'))
                with db:self.audit(db,user[0],'storage.change','storage','archive')
                return '202 Accepted',{'ok':True},[]
            except FileExistsError:raise APIError('409 Conflict','Zmiana katalogu już oczekuje')
            except (OSError,ValueError):raise APIError('400 Bad Request','Nieprawidłowa ścieżka lub niedostępna kolejka')
        if url=='/api/settings/storage' and method=='PATCH':
            self.admin(user);data=self.json_body(environ)
            if set(data)!={'minimum_free','free_buffer'} or any(type(data[k]) is not int or data[k]<0 for k in data) or sum(data.values())>2**63-1:
                raise APIError('400 Bad Request','Nieprawidłowe limity miejsca')
            with db:
                db.execute('BEGIN IMMEDIATE');self.current_admin(db,token)
                db.executemany('INSERT INTO system_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',[(key,str(value)) for key,value in data.items()])
                self.audit(db,user[0],'storage.update','storage','limits')
            return '200 OK',{'ok':True},[]
        if url=='/api/rotation' and method=='GET':
            self.admin(user)
            state=db.execute("SELECT state,changed_at FROM system_health WHERE name='rotation'").fetchone()
            rows=db.execute('SELECT created,state,before_bytes,after_bytes,files_count,protected_count,protected_bytes FROM rotation_history ORDER BY id DESC LIMIT 100').fetchall()
            return '200 OK',{'state':state[0] if state else 'unknown','changed_at':state[1] if state else None,
                             'history':[{'created':r[0],'state':r[1],'before':r[2],'after':r[3],'files_count':r[4],
                                         'protected_count':r[5],'protected_bytes':r[6]} for r in rows]},[]
        if url=='/api/system/health' and method=='GET':
            self.admin(user)
            system=db.execute('SELECT name,state,changed_at FROM system_health ORDER BY name').fetchall()
            services=db.execute('SELECT name,state,checked_at FROM service_health ORDER BY name').fetchall()
            now=int(time.time())
            clocks=dict(db.execute('SELECT c.id,d.clock_checked FROM cameras c JOIN camera_details d ON d.camera_id=c.id WHERE c.enabled=1 AND d.onvif_url IS NOT NULL'))
            clock_age=clock_service.max_sample_age(len(clocks))
            def system_status(row):
                match=re.fullmatch(r'clock:([1-9][0-9]*)',row[0])
                fresh=monitor.sample_fresh(clocks.get(int(match.group(1))),now,clock_age) if match else None
                return {'name':row[0],'state':row[1] if fresh is not False else 'unknown','last_state':row[1],'changed_at':row[2],'fresh':fresh}
            return '200 OK',{'system':[system_status(r) for r in system],
                             'services':[{'name':r[0],'state':r[1] if monitor.sample_fresh(r[2],now,monitor.HEALTH_MAX_AGE) else 'unknown','last_state':r[1],'checked_at':r[2],'fresh':monitor.sample_fresh(r[2],now,monitor.HEALTH_MAX_AGE)} for r in services]},[]
        if url=='/api/rotation/previews' and method=='POST':
            self.admin(user)
            if self.json_body(environ)!={}:raise APIError('400 Bad Request','Nieprawidłowe żądanie podglądu')
            try:
                jid=rotation_service.enqueue_preview(db,user[0],authorize=lambda:self.current_admin(db,token),
                                                     on_enqueue=lambda:self.audit(db,user[0],'rotation.preview','system','archive'))
                return '202 Accepted',{'id':jid},[]
            except RuntimeError:raise APIError('409 Conflict','Podgląd rotacji jest już w kolejce lub trwa')
        match=re.fullmatch(r'/api/rotation/previews/([1-9][0-9]{0,12})',url)
        if match and method=='GET':
            self.admin(user);row=db.execute('SELECT status,result_json,error FROM rotation_preview_jobs WHERE id=?',(int(match.group(1)),)).fetchone()
            if not row:raise APIError('404 Not Found','Nie znaleziono podglądu')
            return '200 OK',{'status':row[0],'result':json.loads(row[1]) if row[1] else None,'error':row[2]},[]
        if url=='/api/admin/camera-statistics' and method=='GET':
            self.admin(user)
            with db:
                db.execute('BEGIN');self.current_admin(db,token)
                settings=dict(db.execute("SELECT key,value FROM system_settings WHERE key IN ('minimum_free','free_buffer')"))
                try:
                    minimum=int(settings.get('minimum_free',self.default_minimum_free));buffer=int(settings.get('free_buffer',self.default_free_buffer))
                    if minimum<0 or buffer<0 or minimum+buffer>2**63-1:raise ValueError()
                except (ValueError,TypeError):minimum=buffer=-1
                def guard():
                    try:return workers.storage_guard(db,self.archive_root)()
                    except (OSError,RuntimeError,ValueError):return False
                return '200 OK',camera_statistics.inventory(db,self.archive_root,minimum=minimum,buffer=buffer,guard=guard),[]
        if url=='/api/admin/inventory' and method=='GET':
            # One consistent inventory instead of three requests per camera
            # and one per viewer. Never return credentials or password hashes.
            self.admin(user)
            with db:
                db.execute('BEGIN');self.current_admin(db,token)
                users=[{'id':r[0],'username':r[1],'role':r[2],'enabled':bool(r[3]),'permissions':[]}
                       for r in db.execute('SELECT id,username,role,enabled FROM users ORDER BY username')]
                by_user={u['id']:u for u in users}
                for uid,cid,download in db.execute('SELECT user_id,camera_id,may_download FROM camera_permissions ORDER BY camera_id'):
                    if uid in by_user:by_user[uid]['permissions'].append({'id':cid,'download':bool(download)})
                accounts=[{'id':r[0],'username':r[1],'enabled':bool(r[2]),'camera_id':r[3]}
                          for r in db.execute('SELECT id,username,enabled,home_camera_id FROM ftp_accounts ORDER BY username')]
                rows=db.execute('SELECT c.id,c.slug,c.name,c.enabled,d.record_mode,d.onvif_url,d.onvif_username,d.onvif_password IS NOT NULL AND d.onvif_password<>\'\',d.clock_offset,d.clock_checked,d.clock_error,d.onvif_auth FROM cameras c JOIN camera_details d ON d.camera_id=c.id ORDER BY c.name').fetchall()
                count=sum(bool(r[3]) and r[5] is not None for r in rows);now=int(time.time());max_age=clock_service.max_sample_age(count)
                cameras=[{'id':r[0],'slug':r[1],'name':r[2],'enabled':bool(r[3]),'recording':r[4],'accounts':[],
                          'onvif':{'url':r[5] or '', 'username':r[6] or '', 'password_configured':bool(r[7]),
                                   'offset':r[8],'checked':r[9],'error':r[10],'auth_mode':r[11],
                                   'fresh':monitor.sample_fresh(r[9],now,max_age)}} for r in rows]
                by_camera={c['id']:c for c in cameras}
                for cid,fid in db.execute('SELECT camera_id,ftp_account_id FROM camera_ftp_accounts ORDER BY ftp_account_id'):
                    if cid in by_camera:by_camera[cid]['accounts'].append(fid)
            return '200 OK',{'users':users,'accounts':accounts,'cameras':cameras},[]
        if url=='/api/users' and method=='GET':
            self.admin(user)
            rows=db.execute('SELECT id,username,role,enabled FROM users ORDER BY username').fetchall()
            return '200 OK',{'users':[{'id':x[0],'username':x[1],'role':x[2],'enabled':bool(x[3])} for x in rows]},[]
        if url=='/api/users' and method=='POST':
            self.admin(user)
            data=self.json_body(environ)
            try:
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    self.current_admin(db,token)
                    uid=security.create_user(db,data.get('username',''),data.get('password',''),data.get('role','viewer'))
                    self.audit(db,user[0],'user.create','user',uid)
                return '201 Created',{'id':uid},[]
            except security.PasswordValidationError as error:raise APIError('400 Bad Request',str(error))
            except (ValueError,TypeError,sqlite3.IntegrityError):raise APIError('400 Bad Request','Nieprawidłowy lub istniejący użytkownik')
        match=re.fullmatch(r'/api/users/([1-9][0-9]{0,9})/permissions',url)
        if match and method=='GET':
            self.admin(user);uid=int(match.group(1))
            if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():raise APIError('404 Not Found','Nie znaleziono użytkownika')
            rows=db.execute('SELECT camera_id,may_download FROM camera_permissions WHERE user_id=?',(uid,)).fetchall()
            return '200 OK',{'cameras':[{'id':r[0],'download':bool(r[1])} for r in rows]},[]
        if match and method=='POST':
            self.admin(user)
            uid=int(match.group(1));data=self.json_body(environ);grants=data.get('cameras')
            if not isinstance(grants,list) or len(grants)>1000:
                raise APIError('400 Bad Request','Nieprawidłowa lista uprawnień')
            try:
                normalized=[];seen=set()
                for g in grants:
                    if not isinstance(g,dict) or type(g.get('id')) is not int or g['id']<=0 or type(g.get('download')) is not bool or g['id'] in seen:
                        raise ValueError('invalid grant')
                    seen.add(g['id']);normalized.append((uid,g['id'],int(g['download'])))
                with db:
                    db.execute('BEGIN IMMEDIATE');self.current_admin(db,token)
                    if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():raise ValueError('unknown user')
                    db.execute('DELETE FROM camera_permissions WHERE user_id=?',(uid,))
                    db.executemany('INSERT INTO camera_permissions(user_id,camera_id,may_download) VALUES (?,?,?)',normalized)
                    self.audit(db,user[0],'user.permissions','user',uid)
                return '200 OK',{'ok':True},[]
            except (ValueError,sqlite3.IntegrityError):raise APIError('400 Bad Request','Nieprawidłowa lista uprawnień')
        match=re.fullmatch(r'/api/users/([1-9][0-9]{0,9})',url)
        if match and method=='PATCH':
            self.admin(user)
            uid=int(match.group(1));data=self.json_body(environ)
            if set(data)-{'enabled','role','password'} or not data:
                raise APIError('400 Bad Request','Nieprawidłowe dane użytkownika')
            try:
                if 'enabled' in data and type(data['enabled']) is not bool:raise ValueError()
                if 'role' in data and data['role'] not in ('admin','viewer'):raise ValueError()
                new_hash=security.password_hash(data['password']) if 'password' in data else None
                db.execute('BEGIN IMMEDIATE')
                self.current_admin(db,token)
                old=db.execute('SELECT role,enabled FROM users WHERE id=?',(uid,)).fetchone()
                if not old:raise ValueError()
                new_role=data.get('role',old[0]);new_enabled=data.get('enabled',bool(old[1]))
                if old[0]=='admin' and old[1] and (new_role!='admin' or not new_enabled):
                    remaining=db.execute("SELECT count(*) FROM users WHERE role='admin' AND enabled=1 AND id<>?",(uid,)).fetchone()[0]
                    if remaining==0:raise ValueError()
                db.execute('UPDATE users SET role=?,enabled=?,password_hash=COALESCE(?,password_hash) WHERE id=?',(new_role,int(new_enabled),new_hash,uid))
                if new_hash is not None or not new_enabled or new_role!=old[0]:
                    db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
                self.audit(db,user[0],'user.update','user',uid)
                db.commit()
                return '200 OK',{'ok':True},[]
            except security.PasswordValidationError as error:
                db.rollback();raise APIError('400 Bad Request',str(error))
            except (ValueError,TypeError,sqlite3.IntegrityError):
                db.rollback();raise APIError('400 Bad Request','Nieprawidłowe dane lub ostatni aktywny administrator')
        if url=='/api/ftp-accounts' and method=='GET':
            self.admin(user)
            rows=db.execute('SELECT id,username,enabled,home_camera_id FROM ftp_accounts ORDER BY username').fetchall()
            return '200 OK',{'accounts':[{'id':r[0],'username':r[1],'enabled':bool(r[2]),'camera_id':r[3]} for r in rows]},[]
        if url=='/api/ftp-accounts' and method=='POST':
            self.admin(user)
            data=self.json_body(environ)
            if set(data)-{'username','password','camera_id'}:raise APIError('400 Bad Request','Nieprawidłowe dane FTP')
            name=data.get('username');camera_id=data.get('camera_id')
            try:
                if not isinstance(name,str) or not ftp_accounts.USERNAME.fullmatch(name):raise ValueError()
                if camera_id is not None and (type(camera_id) is not int or camera_id<=0):raise ValueError()
                password_hash=ftp_accounts.make_hash(data.get('password'))
                with db:
                    db.execute('BEGIN IMMEDIATE');self.current_admin(db,token)
                    cur=db.execute('INSERT INTO ftp_accounts(username,password_hash,home_camera_id) VALUES (?,?,?)',(name,password_hash,camera_id))
                    self.audit(db,user[0],'ftp.create','ftp',cur.lastrowid)
                return '201 Created',{'id':cur.lastrowid},[]
            except security.PasswordValidationError as error:raise APIError('400 Bad Request',str(error))
            except (ValueError,sqlite3.IntegrityError):raise APIError('400 Bad Request','Nieprawidłowe lub istniejące konto FTP')
        match=re.fullmatch(r'/api/ftp-accounts/([1-9][0-9]{0,9})',url)
        if match and method=='PATCH':
            self.admin(user);fid=int(match.group(1));data=self.json_body(environ)
            if not data or set(data)-{'enabled','password','camera_id'} or ('enabled' in data and type(data['enabled']) is not bool):raise APIError('400 Bad Request','Nieprawidłowe dane FTP')
            try:
                new_hash=ftp_accounts.make_hash(data['password']) if 'password' in data else None
                # Serialize scope changes against camera/account assignments.
                db.execute('BEGIN IMMEDIATE')
                with db:
                    self.current_admin(db,token)
                    row=db.execute('SELECT enabled,home_camera_id FROM ftp_accounts WHERE id=?',(fid,)).fetchone()
                    if not row:raise ValueError()
                    camera_id=data.get('camera_id',row[1])
                    if camera_id is not None:
                        if type(camera_id) is not int or camera_id<=0 or not db.execute('SELECT 1 FROM cameras WHERE id=?',(camera_id,)).fetchone():raise ValueError()
                        if db.execute('SELECT 1 FROM camera_ftp_accounts WHERE ftp_account_id=? AND camera_id<>? LIMIT 1',(fid,camera_id)).fetchone():
                            raise APIError('409 Conflict','Usuń pozostałe przypisania konta przed ograniczeniem do jednej kamery')
                    db.execute('UPDATE ftp_accounts SET enabled=?,password_hash=COALESCE(?,password_hash),home_camera_id=? WHERE id=?',(int(data.get('enabled',bool(row[0]))),new_hash,camera_id,fid))
                    self.audit(db,user[0],'ftp.update','ftp',fid)
                return '200 OK',{'ok':True},[]
            except security.PasswordValidationError as error:raise APIError('400 Bad Request',str(error))
            except (ValueError,sqlite3.IntegrityError):raise APIError('400 Bad Request','Nieprawidłowe dane FTP')
        if match and method=='DELETE':
            self.admin(user);fid=int(match.group(1))
            try:
                with db:
                    db.execute('BEGIN IMMEDIATE');self.current_admin(db,token)
                    count=db.execute('DELETE FROM ftp_accounts WHERE id=?',(fid,)).rowcount
                    if count:self.audit(db,user[0],'ftp.delete','ftp',fid)
            except sqlite3.IntegrityError:raise APIError('409 Conflict','Konto FTP jest przypisane do kamery')
            if not count:raise APIError('404 Not Found','Nie znaleziono konta FTP')
            return '200 OK',{'ok':True},[]
        match=re.fullmatch(r'/api/cameras/([1-9][0-9]{0,9})/ftp-accounts',url)
        if match and method=='GET':
            self.admin(user);cid=int(match.group(1))
            if not db.execute('SELECT 1 FROM cameras WHERE id=?',(cid,)).fetchone():raise APIError('404 Not Found','Nie znaleziono kamery')
            rows=db.execute('SELECT ftp_account_id FROM camera_ftp_accounts WHERE camera_id=?',(cid,)).fetchall()
            return '200 OK',{'accounts':[r[0] for r in rows]},[]
        if match and method=='POST':
            self.admin(user);cid=int(match.group(1));data=self.json_body(environ);ids=data.get('accounts')
            if not isinstance(ids,list) or len(ids)>1000 or any(type(x) is not int or x<=0 for x in ids) or len(set(ids))!=len(ids):
                raise APIError('400 Bad Request','Nieprawidłowa lista kont FTP')
            try:
                with db:
                    db.execute('BEGIN IMMEDIATE');self.current_admin(db,token)
                    db.execute('DELETE FROM camera_ftp_accounts WHERE camera_id=?',(cid,))
                    if not db.execute('SELECT 1 FROM cameras WHERE id=?',(cid,)).fetchone():raise ValueError()
                    for fid in ids:
                        account=db.execute('SELECT home_camera_id FROM ftp_accounts WHERE id=?',(fid,)).fetchone()
                        if not account or (account[0] is not None and account[0]!=cid):raise ValueError()
                        db.execute('INSERT INTO camera_ftp_accounts VALUES (?,?)',(cid,fid))
                    self.audit(db,user[0],'camera.ftp','camera',cid)
                return '200 OK',{'ok':True},[]
            except (ValueError,sqlite3.IntegrityError):raise APIError('400 Bad Request','Nieprawidłowa lista kont FTP')
        if url=='/api/layouts' and method=='GET':
            rows=db.execute('SELECT id,name,camera_ids FROM saved_layouts WHERE user_id=? ORDER BY name',(user[0],)).fetchall()
            layouts=[]
            for lid,name,raw in rows:
                try:ids=json.loads(raw)
                except ValueError:continue
                if isinstance(ids,list):layouts.append({'id':lid,'name':name,'cameras':[cid for cid in ids if type(cid) is int and security.may_access(db,user,cid)]})
            return '200 OK',{'layouts':layouts},[]
        if url=='/api/layouts' and method=='POST':
            data=self.json_body(environ);name=data.get('name');ids=data.get('cameras')
            if not isinstance(name,str) or not 1<=len(name)<=80 or any(ord(x)<32 for x in name) or not isinstance(ids,list) or len(ids)>1000 or any(type(x) is not int or x<=0 for x in ids) or len(ids)!=len(set(ids)):
                raise APIError('400 Bad Request','Nieprawidłowy układ kamer')
            try:
                with db:
                    db.execute('BEGIN IMMEDIATE');user=self.current_user(db,token)
                    if any(not security.may_access(db,user,cid) for cid in ids):raise APIError('403 Forbidden','Odmowa dostępu do kamery')
                    cur=db.execute('INSERT INTO saved_layouts(user_id,name,camera_ids) VALUES (?,?,?)',(user[0],name,json.dumps(ids)))
                return '201 Created',{'id':cur.lastrowid},[]
            except sqlite3.IntegrityError:raise APIError('409 Conflict','Układ o tej nazwie już istnieje')
        match=re.fullmatch(r'/api/layouts/([1-9][0-9]{0,9})',url)
        if match and method=='DELETE':
            with db:
                db.execute('BEGIN IMMEDIATE');user=self.current_user(db,token)
                count=db.execute('DELETE FROM saved_layouts WHERE id=? AND user_id=?',(int(match.group(1)),user[0])).rowcount
            if not count:raise APIError('404 Not Found','Nie znaleziono układu')
            return '200 OK',{'ok':True},[]
        if url=='/api/audit' and method=='GET':
            self.admin(user)
            rows=db.execute('SELECT created_at,user_id,action,object_type,object_id FROM audit_log ORDER BY id DESC LIMIT 500').fetchall()
            return '200 OK',{'events':[{'timestamp':r[0],'user_id':r[1],'action':r[2],'object_type':r[3],'object_id':r[4]} for r in rows]},[]
        if url=='/api/cameras' and method=='GET':
            rows=db.execute('SELECT cameras.id,cameras.slug,cameras.name,cameras.enabled,camera_details.sub_url,camera_health.state,camera_details.last_file_at,stream_health.state,stream_health.checked_at,camera_details.record_mode,camera_health.last_checked FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id LEFT JOIN camera_health ON cameras.id=camera_health.camera_id LEFT JOIN stream_health ON cameras.id=stream_health.camera_id ORDER BY cameras.name').fetchall()
            storage=db.execute("SELECT state FROM system_health WHERE name='storage'").fetchone()
            paused=storage[0] if storage and storage[0] in ('disk_low','disk_unavailable') else None
            gateway=db.execute("SELECT state,changed_at FROM system_health WHERE name='stream_service'").fetchone()
            gateway_failed=bool(gateway and gateway[0]=='stream_service_error')
            now=int(time.time());stream_age=stream_health.max_sample_age(sum(1 for row in rows if row[3]))
            result=[{'id':r[0],'slug':r[1],'name':r[2],'may_download':security.may_access(db,user,r[0],download=True),'sub_configured':bool(r[4]),'health':(r[5] or 'unknown') if monitor.sample_fresh(r[10],now,monitor.HEALTH_MAX_AGE) else 'unknown','health_checked_at':r[10],'last_file_at':r[6],'enabled':bool(r[3]),'stream_health':('unknown' if not monitor.sample_fresh(r[8],now,stream_age) or (gateway and gateway[0]=='stream_service_ok' and r[8]<gateway[1]) else (r[7] or 'unknown')),'stream_checked_at':r[8],
                     'stream_gateway_failed':gateway_failed,'recording_pause_reason':paused if r[3] and r[9] in ('rtsp','ftp') else None} for r in rows if user[2]=='admin' or security.may_access(db,user,r[0])]
            return '200 OK',{'cameras':result},[]
        if url=='/api/cameras' and method=='POST':
            self.admin(user)
            data=self.json_body(environ)
            try:
                detected=None
                if 'ip' in data:
                    if set(data)-{'ip','name','slug','username','password','recording'}:raise ValueError()
                    ip=camera_discovery.validate_input(data['ip'],data.get('username',''),data.get('password',''))
                    detected=camera_discovery.discover_bounded(ip,data.get('username',''),data.get('password',''))
                    data={**data,'slug':data.get('slug') or 'cam-'+ip.replace('.','-'),'name':data.get('name') or 'Kamera '+ip,'main_url':detected.main,'sub_url':detected.sub,'username':'','password':''}
                camera=Camera(data.get('slug',''),data.get('name',''),data.get('main_url',''),data.get('sub_url') or None,data.get('recording','ftp'))
                username=data.get('username','');password=data.get('password','')
                if not isinstance(username,str) or not isinstance(password,str):raise ValueError()
                if username or password:
                    camera=Camera(camera.slug,camera.name,with_credentials(camera.main_url,username,password),
                                  with_credentials(camera.sub_url,username,password) if camera.sub_url else None,camera.recording)
                validate(camera)
                db.execute('BEGIN IMMEDIATE')
                self.current_admin(db,token)
                existing=db.execute('SELECT cameras.slug,camera_details.sub_url FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id').fetchall()
                aliases={alias for slug,sub in existing for alias in ([slug,slug+'_sub'] if sub else [slug])}
                new_aliases={camera.slug} | ({camera.slug+'_sub'} if camera.sub_url else set())
                if aliases & new_aliases:raise ValueError('camera aliases collide')
                cid=database.allocate_camera_id(db)
                db.execute('INSERT INTO cameras(id,slug,name) VALUES (?,?,?)',(cid,camera.slug,camera.name))
                db.execute('INSERT INTO camera_details(camera_id,main_url,sub_url,record_mode,recording_since) VALUES (?,?,?,?,?)',(cid,camera.main_url,camera.sub_url,camera.recording,int(time.time())))
                self.audit(db,user[0],'camera.create','camera',cid)
                db.commit()
                result={'id':cid}
                if detected:result.update({'discovery':detected.method,'sub_configured':bool(detected.sub)})
                return '201 Created',result,[]
            except camera_discovery.DiscoveryError as exc:
                db.rollback();raise APIError('400 Bad Request',str(exc))
            except (ValueError,sqlite3.IntegrityError):
                db.rollback()
                raise APIError('400 Bad Request','Nieprawidłowa lub istniejąca kamera')
        match=re.fullmatch(r'/api/cameras/([1-9][0-9]{0,9})/export-(preview|jobs|protect)',url)
        if match and method=='POST':
            cid=int(match.group(1));self.require_download(db,user,cid)
            data=self.json_body(environ)
            try:
                if 'start_local' in data or 'end_local' in data:
                    start=export_service.parse_local(data.get('start_local'),self.timezone,data.get('start_fold'))
                    end=export_service.parse_local(data.get('end_local'),self.timezone,data.get('end_fold'))
                else:
                    start=export_service.parse_instant(data.get('start'));end=export_service.parse_instant(data.get('end'))
                if end<=start or data.get('gaps') not in ('skip','black'):raise ValueError()
                if match.group(2)=='protect':
                    with self.shared_archive_lock():
                        planned,metadata=export_service.plan(db,cid,start,end)
                        paths=list(dict.fromkeys(str(segment.path) for segment in planned.segments))
                        for path in paths:
                            size,mtime,identity=metadata[Path(path)]
                            fd,_=open_confined(self.archive_root,path,expected_size=size,expected_mtime_ns=mtime,expected_identity=identity);os.close(fd)
                        with db:
                            db.execute('BEGIN IMMEDIATE')
                            self.require_download(db,security.get_user(db,token),cid)
                            count=sum(protection.protect(db,paths[i:i+protection.MAX_BATCH],user[0])
                                      for i in range(0,len(paths),protection.MAX_BATCH))
                            self.audit(db,user[0],'recording.protect_range','camera',cid)
                    return '200 OK',{'protected':count,'files':len(paths)},[]
                planned,_=export_service.plan(db,cid,start,end)
                if match.group(2)=='preview':
                    covered=sum(segment.duration for segment in planned.segments)
                    missing=sum((gap.end-gap.start).total_seconds() for gap in planned.gaps)
                    return '200 OK',{'segments':len(planned.segments),
                        'covered_seconds':covered,'missing_seconds':missing,
                        'output_seconds':covered+(missing if data['gaps']=='black' else 0),
                        'gaps':[{'start':g.start.isoformat(),'end':g.end.isoformat()} for g in planned.gaps]},[]
                with db:
                    db.execute('BEGIN IMMEDIATE');user=self.current_user(db,token)
                    self.require_download(db,user,cid)
                    jid=jobs.enqueue(db,user[0],cid,{'start':start.isoformat(),'end':end.isoformat(),'gaps':data['gaps']})
                return '201 Created',{'id':jid},[]
            except export_service.AmbiguousRecordingError:raise APIError('400 Bad Request','Nagranie ma niejednoznaczny czas po zmianie czasu i nie można wybrać go bezpiecznie')
            except export_service.NoRecordingsError:raise APIError('400 Bad Request','Brak nagrań w wybranym zakresie')
            except (ValueError,OSError,export_service.ExportError,MountBoundary,MountUnavailable):raise APIError('400 Bad Request','Nieprawidłowy zakres lub niedostępne nagranie')
        if url=='/api/export-jobs' and method=='GET':
            # A user can remove their own exports even after access is revoked.
            rows=db.execute('''SELECT export_jobs.id,export_jobs.camera_id,cameras.name,export_jobs.status,
                               export_jobs.progress,export_jobs.created,export_jobs.error,export_jobs.result_name,
                               EXISTS(SELECT 1 FROM export_cancel_requests cr WHERE cr.job_id=export_jobs.id)
                               FROM export_jobs LEFT JOIN cameras ON cameras.id=export_jobs.camera_id
                               WHERE export_jobs.user_id=? ORDER BY export_jobs.id DESC LIMIT 100''',(user[0],)).fetchall()
            return '200 OK',{'jobs':[{'id':r[0],'camera_id':r[1],'camera':r[2] if security.may_access(db,user,r[1],download=True) else 'Kamera niedostępna','status':r[3],
                    'progress':r[4],'created':r[5],'error':r[6],'cancel_requested':bool(r[8] and r[3]=='running'),
                    'cancelled':bool(r[3]=='failed' and r[6]==jobs.FAILURE_MESSAGES['cancelled']),
                    'download_ready':bool(r[3]=='done' and r[7] and security.may_access(db,user,r[1],download=True))}
                    for r in rows]},[]
        cancel=re.fullmatch(r'/api/export-jobs/([1-9][0-9]{0,12})/cancel',url)
        if cancel and method=='POST':
            jid=int(cancel.group(1));db.execute('BEGIN IMMEDIATE')
            try:
                user=self.current_user(db,token)
                row=db.execute('SELECT user_id FROM export_jobs WHERE id=?',(jid,)).fetchone()
                if not row:raise APIError('404 Not Found','Nie znaleziono zadania')
                if row[0]!=user[0] and user[2]!='admin':raise APIError('403 Forbidden','Odmowa dostępu')
                if not jobs.request_cancel(db,jid):raise APIError('409 Conflict','Zadanie zostało już zakończone')
                db.commit()
            except BaseException:db.rollback();raise
            return '202 Accepted',{'ok':True},[]
        match=re.fullmatch(r'/api/export-jobs/([1-9][0-9]{0,12})(/file)?',url)
        if match and method=='DELETE' and not match.group(2):
            jid=int(match.group(1));db.execute('BEGIN IMMEDIATE')
            try:
                user=self.current_user(db,token)
                row=db.execute('SELECT user_id,status,result_name FROM export_jobs WHERE id=?',(jid,)).fetchone()
                if not row:raise APIError('404 Not Found','Nie znaleziono zadania')
                if row[0]!=user[0] and user[2]!='admin':raise APIError('403 Forbidden','Odmowa dostępu')
                if row[1]=='running':raise APIError('409 Conflict','Nie można usunąć eksportu w trakcie przetwarzania')
                if row[2]:
                    try:delete_export(self.export_root,row[2],jid)
                    except (OSError,ValueError,RuntimeError):raise APIError('409 Conflict','Nie można bezpiecznie usunąć pliku eksportu')
                db.execute('DELETE FROM export_jobs WHERE id=?',(jid,));db.commit()
            except BaseException:db.rollback();raise
            return '200 OK',{'ok':True},[]
        if match and method in ('GET','HEAD'):
            jid=int(match.group(1))
            row=db.execute('''SELECT user_id,camera_id,status,progress,result_name,error,
                EXISTS(SELECT 1 FROM export_cancel_requests cr WHERE cr.job_id=export_jobs.id)
                FROM export_jobs WHERE id=?''',(jid,)).fetchone()
            if not row:raise APIError('404 Not Found','Nie znaleziono zadania')
            if row[0]!=user[0] and user[2]!='admin':raise APIError('403 Forbidden','Odmowa dostępu')
            self.require_download(db,user,row[1])
            if not match.group(2):
                if method=='HEAD':raise APIError('405 Method Not Allowed','Metoda niedozwolona')
                return '200 OK',{'id':jid,'status':row[2],'progress':row[3],'error':row[5],
                    'cancel_requested':bool(row[6] and row[2]=='running'),
                    'cancelled':bool(row[2]=='failed' and row[5]==jobs.FAILURE_MESSAGES['cancelled'])},[]
            if row[2]!='done' or not row[4]:raise APIError('404 Not Found','Eksport nie jest gotowy')
            try:fd,info=open_export(self.export_root,row[4])
            except (OSError,ValueError,MountBoundary,MountUnavailable):raise APIError('404 Not Found','Plik eksportu jest niedostępny')
            try:part=parse_range(environ.get('HTTP_RANGE'),info.st_size)
            except InvalidRange:
                os.close(fd)
                return '416 Range Not Satisfiable',b'',[('Content-Range',f'bytes */{info.st_size}')]
            status='206 Partial Content' if environ.get('HTTP_RANGE') else '200 OK'
            headers=[('Content-Type','video/mp4'),('Content-Length',str(part.length)),('Accept-Ranges','bytes'),('Content-Disposition',f'attachment; filename="sunhill-{jid}.mp4"')]
            if status.startswith('206'):headers.append(('Content-Range',f'bytes {part.start}-{part.end}/{part.total}'))
            return status,FileBody(fd,part,authorize=self.stream_authorizer(token,row[1],download=True,owner_id=row[0]),head=method=='HEAD'),headers
        match=re.fullmatch(r'/api/cameras/([1-9][0-9]{0,9})/controls',url)
        if match and method in ('GET','POST'):
            self.admin(user)
            cid=int(match.group(1))
            if not security.may_access(db,user,cid,live=True):raise APIError('404 Not Found','Kamera niedostępna')
            row=db.execute('SELECT main_url,onvif_url,onvif_username,onvif_password,onvif_auth FROM camera_details WHERE camera_id=?',(cid,)).fetchone()
            if not row:raise APIError('404 Not Found','Kamera niedostępna')
            if method=='POST':
                try:control,value=camera_control.validate_command(self.json_body(environ))
                except camera_control.ControlError as exc:raise APIError('400 Bad Request',str(exc)) from None
            try:
                with camera_control.Reolink(row[0]) as client:
                    if method=='GET':result=camera_control.capabilities(client)
                    else:
                        # Recheck authority after remote authentication; no DB write lock during network I/O.
                        self.current_admin(db,token)
                        latest=db.execute('SELECT main_url FROM camera_details WHERE camera_id=?',(cid,)).fetchone()
                        if not latest or latest[0]!=row[0] or not security.may_access(db,user,cid,live=True):raise APIError('409 Conflict','Konfiguracja kamery została zmieniona')
                        camera_control.apply(client,control,value)
                if method=='POST':
                    with db:self.audit(db,user[0],'camera.control.'+control,'camera',cid)
                    return '200 OK',{'ok':True},[]
            except camera_control.ControlError as exc:
                if method=='POST':raise APIError('422 Unprocessable Entity',str(exc)) from None
                result={'controls':{},'partial':True,'message':str(exc)}
            result=camera_control.supplement_identity(result,*row)
            self.current_admin(db,token)
            if not security.may_access(db,user,cid,live=True):raise APIError('404 Not Found','Kamera niedostępna')
            return '200 OK',result,[]
        match=re.fullmatch(r'/api/cameras/([1-9][0-9]{0,9})/onvif',url)
        if match and method in ('GET','PATCH'):
            self.admin(user);cid=int(match.group(1))
            row=db.execute('SELECT onvif_url,onvif_username,onvif_password,clock_offset,clock_checked,clock_error,onvif_auth FROM camera_details WHERE camera_id=?',(cid,)).fetchone()
            if not row:raise APIError('404 Not Found','Nie znaleziono kamery')
            if method=='GET':
                count=db.execute('SELECT count(*) FROM cameras c JOIN camera_details d ON d.camera_id=c.id WHERE c.enabled=1 AND d.onvif_url IS NOT NULL').fetchone()[0]
                fresh=monitor.sample_fresh(row[4],int(time.time()),clock_service.max_sample_age(count))
                return '200 OK',{'url':row[0] or '', 'username':row[1] or '', 'password_configured':bool(row[2]),'offset':row[3],'checked':row[4],'error':row[5],'auth_mode':row[6],'fresh':fresh},[]
            data=self.json_body(environ)
            if set(data)-{'url','username','password','auth_mode'} or not isinstance(data.get('url'),str) or not isinstance(data.get('username',''),str) or not isinstance(data.get('password',''),str):
                raise APIError('400 Bad Request','Nieprawidłowe ustawienia ONVIF')
            try:
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    self.current_admin(db,token)
                    current=db.execute('SELECT onvif_password,onvif_auth FROM camera_details WHERE camera_id=?',(cid,)).fetchone()
                    if not current:raise APIError('404 Not Found','Nie znaleziono kamery')
                    target=data['url'];username=data.get('username','');password=data.get('password') or current[0] or '';auth=data.get('auth_mode',current[1])
                    if auth not in ('digest','wsse') or len(username)>128 or len(password)>512 or any(ord(c)<32 for c in username+password) or (target and auth=='wsse' and not username):raise ValueError()
                    try:(username+password+target).encode('utf-8')
                    except UnicodeEncodeError as exc:raise ValueError() from exc
                    if target:
                        from .onvif_time import validate_endpoint
                        validate_endpoint(target)
                    db.execute('UPDATE camera_details SET onvif_url=?,onvif_username=?,onvif_password=?,onvif_auth=?,clock_offset=NULL,clock_checked=NULL,clock_error=NULL WHERE camera_id=?',(target or None,username,password,auth,cid))
                    db.execute('DELETE FROM system_health WHERE name=?',('clock:'+str(cid),))
                    if db.execute("DELETE FROM pending_notifications WHERE camera_id=? AND (origin='onvif' OR (origin='' AND state IN ('clock_unavailable','clock_ok')))",(cid,)).rowcount:
                        health_service.invalidate_delivery(db)
                    self.audit(db,user[0],'camera.onvif','camera',cid)
                return '200 OK',{'ok':True},[]
            except ValueError:raise APIError('400 Bad Request','Nieprawidłowe ustawienia ONVIF')
        match=re.fullmatch(r'/api/cameras/([1-9][0-9]{0,9})',url)
        if match and method in ('GET','PATCH','DELETE'):
            cid=int(match.group(1))
            if method=='GET':
                if user[2]!='admin' and not security.may_access(db,user,cid):raise APIError('403 Forbidden','Odmowa dostępu')
                row=db.execute('SELECT cameras.slug,cameras.name,camera_details.record_mode,camera_details.sub_url,camera_details.last_file_at,camera_details.last_error FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id WHERE cameras.id=?',(cid,)).fetchone()
                if not row:raise APIError('404 Not Found','Nie znaleziono kamery')
                return '200 OK',{'id':cid,'slug':row[0],'name':row[1],'recording':row[2],'sub_configured':bool(row[3]),'last_file_at':row[4],'last_error':row[5]},[]
            self.admin(user)
            if method=='DELETE':
                db.execute('BEGIN IMMEDIATE')
                try:
                    self.current_admin(db,token)
                    camera_row=db.execute('SELECT slug FROM cameras WHERE id=?',(cid,)).fetchone()
                    if not camera_row:raise APIError('404 Not Found','Nie znaleziono kamery')
                    if db.execute('SELECT 1 FROM ftp_accounts WHERE home_camera_id=? LIMIT 1',(cid,)).fetchone():
                        raise APIError('409 Conflict','Najpierw zmień katalog kont FTP przypisanych do tej kamery albo usuń te konta')
                    db.execute('DELETE FROM system_health WHERE name=?',('clock:'+str(cid),))
                    clear_audio_status(db,camera_row[0])
                    camera_statistics.clear(db,cid)
                    db.execute('DELETE FROM cameras WHERE id=?',(cid,))
                    health_service.invalidate_delivery(db)
                    self.audit(db,user[0],'camera.delete','camera',cid)
                    db.commit()
                except BaseException:db.rollback();raise
                return '200 OK',{'ok':True},[]
            data=self.json_body(environ)
            if set(data)-{'name','main_url','sub_url','recording','enabled','username','password','ip'} or not data:
                raise APIError('400 Bad Request','Nieprawidłowe ustawienia kamery')
            try:
                detected=None
                if 'enabled' in data and type(data['enabled']) is not bool:raise ValueError()
                if 'ip' in data:
                    if 'main_url' in data or 'sub_url' in data:raise ValueError()
                    if not db.execute('SELECT 1 FROM cameras WHERE id=?',(cid,)).fetchone():raise APIError('404 Not Found','Nie znaleziono kamery')
                    detected=camera_discovery.discover_bounded(data['ip'],data.get('username',''),data.get('password',''))
                    data={**data,'main_url':detected.main,'sub_url':detected.sub,'username':'','password':''}
                db.execute('BEGIN IMMEDIATE')
                self.current_admin(db,token)
                row=db.execute('SELECT cameras.slug,cameras.name,camera_details.main_url,camera_details.sub_url,camera_details.record_mode,cameras.enabled FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id WHERE cameras.id=?',(cid,)).fetchone()
                if not row:raise ValueError()
                camera=Camera(row[0],data.get('name',row[1]),data.get('main_url',row[2]),data.get('sub_url',row[3]) or None,data.get('recording',row[4]),data.get('enabled',bool(row[5])))
                username=data.get('username','');password=data.get('password','')
                if not isinstance(username,str) or not isinstance(password,str):raise ValueError()
                if username or password:
                    camera=Camera(camera.slug,camera.name,with_credentials(camera.main_url,username,password),
                                  with_credentials(camera.sub_url,username,password) if camera.sub_url else None,camera.recording,camera.enabled)
                validate(camera)
                existing=db.execute('SELECT cameras.slug,camera_details.sub_url FROM cameras JOIN camera_details ON cameras.id=camera_details.camera_id WHERE cameras.id<>?',(cid,)).fetchall()
                aliases={alias for slug,sub in existing for alias in ([slug,slug+'_sub'] if sub else [slug])}
                new_aliases={camera.slug}|({camera.slug+'_sub'} if camera.sub_url else set())
                if aliases & new_aliases:raise ValueError()
                db.execute('UPDATE cameras SET name=?,enabled=? WHERE id=?',(camera.name,int(camera.enabled),cid))
                if not camera.enabled or camera.recording=='none':clear_audio_status(db,camera.slug)
                if camera.main_url!=row[2] or camera.sub_url!=row[3] or camera.enabled!=bool(row[5]):
                    camera_statistics.clear(db,cid)
                if camera.main_url!=row[2] or camera.enabled!=bool(row[5]):
                    db.execute('DELETE FROM stream_health WHERE camera_id=?',(cid,))
                if camera.enabled!=bool(row[5]):
                    db.execute('DELETE FROM system_health WHERE name=?',('clock:'+str(cid),))
                    db.execute('UPDATE camera_details SET clock_offset=NULL,clock_checked=NULL,clock_error=NULL WHERE camera_id=?',(cid,))
                    if not camera.enabled and db.execute('DELETE FROM pending_notifications WHERE camera_id=?',(cid,)).rowcount:
                        health_service.invalidate_delivery(db)
                reset_clock=(camera.recording!=row[4] or (camera.enabled and not row[5]))
                db.execute('UPDATE camera_details SET main_url=?,sub_url=?,record_mode=?,recording_since=CASE WHEN ? THEN ? ELSE recording_since END WHERE camera_id=?',(camera.main_url,camera.sub_url,camera.recording,int(reset_clock),int(time.time()),cid))
                self.audit(db,user[0],'camera.update','camera',cid)
                db.commit()
                result={'ok':True}
                if detected:result.update({'discovery':detected.method,'sub_configured':bool(detected.sub)})
                return '200 OK',result,[]
            except camera_discovery.DiscoveryError as exc:
                db.rollback();raise APIError('400 Bad Request',str(exc))
            except (ValueError,TypeError,sqlite3.IntegrityError):
                db.rollback();raise APIError('400 Bad Request','Nieprawidłowe ustawienia kamery')
        match=re.fullmatch(r'/api/cameras/([1-9][0-9]{0,9})/calendar',url)
        if match and method=='GET':
            from datetime import date,datetime,timedelta
            from zoneinfo import ZoneInfo
            cid=int(match.group(1))
            if not security.may_access(db,user,cid):raise APIError('403 Forbidden','Odmowa dostępu')
            params=parse_qs(environ.get('QUERY_STRING',''),keep_blank_values=True)
            try:
                if set(params)!={'month'} or len(params['month'])!=1 or not re.fullmatch(r'\d{4}-\d{2}',params['month'][0]):raise ValueError()
                month=date.fromisoformat(params['month'][0]+'-01')
                if not 1970<=month.year<=2099:raise ValueError()
                next_month=date(month.year+1,1,1) if month.month==12 else date(month.year,month.month+1,1)
            except ValueError:raise APIError('400 Bad Request','Nieprawidłowy miesiąc')
            slug=db.execute('SELECT slug FROM cameras WHERE id=?',(cid,)).fetchone()[0]
            rows=db.execute('SELECT local_time,utc_first,utc_second,duration FROM recordings WHERE camera=? AND local_time>=? AND local_time<?',
                            (slug,(month-timedelta(days=2)).isoformat(),(next_month+timedelta(days=1)).isoformat()))
            days=set();zone=ZoneInfo(self.timezone)
            for local,first,second,duration in rows:
                try:
                    instants=[datetime.fromisoformat(x) for x in (first,second) if x]
                    intervals=[]
                    for start in instants:
                        end=start+timedelta(seconds=max(0,float(duration)-0.001))
                        intervals.append((start.astimezone(zone).date(),end.astimezone(zone).date()))
                    if not intervals:
                        local_start=datetime.fromisoformat(local)
                        intervals=[(local_start.date(),(local_start+timedelta(seconds=max(0,float(duration)-0.001))).date())]
                    for start_day,end_day in intervals:
                        day=start_day
                        while day<=end_day and day<=next_month:
                            if month<=day<next_month:days.add(day.isoformat())
                            day+=timedelta(days=1)
                except (ValueError,TypeError,OverflowError):continue
            return '200 OK',{'days':sorted(days)},[]
        match=re.fullmatch(r'/api/cameras/([1-9][0-9]{0,9})/recordings',url)
        if match and method=='GET':
            from datetime import date
            cid=int(match.group(1))
            if not security.may_access(db,user,cid):raise APIError('403 Forbidden','Odmowa dostępu')
            params=parse_qs(environ.get('QUERY_STRING',''),keep_blank_values=True)
            try:
                if set(params)-{'date','cursor'} or len(params.get('date',[]))!=1 or len(params.get('cursor',[]))>1:raise ValueError()
                day=date.fromisoformat(params['date'][0])
                if day.isoformat()!=params['date'][0] or not 1970<=day.year<=2099:raise ValueError()
                cursor=archive_cursor(params.get('cursor',[None])[0])
            except ValueError:raise APIError('400 Bad Request','Nieprawidłowa data')
            slug=db.execute('SELECT slug FROM cameras WHERE id=?',(cid,)).fetchone()[0]
            from datetime import datetime,timedelta,timezone
            from zoneinfo import ZoneInfo
            zone=ZoneInfo(self.timezone)
            first=datetime.combine(day,datetime.min.time(),tzinfo=zone).astimezone(timezone.utc)
            last=datetime.combine(day+timedelta(days=1),datetime.min.time(),tzinfo=zone).astimezone(timezone.utc)
            day_start=first.isoformat();day_end=last.isoformat()
            lower=(first-timedelta(days=1,seconds=1)).isoformat();upper=(last+timedelta(seconds=1)).isoformat()
            rows=db.execute("""WITH candidates AS (SELECT id,local_time,utc_first,utc_second,duration,
                  EXISTS(SELECT 1 FROM protected_files WHERE protected_files.relative_path=recordings.relative_path)
                  FROM recordings WHERE camera=? AND utc_first>=? AND utc_first<=?
                   AND julianday(utc_first)<julianday(?) AND julianday(utc_first)+duration/86400.0>julianday(?)
                  UNION
                  SELECT id,local_time,utc_first,utc_second,duration,
                  EXISTS(SELECT 1 FROM protected_files WHERE protected_files.relative_path=recordings.relative_path)
                  FROM recordings WHERE camera=? AND utc_second>=? AND utc_second<=?
                   AND julianday(utc_second)<julianday(?) AND julianday(utc_second)+duration/86400.0>julianday(?)
                  ) SELECT * FROM candidates WHERE (? IS NULL OR utc_first>? OR (utc_first=? AND id>?))
                  ORDER BY utc_first,id LIMIT ?""",
                  (slug,lower,upper,day_end,day_start,slug,lower,upper,day_end,day_start,
                   cursor[0] if cursor else None,cursor[0] if cursor else '',cursor[0] if cursor else '',cursor[1] if cursor else 0,ARCHIVE_PAGE_SIZE+1)).fetchall()
            more=len(rows)>ARCHIVE_PAGE_SIZE;rows=rows[:ARCHIVE_PAGE_SIZE]
            next_cursor=f'{rows[-1][2]}|{rows[-1][0]}' if more else None
            return '200 OK',{'recordings':[{'id':r[0],'local_time':r[1],'utc_first':r[2],'utc_second':r[3],'duration':r[4],'protected':bool(r[5])} for r in rows],'next_cursor':next_cursor},[]
        match=re.fullmatch(r'/api/cameras/([1-9][0-9]{0,9})/next-recording',url)
        if match and method=='GET':
            from datetime import datetime,timedelta,timezone
            cid=int(match.group(1))
            if not security.may_access(db,user,cid):raise APIError('403 Forbidden','Odmowa dostępu')
            params=parse_qs(environ.get('QUERY_STRING',''),keep_blank_values=True)
            try:
                if set(params)!={'after_ms','exclude'} or any(len(v)!=1 for v in params.values()):raise ValueError()
                after_text=params['after_ms'][0];exclude_text=params['exclude'][0]
                if not re.fullmatch(r'[0-9]{1,13}',after_text) or not re.fullmatch(r'[1-9][0-9]{0,12}',exclude_text):raise ValueError()
                after=int(after_text)/1000
                instant=datetime.fromtimestamp(after,timezone.utc)
                if not 1970<=instant.year<=2099:raise ValueError()
                lower=(instant-timedelta(days=1,seconds=2)).isoformat()
            except (ValueError,OverflowError,OSError):raise APIError('400 Bad Request','Nieprawidłowy czas lub nagranie')
            slug=db.execute('SELECT slug FROM cameras WHERE id=?',(cid,)).fetchone()[0]
            row=db.execute("""SELECT id,utc_first,utc_second FROM recordings
                WHERE camera=? AND id<>? AND utc_first>=?
                  AND CAST(strftime('%s',COALESCE(utc_second,utc_first)) AS INTEGER)+duration>?
                ORDER BY utc_first,id LIMIT 1""",(slug,int(exclude_text),lower,after+0.05)).fetchone()
            return '200 OK',{'recording':{'id':row[0],'utc_first':row[1],'utc_second':row[2]} if row else None},[]
        match=re.fullmatch(r'/api/cameras/([1-9][0-9]{0,9})/events',url)
        if match and method=='GET':
            from datetime import date,datetime,timedelta,timezone
            from zoneinfo import ZoneInfo
            cid=int(match.group(1))
            if not security.may_access(db,user,cid):raise APIError('403 Forbidden','Odmowa dostępu')
            params=parse_qs(environ.get('QUERY_STRING',''),keep_blank_values=True)
            try:
                if set(params)-{'date','cursor'} or len(params.get('date',[]))!=1 or len(params.get('cursor',[]))>1:raise ValueError()
                day=date.fromisoformat(params['date'][0]);zone=ZoneInfo(self.timezone)
                if day.isoformat()!=params['date'][0] or not 1970<=day.year<=2099:raise ValueError()
                cursor=archive_cursor(params.get('cursor',[None])[0],event=True)
                first=int(datetime.combine(day,datetime.min.time(),tzinfo=zone).timestamp())
                last=int(datetime.combine(day+timedelta(days=1),datetime.min.time(),tzinfo=zone).timestamp())
            except ValueError:raise APIError('400 Bad Request','Nieprawidłowa data')
            rows=db.execute('SELECT id,started_at,event_type FROM event_markers WHERE camera_id=? AND started_at>=? AND started_at<? AND (? IS NULL OR started_at>? OR (started_at=? AND id>?)) ORDER BY started_at,id LIMIT ?',
                            (cid,first,last,cursor[0] if cursor else None,cursor[0] if cursor else 0,cursor[0] if cursor else 0,cursor[1] if cursor else 0,ARCHIVE_PAGE_SIZE+1)).fetchall()
            more=len(rows)>ARCHIVE_PAGE_SIZE;rows=rows[:ARCHIVE_PAGE_SIZE]
            next_cursor=f'{rows[-1][1]}|{rows[-1][0]}' if more else None
            from .timeline import utc_candidates
            events=[]
            for identifier,timestamp,kind in rows:
                local=datetime.fromtimestamp(timestamp,timezone.utc).astimezone(zone)
                fold=local.fold if len(utc_candidates(local.replace(tzinfo=None),zone))==2 else None
                events.append({'id':identifier,'timestamp':timestamp,'type':kind,'fold':fold})
            return '200 OK',{'events':events,'next_cursor':next_cursor},[]
        match=re.fullmatch(r'/api/recordings/([1-9][0-9]{0,12})/frame',url)
        if match and method=='GET':
            import math
            rid=int(match.group(1));params=parse_qs(environ.get('QUERY_STRING',''),keep_blank_values=True)
            if set(params)-{'offset','download'} or len(params.get('offset',[]))!=1 or len(params.get('download',[]))>1:
                raise APIError('400 Bad Request','Nieprawidłowe parametry klatki')
            try:
                offset=float(params['offset'][0]);download=params.get('download',['0'])[0]
                if not math.isfinite(offset) or offset<0 or download not in ('0','1'):raise ValueError()
            except ValueError:raise APIError('400 Bad Request','Nieprawidłowy czas klatki')
            row=db.execute('SELECT recordings.relative_path,recordings.file_size,recordings.mtime_ns,recordings.duration,cameras.id,i.device,i.inode,i.ctime_ns FROM recordings JOIN cameras ON cameras.slug=recordings.camera LEFT JOIN recording_identity i ON i.relative_path=recordings.relative_path WHERE recordings.id=?',(rid,)).fetchone()
            if not row:raise APIError('404 Not Found','Nie znaleziono nagrania')
            if not security.may_access(db,user,row[4],download=(download=='1')):raise APIError('403 Forbidden','Odmowa dostępu')
            if offset>=row[3]:raise APIError('400 Bad Request','Czas poza nagraniem')
            try:
                fd,_=open_confined(self.archive_root,row[0],expected_size=row[1],expected_mtime_ns=row[2],expected_identity=recorded_identity(*row[5:8]))
                try:
                    acquire_shared(fd)
                    with limits.slot(self.archive_lock.parent,'thumbnail',count=4):
                        data=media.frame(fd,offset,thumbnail=(download!='1'))
                finally:os.close(fd)
            except limits.BusyError:raise APIError('429 Too Many Requests','Zbyt wiele żądań obrazu; spróbuj ponownie')
            except (OSError,ValueError,media.MediaError,MountBoundary,MountUnavailable):raise APIError('404 Not Found','Klatka jest niedostępna')
            if not security.may_access(db,security.get_user(db,token),row[4],download=(download=='1')):
                raise APIError('403 Forbidden','Odmowa dostępu')
            extra=[('Content-Disposition',f'attachment; filename="sunhill-{rid}.jpg"')] if download=='1' else []
            return '200 OK',ImageBody(data),extra
        match=re.fullmatch(r'/api/recordings/([1-9][0-9]{0,12})/protection',url)
        if match and method in ('POST','DELETE'):
            rid=int(match.group(1))
            row=db.execute('SELECT recordings.relative_path,recordings.file_size,recordings.mtime_ns,cameras.id,i.device,i.inode,i.ctime_ns FROM recordings JOIN cameras ON cameras.slug=recordings.camera LEFT JOIN recording_identity i ON i.relative_path=recordings.relative_path WHERE recordings.id=?',(rid,)).fetchone()
            if not row:raise APIError('404 Not Found','Nie znaleziono nagrania')
            self.require_download(db,user,row[3])
            with self.shared_archive_lock():
                try:
                    fd,_=open_confined(self.archive_root,row[0],expected_size=row[1],expected_mtime_ns=row[2],expected_identity=recorded_identity(*row[4:7]));os.close(fd)
                    with db:
                        db.execute('BEGIN IMMEDIATE')
                        self.require_download(db,security.get_user(db,token),row[3])
                        if method=='POST':protection.protect(db,[row[0]],user[0])
                        else:protection.unprotect(db,[row[0]])
                        self.audit(db,user[0],'recording.protect' if method=='POST' else 'recording.unprotect','recording',rid)
                except (OSError,ValueError,MountBoundary,MountUnavailable):raise APIError('409 Conflict','Nagranie jest niedostępne lub zostało zmienione')
            return '200 OK',{'protected':method=='POST'},[]
        match=re.fullmatch(r'/api/recordings/([1-9][0-9]{0,12})/media-info',url)
        if match and method=='GET':
            rid=int(match.group(1))
            if environ.get('QUERY_STRING'):raise APIError('400 Bad Request','Nieprawidłowe parametry nagrania')
            row=db.execute('SELECT recordings.relative_path,recordings.file_size,recordings.mtime_ns,cameras.id,cameras.slug,i.device,i.inode,i.ctime_ns FROM recordings JOIN cameras ON cameras.slug=recordings.camera LEFT JOIN recording_identity i ON i.relative_path=recordings.relative_path WHERE recordings.id=?',(rid,)).fetchone()
            if not row:raise APIError('404 Not Found','Nie znaleziono nagrania')
            if not security.may_access(db,user,row[3]):raise APIError('403 Forbidden','Odmowa dostępu')
            if row[0].split('/',1)[0]!=row[4]:raise APIError('404 Not Found','Nie znaleziono nagrania')
            try:
                fd,_=open_confined(self.archive_root,row[0],expected_size=row[1],expected_mtime_ns=row[2],expected_identity=recorded_identity(*row[5:8]))
                try:
                    acquire_shared(fd)
                    with limits.slot(self.archive_lock.parent,'archive-media-info',count=2):
                        info=media.probe(fd,timeout=8)
                finally:os.close(fd)
            except limits.BusyError:raise APIError('429 Too Many Requests','Diagnostyka zajęta; spróbuj ponownie')
            except (OSError,ValueError,MountBoundary,MountUnavailable):raise APIError('404 Not Found','Nagranie jest niedostępne lub zostało zmienione')
            except media.MediaError:raise APIError('422 Unprocessable Entity','FFprobe nie rozpoznał poprawnego strumienia wideo w dostępnym czasie')
            if not security.may_access(db,security.get_user(db,token),row[3]):raise APIError('403 Forbidden','Odmowa dostępu')
            streams=[]
            for item in info.get('streams',[]):
                if item.get('codec_type') in ('video','audio'):
                    streams.append({'type':item['codec_type'],'codec':str(item.get('codec_name') or 'unknown')[:64], 'tag':str(item.get('codec_tag_string') or '')[:32]})
            return '200 OK',{'streams':streams},[]
        match=re.fullmatch(r'/api/recordings/([1-9][0-9]{0,12})/file',url)
        if match and method in ('GET','HEAD'):
            rid=int(match.group(1))
            params=parse_qs(environ.get('QUERY_STRING',''),keep_blank_values=True)
            if (set(params)-{'download','compat'} or len(params.get('download',[]))>1 or params.get('download',['0'])[0] not in ('0','1')
                or ('compat' in params and params['compat']!=['hvc1']) or ('compat' in params and params.get('download')==['1'])):
                raise APIError('400 Bad Request','Nieprawidłowe parametry nagrania')
            download=params.get('download',['0'])[0]=='1'
            row=db.execute('SELECT recordings.relative_path,recordings.file_size,recordings.mtime_ns,cameras.id,cameras.slug,i.device,i.inode,i.ctime_ns FROM recordings JOIN cameras ON cameras.slug=recordings.camera LEFT JOIN recording_identity i ON i.relative_path=recordings.relative_path WHERE recordings.id=?',(rid,)).fetchone()
            if not row:raise APIError('404 Not Found','Nie znaleziono nagrania')
            if not security.may_access(db,user,row[3],download=download):raise APIError('403 Forbidden','Odmowa dostępu')
            if row[0].split('/',1)[0]!=row[4]:raise APIError('404 Not Found','Nie znaleziono nagrania')
            try:
                fd,info=open_confined(self.archive_root,row[0],expected_size=row[1],expected_mtime_ns=row[2],expected_identity=recorded_identity(*row[5:8]))
                try:acquire_shared(fd)
                except BaseException:
                    os.close(fd);raise
            except (OSError,ValueError,MountBoundary,MountUnavailable):raise APIError('404 Not Found','Nagranie jest niedostępne lub zostało zmienione')
            held_fd=None
            if 'compat' in params:
                try:
                    compatible=archive_compat.open_compatible(self.db_path.parent/'.archive-hvc1-cache',fd)
                    if compatible is not None:held_fd=fd;fd,info=compatible
                    if not security.may_access(db,security.get_user(db,token),row[3]):raise APIError('403 Forbidden','Odmowa dostępu')
                except BaseException as exc:
                    os.close(fd)
                    if held_fd is not None:os.close(held_fd)
                    if isinstance(exc,limits.BusyError):raise APIError('429 Too Many Requests','Przygotowywanie zgodnego nagrania zajęte; ponów próbę') from exc
                    if isinstance(exc,(OSError,ValueError,media.MediaError)):raise APIError('422 Unprocessable Entity','Nie udało się przygotować nagrania HEVC hvc1') from exc
                    raise
            try:part=parse_range(environ.get('HTTP_RANGE'),info.st_size)
            except InvalidRange:
                os.close(fd)
                if held_fd is not None:os.close(held_fd)
                return '416 Range Not Satisfiable',b'',[('Content-Range',f'bytes */{info.st_size}')]
            status='206 Partial Content' if environ.get('HTTP_RANGE') else '200 OK'
            headers=[('Content-Type','video/mp4'),('Content-Length',str(part.length)),('Accept-Ranges','bytes')]
            if download:headers.append(('Content-Disposition',f'attachment; filename="sunhill-{rid}.mp4"'))
            if status.startswith('206'):headers.append(('Content-Range',f'bytes {part.start}-{part.end}/{part.total}'))
            return status,FileBody(fd,part,authorize=self.stream_authorizer(token,row[3],download=download),head=method=='HEAD',held_fd=held_fd),headers
        if url=='/internal/live-auth' and method=='GET':
            # nginx supplies this header itself and strips any client-supplied copy.
            original=environ.get('HTTP_X_ORIGINAL_URI','')
            parsed=urlsplit(original)
            params=parse_qs(parsed.query,keep_blank_values=True)
            if parsed.path!='/live/api/ws' or set(params)!={'src'} or len(params['src'])!=1:
                raise APIError('403 Forbidden','Odmowa dostępu')
            alias=params['src'][0]
            from .live_proxy import authorize_alias
            allowed=authorize_alias(db,token,alias)
            if not allowed:raise APIError('403 Forbidden','Odmowa dostępu')
            return '204 No Content',b'',[]
        raise APIError('404 Not Found','Nie znaleziono')

    def client_address(self,environ):
        client=environ.get('REMOTE_ADDR','unknown')
        if self.trust_client_header and environ.get('HTTP_X_SUNHILL_CLIENT_IP'):
            try:
                if ipaddress.ip_address(client).is_loopback:
                    client=str(ipaddress.ip_address(environ['HTTP_X_SUNHILL_CLIENT_IP']))
            except (ValueError,TypeError):pass
        return client

    def stream_authorizer(self,token: str,camera_id: int, *, download: bool, owner_id: int | None=None):
        """Recheck the session and permissions while a long response is sent."""
        def authorized():
            if not workers.writes_permitted():return False
            with contextlib.closing(database.open_db(self.db_path)) as connection:
                connection.execute('BEGIN')
                user=security.get_user(connection,token)
                if not user or (owner_id is not None and user[0]!=owner_id and user[2]!='admin'):return False
                return security.may_access(connection,user,camera_id,download=download)
        return authorized

    @contextlib.contextmanager
    def shared_archive_lock(self):
        fd=os.open(self.archive_lock,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_SH)
            yield
        finally:os.close(fd)

    @staticmethod
    def require_download(db,user,camera_id):
        if not security.may_access(db,user,camera_id,download=True):
            raise APIError('403 Forbidden','Brak uprawnień do pobierania nagrań')

    @staticmethod
    def audit(db,user_id,action,object_type,object_id):
        db.execute('INSERT INTO audit_log(created_at,user_id,action,object_type,object_id) VALUES (?,?,?,?,?)',
                   (int(time.time()),user_id,action,object_type,str(object_id)))

    @staticmethod
    def admin(user):
        if user[2]!='admin':raise APIError('403 Forbidden','Wymagane uprawnienia administratora')

    @staticmethod
    def current_user(db,token):
        user=security.get_user(db,token)
        if not user:raise APIError('401 Unauthorized','Wymagane logowanie')
        return user

    @classmethod
    def current_admin(cls,db,token):
        user=cls.current_user(db,token)
        cls.admin(user)
        return user

    @staticmethod
    def json_body(environ, *, maximum=MAX_BODY):
        if environ.get('CONTENT_TYPE','').split(';',1)[0].lower()!='application/json':
            raise APIError('415 Unsupported Media Type','Wymagany format JSON')
        try:length=int(environ.get('CONTENT_LENGTH','0'))
        except ValueError:raise APIError('400 Bad Request','Nieprawidłowa długość żądania')
        if length<=0 or length>maximum:raise APIError('413 Payload Too Large','Nieprawidłowa długość żądania')
        try:
            data=json.loads(environ['wsgi.input'].read(length))
        except (UnicodeDecodeError,ValueError,KeyError):raise APIError('400 Bad Request','Nieprawidłowy JSON')
        if not isinstance(data,dict):raise APIError('400 Bad Request','Wymagany obiekt JSON')
        return data
