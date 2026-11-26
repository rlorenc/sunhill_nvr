# Rotator, monitoring i integracje - weryfikacja punktów 62-80

Tabela opisuje dowody dostępne w kodzie i lokalnych testach, a nie wynik próby
produkcyjnej. Przed użyciem rzeczywistego archiwum trzeba uruchomić testy na
izolowanym Ubuntu 26.04 oraz sprawdzić rzeczywiste kamery i Home Assistanta.

| Punkt | Kod i dostępna weryfikacja | Warunek docelowy |
| --- | --- | --- |
| 62 | Limit zapisu i zapas: `workers.py`, `cli.py`, testy workerów. | Potwierdzić FTP i RTSP pod obciążeniem. Limit jest sprawdzany okresowo, więc nie jest gwarancją utrzymania 10 GB w każdej chwili. |
| 63 | Usuwanie ograniczone do archiwum, kontrole montowania, dowiązań, tożsamości i zmian pliku: `retention.py`, `rotation.py`, `test_retention.py`, `test_confined_unlink.py`. | Sprawdzić na docelowym systemie plików i z rzeczywistymi zapisami. Procesy otwierające plik po skanie aktywności nadal nie są objęte gwarancją wzajemnej blokady. |
| 64 | Kolejność wg czasu UTC i ochrona niemożliwych godzin: `test_retention.py`, `test_rotation.py`. | Potwierdzić nazwy generowane przez modele kamer. |
| 65 | Odczyt wolnego miejsca po każdym usunięciu: `retention.py`, `test_retention.py`. | Sprawdzić wartości na dysku docelowym. |
| 66 | Próg i bufor w ustawieniach, wcześniejszy start rotacji: `workers.py`, `test_workers.py`. | Zweryfikować tempo zapisu i odzyskiwanie miejsca na docelowym dysku. |
| 67 | Kontrola otwartych plików, odmowa przy niewystarczającej widoczności: `proc_scan.py`, `activity_rpc.py`, `test_proc_scan.py`. | `test_ubuntu_config.py` przewiduje próbę z prawdziwym `/proc`; wymaga uruchomienia w Ubuntu i wspólnej przestrzeni procesów. |
| 68 | Ochrona pliku i zakresu, transakcja i blokada archiwum: `test_web.py`, `test_retention.py`, `test_storage_workflow.py`. | Potwierdzić zachowanie przy jednoczesnym zapisie i rotacji na Ubuntu. |
| 69 | Pełny raport chronionych i nierozstrzygalnych plików oraz uwzględnienie plików rzadkich: `test_retention.py`. | Wynik podglądu pozostaje szacunkiem. |
| 70 | Kontrola dysku, znacznika i identyfikacji montowania, wstrzymanie pracy przy niepewności: `test_mount.py`, `test_workers.py`, `test_retention.py`. | Sprawdzić odłączenie i ponowne podłączenie dysku na VM. |
| 71 | Zadanie podglądu, blokada pracownika i odzyskiwanie po awarii: `test_rotation_service.py`, `test_web.py`. | Potwierdzić z systemd i panelem na Ubuntu. |
| 72 | Historia, stan i alarm rotatora zapisywane w jednej transakcji: `rotation_service.py`, `test_rotation_service.py`, `test_storage_workflow.py`, `test_web.py`. | Potwierdzić po rzeczywistej rotacji na Ubuntu. |
| 73 | Osobne stany obrazu, zapisu i bramki: `test_stream_health.py`, `test_web.py`, `browser_smoke.js`. | Sprawdzić strumienie wszystkich modeli i przeglądarkę. |
| 74 | Ostatni zindeksowany fragment i prezentacja oraz kontrola równoczesnej rotacji i indeksowania: `health_service.py`, `archive.py`, `test_health_service.py`, `test_archive.py`. | Potwierdzić czas dla plików FTP i RTSP rzeczywistych kamer. |
| 75 | Konfigurowalny próg braku nagrania, okres po włączeniu, korekta czasu: `test_health_service.py`, `test_monitor.py`, `test_monitoring_http_integration.py`. | Dobrać próg do długości segmentu i indeksowania oraz sprawdzić na kamerach. |
| 76 | Stan usług, dysku, rotatora i błędy nagrywania: `test_service_monitor.py`, `test_workers.py`, `test_health_service.py`. | Sprawdzić rzeczywiste awarie systemd, FTP i FFmpeg. |
| 77 | Kolejka powiadomień, ponawianie, kolejność, rozdzielenie źródeł systemowych i kamer o tej samej nazwie, brak przekierowań z tokenem: `test_health_service.py`, `test_monitoring_http_integration.py`. | Test HTTP używa lokalnego serwera testowego, nie potwierdza kompatybilności z rzeczywistym Home Assistantem. |
| 78 | Odczyt czasu ONVIF z Digest i WS-Security oraz obsługa błędnej odpowiedzi: `test_onvif_time.py`, `test_clock_service.py`, `test_monitoring_http_integration.py`. Nowe alarmy ONVIF są rozróżniane w kolejce od alarmów czasu pliku; format zdarzenia HA pozostaje taki sam. | Sprawdzić implementację ONVIF poszczególnych kamer. Starszych oczekujących wpisów `clock_skew` nie da się pewnie przypisać do jednej z tych przyczyn. |
| 79 | Offsety i DST dla nowych plików RTSP: `test_retention.py`, `test_timeline.py`, `browser_smoke.js`. | Nie można odtworzyć brakującego offsetu ze starych niejednoznacznych plików FTP; test kamer i przeglądarki pozostaje otwarty. |
| 80 | Audyt zmian oraz atomowe zlecenie podglądu i jego wpisu: `test_web.py`. | Potwierdzić wpisy po rzeczywistych operacjach na Ubuntu. |

Na komputerze deweloperskim można uruchomić `python3 -m unittest discover -s
tests` i `node tests/browser_smoke.js`. Testy oznaczone
`SUNHILL_UBUNTU_INTEGRATION=1` wymagają przygotowanego Ubuntu i zależności.
Scenariusz instalacyjny w `tests/ubuntu_install_smoke.sh` wolno uruchamiać tylko
na jednorazowym runnerze wskazanym w jego zabezpieczeniach. Sprawdza również
podgląd i rzeczywiste usuwanie na osobnym obrazie ext4 zamontowanym w pętli,
a także zatrzymanie usługi systemd i wysyłkę alarmu do lokalnego odbiornika HTTP;
scenariusz w tej wersji nie został jeszcze uruchomiony na Ubuntu.
