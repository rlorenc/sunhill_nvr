# Weryfikacja w toku

Nie jest to końcowy audyt ani potwierdzenie gotowości produkcyjnej.

## Przegląd funkcjonalny

- W `weryfikacja-punktow-2026-11-24.md` opisano 37 konkretnych punktów
  sprawdzonych w lokalnym kodzie i testach oraz ograniczenia tych testów.
- Test HTTP na pętli zwrotnej obejmuje logowanie, kalendarz, listę nagrań,
  zakres i HEAD pliku, klatkę oraz eksport i pobranie MP4 z dwóch nagrań.
  Materiał jest syntetyczny; test nie uruchamia Nginx ani usług Ubuntu.
- W przeglądarce na lokalnym serwerze testowym sprawdzono logowanie, otwarcie
  archiwum, odtworzenie pliku, zaznaczenie początku i końca na osi oraz
  utworzenie zadania z wynikiem „Gotowe” i linkiem MP4. Znaleziony wyścig,
  w którym odtwarzacz nadpisywał zaznaczoną pozycję, poprawiono i ponowiono
  próbę. Serwer używał syntetycznego materiału i testowego mapowania Origin;
  nie było Nginx, Cloudflare, prawdziwych kamer ani podglądu live.
- Dłuższy test FFmpeg łączy 17 plików z dźwiękiem i lukami przez północ.
  Usunięto niepotrzebny limit jednej doby dla pojedynczej czarnej przerwy;
  bardzo długie rzeczywiste kodowanie nie było testowane wydajnościowo.
  Uzupełniono anulowanie bieżącego eksportu i zabezpieczono publikację po
  anulowaniu. Dodano wariant podglądu H.264/AAC na żądanie; prawdziwy go2rtc
  i przeglądarki nie zostały jeszcze sprawdzone.
- Błąd katalogu jednej kamery RTSP zatrzymuje jej proces, lecz nie pozostałe.
  Puste katalogi nieaktywnych sesji są po czasie sprzątane bez usuwania plików.
  Rotacja uruchamia się wcześniej, w połowie zapasu; gdy wolne miejsce spadnie
  poniżej minimum, zapis jest wstrzymywany do odzyskania miejsca. Mechanizm
  działa okresowo i nie gwarantuje dokładnej wartości wolnego miejsca.
- Odtworzono listę 80 uzgodnionych punktów w `SPECIFICATION.md` i oznaczono stan.
- Dodano utworzenie pierwszego administratora z przeglądarki przy użyciu
  jednorazowego kodu z konsoli, a dotychczasowa inicjalizacja konsolowa została
  zachowana. Przepływ wymaga sprawdzenia przez prawdziwą przeglądarkę i Nginx.
- Testy obejmują m.in. indeksowanie plików, eksport syntetycznych nagrań przez
  FFmpeg, odczyt archiwum, kolejki, ochronę plików oraz odzyskiwanie aktualizacji.
- Przejście archiwum przez północ sprawdzono w uproszczonym środowisku JavaScript;
  nie zastępuje to testów w rzeczywistych przeglądarkach.
- W lokalnej przeglądarce sprawdzono logowanie, widok administracyjny, dodanie
  syntetycznej kamery i otwarcie pustego archiwum. Serwer testowy używał
  tymczasowej bazy i testowego mapowania nagłówka Origin; nie było Nginx, TLS,
  strumienia, prawdziwej kamery ani pracujących usług Ubuntu.
- Kalendarz, podgląd rotacji, historia, status usług i monitor obrazu mają testy
  logiki lub API, ale wymagają sprawdzenia całego przepływu w docelowym środowisku.
- Eksport i dzienne przeglądanie używają indeksów czasowych, a indeksator
  wykonuje częste skany ostatnich dni i pełny skan co sześć godzin. Próby
  lokalne nie mierzą wydajności na docelowym archiwum.
- Pamięć długości nagrania uwzględnia także urządzenie, inode i czas zmiany
  metadanych, aby wykrywać podmienione pliki z takim samym rozmiarem i czasem
  modyfikacji. Stare wpisy bez zapisanej tożsamości są ponownie analizowane;
  pierwszy pełny skan po aktualizacji może przez to potrwać dłużej.
- Odczyt, eksport, klatki i ochrona plików porównują tożsamość z zapisaną
  w indeksie, jeśli jest dostępna. Starsze wpisy bez tych danych do czasu
  ponownego indeksowania nadal używają wcześniejszych kontroli rozmiaru i czasu
  modyfikacji. Podmiana nie usuwa żadnego nagrania; eksport kończy się błędem.
- Sprzątanie porzuconych eksportów rozpoznaje dwa dowiązania pozostawione po
  przerwaniu zapisu. Usuwa je tylko dla starego, nieaktywnego, nieudanego
  zadania albo wewnątrz rozpoznanego katalogu tymczasowego, gdy wszystkie
  dowiązania znajdują się w tym katalogu. Inne pliki pozostają nietknięte.
- Po wczesnym błędzie nagrywania RTSP nadzorca próbuje AAC dla dźwięku,
  pozostawiając obraz bez ponownego kodowania. Test lokalny używa źródła PCM;
  wykrywanie i powrót po błędzie wymagają sprawdzenia na kamerach.
- Publikacja ukończonego segmentu RTSP sprawdza tożsamość pliku, granice
  montowania oraz trwałość dowiązania. Po przerwaniu między utworzeniem
  dowiązania w archiwum a usunięciem nazwy roboczej rozpoznaje dokładnie tę
  samą zawartość i kończy operację. Testy symulują przerwanie i błąd zapisu;
  nie zastępują rzeczywistej próby awarii zasilania na Ubuntu.
- Katalogi robocze segmentów są otwierane bez podążania za dowiązaniami;
  nagrywanie sprawdza również identyfikator montowania bufora i archiwum.
  Błąd publikacji poprawnego segmentu jest zgłaszany oddzielnie od błędu jego
  odczytu, z ponawianiem i powiadomieniem o powrocie do działania.
- Metadane ścieżki audio z ffprobe są zapisywane razem z indeksem i sprawdzane
  dla nowych lub zmienionych nagrań FTP oraz RTSP. Brak ścieżki powoduje alert,
  ale obraz pozostaje w archiwum. Obecność ścieżki nie potwierdza słyszalnego
  dźwięku. Stare, niezmienione pliki bez tych metadanych nie są ponownie
  analizowane tylko na potrzeby tej kontroli. Wymagane są próby na kamerach.
- Przygotowano workflow Ubuntu 26.04 z osobnym scenariuszem instalacji i testu
  FTP do archiwum. Scenariusz sprawdza także odmowę usuwania i nadpisywania
  nagrań przez FTP, usuwanie eksportu, ponowny start monitorów oraz podpisane
  aktualizacje dodające i usuwające testową usługę. Workflow nie został jeszcze
  uruchomiony.

## Przegląd bezpieczeństwa

- Poprawiono błędne rozpoznawanie własnego deskryptora rotatora jako aktywnego
  pliku oraz dodano sprawdzanie identyfikatora montowania dla bind mountów.
- Skanowanie rotatora przerywa pracę przy błędach dostępu do rozpoznanej
  struktury archiwum. Dzięki temu nie usuwa kolejnych plików na podstawie
  niepełnej listy nagrań; pozostałe katalogi i obce montowania są pomijane.
- Instalator i pomocnik sprawdzający otwarte pliki odrzucają `/proc` z opcją
  `hidepid`, która mogłaby ukrywać procesy, oraz sytuację, w której nie można
  potwierdzić montowania procfs. Nie zastępuje to weryfikacji wszystkich
  ograniczeń przestrzeni PID, dostępu do procesów ani testu na Ubuntu.
- Sprawdzanie dysku uwzględnia możliwe bind mounty tworzone przez systemd tylko
  dla dokładnie tego samego podkatalogu. Wymaga potwierdzenia na Linuxie.
- Aktualizator weryfikuje podpis i nie uruchamia kodu z niepodpisanej paczki;
  dziennik transakcji i testy obejmują odtwarzanie po przerwaniu migracji.
- Przygotowano generator zaszyfrowanego klucza wydawcy i podpisywanie bez
  przekazywania hasła w argumentach procesu. Test tworzy klucz, podpisuje
  wydanie i weryfikuje podpis; rzeczywistego klucza jeszcze nie wygenerowano.
- Dla przesyłanych plików ograniczono rozmiar, dostęp i ścieżki. Hasła kamer
  nie są zwracane przez API; pliki stanu i bazy wymagają prywatnych uprawnień.
- Podgląd live przechodzi przez lokalny przekaźnik WebSocket, który sprawdza
  sesję i prawo do kamery także w trakcie połączenia. W testach lokalnych
  odebranie uprawnienia zamyka istniejący strumień. Połączenie przez Nginx,
  go2rtc, rzeczywistą kamerę i Cloudflare pozostaje do sprawdzenia.
- Panel ponawia ładowanie odtwarzacza, jeśli bramka go2rtc nie działała przy
  otwarciu strony. Kolejne próby mają osobne ścieżki obu modułów, aby błąd
  załadowania zależności nie został zapamiętany przez przeglądarkę. Odtworzenie
  widoku sprawdzono w teście logiki oraz w przeglądarce na lokalnym serwerze,
  który zwrócił błąd zależności przy pierwszej próbie i moduły testowe przy
  drugiej. Nginx, rzeczywisty go2rtc i prawdziwy obraz wymagają próby.
- Pobieranie oryginalnego pliku ma osobne sprawdzenie uprawnienia, a odczyt i
  generowanie klatki blokują współpracujący rotator na czas korzystania z pliku.
  Długie odpowiedzi z nagraniami i eksportami sprawdzają sesję oraz uprawnienia
  co kilka sekund i przerywają przesyłanie po ich cofnięciu lub błędzie kontroli.
  Oglądający może jednak skopiować przesłany do przeglądarki obraz; panel
  informuje o tym przy ustawianiu uprawnień.
- Eksport ponownie sprawdza dostęp w trakcie pracy i atomowo przed oznaczeniem
  zadania jako gotowego. Wykryto i zamknięto możliwość przemycenia deklaracji
  encji XML w odpowiedzi ONVIF zakodowanej jako UTF-16.
- Nowe kamery otrzymują identyfikator większy od zapisanej historii, dzięki
  czemu nowa kamera nie przejmuje eksportów usuniętej kamery o tym samym ID.
  Nie usuwa to automatycznie ewentualnych błędnych powiązań utworzonych już
  wcześniej przez starszą wersję.
- Zmiana certyfikatu z panelu wymaga uprawnień administratora, sprawdza domenę,
  zaufanie i zgodność klucza oraz zapisuje dziennik odzyskiwania. Nginx ma
  zależność od usługi odzyskiwania. Sprawdzono scenariusze awaryjne w testach
  lokalnych; zachowanie systemd i Nginx na Ubuntu pozostaje do sprawdzenia.
- Instalator rozdziela dystrybucyjny ProFTPD od własnej usługi, przyznaje
  usługom prawo odczytu kodu i wymusza binarny transfer FTP. Wymaga to jeszcze
  testu na systemie Ubuntu, łącznie z faktycznym transferem plików.
- Wirtualne konta FTP otrzymują grupy dodatkowe konta usługi. Pliki ich
  uwierzytelniania są generowane jako root z dostępem grupowym do odczytu,
  a konfiguracja ProFTPD jest porównywana i aktualizowana wraz z nimi. Przy
  wykryciu błędnych praw powstaje nowa konfiguracja; dowiązania są odrzucane.
  Sprawdzenie logowania i zapisu z ProFTPD na Ubuntu pozostaje konieczne.
- Przed instalacją pakietów sprawdzane są prawa konta aplikacji do istniejących
  katalogów głównych nagrań, bufora i eksportów. Brak praw przerywa instalację
  i uruchamia sprawdzone usunięcie dopiero co utworzonego konta. Dodatkowa
  kontrola przechodzi rozpoznaną strukturę archiwum, sprawdzając odczyt plików
  i prawa do katalogów. Pomija dowiązania oraz obce montowania, podobnie jak
  rotator. Wymaga testu na Linuxie z ACL i dużą liczbą plików.
- Instalator może przypisać nowe konto do wskazanej istniejącej grupy danych.
  Sprawdza jej nazwę i GID oraz odrzuca kilka typowych grup uprzywilejowanych;
  lista ta nie dowodzi, że dowolna inna grupa nie daje nadmiernych uprawnień.
  Administrator musi wskazać grupę przeznaczoną wyłącznie do archiwum.
- Reguły FTP, działanie systemd, Nginx oraz `/proc` nie były sprawdzone na
  rzeczywistym Ubuntu. Nie można jeszcze uznać tych zabezpieczeń za zweryfikowane.
- Rotacja zakłada lokalny system plików i brak zewnętrznych zapisujących do
  tego samego archiwum. Instalator, rejestracja dysku i zmiana katalogu
  odrzucają nieznany typ systemu plików oraz znane typy sieciowe. Sama lista
  typów nie wykrywa eksportowania lokalnego dysku innym maszynom ani wszystkich
  konfiguracji storage; integracja i założenie o jednym hoście wymagają prób.

## Otwarte kwestie

- Monitoring ONVIF obsługuje niewspierane kodowania XML, niepoprawne daty i
  przerwane odpowiedzi HTTP jako błędy pojedynczej kamery. Po włączeniu Home
  Assistanta odtwarzany jest aktualny zestaw awarii. Testy symulują również
  zmianę ustawień w trakcie wysyłki; nie zastępuje to testu prawdziwych kamer i HA.

- Instalator pozostaje zablokowany do czasu weryfikacji. Nie utworzono
  docelowego klucza podpisującego ani procesu publikowania wydań.
- Aktualizator ma symulowaną obsługę dodawania i usuwania jednostek wraz z
  odtworzeniem autostartu po przerwaniu. Zmiana jednostek własnego mechanizmu
  odzyskiwania nadal wymaga osobnej migracji. Brakuje testów z prawdziwym
  systemd i Nginx, także po restarcie w trakcie aktualizacji.
- Zmiana katalogu nagrań z panelu obsługuje wyłącznie istniejący katalog w
  obszarze danych aplikacji i na tym samym systemie plików co bufor. Poprzednie
  pliki nie są przenoszone ani usuwane. Są testy transakcji i odtwarzania bazy;
  nie zweryfikowano działania na Ubuntu ani przełączania między dyskami.
- Brakuje prób na prawdziwych kamerach, ProFTPD, Ubuntu i z tunelem Cloudflare,
  w tym testów awarii montowania i zgodności kodeków.
- Repozytorium GitHub nie zostało utworzone. Istnieje lokalne repozytorium
  z kolejnymi commitami, lecz nie jest zsynchronizowane z GitHubem.
- Oba przeglądy wymagają ukończenia po spięciu i przetestowaniu całości.
