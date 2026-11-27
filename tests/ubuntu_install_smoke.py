"""Run only after ubuntu_install_smoke.sh provisions a disposable runner."""
import io
import json
import os
import re
import base64
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime,timedelta,timezone
from http.client import HTTPConnection,HTTPSConnection
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
    raise SystemExit('requires a disposable GitHub-hosted runner')
log=Path(sys.argv[1]).read_text(encoding='utf-8')
match=re.search(r'One-time setup code \(valid 24 hours\): ([A-Za-z0-9_-]+)',log)
if not match:raise SystemExit('installer did not produce a setup code')
token=match.group(1)
cookie='';csrf=''

def request(path, method='GET', data=None, *, expected=200):
    global cookie,csrf
    body=None if data is None else json.dumps(data).encode()
    headers={'Host':'nvr.example.com','Origin':'https://nvr.example.com'}
    if body is not None:headers['Content-Type']='application/json'
    if cookie:headers['Cookie']=cookie
    if csrf:headers['X-CSRF-Token']=csrf
    connection=HTTPConnection('127.0.0.1',8080,timeout=10)
    try:
        connection.request(method,path,body=body,headers=headers)
        response=connection.getresponse();payload=response.read()
        if response.status!=expected:raise RuntimeError(f'{method} {path} returned {response.status}, expected {expected}')
        if path=='/api/login':
            values=SimpleCookie()
            for key,value in response.getheaders():
                if key.lower()=='set-cookie':values.load(value)
            cookie='; '.join(f'{key}={item.value}' for key,item in values.items())
            csrf=values['sunhill_csrf'].value
        if response.getheader('Content-Type','').startswith('application/json'):return json.loads(payload)
        return payload
    finally:connection.close()

connection=HTTPConnection('127.0.0.1',8080,timeout=10)
try:
    connection.request('GET','/',headers={'Host':'incorrect.example.com'})
    response=connection.getresponse();response.read()
    if response.status!=421:raise RuntimeError(f'unexpected Host header accepted: {response.status}')
finally:connection.close()
connection=HTTPConnection('127.0.0.1',8080,timeout=10)
try:
    connection.request('GET','/',headers={'Host':'NVR.Example.COM'})
    response=connection.getresponse();response.read()
    if response.status!=200:raise RuntimeError(f'mixed-case Host header failed: {response.status}')
finally:connection.close()
with socket.socket() as conn:
    conn.settimeout(5)
    if conn.connect_ex(('127.0.0.1',21))==0:raise RuntimeError('Unexpected FTP listener')
request('/api/setup','POST',{'token':token,'username':'admin','password':'ciAdm6'},expected=201)
request('/api/login','POST',{'username':'admin','password':'ciAdm6'})
camera=request('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://127.0.0.1:8554/test','recording':'rtsp'},expected=201)['id']
request('/api/ftp-accounts',expected=404)
subprocess.run(['sudo','systemctl','stop','sunhill-go2rtc.service'],check=True,timeout=60)
deadline=time.monotonic()+90
while time.monotonic()<deadline:
    if subprocess.run(['systemctl','is-active','--quiet','sunhill-go2rtc.service'],timeout=5).returncode==0:break
    time.sleep(3)
else:raise RuntimeError('controller did not recover go2rtc')
def fixture_copy(source,parts,filename):
    directory=Path('/mnt/sunhill-ci/kamery').joinpath(*parts)
    subprocess.run(['sudo','-u','sunhill','mkdir','-p',str(directory)],check=True,timeout=10)
    subprocess.run(['sudo','install','-o','sunhill','-g','sunhill','-m','0640',str(source),str(directory/filename)],check=True,timeout=10)
player=request('/live/player/1/video-stream.js')
dependency=request('/live/player/1/video-rtc.js')
if b'video-rtc.js' not in player or b'VideoRTC' not in dependency:
    raise RuntimeError('live player modules are unavailable through the retry paths')
websocket=socket.create_connection(('127.0.0.1',8080),timeout=10)
try:
    key=base64.b64encode(os.urandom(16)).decode()
    request_headers=(f'GET /live/api/ws?src=brama HTTP/1.1\r\nHost: nvr.example.com\r\n'
                     f'Origin: https://nvr.example.com\r\nCookie: {cookie}\r\nUpgrade: websocket\r\n'
                     f'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n')
    websocket.sendall(request_headers.encode());response=b''
    while b'\r\n\r\n' not in response and len(response)<16384:
        chunk=websocket.recv(4096)
        if not chunk:break
        response+=chunk
    if not response.startswith(b'HTTP/1.1 101 '):raise RuntimeError('authenticated live WebSocket handshake failed')
finally:websocket.close()
with tempfile.TemporaryDirectory() as name:
    files=[]
    for color in ('blue','red'):
        file=Path(name)/(color+'.mp4');files.append(file)
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',f'color={color}:s=160x90:r=25:d=2',
                        '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(file)],check=True)
    stamp=datetime.now(ZoneInfo('Europe/Warsaw')).replace(hour=12,minute=0,second=0,microsecond=0)
    parts=['brama',stamp.strftime('%Y'),stamp.strftime('%m'),stamp.strftime('%d')]
    for offset,file in enumerate(files):fixture_copy(file,parts,(stamp+timedelta(seconds=offset*2)).strftime('%H%M%S')+'.mp4')
    expected=files[0].read_bytes()
    dedicated=request('/api/cameras','POST',{'slug':'parking','name':'Parking','main_url':'rtsp://127.0.0.1:8554/parking','recording':'rtsp'},expected=201)['id']
    deadline=time.monotonic()+150;recordings=[]
    while time.monotonic()<deadline:
        result=request('/api/cameras/'+str(camera)+'/recordings?date='+quote(stamp.date().isoformat()))
        recordings=result['recordings']
        if len(recordings)>=2:break
        time.sleep(5)
    if len(recordings)<2:raise RuntimeError('both fixture recordings were not indexed')
    actual=request('/api/recordings/'+str(recordings[0]['id'])+'/file')
    if actual!=expected:raise RuntimeError('downloaded fixture recording differs from upload')
    start=stamp.astimezone(timezone.utc);end=start+timedelta(seconds=4)
    payload={'start':start.isoformat(),'end':end.isoformat(),'gaps':'black'}
    preview=request('/api/cameras/'+str(camera)+'/export-preview','POST',payload)
    if preview['segments']!=2 or preview['gaps']:raise RuntimeError('multi-file export plan is incorrect')
    job=request('/api/cameras/'+str(camera)+'/export-jobs','POST',payload,expected=201)['id']
    deadline=time.monotonic()+150
    while time.monotonic()<deadline:
        state=request('/api/export-jobs/'+str(job))
        if state['status']=='done':break
        if state['status']=='failed':raise RuntimeError('multi-file export failed')
        time.sleep(3)
    else:raise RuntimeError('multi-file export timed out')
    output=Path(name)/'export.mp4';output.write_bytes(request('/api/export-jobs/'+str(job)+'/file'))
    probe=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(output)],check=True,stdout=subprocess.PIPE)
    info=json.loads(probe.stdout)
    if abs(float(info['format']['duration'])-4)>0.25 or not any(stream['codec_type']=='audio' for stream in info['streams']):
        raise RuntimeError('export has incorrect duration or missing audio')
    def pixel(offset):
        return subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-ss',str(offset),'-i',str(output),'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],check=True,stdout=subprocess.PIPE).stdout
    first,second=pixel(.5),pixel(2.5)
    if len(first)!=3 or len(second)!=3 or not (first[2]>first[0] and second[0]>second[2]):
        raise RuntimeError('export does not contain the expected two clips')
    request('/api/export-jobs/'+str(job),'DELETE')
    request('/api/export-jobs/'+str(job),expected=404)
    if any(item['id']==job for item in request('/api/export-jobs')['jobs']):
        raise RuntimeError('deleted export remains in the job list')
    # The shell wrapper creates this dedicated, disposable loop-mounted ext4
    # archive. Never run this deletion test against any other archive.
    storage=request('/api/settings/storage')
    archive=Path(storage['archive'])
    if archive!=Path('/mnt/sunhill-ci/kamery') or archive.is_symlink():
        raise RuntimeError('rotation smoke test requires its dedicated archive')
    mount_data=subprocess.run(['findmnt','--json','--output','SOURCE,TARGET,FSTYPE','--target',str(archive)],check=True,stdout=subprocess.PIPE,timeout=10)
    mounts=json.loads(mount_data.stdout)['filesystems']
    if len(mounts)!=1 or mounts[0]['target']!='/mnt/sunhill-ci' or mounts[0]['fstype']!='ext4' or not re.fullmatch(r'/dev/loop[0-9]+',mounts[0]['source']):
        raise RuntimeError('rotation smoke test requires its dedicated loop-mounted ext4')
    backing=subprocess.run(['sudo','losetup','--noheadings','--output','BACK-FILE',mounts[0]['source']],check=True,stdout=subprocess.PIPE,text=True,timeout=10).stdout.strip()
    if Path(backing).resolve()!=(Path(os.environ['RUNNER_TEMP'])/'sunhill-ci/disk.img').resolve():
        raise RuntimeError('rotation smoke test refused an unexpected backing file')
    # The fixture archive is deliberately inaccessible to the runner account.
    # Inspect its paths as root without relaxing its permissions.
    def root_test(option,file):
        return subprocess.run(['sudo','test',option,str(file)],timeout=10).returncode==0
    expected_names=[(stamp+timedelta(seconds=i*2)).strftime('%H%M%S')+'.mp4' for i in range(2)]
    paths=[archive/'brama'/stamp.strftime('%Y/%m/%d')/filename for filename in expected_names]
    if not all(root_test('-f',file) and not root_test('-L',file) for file in paths):raise RuntimeError('rotation fixtures missing')
    protected_record=next((item for item in recordings if item['local_time']==stamp.replace(tzinfo=None).isoformat()),None)
    if protected_record is None:raise RuntimeError('protected recording missing from index')
    subprocess.run(['sudo','systemctl','stop','sunhill-retention.service','sunhill-index.service'],check=True,timeout=30)
    try:
        request('/api/recordings/'+str(protected_record['id'])+'/protection','POST',{})
        old=time.time()-7200
        for file in paths:subprocess.run(['sudo','touch','-m','-d','@'+str(int(old)),'--',str(file)],check=True,timeout=10)
        code='import os,sys; s=os.statvfs(sys.argv[1]); print(s.f_bavail*(s.f_frsize or s.f_bsize))'
        available=int(subprocess.run(['sudo','/usr/bin/python3','-c',code,str(archive)],check=True,stdout=subprocess.PIPE,text=True,timeout=10).stdout)
        request('/api/settings/storage','PATCH',{'minimum_free':available+1_000_000_000,'free_buffer':0})
        preview_id=request('/api/rotation/previews','POST',{},expected=202)['id']
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            preview=request('/api/rotation/previews/'+str(preview_id))
            if preview['status']=='done':break
            if preview['status']=='failed':raise RuntimeError('rotation preview failed')
            time.sleep(3)
        else:raise RuntimeError('rotation preview timed out')
        protected_relative=str(paths[0].relative_to(archive));removable_relative=str(paths[1].relative_to(archive))
        if (protected_relative in preview['result']['files'] or removable_relative not in preview['result']['files']
                or preview['result']['protected_count']<1 or not all(root_test('-f',file) for file in paths)):
            raise RuntimeError('rotation preview did not preserve or classify fixtures')
        subprocess.run(['sudo','-u','sunhill','env','PYTHONPATH=/opt/sunhill-nvr/current/app',
                        '/usr/bin/python3','-m','sunhill_nvr.cli','retention-worker','--once','--apply'],check=True,timeout=180)
        history=request('/api/rotation')
        if (not root_test('-f',paths[0]) or root_test('-e',paths[1]) or not root_test('-f',archive/'keep.txt')
                or history['state']!='rotation_blocked' or not history['history'] or history['history'][0]['files_count']!=1):
            raise RuntimeError('actual rotation did not preserve protected and unrelated files or record its result')
    finally:
        try:request('/api/settings/storage','PATCH',{'minimum_free':storage['minimum_free'],'free_buffer':storage['free_buffer']})
        finally:subprocess.run(['sudo','systemctl','start','sunhill-retention.service','sunhill-index.service'],check=True,timeout=30)
# Exercise the installed health worker, systemd and a real HTTP listener. This
# is a protocol simulator; it does not replace a test with Home Assistant.
ha_original=request('/api/settings/home-assistant')
if ha_original['enabled'] or ha_original['token_configured']:
    raise RuntimeError('integration test requires a fresh Home Assistant configuration')
ha_events=[]
class FakeHA(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        payload=self.rfile.read(int(self.headers.get('Content-Length','0')))
        if self.path!='/api/events/sunhill_nvr' or self.headers.get('Authorization')!='Bearer ci-token':
            self.send_response(403);self.end_headers();return
        ha_events.append(json.loads(payload));self.send_response(200);self.end_headers()
ha_server=ThreadingHTTPServer(('127.0.0.1',0),FakeHA)
ha_thread=threading.Thread(target=lambda:ha_server.serve_forever(poll_interval=.05),daemon=True);ha_thread.start()
def health_cycle():
    subprocess.run(['sudo','-u','sunhill','env','PYTHONPATH=/opt/sunhill-nvr/current/app',
                    '/usr/bin/python3','-m','sunhill_nvr.cli','health-worker','--once'],check=True,timeout=90)
try:
    request('/api/settings/home-assistant','PATCH',{'url':'http://127.0.0.1:'+str(ha_server.server_port),'token':'ci-token','enabled':True})
    health_cycle()
    initial_count=len(ha_events)
    subprocess.run(['sudo','systemctl','stop','sunhill-index.service'],check=True,timeout=30)
    health_cycle();health_cycle()
    if not any(event.get('camera')=='sunhill-index.service' and event.get('state')=='service_down' for event in ha_events[initial_count:]):
        raise RuntimeError('service failure was not delivered to the HTTP receiver')
    down_count=len(ha_events)
    subprocess.run(['sudo','systemctl','start','sunhill-index.service'],check=True,timeout=30)
    health_cycle()
    if not any(event.get('camera')=='sunhill-index.service' and event.get('state')=='service_up' for event in ha_events[down_count:]):
        raise RuntimeError('service recovery was not delivered to the HTTP receiver')
finally:
    try:subprocess.run(['sudo','systemctl','start','sunhill-index.service'],check=True,timeout=30)
    finally:
        try:request('/api/settings/home-assistant','PATCH',{'url':ha_original['url'],'token':'','enabled':False})
        finally:ha_server.shutdown();ha_thread.join(timeout=5);ha_server.server_close()
for service in ('sunhill-clock.service','sunhill-stream-health.service'):
    subprocess.run(['sudo','systemctl','stop',service],check=True,timeout=20)
    subprocess.run(['sudo','systemctl','start',service],check=True,timeout=20)

def upload(kind,content):
    headers={'Host':'nvr.example.com','Origin':'https://nvr.example.com','Cookie':cookie,
             'X-CSRF-Token':csrf,'Content-Type':'application/octet-stream'}
    connection=HTTPConnection('127.0.0.1',8080,timeout=30)
    try:
        connection.request('POST','/api/update-uploads/'+kind,body=content,headers=headers)
        response=connection.getresponse();payload=response.read()
        if response.status!=201:raise RuntimeError('update upload failed: '+str(response.status))
        return json.loads(payload)['id']
    finally:connection.close()

def install_update(source,version,directory, *, expected_failure=False):
    import sys as _sys
    _sys.path.insert(0,str(Path('scripts').resolve()))
    from package_release import write_tar
    package=directory/('sunhill-nvr-'+version+'.tar.gz')
    files={'src/'+str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    files['src/sunhill_nvr/__init__.py']=('"""Sunhill NVR."""\n__version__ = "'+version+'"\n').encode()
    write_tar(package,files,version)
    subprocess.run(['sudo','install','-o','root','-g','root','-m','0644',str(package),'/opt/sunhill-nvr/updates/'+package.name],check=True)
    previous=Path('/opt/sunhill-nvr/current').resolve().name
    request('/api/updates','POST',{'package':package.name},expected=202)
    deadline=time.monotonic()+360
    while time.monotonic()<deadline:
        try:
            result=request('/api/updates');status=result['status']['state']
            if status in ('failed','recovery_required') and not result['pending']:
                if expected_failure and status=='failed' and Path('/opt/sunhill-nvr/current').resolve().name==previous:return
                raise RuntimeError('approved update failed: '+status)
            if status=='done' and not result['pending'] and Path('/opt/sunhill-nvr/current').resolve().name==version:
                if expected_failure:raise RuntimeError('deliberately broken update was activated')
                return
        except (ConnectionError,TimeoutError,OSError):pass
        except RuntimeError as exc:
            if 'returned 503' not in str(exc):raise
        time.sleep(3)
    raise RuntimeError('approved update did not finish: '+version)

with tempfile.TemporaryDirectory() as temporary:
    # Upgrade fixtures must be strictly newer than the installed version.
    current_version=tuple(map(int,Path('/opt/sunhill-nvr/current').resolve().name.split('.')))
    next_versions=['.'.join(map(str,(*current_version[:2],current_version[2]+n))) for n in (1,2,3)]
    directory=Path(temporary);source=directory/'source';shutil.copytree('src',source,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    units=source/'sunhill_nvr/units.py';body=units.read_text();anchor='    return units\n'
    if body.count(anchor)!=1:raise RuntimeError('cannot prepare integration-only test unit')
    extra="""    units['sunhill-ci-probe.service']='''[Unit]\nDescription=Disposable integration test\nRequires=sunhill-upgrade-recover.service\nAfter=sunhill-upgrade-recover.service\n[Service]\nUser=sunhill\nExecStart=/usr/bin/sleep infinity\n[Install]\nWantedBy=multi-user.target\n'''\n"""
    units.write_text(body.replace(anchor,extra+anchor))
    with (source/'sunhill_nvr/lifecycle.py').open('a') as file:
        file.write("\nACTIVE=ACTIVE+('sunhill-ci-probe.service',)\nENABLED=ENABLED+('sunhill-ci-probe.service',)\n")
    install_update(source,next_versions[0],directory)
    subprocess.run(['systemctl','is-active','--quiet','sunhill-ci-probe.service'],check=True)
    subprocess.run(['systemctl','is-enabled','--quiet','sunhill-ci-probe.service'],check=True)
    if not any(item['id']==camera for item in request('/api/cameras')['cameras']):raise RuntimeError('camera configuration lost during update')
    install_update(Path('src').resolve(),next_versions[1],directory)
    if Path('/etc/systemd/system/sunhill-ci-probe.service').exists():raise RuntimeError('removed unit file still exists')
    if subprocess.run(['systemctl','is-active','--quiet','sunhill-ci-probe.service']).returncode==0:
        raise RuntimeError('removed service is still running')
    # Deliberately mutate and commit the database in a signed candidate before
    # failing. A successful rollback must restore both metadata and media.
    broken=directory/'broken';shutil.copytree('src',broken,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    cli=broken/'sunhill_nvr/cli.py';body=cli.read_text()
    anchor='    """Migrate an existing database; the caller must first stop all writers."""\n'
    if body.count(anchor)!=1:raise RuntimeError('cannot prepare migration failure fixture')
    injected=("    with contextlib.closing(database.open_db(config.database)) as db:\n"
              "        with db:db.execute(\"UPDATE cameras SET name='CI rollback marker'\")\n"
              "    raise RuntimeError('intentional migration failure')\n")
    cli.write_text(body.replace(anchor,anchor+injected))
    before=request('/api/admin/inventory')
    recorded=next(item for item in request('/api/cameras/'+str(camera)+'/recordings?date='+quote(stamp.date().isoformat()))['recordings'] if item['local_time']==stamp.replace(tzinfo=None).isoformat())
    before_media=request('/api/recordings/'+str(recorded['id'])+'/file')
    install_update(broken,next_versions[2],directory,expected_failure=True)
    after=request('/api/admin/inventory')
    if [(c['id'],c['name']) for c in before['cameras']]!=[(c['id'],c['name']) for c in after['cameras']]:raise RuntimeError('rollback did not restore camera configuration')
    if before['users']!=after['users'] or before['accounts']!=after['accounts']:raise RuntimeError('rollback did not restore account configuration')
    if request('/api/recordings/'+str(recorded['id'])+'/file')!=before_media:raise RuntimeError('rollback changed recording content')

# Seven archive layouts, using synthetic fixture files (real RTSP migration is tested separately).
slugs=('brama','brama2','drzwi','parking','podworko','podworko2','skoda')
existing={item['slug']:item['id'] for item in request('/api/cameras')['cameras']}
camera_ids={}
for slug in slugs:
    cid=existing.get(slug)
    if cid is None:cid=request('/api/cameras','POST',{'slug':slug,'name':slug,'main_url':f'rtsp://127.0.0.1:8554/{slug}','recording':'rtsp'},expected=201)['id']
    camera_ids[slug]=cid

with tempfile.TemporaryDirectory() as matrix_tmp:
    fixture=Path(matrix_tmp)/'fixture.mp4'
    subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i','color=blue:s=160x90:r=10:d=1',
                    '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(fixture)],check=True,timeout=30)
    content=fixture.read_bytes();matrix_stamp=datetime.now(ZoneInfo('Europe/Warsaw')).replace(hour=13,minute=15,second=0,microsecond=0)
    dated=matrix_stamp.strftime('%Y/%m/%d').split('/')
    for slug in slugs:
        for filename in ('131500.mp4','131502.mp4'):fixture_copy(fixture,[slug,*dated],filename)
    # Confirm the archived objects through the authenticated API, including
    # exact bytes. No filename-only or directory-only success criterion.
    deadline=time.monotonic()+180;remaining=set(slugs)
    while remaining and time.monotonic()<deadline:
        for slug in tuple(remaining):
            records=request(f'/api/cameras/{camera_ids[slug]}/recordings?date={matrix_stamp.date().isoformat()}')['recordings']
            expected_times={matrix_stamp.replace(tzinfo=None).isoformat(),(matrix_stamp+timedelta(seconds=2)).replace(tzinfo=None).isoformat()}
            selected=[item for item in records if item['local_time'] in expected_times]
            if len(selected)!=2:continue
            for item in selected:
                if request(f'/api/recordings/{item["id"]}/file')!=content:raise RuntimeError('fixture camera content mismatch: '+slug)
            remaining.remove(slug)
        if remaining:time.sleep(3)
    if remaining:raise RuntimeError('camera index incomplete: '+','.join(sorted(remaining)))
print('Seven synthetic archive paths: OK',flush=True)

# A private LAN CA is explicitly generated from the console; no client skips
# certificate validation. Include both names for the subsequent domain change.
subprocess.run(['sudo','env','PYTHONPATH=/opt/sunhill-nvr/current/app','/usr/bin/python3','-m','sunhill_nvr.upgrade_admin',
                'generate-lan-tls','--next-hostname','alternate.example.com'],check=True,timeout=90)
ca=subprocess.check_output(['sudo','cat','/opt/sunhill-nvr/config/tls/current/local-ca.pem'],text=True,timeout=10)
class LocalHTTPS(HTTPSConnection):
    def connect(self):
        sock=socket.create_connection(('127.0.0.1',443),self.timeout)
        try:self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except BaseException:sock.close();raise

def lan_login(host):
    connection=LocalHTTPS(host,context=ssl.create_default_context(cadata=ca),timeout=10)
    try:
        body=json.dumps({'username':'admin','password':'ciAdm6'}).encode()
        connection.request('POST','/api/login',body=body,headers={'Host':host,'Origin':'https://'+host,'Content-Type':'application/json'})
        response=connection.getresponse();response.read()
        if response.status!=200:raise RuntimeError('LAN HTTPS login failed')
    finally:connection.close()
lan_login('nvr.example.com')
# Real HTTPS/Chrome with synthetic RTSP audio. Never skip certificate checks.
# The source serves concurrent monitor and media clients on an ephemeral port.
from test_rtsp_integration import Camera as SyntheticCamera
browser_python=os.environ['SUNHILL_BROWSER_PYTHON']
with tempfile.TemporaryDirectory() as browser_fixture:
    ca_file=Path(browser_fixture)/'ca.pem';ca_file.write_text(ca)
    nss=Path.home()/'.pki/nssdb';nss.mkdir(parents=True,exist_ok=True)
    if not (nss/'cert9.db').exists():
        subprocess.run(['certutil','-N','--empty-password','-d','sql:'+str(nss)],check=True,timeout=15)
    subprocess.run(['certutil','-A','-d','sql:'+str(nss),'-n','Sunhill disposable CI CA','-t','C,,','-i',str(ca_file)],check=True,timeout=15)
    live= SyntheticCamera(continuous=True)
    try:
        live_id=request('/api/cameras','POST',{'slug':'ci-live','name':'CI Live','main_url':f'rtsp://127.0.0.1:{live.port}/cam','recording':'none'},expected=201)['id']
        viewer=request('/api/users','POST',{'username':'ci-browser','password':'ci-browser-password-123','role':'viewer'},expected=201)['id']
        request(f'/api/users/{viewer}/permissions','POST',{'cameras':[{'id':live_id,'download':False}]})
        # Wait for the real controller to publish the source before the browser.
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            connection=HTTPConnection('127.0.0.1',1984,timeout=3)
            try:
                connection.request('GET','/api/streams');response=connection.getresponse()
                streams=json.loads(response.read()) if response.status==200 else {}
                if '~ci-live' in streams:break
            except (OSError,ValueError):pass
            finally:connection.close()
            time.sleep(2)
        else:raise RuntimeError('live fixture was not activated')
        subprocess.run([browser_python,'tests/ubuntu_browser_smoke.py','nvr.example.com',str(live_id)],check=True,timeout=420)
    finally:live.close()
# Exercise the hostname change after approved updates, including the frozen
# recovery helper used by an installation made from this release.
request('/api/settings/domain','POST',{'hostname':'alternate.example.com'},expected=202)
deadline=time.monotonic()+90
while True:
    connection=HTTPConnection('127.0.0.1',8080,timeout=5)
    try:
        connection.request('GET','/',headers={'Host':'alternate.example.com'})
        response=connection.getresponse();response.read();ready=response.status==200
    except (OSError,TimeoutError):ready=False
    finally:connection.close()
    if ready:break
    if time.monotonic()>=deadline:raise RuntimeError('new domain did not become available')
    time.sleep(2)
connection=HTTPConnection('127.0.0.1',8080,timeout=10)
try:
    body=json.dumps({'username':'admin','password':'ciAdm6'}).encode()
    connection.request('POST','/api/login',body=body,headers={'Host':'alternate.example.com','Origin':'https://alternate.example.com','Content-Type':'application/json'})
    response=connection.getresponse();response.read()
    if response.status!=200:raise RuntimeError('login through new domain failed')
finally:connection.close()
connection=HTTPConnection('127.0.0.1',8080,timeout=10)
try:
    connection.request('GET','/',headers={'Host':'nvr.example.com'})
    response=connection.getresponse();response.read()
    if response.status!=421:raise RuntimeError('old domain still served the application')
finally:connection.close()
lan_login('alternate.example.com')
connection=HTTPConnection('127.0.0.1',80,timeout=5)
try:
    connection.request('GET','/?probe=domain',headers={'Host':'127.0.0.1'})
    response=connection.getresponse();response.read()
    if response.status!=200 or response.getheader('Location') is not None:
        raise RuntimeError('Direct IPv4 access lost after domain change')
finally:connection.close()
print('Ubuntu installation, RTSP-only, archive, export, approved update, LAN HTTPS and domain change smoke test passed')
