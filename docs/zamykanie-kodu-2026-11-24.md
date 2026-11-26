# Zamknięcie implementacji - 24.11.2026

Baza `fd060b2`. Zachowano zakres i definicje K/C/N ze specyfikacji. Nie jest to odbiór produkcyjny. 27 przejść C → K oznacza potwierdzoną obecność kodu, nie 27 funkcji napisanych od zera. 20 pozostaje C: brak implementacji lub brak wystarczającego dowodu kompletności.

## Nowe zmiany

- `31e5995`, `76e79b4`, `cf1ffec`: podpisana migracja kodu odzyskiwania, zgodność kontraktu jednostek, stan bezczynności menedżera, test rzeczywistej podpisanej paczki. Zmiana jednostek pozostaje nieobsłużona.
- `55b9b5a`: właściwe wystąpienie powtórzonej godziny w zdarzeniach oraz rzeczywista integracja lokalnego Nginx TLS z aplikacją.
- `70ad8ee`: powtórne sprawdzenie aktywności i tożsamości plików podczas sprzątania eksportów.
- `cf1ffec`: 120 kamer w API i 360 aliasów; rzeczywisty statvfs.

## Przejścia C → K

| Punkt | Dowód i granica odbioru |
| --- | --- |
| 4 | `test_cli.py` (console completion, one-time token), `test_web.py` (setup token and origin). Both entry paths present; VM wizard pending. |
| 6 | `install.sh`, `test_units.py`, `test_install_health.py`, `test_controller.py` (unchanged config restarts gateway, FTP recovery). Actual systemd boot pending. |
| 11 | `test_web.py` (revoked download, session and camera authorization), `test_live_proxy.py`, `test_export_service.py`; real Nginx TLS authorization and revocation test. |
| 12 | `nginx_config.py`, `test_nginx_config.py`, `test_nginx_http_integration.py`: actual local origin TLS, redirect and HTTP access. External tunnel/DNS not exercised. |
| 14 | `test_nginx_http_integration.py` (TLS, unauthorized WS, internal paths, secret redaction), `test_http_archive_integration.py`, `test_live_proxy.py`. Live device stream pending. |
| 16 | `test_web.py::test_add_and_list_more_than_one_hundred_cameras_without_alias_truncation`: 120 saved/read cameras, 360 aliases. No live throughput claim. |
| 33 | `test_storage_system.py`, `test_storage_transaction.py` (apply, rollback, crash), same-filesystem constraint retained. Real mount and service switching pending. |
| 35 | `test_archive.py` (incremental scan, identity changes and rollback), `test_http_archive_integration.py` (browse existing media). Large real archive pending. |
| 40 | `test_web.py` (calendar, keyset, >10000 rows), `browser_smoke.js` (pagination, stale month/day responses). Real browser pending. |
| 43 | `test_web.py::test_next_recording_crosses_multiple_days_and_checks_camera_permission`, `browser_smoke.js` (next file, midnight, long gap). Real player pending. |
| 48 | `static/app.js` UTC clock and daily fetch, `browser_smoke.js` (pause, midnight, two cameras, fold selection). Actual playback drift not measured. |
| 50 | `55b9b5a`: `test_nginx_http_integration.py` actual HTTPS API for all 3 event types, deduplication, invalid type, token revocation, repeated hour; JS event fold regression. Camera/HA source pending. |
| 59 | `test_web.py` (cancel, owner, progress/list), `test_export_service.py` (cancel during actual encoding), `browser_smoke.js` (list, cancel and request handling). Actual browser pending. |
| 60 | `70ad8ee`: `test_export_service.py` (identity mutation, activity, internal hard links, mount fences), `test_staging_cleanup.py`. Temporary filesystem exercised; system-wide process visibility is separately open at 67. |
| 61 | `test_export_service.py` (real statvfs refusal, simulated low space during processing, final check). No artificial filling of host disk. |
| 62 | `workers.py::recording_space_available` and `test_workers.py` (minimum, fail closed), `test_controller.py` (FTP stop and resume at reserve). Physical write rate pending. |
| 63 | `test_retention.py` (actual oldest-file removal, protected and unrelated retained, mutation and mount guards), `test_rotation.py`. Actual data disk and process scanning pending. |
| 66 | `workers.py` trigger minimum + buffer//2, target minimum + buffer, `test_workers.py`, `test_retention.py` target handling. Real write/rotation throughput pending. |
| 69 | `test_retention.py` (no-rotation protected totals, unknown local time, sparse size and mount recheck). Actual disk estimates pending. |
| 70 | `test_workers.py` storage enrollment/marker guards, `test_controller.py` (loss/recovery), `test_retention.py` mount loss. Physical disk detach pending. |
| 71 | `test_rotation_service.py` (async preview, lock, worker recovery), `browser_smoke.js` (preview results). Long real scan pending. |
| 72 | `test_rotation_service.py` (atomic history/alarm, backward correction, dedup), `browser_smoke.js` (history). Actual rotation on target pending. |
| 75 | `test_monitor.py`, `test_health_service.py` (staleness, grace, clock skew and recovery), `test_web.py` threshold validation. Real camera pending. |
| 76 | `test_workers.py` (recording faults and recovery), `test_service_monitor.py`, `test_health_service.py`, `test_monitoring_http_integration.py`, `browser_smoke.js`. Real system services pending. |
| 77 | `test_monitoring_http_integration.py` actual local HTTP endpoint for HA delivery, retry, order and redirect token protection; `test_health_service.py`. Actual HA pending. |
| 78 | `test_monitoring_http_integration.py` actual local HTTP Digest/WSSE, malformed camera isolation; `test_onvif_time.py`. Actual camera endpoint pending. |
| 79 | `55b9b5a`: API and UI event fold, `test_timeline.py`, `test_export_service.py` repeated hour, `browser_smoke.js`. Legacy names without offset remain explicitly ambiguous. |

## 20 otwartych pozycji C

| Punkt | Brak lub otwarta weryfikacja kompletności |
| --- | --- |
| 1 | Odbiór pełnej instalacji, uprawnień i restartu na Ubuntu; nie potwierdzono kompletności instalatora. |
| 2 | Instalacja rzeczywistych go2rtc, ProFTPD i zależności, test całego cyklu. |
| 3 | Migracja kodu odzyskiwania istnieje, ale zmiana jednostek odzyskiwania nieobsłużona; domena i rollback na systemd. |
| 5 | Migracja zmienionego kontraktu jednostek odzyskiwania i pełny podpisany update/rollback po awarii. |
| 8 | Odbiór wszystkich operacji administracyjnych i niezależna kontrola kompletności panelu. |
| 9 | Odbiór wszystkich operacji użytkownika, rzeczywisty odtwarzacz i kompletność panelu. |
| 13 | Brak potwierdzenia bezpiecznego bezpośredniego LAN bez własnego certyfikatu; nie dodano niezabezpieczonego HTTP bez decyzji. |
| 18 | Rzeczywisty go2rtc, obraz i dźwięk, kodeki i odtwarzacz. |
| 21 | Dobór strumienia przy różnych obciążeniach, rzeczywiste przełączanie jakości. |
| 23 | Rzeczywisty desktop, iPhone i tablet, obsługa mediów i gestów. |
| 24 | ProFTPD rzeczywiste logowanie, zapis, uprawnienia i restart. |
| 26 | Rzeczywista izolacja FTP i zmiana katalogów, uprawnienia. |
| 27 | Konfiguracja siedmiu rzeczywistych kamer FTP i odbiór ścieżek. |
| 28 | Rzeczywisty ciągły RTSP i publikacja segmentów na docelowym systemie. |
| 29 | Długotrwały zapis z dźwiękiem, rzeczywiste źródła, utrata sygnału. |
| 30 | Wznowienie po rzeczywistym restarcie/awarii połączenia. |
| 53 | Długotrwały wielodniowy eksport, wydajność i pełna obsługa skrajnych przypadków. |
| 56 | Wielodniowy eksport z przerwami, czarny obraz i cisza przy różnych formatach wejścia. |
| 67 | Pełna widoczność procesów /proc, aktywny zapis FTP/RTSP i blokady; lokalna próba realnego skanu odmówiona przez środowisko. |
| 73 | Rzeczywisty go2rtc i kamery, porównanie sygnałów stream health ze stanem nagrywania. |

## Granice środowiska

Debian 13, brak systemd jako PID 1 i /dev/kvm, brak go2rtc i ProFTPD, brak rzeczywistych kamer, HA, tunelu, urządzeń mobilnych. Próba pełnego skanu deskryptorów /proc odmówiona przez środowisko. Chromium wcześniej blokował nawigację. Próby Nginx i HTTP są rzeczywiste, lecz lokalne; część testów systemd symuluje stan jednostek. Nie użyto prawdziwego archiwum. Push niewykonany: origin jest bundle, brak właściwego dostępnego repozytorium.

## Wynik regresji

Pełna regresja lokalna na Debianie 13: 554 testy, 550 zaliczonych, 4 pominięte (przygotowane Ubuntu 26.04), 0 niepowodzeń i błędów, 82,706 s. `node --check src/sunhill_nvr/static/app.js`, `node tests/browser_smoke.js`, `bash -n install.sh` i `bash -n tests/ubuntu_install_smoke.sh` OK. Ostrzeżenia ResourceWarning o niezamkniętych połączeniach SQLite nadal występują w logu testów; nie potwierdzono ich przyczyny. CI nie uruchomiono.
