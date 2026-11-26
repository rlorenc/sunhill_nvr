"""Privileged processing of archive directory change requests."""
from __future__ import annotations
import json
import logging
import re
import os
import secrets
import stat
import tempfile
import time
from pathlib import Path
from . import storage_transaction
from .release import _snapshot, ReleaseError
from .config import load
from .storage_inbox import normalize
from .storage_system import StorageSystem,MediaSelection
from .upgrade_system import SystemAdapter

def status(adapter: SystemAdapter, state: str, message: str, request_id: str | None=None) -> None:
    if state not in ('applying','done','failed','recovery_required'):raise ValueError('invalid storage status')
    if request_id is None:
        # Preserve the applying ID across recovery after a process/host crash.
        try:
            previous=json.loads(SystemAdapter._read_trusted(adapter.state/'storage-status.json',limit=4096))
            if previous.get('state') in ('applying','recovery_required'):request_id=previous.get('request_id')
        except (OSError,ValueError,AttributeError,ReleaseError):pass
    data=json.dumps({'state':state,'message':message,'updated':int(time.time()),'request_id':request_id},ensure_ascii=False,separators=(',',':')).encode()
    temp=adapter.state/('.storage-status-'+secrets.token_hex(16))
    try:
        SystemAdapter._write_private(temp,data,mode=0o640)
        os.chown(temp,0,adapter.gid);os.chmod(temp,0o640);os.replace(temp,adapter.state/'storage-status.json')
        adapter._sync_parent(adapter.state/'storage-status.json')
    finally:temp.unlink(missing_ok=True)

def process(adapter: SystemAdapter, inbox: Path) -> bool:
    request=inbox/'storage-request.json'
    try:
        info=request.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=adapter.uid or info.st_nlink!=1 or info.st_size>4096:return False
    except FileNotFoundError:return False
    identity=(info.st_dev,info.st_ino);system=StorageSystem(adapter);request_id=None
    try:
        with tempfile.TemporaryDirectory(prefix='.storage-input-',dir=adapter.state) as tmp:
            snapshot=Path(tmp)/'request.json';_snapshot(request,snapshot,4096)
            value=json.loads(snapshot.read_bytes())
            if (not isinstance(value,dict) or not {'archive','user_id'}<=set(value) or not set(value)<={'archive','user_id','exports','staging','request_id'} or not isinstance(value['archive'],str)
                or type(value['user_id']) is not int or value['user_id']<1):raise ValueError('invalid storage request')
            request_id=value.get('request_id')
            if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch('[0-9a-f]{32}',request_id)):
                request_id=None;raise ValueError('invalid request id')
            if request_id is not None:
                # The root journal may already have been finalized after a crash
                # before the inbox entry was removed. Never replay its result.
                try:previous=json.loads(SystemAdapter._read_trusted(adapter.state/'storage-status.json',limit=4096))
                except (OSError,ValueError,ReleaseError):previous={}
                if isinstance(previous,dict) and previous.get('request_id')==request_id and previous.get('state') in ('done','failed'):
                    return True
            path=Path(normalize(value['archive']))
            if 'exports' in value or 'staging' in value:
                def media_path(key,default):
                    val=value.get(key)
                    if val is None:return default
                    return Path(normalize(val))
                path=MediaSelection(path,media_path('exports',load(adapter.config_file).exports),media_path('staging',None))
            candidate=system.candidate(path)
            if candidate==load(adapter.config_file):
                status(adapter,'done','Katalogi są już ustawione. Nie wprowadzono zmian.',request_id)
                return True
            status(adapter,'applying','Zmienianie katalogu nagrań. Nagrywanie jest chwilowo wstrzymane.',request_id)
            storage_transaction.activate(adapter.state,path,system.operations())
        status(adapter,'done','Katalogi zmienione. Stare nagrania i eksporty pozostały na poprzednich ścieżkach.',request_id)
    except Exception as error:
        pending=storage_transaction._read(adapter.state) is not None
        detail=failure_message(error)
        logging.error('Storage change failed: %s (%s)',type(error).__name__,detail)
        status(adapter,'recovery_required' if pending else 'failed',
               'Zmiana katalogu przerwana. Wymagane odzyskiwanie.' if pending else detail,request_id)
    finally:
        try:
            current=request.lstat()
            if (current.st_dev,current.st_ino)==identity:request.unlink()
        except FileNotFoundError:pass
    return True


_MESSAGES={
    'media path must be canonical':'Ścieżka nie może prowadzić przez dowiązanie symboliczne.',
    'protected system path':'Wybrano chroniony katalog systemowy.',
    'application code and private data are not media directories':'Katalog koliduje z kodem lub prywatnymi danymi aplikacji.',
    'media parent missing':'Katalog nadrzędny nie istnieje lub jest dowiązaniem.',
    'media parent not writable':'Użytkownik sunhill nie może utworzyć katalogu w tej lokalizacji.',
    'media directory must belong to sunhill and not be writable by others':'Katalog musi należeć do sunhill i nie może być zapisywalny przez innych.',
    'archive and staging must be on the same filesystem':'Nagrania i bufor muszą być na tym samym systemie plików.',
    'archive, staging and exports must be separate directories':'Wybierz oddzielne katalogi; w nagraniach dopuszczalne są osobne ukryte podkatalogi bufora i eksportów.',
    'staging and exports must be separate directories':'Katalogi bufora i eksportów nie mogą się pokrywać.',
    'old and new archive overlap':'Stary i nowy katalog nagrań nakładają się.',
    'old and new export directories overlap':'Stary i nowy katalog eksportów nakładają się.',
    'new exports overlap old archive':'Nowe eksporty kolidują ze starym archiwum.',
    'new archive overlaps old exports':'Nowe nagrania kolidują ze starymi eksportami.',
    'media and private state paths overlap':'Katalog koliduje z prywatnymi danymi aplikacji.',
    'upgrade system command failed':'Nie powiodła się operacja systemowa lub pomocnicza kontrola zapisu.',
    'services failed after storage change':'Usługi nie przeszły kontroli po zmianie katalogów.',
    'storage change alters recovery unit':'Zmiana wymaga migracji jednostek recovery.',
    'invalid media path':'Nieprawidłowa ścieżka katalogu.',
    'unsupported archive filesystem; a verified local filesystem is required':'Nieobsługiwany system plików; wymagany lokalny dysk.',
    'archive and staging need a common non-root directory':'Nagrania i bufor wymagają wspólnego katalogu nadrzędnego innego niż /.',
}

def failure_message(error):
    current=error;seen=set()
    while current is not None and id(current) not in seen and len(seen)<8:
        seen.add(id(current))
        if type(current) in (ReleaseError,ValueError) and str(current) in _MESSAGES:return _MESSAGES[str(current)]
        if isinstance(current,PermissionError):return 'Brak uprawnień do katalogu.'
        if isinstance(current,FileNotFoundError):return 'Katalog lub wymagany plik nie istnieje.'
        current=current.__cause__
    return 'Nie udało się zmienić katalogów. Sprawdź dziennik sunhill-upgrade-apply.'
