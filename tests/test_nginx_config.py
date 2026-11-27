import unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr.nginx_config import render
class NginxTests(unittest.TestCase):
 def test_restricted_proxy(self):
    c=render('nvr.example.com')
    self.assertIn('auth_request /internal/live-auth;',c);self.assertIn('location /live/ { return 404; }',c)
    self.assertIn('listen 127.0.0.1:8080;',c);self.assertNotIn('location /api/streams',c)
    self.assertIn('if (-f /opt/sunhill-nvr/config/maintenance.flag) { return 503; }',c)
    self.assertIn('location = /internal/health { return 404; }',c)
    self.assertIn('proxy_pass http://127.0.0.1:1985/api/ws',c)
    self.assertIn('location ~ "^/live/player/[0-9]{1,12}/(video-stream|video-rtc)\\.js$"',c)
    self.assertIn('proxy_pass http://127.0.0.1:1984/$1.js;',c)
 def test_face_upload_scope(self):
    config=render('nvr.example.com',tls_certificate=Path('/etc/cert.pem'),tls_key=Path('/etc/key.pem'))
    self.assertNotIn('/api/admin/analytics/faces',config)
    self.assertNotIn('client_max_body_size 28m;',config)
    self.assertEqual(config.count('client_max_body_size 16k;'),3)
 def test_direct_lan_ip_even_without_local_tls(self):
    config=render('nvr.example.com')
    self.assertEqual(config.count('listen 80;'),2)
    self.assertNotIn('listen 443 ssl;',config)
    self.assertIn('server_name ~^(?:10[.]|127[.]|192[.]168[.]|172[.](?:1[6-9]|2[0-9]|3[01])[.])[0-9.]+$;',config)
    self.assertNotIn('return 302',config)
    self.assertIn('if ($host != $server_addr) { return 421; }',config)
    self.assertIn('X-Sunhill-LAN-Origin http://$host;',config)
    self.assertIn('allow 192.168.0.0/16;',config);self.assertIn('deny all;',config)
    self.assertNotIn('default_server',config)
 def test_host_cannot_inject_directives(self):
    for host in ('example.com; root /;','https://example.com','x\nserver {}','../etc','localhost'):
        with self.assertRaises(ValueError):render(host)
 def test_hostname_is_normalized_and_other_hosts_are_rejected(self):
    config=render('NVR.Example.COM',tls_certificate=Path('/etc/sunhill/fullchain.pem'),tls_key=Path('/etc/sunhill/key.pem'))
    self.assertEqual(config.count('server_name nvr.example.com;'),3)
    self.assertEqual(config.count('if ($host != nvr.example.com) { return 421; }'),3)
    self.assertIn('return 301 https://nvr.example.com$request_uri;',config)
    self.assertNotIn('return 302',config)
    self.assertIn('if ($host != $server_addr) { return 421; }',config)
    self.assertIn('X-Sunhill-LAN-Origin http://$host;',config)
 def test_lan_tls_requires_both_files_and_no_injection(self):
    with self.assertRaises(ValueError):render('nvr.example.com',tls_certificate=Path('/etc/cert.pem'))
    config=render('nvr.example.com',tls_certificate=Path('/etc/sunhill/fullchain.pem'),tls_key=Path('/etc/sunhill/key.pem'))
    self.assertIn('listen 443 ssl;',config);self.assertIn('ssl_protocols TLSv1.2 TLSv1.3;',config)
    self.assertIn('X-Sunhill-Client-IP $remote_addr;',config);self.assertIn('X-Sunhill-Client-IP $http_cf_connecting_ip;',config)
    with self.assertRaises(ValueError):render('nvr.example.com',tls_certificate=Path('/etc/a;foo'),tls_key=Path('/etc/key'))
if __name__=='__main__':unittest.main()
