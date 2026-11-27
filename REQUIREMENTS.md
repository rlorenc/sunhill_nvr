# Wymagania Sunhill NVR

Jedynym nadrzędnym źródłem treści uzgodnionych wymagań jest [SPECIFICATION.md](SPECIFICATION.md), zawierający wszystkie pozycje 1-80 wraz z wyłączeniami z zakresu. Nie tworzymy drugiej niezależnej kopii specyfikacji. Numeracja jest trwała; nie wolno usuwać lub scalać wymagań bez decyzji użytkownika.

| Numery | Obszar |
| --- | --- |
| 1-14 | Instalacja i dostęp |
| 15-38 | Kamery i zapis RTSP |
| 39-61 | Archiwum i eksport |
| 62-80 | Rotacja i monitoring |

Wyłączenia pozostają dokładnie takie jak w SPECIFICATION.md. Wycofanie nieudanej aktualizacji pozostaje wymagane. Zmiana zakresu wymaga jawnej decyzji użytkownika i wpisu w CHANGELOG.md.

Stan wykonania: [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md). Zasady utrzymywania dokumentacji: [AGENTS.md](AGENTS.md). Stan przekazania: [HANDOVER.md](HANDOVER.md).

Od 0.2.0 decyzją użytkownika: prosta instalacja bez flagi i klucza wydawcy, ręczne aktualizacje z TAR.GZ, numer wersji w aplikacji, trwałe dane w /opt/sunhill-nvr z możliwością niezależnego wskazania nagrań i eksportów w panelu. Zobacz punkty 1, 5, 8, 33 specyfikacji.

Decyzja użytkownika (25.11.2026, 0.2.10): minimum 6 znaków dla haseł administratora, użytkowników i kont FTP, bez wymogu znaków specjalnych; pozostają maksima 1024/128 i dotychczasowe zabezpieczenia. Punkty 4, 7–10, 26.

Decyzja użytkownika (25.11.2026, 0.2.12): dostęp po IP i domenie ma być niezależny, bez przekierowania z IP na domenę. Punkt 13: prywatny IPv4 HTTP (LAN/loopback); HTTPS domeny bez zmian.

Decyzja użytkownika (25.11.2026, 0.3.2): bez listy plików i bez wyświetlania UTC w archiwum; jedna oś całej doby, automatyczny start odtwarzania, pozostałe funkcje dostępne kontekstowo. Punkty 37, 39–43, 51.

Decyzja użytkownika (25.11.2026, 0.3.3): ręczna zmiana położenia i rozmiaru okien podglądu na żywo bez konieczności zapisu presetu; istniejące zapisane układy zachowane. Punkty 19, 22.

Decyzja użytkownika (0.3.6): dodać fizyczne sterowanie kamerą, w tym reflektor i zoom optyczny. Pierwszy adapter Reolink dla administratora; pozostałe protokoły i ruch kierunkowy do dalszej integracji. Punkty 19 i 80.

Decyzja administratora 0.3.12 (poz. 70): magazyn niezależny od fizycznego dysku, wymagany punkt montowania zamiast UUID; naprawa aktualizacji z terminalnego rollbacku bez kasowania danych.

Decyzja użytkownika 0.3.13 (poz. 35–36): przyrostowy import istniejących plików, ręczny zakres kamery i dat, wstrzymanie oraz postęp bez kasowania indeksu.

Decyzja użytkownika 0.3.17 (poz. 19, 22, 35, 63, 67, 68): dwuklik wideo Live powiększa i przywraca kafelek; automatyczne usuwanie potwierdzonych pustych nagrań podczas indeksowania po dodatkowej weryfikacji, natychmiastowa informacja o przyjęciu zlecenia.

Decyzja użytkownika 1.0.2 (poz. 1, 5, 39–40, 50): jedna dotychczasowa baza SQLite, natywne zdarzenia AI kamer Reolink zgodne z ich ustawieniami, historia i przejście do nagrania, prostokąty tylko z rzeczywistymi metadanymi, bez miniatur i dodatkowych plików zdarzeń; zachowana instalacja od zera.

Decyzje użytkownika 1.1.0 (poz. 1–2, 15–32, 39–50, 76, 80): jedno wydanie obejmujące dwukierunkowy głos, zoom cyfrowy i przesuwanie, trwały układ per użytkownik, wszystkie wykryte ramki, śledzenie, edytor reguł przestrzennych, przekroczenie linii, wtargnięcie, pozostawione przedmioty, zasłonięcie, rozpoznawanie twarzy i tablic, wyszukiwanie zmian w obszarze archiwum, restart i firmware kamer, rozszerzenia ONVIF M/T, wycofanie FTP, instalator i CI. Logo prowadzi do Live. Retencja metadanych wynika z faktycznie dostępnego archiwum; nie zmienia retencji MP4. Szczegóły implementacji, ograniczenia i odbiór: docs/analytics-1.1.0.md.

Decyzja użytkownika 1.1.2 (poz. 19/39/50): dodawanie i aktualizacja zdjęć profili twarzy w aplikacji WWW, również przez bezpośrednie połączenie w LAN; CLI pozostaje dostępne.

Decyzja użytkownika 1.1.3 (poz. 5): ręczny rollback/downgrade z konsoli i WWW, ostatnie pięć punktów z kopią SQLite i usług, automatyczne odzyskanie w razie błędu, bez operacji na plikach nagrań. Przywrócenie bazy wymaga potwierdzenia. Szczegóły i ograniczenia w SPECIFICATION.md i docs/rollback-1.1.3.md.

### 1.1.4 - zmiany uzgodnione 2026-11-27

- UI: kompaktowe ustawienia sprzętowe, menu użytkownika otwierane kliknięciem (ustawienia, wyloguj), brak osobnej zakładki zdarzeń; historia pozostaje. Legenda kolorów zdarzeń archiwum. `static/app.js`, `app.css`, `index.html`.
- Diagnoza urządzenia i zapis w SQLite (migracja 14); deklaracje możliwości Reolink, odczyt SDP G.711, usługi ONVIF; nie pokazujemy reflektora i talkback w RLC-510A. Restart oparty na dostępności usługi Device, firmware na deklaracji HTTPFirmwareUpgrade. `device_diagnosis.py`, `web.py`. Nie jest to certyfikacja wszystkich modeli ani odbiór sprzętowy.
- Lokalny obszar ruchu, niezależny od semantycznej klasyfikacji: `motion_boxes.py`, retencja powiązana z archiwum; 320x180, 2 fps, maksymalnie 32 kamery; nie rekonstruuje brakujących historycznych współrzędnych. Wydajność i jakość na realnym sprzęcie niezmierzone.
- Wycofano aktywne FTP: usunięto moduły kont i AppArmor FTP, API/GUI kont, polecenia konfiguratora; instalator i konfiguracja tylko RTSP. Zachowano historyczny schemat, migrację, niskopoziomowe szablony i odczyt stanu usług starszych wydań niezbędne dla zgodności/rollbacku; brak globalnego purge ProFTPD. `--resume` dla 0.2.x wymaga oryginalnego 1.1.3, potem `--update --rtsp-only`.
- Weryfikacja: 41 testów celowanych zaliczonych po poprawkach (`test_camera_diagnosis_motion`, `test_recovery_migration`, `test_rtsp_only_deployment`, `test_security.PasswordFormTests`, `test_cli`, 4 scenariusze `test_web`), browser smoke i składnia JS/Bash. Wcześniejsza regresja 812: 804 zaliczone, 5 błędów/niepowodzeń, 3 pominięte; pięć problemów poprawiono i ponownie sprawdzono celowo. Pełna regresja finalnego drzewa i zmienione scenariusze GitHub Actions na Ubuntu 26.04 nieuruchomione. Statusów odbioru 80 pozycji nie podniesiono. Kandydat, nie wydanie zweryfikowane; nie zmieniano instalacji ani zdalnego repozytorium.

### 1.1.5 - zmiany uzgodnione 2026-11-27

- Jeden przycisk LED w podglądzie i ustawieniach: stan potwierdzony odczytem z kamery, odświeżenie po poleceniu; nieznany stan wymaga ponownego odczytu. Baichuan sprawdza również alternatywną komendę stanu. Zachowano uprawnienia i audyt.
- Diagnoza talkback: unieważnienie starego cache, sprawdzanie głównego i dodatkowego RTSP, obsługa kierunku SDP na poziomie sesji; dla Reolink dodatkowa negocjacja natywnego Baichuan TalkAbility i IMA-ADPCM (8/16 kHz, mono). Zapisywany jest potwierdzony transport. Brak zgadywania według nazwy Lumus. Testy protokołu z symulowanym urządzeniem; fizyczne Lumus, Cloudflare i inne warianty firmware nadal wymagają odbioru.
- Zgodnie z decyzją użytkownika wyłączono detekcję paczki, przekroczenia linii, wtargnięcia i pozostawionego przedmiotu; brak nowych zdarzeń z tych mechanizmów, usunięte opcje i kolory oraz filtrowanie starych wpisów w archiwum i nakładkach. Historyczne rekordy i nieaktywne definicje reguł pozostają dla zgodności bazy i rollbacku do czasu obowiązującej retencji; zapis nagrań i wyszukiwanie ruchu w obszarze bez zmian. Pozostałe klasy i prostokąty ruchu zachowano. Żółty ruch #f8ed36, pomarańczowy pojazd #ff7900, turkusowy opisany jako tablica rejestracyjna; zdarzenie ANPR tylko po rzeczywistym odczycie numeru, bez numeru w samym znaczniku, z retencją powiązaną z archiwum.
- Weryfikacja: 55 testów celowanych (test_reolink_talk, test_talkback, test_camera_diagnosis_motion, test_camera_events, test_object_analytics, test_baichuan_control, test_local_analytics, test_local_objects, test_metadata_retention i 4 scenariusze test_web) zaliczonych; node --check, browser_smoke.js oraz rzeczywisty Chromium live_controls_browser.py (11 kamer i obie zmiany stanu LED) zaliczone. Nie wykonano pełnej regresji ani CI Ubuntu 26.04; nie podniesiono statusów odbioru K/C/N. Kandydat, nie zweryfikowane wydanie. Brak zmian zdalnego repozytorium i produkcji.

Decyzja użytkownika (27.11.2026, 1.1.6): wycofanie obsługi FTP i migracji; tylko RTSP. Punkty 24–27 zastąpiono wymaganiami braku FTP; zgodny rollback pozostaje. Ramki detekcji bez tekstu, przełącznik osobisty wspólny dla Live i Archiwum.
