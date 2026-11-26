# Sunhill NVR

**Niezależny rejestrator sieciowy IP do monitoringu wizyjnego.**

Sunhill NVR to samodzielnie hostowany system monitoringu dla Ubuntu Server, łączący podgląd na żywo, ciągłe nagrywanie, przeglądanie archiwum i zarządzanie kamerami w jednej aplikacji webowej. Dane, nagrania i konfiguracja pozostają na własnym serwerze, bez konieczności korzystania z chmury producenta kamer.

System jest używany w rzeczywistej instalacji monitoringu z 11 kamerami IP i wieloterabajtowym archiwum.

## Najważniejsze funkcje

| Obszar | Możliwości |
| --- | --- |
| Podgląd na żywo | Wielokamerowa siatka, ręczna zmiana położenia i rozmiaru kafelków, powiększanie dwuklikiem i powrót do poprzedniego układu, pełny ekran, dźwięk i wybór jakości zapamiętywany dla każdej kamery. |
| Kamery IP | Dodawanie po adresie IP, wykrywanie strumieni RTSP przez ONVIF i znane ścieżki producentów, konfiguracja ręczna oraz identyfikacja modelu. |
| Sterowanie kamerami | Reflektor, IR, zoom optyczny, ostrość, autofocus i zapisane pozycje PTZ w obsługiwanych urządzeniach Reolink. Funkcje zależą od możliwości kamery i protokołu. |
| Nagrywanie | Ciągłe nagrywanie RTSP do MP4 z zachowaniem oryginalnego strumienia wideo (HEVC z oznaczeniem `hvc1` po rozpoznaniu kodeka), odbiór nagrań przez FTP oraz segmenty RTSP od 30 sekund do 60 minut. |
| Archiwum | Oś czasu całej doby, kalendarz z podświetleniem wyłącznie dni zawierających nagrania wybranej kamery, przewijanie, zmienna prędkość, pełny ekran i odtwarzanie kilku kamer w tym samym czasie. W archiwum pojedynczej kamery automatyczny bezstratny remux starszych plików HEVC `hev1` na żądanie, jeżeli przeglądarka odrzuci źródło. |
| Import nagrań | Przyrostowe indeksowanie istniejących plików MP4, wybór kamery i okresu, wstrzymywanie skanu, liczniki postępu i bezpieczne czyszczenie starych pustych plików. |
| Eksport | Pobieranie oryginalnych plików, eksport wybranego fragmentu do MP4, zapisywanie klatek i ochrona nagrań przed rotacją. |
| Magazyn danych | Konfigurowalne katalogi nagrań, bufora i eksportów, automatyczna rotacja, kontrola wolnego miejsca oraz wymiana dysku bez rejestrowania UUID. |
| Administracja | Konta użytkowników, uprawnienia do kamer, statystyki strumieni i nagrań, stan usług, diagnostyka i dziennik operacji. |
| Integracje | Zdarzenia i powiadomienia dla Home Assistant, dostęp lokalny i zdalny przez HTTPS oraz Cloudflare Tunnel. |

## Architektura

```text
Kamery IP
    | RTSP / FTP / ONVIF / Reolink CGI / Baichuan
    v
Sunhill NVR
    |-- go2rtc       - dystrybucja obrazu na żywo
    |-- FFmpeg       - nagrywanie, przetwarzanie i eksport
    |-- ProFTPD      - odbiór nagrań FTP
    |-- Python       - backend, API, indeksowanie i monitoring
    |-- SQLite       - konfiguracja i indeks nagrań
    |-- Nginx        - dostęp HTTP/HTTPS
    `-- systemd      - zarządzanie usługami
    |
    v
Przeglądarka WWW / opcjonalny Cloudflare Tunnel
```

Aplikacja nie wymaga osobnego klienta ani zewnętrznego serwera bazy danych. Nagrania są przechowywane jako pliki MP4, niezależnie od bazy SQLite. Wybór jakości podglądu nie zmienia jakości zapisu RTSP.

## Instalacja

Platforma: **Ubuntu Server 26.04 LTS**, serwer fizyczny lub maszyna wirtualna. Potrzebne są uprawnienia root, dostęp do sieci kamer, przestrzeń na nagrania i dostęp do repozytoriów zależności.

Rozpakuj wybraną wersję paczki, wejdź do jej katalogu i uruchom instalator jako root. Przykład dla 0.3.17:

```bash
tar -xzf sunhill-nvr-0.3.17.tar.gz
cd sunhill-nvr-0.3.17
bash install.sh
```

Opcjonalnie podaj nazwę hosta:

```bash
bash install.sh nvr.example.com
```

Instalator przygotowuje zależności, konfigurację usług i środowisko aplikacji. Pierwsze konto administratora utworzysz za pomocą kodu jednorazowego wyświetlonego podczas instalacji.

### Dostęp do aplikacji

- Sieć lokalna: `http://ADRES_IP_SERWERA` (prywatny IPv4).
- Domena: `https://nvr.example.com`.
- Cloudflare Tunnel dla domeny publicznej: origin `http://127.0.0.1:8080`.

HTTP po adresie IP jest przeznaczone wyłącznie dla zaufanej sieci lokalnej. Do dostępu zdalnego używaj HTTPS. Portów RTSP, FTP i API kamer nie wystawiaj bezpośrednio do Internetu. Szczegóły konfiguracji: [magazyn i TLS](docs/storage-and-tls.md).

## Pierwsza konfiguracja

### Utworzenie pierwszego administratora przez WWW

1. Po zakończeniu instalacji skopiuj jednorazowy kod konfiguracji z terminala (ważny 24 godziny).
2. Otwórz adres aplikacji w przeglądarce w zaufanej sieci lokalnej lub przez HTTPS.
3. Rozwiń **Pierwsza konfiguracja** na ekranie logowania.
4. Wpisz kod, nazwę administratora, hasło i jego potwierdzenie. Minimalna długość hasła to 6 znaków; zalecane jest unikatowe hasło co najmniej 16 znaków.
5. Zatwierdź formularz, a następnie zaloguj się utworzonym kontem.

Jeżeli kod wygasł lub został zgubiony, wygeneruj nowy z konsoli jako root:

```bash
runuser -u sunhill -- env PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.cli renew-setup
```

Nowy kod zastępuje poprzedni. Nie udostępniaj go osobom postronnym.

### Alternatywnie: pierwszy administrator z konsoli

```bash
runuser -u sunhill -- env PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.cli complete-setup
```

Polecenie interaktywnie poprosi o nazwę administratora, hasło i potwierdzenie hasła (hasło nie jest wyświetlane). Po zakończeniu zaloguj się przez WWW. Oba polecenia konfiguracji początkowej działają wyłącznie wtedy, gdy w bazie nie ma jeszcze żadnego konta; nie resetują istniejącego administratora. Kolejne konta dodawaj w **Administracja / Użytkownicy**, wybierając rolę oraz uprawnienia.

### Konfiguracja kamer i nagrywania

1. W **Administracja / Kamery** dodaj kamerę, podając adres IP i dane logowania. Wybierz RTSP, FTP lub podgląd bez nagrywania. W razie potrzeby skonfiguruj adresy RTSP ręcznie.
2. W **Administracja / Nagrywanie** ustaw katalogi, długość segmentu RTSP i limity wolnej przestrzeni. Dla FTP skonfiguruj również wysyłkę nagrań w kamerze.
3. W **Administracja / Użytkownicy** skonfiguruj konta i dostęp do kamer.
4. W **Na żywo** wybierz jakość i układ podglądu; dodatkowe funkcje urządzenia znajdują się w menu kamery.

## Magazyn i import nagrań

Konfiguracja, baza i wydania aplikacji znajdują się pod `/opt/sunhill-nvr`. Katalogi mediów można wskazać niezależnie, np.:

```text
/home/store/kamery/
    .sunhill-staging/             # bufor nagrywania
    .sunhill-exports/             # gotowe eksporty
    cam-192-168-1-11/
        2026/11/25/
            132158.mp4
            20261125T132400+0100.mp4
```

Bufor musi znajdować się na tym samym systemie plików co nagrania. Eksporty mogą znajdować się na osobnym dysku. Ukryte podkatalogi bufora i eksportów nie są częścią archiwum kamer. Po wymianie dysku nie trzeba rejestrować jego tożsamości; należy zamontować go w skonfigurowanym miejscu i zapewnić dostęp użytkownikowi usługi `sunhill`.

Aby zaimportować istniejące pliki, umieść je w katalogach odpowiadających identyfikatorom kamer i dacie (`kamera/YYYY/MM/DD/HHMMSS.mp4`). W **Administracja / Nagrywanie / Indeksowanie archiwum** wybierz kamerę i opcjonalny zakres dat, a następnie uruchom indeksowanie. Nagrania pojawiają się w archiwum partiami, bez konieczności konwersji plików i bez zatrzymywania bieżącego zapisu RTSP.

Podczas indeksowania aplikacja automatycznie usuwa wyłącznie rozpoznane pliki MP4 o rozmiarze 0 B, starsze niż 24 godziny i niezmienione od co najmniej 5 minut, po ponownym sprawdzeniu aktywności, blokad, ochrony i tożsamości pliku. Nie usuwa niepustych uszkodzonych nagrań.

## Aktualizacje

Rozpakuj nowszą paczkę i w jej katalogu wykonaj jako root:

```bash
bash install.sh --update
```

Aktualizator sprawdza manifest, wykonuje kopię konfiguracji i bazy danych, aktywuje nowe wydanie i kontroluje stan usług. Aktualizacje są inicjowane przez administratora; paczki należy pobierać z zaufanego źródła. Podczas aktualizacji występuje krótka przerwa w działaniu usług. Nie ma potrzeby ponownego dodawania kamer ani przenoszenia archiwum.

Tryby naprawcze i migracja mechanizmu recovery są opisane w [dokumentacji aktualizacji](docs/releasing.md). Nie są częścią zwykłej aktualizacji sprawnej instalacji.

## Dokumentacja

| Dokument | Zawartość |
| --- | --- |
| [Magazyn i TLS](docs/storage-and-tls.md) | Konfiguracja dysków, ścieżek i dostępu HTTPS |
| [Home Assistant](docs/home-assistant.md) | Zdarzenia, powiadomienia i przykładowe automatyzacje |
| [Aktualizacje i recovery](docs/releasing.md) | Wydania, kopie, migracje i odzyskiwanie |
| [Historia zmian](CHANGELOG.md) | Zmiany w kolejnych wersjach |
| [Specyfikacja](SPECIFICATION.md) | Uzgodnione wymagania funkcjonalne i techniczne |
| [Status techniczny](STATUS.md) | Wyniki testów i potwierdzenia wdrożeń |

## Rozwój projektu

Kod backendu znajduje się w `src/sunhill_nvr`, frontend w `src/sunhill_nvr/static`, a testy w `tests`.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
node tests/browser_smoke.js
```

Każda paczka zawiera manifest integralności i pełną historię Git (`sunhill-nvr-history.bundle`). Repozytorium można odtworzyć poleceniem:

```bash
git clone sunhill-nvr-history.bundle sunhill-nvr
```

Szczegóły przygotowania wydań i zasady pracy: [AGENTS.md](AGENTS.md) oraz [HANDOVER.md](HANDOVER.md).
