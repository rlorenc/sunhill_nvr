# Zamykanie kodu - etap 4, 24.11.2026

Baza `c42dece`. Wprowadzono osiem commitów implementacji i testów. Status kodu: 73 K / 7 C / 0 N. K nie jest odbiorem produkcyjnym.

| Commit | Wymagania | Dowód |
| --- | --- | --- |
| `fa61955` | 8, 9 | Spójny snapshot panelu admina; brak danych uwierzytelniających; ponowna autoryzacja sesji w transakcji; zakończenie podglądu w ukrytej zakładce. `test_web.py`, `browser_smoke.js`. |
| `814cca6` | 1, 2, 24, 27 | Gotowość usług rozpoznaje fragmentowaną linię statusu i odrzuca błędną odpowiedź; ograniczony czas. Test rzeczywistych połączonych gniazd, nie rzeczywistego ProFTPD/go2rtc. |
| `15a72dd` | 9, 18, 23 | Jawne sterowanie dźwiękiem i ponowne połączenie; starsza odrzucona obietnica play() nie nadpisuje nowszego wyboru; CSS kontrolek mobilnych. Test DOM, nie rzeczywista przeglądarka. |
| `9b6795e` | 5 | Wspólne finalizowanie odzyskiwania online/offline: ponowna blokada, start i health check przed sprzątnięciem dziennika. Testy awarii po rollbacku i po przerwanym sprzątaniu. |
| `79e411b` | 8 | Liczba kamer ONVIF w snapshot zgodna z dotychczasowym endpointem. |
| `86db39d` | 5 | Scenariusz Ubuntu: podpisana wersja celowo zatwierdza zmianę bazy, po czym migracja kończy się błędem; sprawdzane są aktywna wersja, konta, kamery i bajty nagrania. Sprawdzona składnia, wykonanie VM otwarte. |
| `518f741` | 2, 24, 26, 27 | Trwała intencja aktywacji konfiguracji, fsync plików/katalogów, ponowienie restartu demonów po przerwaniu. Testy przerwania po publikacji i błędu fsync. |
| `71b4064` | 8 | Natychmiastowy refresh administracji i widoku kamer po utworzeniu kamery; test DOM. |

## Audyt punktów 8 i 9

Poniższy audyt dotyczy obecności kodu i lokalnych testów. Test DOM to kontrolowany stub, nie przeglądarka. API pozostaje kompatybilne z wcześniejszymi ścieżkami.

| Panel | Funkcje | Dowody w kodzie i testach |
| --- | --- | --- |
| Admin | Tworzenie/edycja/usuwanie/wyłączanie kamer, ONVIF, przypisania FTP | `index.html`, `app.js`, `web.py`; `test_web.py` (camera credentials, camera removal, ONVIF, FTP scope), `browser_smoke.js` (refresh panelu). |
| Admin | Konta, role, hasła, uprawnienia do kamer i pobierania, konta FTP | `test_web.py` (user lifecycle, last admin, grants, revocation, FTP shared and specific). |
| Admin | Dysk, limity, rotacja/podgląd/historia, HA, monitoring, audyt | `test_web.py` (storage limits, rotation previews, integration, audit), `test_rotation_service.py`. |
| Admin | Zmiana domeny i TLS, przesyłanie podpisanej aktualizacji, status i alarmy usług | `test_web.py` (domain, TLS, update upload, health), `test_upgrade_transaction.py`, `test_domain_change.py`, `test_tls_apply.py`. |
| Użytkownik | Logowanie, własne hasło, dostęp do przydzielonych kamer, układy | `test_security.py`, `test_web.py` (login, separate accounts, layouts, own password), `browser_smoke.js` (reset kontekstu). |
| Użytkownik | Podgląd, jakość, dźwięk, powiększanie, odłączanie po zmianie widoku | `app.js`, `browser_smoke.js` (alias, mute, reconnect, stale play, hidden view), `test_live_proxy.py`. Rzeczywiste dekodowanie osobno w punkcie 18. |
| Użytkownik | Archiwum, kalendarz, oś czasu, zdarzenia, wiele kamer, klatki, eksport, ochrona plików | `test_web.py` (archive, playback, protection, export, grants), `browser_smoke.js` (navigation, seek, DST, sync, export), `test_export_service.py`. |

Po poprawieniu konkretnych braków p. 8 i 9 przechodzą C → K zgodnie z definicją kodu. Odbiór wszystkich operacji w rzeczywistej przeglądarce oraz na urządzeniach pozostaje obowiązkowy. Nie zmieniono wymagań ani definicji statusów.

## Pozostałe C

| Punkt | Pozostały warunek |
| --- | --- |
| 1 | Pełna instalacja na świeżym Ubuntu 26.04, restart oraz sprawdzenie zachowania danych. |
| 2 | Rzeczywista instalacja zależności, sprawdzenie systemd i komunikacji z demonami. |
| 5 | Kompatybilność między wersjami, rzeczywisty rollback po błędzie i przerwaniu. Migracja recovery zachowuje cztery stabilne nazwy jednostek; nie obsługuje zmiany ich zbioru. |
| 18 | Rzeczywisty strumień przez go2rtc z obrazem i dźwiękiem, odtwarzany przez przeglądarkę. |
| 23 | Rzeczywiste renderowanie, gesty i odtwarzanie desktop/iPhone/tablet. |
| 24 | Rzeczywisty ProFTPD: transfer, chroot, zmiana konfiguracji, restart i odzyskanie. |
| 27 | Konfiguracja i transfer z kamer brama, brama2, drzwi, parking, podworko, podworko2, skoda. |

## Weryfikacja

Debian 13, PID 1 supervisord. Bez działającego systemd, ProFTPD i go2rtc; próba uzyskania przypiętej binarki go2rtc nie powiodła się. Nie obchodzono ograniczeń nawigacji Chromium. Nie uruchomiono workflow Ubuntu ani nie zmieniano produkcyjnego archiwum. Brak skonfigurowanego repozytorium zdalnego NVR, origin jest lokalnym bundle. Testy nie zastępują odbioru na VM.

- Pierwsza regresja (`fa61955`): 578 testów, 574 OK, 4 pominięte, 0 błędów.
- Druga regresja (`86db39d`): 582 testy, 578 OK, 4 pominięte, 0 błędów, 100,457 s.
- Testy celowane po `518f741`: config reconcile 11/11, controller 13/13.
- Aktualny test DOM po `71b4064`, składnia JS, skryptów bash i scenariusza Python Ubuntu: OK.
- Końcowa pełna regresja (`71b4064`): 584 testy, 580 zaliczonych, 4 pominięte (przygotowane Ubuntu), 0 niepowodzeń i błędów, 100,025 s. Nadal występują ostrzeżenia ResourceWarning SQLite. Testy scenariuszy Ubuntu tylko sprawdzone składniowo.
