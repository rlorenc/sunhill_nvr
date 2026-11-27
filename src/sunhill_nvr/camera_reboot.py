"""Explicit, single-attempt ONVIF device reboot (administrator only at API boundary)."""
from urllib.parse import urlsplit
import http.client
import xml.etree.ElementTree as ET
from .camera_discovery import validate_input, same_camera, soap, DEVICE, SOAP
from .onvif_time import validate_endpoint


class RebootError(ValueError):
    pass


def target(main_url, endpoint, username, password, mode):
    """Do not follow camera-provided or client-supplied alternative addresses."""
    try:
        parts = urlsplit(main_url)
        if parts.scheme not in ('rtsp', 'rtsps') or not endpoint:
            raise ValueError()
        username, password = username or '', password or ''
        ip = validate_input(parts.hostname, username, password)
        validate_endpoint(endpoint)
        same_camera(endpoint, ip, ('http', 'https'))
        if mode not in ('digest', 'wsse') or (mode == 'wsse' and not username):
            raise ValueError()
    except (TypeError, ValueError):
        raise RebootError('Restart wymaga zapisanej usługi ONVIF pod prywatnym adresem IP tej samej kamery.') from None
    return endpoint, ip, username, password, mode


def reboot(config):
    endpoint, ip, username, password, mode = target(*config)
    # Never retry a write: a lost response can mean the camera is already rebooting.
    try:
        root = soap(endpoint, ip, username, password, DEVICE, 'SystemReboot', mode=mode)
        body = root.find('{%s}Body' % SOAP)
        if body is None or body.find('{%s}SystemRebootResponse' % DEVICE) is None:
            raise ValueError()
    except (OSError, ValueError, ET.ParseError, http.client.HTTPException):
        raise RebootError('Brak potwierdzenia restartu z kamery. Sprawdź jej stan; polecenie nie zostanie automatycznie ponowione.') from None
