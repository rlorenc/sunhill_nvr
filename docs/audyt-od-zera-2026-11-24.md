## Niezależny audyt paczki i cykle napraw - 24.11.2026

Baza `f35ae87`, odtworzona z dostarczonej paczki i Git bundle. Trzy commity kodu i testów: `560d2b4`, `628cbfc`, `4128aed`. Wykryto i naprawiono rozbieżność autoryzacji strumienia z aktywowaną konfiguracją, nieatomową publikację pierwszej bazy, niezamykanie połączeń przy błędzie inicjalizacji WAL oraz zbyt słabą kontrolę gotowości aktualizacji/odzyskiwania. Niezależna regresja bazowa: 584 testy, 580 OK, 4 pominięte. Regresja po poprawkach: 589 testów, 585 OK, 4 pominięte, 0 błędów i niepowodzeń (101.004 s). JavaScript DOM, składnia JS i skryptów instalacji OK. **Statusy specyfikacji bez zmian: 73 K / 7 C / 0 N; nie jest to niezależne potwierdzenie pełnej implementacji wszystkich 80 punktów.** Zakres dowodów, ryzyka i ograniczenia: [raport audytu](docs/audyt-od-zera-2026-11-24.md). Brak docelowego Ubuntu/systemd, realnego go2rtc, ProFTPD, przeglądarek i kamer; nie wykonano push. W logach nadal ostrzeżenia ResourceWarning SQLite; część fixture nie zamyka połączeń, komplet źródeł nieustalony. Poniżej raporty historyczne.

### Rzeczywiste zmiany i próby

| Usterka | Poprawka | Dowód |
| --- | --- | --- |
| Baza z nowym źródłem lub identyfikatorem kamery, go2rtc nadal ze starym mapowaniem | Walidacja root-published rewizji oraz oczekiwanych źródeł, odmowa w trakcie aktywacji, związanie WebSocket z tożsamością | `560d2b4`, 9 testów relay, lokalny protokół WebSocket z atrapą go2rtc |
| Awaria między hard-link i unlink podczas pierwszej instalacji pozostawia bazę odrzuconą przez open_db | Linux renameat2(RENAME_NOREPLACE), fsync i kontrola checkpoint | `628cbfc`, 13 testów CLI |
| Wyjątek PRAGMA po nawiązaniu połączenia SQLite i brak rzeczywistego WAL | Zamknięcie połączenia przy wyjątku, odmowa pracy bez WAL | `628cbfc`, 10 testów DB |
| Aktywne procesy, ale brak odpowiedzi FTP/go2rtc lub nieaktywny Nginx | Wymagane odpowiedzi protokołów i aktywność Nginx przed zakończeniem aktualizacji / odzyskiwania | `4128aed`, 32 testy adapterów i transakcji |

Weryfikacja konfiguracji gateway jest wykonywana bez ujawniania URL kamer; zmiana konfiguracji FTP może również przejściowo zamknąć podgląd (wspólna rewizja), to świadome fail-closed. Kontrola WebSocket jest okresowa (15 sekund w produkcji); nie gwarantuje natychmiastowego cofnięcia w trakcie pojedynczego interwału. Odpowiedź portu gateway nie dowodzi poprawnego dekodowania audio. Nowy kod mechanizmu odzyskiwania w istniejącej instalacji wymaga odpowiedniej podpisanej migracji. Nie zmieniono stabilnego kontraktu czterech jednostek recovery.

### Indeks lokalnych dowodów dla wszystkich 80 wymagań

Poniższa tabela jest indeksem uruchomionych grup testów oraz ograniczeń, nie wynikiem 80 niezależnych prób end-to-end. Każda grupa może dotyczyć więcej niż jednego punktu; liczba obejmuje cały moduł, nie liczbę testów danego wymagania. `browser_smoke.js` to atrapa DOM. `test_ubuntu_config` to cztery pominięte próby. Oznaczenia K/C odziedziczono ze specyfikacji, nie podwyższono ich w tym audycie. Kod wszystkich wymagań nie został wyczerpująco przejrzany linia po linii. Wnioski o usterkach opierają się na sprawdzonych ścieżkach produkcyjnych oraz testach celowanych.

| Punkt | Status spec. | Uruchomione grupy testów (liczba testów modułu) | Zakres dowodu / ograniczenie |
| --- | --- | --- | --- |
| 1 | C | `test_install_preflight` (6), `test_install_gate` (1), `test_install_helpers` (12), `test_install_health` (3), `test_install_download` (3), `test_units` (3) | instalator, start VM niepotwierdzony |
| 2 | C | `test_install_download` (3), `test_install_helpers` (12), `test_install_health` (3), `test_controller` (13), `test_ubuntu_config` (4, pominięte) | go2rtc i FTP nieuruchomione |
| 3 | K | `test_config` (4), `test_domain_change` (12), `test_tls_apply` (7), `test_recovery_units` (6) | zmiana domeny na systemd otwarta |
| 4 | K | `test_cli` (13), `test_web` (86), `test_database` (10) | pierwszy admin, atomowość DB |
| 5 | C | `test_upgrade_system` (18), `test_upgrade_transaction` (12), `test_upgrade_integration` (6), `test_upgrade_database` (3), `test_recovery_migration` (7), `test_recovery_units` (6) | podpisana aktualizacja na VM otwarta |
| 6 | K | `test_units` (3), `test_controller` (13), `test_install_health` (3), `test_supervisor` (19) | restart rzeczywistego systemu otwarty |
| 7 | K | `test_security` (7), `test_web` (86) | sesje, logowanie |
| 8 | K | `test_web` (86), `browser_smoke.js` (DOM OK) | panel admin, DOM nie jest przeglądarką |
| 9 | K | `test_web` (86), `browser_smoke.js` (DOM OK) | panel użytkownika, DOM nie jest przeglądarką |
| 10 | K | `test_security` (7), `test_web` (86) | izolacja kont |
| 11 | K | `test_security` (7), `test_web` (86), `test_live_proxy` (9) | uprawnienia i nowa autoryzacja |
| 12 | K | `test_nginx_config` (4), `test_nginx_http_integration` (1) | lokalny Nginx, rzeczywisty tunel otwarty |
| 13 | K | `test_tls` (4), `test_tls_apply` (7), `test_nginx_http_integration` (1) | LAN TLS, urządzenia otwarte |
| 14 | K | `test_live_proxy` (9), `test_web` (86), `test_nginx_config` (4) | relay i kontrola sesji, go2rtc otwarty |
| 15 | K | `test_cameras` (4), `test_web` (86), `test_config_reconcile` (11) | CRUD kamer |
| 16 | K | `test_cameras` (4), `test_web` (86) | konfiguracja ponad 100 kamer, brak testu 120 równoczesnych streamów |
| 17 | K | `test_cameras` (4), `test_web` (86), `test_config_reconcile` (11) | konfiguracja i aliasy |
| 18 | C | `test_live_proxy` (9), `test_cameras` (4), `browser_smoke.js` (DOM OK) | relay i UI; dekodowanie audio go2rtc otwarte |
| 19 | K | `browser_smoke.js` (DOM OK), `test_web` (86) | siatka DOM, nie test odtwarzania wielu kamer |
| 20 | K | `browser_smoke.js` (DOM OK) | powiększenie UI, nie test urządzenia |
| 21 | K | `test_cameras` (4), `test_live_proxy` (9), `browser_smoke.js` (DOM OK) | wybór aliasu i kontrola dostępu |
| 22 | K | `test_web` (86), `browser_smoke.js` (DOM OK) | układy i izolacja |
| 23 | C | `browser_smoke.js` (DOM OK) | symulowany DOM, desktop/iPhone/tablet otwarte |
| 24 | C | `test_ftp_accounts` (7), `test_controller` (13), `test_config_reconcile` (11), `test_ubuntu_config` (4, pominięte) | konfiguracja FTP, demon nieuruchomiony |
| 25 | K | `test_ftp_accounts` (7), `test_web` (86) | konta wspólne i dedykowane |
| 26 | K | `test_ftp_accounts` (7), `test_web` (86), `test_controller` (13) | przygotowanie katalogów, rzeczywisty chroot otwarty |
| 27 | C | `test_ftp_accounts` (7), `test_archive` (18), `test_web` (86) | formaty i konfiguracja; fizyczne kamery otwarte |
| 28 | K | `test_recorder` (16), `test_supervisor` (19), `test_rtsp_integration` (2) | rzeczywiste RTSP/FFmpeg z syntetycznym źródłem |
| 29 | K | `test_recorder` (16), `test_supervisor` (19), `test_rtsp_integration` (2) | obraz i audio AAC, test 24h otwarty |
| 30 | K | `test_supervisor` (19), `test_rtsp_integration` (2) | ponowne połączenie, reboot VM otwarty |
| 31 | K | `test_recorder` (16), `test_supervisor` (19) | segmentowanie |
| 32 | K | `test_recorder` (16), `test_rtsp_integration` (2) | copy video i fallback audio |
| 33 | K | `test_storage_workflow` (1), `test_storage_system` (2), `test_storage_transaction` (4) | zmiana katalogu, rzeczywisty mount otwarty |
| 34 | K | `test_archive` (18), `test_recorder` (16), `test_files` (8) | struktura archiwum |
| 35 | K | `test_archive` (18), `test_workers` (19) | indeks istniejących plików |
| 36 | K | `test_archive` (18), `test_files` (8) | rozpoznawanie nazw |
| 37 | K | `test_archive` (18), `test_timeline` (3), `test_web` (86) | czas i data |
| 38 | K | `test_archive` (18), `test_files` (8) | oryginalne nazwy |
| 39 | K | `test_web` (86), `test_http_archive_integration` (1), `browser_smoke.js` (DOM OK) | archiwum, HTTP |
| 40 | K | `test_web` (86), `test_archive` (18), `browser_smoke.js` (DOM OK) | kalendarz |
| 41 | K | `test_export_plan` (6), `test_timeline` (3), `test_web` (86), `browser_smoke.js` (DOM OK) | oś i przerwy |
| 42 | K | `test_web` (86), `test_timeline` (3), `browser_smoke.js` (DOM OK) | klik, DST |
| 43 | K | `test_web` (86), `browser_smoke.js` (DOM OK) | przejście między plikami |
| 44 | K | `test_media` (11), `test_web` (86), `browser_smoke.js` (DOM OK) | miniatury i klatki |
| 45 | K | `test_web` (86), `test_timeline` (3), `browser_smoke.js` (DOM OK) | przewijanie i granice dnia |
| 46 | K | `browser_smoke.js` (DOM OK) | kontrolka prędkości, odtwarzanie rzeczywiste otwarte |
| 47 | K | `test_web` (86), `browser_smoke.js` (DOM OK) | ostatnie 5/15/30 minut, odtwarzacz otwarty |
| 48 | K | `test_web` (86), `test_timeline` (3), `browser_smoke.js` (DOM OK) | UTC, wiele archiwów, urządzenia otwarte |
| 49 | K | `test_media` (11), `test_http_archive_integration` (1) | ekstrakcja JPEG, HTTP |
| 50 | K | `test_web` (86), `test_monitoring_http_integration` (5) | znaczniki i cofnięcie tokenu |
| 51 | K | `test_web` (86), `test_http_archive_integration` (1) | oryginalny plik, HTTP |
| 52 | K | `browser_smoke.js` (DOM OK), `test_web` (86), `test_export_plan` (6) | wybór zakresu |
| 53 | K | `test_export_service` (29), `test_media` (11) | wielodniowy zakres z realnym FFmpeg |
| 54 | K | `test_media` (11), `test_export_plan` (6) | cięcie i kodowanie |
| 55 | K | `test_export_plan` (6), `test_web` (86) | przerwy i długość |
| 56 | K | `test_export_plan` (6), `test_media` (11), `test_export_service` (29) | pomijanie i wypełnianie przerw, pełne wielodniowe renderowanie otwarte |
| 57 | K | `test_export_service` (29), `test_media` (11) | ochrona źródła |
| 58 | K | `test_jobs` (6), `test_export_service` (29) | kolejka, lease |
| 59 | K | `test_jobs` (6), `test_web` (86), `test_http_archive_integration` (1) | status i pobranie |
| 60 | K | `test_export_service` (29), `test_staging_cleanup` (3) | sprzątanie plików i aktywność |
| 61 | K | `test_export_service` (29), `test_storage` (3), `test_limits` (2) | rezerwa i bramka zapisu |
| 62 | K | `test_limits` (2), `test_retention` (16), `test_web` (86) | domyślna rezerwa |
| 63 | K | `test_retention` (16), `test_confined_unlink` (4), `test_fd_mount` (3) | granice i usuwanie |
| 64 | K | `test_rotation` (11), `test_retention` (16), `test_archive` (18) | kolejność UTC |
| 65 | K | `test_rotation` (11), `test_retention` (16) | kontrola wolnego miejsca |
| 66 | K | `test_rotation` (11), `test_retention` (16), `test_limits` (2) | dodatkowy bufor |
| 67 | K | `test_proc_scan` (8), `test_linux_activity` (2), `test_activity_rpc` (3), `test_ubuntu_config` (4, pominięte) | deskryptory, pełny scan VM otwarty |
| 68 | K | `test_protection` (3), `test_web` (86) | ochrona zakresu |
| 69 | K | `test_rotation` (11), `test_retention` (16), `test_rotation_service` (6) | raport chronionych |
| 70 | K | `test_mount` (5), `test_storage` (3), `test_workers` (19), `test_retention` (16) | utrata dysku, test VM otwarty |
| 71 | K | `test_rotation_service` (6), `test_web` (86) | kolejka podglądu |
| 72 | K | `test_rotation_service` (6), `test_web` (86) | historia |
| 73 | K | `test_stream_health` (11), `test_rtsp_integration` (2), `test_monitor` (5) | rzeczywista próbka RTSP, fizyczne kamery otwarte |
| 74 | K | `test_archive` (18), `test_health_service` (25), `test_web` (86) | ostatni fragment |
| 75 | K | `test_monitor` (5), `test_health_service` (25), `test_web` (86) | alarm braku nagrań |
| 76 | K | `test_service_monitor` (6), `test_health_service` (25), `test_supervisor` (19) | awarie usług i zapisu |
| 77 | K | `test_health_service` (25), `test_monitoring_http_integration` (5) | lokalny serwer HTTP HA, rzeczywisty HA otwarty |
| 78 | K | `test_onvif_time` (9), `test_clock_service` (5), `test_web` (86) | ONVIF, fizyczne kamery otwarte |
| 79 | K | `test_timeline` (3), `test_archive` (18), `test_export_plan` (6), `test_recorder` (16) | DST i UTC |
| 80 | K | `test_web` (86), `test_security` (7) | audyt operacji |

### Pozostałe C i warunki odbioru

1, 2: rzeczywista instalacja Ubuntu 26.04, restart, wszystkie usługi, archiwum po starcie.

5: aktualizacja z poprzedniego podpisanego wydania, błędna migracja, restart w trakcie transakcji i rollback z kontrolą danych oraz działających demonów; migracja zmienionego zbioru jednostek recovery nadal nieobsługiwana.

18: rzeczywista bramka go2rtc, Nginx, WebSocket, dekodowanie H.264/AAC oraz ponowne połączenie.

23: test prawdziwego desktopu, iPhone'a i tabletu, w tym odtwarzanie i audio; nie obchodzono polityki Chromium.

24, 27: rzeczywisty ProFTPD, logowanie, upload, izolacja kont, przerwane transfery, restart, indeksowanie i faktyczne kamery FTP.

Pozostałe K również wymagają odbioru integracyjnego: szczególnie Cloudflare, HA, ONVIF, rzeczywiste urządzenia, długo działające nagrywanie i obciążenie, awarie montowania oraz fizyczny reboot. Nie ma skonfigurowanego zdalnego repozytorium NVR. Przed wdrożeniem należy powtórzyć cykl instalacja/test/poprawka na izolowanej VM. Nie wolno utożsamiać liczby zaliczonych testów z procentem gotowości produkcyjnej.
