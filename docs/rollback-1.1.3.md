# Przywracanie wersji Sunhill NVR 1.1.3

Kandydat do odbioru na testowej maszynie Ubuntu 26.04. Przed pierwszym wdrożeniem wykonaj niezależną kopię zapasową bazy i konfiguracji. Nie traktuj punktów rollback jako kopii awaryjnej dysku.

## Pierwsza aktualizacja

Rozpakuj zatwierdzoną paczkę 1.1.3 jako root i wykonaj z jej katalogu:

```bash
bash install.sh --update --repair
```

Jeżeli aktualizujesz wersję legacy z FTP i chcesz jednocześnie przejść na RTSP-only, użyj zamiast tego:

```bash
bash install.sh --update --rtsp-only
```

Zwykłe `--update` z dokładnego rozpakowanego 1.1.3 wykrywa brak nowego modułu w przypiętym recovery i wybiera ścieżkę repair. Nie aktualizuj pierwszego 1.1.3 przez starszy GUI/CLI bez przygotowania recovery: stary manager usuwa kopie po sukcesie. Nie da się retrospektywnie odtworzyć kopii wcześniej usuniętych przez stary manager. Po aktualizacji sprawdź:

```bash
bash install.sh --rollback-history
```

## Przywrócenie

W panelu WWW wybierz Administracja / Aktualizacja aplikacji / Przywracanie wersji, datę i wersję oraz potwierdź przywrócenie historycznej bazy. Dostęp tylko administratora. W konsoli root, zawsze z zachowanego katalogu 1.1.3 lub nowszego:

```bash
bash install.sh --rollback                 # najnowszy dostępny punkt inny niż bieżąca wersja
bash install.sh --downgrade 1.0.2            # najnowsza kopia wybranej wersji, jeśli istnieje
bash install.sh --rollback-history          # lista punktów
```

Interaktywnie trzeba wpisać YES. W skrypcie świadomie dodaj `--yes`. Po przywróceniu starej wersji jej WWW może nie oferować ręcznego rollbacku; nadal działa konsola z katalogu 1.1.3, ponieważ przypięty kod recovery pozostaje nowszy.

## Dane i ograniczenia

- Pięć ostatnich punktów udanej zmiany wersji, także po ręcznym przywróceniu. Każda kopia SQLite zawiera ustawienia, użytkowników, kamery, indeks, historię AI i profile z momentu jej wykonania. Cofnięcie przywraca również stare hasła i uprawnienia; wszystkie sesje zostają unieważnione. Użyj ówczesnych danych logowania. Nie jest to bezstratna migracja wstecz.
- Metadane w kopiach mogą być starsze od bieżącego archiwum (retencja aktywnej bazy bez zmian); kopie prywatne root, bez osobnego przechowywania obrazów. Rozmiar dysku zależy od bazy (maksimum pojedynczej kopii w dotychczasowym adapterze 10 GiB, plus przejściowa kopia transakcji). Nie kasuje się historycznych katalogów kodu.
- Pliki nagrań i eksportów nie są kopiowane ani usuwane przez transakcję. Indeks w przywróconej bazie może nie zawierać najnowszych nagrań; istniejący indeksator może je ponownie zaimportować. Kamery dodane później mogą wymagać ponownego dodania. Po powrocie starsze zasady rotacji i zapisu ponownie obowiązują.
- Nie odtwarzamy zewnętrznych pakietów, certyfikatów, modeli, zmian innych niż zarządzane jednostki/Nginx. Zmieniony `config.json` lub tryb TLS blokuje przywracanie; nie przełączamy niejawnie ścieżek archiwum. Starszy wariant FTP wymaga nadal zainstalowanego ProFTPD. Jednostki recovery muszą być zgodne; sam manager pozostaje aktualny.
- Nieudana operacja przywraca stan wyjściowy z nowej kopii. Przerwanie ma istniejącą ścieżkę odzyskiwania. Jeśli health check lub recovery nie przejdzie, kontroluj `transaction.json`, status usług i maintenance; nie usuwaj ręcznie kopii ani flagi. Jeżeli przywrócenie zakończy się sukcesem, zachowany zostaje również punkt wersji opuszczonej. `--rollback` może więc przywrócić nowszą wersję po downgrade.
- Scenariusz CI rzeczywistego powrotu FTP/RTSP jest dodany, ale nie został jeszcze wykonany. Dotychczasowe testy są lokalne/symulowane. Nie traktuj kandydata jako wydania po odbiorze produkcyjnym.
