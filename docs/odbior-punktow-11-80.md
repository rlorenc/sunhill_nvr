# Punkty 11-80: stan prac i warunki odbioru

Uwaga: statusy w tej tabeli są historyczne dla partii 11-80. Bieżące statusy i dowody w `../IMPLEMENTATION_STATUS.md` oraz `zamykanie-kodu-2026-11-24.md`; scenariusze odbiorowe pozostają aktualne.

Baza `f95577c`, osiem commitów kodu. Macierz obejmuje 70 pozycji, nie jest deklaracją zakończenia wszystkich prac. Zmiana przy punkcie może dotyczyć współdzielonego mechanizmu, nie całego wymagania. W pozostałych pozycjach wskazano istniejący kod/testy, a nie nową implementację. Testy jednostkowe i integracja lokalna nie zastępują testów urządzeń i Ubuntu.

| Punkt | K/C/N | Nowa zmiana w tej partii | Pozostały warunek odbioru |
| --- | --- | --- | --- |
| 11 | C | `c8bd556`, `7259223` | Cofnąć uprawnienia w trakcie live, odtwarzania, pobierania i eksportu; sprawdzić role. |
| 12 | C | Nie; istniejący kod | Skonfigurować DNS i rzeczywisty Cloudflare Tunnel, login, HTTPS, WebSocket i MP4 z zewnątrz. |
| 13 | C | Nie; istniejący kod | Na Ubuntu sprawdzić LAN z TLS i bez TLS, rotację certyfikatu, rollback i zaufanie klientów. |
| 14 | C | `c8bd556`, `7259223` | Bez sesji lub po jej cofnięciu sprawdzić HTTP, WebSocket, oryginalny MP4; sekrety nie mogą wyciekać. |
| 15 | K | `d13a09a` | Dodać, edytować, wyłączyć i ponownie włączyć kamerę z panelu; sprawdzić konfigurację usług. |
| 16 | C | `c8bd556` | Stopniowo zwiększać liczbę kamer i klientów; zmierzyć CPU, RAM, I/O, opóźnienie i limity połączeń. |
| 17 | K | `d13a09a` | Zmienić parametry i hasła rzeczywistych kamer; potwierdzić poprawność RTSP i brak ujawnienia haseł. |
| 18 | C | `c8bd556` | Odtworzyć obraz i dźwięk z rzeczywistych modeli; H.264/H.265, AAC, awaria i powrót go2rtc. |
| 19 | K | `9114505` | Sprawdzić wielokamerową siatkę i jednoczesne strumienie na docelowych klientach. |
| 20 | K | `9114505` | Powiększyć i zmniejszyć kamerę, sprawdzić obraz i zmianę jakości bez wycieku połączeń. |
| 21 | C | `c8bd556` | Sprawdzić automatyczną i ręczną jakość, wariant zgodności oraz obciążenie CPU. |
| 22 | K | `9114505` | Zapisać, wybrać, usunąć układ; dwa niezależne konta, zmiana uprawnień. |
| 23 | C | `9114505` | Sprawdzić desktop, iPhone Safari i tablet; orientacje, dotyk, dźwięk, odtwarzanie i eksport. |
| 24 | C | `d13a09a` | Na Ubuntu uruchomić ProFTPD, zalogować się i przesłać nagranie, sprawdzić prawa plików. |
| 25 | K | `d13a09a` | Zweryfikować dostęp wspólnego i prywatnego konta FTP; odmowa dostępu poza katalogiem. |
| 26 | C | `d13a09a` | Zmienić katalog oraz przypisanie konta, potwierdzić izolację rzeczywistym klientem FTP. |
| 27 | C | `d13a09a` | Uruchomić przesyłanie z brama, brama2, drzwi, parking, podworko, podworko2 i skoda. |
| 28 | C | Nie; istniejący kod | Nagrywać RTSP ciągle, odłączyć kamerę, spowodować błąd katalogu; pozostałe kamery mają działać. |
| 29 | C | `a6a4255` | Test całodobowy, odsłuch dźwięku, kodeki kamer i odzyskanie po błędzie audio. |
| 30 | C | Nie; istniejący kod | Restart serwera i FFmpeg, awaria sieci, korekta czasu i odzyskanie segmentu bez duplikatu. |
| 31 | K | Nie; istniejący kod | Zweryfikować długość i ciągłość segmentów przy rzeczywistym zapisie. |
| 32 | K | Nie; istniejący kod | Sprawdzić kodeki strumienia i pliku, brak ponownego kodowania obrazu oraz CPU. |
| 33 | C | Nie; istniejący kod | Zmiana istniejącego katalogu i przerwana operacja na Ubuntu, bez przeniesienia plików. |
| 34 | K | `a6a4255` | Porównać strukturę FTP/RTSP z istniejącym archiwum. |
| 35 | C | `a6a4255` | Zaindeksować duże archiwum, sprawdzić podmianę plików oraz wydajność. |
| 36 | K | Nie; istniejący kod | Przetestować nazwy 190900.mp4 i Brama_00_20261121190900.mp4 w rzeczywistym archiwum. |
| 37 | K | Nie; istniejący kod | Porównać czas wyświetlany z rzeczywistym czasem nagrania. |
| 38 | K | Nie; istniejący kod | Zaindeksować oryginalne pliki Reolink bez zmiany nazw. |
| 39 | K | `9114505` | Wybrać kamerę i przejść do archiwum z prawami właściwego konta. |
| 40 | C | `9114505` | Kalendarz, dni bez danych, duża liczba plików, szybka zmiana daty i aktualizacja bieżącego dnia. |
| 41 | K | Nie; istniejący kod | Porównać oś z nagraniami zawierającymi przerwy. |
| 42 | K | Nie; istniejący kod | Kliknięcie i dotyk osi, w tym powtórzona godzina DST. |
| 43 | C | `9114505` | Odtworzyć przez granicę plików i dni, również po wielodniowej przerwie. |
| 44 | K | Nie; istniejący kod | Sprawdzić miniatury i ich zgodność z momentem na osi. |
| 45 | K | `9114505` | Przewijanie do przodu i wstecz przez północ, przerwy i zmianę czasu. |
| 46 | K | Nie; istniejący kod | Zmienić prędkość, potwierdzić jej utrzymanie po zmianie pliku. |
| 47 | K | Nie; istniejący kod | Sprawdzić przyciski 5/15/30 min z plikiem i bez pliku w danym czasie. |
| 48 | C | Nie; istniejący kod | Porównać synchronizację kilku rzeczywistych strumieni archiwalnych, przerwy, DST i północ. |
| 49 | K | Nie; istniejący kod | Pobrać klatkę, sprawdzić moment i odmowę bez uprawnienia. |
| 50 | C | `b017995` | Przesłać ruch/osobę/pojazd z integracji, sprawdzić datę i położenie znacznika na osi. |
| 51 | K | `7259223` | Porównać pobrany plik z oryginałem i sprawdzić cofnięcie praw. |
| 52 | K | Nie; istniejący kod | Zaznaczyć zakres myszą i dotykiem, sprawdzić zgodność początku i końca. |
| 53 | C | `7259223` | Wielodniowy eksport z rzeczywistych kamer, dźwięk, przerwy, wydajność i miejsce. |
| 54 | K | Nie; istniejący kod | Porównać pierwszą i ostatnią klatkę z zaznaczonym zakresem. |
| 55 | K | Nie; istniejący kod | Porównać przewidywany czas i raport przerw z gotowym plikiem. |
| 56 | C | Nie; istniejący kod | Eksport z pominięciem przerw i czarną planszą; długi zakres. |
| 57 | K | `7259223` | Porównać oryginały przed i po eksporcie, również błędzie i anulowaniu. |
| 58 | K | Nie; istniejący kod | Wiele równoczesnych zleceń, awaria procesu, wznowienie bez duplikatów. |
| 59 | C | `7259223` | Postęp, anulowanie, powiadomienie i pobieranie na rzeczywistej przeglądarce. |
| 60 | C | Nie; istniejący kod | Sprawdzić osierocone pliki i katalogi po awarii na docelowym Linuxie. |
| 61 | C | `7259223` | Kontrolowana utrata wolnego miejsca w trakcie i przed publikacją; nie naruszać oryginałów. |
| 62 | C | `93aade3` | Obciążyć dysk FTP i RTSP, sprawdzić próg 10 GB i wznowienie zapisu. |
| 63 | C | `a6a4255`, `93aade3` | Test na docelowym systemie plików, granice montowania, symlinki, podmiana plików i równoczesny zapis. |
| 64 | K | `93aade3` | Zweryfikować kolejność usuwania z plikami o offsetach i bez nich. |
| 65 | K | `93aade3` | Sprawdzić rzeczywistą ilość odzyskanego miejsca po usunięciu kolejnych plików. |
| 66 | C | `93aade3` | Sprawdzić zapas i wcześniejszy start pod obciążeniem docelowego dysku. |
| 67 | C | `a6a4255` | Na Ubuntu sprawdzić /proc, FTP, FFmpeg i eksport przy otwartych plikach; odrzucić niepewność. |
| 68 | K | Nie; istniejący kod | Ochrona zakresu i plików, równoczesna rotacja oraz operacje administratora. |
| 69 | C | `93aade3` | Porównać podgląd, chronione pliki, DST, pliki rzadkie i faktyczny wynik rotacji. |
| 70 | C | `7259223`, `a6a4255`, `93aade3` | Odłączyć, podmienić i ponownie podłączyć dysk na VM, sprawdzić wszystkie operacje zapisu. |
| 71 | C | `93aade3` | Asynchroniczny podgląd, długi skan, awaria procesu i odzyskanie zadania w systemd. |
| 72 | C | `74f1725`, `93aade3` | Porównać raport historii z faktyczną rotacją i korektą zegara. |
| 73 | C | `74f1725` | Odtworzyć awarię obrazu, zapisu, go2rtc i dysku na rzeczywistych kamerach. |
| 74 | K | `a6a4255` | Porównać ostatni zindeksowany plik z rzeczywistym zapisem FTP/RTSP. |
| 75 | C | Nie; istniejący kod | Kamera online bez nowych nagrań, zmiana progu, ponowne włączenie i powrót zapisu. |
| 76 | C | `74f1725` | Zatrzymać usługi i FFmpeg, odłączyć dysk, wywołać błąd publikacji i odzyskać usługi. |
| 77 | C | Nie; istniejący kod | Rzeczywisty HA, awaria i powrót endpointu, token, powiadomienia i kolejność. |
| 78 | C | Nie; istniejący kod | Porównać zegary wszystkich kamer ONVIF, Digest/WS-Security, błąd i powrót. |
| 79 | C | `74f1725` | Przejście DST, offsety, powtórzona godzina i stare pliki FTP; test przeglądarek i kamer. |
| 80 | K | Nie; istniejący kod | Wykonać operacje administratora i porównać wpisy audytu, czas oraz tożsamość konta. |

## Zmiany i dowody

- `c8bd556` (11, 14, 16, 18, 21): Jednoznaczne rozwiązywanie aliasów live, odmowa kolizji i ponowna autoryzacja po handshake upstream. Pliki/testy: `live_proxy.py, web.py; test_live_proxy.py, test_web.py`.
- `b017995` (50): Token zdarzenia i aktywność kamery sprawdzane atomowo przy zapisie znacznika. Pliki/testy: `web.py; test_web.py`.
- `7259223` (11, 14, 51, 53, 57, 59, 61, 70): Ponowna kontrola dostępu przed pierwszym fragmentem odpowiedzi i kontrola dysku, miejsca oraz ważności zadania przed publikacją eksportu. Pliki/testy: `files.py, export_service.py, web.py; test_files.py, test_export_service.py`.
- `9114505` (19, 20, 22, 23, 39, 40, 43, 45): Kontrolki dostosowane do wąskiego ekranu; odświeżenie listy zachowuje ID odtwarzanego nagrania lub zatrzymuje usunięte nagranie. Pliki/testy: `static/app.js, static/app.css; browser_smoke.js`.
- `74f1725` (72, 73, 76, 79): Pierwszy nieudany pomiar po awarii lub wygaśnięciu próbki nie odnawia starego stanu obrazu; historia rotacji uwzględnia korektę zegara wstecz. Pliki/testy: `stream_health.py, rotation_service.py; test_stream_health.py, test_rotation_service.py`.
- `d13a09a` (15, 17, 24, 25, 26, 27): Konfiguracja kamer i FTP budowana z jednego snapshotu; brak prywatnego katalogu nie przełącza konta na wspólne. Pliki/testy: `config_reconcile.py; test_config_reconcile.py`.
- `a6a4255` (29, 34, 35, 63, 67, 70, 74): Odczyt metadanych nagrania przez sprawdzony deskryptor z blokadą współdzieloną; zmiana metadanych podczas próby odrzuca wynik. Pliki/testy: `workers.py; test_workers.py`.
- `93aade3` (62, 63, 64, 65, 66, 69, 70, 71, 72): Kontrola tożsamości dysku po blokadzie, przed szybkim zwrotem oraz na końcu rotacji; brak fałszywego sukcesu po utracie dysku. Pliki/testy: `retention.py; test_retention.py`.

Szczegółowy indeks kodu i testów dla wszystkich pozycji: `../IMPLEMENTATION_STATUS.md`. W szczególności brak testów urządzeń dla 12, 23, 27, 77 i 78, brak próby obciążeniowej 16, brak samodzielnej próby urządzenia 20, 46 i 47. Nie odtwarzano istniejącej implementacji ani nie zmieniano uzgodnionych wymagań.

## Regresja

Pełna regresja lokalna na Debianie 13: 542 testy, 538 zaliczonych, 4 pominięte (wymagają przygotowanego Ubuntu 26.04), 0 niepowodzeń i błędów testów; 85,791 s. `node --check src/sunhill_nvr/static/app.js` i `node tests/browser_smoke.js` OK. W logu pojawiły się komunikaty symulowanych awarii i ostrzeżenia ResourceWarning; nie traktowano ich jako niepowodzeń testów. Testy nie potwierdzają odbioru na Ubuntu, w rzeczywistej przeglądarce ani na urządzeniach.

Osiem commitów kodu i testów, wspólne mechanizmy powiązane z 42 wymaganiami; 28 pozycji bez nowej implementacji. Wszystkie 70 pozycji ma wskazanie dowodów lub przeglądu kodu oraz pozostających prób. Statusy 33 K / 47 C / 0 N bez zmiany (dla 11-80: 31 K / 39 C).
