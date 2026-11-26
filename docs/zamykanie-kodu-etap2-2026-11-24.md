# Zamykanie kodu - etap 2 (24.11.2026)

Baza `3e6055b`. Zmiany implementacyjne oraz testy w lokalnych commitach `1c29fce`, `48a786e`, `47cdb8e`, `79d1dce`, `1bd7d9c`. Nie zmieniono zakresu uzgodnionych funkcji.

## Dowody dla sześciu pozycji C → K

| Punkt | Zmiana i dowód | Odbiór nadal otwarty |
| --- | --- | --- |
| 28 | Rzeczywisty klient FFmpeg 7.1.5 po RTSP/TCP odrzucał `-rw_timeout` z błędem `Option rw_timeout not found`. Usunięto ten parametr i pozostawiono RTSP `-timeout`; realny test lokalnej kamery syntetycznej zapisuje i publikuje MP4. | go2rtc i rzeczywiste źródła, długotrwała segmentacja na docelowym serwerze |
| 29 | Realny RTSP H.264/PCMA: nieudana kopia PCMA do MP4, następnie AAC z zachowaniem H.264; dwa poprawne pliki. | praca 24/7 i słyszalność audio na kamerach |
| 30 | Monotoniczny watchdog braku opublikowanego segmentu (1200 s), nie restartuje innych kamer ani nie reaguje restartem na rozpoznaną blokadę publikacji. Test cofnięcia zegara, realne ponowne połączenia po rozłączeniu i zachowanie oryginału. | restart systemd, długotrwałe awarie źródeł |
| 53 | Czas pliku pośredniego nie zastępuje logicznego czasu cięcia; limit końcowej długości, normalizacja proporcji obrazu i SAR, test 17 plików w ciągu 64 h (16,6 s przy pominięciu przerw) oraz wielopoziomowych partii. | wydajność eksportu rzeczywistych wielodniowych nagrań |
| 56 | Skończony limit kodowania zależny od długości (max z 7200 s i 4 × długość + 600 s), jawny timeout nadal obowiązuje. Testy czerni z ciszą, dźwięku i przerw przez północ. | rzeczywiste renderowanie wielodniowej przerwy w docelowym formacie; nie wykonano takiego długiego renderu lokalnie |
| 67 | Inspekcja wszystkich tablic deskryptorów wątków, odmowa przy braku widoczności żyjącego procesu. Rzeczywisty osobny proces otwiera plik, skan ograniczony do jego wpisu /proc wykrywa inode; po zakończeniu nie wykrywa. | pełna widoczność hosta i równoczesny FTP/RTSP w docelowej przestrzeni PID |

K oznacza kod funkcji obecny, jeszcze bez odbioru produkcyjnego. Test RTSP używa własnego serwera syntetycznego, nie go2rtc ani kamer fizycznych; test otwartego pliku nie potwierdza uprawnień pełnego skanu hosta. Nie przeprowadzono rzeczywistego restartu Ubuntu. Nie zmieniono wersji wydania ani kluczy, nie publikowano w obcym repozytorium.

## Pozostałe 14 C

| Punkt | Otwarte zadanie |
| --- | --- |
| 1 | Pełna instalacja Ubuntu |
| 2 | Zależności i rzeczywiste usługi |
| 3 | Migracja zmienionego kontraktu jednostek odzyskiwania, domena |
| 5 | Zmiana kontraktu jednostek odzyskiwania i podpisany update/rollback |
| 8 | Kompletność panelu administratora i rzeczywisty odbiór |
| 9 | Kompletność panelu użytkownika i rzeczywisty odbiór |
| 13 | Bezpieczny bezpośredni LAN bez własnego certyfikatu; nie wprowadzono niezabezpieczonego HTTP |
| 18 | go2rtc, dźwięk, kodeki i odtwarzacz |
| 21 | Rzeczywiste przełączanie jakości |
| 23 | Desktop, iPhone, tablet |
| 24 | ProFTPD |
| 26 | Izolacja katalogów FTP |
| 27 | Siedem rzeczywistych kamer FTP |
| 73 | Monitoring rzeczywistych strumieni i nagrywania |

## Regresja

Pełna regresja na Debianie 13, FFmpeg 7.1.5: 561 testów, 557 zaliczonych, 4 pominięte (Ubuntu 26.04), 0 błędów i niepowodzeń, 90,984 s. `node --check src/sunhill_nvr/static/app.js`, `node tests/browser_smoke.js`, `bash -n install.sh` i `bash -n tests/ubuntu_install_smoke.sh` OK. W logach nadal pojawiają się ostrzeżenia ResourceWarning SQLite oraz błędy celowo wywołane w testach. CI i fizyczne urządzenia nieuruchomione.
