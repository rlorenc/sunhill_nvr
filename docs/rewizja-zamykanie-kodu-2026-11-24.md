# Rewizja przed odbiorem - 24.11.2026

Punkt wyjścia: `9d1ed64` (135 commitów). Wszystkie 178 śledzonych plików wejściowego ZIP porównano bajtowo z tym commitem. Stary bundle zawierał main bez HEAD, przez co zwykłe `git clone` nie wybierało gałęzi automatycznie. Weryfikacja bazowa: 598 testów, 594 OK, 4 pominięte, 0 błędów.

## Zmiany rzeczywiście wykonane

| Commit | Kod i testy | Dowód |
| --- | --- | --- |
| `f899c56` | Blokada bezpośredniego API (poza GET /internal/health), autoryzacji produkcyjnego relay, pobierania nowych zadań eksportu i okresowych zadań przed zatwierdzeniem wersji. Ponowny test maintenance przed sprzątaniem eksportów. | test_cli.py, test_live_proxy.py, test_web.py |
| `44306dc` | Zamykanie połączeń SQLite w pięciu plikach fixture przy zachowaniu semantyki transakcji. Nie jest to zmiana schematu ani produkcyjnej bazy. | test_export_service.py, test_rtsp_integration.py, test_upgrade_database.py, test_upgrade_integration.py, test_upgrade_transaction.py |
| `efc166b` | Powtarzalne tworzenie paczki czystego main wraz z HEAD, klonowaniem, fsck i porównaniem bajtów. | test_package_handover.py (2 testy) |
| `b98ea2e` | Ponowna kontrola maintenance podczas długiego transferu pliku; zamykanie uchwytu i zwolnienie blokady archiwum przy odrzuceniu nagłówków przez WSGI. | test_web.py |
| `ec0f67f` | Trwały zapis fazy rolled_back przed startem starej wersji. Ponowienie po nieudanym health checku nie odtwarza po raz drugi starej kopii bazy. | test_upgrade_transaction.py (13 testów celowanych OK), scenariusz zachowania wartości zapisanej przez starszego workera po rollbacku. |

Zmiany nie zastępują testów z rzeczywistymi usługami. Blokada jest sprawdzana przy uruchamianiu zadań i okresowej autoryzacji; nie jest obietnicą atomowego zatrzymania każdego możliwego zapisu. Aktualizator nadal musi zatrzymywać i weryfikować procesy przed migracją. Zachowano dotychczasowe API i schemat danych. Nie zmieniano kontraktu czterech stabilnych jednostek recovery, od których zależy bezpieczeństwo startu; ich zmiana nazw/liczby pozostaje nieobsługiwana.

## Otwarte warunki

| Punkt | Brakujący dowód lub ograniczenie |
| --- | --- |
| 1 | Pełna instalacja i reboot Ubuntu 26.04 na jednorazowej VM. |
| 2 | Instalacja wszystkich zależności i uruchomienie usług na Ubuntu. |
| 5 | Podpisana aktualizacja, awaria, rollback oraz porównanie danych na docelowym systemie; kompatybilność mechanizmu recovery i ścieżek zapisu. |
| 18 | Rzeczywiste go2rtc i dekodowanie obrazu oraz audio w przeglądarce. |
| 23 | Odbiór interfejsu na desktopie, iPhonie i tablecie. |
| 24 | ProFTPD, transfer, izolacja sesji, restart i odzyskiwanie. |
| 27 | Test fizycznych kamer oraz plików z rzeczywistych transferów FTP. |

Środowisko: Debian 13, PID 1 supervisord, brak go2rtc i ProFTPD, nie jest to docelowa VM. Nie uruchomiono pełnego CI Ubuntu ani testów fizycznych urządzeń. Nie wykonano push (brak właściwego zdalnego repozytorium). Statusy 73 K / 7 C / 0 N pozostają bez zmian; nie potwierdzono ukończenia 80 punktów ani wyczerpującego audytu wszystkich ścieżek. Nie używać z prawdziwym archiwum przed odbiorem.

## Wyniki końcowe

Pełna regresja na `ec0f67f`: 606 testów, 602 zaliczone, 4 pominięte (wymagają przygotowanego Ubuntu 26.04), 0 błędów i niepowodzeń, 103,175 s. `PYTHONWARNINGS=always::ResourceWarning`: nie odnotowano ostrzeżeń SQLite; pozostało jedno ostrzeżenie otwartego pliku w pomocniczym procesie testu `test_proc_scan.py`. Nie jest to dowód braku wszystkich wycieków na produkcji. Test DOM JavaScript, kontrola składni JS, kompilacja Python i osobne `bash -n` każdego skryptu przeszły.
