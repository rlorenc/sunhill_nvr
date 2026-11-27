"""Explicit local-admin ONVIF firmware upload. No downloading or auto-updates.

ONVIF Device Management StartFirmwareUpgrade + binary HTTP POST. A successful
POST acknowledges the upload, not the eventual installation. The operator must
obtain the image, target identity and checksum from a trusted vendor source.
"""
from __future__ import annotations
import hashlib
import http.client
import os
import re
import stat
import time
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from .camera_discovery import DEVICE, local, same_camera, soap
from .camera_reboot import target
from .home_assistant import _NoRedirect

MAX_IMAGE=128*1024*1024


class FirmwareError(ValueError):pass


def image(path,sha256):
    if not isinstance(sha256,str) or not re.fullmatch('[0-9a-fA-F]{64}',sha256):raise FirmwareError('Wymagany SHA-256 producenta')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        info=os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=MAX_IMAGE:raise FirmwareError('Nieprawidłowy rozmiar lub typ pliku firmware')
        data=f.read(MAX_IMAGE+1)
    if len(data)!=info.st_size:raise FirmwareError('Niepełny plik firmware')
    return verify_image(data,sha256)


def verify_image(data,sha256):
    if not isinstance(data,bytes) or not 0<len(data)<=MAX_IMAGE or not isinstance(sha256,str) or not re.fullmatch('[0-9a-fA-F]{64}',sha256) or hashlib.sha256(data).hexdigest()!=sha256.lower():raise FirmwareError('Nieprawidłowy plik lub niezgodny SHA-256 firmware')
    return data


def identity(config):
    endpoint,ip,user,password,mode=target(*config)
    try:
        root=soap(endpoint,ip,user,password,DEVICE,'GetDeviceInformation',mode=mode)
        response=local(root,'GetDeviceInformationResponse')
        if response is None:raise ValueError()
        values={}
        for key in ('Manufacturer','Model','HardwareId','FirmwareVersion'):
            item=local(response,key)
            if item is None or not item.text or len(item.text)>256 or any(ord(c)<32 for c in item.text):raise ValueError()
            values[key]=item.text
        return values
    except (OSError,ValueError,ET.ParseError,http.client.HTTPException):raise FirmwareError('Nie można potwierdzić tożsamości kamery') from None


def upload_delay(value):
    if value is None:return 0
    m=re.fullmatch(r'PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?',value)
    if not m or not any(m.groups()):raise FirmwareError('Nieobsługiwany czas oczekiwania kamery')
    delay=int(m[1] or 0)*60+float(m[2] or 0)
    if delay>120:raise FirmwareError('Czas oczekiwania kamery przekracza limit 120 s')
    return delay


def upload(config,data,expected):
    if not isinstance(data,bytes) or not 0<len(data)<=MAX_IMAGE:raise FirmwareError('Nieprawidłowy firmware')
    found=identity(config)
    if set(expected)!={'Manufacturer','Model','HardwareId'} or any(found[k]!=v for k,v in expected.items()):raise FirmwareError('Model, producent lub rewizja sprzętowa niezgodna; nie wysłano firmware')
    endpoint,ip,user,password,mode=target(*config)
    try:
        root=soap(endpoint,ip,user,password,DEVICE,'StartFirmwareUpgrade',mode=mode)
        response=local(root,'StartFirmwareUpgradeResponse')
        if response is None:raise ValueError()
        uri=local(response,'UploadUri');delay=local(response,'UploadDelay')
        if uri is None or not uri.text:raise ValueError()
        uri=uri.text.strip();same_camera(uri,ip,('http','https'))
        if urlsplit(endpoint).scheme=='https' and urlsplit(uri).scheme!='https':raise ValueError()
        seconds=upload_delay(delay.text if delay is not None else None)
        time.sleep(seconds)
        manager=urllib.request.HTTPPasswordMgrWithDefaultRealm()
        if user:manager.add_password(None,uri,user,password)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect(),urllib.request.HTTPDigestAuthHandler(manager))
        request=urllib.request.Request(uri,data=data,method='POST',headers={'Content-Type':'application/octet-stream'})
        with opener.open(request,timeout=120) as result:
            if not 200<=result.status<300:raise ValueError()
            result.read(1024)
    except (OSError,ValueError,ET.ParseError,http.client.HTTPException):raise FirmwareError('Brak potwierdzenia uploadu firmware. Sprawdź kamerę; nie ponawiaj automatycznie.') from None
    return found


def verify_installed(config, expected, version, *, timeout=180, interval=5, read=identity, monotonic=time.monotonic, sleep=time.sleep):
    """Read only. Never repeat the firmware write or reboot the device.

    The vendor's exact target version is required. An unexpected identity
    aborts immediately; a transient offline state is retried within the budget.
    """
    if not isinstance(version,str) or not 1<=len(version)<=256 or any(ord(c)<32 for c in version):raise FirmwareError('Nieprawidłowa oczekiwana wersja firmware')
    if set(expected)!={'Manufacturer','Model','HardwareId'}:raise FirmwareError('Nieprawidłowa tożsamość sprzętu')
    if not 0<=timeout<=600 or not 0<interval<=30:raise ValueError('invalid polling budget')
    deadline=monotonic()+timeout
    while True:
        try:found=read(config)
        except (FirmwareError,OSError):found=None
        if found is not None:
            if any(found.get(key)!=value for key,value in expected.items()):raise FirmwareError('Tożsamość kamery zmieniła się po uploadzie')
            if found.get('FirmwareVersion')==version:return found
        remaining=deadline-monotonic()
        if remaining<=0:return None
        sleep(min(interval,remaining))
