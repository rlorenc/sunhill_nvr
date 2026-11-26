# Weryfikacja punktów zakresu - 24 listopada 2026

Poniżej zapisano 40 punktów z listy `SPECIFICATION.md` objętych lokalnymi testami
z dwóch kolejnych paczek prac. Oryginalnych 37 pozycji pochodzi z wcześniejszej
paczki, kolejne trzy dopisano przy rozbudowie monitoringu. Tabela nie oznacza,
że wszystkie 40 funkcji zostało dopisanych w tej aktualizacji ani że są gotowe
produkcyjnie. Test HTTP działa na pętli zwrotnej z syntetycznymi nagraniami;
test JavaScript używa uproszczonego DOM. Dodatkowo w przeglądarce wykonano
pojedynczą próbę archiwum i eksportu na serwerze testowym z mapowaniem Origin.

| Punkt | Sprawdzony zakres | Przykładowe dowody |
| --- | --- | --- |
| 7 | Logowanie, zmiana własnego hasła, odmowa dostępu i zamknięcie podglądu po unieważnieniu sesji | `test_http_archive_integration.py`, `test_security.py`, `test_web.py`, `test_live_proxy.py` |
| 10 | Oddzielne konta i sesje | `test_security.py`, `test_web.py` |
| 11 | Uprawnienia i ich cofnięcie przy pobieraniu | `test_web.py`, `test_live_proxy.py` |
| 15 | Tworzenie i edycja kamer | `test_web.py`, `test_cameras.py` |
| 17 | Walidacja strumieni i kodowanie danych kamery | `test_cameras.py` |
| 18 | Autoryzacja live i dodatkowy wariant zgodności H.264/AAC | `test_live_proxy.py`, `test_cameras.py`; rzeczywistego go2rtc jeszcze nie sprawdzono |
| 21 | Wybór strumienia, w tym nowy wariant zgodności | `test_cameras.py`, przegląd `static/app.js`; wymagany test przeglądarki |
| 24 | Konfiguracja FTP i zamykanie zapisu przy braku miejsca | `test_ftp_accounts.py`, `test_controller.py`; wymagany ProFTPD na Ubuntu |
| 25 | Konta wspólne i przypisane do kamery | `test_ftp_accounts.py`, `test_config_reconcile.py` |
| 28 | Izolacja błędu katalogu i wykrywanie awarii procesu nagrywania dla jednej kamery RTSP | `test_supervisor.py`, `test_cli.py` |
| 29 | Kopiowanie obrazu i awaryjne AAC dźwięku | `test_recorder.py`, `test_workers.py` |
| 30 | Ponawianie i korekta zegara wstecz | `test_supervisor.py`, `test_recorder.py` |
| 31 | Tworzenie segmentów i publikacja | `test_recorder.py`, `test_supervisor.py` |
| 32 | Kopiowanie obrazu podczas zapisu | `test_recorder.py` |
| 34 | Struktura archiwum kamera/data/plik | `test_recorder.py`, `test_archive.py` |
| 35 | Indeksowanie, ponowne sprawdzenie i wykrycie podmiany pliku | `test_archive.py`, `test_web.py` |
| 36 | Nazwy Reolink i krótkie nazwy z wcześniejszego skryptu | `test_rotation.py`, `test_archive.py` |
| 39 | Archiwum wybranej kamery | `test_http_archive_integration.py` |
| 40 | Kalendarz dni i stronicowanie, również powyżej 10 000 nagrań | `test_http_archive_integration.py`, `test_web.py`, `browser_smoke.js` |
| 41 | Zawartość osi i przerwy | `test_export_plan.py`, `browser_smoke.js`; uproszczony DOM |
| 42 | Wybór wystąpienia powtórzonej godziny | `test_timeline.py`, `browser_smoke.js` |
| 43 | Przejście między plikami i przez granicę dnia | `browser_smoke.js`; uproszczony DOM |
| 44 | Miniatura z nagrania | `test_http_archive_integration.py`, `test_media.py` |
| 45 | Przewijanie przez północ | `browser_smoke.js`; uproszczony DOM |
| 48 | Przygotowanie synchronizacji wielu kamer | `browser_smoke.js`; wymagany test rzeczywistych przeglądarek |
| 49 | Pobranie klatki | `test_media.py`, `test_web.py` |
| 51 | Oryginalny plik, zakres HTTP i HEAD | `test_http_archive_integration.py`, `test_web.py` |
| 52 | Zaznaczenie początku i końca, odporność na zmianę pozycji odtwarzacza | `test_export_plan.py`, `browser_smoke.js` i jedna próba w przeglądarce z danymi syntetycznymi |
| 53 | Jeden MP4 z 17 segmentów, dźwiękiem i przejściem przez północ | `test_export_service.py` z FFmpeg |
| 54 | Przycięcie zakresu | `test_export_plan.py`, `test_media.py` |
| 55 | Przerwy i przewidywany czas przed eksportem | `test_export_plan.py`, `test_web.py` |
| 56 | Czarna plansza lub pominięcie przerw | `test_media.py`, `test_export_service.py` |
| 57 | Oryginały zachowane przy eksporcie | `test_export_service.py`, `test_http_archive_integration.py` |
| 58 | Kolejka i blokada równoczesnych pracowników | `test_jobs.py` |
| 59 | Postęp i anulowanie także w trakcie pracy | `test_jobs.py`, `test_export_service.py`, `test_web.py`, `browser_smoke.js` |
| 60 | Sprzątanie nieudanych eksportów i starych pustych sesji | `test_export_service.py`, `test_staging_cleanup.py` |
| 61 | Kontrola wolnego miejsca przed i podczas eksportu | `test_export_service.py` |
| 76 | Alarm procesu RTSP, zachowanie po restarcie i powrót po poprawnym segmencie | `test_supervisor.py`, `test_cli.py`, `test_workers.py`, `browser_smoke.js` |
| 77 | Kolejka powiadomień, ponawianie i odtworzenie aktualnych awarii po włączeniu integracji | `test_health_service.py`, `test_web.py`; brak próby z rzeczywistym HA |
| 78 | Obsługa ONVIF, błędnych odpowiedzi i powrotu po awarii | `test_onvif_time.py`, `test_clock_service.py`; brak próby z kamerą |

W testach lokalnych objęto także punkty 62-70 oraz 73-75 i 79 dotyczące retencji, dysku,
monitoringu, Home Assistanta oraz czasu. Zmiana wstrzymuje nowe zapisy
poniżej limitu, a przy skonfigurowanym zapasie rozpoczyna rotację wcześniej.
Ponieważ kontrole działają okresowo i inne procesy także mogą zajmować dysk,
nie jest to gwarancja ścisłego utrzymania 10 GB w każdej sekundzie. Wymaga
testów na docelowym serwerze, z prawdziwymi zapisami FTP i RTSP.

W dalszym ciągu brakuje pełnej weryfikacji instalacji i aktualizacji na Ubuntu,
prób kamer i kodeków, prawdziwego ProFTPD, przeglądarek mobilnych, Cloudflare
Tunnel oraz końcowych dwóch przeglądów całego systemu. Nie oznaczono żadnego
punktu jako produkcyjnie zakończonego na podstawie samych testów lokalnych.
