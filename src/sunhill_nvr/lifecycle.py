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
REQUIRED={'sunhill-activity.service','sunhill-controller.service','sunhill-controller.timer','sunhill-go2rtc.service','sunhill-ftp.service'}

def default() -> dict:
    return {'version':1,'active':list(ACTIVE),'enabled':list(ENABLED)}

def validate(value, names) -> dict:
    """Validate a signed release's explicit runtime and boot unit lists."""
    if not isinstance(value,dict) or set(value)!={'version','active','enabled'} or type(value['version']) is not int or value['version']!=1:
        raise ValueError('invalid lifecycle manifest')
    if not isinstance(names,(list,tuple,set)) or not REQUIRED.issubset(names):raise ValueError('missing core units')
    for key in ('active','enabled'):
        items=value[key]
        if (not isinstance(items,list) or len(items)>100
            or any(not isinstance(item,str) or not NAME.fullmatch(item) or item.startswith('sunhill-upgrade-') for item in items)
            or len(set(items))!=len(items) or not set(items).issubset(names)):
            raise ValueError('invalid lifecycle unit list')
    if not REQUIRED.difference({'sunhill-controller.service'}).issubset(value['active']):raise ValueError('missing core runtime')
    if 'sunhill-controller.service' in value['active']:raise ValueError('one-shot controller is not a persistent service')
    if not set(value['enabled']).issubset(value['active']):raise ValueError('enabled unit is not active')
    return {'version':1,'active':list(value['active']),'enabled':list(value['enabled'])}
