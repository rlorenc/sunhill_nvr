# Katalog nagrań i dostęp lokalny TLS

Kod opisany poniżej jest eksperymentalny i wymaga testów na Ubuntu 26.04.

## Zmiana katalogów nagrań i eksportów (0.2.0)

W panelu administratora > Ustawienia dysku wybierz istniejące katalogi nagrań i eksportów. Mogą znajdować się na różnych lokalnych systemach plików. Bufor segmentów musi być na tym samym systemie plików co nagrania; opcjonalnie wybierz go jawnie. Ścieżki mają być rzeczywiste, bez symlinków, zapisywalne przez użytkownika sunhill i nie mogą nakładać się na prywatne katalogi aplikacji lub systemowe. Sprawdź montowanie dysków przed wyborem.

Domyślnie wszystkie trzy katalogi są pod /opt/sunhill-nvr/media. Zmiana nie przenosi starych nagrań; indeks powstaje dla nowej lokalizacji. Gotowe eksporty są kopiowane bez nadpisywania i bez kasowania źródła; wymagana jest przestrzeń na kopię. Zadania oczekujące lub przerwane są oznaczane jako nieudane, a zapis jest chwilowo wstrzymywany. W razie błędu mechanizm odzyskiwania przywraca konfigurację i bazę. Stare pliki pozostają. Test całej operacji z rzeczywistym ProFTPD i montowaniem na Ubuntu pozostaje otwarty.

## Pierwszy administrator

Eksperymentalny instalator domyślnie wyświetla na konsoli jednorazowy kod ważny
24 godziny. Po skonfigurowaniu dostępu HTTPS kod wpisuje się w sekcji pierwszej
konfiguracji na ekranie logowania i ustala konto administratora. Po utworzeniu
konta kod jest unieważniany. Kod nie zastępuje hasła użytkownika.

Jeżeli kod wygaśnie przed utworzeniem konta, można go ponowić na konsoli:

```sh
sudo -u sunhill env PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.cli renew-setup
```

Polecenie odmawia wydania kodu, gdy istnieje jakiekolwiek konto. Można także
wybrać istniejący tryb zakładania administratora w konsoli, ustawiając
`SUNHILL_INSTALL_CONSOLE_ADMIN=1` podczas uruchamiania instalatora.

## Certyfikat HTTPS dla LAN

Panel administratora przyjmuje plik PEM z pełnym łańcuchem i plik PEM z
niezaszyfrowanym kluczem prywatnym. Nazwa musi pasować do skonfigurowanej domeny,
certyfikat musi pozostać ważny co najmniej 24 godziny i mieć łańcuch zaufany
przez system serwera. Prywatny klucz nie jest zwracany przez API. Żądanie jest
przekazywane do uprzywilejowanej usługi, która waliduje dane, sprawdza
konfigurację Nginx i w razie błędu przywraca poprzednią konfigurację.

Przerwana zmiana jest odtwarzana przed startem Nginx. Zaufanie certyfikatu na
urządzeniach użytkownika nadal zależy od ich własnych magazynów CA i konfiguracji
DNS. Ta funkcja nie konfiguruje tunelu Cloudflare ani nie wystawia certyfikatu.

Jeżeli baza została już zainicjalizowana, lecz nie istnieje żadne konto, można dokończyć konfigurację bez przeglądarki: `sudo -u sunhill env PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.cli complete-setup`. Operacja nie nadpisuje bazy i unieważnia kod jednorazowy.

### Opcjonalny prywatny CA dla LAN

Jeśli administrator nie ma zaufanego certyfikatu, może na testowej VM wybrać
`SUNHILL_GENERATE_LAN_TLS=1` podczas instalacji (bez podawania plików PEM;
OpenSSL musi być dostępny już przed preflightem). Na istniejącej instalacji
z nowym kodem odzyskiwania można użyć zaufanej konsoli root:

```sh
sudo env PYTHONPATH=/opt/sunhill-nvr/recovery/app /usr/bin/python3 -m sunhill_nvr.upgrade_admin generate-lan-tls
```

Opcja `--next-hostname nvr2.example.com` pozwala uwzględnić przyszłą nazwę DNS
przed zmianą domeny w panelu. Publiczny CA znajduje się pod
`/opt/sunhill-nvr/config/tls/current/local-ca.pem`. Przekaż wyłącznie ten publiczny plik
zaufanym kanałem, sprawdź jego odcisk i zainstaluj go w magazynie zaufania
każdego klienta. DNS w LAN musi wskazywać adres NVR; nie używaj adresu IP
zamiast nazwy certyfikatu ani nie omijaj ostrzeżeń TLS. Prywatny klucz CA nie
jest zachowywany. Każde ponowne generowanie tworzy nowy CA (wymagany ponowny
import na klientach); zaplanuj wymianę przed wygaśnięciem certyfikatu. Nie
udostępniaj klucza serwera ani FTP przez tunel. Domyślny tryb tunelu pozostaje
bez zmian. Test lokalnego Nginx przeszedł; urządzenia i Ubuntu do odbioru.

Od 0.3.7 każda jawnie wpisana ścieżka jest przekazywana bez automatycznego zastępowania; trzy katalogi muszą być rozłączne (np. `/home/store/kamery`, `/home/store/eksporty`, `/home/store/staging`). Aktualizacja wypisuje ścieżki i sprawdza, czy nie zmieniły się. Dla RTSP można wybrać segment 30 s, zakończone pliki indeksowane są po publikacji; aktualnie zapisywany segment pozostaje w staging.
