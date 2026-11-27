# Sunhill NVR 1.1.x - konfiguracja analityki i odbiór kandydata

## Status

Paczka 1.1.2 jest kandydatem instalacyjnym, nie wydaniem zweryfikowanym przez CI. Zawiera pełną historię Git i kod poprzednich partii WIP. Nie dołącza wag ONNX ani danych biometrycznych. Nie jest certyfikowanym klientem ONVIF Profile M/T. Nie przełączać istniejącego archiwum produkcyjnego bez odbioru na kopii i kopii zapasowej.

## Analiza obrazu

W Administracja / Kamery / Metadane ONVIF, lokalna analiza i reguły przestrzenne:

- Automatycznie: preferowane są rzeczywiste metadane kamery, przy ich braku uruchamiana jest lokalna analiza.
- Lokalna analiza: niezależnie od tego, czy kamera wysyła metadane ONVIF. To ustawienie jest potrzebne do lokalnego dopasowywania zapisanych profili twarzy na kamerach z działającym ONVIF. Nie łączy obu detektorów w jednym obrazie.
- Wszystkie wykryte ramki są wyświetlane; nie gwarantuje to wykrycia każdego fizycznego przedmiotu. Śledzenie działa w jednej sesji kamery. Reguły pozostawionych przedmiotów wymagają obecności osoby i stabilnego położenia kandydata. Detekcja zasłonięcia i trwałych zmian obrazu jest heurystyczna i może generować fałszywe alarmy.
- Metadane mają tę samą historię co indeks dostępnych nagrań z dwugodzinnym buforem publikacji i ochroną podczas utraty dysku. Bez niezależnego limitu dni lub liczby rekordów. Retencja samych MP4 pozostaje bez zmian.

### Modele opcjonalne

Zainstalowane przez system OpenCV HOG/Haar oraz Tesseract działają bez własnych plików modeli ONNX. Model COCO rozszerza klasy obiektów, a YuNet i SFace umożliwiają dopasowanie do dobrowolnie zarejestrowanych profili. Modele nie są pobierane z kamer ani automatycznie podczas instalacji lub startu. Administrator może użyć opisanego niżej jawnego polecenia pobierania modeli twarzy lub sam dostarczyć zgodne, zaufane pliki i zweryfikowane SHA-256:

| Rodzaj | Format | Nazwa docelowa |
| --- | --- | --- |
| coco | YOLOv8 ONNX, 640x640, COCO 80 klas, wyjście 1x84xN lub 1xNx84 bez zintegrowanego NMS | coco-yolov8.onnx |
| face-detector | OpenCV FaceDetectorYN / YuNet ONNX | face-yunet.onnx |
| face-recognizer | OpenCV FaceRecognizerSF / SFace ONNX, wektor 128 | face-sface.onnx |

Przykład z konsoli root, po instalacji testowej (zmienne zawierają rzeczywiste ścieżki i niezależnie sprawdzone sumy):

```bash
export PYTHONPATH=/opt/sunhill-nvr/current/app
/usr/bin/python3 -m sunhill_nvr.cli analytics-model-install coco "$COCO_FILE" --sha256 "$COCO_SHA256"
/usr/bin/python3 -m sunhill_nvr.cli analytics-model-install face-detector "$YUNET_FILE" --sha256 "$YUNET_SHA256"
/usr/bin/python3 -m sunhill_nvr.cli analytics-model-install face-recognizer "$SFACE_FILE" --sha256 "$SFACE_SHA256"
```

Polecenie weryfikuje hash, typ, rozmiar, właściciela katalogów i zgodność sieci przy użyciu rzeczywistego wywołania OpenCV na syntetycznej klatce. Dopiero wtedy atomowo umieszcza plik w `/opt/sunhill-nvr/config/models/`. Nie dowodzi to skuteczności modelu na nagraniach. Istniejące modele nie są zastępowane w razie błędu. Lokalny worker sprawdza tożsamość plików co około 5 s; zmiana kończy dotychczasową sesję lokalnej analizy, a kolejna ładuje modele od nowa, z nowym ID śledzenia i stanem reguł. W trybie auto ponownie próbuje ONVIF. Nie trzeba zatrzymywać nagrywania. Długość pojedynczej inferencji, retry ONVIF i brak klatek mogą opóźnić przełączenie.

### Pobieranie modeli twarzy (1.1.1)

Polecenie root na serwerze NVR, po zapoznaniu się z licencjami i przy dostępie HTTPS do GitHub:

```bash
export PYTHONPATH=/opt/sunhill-nvr/current/app
/usr/bin/python3 -m sunhill_nvr.cli analytics-model-fetch-faces --accept-licenses
/usr/bin/python3 -m sunhill_nvr.cli analytics-model-status --verify
```

Pobieranie używa przypiętej rewizji [OpenCV Zoo `48b10fa`](https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7), nie ruchomej gałęzi. SHA-256 i rozmiar pochodzą z oficjalnych wskaźników Git LFS w tej rewizji:

- [YuNet, MIT](https://github.com/opencv/opencv_zoo/blob/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet/LICENSE): 232589 B, `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.
- [SFace, Apache-2.0](https://github.com/opencv/opencv_zoo/blob/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_recognition_sface/LICENSE): 38696353 B, `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`.

Oba nowe pliki są sprawdzane przed zmianą któregokolwiek z nich; każdy jest publikowany atomowo, ale para plików nie stanowi jednej transakcji. Wymiana istniejącego niestandardowego modelu wymaga `--replace`; obecność profili blokuje wymianę. Polecenie nie pobiera COCO (własny zgodny YOLOv8 ONNX pozostaje wymagany). `analytics-model-status` bez `--verify` sprawdza dostępność i zaufanie do pliku; `--verify` uruchamia test sieci i zwraca kod 1, jeżeli choć jeden z trzech modeli jest niedostępny lub test nie przejdzie. Test na klatce syntetycznej nie zastępuje walidacji skuteczności. Pobranie na środowisku deweloperskim nie powiodło się; test pobierania w CI jest mockiem, nie dowodem realnej inferencji.

Administracja / Kamery / Lokalna analiza AI pokazuje stan plików, liczbę próbek zapisanych profili i umożliwia administratorowi usunięcie profilu. API nie zwraca wektorów cech. Od 1.1.2 administrator może także dodawać i aktualizować zdjęcia profili w panelu WWW, niezależnie od dostępu przez LAN lub domenę. Połączenie HTTP w LAN nie jest szyfrowane; używaj lokalnego HTTPS, jeśli jest dostępny.

### Profile twarzy

Administrator aplikacji WWW lub lokalny operator root, za zgodą osoby, może wprowadzić 1-5 własnych zdjęć jednej osoby. W panelu wybierz Administracja / Kamery / Lokalna analiza AI, wpisz nazwę, wybierz zdjęcia JPEG lub PNG (do 8 MiB każde, 20 MiB łącznie, maksymalnie 4096 px na bok), potwierdź zgodę i zapisz. Przycisk Aktualizuj zdjęcia zastępuje wszystkie próbki atomowo, zachowując identyfikator profilu; nieudana analiza nie zmienia poprzednich próbek. Dodawanie przez CLI pozostaje dostępne. Wybieraj zdjęcia dobrej jakości i z dużą twarzą. NVR nie kopiuje zdjęć, zachowuje tylko cechy modelu w istniejącej SQLite. Nie tworzy profili osób nieznanych. Dopasowanie wymaga progu podobieństwa, odstępu od drugiego kandydata i dwóch kolejnych zgodnych obserwacji na jednej ścieżce. Wynik jest oznaczony jako możliwe dopasowanie, nie potwierdzona tożsamość. Zwykli użytkownicy widzą ramkę twarzy bez etykiety i bez identyfikatora profilu. Model nie został skalibrowany na fizycznych kamerach; nie wykorzystywać samodzielnie do decyzji o tożsamości.

```bash
export PYTHONPATH=/opt/sunhill-nvr/current/app
/usr/bin/python3 -m sunhill_nvr.cli face-enroll 'Profil testowy' /root/zdjecie1.jpg /root/zdjecie2.jpg --consent
/usr/bin/python3 -m sunhill_nvr.cli face-list
/usr/bin/python3 -m sunhill_nvr.cli face-remove "$PROFILE_ID"
```

Usunięcie profilu usuwa szablony oraz uniemożliwia dalsze wyświetlanie jego etykiety, także w archiwum. W historycznych metadanych do rotacji mogą pozostać niepowiązane losowe identyfikatory; kopie zapasowe bazy mogą zawierać starsze dane. Zarządzaj dostępem i retencją kopii zgodnie z zasadami administratora. Modele twarzy można wymienić po usunięciu profili; wektory różnych modeli nie są porównywane. Etykiety nie są zwracane przez API użytkownikom typu viewer. Zastosowanie rozpoznawania twarzy wymaga także odpowiedniej podstawy prawnej i zabezpieczenia danych w lokalnej instalacji.

## Instalator i aktualizacja

Czysta instalacja 1.1.0 używa RTSP-only; stary kod FTP pozostał na potrzeby zgodności i rollbacku. Migrację z 1.0.2 wykonuj na kopii VM z dostępem do kamer, po weryfikacji backupu konfiguracji, SQLite i archiwum. Dopiero po zaakceptowaniu wyników CI i własnym odbiorze, jawne polecenie migracji z katalogu pakietu:

```bash
bash install.sh --update --rtsp-only
```

Bez tej flagi aktualizacja legacy zachowuje tryb zgodności. Migracja nie usuwa plików archiwalnych i nie odinstalowuje z hosta poprzedniego pakietu systemowego ProFTPD; wycofuje jego zarządzane jednostki NVR. Nie usuwaj ręcznie dawnych jednostek ani pakietów przed testem rollbacku. Jeśli kontrola strumienia nie przejdzie, diagnozuj przyczynę; nie omijaj sprawdzenia.

## Kryteria odbioru pozostające otwarte

- Realne CI `verify`, `install-smoke`, `default-install` clean/resume i `rtsp-migration` na Ubuntu 26.04 dla dokładnego commitu; brak produkcyjnego odbioru w Debianie.
- Weryfikacja prawdziwych plików modelu COCO, YuNet, SFace i jakości lokalnej analizy na nagraniach, w tym OCR, stref i pozostawionych obiektów. Testy syntetyczne nie zastępują tych pomiarów.
- ONVIF M/T: różne modele kamer, faktyczne formaty i profile, zgodność kadru, znacznik czasu; fizyczne testy audio G.711 przez LAN i Cloudflare, firmware z zaufaną aktualizacją i odczytem wersji po restarcie. Brak certyfikacji pełnych profili.
- Instalacja, migracja, rollback na kopii istniejącej VM; porównanie plików nagrań, ciągłość zapisu i odtworzenie istniejącej bazy. Nie wyłączaj zabezpieczeń aktualizacji.
