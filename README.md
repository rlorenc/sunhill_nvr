# Sunhill NVR 0.3.3

Self-hosted NVR for Ubuntu Server 26.04 LTS. FTP, RTSP, archive, live view, export, retention and account administration. Release for testing on an isolated VM; not a production acceptance.

## Instalacja

Rozpakuj paczkę `sunhill-nvr-0.3.3.tar.gz`, wejdź do katalogu `sunhill-nvr-0.3.3` i jako root wykonaj:

```sh
bash install.sh
```

Podaj nazwę DNS (domyślnie `nvr.local`) lub użyj `bash install.sh nvr.example.com`. Nie trzeba generować klucza wydawcy, podawać katalogu nagrań ani ustawiać flagi eksperymentalnej. Instalator sprawdza system, zależności, porty i ścieżki. Dla domeny publicznej origin Cloudflare Tunnel to `http://127.0.0.1:8080`; dla `.local` domyślnie generowane jest lokalne CA i HTTPS. Pierwsze konto tworzysz kodem jednorazowym z konsoli instalacji (nie jest to klucz aktualizacji).

Wejście przez `http://ADRES_IPV4_SERWERA` (np. `http://192.168.0.7`) w prywatnej sieci IPv4 (RFC 1918) lub loopback udostępnia aplikację bez przekierowania i bez DNS/tunelu. HTTP po domenie przekierowuje wyłącznie na HTTPS tej samej domeny (301). Domena HTTPS zachowuje Secure cookies; HTTP po IP nie szyfruje haseł, sesji ani nagrań. Używaj tylko w zaufanym LAN i nie wystawiaj portu 80 do Internetu. Po aktualizacji starszej instalacji mechanizm recovery pozostaje przypięty do poprzedniej wersji; przed następną zmianą DNS lub TLS należy odświeżyć recovery zgodnie z `docs/releasing.md`, aby starszy generator nie odtworzył przekierowania IP.

Cały trwały stan aplikacji znajduje się pod `/opt/sunhill-nvr` (config, data, control, releases, recovery, bin, updates, media). Wyjątki: pakiety systemowe, jednostki systemd, konfiguracja Nginx i pliki runtime systemu. Panel administratora > Ustawienia dysku pozwala niezależnie wybrać nagrania i eksporty, również na różnych dyskach. Szczegóły: [instalacja](docs/instalacja-testowa.md), [dysk i TLS](docs/storage-and-tls.md).

## Ponowienie nieudanej instalacji 0.2.0

Jeżeli instalacja 0.2.0 zatrzymała się na `Directory not writable by application: /opt/sunhill-nvr/media/kamery`, rozpakuj nowe wydanie do osobnego katalogu i uruchom jako root:

```sh
bash install.sh --retry-incomplete
```

Można podać dotychczasową nazwę DNS po opcji. Tryb dotyczy wyłącznie wczesnej awarii z domyślnym katalogiem mediów. Sprawdza brak bazy, nagrań, konfiguracji, zainstalowanych jednostek Sunhill i aktywnego ProFTPD. Zachowuje stary katalog w `/opt/sunhill-nvr-incomplete.XXXXXXXX/original` i konto usługi, niczego nie kasuje. Jeżeli warunki nie są spełnione, przerywa pracę; nie omijaj kontroli. Działający Nginx po poprzedniej próbie może zajmować wymagany port 80; zatrzymaj go przed retry tylko jeśli jest to dedykowany serwer testowy i nie obsługuje żadnej innej aplikacji. Na czystej VM nadal wystarczy `bash install.sh`.

## Wznowienie przerwanej instalacji 0.2.1, 0.2.5 lub 0.2.6

Jeżeli instalacja 0.2.1 zatrzymała się przy pierwszym uruchomieniu `sunhill-controller.service` z `sqlite3.OperationalError: unable to open database file`, albo wznowienie 0.2.5 przerwało się przy `systemctl reset-failed`, albo 0.2.6 zatrzymała się na `ScoreboardFile: unable to use`, rozpakuj aktualne wydanie w nowym katalogu i uruchom jako root:

```sh
bash install.sh --resume
```

Tryb 0.2.7 weryfikuje znane warianty wszystkich jednostek, zatrzymuje pozostawiony w pętli restartu wyłączony go2rtc/FTP, sprawdza manifest i wdraża kod 0.2.7 oraz zgodne recovery przed pierwszym startem kontrolera. Zachowuje bazę, konfigurację, nagrania, stary release i kopie jednostek w `control/upgrade/resume-release-*`; poprzednie recovery zachowuje w `recovery-resume-*`. Nie wymaga oddzielnego `--update`. Reset stanu jednostek jest wykonywany osobno; niezaładowana jednostka jest akceptowana tylko po potwierdzeniu przez systemd, że istnieje i jest nieaktywna. Brak konfiguracji go2rtc pomija start (ExecCondition), bez pętli restartów. Odrzuca aktywną/włączoną instalację, niestandardowe jednostki i przerwane transakcje. Po błędzie startu zatrzymuje usługi; nie usuwa danych ani nie resetuje bazy. Po udanym wznowieniu, jeśli nie utworzono wcześniej administratora, wykonaj polecenie `complete-setup` wyświetlone przez instalator. Nie używaj tego trybu do aktualizacji działającego NVR. Testowano lokalnie na odtworzonym zestawie jednostek i zasymulowanym systemctl, nie na hoście użytkownika.

Walidacja konfiguracji FTP używa prywatnego katalogu tymczasowego pod kandydatem konfiguracji. Nie zależy od istnienia `/run/sunhill-ftp` i nie dotyka aktywnego scoreboard demona. Produkcyjny katalog runtime nadal tworzy systemd.

## AppArmor na Ubuntu 26.04

ProFTPD 1.3.9 może być objęty systemowym profilem `proftpd`. Instalator tworzy lub uzupełnia wyłącznie zarządzany blok w `/etc/apparmor.d/local/proftpd`, zachowując pozostałe reguły, i przeładowuje profil bez wyłączania AppArmor. Dedykowany `sunhill-ftp-policy.service` jest zależnością kontrolera i synchronizuje blok z bieżącą konfiguracją katalogu nagrań również po zmianie dysku lub rollbacku. Nie przyznaje dostępu do bazy ani eksportów. W razie błędu parsera odtwarzany jest poprzedni lokalny plik. Ścieżki zawierające metaznaki składni AppArmor są odrzucane; profil bez lokalnego include wymaga interwencji administratora. Pełna zgodność z wymuszaniem ABI 5.0 oraz FTP na Ubuntu wymaga testu na VM; lokalny parser 4.1 w trybie bez ładowania do jądra potwierdził składnię samych reguł.

## Aktualizacja na żądanie

Od 0.2.3 katalog w TAR.GZ zawiera wersję. Nowy czytnik przyjmuje oba układy, starszy mechanizm recovery 0.2.0–0.2.2 przyjmuje wyłącznie `sunhill-nvr/`. Nie używaj nowej paczki bezpośrednio w starszym aktualizatorze; wymagana jest migracja recovery lub osobna paczka zgodności. Wznowienie 0.2.7 jest specjalnym pomostem dla przerwanej pierwszej instalacji 0.2.1 lub nieudanego wznowienia 0.2.5/0.2.6: wymienia recovery i kod w jednym przebiegu.

```sh
bash install.sh --update
```

Działającą instalację aktualizuj w ten sposób, nie przez --resume. Pozostaw archiwum `sunhill-nvr-0.3.3.tar.gz` obok rozpakowanego katalogu `sunhill-nvr-0.3.3/`; instalator sam je odnajdzie. Alternatywnie użyj jawnej ścieżki `bash install.sh --update /ścieżka/sunhill-nvr-0.3.3.tar.gz` lub umieść nowszą paczkę w katalogu updates. Brak serwera dystrybucyjnego; polecenie nie pobiera z Internetu. Od 0.3.1 możliwa jest także aktualizacja z samego rozpakowanego katalogu po weryfikacji manifestu. CLI pokazuje aktywną wersję, wynik transakcji i adresy HTTP LAN oraz HTTPS domeny, a DONE wyłącznie po pozytywnych kontrolach. Błąd kontroli dostępu po zatwierdzonej transakcji nie wykonuje automatycznego rollbacku. Alternatywnie umieść paczkę zatwierdzoną przez root w `/opt/sunhill-nvr/updates/` i wybierz ją w panelu administratora. Nie ma automatycznych aktualizacji ani obowiązkowego podpisu. Paczka musi pochodzić z zaufanego źródła. Kontrola manifestu nie zastępuje autentyczności wydawcy. Zobacz [wydania i rollback](docs/releasing.md).

Wersja 0.3.3 obsługuje czystą instalację i dotychczasowe wznowienia przerwanych instalacji; brak automatycznego przeniesienia starszej zainstalowanej wersji z /etc i /var/lib. Nie podłączaj produkcyjnego archiwum przed odbiorem.

## Hasła

Minimum 6 znaków dla administratora, użytkowników i kont FTP. Maksimum 1024 znaków dla aplikacji i 128 dla FTP. Brak obowiązkowych znaków specjalnych. Przy dostępie publicznym zalecane dłuższe unikatowe hasło. Po aktualizacji działającej 0.2.9, jeśli nie ma administratora, wykonaj `runuser -u sunhill -- env PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.cli complete-setup`.

## Weryfikacja i praca nad kodem

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
node tests/browser_smoke.js
```

FFmpeg, ffprobe, Nginx i OpenSSL są potrzebne do wybranych testów. Rzeczywista instalacja, systemd, go2rtc, ProFTPD, fizyczne kamery i urządzenia mobilne wymagają dalszego odbioru. [Specyfikacja 80 punktów](SPECIFICATION.md), [macierz](IMPLEMENTATION_STATUS.md), [historia zmian](CHANGELOG.md), [przekazanie](HANDOVER.md), [status](STATUS.md), [zasady pracy](AGENTS.md). Integracja [Home Assistant](docs/home-assistant.md).

Historia Git znajduje się w `sunhill-nvr-history.bundle`; odtworzenie: `git clone sunhill-nvr-history.bundle sunhill-nvr-work`. Nowe wydania tworzy `python3 scripts/package_release.py --candidate /ścieżka/sunhill-nvr-WERSJA.tar.gz` z czystej gałęzi main.

Od 0.2.9 lokalne pakowanie z `--candidate` tworzy paczkę testową. Zweryfikowane wydanie wymaga wszystkich rzeczywistych zadań CI Ubuntu; zob. `docs/releasing.md` i `HANDOVER.md`.

## Dodawanie kamer od 0.2.15

W Administracja / Dodaj kamerę wpisz prywatny adres IPv4 i, jeśli wymagane, użytkownika i hasło kamery. Nazwa jest opcjonalna; identyfikator powstaje z IP. Wybierz tryb zapisu (domyślnie dotychczasowe FTP; konfiguracja wysyłania FTP w kamerze pozostaje osobna). Przycisk Wykryj i dodaj kamerę próbuje ONVIF Media1 na HTTP 80/8000 z Digest, a w razie potrzeby UsernameToken, następnie znanych ścieżek RTSP (Reolink, Hikvision, Dahua). Każdy zapisywany automatycznie strumień musi przejść próbę ffprobe po TCP z metadanymi obrazu. Nie jest to gwarancja dekodowania ani długotrwałego zapisu. Jeżeli pomocniczy nie jest dostępny, zapisany zostaje tylko główny. Czas pojedynczej operacji wykrywania do 27 sekund. Nie skanuje całej sieci. Niestandardowe porty, adresy Media2, inne URI lub kamery niedostępne przez RTSP mogą wymagać konfiguracji ręcznej. Nie włącza ONVIF/RTSP w kamerze i nie zmienia konfiguracji kontroli czasu ONVIF. Przy ponownym wykrywaniu istniejącej kamery podaj aktualne dane logowania; udana operacja zastąpi strumienie, bez zmiany identyfikatora, nagrań i uprawnień. Nieudana operacja nie zmieni konfiguracji. Używaj zaufanej sieci kamer; HTTP nie szyfruje ruchu.

## Tymczasowy układ podglądu

W Na żywo wybierz Edytuj widok; przeciągnij uchwyt Przesuń na inną kamerę lub użyj uchwytu Zmień rozmiar. Strzałki na uchwytach także działają. Zakończ edycję pozostawia układ do przeładowania strony; Przywróć układ resetuje. Zapisane presety pozostają niezależne.

## Sterowanie kamerą Reolink (0.3.6)

Na żywo → Opcje → Sterowanie kamerą. Dostęp tylko administratora; możliwości odczytywane na żądanie. Używa danych logowania z RTSP oraz prywatnego IPv4 i API HTTP (port 80, `/api.cgi` lub `/cgi-bin/api.cgi`). HTTP przesyła dane bez szyfrowania: używać wyłącznie w zaufanej, izolowanej sieci kamer; nie udostępniać portu w Internecie. Firmware musi obsługiwać CGI, konto mieć uprawnienia. Reflektor, jasność, IR, optyczny zoom, ostrość, autofocus i odczyt istniejących presetów PTZ tylko po poprawnym odczycie danej funkcji; zakres zoomu/ostrości pochodzi z kamery. Brak API HTTPS-only, ONVIF PTZ, ruchu kierunkowego i wielokanałowych NVR. Nie zmienia parametrów RTSP ani nagrywania. Testy lokalne z atrapą, fizyczne urządzenia wymagają odbioru.
