# Rewizja kodu po partii siedmiu wymagań - 24.11.2026

Baza: `0c252011f596ac1e7cdf40e42c47249250a94311`. Odtworzono Git z przekazanego bundle; wszystkie 177 śledzonych plików wejściowego ZIP porównano bajtowo z bazą. Bez rozbieżności; jedyny dodatkowy plik to bundle. Audyt celowany, nie ponowny wyczerpujący przegląd wszystkich 80 funkcji. Nie zmieniono API, schematu DB ani uzgodnionych wymagań.

| Commit | Usterka | Test regresyjny | Wynik celowany |
| --- | --- | --- | --- |
| `7da502d` | Pierwsza porażka kontroli usługi odświeżała timestamp starego dodatniego stanu. | `test_first_failed_sample_does_not_renew_active_status` | 7/7 |
| `55a21c3` | Błąd aktywacji mógł cofnąć konfigurację do niekompletnego zestawu lub plików z nieprawidłowymi prawami; naprawa praw nie zawsze wymuszała restart właściwego demona. | `test_unsafe_previous_snapshot_is_not_restored_after_failed_activation`, `test_missing_previous_file_is_not_a_rollback_target` | 13/13 |
| `2a0acdc` | Finalizacja odzyskiwania zmiany katalogu nie odnawiała blokady po przerwaniu usuwania maintenance i zakładała uruchomienie usług. | `test_finalize_reestablishes_fence_after_interrupted_cleanup` | 5/5 |
| `981af33` | Awaryjne przywracanie podglądu przez kontroler mogło uruchomić bramkę po nieudanej aktywacji, mimo utrwalonego znacznika pending. | `test_pending_activation_never_restarts_unverified_gateway_on_error` | 14/14 |

Pierwsza pełna regresja, po dwóch pierwszych commitach: 596 testów, 592 OK, 4 pominięte, 0 błędów, 116,934 s. Ta regresja nie obejmuje ostatnich dwóch commitów, które wykonano po rozpoczęciu testu. JavaScript DOM, kontrola składni JS i kompilacja Python OK. Zmieniony kod i testy: 8 plików, +110/-13 linii względem bazy przed dokumentacją.

Ograniczenia: Debian 13, Python 3.13.5, PID 1 supervisord; FFmpeg i Nginx dostępne, go2rtc i ProFTPD nieobecne. Nie uruchomiono instalatora ani rzeczywistego systemd, workflow Ubuntu, Chrome z go2rtc ani fizycznych kamer. Nie wykonano push (origin jest lokalnym bundle). Wciąż otwarte C: 1, 2, 5, 18, 23, 24, 27. Nie zmieniono klasyfikacji 73 K / 7 C / 0 N. Nadal do zweryfikowania m.in. migracja zbioru nazw jednostek recovery, wszystkie ścieżki zapisu podczas aktualizacji oraz źródła ostrzeżeń SQLite ResourceWarning. Testy z atrapami systemd nie potwierdzają zachowania rzeczywistych demonów.

## Końcowa regresja

Na kompletnym kodzie `981af33`: 598 testów, 594 zaliczone, 4 pominięte, 0 błędów i niepowodzeń, 109,702 s. Wszystkie pięć nowych przypadków przeszło. Pominięte są cztery testy z `test_ubuntu_config.py`, wymagające przygotowanego Ubuntu. Kontrola składni każdego skryptu shell osobno, kompilacja składniowa Python, kontrola JS i test DOM OK. Nadal występują ostrzeżenia o niezamkniętych połączeniach SQLite. Nie uruchamiano testów VM ani rzeczywistego Chrome.
