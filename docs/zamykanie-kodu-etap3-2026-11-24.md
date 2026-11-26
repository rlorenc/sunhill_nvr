# Etap 3 zamykania implementacji - 24.11.2026

Baza: `6556ce4`. Statusy wg pierwotnej definicji K/C/N, nie odbiór produkcyjny.

## Nowe implementacje i dowody

| Commit | Zakres | Dowód lokalny |
| --- | --- | --- |
| `69e3ed3` | Prywatne CA dla HTTPS LAN i certyfikat na dwie nazwy DNS | Testy TLS, rollback, rzeczywisty Nginx: odrzucenie obcego CA i logowanie po zaufaniu |
| `c97bd01` | Trwały wybór jakości, brak zbędnego reconnect, jawne sterowanie audio, świeża lista FTP | Test DOM w Node; rzeczywisty odtwarzacz nieuruchomiony |
| `f9127c0` | Monitoring obrazu na rzeczywistym RTSP | FFmpeg dekoduje obraz syntetycznej kamery; utrata połączenia i odzyskanie, powiadomienia |
| `251a072` | Migrator offline z podpisem, dziennikiem i rollbackiem czterech jednostek odzyskiwania | Sześć testów z rzeczywistymi podpisanymi paczkami i symulowanym systemd, w tym przerwanie przed i po wymianie katalogu |
| `534a9cd` | Rozszerzenie scenariusza integracyjnego Ubuntu | Testy składni; scenariusz VM nieuruchomiony |
| `37a6d81` | Uzupełnienie fixture adaptera aktualizatora o katalog stanu, bez pomijania kontroli dziennika | 6 testów aktualizatora OK |
| `9b1ae61` | Przygotowanie dedykowanych katalogów FTP, kontrola montowania i blokada błędnej konfiguracji | Testy katalogów, symlinków, ścieżek, utraty montowania, kontrolera i renderowania jednostek; test prawdziwego FTP przygotowany, niewykonany |

## Pięć pozycji C → K

- 3: zmiana nazwy DNS i osobny mechanizm migracji treści istniejących jednostek odzyskiwania. Nie obsługuje zmiany ich zbioru; pełny cykl systemd do odbioru.
- 13: bezpieczny dostęp LAN bez zakupu certyfikatu, z prywatnym CA świadomie dodanym do zaufanych na urządzeniu i właściwym DNS. Test Nginx wykonany.
- 21: automatyczna i ręczna jakość, zachowanie wyboru po odświeżeniu, brak restartu przy tym samym aliasie. Test DOM wykonany.
- 26: dedykowane katalogi FTP, walidacja, blokada wyjścia poza archiwum. Dodatkowa próba systemowa: rzeczywisty nieuprzywilejowany UID 65534 odczytał plik konta należący do root i utworzył katalog kamery z własnym UID, przy syntetycznym guardzie dysku. Realna izolacja przez ProFTPD pozostaje do odbioru.
- 73: rzeczywisty test dekodowania RTSP i alarmów na syntetycznej kamerze. Docelowy go2rtc oraz kamery do odbioru.

## Dziewięć pozostałych C

| Punkt | Brak lub konieczny test |
| --- | --- |
| 1 | Pełna instalacja i restart Ubuntu 26.04 |
| 2 | Instalacja i integracja wszystkich rzeczywistych zależności |
| 5 | Zgodność mechanizmów aktualizacji między wersjami, pełny rollback; zmiana zbioru jednostek recovery nadal nieobsługiwana |
| 8 | Kompletność operacji panelu administratora w rzeczywistym kliencie |
| 9 | Kompletność operacji panelu użytkownika w rzeczywistym kliencie |
| 18 | Rzeczywisty podgląd z dźwiękiem przez go2rtc i przeglądarki |
| 23 | Odbiór desktop, iPhone, tablet |
| 24 | Rzeczywisty ProFTPD, start, restart, transfer |
| 27 | Rzeczywiste kamery FTP, format zapisu i uprawnienia |

Odbiór produkcyjny wszystkich wymagań pozostaje odrębnym zadaniem. Nie
wyłączono bramki eksperymentalnego instalatora. Nie publikowano kodu w obcym
repozytorium: origin wskazuje lokalny bundle. Wcześniejsze raporty są historyczne.

## Regresja

Pierwszy pełny przebieg wykrył błąd fixture starszego testu aktualizatora;
uzupełniono adapter testowy o katalog stanu (commit `37a6d81`), zachowując
kontrolę dziennika w kodzie produkcyjnym. Drugi pełny przebieg: 576 testów,
572 zaliczone, 4 pominięte, 0 błędów i niepowodzeń, 99,246 s (Debian 13).
Test DOM, kontrola składni JS, skrypty bash i kompilacja Python OK.
Ostrzeżenia ResourceWarning SQLite nadal widoczne; brak odbioru produkcyjnego.
