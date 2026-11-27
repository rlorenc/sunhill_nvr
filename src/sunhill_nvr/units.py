"""Systemd unit generation; values are validated and systemd-escaped."""
from __future__ import annotations
import os
from pathlib import Path
from .config import Config

def systemd_path(path: Path) -> str:
    value=str(path)
    if not path.is_absolute() or '\n' in value or '\r' in value or '\0' in value:
        raise ValueError('unsafe path')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'

def worker(name: str, command: str, config: Config, *, write_paths: tuple[Path,...], root: bool=False) -> str:
    if name not in ('export','index','retention','retention-preview','recorder','health','stream-health','clock','activity','controller','web','live-proxy','go2rtc'):
        raise ValueError('unknown service')
    if '\n' in command or '\r' in command:raise ValueError('unsafe command')
    user='root' if root else 'sunhill'
    media_paths={config.archive,config.staging,config.exports,Path(os.path.commonpath((str(config.archive),str(config.staging))))}
    writable=' '.join(('-' if p in media_paths else '')+systemd_path(p) for p in dict.fromkeys(write_paths))
    runtime=''
    if name=='activity':
        parent=config.activity_socket.parent
        if parent.parent!=Path('/run') or not parent.name or not parent.name.replace('-','').replace('_','').isalnum():
            raise ValueError('activity socket must be in a named /run directory')
        runtime=f'RuntimeDirectory={parent.name}\nRuntimeDirectoryMode=0750\n'
    return f'''[Unit]
Description=Sunhill NVR {name}
Requires=sunhill-upgrade-recover.service
After=network-online.target sunhill-upgrade-recover.service
Wants=network-online.target

[Service]
Type=simple
User={user}
Group=sunhill
Environment="PYTHONPATH=/opt/sunhill-nvr/current/app"
Environment="PYTHONDONTWRITEBYTECODE=1"
Environment="SUNHILL_CONFIG=/opt/sunhill-nvr/config/config.json"
Environment="TZ={config.timezone}"
UMask=0077
{runtime}ExecStart={command}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=false
ReadWritePaths={writable}

[Install]
WantedBy=multi-user.target
'''

def render(config: Config, *, rtsp_only: bool=True) -> dict[str,str]:
    py='/usr/bin/python3 -m sunhill_nvr.cli'
    state=config.database.parent;lock=config.archive_lock.parent
    recorder_root=Path(os.path.commonpath((str(config.archive),str(config.staging))))
    if recorder_root==Path('/'):raise ValueError('archive and staging need a common non-root directory')
    units={}
    for name,command,paths in [
        ('web','/usr/bin/gunicorn --bind 127.0.0.1:8088 --workers 2 --threads 4 sunhill_nvr.wsgi:application',(state,lock)),
        ('live-proxy','/usr/bin/python3 -m sunhill_nvr.live_proxy',(state,)),
        ('export',py+' export-worker',(state,lock,config.exports)),
        ('index',py+' index-worker',(state,lock,config.archive)),
        ('retention',py+' retention-worker --apply',(state,lock,config.archive)),
        ('retention-preview',py+' retention-preview-worker',(state,lock)),
        # One systemd bind mount must cover both trees. Separate bind mounts can
        # make atomic hard-link publication fail with EXDEV even on one device.
        ('recorder',py+' recorder-worker',(state,lock,recorder_root)),
        ('health',py+' health-worker',(state,)),
        ('stream-health',py+' stream-health-worker',(state,)),
        ('clock',py+' clock-worker',(state,)),
        ('activity',py+' activity-helper',(config.activity_socket.parent,)),
        ('go2rtc','/opt/sunhill-nvr/bin/go2rtc -config /opt/sunhill-nvr/control/current/go2rtc.json',(state,)),
    ]:
        units[f'sunhill-{name}.service']=worker(name,command,config,write_paths=paths,root=(name=='activity'))
    units['sunhill-go2rtc.service']=units['sunhill-go2rtc.service'].replace('ExecStart=/opt/sunhill-nvr/bin/go2rtc', 'ExecCondition=/usr/bin/test -s /opt/sunhill-nvr/control/current/go2rtc.json\nExecStart=/opt/sunhill-nvr/bin/go2rtc')
    # The privileged controller is timer-driven and must run once, not forever.
    controller=worker('controller',py+' control-rtsp-once',config,write_paths=(state,config.control_state,Path('/run')),root=True)
    controller=controller.replace('ExecStart=', 'ExecStartPre=+'+py+' prepare-storage\nExecStart=',1)
    units['sunhill-controller.service']=controller.replace('Type=simple','Type=oneshot').replace('Restart=on-failure\nRestartSec=5\n','')
    units['sunhill-controller.timer']='''[Unit]\nDescription=Refresh Sunhill camera configuration\nRequires=sunhill-upgrade-recover.service\nAfter=sunhill-upgrade-recover.service\n\n[Timer]\nOnBootSec=10s\nOnUnitActiveSec=15s\nUnit=sunhill-controller.service\n\n[Install]\nWantedBy=timers.target\n'''
    recovery_command='/usr/bin/python3 -m sunhill_nvr.upgrade_admin'
    protected_paths=' '.join(systemd_path(p) for p in (Path('/opt/sunhill-nvr'),Path('/opt/sunhill-nvr/config'),Path('/etc/systemd/system'),Path('/etc/nginx/conf.d'),config.control_state,config.database.parent))
    units['sunhill-upgrade-recover.service']=f'''[Unit]
Description=Recover interrupted Sunhill updates before starting services
Before=nginx.service sunhill-web.service sunhill-activity.service sunhill-controller.service sunhill-controller.timer

[Service]
Type=oneshot
RemainAfterExit=yes
User=root
Group=sunhill
Environment="PYTHONPATH=/opt/sunhill-nvr/recovery/app"
Environment="PYTHONDONTWRITEBYTECODE=1"
UMask=0077
ExecStart={recovery_command} recover-offline
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=false
ReadWritePaths={protected_paths}
'''
    units['sunhill-upgrade-ready.service']=f'''[Unit]
Description=Finish Sunhill update recovery after service checks
Wants=nginx.service sunhill-controller.service sunhill-web.service sunhill-recorder.service sunhill-controller.timer
After=nginx.service sunhill-controller.service sunhill-web.service sunhill-recorder.service sunhill-controller.timer

[Service]
Type=oneshot
User=root
Group=sunhill
Environment="PYTHONPATH=/opt/sunhill-nvr/recovery/app"
Environment="PYTHONDONTWRITEBYTECODE=1"
UMask=0077
ExecStart={recovery_command} finalize
Restart=on-failure
RestartSec=30
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=false
ReadWritePaths={protected_paths}

[Install]
WantedBy=multi-user.target
'''
    units['sunhill-upgrade-apply.service']=f'''[Unit]
Description=Apply a Sunhill update request
Requires=sunhill-upgrade-recover.service
After=sunhill-upgrade-recover.service

[Service]
Type=oneshot
User=root
Group=sunhill
Environment="PYTHONPATH=/opt/sunhill-nvr/recovery/app"
Environment="PYTHONDONTWRITEBYTECODE=1"
UMask=0077
ExecStart={recovery_command} process-inbox
TimeoutStartSec=1800
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=false
ReadWritePaths={protected_paths}
'''
    units['sunhill-upgrade-apply.timer']='''[Unit]
Description=Check for Sunhill update requests

[Timer]
OnBootSec=30s
OnUnitActiveSec=15s
Unit=sunhill-upgrade-apply.service

[Install]
WantedBy=timers.target
'''
    return units
