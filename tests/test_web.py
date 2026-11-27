import io,json,sqlite3,tempfile,unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import security,database,archive,jobs
from sunhill_nvr.web import Application
class WebTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory();self.file=Path(self.temp.name)/'db.sqlite'
    db=database.open_db(self.file,create=True);security.initialize(db);database.migrate(db);archive.initialize(db);jobs.initialize(db)
    self.uid=security.create_user(db,'admin','password-for-tests','admin');db.close();self.root=Path(self.temp.name)/'archive';self.root.mkdir();self.exports=Path(self.temp.name)/'exports';self.exports.mkdir();self.app=Application(self.file,self.root,self.exports,allowed_origin='https://nvr.example',archive_lock=Path(self.temp.name)/'archive.lock')
 def tearDown(self):self.temp.cleanup()
 def call(self,url,method='GET',data=None,cookie='',csrf='',extra=None):
    body=json.dumps(data).encode() if data is not None else b''
    env={'PATH_INFO':url,'REQUEST_METHOD':method,'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(body)),
         'REMOTE_ADDR':'192.0.2.1','HTTP_ORIGIN':'https://nvr.example','HTTP_COOKIE':cookie,'HTTP_X_CSRF_TOKEN':csrf,'wsgi.input':io.BytesIO(body)}
    if extra:env.update(extra)
    response={}
    result=b''.join(self.app(env,lambda status,headers:response.update(status=status,headers=headers)))
    return response,result
 def login(self):
    r,body=self.call('/api/login','POST',{'username':'admin','password':'password-for-tests'})
    self.assertEqual(r['status'],'200 OK')
    cookie=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0]
    return cookie,json.loads(body)['csrf']
 def test_live_workspace_isolation_migration_acl_and_validation(self):
    cookie,csrf=self.login();url='/api/live-workspace'
    r,b=self.call('/api/cameras','POST',{'slug':'first','name':'First','main_url':'rtsp://192.168.1.3/main'},cookie,csrf);self.assertEqual(r['status'],'201 Created');a=json.loads(b)['id']
    r,b=self.call('/api/cameras','POST',{'slug':'second','name':'Second','main_url':'rtsp://192.168.1.4/main'},cookie,csrf);self.assertEqual(r['status'],'201 Created');b_id=json.loads(b)['id']
    db=database.open_db(self.file);uid=security.create_user(db,'workspaceviewer','password-for-viewer','viewer');db.execute('INSERT INTO camera_permissions(user_id,camera_id,may_download) VALUES (?,?,0)',(uid,a));db.commit();db.close()
    r,b=self.call('/api/login','POST',{'username':'workspaceviewer','password':'password-for-viewer'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0];vcsrf=json.loads(b)['csrf']
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1]),{'workspace':None})
    r,b=self.call('/api/layouts','POST',{'name':'Own','cameras':[a,b_id]},cookie,csrf);lid=json.loads(b)['id']
    payload={'layout_id':lid,'order':[b_id,a],'sizes':{str(a):{'span':5,'height':300}}}
    self.assertEqual(self.call(url,'POST',payload,cookie)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(url,'POST',payload,cookie,csrf)[0]['status'],'200 OK')
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1])['workspace'],payload)
    self.assertEqual(json.loads(self.call(url,cookie=viewer)[1])['workspace'],None)
    self.assertEqual(self.call(url,'POST',payload,viewer,vcsrf)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(url,'POST',{'layout_id':None,'order':[b_id],'sizes':{}},viewer,vcsrf)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(url,'POST',{'layout_id':None,'order':[a],'sizes':{str(a):{'span':13}}},viewer,vcsrf)[0]['status'],'400 Bad Request')
    valid={'layout_id':None,'order':[a],'sizes':{str(a):{'height':250}}}
    self.assertEqual(self.call(url,'POST',valid,viewer,vcsrf)[0]['status'],'200 OK')
    self.assertEqual(json.loads(self.call(url,cookie=viewer)[1])['workspace'],valid)
    db=database.open_db(self.file);db.execute('DELETE FROM camera_permissions WHERE user_id=?',(uid,));db.commit();db.close()
    self.assertEqual(json.loads(self.call(url,cookie=viewer)[1])['workspace'],{'layout_id':None,'order':[],'sizes':{}})
    self.assertEqual(self.call(f'/api/layouts/{lid}','DELETE',cookie=cookie,csrf=csrf)[0]['status'],'200 OK')
    self.assertIsNone(json.loads(self.call(url,cookie=cookie)[1])['workspace']['layout_id'])
 def test_detection_box_preference_is_personal_and_backward_compatible(self):
    cookie,csrf=self.login();url='/api/live-workspace';base={'layout_id':None,'order':[],'sizes':{}}
    self.assertEqual(self.call(url,'POST',{**base,'detection_boxes':False},cookie,csrf)[0]['status'],'200 OK')
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1])['workspace']['detection_boxes'],False)
    self.assertEqual(self.call(url,'POST',{**base,'detection_boxes':'false'},cookie,csrf)[0]['status'],'400 Bad Request')
    self.assertEqual(self.call(url,'POST',base,cookie,csrf)[0]['status'],'200 OK')
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1])['workspace']['detection_boxes'],False)
    db=database.open_db(self.file);security.create_user(db,'boxviewer','password-for-viewer','viewer');db.close()
    r,b=self.call('/api/login','POST',{'username':'boxviewer','password':'password-for-viewer'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0];vcsrf=json.loads(b)['csrf']
    self.assertIsNone(json.loads(self.call(url,cookie=viewer)[1])['workspace'])
    self.assertEqual(self.call(url,'POST',{**base,'detection_boxes':True},viewer,vcsrf)[0]['status'],'200 OK')
    self.assertEqual(json.loads(self.call(url,cookie=viewer)[1])['workspace']['detection_boxes'],True)
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1])['workspace']['detection_boxes'],False)
 def test_metadata_api_acl_settings_and_history(self):
    from sunhill_nvr import object_analytics as analytics
    import time
    cookie,csrf=self.login();r,b=self.call('/api/cameras','POST',{'slug':'metacam','name':'Metadata','main_url':'rtsp://192.168.1.40/main'},cookie,csrf);self.assertEqual(r['status'],'201 Created');cid=json.loads(b)['id'];settings=f'/api/cameras/{cid}/metadata-settings';history=f'/api/cameras/{cid}/metadata-history';now=int(time.time()*1000)
    db=database.open_db(self.file);uid=security.create_user(db,'metaviewer','password-for-viewer','viewer');db.commit();db.close()
    r,b=self.call('/api/login','POST',{'username':'metaviewer','password':'password-for-viewer'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0];vcsrf=json.loads(b)['csrf']
    cfg={'enabled':True,'retention_days':7,'rules':[]}
    self.assertEqual(self.call(settings,'PATCH',cfg,cookie)[0]['status'],'403 Forbidden');self.assertEqual(self.call(settings,'PATCH',cfg,viewer,vcsrf)[0]['status'],'403 Forbidden');self.assertEqual(self.call(settings,'PATCH',cfg,cookie,csrf)[0]['status'],'200 OK')
    db=database.open_db(self.file);analytics.snapshot(db,cid,'rtsp://192.168.1.40/main',now,[{'id':'1','kind':'person','box':[.2,.3,.1,.1]}],'session',analytics.RuleEngine(),received=now);db.close()
    self.assertEqual(json.loads(self.call('/api/metadata/live',cookie=viewer)[1]),{'cameras':{},'motion':{}});self.assertEqual(self.call(history,cookie=viewer,extra={'QUERY_STRING':f'at_ms={now}'})[0]['status'],'403 Forbidden')
    db=database.open_db(self.file);db.execute('INSERT INTO camera_permissions(user_id,camera_id,may_download) VALUES(?,?,0)',(uid,cid));db.commit();db.close()
    self.assertIn(str(cid),json.loads(self.call('/api/metadata/live',cookie=viewer)[1])['cameras']);self.assertEqual(len(json.loads(self.call(history,cookie=viewer,extra={'QUERY_STRING':f'at_ms={now}'})[1])['frames']),1)
    self.assertEqual(self.call(settings,'PATCH',{'enabled':True,'retention_days':0,'rules':[]},cookie,csrf)[0]['status'],'400 Bad Request')
    from sunhill_nvr import face_profiles
    db=database.open_db(self.file);ident=face_profiles.enroll(db,'sample profile',[[1]+[0]*127],model_id='a'*64);db.close()
    self.assertEqual(json.loads(self.call(settings,'PATCH',{'enabled':True,'rules':[],'source_mode':'local'},cookie,csrf)[1])['source_mode'],'local')
    self.assertEqual(json.loads(self.call(settings,'PATCH',{'enabled':True,'rules':[]},cookie,csrf)[1])['source_mode'],'local')
    db=database.open_db(self.file);stamp=now+1
    with self.assertRaises(InterruptedError):analytics.snapshot(db,cid,'rtsp://192.168.1.40/main',stamp,[],'old',analytics.RuleEngine(),received=stamp,expected_mode='auto')
    analytics.snapshot(db,cid,'rtsp://192.168.1.40/main',stamp,[{'id':'2','kind':'face','box':[.2,.3,.1,.1],'face_profile':ident}],'new',analytics.RuleEngine(),received=stamp,source='local',expected_mode='local');db.close()
    for path,extra in [('/api/metadata/live',{}),(history,{'QUERY_STRING':f'at_ms={stamp}'})]:
        self.assertIn('sample profile',self.call(path,cookie=cookie,extra=extra)[1].decode());body=self.call(path,cookie=viewer,extra=extra)[1].decode();self.assertNotIn('sample profile',body);self.assertNotIn(ident,body)
    db=database.open_db(self.file);face_profiles.remove(db,ident);db.close()
    self.assertNotIn('sample profile',self.call(history,cookie=cookie,extra={'QUERY_STRING':f'at_ms={stamp}'})[1].decode())

 def test_analytics_admin_inventory_and_delete(self):
    from sunhill_nvr import face_profiles
    from unittest.mock import patch
    cookie,csrf=self.login();url='/api/admin/analytics'
    db=database.open_db(self.file);ident=face_profiles.enroll(db,'test profile',[[1]+[0]*127],model_id='a'*64);security.create_user(db,'profileviewer','password-for-viewer','viewer');db.close()
    r,b=self.call('/api/login','POST',{'username':'profileviewer','password':'password-for-viewer'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0];vcsrf=json.loads(b)['csrf']
    delete=url+'/faces/'+ident
    with patch('sunhill_nvr.analytics_models.status',return_value={'coco':{'state':'missing'}}) as status:
        self.assertEqual(self.call(url)[0]['status'],'401 Unauthorized')
        self.assertEqual(self.call(url,cookie=viewer)[0]['status'],'403 Forbidden');status.assert_not_called()
        r,b=self.call(url,cookie=cookie);self.assertEqual(r['status'],'200 OK');self.assertEqual(json.loads(b),{'models':{'coco':{'state':'missing'}},'profiles':[{'id':ident,'label':'test profile','samples':1}]})
    self.assertEqual(self.call(delete,'DELETE',cookie=cookie)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(delete,'DELETE',cookie=viewer,csrf=vcsrf)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(delete,'DELETE',cookie=cookie,csrf=csrf,extra={'HTTP_ORIGIN':'https://foreign.example'})[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(delete,'DELETE',cookie=cookie,csrf=csrf)[0]['status'],'200 OK')
    self.assertEqual(self.call(delete,'DELETE',cookie=cookie,csrf=csrf)[0]['status'],'404 Not Found')
    db=database.open_db(self.file);self.assertEqual(face_profiles.inventory(db),[]);self.assertEqual(db.execute('SELECT count(*) FROM face_templates').fetchone(),(0,));self.assertEqual(db.execute("SELECT user_id FROM audit_log WHERE action='face.remove'").fetchone(),(self.uid,));db.close()
 def test_face_web_enroll_replace_lan_acl_and_rollback(self):
    import base64,contextlib
    from unittest.mock import patch
    from sunhill_nvr import face_profiles
    cookie,csrf=self.login();url='/api/admin/analytics/faces'
    self.app.trust_client_header=True
    lan={'REMOTE_ADDR':'127.0.0.1','HTTP_HOST':'192.168.0.7','HTTP_X_SUNHILL_LAN_ORIGIN':'http://192.168.0.7','HTTP_X_SUNHILL_CLIENT_IP':'192.168.0.8','HTTP_ORIGIN':'http://192.168.0.7'}
    db=database.open_db(self.file);security.create_user(db,'faceviewer','password-for-viewer','viewer');db.close()
    r,b=self.call('/api/login','POST',{'username':'faceviewer','password':'password-for-viewer'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0];vcsrf=json.loads(b)['csrf']
    sample=base64.b64encode(b'\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR'+(100).to_bytes(4,'big')*2+b'\x00'*9).decode()
    payload={'label':'Sample','consent':True,'images':[sample]};v1=[1]+[0]*127;v2=[0,1]+[0]*126
    with patch('sunhill_nvr.analytics_models.provisioning_lock',side_effect=lambda **kw:contextlib.nullcontext()) as lock,patch('sunhill_nvr.face_profiles.web_features',return_value=('a'*64,[v1])) as features:
        self.assertEqual(self.call(url,'POST',payload)[0]['status'],'401 Unauthorized')
        self.assertEqual(self.call(url,'POST',payload,cookie)[0]['status'],'403 Forbidden')
        self.assertEqual(self.call(url,'POST',payload,viewer,vcsrf)[0]['status'],'403 Forbidden')
        self.assertEqual(self.call(url,'POST',payload,cookie,csrf,extra={'HTTP_ORIGIN':'https://foreign.example'})[0]['status'],'403 Forbidden')
        self.assertEqual(self.call(url,'POST',{**payload,'consent':False},cookie,csrf)[0]['status'],'422 Unprocessable Entity')
        self.assertEqual(self.call(url,'POST',{**payload,'images':['invalid']},cookie,csrf)[0]['status'],'422 Unprocessable Entity');features.assert_not_called()
        self.assertEqual(self.call(url,'POST',payload,cookie,csrf,extra={'CONTENT_LENGTH':str(face_profiles.MAX_UPLOAD_BODY+1)})[0]['status'],'413 Payload Too Large')
        r,b=self.call(url,'POST',payload,cookie,csrf,extra=lan);self.assertEqual(r['status'],'201 Created');ident=json.loads(b)['id'];lock.assert_called_with(create=False,nonblocking=True)
        target=url+'/'+ident
        features.side_effect=ValueError('bad second photo')
        self.assertEqual(self.call(target,'PUT',{**payload,'label':'Bad','images':[sample,sample]},cookie,csrf)[0]['status'],'422 Unprocessable Entity')
        db=database.open_db(self.file);self.assertEqual(face_profiles.inventory(db),[{'id':ident,'label':'Sample','samples':1}]);self.assertEqual(face_profiles.active_templates(db,'a'*64),[(ident,v1)]);db.close()
        features.side_effect=None;features.return_value=('a'*64,[v2,v1]);r,b=self.call(target,'PUT',{**payload,'label':'Updated','images':[sample,sample]},cookie,csrf);self.assertEqual(r['status'],'200 OK');self.assertEqual(json.loads(b),{'id':ident})
        self.assertEqual(self.call(url+'/'+'b'*32,'PUT',payload,cookie,csrf)[0]['status'],'404 Not Found')
        self.assertEqual(self.call(target,'PUT',payload,viewer,vcsrf)[0]['status'],'403 Forbidden')
        features.return_value=('b'*64,[v1]);self.assertEqual(self.call(target,'PUT',payload,cookie,csrf)[0]['status'],'422 Unprocessable Entity')
    db=database.open_db(self.file);self.assertEqual(face_profiles.inventory(db),[{'id':ident,'label':'Updated','samples':2}]);self.assertEqual(face_profiles.active_templates(db,'a'*64),[(ident,v2),(ident,v1)]);self.assertEqual(db.execute("SELECT action,user_id FROM audit_log WHERE object_type='face-profile' ORDER BY id").fetchall(),[('face.enroll',self.uid),('face.update',self.uid)]);db.close()
 def test_firmware_web_requires_csrf_hash_identity_and_admin(self):
    import hashlib
    from urllib.parse import urlencode
    from unittest.mock import patch
    cookie,csrf=self.login();r,b=self.call('/api/cameras','POST',{'slug':'fwcam','name':'Firmware','main_url':'rtsp://192.168.1.40/main'},cookie,csrf);self.assertEqual(r['status'],'201 Created');cid=json.loads(b)['id'];url=f'/api/cameras/{cid}/firmware'
    identity={'Manufacturer':'Vendor','Model':'M','HardwareId':'H','FirmwareVersion':'1'};data=b'firmware';params={**{k:v for k,v in identity.items() if k!='FirmwareVersion'},'sha256':hashlib.sha256(data).hexdigest(),'confirm':'yes'}
    def extra(values):return {'CONTENT_TYPE':'application/octet-stream','CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data),'QUERY_STRING':urlencode(values)}
    with patch('sunhill_nvr.camera_firmware.identity',return_value=identity),patch('sunhill_nvr.camera_firmware.upload',return_value=identity) as upload:
        self.assertEqual(json.loads(self.call(url,cookie=cookie)[1]),identity)
        self.assertEqual(self.call(url,'POST',cookie=cookie,extra=extra(params))[0]['status'],'403 Forbidden')
        self.assertEqual(self.call(url,'POST',cookie=cookie,csrf=csrf,extra=extra({**params,'sha256':'0'*64}))[0]['status'],'422 Unprocessable Entity');upload.assert_not_called()
        self.assertEqual(self.call(url,'POST',cookie=cookie,csrf=csrf,extra=extra(params))[0]['status'],'200 OK');upload.assert_called_once();self.assertEqual(upload.call_args.args[1],data)
    db=database.open_db(self.file);security.create_user(db,'firmwareviewer','password-for-viewer','viewer');db.commit();db.close()
    r,b=self.call('/api/login','POST',{'username':'firmwareviewer','password':'password-for-viewer'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0];vcsrf=json.loads(b)['csrf']
    with patch('sunhill_nvr.camera_firmware.upload') as upload:
        self.assertEqual(self.call(url,'POST',cookie=viewer,csrf=vcsrf,extra=extra(params))[0]['status'],'403 Forbidden');upload.assert_not_called()
 def test_onvif_capabilities_requires_admin(self):
    from unittest.mock import patch
    cookie,csrf=self.login();r,b=self.call('/api/cameras','POST',{'slug':'capcam','name':'Capabilities','main_url':'rtsp://192.168.1.10/main'},cookie,csrf);self.assertEqual(r['status'],'201 Created');cid=json.loads(b)['id'];url=f'/api/cameras/{cid}/onvif-capabilities'
    db=database.open_db(self.file);uid=security.create_user(db,'capviewer','password-for-viewer','viewer');db.execute('INSERT INTO camera_permissions(user_id,camera_id,may_download) VALUES(?,?,0)',(uid,cid));db.commit();db.close()
    r,b=self.call('/api/login','POST',{'username':'capviewer','password':'password-for-viewer'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0]
    with patch('sunhill_nvr.onvif_capabilities.inspect',return_value={'media2':True,'profiles':[]}) as inspect:
        self.assertEqual(self.call(url,cookie=viewer)[0]['status'],'403 Forbidden');inspect.assert_not_called()
        self.assertEqual(self.call(url,cookie=cookie)[0]['status'],'200 OK');inspect.assert_called_once()
 def test_camera_reboot_admin_confirmation_target_and_audit(self):
    from unittest.mock import patch
    from sunhill_nvr.camera_reboot import RebootError
    cookie,csrf=self.login();r,b=self.call('/api/cameras','POST',{'slug':'rebootcam','name':'Reboot','main_url':'rtsp://u:p@192.168.1.30/main'},cookie,csrf);cid=json.loads(b)['id'];url=f'/api/cameras/{cid}/reboot'
    db=database.open_db(self.file);uid=security.create_user(db,'rebootviewer','password-for-viewer','viewer');db.execute('INSERT INTO camera_permissions(user_id,camera_id,may_download) VALUES (?,?,0)',(uid,cid));db.commit();db.close()
    r,b=self.call('/api/login','POST',{'username':'rebootviewer','password':'password-for-viewer'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0];vcsrf=json.loads(b)['csrf']
    with patch('sunhill_nvr.camera_reboot.reboot') as call:
        self.assertEqual(self.call(url,'POST',{'confirm':True},cookie,csrf)[0]['status'],'400 Bad Request')
        self.assertEqual(self.call(url,'POST',{'confirm':True},viewer,vcsrf)[0]['status'],'403 Forbidden')
        self.assertEqual(self.call(url,'POST',{'confirm':True},cookie)[0]['status'],'403 Forbidden')
        self.assertEqual(self.call(url,'POST',{'confirm':1},cookie,csrf)[0]['status'],'400 Bad Request')
        db=database.open_db(self.file);db.execute('UPDATE camera_details SET onvif_url=?,onvif_username=?,onvif_password=? WHERE camera_id=?',('http://192.168.1.31/onvif/device_service','u','p',cid));db.commit();db.close()
        self.assertEqual(self.call(url,'POST',{'confirm':True},cookie,csrf)[0]['status'],'400 Bad Request');call.assert_not_called()
        db=database.open_db(self.file);db.execute('UPDATE camera_details SET onvif_url=? WHERE camera_id=?',('http://192.168.1.30/onvif/device_service',cid));db.commit();db.close()
        self.assertEqual(self.call(url,'POST',{'confirm':True},cookie,csrf)[0]['status'],'200 OK');call.assert_called_once()
        call.side_effect=RebootError('Brak potwierdzenia');self.assertEqual(self.call(url,'POST',{'confirm':True},cookie,csrf)[0]['status'],'422 Unprocessable Entity');self.assertEqual(call.call_count,2)
    db=database.open_db(self.file);self.assertEqual([r[0] for r in db.execute("SELECT action FROM audit_log WHERE action LIKE 'camera.reboot.%' ORDER BY id")],['camera.reboot.request','camera.reboot.confirmed','camera.reboot.request','camera.reboot.unconfirmed']);db.close()
 def test_archive_index_admin_scope_csrf_and_pause(self):
    cookie,csrf=self.login();url='/api/settings/index'
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1])['state'],'idle')
    payload={'action':'start','camera':None,'start':'2026-11-13','end':'2026-11-14'}
    self.assertEqual(self.call(url,'POST',payload,cookie)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(url,'POST',{**payload,'camera':'missing'},cookie,csrf)[0]['status'],'400 Bad Request')
    r,b=self.call(url,'POST',payload,cookie,csrf);self.assertEqual(r['status'],'200 OK');self.assertEqual(json.loads(b)['state'],'queued')
    self.assertEqual(json.loads(self.call(url,'POST',{'action':'pause'},cookie,csrf)[1])['state'],'paused')
    db=database.open_db(self.file);security.create_user(db,'indexviewer','password-for-viewer','viewer');db.close()
    r,b=self.call('/api/login','POST',{'username':'indexviewer','password':'password-for-viewer'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0]
    self.assertEqual(self.call(url,cookie=viewer)[0]['status'],'403 Forbidden')
 def test_lan_origin_requires_trusted_loopback_proxy_private_addresses_and_matching_host(self):
    lan={'REMOTE_ADDR':'127.0.0.1','HTTP_HOST':'192.168.0.7','HTTP_X_SUNHILL_LAN_ORIGIN':'http://192.168.0.7','HTTP_X_SUNHILL_CLIENT_IP':'192.168.0.8','HTTP_ORIGIN':'http://192.168.0.7'}
    self.assertEqual(self.app.request_origin(lan),'https://nvr.example')
    self.app.trust_client_header=True
    self.assertEqual(self.app.request_origin(lan),'http://192.168.0.7')
    for patch in ({'REMOTE_ADDR':'192.168.0.8'},{'HTTP_HOST':'192.168.0.9'},{'HTTP_X_SUNHILL_CLIENT_IP':'8.8.8.8'},{'HTTP_HOST':'8.8.8.8','HTTP_X_SUNHILL_LAN_ORIGIN':'http://8.8.8.8'}):
        self.assertEqual(self.app.request_origin({**lan,**patch}),'https://nvr.example')
        self.assertEqual(self.call('/api/login','POST',{'username':'admin','password':'password-for-tests'},extra={**lan,**patch})[0]['status'],'403 Forbidden')
 def test_auto_camera_create_and_rediscover_without_leaking_credentials(self):
    from unittest.mock import patch
    from sunhill_nvr.camera_discovery import Streams,DiscoveryError
    cookie,csrf=self.login();payload={'ip':'192.168.0.10','username':'u','password':'secret'}
    with patch('sunhill_nvr.camera_discovery.discover_bounded',return_value=Streams('rtsp://u:secret@192.168.0.10/main','rtsp://u:secret@192.168.0.10/sub')) as discover:
        self.assertEqual(self.call('/api/cameras','POST',payload,cookie)[0]['status'],'403 Forbidden');discover.assert_not_called()
        response,body=self.call('/api/cameras','POST',payload,cookie,csrf);self.assertEqual(response['status'],'201 Created');self.assertNotIn(b'secret',body);cid=json.loads(body)['id'];self.assertTrue(json.loads(body)['sub_configured'])
    db=database.open_db(self.file);row=db.execute('SELECT slug,name FROM cameras WHERE id=?',(cid,)).fetchone();self.assertEqual(tuple(row),('cam-192-168-0-10','Kamera 192.168.0.10'));db.close()
    with patch('sunhill_nvr.camera_discovery.discover_bounded',side_effect=DiscoveryError('Nie wykryto strumienia')):
        response,body=self.call(f'/api/cameras/{cid}','PATCH',payload,cookie,csrf);self.assertEqual(response['status'],'400 Bad Request')
    db=database.open_db(self.file);self.assertTrue(db.execute('SELECT sub_url FROM camera_details WHERE camera_id=?',(cid,)).fetchone()[0]);db.close()
    with patch('sunhill_nvr.camera_discovery.discover_bounded',return_value=Streams('rtsp://192.168.0.11/new')):
        response,body=self.call(f'/api/cameras/{cid}','PATCH',{'ip':'192.168.0.11'},cookie,csrf);self.assertEqual(response['status'],'200 OK');self.assertFalse(json.loads(body)['sub_configured'])
    db=database.open_db(self.file);self.assertEqual(tuple(db.execute('SELECT main_url,sub_url FROM camera_details WHERE camera_id=?',(cid,)).fetchone()),('rtsp://192.168.0.11/new',None));db.close()
 def test_physical_camera_control_admin_csrf_and_audit(self):
    from unittest.mock import patch
    cookie,csrf=self.login()
    r,b=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://u:secret@192.168.1.30/main'},cookie,csrf)
    self.assertEqual(r['status'],'201 Created');cid=json.loads(b)['id'];url=f'/api/cameras/{cid}/controls'
    db=database.open_db(self.file);uid=security.create_user(db,'viewer','password-for-viewer','viewer');db.execute('INSERT INTO camera_permissions(user_id,camera_id,may_download) VALUES (?,?,0)',(uid,cid));db.commit();db.close()
    r,b=self.call('/api/login','POST',{'username':'viewer','password':'password-for-viewer'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0];vcsrf=json.loads(b)['csrf']
    with patch('sunhill_nvr.device_diagnosis.probe_talkback',return_value=False),patch('sunhill_nvr.camera_control.Reolink') as device,patch('sunhill_nvr.camera_control.capabilities',return_value={'controls':{'light':False},'partial':False,'device':{'model':'Lumus'}}),patch('sunhill_nvr.camera_control.apply') as apply:
        for method,body in [('GET',None),('POST',{'control':'light','value':True})]:self.assertEqual(self.call(url,method,body,viewer,vcsrf)[0]['status'],'403 Forbidden')
        self.assertEqual(self.call(url,'POST',{'control':'light','value':True},cookie)[0]['status'],'403 Forbidden');device.assert_not_called()
        self.assertEqual(self.call(url,'POST',{'control':'Reboot','value':1},cookie,csrf)[0]['status'],'400 Bad Request');device.assert_not_called()
        r,b=self.call(url,cookie=cookie);self.assertEqual(r['status'],'200 OK');self.assertNotIn(b'secret',b)
        r,b=self.call(url,'POST',{'control':'light','value':True},cookie,csrf);self.assertEqual(r['status'],'200 OK');apply.assert_called_once()
    db=database.open_db(self.file);self.assertEqual(db.execute("SELECT count(*) FROM audit_log WHERE action='camera.control.light'").fetchone()[0],1);db.close()
 def test_led_without_readback_remembers_ack_and_does_not_rediscover(self):
    from unittest.mock import patch
    from sunhill_nvr.camera_control import ControlError
    cookie,csrf=self.login();r,b=self.call('/api/cameras','POST',{'slug':'lumus','name':'Lumus','main_url':'rtsp://u:secret@192.168.1.30/main'},cookie,csrf);cid=json.loads(b)['id'];url=f'/api/cameras/{cid}/controls'
    def diagnosis(_):return {'controls':{'light_available':True},'settings':[],'talkback':True,'checked_at':100,'partial':False}
    with patch('sunhill_nvr.device_diagnosis.diagnose',side_effect=diagnosis) as inspect,patch('sunhill_nvr.camera_control.Reolink'),patch('sunhill_nvr.camera_control.apply') as apply,patch('sunhill_nvr.camera_control.read_light_state',return_value=None) as state:
        self.assertEqual(json.loads(self.call(url,cookie=cookie)[1])['controls'],{'light_available':True})
        for value in (True,False):
            self.assertEqual(self.call(url,'POST',{'control':'light','value':value},cookie,csrf)[0]['status'],'200 OK')
            result=json.loads(self.call(url,cookie=cookie,extra={'QUERY_STRING':'state=1'})[1]);self.assertIs(result['controls']['light'],value);self.assertEqual(result['controls']['light_source'],'command');self.assertTrue(result['talkback']);self.assertEqual(result['checked_at'],100)
        self.assertEqual(inspect.call_count,1);self.assertEqual(state.call_count,2)
        apply.side_effect=ControlError('Rejected')
        self.assertEqual(self.call(url,'POST',{'control':'light','value':True},cookie,csrf)[0]['status'],'422 Unprocessable Entity')
        self.assertFalse(json.loads(self.call(url,cookie=cookie)[1])['controls']['light'])
        self.assertFalse(json.loads(self.call(url,cookie=cookie,extra={'QUERY_STRING':'refresh=1'})[1])['controls']['light'])
        self.assertEqual(inspect.call_count,2)
 def test_extended_camera_settings_permission_and_whitelist(self):
    from unittest.mock import patch
    cookie,csrf=self.login();r,b=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://u:secret@192.168.1.30/main'},cookie,csrf);cid=json.loads(b)['id'];url=f'/api/cameras/{cid}/settings'
    with patch('sunhill_nvr.device_diagnosis.probe_talkback',return_value=False),patch('sunhill_nvr.camera_control.Reolink') as device,patch('sunhill_nvr.camera_control.capabilities',return_value={'controls':{},'partial':False,'device':{'model':'Lumus'}}),patch('sunhill_nvr.camera_settings.read',return_value=([{'key':'mirror','value':False}],False)),patch('sunhill_nvr.camera_settings.apply') as apply:
        self.assertEqual(self.call(url,'POST',{'control':'mirror','value':True},cookie)[0]['status'],'403 Forbidden');device.assert_not_called()
        self.assertEqual(self.call(url,'POST',{'control':'SetNetPort','value':True},cookie,csrf)[0]['status'],'400 Bad Request');device.assert_not_called()
        r,b=self.call(url,cookie=cookie);self.assertEqual(r['status'],'200 OK');self.assertEqual(json.loads(b)['settings'],[{'key':'mirror','value':False}]);self.assertNotIn(b'secret',b)
        self.assertEqual(self.call(url,'POST',{'control':'mirror','value':True},cookie,csrf)[0]['status'],'200 OK');apply.assert_called_once()
 def test_camera_identity_when_control_transport_fails(self):
    from unittest.mock import patch
    from sunhill_nvr.camera_control import ControlError
    cookie,csrf=self.login();r,b=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://u:secret@192.168.1.30/main'},cookie,csrf);cid=json.loads(b)['id'];url=f'/api/cameras/{cid}/controls'
    with patch('sunhill_nvr.device_diagnosis.probe_talkback',return_value=False),patch('sunhill_nvr.camera_control.Reolink',side_effect=ControlError('API unavailable')),patch('sunhill_nvr.camera_control.identify_onvif',return_value={'model':'Lumus','manufacturer':'Reolink','identity_source':'ONVIF'}):
        r,b=self.call(url,cookie=cookie);self.assertEqual(r['status'],'200 OK');result=json.loads(b);self.assertEqual(result['device']['model'],'Lumus');self.assertEqual(result['controls'],{});self.assertTrue(result['partial']);self.assertNotIn(b'secret',b)
        r,b=self.call(url,'POST',{'control':'light','value':True},cookie,csrf);self.assertEqual(r['status'],'422 Unprocessable Entity')
 def test_admin_password_boundaries_on_create_and_reset(self):
    cookie,csrf=self.login()
    for endpoint,username,maximum in (('/api/users','viewer',1024),):
        payload={'username':username,'password':'12345'}
        r,b=self.call(endpoint,'POST',payload,cookie,csrf)
        self.assertEqual(r['status'],'400 Bad Request');self.assertIn(f'6 do {maximum}',b.decode())
        r,b=self.call(endpoint,'POST',{**payload,'password':'123456'},cookie,csrf)
        self.assertEqual(r['status'],'201 Created');url=endpoint+'/'+str(json.loads(b)['id'])
        r,b=self.call(url,'PATCH',{'password':'abcde'},cookie,csrf)
        self.assertEqual(r['status'],'400 Bad Request');self.assertIn(f'6 do {maximum}',b.decode())
        self.assertEqual(self.call(url,'PATCH',{'password':'abcdef'},cookie,csrf)[0]['status'],'200 OK')
    self.assertEqual(self.call('/api/login','POST',{'username':'viewer','password':'abcdef'},extra={'REMOTE_ADDR':'192.0.2.4'})[0]['status'],'200 OK')
 def test_maintenance_blocks_direct_requests_but_allows_health_probe(self):
    from unittest.mock import patch
    flag=Path(self.temp.name)/'maintenance.flag';flag.touch()
    with patch('sunhill_nvr.workers.MAINTENANCE_FLAG',flag):
        for url,method in (('/api/login','POST'),('/api/cameras','GET'),('/api/integrations/events','POST'),('/internal/live-auth','GET'),('/internal/health','POST')):
            response,_=self.call(url,method)
            self.assertEqual(response['status'],'503 Service Unavailable',url)
        response,body=self.call('/internal/health');self.assertEqual(response['status'],'200 OK');self.assertEqual(json.loads(body),{'status':'ok'})
        flag.unlink();self.login()
 def test_maintenance_revokes_existing_file_authorization(self):
    from unittest.mock import patch
    cookie,csrf=self.login()
    response,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://camera'},cookie,csrf)
    self.assertEqual(response['status'],'201 Created')
    authorized=self.app.stream_authorizer(cookie.split('=',1)[1],json.loads(body)['id'],download=True)
    flag=Path(self.temp.name)/'maintenance.flag'
    with patch('sunhill_nvr.workers.MAINTENANCE_FLAG',flag):
        self.assertTrue(authorized());flag.touch();self.assertFalse(authorized())
        flag.unlink();self.assertTrue(authorized())
 def test_rejected_wsgi_headers_release_open_recording(self):
    import os
    from unittest.mock import patch
    from sunhill_nvr.files import ByteRange,FileBody
    file=self.root/'fixture.mp4';file.write_bytes(b'test')
    fd=os.open(file,os.O_RDONLY);payload=FileBody(fd,ByteRange(0,3,4))
    self.addCleanup(payload.close);calls=[]
    def response(status,headers):
        calls.append(status)
        if len(calls)==1:raise OSError('client disconnected')
    with patch.object(self.app,'dispatch',return_value=('200 OK',payload,[])):
        self.app({'REQUEST_METHOD':'GET','PATH_INFO':'/fixture'},response)
    self.assertIsNone(payload.fd)
    self.assertEqual(calls,['200 OK','500 Internal Server Error'])
 def test_login_csrf_camera_and_live_authorization(self):
    cookie,csrf=self.login()
    r,_=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://x/test'},cookie)
    self.assertEqual(r['status'],'403 Forbidden')
    r,_=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://x/test'},cookie,csrf)
    self.assertEqual(r['status'],'201 Created')
    r,body=self.call('/api/cameras',cookie=cookie);self.assertEqual(json.loads(body)['cameras'][0]['slug'],'brama')
    r,_=self.call('/internal/live-auth',cookie=cookie,extra={'HTTP_X_ORIGINAL_URI':'/live/api/ws?src=brama'})
    self.assertEqual(r['status'],'204 No Content')
    r,_=self.call('/internal/live-auth',cookie=cookie,extra={'HTTP_X_ORIGINAL_URI':'/live/api/ws?src=~brama'})
    self.assertEqual(r['status'],'204 No Content')
    r,_=self.call('/internal/live-auth',cookie=cookie,extra={'HTTP_X_ORIGINAL_URI':'/live/api/ws?src=~other'})
    self.assertEqual(r['status'],'403 Forbidden')
    r,_=self.call('/internal/live-auth',cookie=cookie,extra={'HTTP_X_ORIGINAL_URI':'/live/api/ws?src=brama&foo=bar'})
    self.assertEqual(r['status'],'403 Forbidden')
 def test_add_and_list_more_than_one_hundred_cameras_without_alias_truncation(self):
    from sunhill_nvr.cameras import go2rtc_config,Camera
    cookie,csrf=self.login()
    for i in range(120):
        slug=f'camera-{i:03d}'
        status,_=self.call('/api/cameras','POST',{'slug':slug,'name':slug,'main_url':f'rtsp://camera{i}.example/main','sub_url':f'rtsp://camera{i}.example/sub'},cookie,csrf)
        self.assertEqual(status['status'],'201 Created',slug)
    status,body=self.call('/api/cameras',cookie=cookie);self.assertEqual(status['status'],'200 OK')
    cameras=json.loads(body)['cameras'];self.assertEqual(len(cameras),120)
    self.assertEqual({c['slug'] for c in cameras},{f'camera-{i:03d}' for i in range(120)})
    aliases=json.loads(go2rtc_config([Camera(c['slug'],c['name'],'rtsp://camera.example/main','rtsp://camera.example/sub') for c in cameras]))['streams']
    self.assertEqual(len(aliases),360)
    self.assertEqual(self.call('/internal/live-auth',cookie=cookie,extra={'HTTP_X_ORIGINAL_URI':'/live/api/ws?src=camera-119_sub'})[0]['status'],'204 No Content')
 def test_login_and_logout_are_audited_without_session_secrets(self):
    cookie,csrf=self.login()
    self.assertEqual(self.call('/api/logout','POST',{},cookie,csrf)[0]['status'],'200 OK')
    self.assertEqual(self.call('/api/me',cookie=cookie)[0]['status'],'401 Unauthorized')
    db=database.open_db(self.file)
    try:
        rows=db.execute("SELECT user_id,action,object_id FROM audit_log ORDER BY id").fetchall()
        self.assertEqual(rows,[(self.uid,'session.login',str(self.uid)),(self.uid,'session.logout',str(self.uid))])
        self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
    finally:db.close()
 def test_revoked_administrator_cannot_create_camera(self):
    from unittest.mock import patch
    cookie,csrf=self.login()
    real=self.app.json_body
    for path,data in [('/api/cameras',{'slug':'cam','name':'Cam','main_url':'rtsp://x/test'})]:
        with self.subTest(path=path):
            def revoke(environ,**kwargs):
                value=real(environ,**kwargs)
                db=database.open_db(self.file)
                try:
                    with db:db.execute("UPDATE users SET role='viewer' WHERE id=?",(self.uid,))
                finally:db.close()
                return value
            with patch.object(self.app,'json_body',side_effect=revoke):
                self.assertEqual(self.call(path,'POST',data,cookie,csrf)[0]['status'],'403 Forbidden')
            db=database.open_db(self.file)
            try:
                with db:db.execute("UPDATE users SET role='admin' WHERE id=?",(self.uid,))
                self.assertEqual(db.execute('SELECT count(*) FROM cameras').fetchone()[0],0)
            finally:db.close()
 def test_origin_is_normalized_like_browser_origin(self):
    self.app=Application(self.file,self.root,self.exports,allowed_origin='https://NVR.Example:443',archive_lock=self.file.parent/'archive.lock')
    self.assertEqual(self.app.allowed_origin,'https://nvr.example')
    self.assertEqual(self.app.websocket_origin,'wss://nvr.example')
    response,_=self.call('/api/login','POST',{'username':'admin','password':'password-for-tests'})
    self.assertEqual(response['status'],'200 OK')
 def test_domain_change_requires_admin_and_csrf_and_queues_once(self):
    from unittest.mock import patch
    inbox=self.file.parent/'upgrade-inbox';inbox.mkdir(mode=0o700)
    cookie,csrf=self.login()
    response,body=self.call('/api/settings/domain',cookie=cookie)
    self.assertEqual(response['status'],'200 OK');self.assertEqual(json.loads(body)['origin'],'https://nvr.example')
    self.assertEqual(self.call('/api/settings/domain','POST',{'hostname':'new.example.com'},cookie)[0]['status'],'403 Forbidden')
    with patch('sunhill_nvr.domain_inbox.recovery_available',return_value=False):
        self.assertEqual(self.call('/api/settings/domain','POST',{'hostname':'new.example.com'},cookie,csrf)[0]['status'],'503 Service Unavailable')
    self.app.domain_status=self.file.parent/'domain-status.json'
    self.app.domain_status.write_text(json.dumps({'state':'applying','message':'changing','updated':1}))
    with patch('sunhill_nvr.domain_inbox.recovery_available',return_value=True):
        self.assertEqual(self.call('/api/settings/domain','POST',{'hostname':'new.example.com'},cookie,csrf)[0]['status'],'409 Conflict')
    self.app.domain_status.unlink()
    with patch('sunhill_nvr.domain_inbox.recovery_available',return_value=True):
        self.assertEqual(self.call('/api/settings/domain','POST',{'hostname':'nvr.example'},cookie,csrf)[0]['status'],'400 Bad Request')
        self.assertEqual(self.call('/api/settings/domain','POST',{'hostname':'bad;host'},cookie,csrf)[0]['status'],'400 Bad Request')
        self.assertEqual(self.call('/api/settings/domain','POST',{'hostname':'NEW.example.com'},cookie,csrf)[0]['status'],'202 Accepted')
    self.assertEqual(json.loads((inbox/'domain-request.json').read_text())['hostname'],'new.example.com')
    with patch('sunhill_nvr.domain_inbox.recovery_available',return_value=True):
        self.assertEqual(self.call('/api/settings/domain','POST',{'hostname':'other.example.com'},cookie,csrf)[0]['status'],'409 Conflict')
    db=database.open_db(self.file);security.create_user(db,'viewer','password-for-tests','viewer');db.close()
    response,body=self.call('/api/login','POST',{'username':'viewer','password':'password-for-tests'})
    viewer=next(v for k,v in response['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0]
    self.assertEqual(self.call('/api/settings/domain',cookie=viewer)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call('/api/settings/domain','POST',{'hostname':'third.example.com'},viewer,json.loads(body)['csrf'])[0]['status'],'403 Forbidden')
 def test_setup_requires_one_time_console_token_and_expected_origin(self):
    from sunhill_nvr.cli import initialize_setup
    from sunhill_nvr.config import parse
    root=self.file.parent
    cfg=parse({'hostname':'nvr.example.com','database':str(root/'setup.sqlite'),'archive':str(self.root),'staging':str(root/'staging'),'activity_socket':str(root/'activity.sock'),'control_state':str(root/'control'),'exports':str(self.exports),'archive_lock':str(root/'archive.lock'),'timezone':'Europe/Warsaw','minimum_free':0,'free_buffer':0})
    token=initialize_setup(cfg)
    self.app=Application(cfg.database,self.root,self.exports,allowed_origin='https://nvr.example',archive_lock=root/'archive.lock')
    self.assertTrue(json.loads(self.call('/api/setup')[1])['available'])
    request={'token':token,'username':'admin','password':'123456'}
    self.assertEqual(self.call('/api/setup','POST',request,extra={'HTTP_ORIGIN':'https://elsewhere.example'})[0]['status'],'403 Forbidden')
    self.assertEqual(self.call('/api/setup','POST',{**request,'token':'incorrect'})[0]['status'],'400 Bad Request')
    r,b=self.call('/api/setup','POST',{**request,'password':'12345'},extra={'REMOTE_ADDR':'192.0.2.3'})
    self.assertEqual(r['status'],'400 Bad Request');self.assertIn('6 do 1024',b.decode())
    self.assertEqual(self.call('/api/setup','POST',request,extra={'REMOTE_ADDR':'192.0.2.2'})[0]['status'],'201 Created')
    self.assertFalse(json.loads(self.call('/api/setup')[1])['available'])
    self.assertEqual(self.call('/api/setup','POST',request)[0]['status'],'400 Bad Request')
    self.assertEqual(self.call('/api/login','POST',{'username':'admin','password':'123456'})[0]['status'],'200 OK')
 def test_proxy_client_addresses_are_validated_and_rate_limited_separately(self):
    import time
    from unittest.mock import patch
    self.app.trust_client_header=True
    with patch('sunhill_nvr.security.time.time',return_value=time.time()):
        _,_=self.call('/api/login','POST',{'username':'admin','password':'incorrect'},extra={'REMOTE_ADDR':'127.0.0.1','HTTP_X_SUNHILL_CLIENT_IP':'192.0.2.1'})
        r,_=self.call('/api/login','POST',{'username':'admin','password':'password-for-tests'},extra={'REMOTE_ADDR':'127.0.0.1','HTTP_X_SUNHILL_CLIENT_IP':'192.0.2.2'})
        self.assertEqual(r['status'],'200 OK')
        self.call('/api/login','POST',{'username':'admin','password':'incorrect'},extra={'REMOTE_ADDR':'127.0.0.1','HTTP_X_SUNHILL_CLIENT_IP':'spoofed,header'})
        r,_=self.call('/api/login','POST',{'username':'admin','password':'password-for-tests'},extra={'REMOTE_ADDR':'127.0.0.1','HTTP_X_SUNHILL_CLIENT_IP':'other-invalid'})
        self.assertEqual(r['status'],'401 Unauthorized')
 def test_malformed_unicode_login_password_is_unauthorized(self):
    response,_=self.call('/api/login','POST',{'username':'admin','password':'x'*12+'\ud800'})
    self.assertEqual(response['status'],'401 Unauthorized')
 def test_malformed_unicode_login_username_is_unauthorized(self):
    response,_=self.call('/api/login','POST',{'username':'user\ud800','password':'password-for-tests'})
    self.assertEqual(response['status'],'401 Unauthorized')
 def test_local_time_resolution_handles_dst_ambiguity_and_gaps(self):
    cookie,csrf=self.login()
    payload={'local':'2026-10-25T02:30:00','fold':None}
    self.assertEqual(self.call('/api/time/resolve','POST',payload,cookie,csrf)[0]['status'],'400 Bad Request')
    _,first=self.call('/api/time/resolve','POST',{**payload,'fold':0},cookie,csrf)
    _,second=self.call('/api/time/resolve','POST',{**payload,'fold':1},cookie,csrf)
    from datetime import datetime
    self.assertEqual((datetime.fromisoformat(json.loads(second)['utc'])-datetime.fromisoformat(json.loads(first)['utc'])).total_seconds(),3600)
    self.assertEqual(self.call('/api/time/resolve','POST',{'local':'2026-03-29T02:30:00','fold':None},cookie,csrf)[0]['status'],'400 Bad Request')
    self.assertEqual(self.call('/api/time/resolve','POST',{**payload,'fold':True},cookie,csrf)[0]['status'],'400 Bad Request')
    self.assertEqual(self.call('/api/time/resolve','POST',payload)[0]['status'],'401 Unauthorized')
 def test_onvif_auth_mode_requires_admin_and_preserves_password(self):
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    path=f'/api/cameras/{cid}/onvif';payload={'url':'http://192.0.2.1/onvif/device_service','username':'admin','password':'secret','auth_mode':'wsse'}
    self.assertEqual(self.call(path,'PATCH',payload,cookie,csrf)[0]['status'],'200 OK')
    result=json.loads(self.call(path,cookie=cookie)[1]);self.assertEqual(result['auth_mode'],'wsse');self.assertTrue(result['password_configured']);self.assertNotIn('secret',str(result))
    self.assertEqual(self.call(path,'PATCH',{**payload,'password':'','auth_mode':'digest'},cookie,csrf)[0]['status'],'200 OK')
    db=database.open_db(self.file);self.assertEqual(db.execute('SELECT onvif_password,onvif_auth FROM camera_details WHERE camera_id=?',(cid,)).fetchone(),('secret','digest'));db.close()
    self.assertEqual(self.call(path,'PATCH',{**payload,'auth_mode':'unknown'},cookie,csrf)[0]['status'],'400 Bad Request')
 def test_onvif_edits_keep_the_latest_password_when_requests_overlap(self):
    from unittest.mock import patch
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file)
    with db:db.execute("UPDATE camera_details SET onvif_password='old',onvif_auth='digest' WHERE camera_id=?",(cid,))
    db.close();original=self.app.json_body
    def concurrent(environ,**kwargs):
        data=original(environ,**kwargs);other=database.open_db(self.file)
        with other:other.execute("UPDATE camera_details SET onvif_password='new',onvif_auth='wsse' WHERE camera_id=?",(cid,))
        other.close();return data
    with patch.object(self.app,'json_body',side_effect=concurrent):
        response,_=self.call(f'/api/cameras/{cid}/onvif','PATCH',{'url':'http://192.0.2.1/onvif/device_service','username':'admin','password':''},cookie,csrf)
    self.assertEqual(response['status'],'200 OK')
    db=database.open_db(self.file);self.assertEqual(db.execute('SELECT onvif_password,onvif_auth FROM camera_details WHERE camera_id=?',(cid,)).fetchone(),('new','wsse'));db.close()
 def test_admin_rejects_invalid_integration_ports_without_saving(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    self.assertEqual(self.call(f'/api/cameras/{cid}/onvif','PATCH',{'url':'http://host:65536/onvif/device_service','username':'admin','password':'pw'},cookie,csrf)[0]['status'],'400 Bad Request')
    self.assertEqual(self.call('/api/settings/home-assistant','PATCH',{'url':'http://host:0','token':'abc','enabled':True},cookie,csrf)[0]['status'],'400 Bad Request')
    db=database.open_db(self.file);self.assertIsNone(db.execute('SELECT onvif_url FROM camera_details WHERE camera_id=?',(cid,)).fetchone()[0]);self.assertIsNone(db.execute("SELECT value FROM system_settings WHERE key='ha_enabled'").fetchone());db.close()
 def test_disabled_home_assistant_rejects_invalid_token_type_and_control_characters(self):
    cookie,csrf=self.login()
    for token in (['bad'],123,'bad\x00token'):
        response,_=self.call('/api/settings/home-assistant','PATCH',{'url':'','token':token,'enabled':False},cookie,csrf)
        self.assertEqual(response['status'],'400 Bad Request')
    db=database.open_db(self.file);self.assertIsNone(db.execute("SELECT value FROM system_settings WHERE key='ha_token'").fetchone());db.close()
 def test_admin_revocation_during_integration_update_is_respected(self):
    from unittest.mock import patch
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id'];original=self.app.json_body
    def revoke(environ,**kwargs):
        data=original(environ,**kwargs);other=database.open_db(self.file)
        with other:other.execute("UPDATE users SET role='viewer' WHERE id=?",(self.uid,))
        other.close();return data
    for endpoint,payload in [('/api/settings/home-assistant',{'url':'http://192.0.2.1:8123','token':'secret','enabled':True}),(f'/api/cameras/{cid}/onvif',{'url':'http://192.0.2.1/onvif/device_service','username':'admin','password':'secret'})]:
        db=database.open_db(self.file)
        with db:db.execute("UPDATE users SET role='admin' WHERE id=?",(self.uid,))
        db.close()
        with patch.object(self.app,'json_body',side_effect=revoke):self.assertEqual(self.call(endpoint,'PATCH',payload,cookie,csrf)[0]['status'],'403 Forbidden')
    db=database.open_db(self.file);self.assertIsNone(db.execute("SELECT value FROM system_settings WHERE key='ha_enabled'").fetchone());self.assertIsNone(db.execute('SELECT onvif_url FROM camera_details WHERE camera_id=?',(cid,)).fetchone()[0]);db.close()
 def test_switching_home_assistant_destination_sends_current_faults(self):
    cookie,csrf=self.login();db=database.open_db(self.file)
    with db:
        db.executemany('INSERT INTO system_settings(key,value) VALUES(?,?)',[('ha_url','http://192.0.2.1:8123'),('ha_token','token'),('ha_enabled','1')])
        db.execute("INSERT INTO system_health VALUES('storage','disk_low',1)")
        db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL,'storage','disk_ok',1)")
    db.close()
    response,_=self.call('/api/settings/home-assistant','PATCH',{'url':'http://192.0.2.2:8123','token':'','enabled':True},cookie,csrf)
    self.assertEqual(response['status'],'200 OK');db=database.open_db(self.file)
    self.assertEqual(db.execute('SELECT source,state FROM pending_notifications').fetchall(),[('storage','disk_low')]);db.close()
    response,_=self.call('/api/settings/home-assistant','PATCH',{'url':'http://192.0.2.2:8123','token':'new-token','enabled':True},cookie,csrf)
    self.assertEqual(response['status'],'200 OK');db=database.open_db(self.file)
    self.assertEqual(db.execute('SELECT source,state FROM pending_notifications').fetchall(),[('storage','disk_low')]);db.close()
 def test_camera_changes_clear_obsolete_onvif_alarm(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    def set_alarm():
        db=database.open_db(self.file)
        with db:
            db.execute("INSERT OR REPLACE INTO system_health VALUES (?, 'clock_skew', 1)",('clock:'+str(cid),))
            db.execute('UPDATE camera_details SET clock_offset=90,clock_checked=1 WHERE camera_id=?',(cid,))
        db.close()
    def cleared():
        db=database.open_db(self.file);self.assertIsNone(db.execute('SELECT state FROM system_health WHERE name=?',('clock:'+str(cid),)).fetchone());db.close()
    set_alarm();self.assertEqual(self.call(f'/api/cameras/{cid}/onvif','PATCH',{'url':'','username':'','password':''},cookie,csrf)[0]['status'],'200 OK');cleared()
    set_alarm();self.assertEqual(self.call(f'/api/cameras/{cid}','PATCH',{'enabled':False},cookie,csrf)[0]['status'],'200 OK');cleared()
    set_alarm();self.assertEqual(self.call(f'/api/cameras/{cid}','DELETE',cookie=cookie,csrf=csrf)[0]['status'],'200 OK');cleared()
 def test_onvif_change_discards_only_its_queued_clock_alarm(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file)
    with db:
        db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(?,'cam','clock_skew',1)",(cid,))
        db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at,origin) VALUES(?,'cam','clock_skew',2,'onvif')",(cid,))
    db.close()
    self.assertEqual(self.call(f'/api/cameras/{cid}/onvif','PATCH',{'url':'','username':'','password':''},cookie,csrf)[0]['status'],'200 OK')
    db=database.open_db(self.file)
    self.assertEqual(db.execute('SELECT state,origin FROM pending_notifications').fetchall(),[('clock_skew','')])
    self.assertIsNotNone(db.execute("SELECT value FROM system_settings WHERE key='ha_generation'").fetchone());db.close()
 def test_disabling_camera_clears_its_audio_warning_and_queued_alerts(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x','recording':'rtsp'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file)
    with db:
        db.execute("INSERT INTO system_health VALUES('recorder_audio:cam','recording_audio_missing',1)")
        db.execute("INSERT INTO system_settings VALUES('recorder_audio_seen:cam','100')")
        db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(?,'cam','recording_audio_missing',1)",(cid,))
        db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(?,'cam','stale',1)",(cid,))
    db.close()
    self.assertEqual(self.call(f'/api/cameras/{cid}','PATCH',{'enabled':False},cookie,csrf)[0]['status'],'200 OK')
    db=database.open_db(self.file)
    self.assertIsNone(db.execute("SELECT 1 FROM system_health WHERE name='recorder_audio:cam'").fetchone())
    self.assertIsNone(db.execute("SELECT 1 FROM system_settings WHERE key='recorder_audio_seen:cam'").fetchone())
    self.assertEqual(db.execute('SELECT state FROM pending_notifications').fetchall(),[]);db.close()
 def test_malformed_camera_fields_rejected(self):
    cookie,csrf=self.login()
    r,_=self.call('/api/cameras','POST',{'slug':['wrong'],'name':'Cam','main_url':'rtsp://x/test'},cookie,csrf)
    self.assertEqual(r['status'],'400 Bad Request')
 def test_user_lifecycle_and_last_admin_guard(self):
    cookie,csrf=self.login()
    r,body=self.call('/api/users','POST',{'username':'emi','password':'other-test-password','role':'viewer'},cookie,csrf)
    self.assertEqual(r['status'],'201 Created');uid=json.loads(body)['id']
    r,_=self.call(f'/api/users/{self.uid}','PATCH',{'enabled':False},cookie,csrf)
    self.assertEqual(r['status'],'400 Bad Request')
    r,_=self.call(f'/api/users/{uid}','PATCH',{'enabled':False},cookie,csrf)
    self.assertEqual(r['status'],'200 OK')
 def test_separate_accounts_isolate_layouts_and_revoke_sessions_on_role_change(self):
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    sessions=[]
    for username in ('viewer1','viewer2'):
        _,body=self.call('/api/users','POST',{'username':username,'password':'viewer-test-password','role':'viewer'},cookie,csrf);uid=json.loads(body)['id']
        self.assertEqual(self.call(f'/api/users/{uid}/permissions','POST',{'cameras':[{'id':cid,'download':False}]},cookie,csrf)[0]['status'],'200 OK')
        db=database.open_db(self.file)
        try:session=security.authenticate(db,username,'viewer-test-password',username)
        finally:db.close()
        sessions.append((uid,'sunhill_session='+session.token,session.csrf))
    first,second=sessions
    r,body=self.call('/api/layouts','POST',{'name':'Prywatny','cameras':[cid]},first[1],first[2]);self.assertEqual(r['status'],'201 Created');lid=json.loads(body)['id']
    self.assertEqual(json.loads(self.call('/api/layouts',cookie=second[1])[1])['layouts'],[])
    self.assertEqual(self.call(f'/api/layouts/{lid}','DELETE',cookie=second[1],csrf=second[2])[0]['status'],'404 Not Found')
    self.assertEqual(len(json.loads(self.call('/api/layouts',cookie=first[1])[1])['layouts']),1)
    self.assertEqual(self.call('/api/users',cookie=second[1])[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(f'/api/users/{first[0]}','PATCH',{'role':'admin'},cookie,csrf)[0]['status'],'200 OK')
    self.assertEqual(self.call('/api/me',cookie=first[1])[0]['status'],'401 Unauthorized')
    self.assertEqual(self.call('/api/me',cookie=second[1])[0]['status'],'200 OK')
 def test_revoked_admin_cannot_change_accounts_or_grants(self):
    from unittest.mock import patch
    cookie,csrf=self.login()
    db=database.open_db(self.file)
    try:uid=security.create_user(db,'viewer','viewer-test-password','viewer')
    finally:db.close()
    real=self.app.json_body
    def revoke(environ,**kwargs):
        value=real(environ,**kwargs)
        db=database.open_db(self.file)
        try:
            with db:db.execute('DELETE FROM sessions WHERE user_id=?',(self.uid,))
        finally:db.close()
        return value
    with patch.object(self.app,'json_body',side_effect=revoke):
        self.assertEqual(self.call(f'/api/users/{uid}','PATCH',{'role':'admin'},cookie,csrf)[0]['status'],'401 Unauthorized')
    db=database.open_db(self.file)
    try:self.assertEqual(db.execute('SELECT role FROM users WHERE id=?',(uid,)).fetchone()[0],'viewer')
    finally:db.close()
 def test_deleted_camera_identity_is_never_reused_for_old_export(self):
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'old','name':'Stara','main_url':'rtsp://x'},cookie,csrf);old=json.loads(body)['id']
    db=database.open_db(self.file);jid=jobs.enqueue(db,self.uid,old,{});db.close()
    self.assertEqual(self.call(f'/api/cameras/{old}','DELETE',cookie=cookie,csrf=csrf)[0]['status'],'200 OK')
    _,body=self.call('/api/cameras','POST',{'slug':'new','name':'Nowa','main_url':'rtsp://x'},cookie,csrf);new=json.loads(body)['id']
    self.assertGreater(new,old)
    self.assertEqual(self.call(f'/api/export-jobs/{jid}',cookie=cookie)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(f'/api/export-jobs/{jid}','DELETE',cookie=cookie,csrf=csrf)[0]['status'],'200 OK')
    self.assertEqual(self.call(f'/api/cameras/{new}','DELETE',cookie=cookie,csrf=csrf)[0]['status'],'200 OK')
    _,body=self.call('/api/cameras','POST',{'slug':'third','name':'Trzecia','main_url':'rtsp://x'},cookie,csrf)
    self.assertGreater(json.loads(body)['id'],new)
 def test_permission_grants_reject_unrecognised_camera(self):
    cookie,csrf=self.login()
    r,body=self.call('/api/users','POST',{'username':'emi','password':'other-test-password','role':'viewer'},cookie,csrf)
    uid=json.loads(body)['id']
    r,_=self.call(f'/api/users/{uid}/permissions','POST',{'cameras':[{'id':999,'download':True}]},cookie,csrf)
    self.assertEqual(r['status'],'400 Bad Request')
 def test_camera_list_reports_recording_pause_without_exposing_storage_details(self):
    cookie,csrf=self.login()
    self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x','recording':'rtsp'},cookie,csrf)
    self.call('/api/cameras','POST',{'slug':'live','name':'Live','main_url':'rtsp://x','recording':'none'},cookie,csrf)
    db=database.open_db(self.file)
    for state,expected in [('disk_low','disk_low'),('disk_unavailable','disk_unavailable'),('disk_ok',None)]:
        with db:db.execute("INSERT OR REPLACE INTO system_health(name,state,changed_at) VALUES('storage',?,1)",(state,))
        cameras={item['slug']:item for item in json.loads(self.call('/api/cameras',cookie=cookie)[1])['cameras']}
        self.assertEqual(cameras['cam']['recording_pause_reason'],expected)
        self.assertIsNone(cameras['live']['recording_pause_reason'])
    db.close()
 def test_archive_browse_and_authorized_range(self):
    cookie,csrf=self.login()
    r,body=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://x/test'},cookie,csrf)
    cid=json.loads(body)['id'];file=self.root/'brama/2026/11/21/190900.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'0123456789');st=file.stat()
    db=sqlite3.connect(str(self.file));db.execute("INSERT INTO recordings(relative_path,camera,local_time,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?)",('brama/2026/11/21/190900.mp4','brama','2026-11-21T19:09:00',10,10,st.st_mtime_ns,1));db.execute("UPDATE recordings SET utc_first='2026-11-21T18:09:00+00:00' WHERE utc_first IS NULL");db.commit();db.close()
    r,body=self.call(f'/api/cameras/{cid}/recordings',cookie=cookie,extra={'QUERY_STRING':'date=2026-11-21'})
    self.assertEqual(r['status'],'200 OK');rid=json.loads(body)['recordings'][0]['id']
    r,body=self.call(f'/api/recordings/{rid}/file',cookie=cookie,extra={'HTTP_RANGE':'bytes=2-4'})
    self.assertEqual(r['status'],'206 Partial Content');self.assertEqual(body,b'234')
    r,body=self.call(f'/api/recordings/{rid}/file','HEAD',cookie=cookie,extra={'HTTP_RANGE':'bytes=2-4'})
    self.assertEqual(r['status'],'206 Partial Content');self.assertEqual(body,b'')
    self.assertEqual(dict(r['headers'])['Content-Range'],'bytes 2-4/10');self.assertEqual(dict(r['headers'])['Content-Length'],'3')
    r,body=self.call(f'/api/recordings/{rid}/file','HEAD',cookie=cookie)
    self.assertEqual(r['status'],'200 OK');self.assertEqual(body,b'');self.assertEqual(dict(r['headers'])['Content-Length'],'10')
    self.assertEqual(self.call(f'/api/recordings/{rid}/file')[0]['status'],'401 Unauthorized')
    response,empty=self.call(f'/api/recordings/{rid}/file','HEAD')
    self.assertEqual(response['status'],'401 Unauthorized');self.assertEqual(empty,b'')
 def test_indexed_file_replacement_is_rejected_until_reindexed(self):
    import os
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://x/test'},cookie,csrf)
    cid=json.loads(body)['id'];file=self.root/'brama/2026/11/21/190900.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'0123456789')
    db=database.open_db(self.file)
    args=dict(inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True,min_age_seconds=0)
    archive.refresh(db,self.root,'Europe/Warsaw',**args)
    rid=db.execute('SELECT id FROM recordings').fetchone()[0];before=file.stat()
    self.assertEqual(self.call(f'/api/recordings/{rid}/file',cookie=cookie)[0]['status'],'200 OK')
    replacement=file.with_name('replacement.tmp');replacement.write_bytes(b'abcdefghij')
    os.utime(replacement,ns=(before.st_atime_ns,before.st_mtime_ns));os.replace(replacement,file)
    self.assertEqual(self.call(f'/api/recordings/{rid}/file',cookie=cookie)[0]['status'],'404 Not Found')
    self.assertEqual(self.call(f'/api/recordings/{rid}/frame',cookie=cookie,extra={'QUERY_STRING':'offset=0'})[0]['status'],'404 Not Found')
    self.assertEqual(self.call(f'/api/recordings/{rid}/protection','POST',{},cookie,csrf)[0]['status'],'409 Conflict')
    archive.refresh(db,self.root,'Europe/Warsaw',**args);db.close()
    response,payload=self.call(f'/api/recordings/{rid}/file',cookie=cookie)
    self.assertEqual(response['status'],'200 OK');self.assertEqual(payload,b'abcdefghij')
    from unittest.mock import patch
    from sunhill_nvr import fd_mount
    with patch('sunhill_nvr.files.fd_mount.require_same',side_effect=fd_mount.MountBoundary()):
        self.assertEqual(self.call(f'/api/recordings/{rid}/file',cookie=cookie)[0]['status'],'404 Not Found')
        self.assertEqual(self.call(f'/api/recordings/{rid}/protection','POST',{},cookie,csrf)[0]['status'],'409 Conflict')
 def test_export_owner_can_delete_after_permission_is_revoked(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file);uid=security.create_user(db,'viewer','viewer-test-password','viewer');security.create_user(db,'other','other-test-password','viewer')
    one=security.authenticate(db,'viewer','viewer-test-password','first');two=security.authenticate(db,'other','other-test-password','second')
    jid=jobs.enqueue(db,uid,cid,{})
    name=f'export-{jid}-0123456789abcdef.mp4';(self.exports/name).write_bytes(b'output')
    with db:db.execute("UPDATE export_jobs SET status='done',result_name=? WHERE id=?",(name,jid))
    db.close();own='sunhill_session='+one.token;foreign='sunhill_session='+two.token
    rows=json.loads(self.call('/api/export-jobs',cookie=own)[1])['jobs']
    self.assertEqual(rows[0]['camera'],'Kamera niedostępna');self.assertFalse(rows[0]['download_ready'])
    self.assertEqual(self.call(f'/api/export-jobs/{jid}','DELETE',cookie=foreign,csrf=two.csrf)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(f'/api/export-jobs/{jid}','DELETE',cookie=own)[0]['status'],'403 Forbidden')
    self.assertTrue((self.exports/name).exists())
    self.assertEqual(self.call(f'/api/export-jobs/{jid}','DELETE',cookie=own,csrf=one.csrf)[0]['status'],'200 OK')
    self.assertFalse((self.exports/name).exists());self.assertEqual(json.loads(self.call('/api/export-jobs',cookie=own)[1])['jobs'],[])
 def test_running_export_cannot_be_deleted_and_queued_export_can_be_cancelled(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file);running=jobs.enqueue(db,self.uid,cid,{});queued=jobs.enqueue(db,self.uid,cid,{});jobs.claim(db);db.close()
    self.assertEqual(self.call(f'/api/export-jobs/{running}','DELETE',cookie=cookie,csrf=csrf)[0]['status'],'409 Conflict')
    self.assertEqual(self.call(f'/api/export-jobs/{queued}','DELETE',cookie=cookie,csrf=csrf)[0]['status'],'200 OK')
 def test_export_cancellation_requires_owner_and_preserves_running_job_until_worker_stops(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file);viewer=security.create_user(db,'viewer','viewer-test-password','viewer')
    session=security.authenticate(db,'viewer','viewer-test-password','client');jid=jobs.enqueue(db,self.uid,cid,{})
    job=jobs.claim(db);db.close();other='sunhill_session='+session.token
    self.assertEqual(self.call(f'/api/export-jobs/{jid}/cancel','POST',{},other,session.csrf)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(f'/api/export-jobs/{jid}/cancel','POST',{},cookie)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(f'/api/export-jobs/{jid}/cancel','POST',{},cookie,csrf)[0]['status'],'202 Accepted')
    self.assertTrue(json.loads(self.call('/api/export-jobs',cookie=cookie)[1])['jobs'][0]['cancel_requested'])
    db=database.open_db(self.file)
    self.assertFalse(jobs.heartbeat(db,job,.5));self.assertFalse(jobs.complete(db,job,'clip.mp4'))
    self.assertEqual(db.execute('SELECT status FROM export_jobs WHERE id=?',(jid,)).fetchone()[0],'running');db.close()
 def test_playback_lock_and_explicit_download_permission(self):
    from sunhill_nvr.files import open_confined,try_exclusive
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file);uid=security.create_user(db,'viewer','viewer-test-password','viewer')
    with db:db.execute('INSERT INTO camera_permissions VALUES (?,?,0)',(uid,cid))
    session=security.authenticate(db,'viewer','viewer-test-password','other-client');db.close()
    viewer='sunhill_session='+session.token
    file=self.root/'brama/2026/11/21/190900.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'0123456789');st=file.stat()
    db=sqlite3.connect(self.file)
    rid=db.execute('INSERT INTO recordings(relative_path,camera,local_time,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?)',('brama/2026/11/21/190900.mp4','brama','2026-11-21T19:09:00',10,10,st.st_mtime_ns,1)).lastrowid
    db.commit();db.close()
    r,_=self.call(f'/api/recordings/{rid}/file',cookie=viewer,extra={'QUERY_STRING':'download=1'})
    self.assertEqual(r['status'],'403 Forbidden')
    r,body=self.call(f'/api/recordings/{rid}/file',cookie=viewer)
    self.assertEqual(r['status'],'200 OK');self.assertEqual(body,b'0123456789')
    r,_=self.call(f'/api/recordings/{rid}/file',cookie=cookie,extra={'QUERY_STRING':'download=1'})
    self.assertEqual(r['status'],'200 OK');self.assertIn('attachment;',dict(r['headers'])['Content-Disposition'])
    env={'PATH_INFO':f'/api/recordings/{rid}/file','REQUEST_METHOD':'GET','HTTP_COOKIE':viewer,'wsgi.input':io.BytesIO()}
    response={};stream=self.app(env,lambda status,headers:response.update(status=status))
    fd,_=open_confined(self.root,'brama/2026/11/21/190900.mp4')
    try:
        self.assertEqual(response['status'],'200 OK');self.assertFalse(try_exclusive(fd))
        stream.close();self.assertTrue(try_exclusive(fd))
    finally:
        import os
        os.close(fd);stream.close()
 def test_revoked_download_permission_interrupts_open_file_response(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file);uid=security.create_user(db,'viewer','viewer-test-password','viewer')
    with db:db.execute('INSERT INTO camera_permissions VALUES(?,?,1)',(uid,cid))
    session=security.authenticate(db,'viewer','viewer-test-password','viewer-client')
    file=self.root/'cam/2026/11/21/190900.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'x'*(800*1024));info=file.stat()
    with db:rid=db.execute('INSERT INTO recordings(relative_path,camera,local_time,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?)',('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00',10,info.st_size,info.st_mtime_ns,1)).lastrowid
    env={'PATH_INFO':f'/api/recordings/{rid}/file','QUERY_STRING':'download=1','REQUEST_METHOD':'GET','HTTP_COOKIE':'sunhill_session='+session.token,'wsgi.input':io.BytesIO()};response={}
    stream=self.app(env,lambda status,headers:response.update(status=status));self.assertEqual(response['status'],'200 OK')
    clock=[0.0];stream.monotonic=lambda:clock[0];chunks=iter(stream);self.assertEqual(len(next(chunks)),256*1024)
    with db:db.execute('UPDATE camera_permissions SET may_download=0 WHERE user_id=? AND camera_id=?',(uid,cid))
    clock[0]=6
    with self.assertRaises(StopIteration):next(chunks)
    self.assertIsNone(stream.fd);db.close()
 def test_revoked_permission_interrupts_export_download(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file);uid=security.create_user(db,'viewer','viewer-test-password','viewer')
    with db:db.execute('INSERT INTO camera_permissions VALUES(?,?,1)',(uid,cid))
    session=security.authenticate(db,'viewer','viewer-test-password','viewer-client');jid=jobs.enqueue(db,uid,cid,{})
    name=f'export-{jid}-0123456789abcdef.mp4';(self.exports/name).write_bytes(b'x'*(800*1024))
    with db:db.execute("UPDATE export_jobs SET status='done',result_name=? WHERE id=?",(name,jid))
    env={'PATH_INFO':f'/api/export-jobs/{jid}/file','REQUEST_METHOD':'GET','HTTP_COOKIE':'sunhill_session='+session.token,'wsgi.input':io.BytesIO()};response={}
    stream=self.app(env,lambda status,headers:response.update(status=status));self.assertEqual(response['status'],'200 OK')
    clock=[0.0];stream.monotonic=lambda:clock[0];chunks=iter(stream);self.assertEqual(len(next(chunks)),256*1024)
    with db:db.execute('DELETE FROM camera_permissions WHERE user_id=? AND camera_id=?',(uid,cid))
    clock[0]=6
    with self.assertRaises(StopIteration):next(chunks)
    self.assertIsNone(stream.fd);db.close()
 def test_archive_hvc1_range_and_original_download_permissions(self):
    import subprocess
    from sunhill_nvr.media import probe
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    file=self.root/'cam/2026/11/21/190900.mp4';file.parent.mkdir(parents=True)
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=blue:s=160x96:r=10:d=1','-c:v','libx265','-x265-params','pools=1:frame-threads=1:log-level=error','-tag:v','hev1','-movflags','+frag_keyframe+empty_moov+default_base_moof',str(file)],check=True,stderr=subprocess.PIPE)
    original=file.read_bytes();db=database.open_db(self.file);archive.refresh(db,self.root,'Europe/Warsaw',inspect_duration=lambda p:1,is_open=lambda p:False,mount_check=lambda:True,min_age_seconds=0)
    rid=db.execute('SELECT id FROM recordings').fetchone()[0];uid=security.create_user(db,'viewer','viewer-test-password','viewer');session=security.authenticate(db,'viewer','viewer-test-password','fixture');db.close()
    url=f'/api/recordings/{rid}/file';viewer='sunhill_session='+session.token;q={'QUERY_STRING':'compat=hvc1'}
    self.assertEqual(self.call(url,cookie=viewer,extra=q)[0]['status'],'403 Forbidden')
    self.assertFalse((self.file.parent/'.archive-hvc1-cache').exists())
    db=database.open_db(self.file);db.execute('INSERT INTO camera_permissions(user_id,camera_id,may_download) VALUES (?,?,0)',(uid,cid));db.commit();db.close()
    r,body=self.call(url,cookie=viewer,extra=q);self.assertEqual(r['status'],'200 OK');self.assertNotEqual(body,original)
    cache=next((self.file.parent/'.archive-hvc1-cache').glob('*.mp4'));self.assertEqual(probe(cache)['streams'][0]['codec_tag_string'],'hvc1')
    r,part=self.call(url,cookie=viewer,extra={**q,'HTTP_RANGE':'bytes=10-99'});self.assertEqual(r['status'],'206 Partial Content');self.assertEqual(part,body[10:100]);self.assertEqual(dict(r['headers'])['Content-Range'],f'bytes 10-99/{len(body)}')
    r,empty=self.call(url,'HEAD',cookie=viewer,extra=q);self.assertEqual(r['status'],'200 OK');self.assertEqual(empty,b'');self.assertEqual(dict(r['headers'])['Content-Length'],str(len(body)))
    self.assertEqual(self.call(url,cookie=viewer,extra={'QUERY_STRING':'download=1'})[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(url,cookie=cookie,extra={'QUERY_STRING':'download=1'})[1],original)
    self.assertEqual(self.call(url,cookie=cookie,extra={'QUERY_STRING':'download=1&compat=hvc1'})[0]['status'],'400 Bad Request')
    self.assertEqual(file.read_bytes(),original)
    file.write_bytes(b'changed');self.assertEqual(self.call(url,cookie=cookie,extra=q)[0]['status'],'404 Not Found')
 def test_motion_search_acl_range_csrf_and_identity(self):
    from unittest.mock import patch
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    file=self.root/'cam/2026/11/21/190900.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'fixture')
    db=database.open_db(self.file);archive.refresh(db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True,min_age_seconds=0)
    rid=db.execute('SELECT id FROM recordings').fetchone()[0];uid=security.create_user(db,'motionviewer','viewer-test-password','viewer');db.close()
    r,b=self.call('/api/login','POST',{'username':'motionviewer','password':'viewer-test-password'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0];vcsrf=json.loads(b)['csrf']
    url=f'/api/recordings/{rid}/motion-search';payload={'box':[0,0,.5,1],'start':0,'duration':10};result={'hits':[{'start':1,'end':2}],'sample_count':20,'sample_fps':2}
    with patch('sunhill_nvr.motion_search.scan',return_value=result) as scan:
        self.assertEqual(self.call(url,'POST',payload,cookie)[0]['status'],'403 Forbidden')
        self.assertEqual(self.call(url,'POST',payload,viewer,vcsrf)[0]['status'],'403 Forbidden')
        self.assertEqual(self.call(url,'POST',{**payload,'duration':11},cookie,csrf)[0]['status'],'400 Bad Request');scan.assert_not_called()
        db=database.open_db(self.file);db.execute('INSERT INTO camera_permissions(user_id,camera_id,may_download) VALUES (?,?,0)',(uid,cid));db.commit();db.close()
        r,b=self.call(url,'POST',payload,viewer,vcsrf);self.assertEqual(r['status'],'200 OK');self.assertEqual(json.loads(b),result);scan.assert_called_once()
        file.write_bytes(b'changed fixture');self.assertEqual(self.call(url,'POST',payload,cookie,csrf)[0]['status'],'404 Not Found');scan.assert_called_once()
 def test_archive_media_info_checks_access_and_file_identity(self):
    from unittest.mock import patch
    from sunhill_nvr.media import MediaError
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    file=self.root/'cam/2026/11/21/190900.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'fixture')
    db=database.open_db(self.file);archive.refresh(db,self.root,'Europe/Warsaw',inspect_duration=lambda p:10,is_open=lambda p:False,mount_check=lambda:True,min_age_seconds=0)
    rid=db.execute('SELECT id FROM recordings').fetchone()[0];uid=security.create_user(db,'viewer','viewer-test-password','viewer');session=security.authenticate(db,'viewer','viewer-test-password','fixture');db.close()
    url=f'/api/recordings/{rid}/media-info';viewer='sunhill_session='+session.token
    with patch('sunhill_nvr.media.probe',return_value={'streams':[{'codec_type':'video','codec_name':'hevc','codec_tag_string':'hev1','secret':'not returned'}]}) as probe:
        self.assertEqual(self.call(url,cookie=viewer)[0]['status'],'403 Forbidden');probe.assert_not_called()
        db=database.open_db(self.file);db.execute('INSERT INTO camera_permissions(user_id,camera_id,may_download) VALUES (?,?,0)',(uid,cid));db.commit();db.close()
        r,b=self.call(url,cookie=viewer);self.assertEqual(r['status'],'200 OK');self.assertEqual(json.loads(b),{'streams':[{'type':'video','codec':'hevc','tag':'hev1'}]})
        self.assertEqual(probe.call_args.kwargs['timeout'],8)
        self.assertEqual(self.call(url,cookie=cookie,extra={'QUERY_STRING':'extra=1'})[0]['status'],'400 Bad Request')
    with patch('sunhill_nvr.media.probe',side_effect=MediaError('invalid')):
        self.assertEqual(self.call(url,cookie=cookie)[0]['status'],'422 Unprocessable Entity')
    file.write_bytes(b'changed fixture')
    with patch('sunhill_nvr.media.probe') as probe:
        self.assertEqual(self.call(url,cookie=cookie)[0]['status'],'404 Not Found');probe.assert_not_called()
 def test_calendar_shows_days_crossed_by_a_recording(self):
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    db=sqlite3.connect(self.file)
    db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',('brama/2026/11/30/235958.mp4','brama','2026-11-30T23:59:58','2026-11-30T22:59:58+00:00',10,1,1,1));db.commit();db.close()
    r,body=self.call(f'/api/cameras/{cid}/calendar',cookie=cookie,extra={'QUERY_STRING':'month=2026-12'})
    self.assertEqual(r['status'],'200 OK');self.assertEqual(json.loads(body)['days'],['2026-12-01'])
    self.assertEqual(self.call(f'/api/cameras/{cid}/calendar',cookie=cookie,extra={'QUERY_STRING':'month=2099-99'})[0]['status'],'400 Bad Request')
    for endpoint in ('recordings','events'):
        self.assertEqual(self.call(f'/api/cameras/{cid}/{endpoint}',cookie=cookie,extra={'QUERY_STRING':'date=9999-12-31'})[0]['status'],'400 Bad Request')
 def test_next_recording_crosses_multiple_days_and_checks_camera_permission(self):
    from datetime import datetime,timezone
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file)
    with db:
        for slug,stamp in [('cam','2026-11-20T10:00:00+00:00'),('cam','2026-12-02T10:00:00+00:00'),('other','2026-11-21T10:00:00+00:00')]:
            db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',(slug+stamp,slug,stamp[:19],stamp,60,1,1,1))
    uid=security.create_user(db,'viewer','viewer-test-password','viewer');session=security.authenticate(db,'viewer','viewer-test-password','client');db.close()
    endpoint=f'/api/cameras/{cid}/next-recording';after=int(datetime(2026,11,20,10,1,tzinfo=timezone.utc).timestamp()*1000)
    query=f'after_ms={after}&exclude=1';r,body=self.call(endpoint,cookie=cookie,extra={'QUERY_STRING':query})
    self.assertEqual(r['status'],'200 OK');self.assertEqual(json.loads(body)['recording']['utc_first'],'2026-12-02T10:00:00+00:00')
    self.assertEqual(self.call(endpoint,cookie='sunhill_session='+session.token,extra={'QUERY_STRING':query})[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(endpoint,cookie=cookie,extra={'QUERY_STRING':'after_ms=9999999999999&exclude=1'})[0]['status'],'400 Bad Request')
 def test_update_camera_preserves_secrets_and_removal_preserves_archive(self):
    cookie,csrf=self.login()
    r,body=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://admin:secret@camera/live'},cookie,csrf)
    cid=json.loads(body)['id'];file=self.root/'brama/2026/11/21/190900.mp4';file.parent.mkdir(parents=True);file.write_bytes(b'original')
    r,body=self.call(f'/api/cameras/{cid}',cookie=cookie);self.assertNotIn(b'secret',body)
    db=sqlite3.connect(str(self.file));db.execute("INSERT INTO stream_health VALUES(?,'available',0,1,1)",(cid,));db.commit();db.close()
    r,_=self.call(f'/api/cameras/{cid}','PATCH',{'name':'Nowa nazwa'},cookie,csrf);self.assertEqual(r['status'],'200 OK')
    db=sqlite3.connect(str(self.file));url=db.execute('SELECT main_url FROM camera_details WHERE camera_id=?',(cid,)).fetchone()[0];db.close()
    self.assertEqual(url,'rtsp://admin:secret@camera/live')
    r,_=self.call(f'/api/cameras/{cid}','PATCH',{'main_url':'rtsp://camera/new'},cookie,csrf);self.assertEqual(r['status'],'200 OK')
    db=sqlite3.connect(str(self.file));self.assertIsNone(db.execute('SELECT state FROM stream_health WHERE camera_id=?',(cid,)).fetchone());db.close()
    r,_=self.call(f'/api/cameras/{cid}','DELETE',None,cookie,csrf);self.assertEqual(r['status'],'200 OK');self.assertEqual(file.read_bytes(),b'original')
 def test_camera_credentials_can_be_set_without_returning_them(self):
    cookie,csrf=self.login()
    r,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://camera/main','sub_url':'rtsp://camera/sub','username':'admin','password':'p@ss:word'},cookie,csrf)
    self.assertEqual(r['status'],'201 Created');cid=json.loads(body)['id']
    db=sqlite3.connect(self.file);main,sub=db.execute('SELECT main_url,sub_url FROM camera_details WHERE camera_id=?',(cid,)).fetchone();db.close()
    self.assertEqual(main,'rtsp://admin:p%40ss%3Aword@camera/main');self.assertEqual(sub,'rtsp://admin:p%40ss%3Aword@camera/sub')
    r,body=self.call(f'/api/cameras/{cid}',cookie=cookie);self.assertNotIn(b'p%40ss',body)
    r,_=self.call(f'/api/cameras/{cid}','PATCH',{'username':'new','password':'secret!'},cookie,csrf);self.assertEqual(r['status'],'200 OK')
    db=sqlite3.connect(self.file);main=db.execute('SELECT main_url FROM camera_details WHERE camera_id=?',(cid,)).fetchone()[0];db.close()
    self.assertEqual(main,'rtsp://new:secret%21@camera/main')
 def test_camera_statistics_admin_only(self):
    cookie,csrf=self.login()
    r,body=self.call('/api/cameras','POST',{'slug':'testcam','name':'Test','main_url':'rtsp://u:secret@x/test'},cookie,csrf);self.assertEqual(r['status'],'201 Created');cid=json.loads(body)['id']
    r,body=self.call('/api/admin/camera-statistics',cookie=cookie);self.assertEqual(r['status'],'200 OK',body);self.assertNotIn(b'secret',body);self.assertEqual(json.loads(body)['cameras'][str(cid)]['files'],0)
    self.assertEqual(self.call('/api/admin/camera-statistics')[0]['status'],'401 Unauthorized')
    self.call('/api/users','POST',{'username':'viewer','password':'viewer-secret-password','role':'viewer'},cookie,csrf)
    r,_=self.call('/api/login','POST',{'username':'viewer','password':'viewer-secret-password'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0]
    self.assertEqual(self.call('/api/admin/camera-statistics',cookie=viewer)[0]['status'],'403 Forbidden')
 def test_rtsp_only_retired_ftp_api_and_camera_modes(self):
    cookie,csrf=self.login();payload={'slug':'legacy','name':'Legacy','main_url':'rtsp://host/live','recording':'ftp'}
    r,b=self.call('/api/cameras','POST',dict(payload,recording='rtsp'),cookie,csrf);self.assertEqual(r['status'],'201 Created');cid=json.loads(b)['id']
    self.assertEqual(self.call('/api/ftp-accounts',cookie=cookie)[0]['status'],'404 Not Found')
    self.assertEqual(self.call('/api/ftp-accounts','POST',{'username':'legacy','password':'long-password'},cookie,csrf)[0]['status'],'404 Not Found')
    self.assertEqual(self.call(f'/api/cameras/{cid}/ftp-accounts',cookie=cookie)[0]['status'],'404 Not Found')
    self.assertEqual(self.call(f'/api/cameras/{cid}','PATCH',{'recording':'ftp'},cookie,csrf)[0]['status'],'400 Bad Request')
    self.assertEqual(self.call('/api/cameras','POST',dict(payload,slug='second'),cookie,csrf)[0]['status'],'400 Bad Request')
    inventory=json.loads(self.call('/api/admin/inventory',cookie=cookie)[1]);self.assertTrue(inventory['rtsp_only']);self.assertEqual(inventory['accounts'],[]);self.assertEqual(inventory['cameras'][0]['recording'],'rtsp')
    self.assertEqual(self.call(f'/api/cameras/{cid}','PATCH',{'recording':'none'},cookie,csrf)[0]['status'],'200 OK')
 def test_admin_inventory_matches_existing_endpoints_without_secrets(self):
    cookie,csrf=self.login()
    def call(url,method='GET',data=None,status='200 OK'):
        r,body=self.call(url,method,data,cookie,csrf);self.assertEqual(r['status'],status,body);return json.loads(body)
    cid=call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://camera-user:camera-secret@x/test'},'201 Created')['id']
    uid=call('/api/users','POST',{'username':'viewer','password':'viewer-secret-password','role':'viewer'},'201 Created')['id']
    call(f'/api/users/{uid}/permissions','POST',{'cameras':[{'id':cid,'download':True}]})
    call(f'/api/cameras/{cid}/onvif','PATCH',{'url':'http://192.0.2.10/onvif/device_service','username':'onvif','password':'onvif-secret','auth_mode':'digest'})
    call(f'/api/cameras/{cid}','PATCH',{'enabled':False})
    inventory=call('/api/admin/inventory')
    self.assertEqual(inventory['accounts'],[])
    self.assertEqual([{k:v for k,v in u.items() if k!='permissions'} for u in inventory['users']],call('/api/users')['users'])
    viewer=next(u for u in inventory['users'] if u['id']==uid)
    self.assertEqual(viewer['permissions'],call(f'/api/users/{uid}/permissions')['cameras'])
    camera=inventory['cameras'][0];self.assertEqual(camera['id'],cid);self.assertFalse(camera['enabled'])
    self.assertEqual(camera['accounts'],[])
    self.assertEqual(camera['onvif'],call(f'/api/cameras/{cid}/onvif'))
    self.assertEqual(camera['recording'],call(f'/api/cameras/{cid}')['recording'])
    for secret in ('camera-secret','unused-secret-password','viewer-secret-password','onvif-secret','password_hash','main_url'):
        self.assertNotIn(secret,json.dumps(inventory))
    self.assertEqual(self.call('/api/admin/inventory')[0]['status'],'401 Unauthorized')
    r,_=self.call('/api/login','POST',{'username':'viewer','password':'viewer-secret-password'})
    viewer_cookie=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0]
    self.assertEqual(self.call('/api/admin/inventory',cookie=viewer_cookie)[0]['status'],'403 Forbidden')
 def test_admin_inventory_rechecks_revoked_session_inside_snapshot(self):
    from unittest.mock import patch
    cookie,csrf=self.login();real=self.app.current_admin
    def revoke(db,token):
        other=database.open_db(self.file)
        try:
            with other:other.execute('DELETE FROM sessions')
        finally:other.close()
        return real(db,token)
    with patch.object(self.app,'current_admin',side_effect=revoke):
        r,body=self.call('/api/admin/inventory',cookie=cookie)
    self.assertEqual(r['status'],'401 Unauthorized',body)
 def test_static_ui_and_security_headers(self):
    r,body=self.call('/')
    self.assertEqual(r['status'],'200 OK');self.assertIn(b'Sunhill NVR',body)
    self.assertIn('Content-Security-Policy',dict(r['headers']))
    self.assertIn('wss://nvr.example',dict(r['headers'])['Content-Security-Policy'])
    self.assertTrue(dict(r['headers'])['Content-Type'].startswith('text/html'))
 def test_player_asset_route_is_narrow_and_has_correct_mime(self):
    from unittest.mock import patch
    with patch('sunhill_nvr.player_assets.load_player_asset',return_value=b'/* script */') as load:
        for name in ('video-stream','video-rtc'):
            url='/assets/live-player/1/'+name+'.js'
            r,body=self.call(url);self.assertEqual(r['status'],'200 OK');self.assertEqual(body,b'/* script */');self.assertEqual(dict(r['headers'])['Content-Type'],'text/javascript; charset=utf-8');self.assertEqual(dict(r['headers'])['Cache-Control'],'no-store');load.assert_called_with(name)
            self.assertEqual(self.call(url,'HEAD')[1],b'')
        count=load.call_count
        for url in ('/assets/live-player/0/video-rtc.js','/assets/live-player/1/api/streams','/assets/live-player/1/../video-rtc.js'):
            self.assertNotEqual(self.call(url)[0]['status'],'200 OK')
        self.assertNotEqual(self.call('/assets/live-player/1/video-rtc.js','POST')[0]['status'],'200 OK');self.assertEqual(load.call_count,count)
    with patch('sunhill_nvr.player_assets.load_player_asset',side_effect=ConnectionRefusedError):
        self.assertEqual(self.call('/assets/live-player/1/video-rtc.js')[0]['status'],'503 Service Unavailable')
 def test_internal_health_checks_database_without_exposing_sensitive_data(self):
    r,body=self.call('/internal/health')
    self.assertEqual(r['status'],'200 OK');self.assertEqual(json.loads(body),{'status':'ok'})
 def test_rotation_preview_and_history_require_admin(self):
    from sunhill_nvr import rotation_service,retention
    cookie,csrf=self.login()
    self.assertEqual(self.call('/api/rotation')[0]['status'],'401 Unauthorized')
    r,body=self.call('/api/rotation/previews','POST',{},cookie,csrf);self.assertEqual(r['status'],'202 Accepted');jid=json.loads(body)['id']
    db=database.open_db(self.file);rotation_service.run_preview(db,lambda:retention.RetentionResult(8,9,('cam/2026/11/21/190900.mp4',),True,False,2,40));db.close()
    r,body=self.call(f'/api/rotation/previews/{jid}',cookie=cookie);self.assertEqual(json.loads(body)['result']['protected_count'],2)
    r,body=self.call('/api/rotation',cookie=cookie);self.assertEqual(r['status'],'200 OK')
 def test_rotation_preview_is_rolled_back_if_audit_fails(self):
    from unittest.mock import patch
    cookie,csrf=self.login()
    with patch.object(self.app,'audit',side_effect=sqlite3.OperationalError('disk error')):
        self.assertEqual(self.call('/api/rotation/previews','POST',{},cookie,csrf)[0]['status'],'500 Internal Server Error')
    db=database.open_db(self.file);self.assertEqual(db.execute('SELECT count(*) FROM rotation_preview_jobs').fetchone()[0],0);db.close()
 def test_rotation_preview_rechecks_admin_before_queueing(self):
    from unittest.mock import patch
    cookie,csrf=self.login();original=self.app.json_body
    def revoke(environ,**kwargs):
        data=original(environ,**kwargs);db=database.open_db(self.file)
        with db:db.execute("UPDATE users SET role='viewer' WHERE id=?",(self.uid,))
        db.close();return data
    with patch.object(self.app,'json_body',side_effect=revoke):
        self.assertEqual(self.call('/api/rotation/previews','POST',{},cookie,csrf)[0]['status'],'403 Forbidden')
    db=database.open_db(self.file);self.assertEqual(db.execute('SELECT count(*) FROM rotation_preview_jobs').fetchone()[0],0);db.close()
 def test_tls_request_is_admin_only_private_and_never_echoes_the_key(self):
    inbox=self.file.parent/'upgrade-inbox';inbox.mkdir(mode=0o700)
    cookie,csrf=self.login()
    r,_=self.call('/api/settings/tls','POST',{'certificate':'cert','key':'private'},cookie)
    self.assertEqual(r['status'],'403 Forbidden')
    r,body=self.call('/api/settings/tls','POST',{'certificate':'cert','key':'private'},cookie,csrf)
    self.assertEqual(r['status'],'202 Accepted');self.assertNotIn(b'private',body)
    r,body=self.call('/api/settings/tls',cookie=cookie)
    self.assertEqual(r['status'],'200 OK');self.assertTrue(json.loads(body)['pending']);self.assertNotIn(b'private',body)
    self.assertEqual(self.call('/api/settings/tls','POST',{'certificate':'cert','key':'private'},cookie,csrf)[0]['status'],'409 Conflict')
    db=database.open_db(self.file);security.create_user(db,'viewer','viewer-test-password','viewer')
    session=security.authenticate(db,'viewer','viewer-test-password','other-client');db.close()
    viewer='sunhill_session='+session.token
    self.assertEqual(self.call('/api/settings/tls',cookie=viewer)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call('/api/settings/tls','POST',{'certificate':'cert','key':'private'},viewer,session.csrf)[0]['status'],'403 Forbidden')
 def test_storage_noop_normalizes_and_does_not_queue(self):
    self.app.staging_root=Path(self.temp.name)/'stage'
    self.app.upgrade_inbox.mkdir(mode=0o700);cookie,csrf=self.login()
    data={'archive':str(self.root)+'/', 'exports':str(self.exports)+'/', 'staging':str(self.app.staging_root)+'/', 'request_id':'a'*32}
    response,body=self.call('/api/settings/storage/archive','POST',data,cookie,csrf)
    self.assertEqual(response['status'],'200 OK');self.assertTrue(json.loads(body)['unchanged']);self.assertFalse((self.app.upgrade_inbox/'storage-request.json').exists())
    value=json.loads(self.call('/api/settings/storage',cookie=cookie)[1]);self.assertEqual(value['staging'],str(self.app.staging_root))
 def test_storage_explicit_paths_preserved_and_overlap_rejected(self):
    from unittest.mock import patch
    self.app.staging_root=Path('/home/store/staging')
    self.app.upgrade_inbox.mkdir(mode=0o700);cookie,csrf=self.login()
    url='/api/settings/storage/archive'
    with patch('sunhill_nvr.storage_inbox.recovery_available',return_value=True):
        bad={'archive':'/home/store/kamery/','exports':'/home/store/kamery/','staging':'/home/store/staging','request_id':'a'*32}
        result,body=self.call(url,'POST',bad,cookie,csrf)
        self.assertEqual(result['status'],'400 Bad Request');self.assertFalse((self.app.upgrade_inbox/'storage-request.json').exists())
        good={'archive':'/home/store/other/','exports':'/home/store/exports/','staging':'/home/store/staging/','request_id':'b'*32}
        self.assertEqual(self.call(url,'POST',good,cookie,csrf)[0]['status'],'202 Accepted')
        request=json.loads((self.app.upgrade_inbox/'storage-request.json').read_text())
        self.assertEqual((request['archive'],request['exports'],request['staging']),('/home/store/other','/home/store/exports','/home/store/staging'))
 def test_nested_storage_requires_compatible_recovery_and_keeps_exact_paths(self):
    from unittest.mock import patch
    self.app.upgrade_inbox.mkdir(mode=0o700);cookie,csrf=self.login()
    data={'archive':'/home/store/kamery/','exports':'/home/store/kamery/.sunhill-exports/','staging':'/home/store/kamery/.sunhill-staging/','request_id':'c'*32}
    url='/api/settings/storage/archive'
    with patch('sunhill_nvr.storage_inbox.nested_recovery_available',return_value=False):
        self.assertEqual(self.call(url,'POST',data,cookie,csrf)[0]['status'],'503 Service Unavailable')
        self.assertFalse((self.app.upgrade_inbox/'storage-request.json').exists())
    with patch('sunhill_nvr.storage_inbox.nested_recovery_available',return_value=True),patch('sunhill_nvr.storage_inbox.recovery_available',return_value=True):
        self.assertEqual(self.call(url,'POST',data,cookie,csrf)[0]['status'],'202 Accepted')
    request=json.loads((self.app.upgrade_inbox/'storage-request.json').read_text())
    for name in ('archive','exports','staging'):self.assertEqual(request[name],data[name].rstrip('/'))
 def test_storage_correlated_request_requires_new_recovery(self):
    from unittest.mock import patch
    self.app.upgrade_inbox.mkdir(mode=0o700);cookie,csrf=self.login()
    data={'archive':'/home/store/new/', 'exports':str(self.exports), 'request_id':'a'*32}
    with patch('sunhill_nvr.storage_inbox.recovery_available',return_value=False):
        self.assertEqual(self.call('/api/settings/storage/archive','POST',data,cookie,csrf)[0]['status'],'503 Service Unavailable')
    with patch('sunhill_nvr.storage_inbox.recovery_available',return_value=True):
        self.assertEqual(self.call('/api/settings/storage/archive','POST',data,cookie,csrf)[0]['status'],'202 Accepted')
    request=json.loads((self.app.upgrade_inbox/'storage-request.json').read_text());self.assertEqual(request['request_id'],'a'*32);self.assertEqual(request['archive'],'/home/store/new')
 def test_recording_segment_settings_validate_and_persist(self):
    cookie,csrf=self.login();url='/api/settings/recording'
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1])['segment_seconds'],300)
    self.assertEqual(self.call(url,'PATCH',{'segment_seconds':60},cookie)[0]['status'],'403 Forbidden')
    for bad in (True,59,3601,'300'):
        self.assertEqual(self.call(url,'PATCH',{'segment_seconds':bad},cookie,csrf)[0]['status'],'400 Bad Request')
    self.assertEqual(self.call(url,'PATCH',{'segment_seconds':30},cookie,csrf)[0]['status'],'200 OK')
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1])['segment_seconds'],30)
    self.assertEqual(self.call(url,'PATCH',{'segment_seconds':3600},cookie,csrf)[0]['status'],'200 OK')
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1])['segment_seconds'],3600)
 def test_archive_change_request_requires_admin_and_csrf(self):
    inbox=self.file.parent/'upgrade-inbox';inbox.mkdir(mode=0o700)
    cookie,csrf=self.login()
    self.assertEqual(self.call('/api/settings/storage/archive','POST',{'archive':'/home/store/new'},cookie)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call('/api/settings/storage/archive','POST',{'archive':'relative'},cookie,csrf)[0]['status'],'400 Bad Request')
    self.assertEqual(self.call('/api/settings/storage/archive','POST',{'archive':'/home/store/new'},cookie,csrf)[0]['status'],'202 Accepted')
    response,body=self.call('/api/settings/storage',cookie=cookie)
    self.assertEqual(response['status'],'200 OK');self.assertTrue(json.loads(body)['change_pending'])
    self.assertEqual(self.call('/api/settings/storage/archive','POST',{'archive':'/home/store/other'},cookie,csrf)[0]['status'],'409 Conflict')
    db=database.open_db(self.file);security.create_user(db,'viewer','viewer-test-password','viewer')
    session=security.authenticate(db,'viewer','viewer-test-password','other-client');db.close()
    self.assertEqual(self.call('/api/settings/storage/archive','POST',{'archive':'/home/store/new'},'sunhill_session='+session.token,session.csrf)[0]['status'],'403 Forbidden')
 def test_version_visible_and_media_paths_queued_without_signing_key(self):
    from sunhill_nvr import __version__
    self.assertIn(('v'+__version__).encode(),self.call('/')[1])
    self.app.upgrade_inbox.mkdir(mode=0o700)
    cookie,csrf=self.login()
    response,body=self.call('/api/settings/storage',cookie=cookie)
    self.assertEqual(json.loads(body)['exports'],str(self.app.export_root))
    data={'archive':'/mnt/recordings','exports':'/mnt/exports'}
    self.assertEqual(self.call('/api/settings/storage/archive','POST',data,cookie,csrf)[0]['status'],'202 Accepted')
    request=json.loads((self.app.upgrade_inbox/'storage-request.json').read_text())
    self.assertEqual(request['archive'],data['archive']);self.assertEqual(request['exports'],data['exports'])
 def test_approved_update_endpoint_does_not_require_signing_key(self):
    from sunhill_nvr import approved_update
    from unittest.mock import patch
    self.app.upgrade_inbox.mkdir(mode=0o700);self.app.upgrade_public_key=Path(self.temp.name)/'nonexistent-key'
    cookie,csrf=self.login()
    package='sunhill-nvr-0.2.1.tar.gz'
    with patch.object(approved_update,'available',return_value=[package]):
        self.assertEqual(self.call('/api/updates','POST',{'package':package},cookie)[0]['status'],'403 Forbidden')
        self.assertEqual(self.call('/api/updates','POST',{'package':'../invalid.tar.gz'},cookie,csrf)[0]['status'],'400 Bad Request')
        self.assertEqual(self.call('/api/updates','POST',{'package':package},cookie,csrf)[0]['status'],'202 Accepted')
        data=json.loads(self.call('/api/updates',cookie=cookie)[1]);self.assertFalse(data['key_configured']);self.assertTrue(data['pending']);self.assertIn(package,data['packages'])
 def test_rollback_endpoint_requires_confirmation_and_admin(self):
    from sunhill_nvr import rollback_history,__version__
    from unittest.mock import patch
    self.app.upgrade_inbox.mkdir(mode=0o700);cookie,csrf=self.login()
    row={'id':'a'*32,'version':'1.0.2','replaced_by':__version__,'created':1}
    data={'restore_point':row['id'],'expected':__version__,'confirm':True}
    with patch.object(rollback_history,'read',return_value=[row]):
        self.assertEqual(json.loads(self.call('/api/updates',cookie=cookie)[1])['restore_points'],[row])
        self.assertEqual(self.call('/api/updates','POST',data,cookie)[0]['status'],'403 Forbidden')
        self.assertEqual(self.call('/api/updates','POST',{**data,'confirm':False},cookie,csrf)[0]['status'],'409 Conflict')
        self.assertEqual(self.call('/api/updates','POST',{**data,'expected':'0.0.1'},cookie,csrf)[0]['status'],'409 Conflict')
        self.assertEqual(self.call('/api/updates','POST',data,cookie,csrf)[0]['status'],'202 Accepted')
        self.assertTrue(json.loads(self.call('/api/updates',cookie=cookie)[1])['pending'])
        self.assertEqual(self.call('/api/updates','POST',data,cookie,csrf)[0]['status'],'409 Conflict')
 def test_update_upload_requires_admin_and_csrf_and_queues_a_pair(self):
    self.app.upgrade_inbox.mkdir(mode=0o700);self.app.upgrade_public_key=Path(self.temp.name)/'public.pem';self.app.upgrade_public_key.write_text('test-key')
    cookie,csrf=self.login()
    upload=lambda kind,data,csrf_value: self.call('/api/update-uploads/'+kind,'POST',cookie=cookie,csrf=csrf_value,extra={'CONTENT_LENGTH':str(len(data)),'CONTENT_TYPE':'application/octet-stream','wsgi.input':io.BytesIO(data)})
    self.assertEqual(upload('archive',b'archive','')[0]['status'],'403 Forbidden')
    r,body=upload('archive',b'archive',csrf);self.assertEqual(r['status'],'201 Created');archive=json.loads(body)['id']
    r,body=upload('signature',b'signature',csrf);self.assertEqual(r['status'],'201 Created');signature=json.loads(body)['id']
    r,_=self.call('/api/updates','POST',{'archive':archive,'signature':signature},cookie,csrf);self.assertEqual(r['status'],'202 Accepted')
    r,body=self.call('/api/updates',cookie=cookie);self.assertTrue(json.loads(body)['pending']);self.assertTrue(json.loads(body)['key_configured'])
    r,_=self.call(f'/api/update-uploads/archive/{archive}','DELETE',None,cookie,csrf);self.assertEqual(r['status'],'409 Conflict')
 def test_update_request_reports_busy_upload_lock(self):
    from sunhill_nvr import upgrade_inbox
    self.app.upgrade_inbox.mkdir(mode=0o700);self.app.upgrade_public_key=Path(self.temp.name)/'public.pem';self.app.upgrade_public_key.write_text('test-key')
    cookie,csrf=self.login()
    with upgrade_inbox._upload_lock(self.app.upgrade_inbox):
        result,_=self.call('/api/updates','POST',{'archive':'a'*32,'signature':'b'*32},cookie,csrf)
    self.assertEqual(result['status'],'409 Conflict')
 def test_viewer_cannot_inspect_or_upload_updates(self):
    cookie,csrf=self.login()
    self.call('/api/users','POST',{'username':'viewer','password':'viewer-password-123','role':'viewer'},cookie,csrf)
    response,body=self.call('/api/login','POST',{'username':'viewer','password':'viewer-password-123'},extra={'REMOTE_ADDR':'192.0.2.2'})
    viewer_cookie=next(v for k,v in response['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0]
    viewer_csrf=json.loads(body)['csrf']
    self.assertEqual(self.call('/api/updates',cookie=viewer_cookie)[0]['status'],'403 Forbidden')
    response,_=self.call('/api/update-uploads/archive','POST',cookie=viewer_cookie,csrf=viewer_csrf,extra={'CONTENT_LENGTH':'3','wsgi.input':io.BytesIO(b'zip')})
    self.assertEqual(response['status'],'403 Forbidden')
 def test_export_preview_queue_and_access_control(self):
    cookie,csrf=self.login()
    r,body=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    db=sqlite3.connect(str(self.file));db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)",('brama/2026/11/21/190900.mp4','brama','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',60,10,1,1));db.commit();db.close()
    payload={'start':'2026-11-21T18:09:10Z','end':'2026-11-21T18:09:50Z','gaps':'black'}
    r,body=self.call(f'/api/cameras/{cid}/export-preview','POST',payload,cookie,csrf);self.assertEqual(r['status'],'200 OK');self.assertEqual(json.loads(body)['segments'],1)
    self.assertEqual((json.loads(body)['covered_seconds'],json.loads(body)['missing_seconds'],json.loads(body)['output_seconds']),(40,0,40))
    gap={**payload,'start':'2026-11-21T18:08:50Z'}
    preview=json.loads(self.call(f'/api/cameras/{cid}/export-preview','POST',gap,cookie,csrf)[1])
    self.assertEqual((preview['covered_seconds'],preview['missing_seconds'],preview['output_seconds']),(50,10,60))
    preview=json.loads(self.call(f'/api/cameras/{cid}/export-preview','POST',{**gap,'gaps':'skip'},cookie,csrf)[1])
    self.assertEqual((preview['covered_seconds'],preview['missing_seconds'],preview['output_seconds']),(50,10,50))
    r,body=self.call(f'/api/cameras/{cid}/export-jobs','POST',payload,cookie,csrf);self.assertEqual(r['status'],'201 Created');jid=json.loads(body)['id']
    r,body=self.call(f'/api/export-jobs/{jid}',cookie=cookie);self.assertEqual(json.loads(body)['status'],'queued')
    r,body=self.call('/api/export-jobs',cookie=cookie);self.assertEqual(r['status'],'200 OK');self.assertEqual([j['id'] for j in json.loads(body)['jobs']],[jid]);self.assertNotIn('result_name',json.loads(body)['jobs'][0])
    self.assertEqual(self.call('/api/export-jobs')[0]['status'],'401 Unauthorized')
    self.assertEqual(self.call(f'/api/export-jobs/{jid}')[0]['status'],'401 Unauthorized')
 def test_export_list_is_private_and_respects_revoked_download_access(self):
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file);uid=security.create_user(db,'viewer','viewer-test-password','viewer')
    with db:db.execute('INSERT INTO camera_permissions VALUES (?,?,1)',(uid,cid))
    session=security.authenticate(db,'viewer','viewer-test-password','other-client')
    own=jobs.enqueue(db,uid,cid,{});other=jobs.enqueue(db,self.uid,cid,{})
    viewer='sunhill_session='+session.token
    self.assertEqual([j['id'] for j in json.loads(self.call('/api/export-jobs',cookie=viewer)[1])['jobs']],[own])
    self.assertEqual(self.call(f'/api/export-jobs/{other}',cookie=viewer)[0]['status'],'403 Forbidden')
    with db:db.execute('UPDATE camera_permissions SET may_download=0 WHERE user_id=?',(uid,))
    item=json.loads(self.call('/api/export-jobs',cookie=viewer)[1])['jobs'][0]
    self.assertEqual(item['id'],own);self.assertEqual(item['camera'],'Kamera niedostępna');self.assertFalse(item['download_ready'])
    self.assertEqual(self.call(f'/api/export-jobs/{own}',cookie=viewer)[0]['status'],'403 Forbidden');db.close()
 def test_cross_origin_websocket_and_mutations_rejected(self):
    cookie,csrf=self.login()
    r,_=self.call('/api/users','POST',{'username':'other','password':'password-for-tests'},cookie,csrf,{'HTTP_ORIGIN':'https://evil.example'})
    self.assertEqual(r['status'],'403 Forbidden')
    r,_=self.call('/internal/live-auth',cookie=cookie,extra={'HTTP_ORIGIN':'https://evil.example','HTTP_X_ORIGINAL_URI':'/live/api/ws?src=brama'})
    self.assertEqual(r['status'],'403 Forbidden')
 def test_api_to_archive_to_queued_mp4_and_download(self):
    import subprocess
    from sunhill_nvr import export_service
    from sunhill_nvr.media import probe
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    db=sqlite3.connect(str(self.file))
    for second in (0,1):
        file=self.root/f'brama/2026/11/21/19090{second}.mp4';file.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=blue:s=160x90:r=25:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(file)],check=True,stderr=subprocess.PIPE)
        st=file.stat();db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)',(str(file.relative_to(self.root)),'brama',f'2026-11-21T19:09:0{second}',f'2026-11-21T18:09:0{second}+00:00',1,st.st_size,st.st_mtime_ns,1))
    db.commit()
    payload={'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:02Z','gaps':'skip'}
    r,body=self.call(f'/api/cameras/{cid}/export-jobs','POST',payload,cookie,csrf);self.assertEqual(r['status'],'201 Created');jid=json.loads(body)['id']
    export_service.run_one(db,self.root,self.exports,archive_lock=Path(self.temp.name)/'archive.lock',free=lambda p:10**12);db.close()
    r,body=self.call(f'/api/export-jobs/{jid}',cookie=cookie);self.assertEqual(json.loads(body)['status'],'done')
    r,video=self.call(f'/api/export-jobs/{jid}/file',cookie=cookie);self.assertEqual(r['status'],'200 OK')
    r,empty=self.call(f'/api/export-jobs/{jid}/file','HEAD',cookie=cookie)
    self.assertEqual(r['status'],'200 OK');self.assertEqual(empty,b'');self.assertEqual(int(dict(r['headers'])['Content-Length']),len(video))
    r,empty=self.call(f'/api/export-jobs/{jid}/file','HEAD',cookie=cookie,extra={'HTTP_RANGE':'bytes=0-4'})
    self.assertEqual(r['status'],'206 Partial Content');self.assertEqual(empty,b'');self.assertEqual(dict(r['headers'])['Content-Length'],'5')
    output=Path(self.temp.name)/'download.mp4';output.write_bytes(video);self.assertAlmostEqual(float(probe(output)['format']['duration']),2,delta=.2)
 def test_missing_database_is_not_recreated(self):
    self.file.unlink();r,_=self.call('/')
    self.assertEqual(r['status'],'500 Internal Server Error');self.assertFalse(self.file.exists())
 def test_storage_limits_are_admin_configurable(self):
    cookie,csrf=self.login();r,body=self.call('/api/settings/storage',cookie=cookie)
    self.assertEqual(json.loads(body)['minimum_free'],10_000_000_000)
    r,_=self.call('/api/settings/storage','PATCH',{'minimum_free':20_000_000_000,'free_buffer':5_000_000_000},cookie,csrf)
    self.assertEqual(r['status'],'200 OK')
    r,body=self.call('/api/settings/storage',cookie=cookie);self.assertEqual(json.loads(body)['minimum_free'],20_000_000_000)
 def test_storage_limit_defaults_match_loaded_configuration(self):
    self.app=Application(self.file,self.root,self.exports,allowed_origin='https://nvr.example',archive_lock=Path(self.temp.name)/'archive.lock',minimum_free=123,free_buffer=456)
    cookie,_=self.login();data=json.loads(self.call('/api/settings/storage',cookie=cookie)[1])
    self.assertEqual((data['minimum_free'],data['free_buffer']),(123,456));self.assertFalse(data['limits_invalid'])
 def test_corrupt_storage_limits_can_be_seen_and_repaired(self):
    cookie,csrf=self.login();db=database.open_db(self.file)
    with db:db.execute("INSERT INTO system_settings(key,value) VALUES('minimum_free','invalid')")
    db.close();response,body=self.call('/api/settings/storage',cookie=cookie)
    data=json.loads(body);self.assertEqual(response['status'],'200 OK');self.assertTrue(data['limits_invalid']);self.assertIsNone(data['minimum_free'])
    self.assertEqual(self.call('/api/settings/storage','PATCH',{'minimum_free':10_000_000_000,'free_buffer':5_000_000_000},cookie,csrf)[0]['status'],'200 OK')
    self.assertFalse(json.loads(self.call('/api/settings/storage',cookie=cookie)[1])['limits_invalid'])
 def test_storage_limit_change_rechecks_admin_before_write(self):
    from unittest.mock import patch
    cookie,csrf=self.login();original=self.app.json_body
    def revoke(environ,**kwargs):
        data=original(environ,**kwargs);db=database.open_db(self.file)
        with db:db.execute("UPDATE users SET role='viewer' WHERE id=?",(self.uid,))
        db.close();return data
    with patch.object(self.app,'json_body',side_effect=revoke):
        response,_=self.call('/api/settings/storage','PATCH',{'minimum_free':12,'free_buffer':4},cookie,csrf)
    self.assertEqual(response['status'],'403 Forbidden')
    db=database.open_db(self.file);self.assertIsNone(db.execute("SELECT value FROM system_settings WHERE key='minimum_free'").fetchone());db.close()
 def test_recording_health_threshold_is_validated_and_audited(self):
    cookie,csrf=self.login();url='/api/settings/monitoring'
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1]),{'stale_after':900,'invalid':False})
    for value in (59,86401,True,3.5,'1800',None):
        self.assertEqual(self.call(url,'PATCH',{'stale_after':value},cookie,csrf)[0]['status'],'400 Bad Request')
    self.assertEqual(self.call(url,'PATCH',{'stale_after':1800},cookie)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(url,'PATCH',{'stale_after':1800},cookie,csrf)[0]['status'],'200 OK')
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1]),{'stale_after':1800,'invalid':False})
    db=database.open_db(self.file)
    self.assertEqual(db.execute("SELECT count(*) FROM audit_log WHERE action='monitoring.update'").fetchone()[0],1)
    with db:db.execute("UPDATE system_settings SET value='bad' WHERE key='recording_stale_after'")
    db.close()
    self.assertEqual(json.loads(self.call(url,cookie=cookie)[1]),{'stale_after':900,'invalid':True})
 def test_recording_health_setting_rechecks_admin_before_writing(self):
    from unittest.mock import patch
    cookie,csrf=self.login();original=self.app.json_body
    def revoke(environ,**kwargs):
        data=original(environ,**kwargs);db=database.open_db(self.file)
        with db:db.execute("UPDATE users SET role='viewer' WHERE id=?",(self.uid,))
        db.close();return data
    with patch.object(self.app,'json_body',side_effect=revoke):
        self.assertEqual(self.call('/api/settings/monitoring','PATCH',{'stale_after':1800},cookie,csrf)[0]['status'],'403 Forbidden')
    db=database.open_db(self.file)
    self.assertIsNone(db.execute("SELECT value FROM system_settings WHERE key='recording_stale_after'").fetchone());db.close()
 def test_recording_protection_api(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'brama','name':'Brama','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    f=self.root/'brama/2026/11/21/190900.mp4';f.parent.mkdir(parents=True);f.write_bytes(b'video');st=f.stat()
    db=sqlite3.connect(str(self.file));db.execute("INSERT INTO recordings(relative_path,camera,local_time,duration,file_size,mtime_ns,generation) VALUES(?,?,?,?,?,?,?)",('brama/2026/11/21/190900.mp4','brama','2026-11-21T19:09:00',1,st.st_size,st.st_mtime_ns,1));rid=db.execute('SELECT last_insert_rowid()').fetchone()[0];db.execute("UPDATE recordings SET utc_first='2026-11-21T18:09:00+00:00' WHERE id=?",(rid,));db.commit();db.close()
    r,_=self.call(f'/api/recordings/{rid}/protection','POST',{},cookie,csrf);self.assertEqual(r['status'],'200 OK')
    r,body=self.call(f'/api/cameras/{cid}/recordings',cookie=cookie,extra={'QUERY_STRING':'date=2026-11-21'});self.assertTrue(json.loads(body)['recordings'][0]['protected'])
    r,_=self.call(f'/api/recordings/{rid}/protection','DELETE',None,cookie,csrf);self.assertEqual(r['status'],'200 OK')
    r,body=self.call(f'/api/cameras/{cid}/export-protect','POST',{'start_local':'2026-11-21T19:09:00','end_local':'2026-11-21T19:09:01','start_fold':None,'end_fold':None,'gaps':'skip'},cookie,csrf)
    self.assertEqual(r['status'],'200 OK');self.assertEqual(json.loads(body)['files'],1)
    r,body=self.call(f'/api/cameras/{cid}/recordings',cookie=cookie,extra={'QUERY_STRING':'date=2026-11-21'});self.assertTrue(json.loads(body)['recordings'][0]['protected'])
 def test_layout_and_audit_endpoints(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x/test'},cookie,csrf);cid=json.loads(body)['id']
    r,body=self.call('/api/layouts','POST',{'name':'Brama','cameras':[cid]},cookie,csrf);self.assertEqual(r['status'],'201 Created');lid=json.loads(body)['id']
    r,body=self.call('/api/layouts',cookie=cookie);self.assertEqual(json.loads(body)['layouts'][0]['cameras'],[cid])
    r,body=self.call('/api/audit',cookie=cookie);self.assertEqual(json.loads(body)['events'][0]['action'],'camera.create')
    r,_=self.call(f'/api/layouts/{lid}','DELETE',None,cookie,csrf);self.assertEqual(r['status'],'200 OK')
 def test_revocation_during_protection_or_frame_blocks_operation(self):
    from unittest.mock import patch
    from sunhill_nvr import web
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    f=self.root/'cam/2026/11/21/190900.mp4';f.parent.mkdir(parents=True);f.write_bytes(b'video');st=f.stat()
    db=database.open_db(self.file);uid=security.create_user(db,'viewer','viewer-test-password','viewer')
    with db:
        db.execute('INSERT INTO camera_permissions VALUES(?,?,1)',(uid,cid))
        rid=db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',1,?,?,1)",(st.st_size,st.st_mtime_ns)).lastrowid
    db.close()
    r,body=self.call('/api/login','POST',{'username':'viewer','password':'viewer-test-password'})
    viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0];viewer_csrf=json.loads(body)['csrf']
    original=web.open_confined
    def revoke(*args,**kwargs):
        fd,info=original(*args,**kwargs)
        other=database.open_db(self.file)
        with other:other.execute('DELETE FROM camera_permissions WHERE user_id=? AND camera_id=?',(uid,cid))
        other.close();return fd,info
    payload={'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:01Z','gaps':'skip'}
    for path,data in [(f'/api/recordings/{rid}/protection',{}),(f'/api/cameras/{cid}/export-protect',payload)]:
        db=database.open_db(self.file)
        with db:db.execute('INSERT OR REPLACE INTO camera_permissions VALUES(?,?,1)',(uid,cid))
        db.close()
        with patch.object(web,'open_confined',side_effect=revoke):
            self.assertEqual(self.call(path,'POST',data,viewer,viewer_csrf)[0]['status'],'403 Forbidden')
        db=database.open_db(self.file);self.assertEqual(db.execute('SELECT count(*) FROM protected_files').fetchone()[0],0);db.close()
    db=database.open_db(self.file)
    with db:db.execute('INSERT OR REPLACE INTO camera_permissions VALUES(?,?,1)',(uid,cid))
    db.close()
    def frame(*args,**kwargs):
        other=database.open_db(self.file)
        with other:other.execute('DELETE FROM camera_permissions WHERE user_id=? AND camera_id=?',(uid,cid))
        other.close();return b'fake-jpeg'
    with patch('sunhill_nvr.media.frame',side_effect=frame):
        response,body=self.call(f'/api/recordings/{rid}/frame',cookie=viewer,extra={'QUERY_STRING':'offset=0&download=1'})
    self.assertEqual(response['status'],'403 Forbidden');self.assertNotIn(b'fake-jpeg',body)
 def test_range_protection_batches_are_atomic_and_audited(self):
    from unittest.mock import patch
    from sunhill_nvr import protection
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file)
    for second in range(5):
        f=self.root/f'cam/2026/11/21/19090{second}.mp4';f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b'video');st=f.stat()
        db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES(?,?,?,?,1,?,?,1)',(str(f.relative_to(self.root)),'cam',f'2026-11-21T19:09:0{second}',f'2026-11-21T18:09:0{second}+00:00',st.st_size,st.st_mtime_ns))
    db.commit();db.close()
    payload={'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:05Z','gaps':'skip'};original=protection.protect;calls=[]
    def fail_on_last(*args,**kwargs):
        calls.append(1)
        if len(calls)==3:raise ValueError('batch failed')
        return original(*args,**kwargs)
    with patch.object(protection,'MAX_BATCH',2),patch.object(protection,'protect',side_effect=fail_on_last):
        self.assertEqual(self.call(f'/api/cameras/{cid}/export-protect','POST',payload,cookie,csrf)[0]['status'],'400 Bad Request')
    db=database.open_db(self.file);self.assertEqual(db.execute('SELECT count(*) FROM protected_files').fetchone()[0],0);db.close()
    with patch.object(protection,'MAX_BATCH',2):
        response,body=self.call(f'/api/cameras/{cid}/export-protect','POST',payload,cookie,csrf)
    self.assertEqual(response['status'],'200 OK');self.assertEqual(json.loads(body)['protected'],5)
    db=database.open_db(self.file);self.assertEqual(db.execute('SELECT count(*) FROM protected_files').fetchone()[0],5)
    self.assertEqual(db.execute("SELECT count(*) FROM audit_log WHERE action='recording.protect_range'").fetchone()[0],1);db.close()
 def test_audit_failure_rolls_back_protection(self):
    from unittest.mock import patch
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    f=self.root/'cam/2026/11/21/190900.mp4';f.parent.mkdir(parents=True);f.write_bytes(b'video');st=f.stat()
    db=database.open_db(self.file)
    with db:rid=db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES('cam/2026/11/21/190900.mp4','cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',1,?,?,1)",(st.st_size,st.st_mtime_ns)).lastrowid
    db.close()
    payload={'start':'2026-11-21T18:09:00Z','end':'2026-11-21T18:09:01Z','gaps':'skip'}
    for path,data in [(f'/api/recordings/{rid}/protection',{}),(f'/api/cameras/{cid}/export-protect',payload)]:
        with patch.object(self.app,'audit',side_effect=sqlite3.OperationalError('audit unavailable')):
            self.assertEqual(self.call(path,'POST',data,cookie,csrf)[0]['status'],'500 Internal Server Error')
        db=database.open_db(self.file);self.assertEqual(db.execute('SELECT count(*) FROM protected_files').fetchone()[0],0);db.close()
 def test_protection_can_cover_more_than_five_thousand_files(self):
    from datetime import datetime,timedelta
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    folder=self.root/'cam/2026/11/21';folder.mkdir(parents=True);db=database.open_db(self.file);start=datetime(2026,11,21,18,0,0)
    for second in range(5001):
        utc=start+timedelta(seconds=second);local=utc+timedelta(hours=1);f=folder/(local.strftime('%H%M%S')+'.mp4');f.write_bytes(b'x');st=f.stat()
        db.execute('INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES(?,?,?,?,1,?,?,1)',(str(f.relative_to(self.root)),'cam',local.isoformat(),utc.isoformat()+'+00:00',st.st_size,st.st_mtime_ns))
    db.commit();db.close()
    response,body=self.call(f'/api/cameras/{cid}/export-protect','POST',{'start':'2026-11-21T18:00:00Z','end':(start+timedelta(seconds=5001)).isoformat()+'Z','gaps':'skip'},cookie,csrf)
    self.assertEqual(response['status'],'200 OK');self.assertEqual(json.loads(body)['protected'],5001)
    db=database.open_db(self.file);self.assertEqual(db.execute('SELECT count(*) FROM protected_files').fetchone()[0],5001);db.close()
 def test_archive_day_includes_previous_day_clip_crossing_midnight(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=sqlite3.connect(str(self.file));db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?,?,?,?,?,?,?,?)",('cam/2026/11/20/235900.mp4','cam','2026-11-20T23:59:00','2026-11-20T22:59:00+00:00',120,1,1,1));db.commit();db.close()
    r,body=self.call(f'/api/cameras/{cid}/recordings',cookie=cookie,extra={'QUERY_STRING':'date=2026-11-21'})
    self.assertEqual(len(json.loads(body)['recordings']),1)
 def test_home_assistant_token_is_not_exposed(self):
    cookie,csrf=self.login()
    r,_=self.call('/api/settings/home-assistant','PATCH',{'url':'http://192.0.2.1:8123','token':'private-token','enabled':True},cookie,csrf);self.assertEqual(r['status'],'200 OK')
    r,body=self.call('/api/settings/home-assistant',cookie=cookie);self.assertTrue(json.loads(body)['token_configured']);self.assertNotIn(b'private-token',body)
 def test_enabling_home_assistant_sends_current_faults_only_once(self):
    import time
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x','record_mode':'rtsp'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file)
    with db:
        db.execute('UPDATE camera_details SET onvif_url=?,clock_checked=? WHERE camera_id=?',('http://192.0.2.1/onvif',int(time.time()),cid))
        now=int(time.time())
        db.execute("INSERT INTO camera_health VALUES(?, 'stale',1,?)",(cid,now))
        db.execute("INSERT INTO stream_health VALUES(?, 'unavailable',1,?,1)",(cid,now))
        db.execute("INSERT INTO system_health VALUES('clock:'||?, 'clock_unavailable',1)",(cid,))
        db.execute("INSERT INTO system_health VALUES('storage','disk_low',1)")
        db.execute("INSERT INTO system_health VALUES('recorder_process:cam','recording_process_error',1)")
        db.execute("INSERT INTO system_health VALUES('rotation','rotation_ok',1)")
        db.execute("INSERT INTO service_health VALUES('sunhill-indexer','inactive',1,?,1)",(now,))
        db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(?, 'cam','recording',1)",(cid,))
    db.close()
    payload={'url':'http://192.0.2.1:8123','token':'private-token','enabled':True}
    self.assertEqual(self.call('/api/settings/home-assistant','PATCH',payload,cookie,csrf)[0]['status'],'200 OK')
    db=database.open_db(self.file)
    expected={('cam','stale'),('cam','offline'),('cam','clock_unavailable'),('cam','recording_process_error'),('storage','disk_low'),('sunhill-indexer','service_down')}
    self.assertEqual(set(db.execute('SELECT source,state FROM pending_notifications')),expected);db.close()
    self.assertEqual(self.call('/api/settings/home-assistant','PATCH',{**payload,'token':''},cookie,csrf)[0]['status'],'200 OK')
    db=database.open_db(self.file);self.assertEqual(db.execute('SELECT count(*) FROM pending_notifications').fetchone()[0],len(expected));db.close()
 def test_camera_disable_discards_its_queued_alerts_and_gateway_failure_is_visible(self):
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file)
    with db:
        db.execute("INSERT INTO stream_health VALUES(?, 'available',0,1,1)",(cid,))
        db.execute("INSERT INTO system_health VALUES('stream_service','stream_service_error',1)")
        db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(?, 'cam','offline',1)",(cid,))
        db.execute("INSERT INTO pending_notifications(camera_id,source,state,created_at) VALUES(NULL, 'storage','disk_low',1)")
    db.close()
    cameras=json.loads(self.call('/api/cameras',cookie=cookie)[1])['cameras']
    self.assertTrue(cameras[0]['stream_gateway_failed'])
    self.assertEqual(self.call('/api/cameras/'+str(cid),'PATCH',{'enabled':False},cookie,csrf)[0]['status'],'200 OK')
    db=database.open_db(self.file);self.assertEqual(db.execute('SELECT source FROM pending_notifications').fetchall(),[('storage',)]);db.close()
 def test_gateway_recovery_does_not_show_old_camera_status_as_current(self):
    from unittest.mock import patch
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file)
    with db:
        db.execute("INSERT INTO stream_health VALUES(?,'available',0,10,10)",(cid,))
        db.execute("INSERT INTO system_health VALUES('stream_service','stream_service_ok',20)")
    db.close()
    with patch('sunhill_nvr.web.time.time',return_value=30):
        self.assertEqual(json.loads(self.call('/api/cameras',cookie=cookie)[1])['cameras'][0]['stream_health'],'unknown')
        db=database.open_db(self.file)
        with db:db.execute('UPDATE stream_health SET checked_at=21 WHERE camera_id=?',(cid,))
        db.close();self.assertEqual(json.loads(self.call('/api/cameras',cookie=cookie)[1])['cameras'][0]['stream_health'],'available')
    db=database.open_db(self.file)
    with db:db.execute('UPDATE stream_health SET checked_at=10000 WHERE camera_id=?',(cid,))
    db.close()
    with patch('sunhill_nvr.web.time.time',return_value=100):
        # Keep session time real while evaluating just the API's status clock.
        with patch('sunhill_nvr.security.time.time',return_value=100):
            self.assertEqual(json.loads(self.call('/api/cameras',cookie=cookie)[1])['cameras'][0]['stream_health'],'unknown')
 def test_old_camera_and_service_statuses_are_not_presented_as_current(self):
    import time
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    now=int(time.time());db=database.open_db(self.file)
    with db:
        db.execute("INSERT INTO camera_health VALUES(?,'recording',1,?)",(cid,now-181))
        db.execute("INSERT INTO stream_health VALUES(?,'available',0,?,1)",(cid,now-901))
        db.execute("INSERT INTO service_health VALUES('sunhill-index.service','active',0,?,1)",(now-181,))
        db.execute("UPDATE camera_details SET onvif_url='http://cam/onvif/device_service',clock_checked=?,clock_offset=50 WHERE camera_id=?",(now-1801,cid))
        db.execute("INSERT INTO system_health VALUES(?,'clock_skew',1)",('clock:'+str(cid),))
    db.close();camera=json.loads(self.call('/api/cameras',cookie=cookie)[1])['cameras'][0]
    self.assertEqual((camera['health'],camera['stream_health']),('unknown','unknown'))
    status=json.loads(self.call('/api/system/health',cookie=cookie)[1]);service=status['services'][0]
    self.assertEqual((service['fresh'],service['state'],service['last_state']),(False,'unknown','active'))
    self.assertEqual((status['system'][0]['fresh'],status['system'][0]['state']),(False,'unknown'))
    self.assertFalse(json.loads(self.call(f'/api/cameras/{cid}/onvif',cookie=cookie)[1])['fresh'])
    db=database.open_db(self.file)
    with db:
        db.execute('UPDATE camera_health SET last_checked=? WHERE camera_id=?',(now,cid))
        db.execute('UPDATE stream_health SET checked_at=? WHERE camera_id=?',(now,cid))
        db.execute('UPDATE service_health SET checked_at=?',(now,))
        db.execute('UPDATE camera_details SET clock_checked=? WHERE camera_id=?',(now,cid))
    db.close();camera=json.loads(self.call('/api/cameras',cookie=cookie)[1])['cameras'][0]
    self.assertEqual((camera['health'],camera['stream_health']),('recording','available'))
    status=json.loads(self.call('/api/system/health',cookie=cookie)[1]);service=status['services'][0]
    self.assertEqual((service['fresh'],service['state']),(True,'active'))
    self.assertEqual((status['system'][0]['fresh'],status['system'][0]['state']),(True,'clock_skew'))
    self.assertTrue(json.loads(self.call(f'/api/cameras/{cid}/onvif',cookie=cookie)[1])['fresh'])
 def test_stream_freshness_allows_time_to_check_many_cameras(self):
    import time
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    now=int(time.time());db=database.open_db(self.file)
    with db:
        db.execute("INSERT INTO stream_health VALUES(?,'available',0,?,1)",(cid,now-1000))
        for index in range(2,51):
            db.execute('INSERT INTO cameras VALUES(?,?,?,1)',(index,'cam'+str(index),'Camera '+str(index)))
            db.execute("INSERT INTO camera_details(camera_id,main_url,record_mode) VALUES(?,'rtsp://x','none')",(index,))
    db.close();result=json.loads(self.call('/api/cameras',cookie=cookie)[1])['cameras']
    self.assertEqual(next(item for item in result if item['id']==cid)['stream_health'],'available')
 def test_archive_keyset_pages_keep_equal_timestamps_and_survive_deletion(self):
    from unittest.mock import patch
    from urllib.parse import urlencode
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file)
    with db:
        for i in range(5):
            db.execute("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?, 'cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',1,1,1,1)",(f'cam/2026/11/21/file{i}.mp4',))
            db.execute("INSERT INTO event_markers(camera_id,started_at,event_type,source) VALUES(?,1795284540,?,'test')",(cid,'type'+str(i)))
    db.close()
    def get(endpoint,cursor=None):
        query={'date':'2026-11-21'}
        if cursor is not None:query['cursor']=cursor
        response,body=self.call(f'/api/cameras/{cid}/{endpoint}',cookie=cookie,extra={'QUERY_STRING':urlencode(query)})
        self.assertEqual(response['status'],'200 OK');return json.loads(body)
    with patch('sunhill_nvr.web.ARCHIVE_PAGE_SIZE',2):
        first=get('recordings');self.assertEqual(len(first['recordings']),2)
        db=database.open_db(self.file)
        with db:db.execute('DELETE FROM recordings WHERE id=?',(first['recordings'][0]['id'],))
        db.close()
        second=get('recordings',first['next_cursor']);third=get('recordings',second['next_cursor'])
        ids=[x['id'] for page in (first,second,third) for x in page['recordings']]
        self.assertEqual(len(ids),5);self.assertEqual(len(set(ids)),5);self.assertIsNone(third['next_cursor'])
        first=get('events');second=get('events',first['next_cursor']);third=get('events',second['next_cursor'])
        ids=[x['id'] for page in (first,second,third) for x in page['events']]
        self.assertEqual(len(ids),5);self.assertEqual(len(set(ids)),5);self.assertIsNone(third['next_cursor'])
        response,_=self.call(f'/api/cameras/{cid}/recordings',cookie=cookie,extra={'QUERY_STRING':urlencode({'date':'2026-11-21','cursor':'invalid'})})
        self.assertEqual(response['status'],'400 Bad Request')
 def test_archive_over_ten_thousand_recordings_is_not_truncated(self):
    from urllib.parse import urlencode
    cookie,csrf=self.login()
    _,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    db=database.open_db(self.file)
    with db:
        db.executemany("INSERT INTO recordings(relative_path,camera,local_time,utc_first,duration,file_size,mtime_ns,generation) VALUES (?, 'cam','2026-11-21T19:09:00','2026-11-21T18:09:00+00:00',1,1,1,1)",((f'cam/2026/11/21/{i}.mp4',) for i in range(10001)))
    db.close()
    def get(cursor=None):
        query={'date':'2026-11-21'}
        if cursor:query['cursor']=cursor
        response,body=self.call(f'/api/cameras/{cid}/recordings',cookie=cookie,extra={'QUERY_STRING':urlencode(query)})
        self.assertEqual(response['status'],'200 OK');return json.loads(body)
    first=get();self.assertEqual(len(first['recordings']),10000);self.assertIsNotNone(first['next_cursor'])
    second=get(first['next_cursor']);self.assertEqual(len(second['recordings']),1);self.assertIsNone(second['next_cursor'])
    self.assertNotEqual(first['recordings'][-1]['id'],second['recordings'][0]['id'])
 def test_viewer_changes_own_password_requiring_current_password_and_revoking_sessions(self):
    db=database.open_db(self.file);uid=security.create_user(db,'viewer','viewer-old-password','viewer');db.close()
    def viewer_login(password):
        response,body=self.call('/api/login','POST',{'username':'viewer','password':password})
        if response['status']!='200 OK':return response,None,None
        cookie=next(v for k,v in response['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0]
        return response,cookie,json.loads(body)['csrf']
    _,cookie,csrf=viewer_login('viewer-old-password');_,other,_=viewer_login('viewer-old-password')
    r,b=self.call('/api/account/password','POST',{'current':'viewer-old-password','new':'12345'},cookie,csrf)
    self.assertEqual(r['status'],'400 Bad Request');self.assertIn('6 do 1024',b.decode())
    payload={'current':'wrong-password','new':'123456'}
    self.assertEqual(self.call('/api/account/password','POST',payload,cookie)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call('/api/account/password','POST',payload,cookie,csrf)[0]['status'],'401 Unauthorized')
    # The first backoff is one second, which can elapse while PBKDF2 runs or
    # when the test crosses a clock boundary. Test an explicitly active limit.
    db=database.open_db(self.file)
    with db:db.execute("UPDATE auth_attempts SET next_allowed=CAST(strftime('%s','now') AS INTEGER)+60")
    db.close()
    self.assertEqual(self.call('/api/account/password','POST',{**payload,'current':'viewer-old-password'},cookie,csrf)[0]['status'],'401 Unauthorized')
    db=database.open_db(self.file)
    with db:db.execute('DELETE FROM auth_attempts')
    db.close()
    response,_=self.call('/api/account/password','POST',{**payload,'current':'viewer-old-password'},cookie,csrf)
    self.assertEqual(response['status'],'200 OK');self.assertEqual(self.call('/api/me',cookie=cookie)[0]['status'],'401 Unauthorized')
    self.assertEqual(self.call('/api/me',cookie=other)[0]['status'],'401 Unauthorized')
    self.assertEqual(viewer_login('123456')[0]['status'],'200 OK')
    db=database.open_db(self.file);self.assertEqual(db.execute("SELECT count(*) FROM audit_log WHERE user_id=? AND action='user.password.change'",(uid,)).fetchone()[0],1);db.close()
 def test_password_change_rejects_a_session_revoked_during_verification(self):
    from unittest.mock import patch
    cookie,csrf=self.login();db=database.open_db(self.file);token=cookie.split('=',1)[1]
    original=security.password_verify
    def verify(*args):
        result=original(*args)
        with db:db.execute('DELETE FROM sessions WHERE user_id=?',(self.uid,))
        return result
    with patch.object(security,'password_verify',side_effect=verify):
        with self.assertRaises(security.AuthError):security.change_own_password(db,self.uid,token,'password-for-tests','a-new-password-for-tests')
    self.assertTrue(security.password_verify('password-for-tests',db.execute('SELECT password_hash FROM users WHERE id=?',(self.uid,)).fetchone()[0]));db.close()
 def test_admin_can_find_and_reenable_disabled_camera(self):
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    r,_=self.call(f'/api/cameras/{cid}','PATCH',{'enabled':False},cookie,csrf);self.assertEqual(r['status'],'200 OK')
    _,body=self.call('/api/cameras',cookie=cookie);self.assertFalse(json.loads(body)['cameras'][0]['enabled'])
    r,_=self.call('/internal/live-auth',cookie=cookie,extra={'HTTP_X_ORIGINAL_URI':'/live/api/ws?src=cam'});self.assertEqual(r['status'],'403 Forbidden')
    r,_=self.call(f'/api/cameras/{cid}','PATCH',{'enabled':True},cookie,csrf);self.assertEqual(r['status'],'200 OK')
 def test_native_events_options_acl_global_list_and_boxes(self):
    import datetime
    from urllib.parse import urlencode
    cookie,csrf=self.login()
    ids=[]
    for slug in ('first','second'):
        _,body=self.call('/api/cameras','POST',{'slug':slug,'name':slug,'main_url':'rtsp://x'},cookie,csrf);ids.append(json.loads(body)['id'])
    url=f'/api/cameras/{ids[0]}/event-settings';data={'enabled':True,'types':['person'],'retention_days':30}
    self.assertEqual(self.call(url,'PATCH',data,cookie)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call(url,'PATCH',{**data,'retention_days':0},cookie,csrf)[0]['status'],'400 Bad Request')
    self.assertEqual(json.loads(self.call(url,'PATCH',data,cookie,csrf)[1])['types'],list(__import__('sunhill_nvr.camera_events',fromlist=['TYPES']).TYPES))
    _,body=self.call('/api/integrations/events/token','POST',{},cookie,csrf);token=json.loads(body)['token']
    now=datetime.datetime.now(datetime.timezone.utc);stamp=int(now.timestamp())
    payload={'camera':'first','type':'person','time':now.isoformat(),'box':[0.1,0.2,0.3,0.4],'zone':'Brama'}
    self.assertEqual(self.call('/api/integrations/events','POST',{**payload,'box':[0,0,2,2]},extra={'HTTP_AUTHORIZATION':'Bearer '+token})[0]['status'],'400 Bad Request')
    self.assertEqual(self.call('/api/integrations/events','POST',payload,extra={'HTTP_AUTHORIZATION':'Bearer '+token})[0]['status'],'202 Accepted')
    with database.open_db(self.file) as db:
        db.execute("INSERT INTO event_markers(camera_id,started_at,event_type,source) VALUES(?,?,'animal','reolink')",(ids[1],stamp))
        uid=security.create_user(db,'eventviewer','password-for-viewer','viewer');db.execute('INSERT INTO camera_permissions VALUES(?,?,0)',(uid,ids[0]))
    db.close()
    r,b=self.call('/api/login','POST',{'username':'eventviewer','password':'password-for-viewer'},extra={'REMOTE_ADDR':'192.0.2.9'});viewer=next(v for k,v in r['headers'] if k=='Set-Cookie' and v.startswith('sunhill_session=')).split(';')[0]
    from zoneinfo import ZoneInfo
    query=urlencode({'date':now.astimezone(ZoneInfo('Europe/Warsaw')).date().isoformat()})
    self.assertEqual(self.call(url,cookie=viewer)[0]['status'],'403 Forbidden')
    self.assertEqual(self.call('/api/settings/database',cookie=viewer)[0]['status'],'403 Forbidden')
    self.assertEqual(json.loads(self.call('/api/settings/database',cookie=cookie)[1])['events'],2)
    maintenance='/api/settings/database/maintenance'
    self.assertEqual(self.call(maintenance,'POST',{'operation':'vacuum'},cookie,csrf)[0]['status'],'400 Bad Request')
    self.assertEqual(self.call(maintenance,'POST',{'operation':'quick_check'},cookie)[0]['status'],'403 Forbidden')
    self.assertTrue(json.loads(self.call(maintenance,'POST',{'operation':'quick_check'},cookie,csrf)[1])['ok'])
    self.assertTrue(json.loads(self.call(maintenance,'POST',{'operation':'optimize'},cookie,csrf)[1])['ok'])
    events=json.loads(self.call('/api/events',cookie=viewer,extra={'QUERY_STRING':query})[1])['events']
    self.assertEqual(len(events),1);self.assertEqual(events[0]['box'],payload['box']);self.assertEqual(events[0]['zone'],'Brama')
    self.assertEqual(len(json.loads(self.call('/api/events',cookie=cookie,extra={'QUERY_STRING':query})[1])['events']),2)
    self.assertEqual(self.call('/api/events',cookie=viewer,extra={'QUERY_STRING':query+'&camera='+str(ids[1])})[0]['status'],'403 Forbidden')
 def test_event_token_has_narrow_scope_and_can_be_revoked(self):
    import datetime
    cookie,csrf=self.login();_,body=self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf);cid=json.loads(body)['id']
    r,body=self.call('/api/integrations/events/token','POST',{},cookie,csrf);token=json.loads(body)['token'];self.assertEqual(r['status'],'201 Created')
    now=datetime.datetime.now(datetime.timezone.utc).isoformat();payload={'camera':'cam','type':'person','time':now}
    r,_=self.call('/api/integrations/events','POST',payload,extra={'HTTP_AUTHORIZATION':'Bearer '+token,'HTTP_ORIGIN':''});self.assertEqual(r['status'],'202 Accepted')
    r,_=self.call('/api/cameras',extra={'HTTP_AUTHORIZATION':'Bearer '+token});self.assertEqual(r['status'],'401 Unauthorized')
    self.call('/api/integrations/events/token','DELETE',None,cookie,csrf)
    r,_=self.call('/api/integrations/events','POST',payload,extra={'HTTP_AUTHORIZATION':'Bearer '+token,'HTTP_ORIGIN':''});self.assertEqual(r['status'],'401 Unauthorized')
 def test_event_revocation_during_body_read_cannot_publish_marker(self):
    import datetime
    from unittest.mock import patch
    cookie,csrf=self.login()
    self.assertEqual(self.call('/api/cameras','POST',{'slug':'cam','name':'Cam','main_url':'rtsp://x'},cookie,csrf)[0]['status'],'201 Created')
    _,body=self.call('/api/integrations/events/token','POST',{},cookie,csrf);token=json.loads(body)['token']
    payload={'camera':'cam','type':'motion','time':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    real=self.app.json_body
    def revoke(env,**kwargs):
        value=real(env,**kwargs)
        db=database.open_db(self.file)
        try:
            with db:db.execute("DELETE FROM system_settings WHERE key='event_token_hash'")
        finally:db.close()
        return value
    with patch.object(self.app,'json_body',side_effect=revoke):
        self.assertEqual(self.call('/api/integrations/events','POST',payload,extra={'HTTP_AUTHORIZATION':'Bearer '+token})[0]['status'],'401 Unauthorized')
    db=database.open_db(self.file)
    try:self.assertEqual(db.execute('SELECT count(*) FROM event_markers').fetchone()[0],0)
    finally:db.close()
 def test_unauthenticated_rejected(self):
    self.assertEqual(self.call('/api/cameras')[0]['status'],'401 Unauthorized')
if __name__=='__main__':unittest.main()
