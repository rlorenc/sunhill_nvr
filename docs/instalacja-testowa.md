# Instalacja testowa Sunhill NVR 0.2.3

Użyj osobnej VM Ubuntu Server 26.04 LTS, ze snapshotem i dostępem do repozytoriów pakietów. Nie podłączaj jedynej kopii nagrań. Nie uruchamiaj instalatora na hoście z działającym NVR lub ProFTPD.

```sh
tar -xzf sunhill-nvr-0.2.3.tar.gz
cd sunhill-nvr-0.2.3
bash install.sh
```

Polecenia jako root. Domyślnie nvr.local, media w /opt/sunhill-nvr/media. Instalator pyta tylko o nazwę DNS; `bash install.sh nvr.example.com` pomija pytanie. `bash install.sh --check nvr.example.com` jest opcjonalnym preflightem. Nie wymaga flagi eksperymentalnej ani klucza wydawcy. APT i pobranie go2rtc wymagają sieci; opcjonalnie `SUNHILL_GO2RTC_FILE` wskazuje lokalną binarkę z przypiętym SHA-256.

Dla domeny publicznej ustaw Cloudflare Tunnel na http://127.0.0.1:8080. Dla .local instalator generuje lokalny certyfikat TLS (klient musi zaufać lokalnemu CA). Kod jednorazowy z konsoli pozwala utworzyć pierwszego administratora; alternatywnie `SUNHILL_INSTALL_CONSOLE_ADMIN=1 bash install.sh` tworzy go w konsoli. Nie myl tego kodu z usuniętym wymaganiem klucza aktualizacji.

Po zalogowaniu: Administracja > Ustawienia dysku. Wybierz katalogi nagrań, eksportów i opcjonalnie bufor, zgodnie z [dysk i TLS](storage-and-tls.md). Nagrania i eksporty domyślnie znajdują się w media/kamery oraz media/.sunhill-exports. Przy zmianie lokalizacji nie są usuwane poprzednie dane. Po testach sprawdź autostart, nagrywanie FTP/RTSP, podgląd, eksport, rotację i aktualizację z rollbackiem. Scenariusz Ubuntu w tests/ubuntu_install_smoke.sh nie został jeszcze uruchomiony w docelowym środowisku.

To czysta instalacja; brak automatycznej migracji starszej instalacji z /etc lub /var/lib. Nie usuwaj starego systemu, jeśli zawiera dane. Aktualizacje między wydaniami 0.2.x: [wydania](releasing.md).

## Ponowienie nieudanej instalacji 0.2.0

Jeżeli instalacja 0.2.0 zatrzymała się na `Directory not writable by application: /opt/sunhill-nvr/media/kamery`, rozpakuj nowe wydanie do osobnego katalogu i uruchom jako root:

```sh
bash install.sh --retry-incomplete
```

Można podać dotychczasową nazwę DNS po opcji. Tryb dotyczy wyłącznie wczesnej awarii z domyślnym katalogiem mediów. Sprawdza brak bazy, nagrań, konfiguracji, zainstalowanych jednostek Sunhill i aktywnego ProFTPD. Zachowuje stary katalog w `/opt/sunhill-nvr-incomplete.XXXXXXXX/original` i konto usługi, niczego nie kasuje. Jeżeli warunki nie są spełnione, przerywa pracę; nie omijaj kontroli. Działający Nginx po poprzedniej próbie może zajmować wymagany port 80; zatrzymaj go przed retry tylko jeśli jest to dedykowany serwer testowy i nie obsługuje żadnej innej aplikacji. Na czystej VM nadal wystarczy `bash install.sh`.
