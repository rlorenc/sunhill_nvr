# Zasady pracy nad Sunhill NVR

## Przed zmianą

1. Przeczytaj SPECIFICATION.md, REQUIREMENTS.md, IMPLEMENTATION_STATUS.md, STATUS.md, CHANGELOG.md i HANDOVER.md.
2. Sprawdź `git status --short` i `git log -1 --oneline`; porównaj z przekazanym stanem. Nie odtwarzaj brakującego kodu z pamięci ani nie usuwaj cudzych zmian.
3. Ustal numery wymagań objęte zadaniem oraz warunki odbioru. Zachowaj dotychczasowe funkcje, konstrukcję, API, format danych i kompatybilność; konieczne zmiany niekompatybilne uzgodnij z użytkownikiem.

## Przy każdej zmianie

- Zaktualizuj SPECIFICATION.md tylko w zakresie rzeczywiście zmienionego stanu; treść wymagań zmieniaj wyłącznie po decyzji użytkownika.
- Zsynchronizuj wszystkie 80 pozycji w IMPLEMENTATION_STATUS.md z numeracją i statusem K/C/N w SPECIFICATION.md; dla zmienionych punktów dodaj dowody (pliki, testy, wynik oraz ograniczenia).
- Utrzymuj STATUS.md jako listę prac integracyjnych i CHANGELOG.md jako historię zmian z rzeczywistymi wynikami testów. Nie zapisuj wyników nieuruchomionych testów jako zaliczonych.
- Zaktualizuj HANDOVER.md: baza kodu, stan prac, blokery, następna kolejność i polecenia weryfikacji. Dla każdej zmiany zachowaj historię Git. Nie publikuj kodu zdalnie bez polecenia.
- Testy lokalne nie są dowodem odbioru produkcyjnego. Rozróżniaj funkcję obecną w kodzie, test lokalny, test docelowy i rzeczywisty odbiór. Punkt nie jest zamknięty, jeśli pozostaje jakikolwiek jego niespełniony warunek; nie zwiększaj statusu wyłącznie z powodu większej liczby testów.
- Nie uruchamiaj instalacji, rotacji ani usuwania na prawdziwym archiwum przed właściwym odbiorem. Scenariusze instalacyjne tylko na jednorazowej VM zgodnie z ich zabezpieczeniami.

## Przekazanie

Po zakończeniu partii dostarcz wyniki i ewentualne niezaliczone warunki; zaktualizuj dokumenty i zapisz commit. Wydawaj wersjonowany TAR.GZ z pełnym bundle historii Git, weryfikuj zgodność drzewa z HEAD i kompletność 80 pozycji. Nowy czat ma zaczynać od tego pakietu, nie od założeń o pamięci poprzedniego czatu. Nie deklaruj pracy wykonanej w tle ani automatycznej aktualizacji bez faktycznej zmiany plików.

## Odtwarzanie i pakowanie repozytorium

Pakiet wydania generuj z czystej gałęzi `main`: `python3 scripts/package_release.py --candidate /ścieżka/sunhill-nvr-WERSJA.tar.gz`. Numer w `src/sunhill_nvr/__init__.py` zwiększaj przy każdym wydaniu i utrzymuj widoczny w aplikacji. Skrypt dodaje HEAD do bundle, wykonuje zwykłe git clone i fsck oraz weryfikuje pliki. Nie nadpisuj poprzednich paczek. Odtworzenie: `git clone sunhill-nvr-history.bundle sunhill-nvr-work`.

Od 0.2.9 nie publikuj zweryfikowanego wydania bez zaliczonych rzeczywistych zadań CI Ubuntu 26.04 (`verify`, `install-smoke`, `default-install`: clean i resume). Lokalna paczka `--candidate` musi być oznaczona jako niezweryfikowany kandydat. Testy z zastępczym demonem nie zastępują realnych protokołów.
