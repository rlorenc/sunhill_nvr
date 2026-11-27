"""Versioned description of the services managed by an application release."""
from __future__ import annotations
import re

NAME=re.compile(r'sunhill-[a-z0-9-]+\.(?:service|timer)\Z')
ACTIVE=(
    'sunhill-activity.service','sunhill-web.service','sunhill-live-proxy.service','sunhill-export.service',
    'sunhill-index.service','sunhill-recorder.service','sunhill-health.service','sunhill-stream-health.service',
    'sunhill-clock.service','sunhill-retention.service','sunhill-retention-preview.service','sunhill-go2rtc.service',
    'sunhill-ftp.service','sunhill-controller.timer',
)
ENABLED=tuple(name for name in ACTIVE if name not in ('sunhill-go2rtc.service','sunhill-ftp.service'))
FTP='sunhill-ftp.service'
FTP_UNITS=frozenset((FTP,'sunhill-ftp-policy.service'))
CORE={'sunhill-activity.service','sunhill-controller.service','sunhill-controller.timer','sunhill-go2rtc.service'}
REQUIRED=CORE|{FTP}
RTSP_ACTIVE=tuple(name for name in ACTIVE if name!=FTP)

def default(*, rtsp_only: bool=False) -> dict:
    # Version 1 remains the fallback for installed releases without a manifest.
    return {'version':2 if rtsp_only else 1,'active':list(RTSP_ACTIVE if rtsp_only else ACTIVE),'enabled':list(ENABLED)}

def validate(value, names, *, transitional: bool=False) -> dict:
    """Validate a signed release's explicit runtime and boot unit lists."""
    if not isinstance(value,dict) or set(value)!={'version','active','enabled'} or type(value['version']) is not int or value['version'] not in (1,2):
        raise ValueError('invalid lifecycle manifest')
    required=REQUIRED if value['version']==1 else CORE
    if not isinstance(names,(list,tuple,set)) or not required.issubset(names):raise ValueError('missing core units')
    for key in ('active','enabled'):
        items=value[key]
        if (not isinstance(items,list) or len(items)>100
            or any(not isinstance(item,str) or not NAME.fullmatch(item) or item.startswith('sunhill-upgrade-') for item in items)
            or len(set(items))!=len(items) or not set(items).issubset(names)):
            raise ValueError('invalid lifecycle unit list')
    if not required.difference({'sunhill-controller.service'}).issubset(value['active']):raise ValueError('missing core runtime')
    if value['version']==2 and (FTP_UNITS.intersection(value['active']) or (not transitional and FTP_UNITS.intersection(names))):
        raise ValueError('FTP is forbidden in RTSP-only lifecycle')
    if 'sunhill-controller.service' in value['active']:raise ValueError('one-shot controller is not a persistent service')
    if not set(value['enabled']).issubset(value['active']):raise ValueError('enabled unit is not active')
    return {'version':value['version'],'active':list(value['active']),'enabled':list(value['enabled'])}
