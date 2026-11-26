# Wydania od 0.2.0

Wersja SemVer w src/sunhill_nvr/__init__.py, widoczna w aplikacji. Aktualizacje ręczne, bez klucza podpisującego. Każde wydanie jest TAR.GZ z manifestem SHA-256 aplikacji oraz pełnym Git bundle. Nie jest to kryptograficzny dowód pochodzenia: pobieraj paczki tylko z zaufanego źródła.

Od wersji 0.2.3 paczka rozpakowuje się do `sunhill-nvr-WERSJA/`. Czytnik 0.2.3 akceptuje też historyczny katalog `sunhill-nvr/` i odrzuca mieszane katalogi oraz niezgodność wersji katalogu i manifestu. Starsze recovery 0.2.0–0.2.2 nie odczyta nowego formatu; przed aktualizacją potrzebny jest osobny pomost zgodności albo migracja recovery. Nie zweryfikowano tego przejścia na Ubuntu.

Z czystej gałęzi main:

```sh
python3 scripts/package_release.py --candidate /ścieżka/sunhill-nvr-0.2.3.tar.gz
```

Od 0.2.9 lokalne pakowanie z `--candidate` tworzy wyłącznie kandydata. Zwykły `package_release.py` wymaga zadania CI `package-verified` dla aktualnego commita, zależnego od wszystkich testów Ubuntu. Kandydat nie może być przedstawiany jako wydanie zweryfikowane. Nie ma obowiązku klucza podpisu; bramka dotyczy jakości wydania, a nie prawa administratora do instalacji.

Aktualizacja (przykład kolejnej wersji):

```sh
bash install.sh --update /tmp/sunhill-nvr-0.2.1.tar.gz
```

Plik musi być zwykłym plikiem należącym do root, bez zapisu dla grupy i innych. Alternatywnie jako root:

```sh
install -o root -g root -m 0644 /tmp/sunhill-nvr-0.2.1.tar.gz /opt/sunhill-nvr/updates/
```

Następnie wybierz paczkę w panelu administratora. Wydania nie są pobierane ani instalowane automatycznie. Nie pozwalamy aplikacji WWW wgrać dowolnego wykonywanego jako root kodu bez zatwierdzenia root. Aktualizator kontroluje wersję, manifest i ścieżki, a następnie korzysta z dotychczasowego mechanizmu maintenance, kopii konfiguracji i bazy, migracji, kontroli gotowości oraz rollbacku. Aktualizacja aplikacji nie zmienia automatycznie systemowego kontraktu recovery. Dotychczasowe funkcje podpisanych wydań pozostają w kodzie jako opcjonalna ścieżka historyczna, ale nie są wymagane w 0.2.0.

Odbiór rzeczywistego rollbacku, FTP w trakcie maintenance i migracji zbioru nazw jednostek recovery pozostaje otwarty. Instalacja 0.2.1 nie migruje automatycznie starego rozproszonego układu /etc i /var/lib. Testuj na izolowanej VM ze snapshotem.

## Niezależny dostęp IPv4 od 0.2.12

Aktualizacja generuje Nginx z kodu nowego wydania. Przypięty recovery starszej instalacji może przy następnej zmianie domeny lub TLS wygenerować starszy vhost, z przekierowaniem IPv4 lub bez vhosta IP. Na już zaktualizowanej instalacji, z kopią VM, bez oczekujących transakcji, można odświeżyć recovery dotychczasowym migratorem (paczka musi odpowiadać aktywnej wersji i należeć do root):

```sh
systemctl stop sunhill-upgrade-apply.timer sunhill-upgrade-apply.service sunhill-upgrade-ready.service
PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.recovery_migration /tmp/sunhill-nvr-0.2.12.tar.gz
systemctl start sunhill-upgrade-ready.service sunhill-upgrade-apply.timer
```

Migrator sprawdza zgodność jednostek i zachowuje poprzednie recovery. Nie kontynuować zmiany DNS/TLS w razie błędu migratora. Ta procedura nie została wykonana na docelowym Ubuntu.

## CLI od 0.2.13

Po rozpakowaniu nowego archiwum pozostaw je w tym samym katalogu nadrzędnym i wykonaj `bash install.sh --update`. Jeżeli odpowiedniej paczki obok katalogu nie ma, CLI wybiera najwyższy stabilny numer wersji nowszy od aktywnego z root-approved `/opt/sunhill-nvr/updates/`. Nie skanuje dowolnych plików w /tmp, nie pobiera z Internetu. Jawna ścieżka nadal działa. Nie ma nowego obowiązku podpisu; dotychczasowe opcjonalne podpisy CLI recovery pozostają dostępne.

Po pomyślnej transakcji CLI sprawdza origin domeny na 127.0.0.1:8080, HTTP HEAD na loopback oraz wykrytych adresach RFC1918 bez śledzenia przekierowań, CSP WebSocket LAN i 301 HTTP po domenie. Wypisuje adresy dostępowe i DONE tylko po sukcesie. Nie testuje zewnętrznego HTTPS przez Cloudflare. Niepowodzenie po zatwierdzeniu transakcji oznacza niezerowy kod, komunikat o aktywnej wersji, brak DONE; nie wycofuje automatycznie zatwierdzonej transakcji. Dotychczasowy rollback podczas transakcji i osobny migrator recovery pozostają bez zmian.

### Jednorazowa migracja recovery dla 0.2.19

Po pomyślnym `bash install.sh --update` i snapshot VM, gdy brak oczekujących transakcji, uruchom z katalogu rozpakowanego wydania jako root (archiwum obok katalogu). Migrator sprawdza zgodność jednostek i zachowuje poprzednie recovery. Uruchom ponownie timer także wtedy, gdy migracja zakończy się błędem; przy błędzie nie kontynuuj zmian katalogów.

```sh
(
  set -e
  PACKAGE="$(realpath ../sunhill-nvr-0.2.19.tar.gz)"
  test -f "$PACKAGE"
  trap 'systemctl start sunhill-upgrade-ready.service sunhill-upgrade-apply.timer' EXIT
  systemctl stop sunhill-upgrade-apply.timer sunhill-upgrade-apply.service sunhill-upgrade-ready.service
  PYTHONPATH=/opt/sunhill-nvr/current/app PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m sunhill_nvr.recovery_migration "$PACKAGE"
)
```

Sprawdź `grep __version__ /opt/sunhill-nvr/{current,recovery}/app/sunhill_nvr/__init__.py`. Obie wersje powinny wynosić 0.2.19. Procedura nie była wykonana na docelowym serwerze.

## CLI od 0.3.1

Po rozpakowaniu zaufanej dystrybucji jako root, `bash install.sh --update` wystarcza. Jeśli nie ma oryginalnego archiwum obok katalogu ani dokładnej zatwierdzonej paczki w updates, CLI sprawdza manifest rozpakowanego wydania i tworzy minimalną paczkę aplikacji w `/opt/sunhill-nvr/updates/` bez nadpisywania istniejącej. Nie wymaga ręcznego kopiowania. Katalog i pliki manifestu muszą należeć do root, bez zapisu przez grupę lub innych. Odrzucane są symlinki, niespójność wersji i hashy. Pełny TAR.GZ z historią Git warto zachować jako kopię wydania; odtworzony TAR.GZ służy tylko instalacji. Brak automatycznego pobierania, migracji recovery i odbioru docelowego.
