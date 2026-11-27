"""Generate narrowly scoped HTTP and go2rtc reverse proxy configuration."""
from __future__ import annotations
import re
from pathlib import Path

HOST=re.compile(r'(?=.{1,253}\Z)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}\Z')

def _locations(app_port: int,stream_port: int, *, client_ip: str, hostname: str, lan: bool = False) -> str:
    expected_host='$server_addr' if lan else hostname
    forwarded_proto='http' if lan else 'https'
    lan_origin='http://$host' if lan else '""'
    lan_acl='    allow 10.0.0.0/8;\n    allow 172.16.0.0/12;\n    allow 192.168.0.0/16;\n    allow 127.0.0.0/8;\n    deny all;\n' if lan else ''
    websocket_origin=f'https://{hostname}' if lan else '$http_origin'
    return f'''    if ($host != {expected_host}) {{ return 421; }}
{lan_acl.rstrip()}
    client_max_body_size 16k;
    if (-f /opt/sunhill-nvr/config/maintenance.flag) {{ return 503; }}
    location = /internal/health {{ return 404; }}
    location = /api/settings/tls {{
        client_max_body_size 128k;
        proxy_pass http://127.0.0.1:{app_port};
        proxy_set_header Host $host;
        proxy_set_header Origin $http_origin;
        proxy_set_header X-Forwarded-Proto {forwarded_proto};
        proxy_set_header X-Sunhill-LAN-Origin {lan_origin};
        proxy_set_header X-Sunhill-Client-IP {client_ip};
        proxy_buffering off;
    }}
    location ~ ^/api/update-uploads/(archive|signature)$ {{
        client_max_body_size 201m;
        proxy_request_buffering off;
        proxy_pass http://127.0.0.1:{app_port};
        proxy_set_header Host $host;
        proxy_set_header Origin $http_origin;
        proxy_set_header X-Forwarded-Proto {forwarded_proto};
        proxy_set_header X-Sunhill-LAN-Origin {lan_origin};
        proxy_set_header X-Sunhill-Client-IP {client_ip};
        proxy_read_timeout 1800s;
    }}
    location ~ "^/api/cameras/[1-9][0-9]{{0,9}}/firmware$" {{
        client_max_body_size 128m;
        proxy_request_buffering off;
        proxy_pass http://127.0.0.1:{app_port};
        proxy_set_header Host $host;
        proxy_set_header Origin $http_origin;
        proxy_set_header X-Forwarded-Proto {forwarded_proto};
        proxy_set_header X-Sunhill-LAN-Origin {lan_origin};
        proxy_set_header X-Sunhill-Client-IP {client_ip};
        proxy_read_timeout 300s;
    }}
    location = /internal/live-auth {{
        internal;
        proxy_pass http://127.0.0.1:{app_port}/internal/live-auth;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Original-URI $request_uri;
        proxy_set_header Host $host;
        proxy_set_header X-Sunhill-LAN-Origin {lan_origin};
        proxy_set_header X-Sunhill-Client-IP {client_ip};
        proxy_set_header Origin $http_origin;
        proxy_set_header Cookie $http_cookie;
    }}
    location = /live/talk {{
        proxy_pass http://127.0.0.1:1986/api/talk$is_args$args;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Origin $http_origin;
        proxy_read_timeout 150s;
        proxy_buffering off;
    }}
    location = /live/api/ws {{
        auth_request /internal/live-auth;
        proxy_pass http://127.0.0.1:1985/api/ws$is_args$args;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Origin {websocket_origin};
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_buffering off;
    }}
    location = /live/video-stream.js {{ proxy_pass http://127.0.0.1:{stream_port}/video-stream.js; }}
    location = /live/video-rtc.js {{ proxy_pass http://127.0.0.1:{stream_port}/video-rtc.js; }}
    location ~ "^/live/player/[0-9]{{1,12}}/(video-stream|video-rtc)\\.js$" {{
        proxy_pass http://127.0.0.1:{stream_port}/$1.js;
    }}
    location /live/ {{ return 404; }}
    location / {{
        proxy_pass http://127.0.0.1:{app_port};
        proxy_set_header Host $host;
        proxy_set_header Origin $http_origin;
        proxy_set_header X-Forwarded-Proto {forwarded_proto};
        proxy_set_header X-Sunhill-LAN-Origin {lan_origin};
        proxy_set_header X-Sunhill-Client-IP {client_ip};
        proxy_buffering off;
    }}
'''

def render(hostname: str, *, app_port: int = 8088, stream_port: int = 1984,
           tls_certificate: Path | None = None, tls_key: Path | None = None) -> str:
    if not isinstance(hostname,str) or not HOST.fullmatch(hostname):raise ValueError('invalid hostname')
    hostname=hostname.lower()
    if not 1 <= app_port <= 65535 or not 1 <= stream_port <= 65535:raise ValueError('invalid port')
    if (tls_certificate is None)!=(tls_key is None):raise ValueError('certificate and key required together')
    locations=_locations(app_port,stream_port,client_ip='$http_cf_connecting_ip',hostname=hostname)
    output=f'server {{\n    listen 127.0.0.1:8080;\n    server_name {hostname};\n{locations}}}\n'
    if tls_certificate is not None:
        for path in (tls_certificate,tls_key):
            if not path.is_absolute() or not re.fullmatch(r'/[A-Za-z0-9_./-]+',str(path)) or '..' in path.parts:
                raise ValueError('invalid TLS path')
        local_locations=_locations(app_port,stream_port,client_ip='$remote_addr',hostname=hostname)
        output+=f'''server {{
    listen 443 ssl;
    server_name {hostname};
    ssl_certificate {tls_certificate};
    ssl_certificate_key {tls_key};
    ssl_protocols TLSv1.2 TLSv1.3;
{local_locations}}}
'''
    # Exact hostname and numeric IPv4 Host routing win over the distribution's
    # default site without deleting it or claiming another application's vhost.
    # Direct private IPv4 HTTP is an explicit LAN-only access mode. The public
    # domain remains HTTPS and the tunnel origin remains strictly canonical.
    ip_locations=_locations(app_port,stream_port,client_ip='$remote_addr',hostname=hostname,lan=True)
    output+=f'''server {{
    listen 80;
    server_name {hostname};
    if ($host != {hostname}) {{ return 421; }}
    return 301 https://{hostname}$request_uri;
}}
server {{
    listen 80;
    server_name ~^(?:10[.]|127[.]|192[.]168[.]|172[.](?:1[6-9]|2[0-9]|3[01])[.])[0-9.]+$;
{ip_locations.rstrip()}
}}
'''
    return output
