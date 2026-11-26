# Wymagania Sunhill NVR

Jedynym nadrzędnym źródłem treści uzgodnionych wymagań jest [SPECIFICATION.md](SPECIFICATION.md), zawierający wszystkie pozycje 1-80 wraz z wyłączeniami z zakresu. Nie tworzymy drugiej niezależnej kopii specyfikacji. Numeracja jest trwała; nie wolno usuwać lub scalać wymagań bez decyzji użytkownika.

| Numery | Obszar |
| --- | --- |
| 1-14 | Instalacja i dostęp |
| 15-38 | Kamery, FTP i zapis |
| 39-61 | Archiwum i eksport |
| 62-80 | Rotacja i monitoring |

Wyłączenia pozostają dokładnie takie jak w SPECIFICATION.md. Wycofanie nieudanej aktualizacji pozostaje wymagane. Zmiana zakresu wymaga jawnej decyzji użytkownika i wpisu w CHANGELOG.md.

Stan wykonania: [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md). Zasady utrzymywania dokumentacji: [AGENTS.md](AGENTS.md). Stan przekazania: [HANDOVER.md](HANDOVER.md).

Od 0.2.0 decyzją użytkownika: prosta instalacja bez flagi i klucza wydawcy, ręczne aktualizacje z TAR.GZ, numer wersji w aplikacji, trwałe dane w /opt/sunhill-nvr z możliwością niezależnego wskazania nagrań i eksportów w panelu. Zobacz punkty 1, 5, 8, 33 specyfikacji.

Decyzja użytkownika (25.11.2026, 0.2.10): minimum 6 znaków dla haseł administratora, użytkowników i kont FTP, bez wymogu znaków specjalnych; pozostają maksima 1024/128 i dotychczasowe zabezpieczenia. Punkty 4, 7–10, 26.

Decyzja użytkownika (25.11.2026, 0.2.12): dostęp po IP i domenie ma być niezależny, bez przekierowania z IP na domenę. Punkt 13: prywatny IPv4 HTTP (LAN/loopback); HTTPS domeny bez zmian.

Decyzja użytkownika (25.11.2026, 0.3.2): bez listy plików i bez wyświetlania UTC w archiwum; jedna oś całej doby, automatyczny start odtwarzania, pozostałe funkcje dostępne kontekstowo. Punkty 37, 39–43, 51.

Decyzja użytkownika (25.11.2026, 0.3.3): ręczna zmiana położenia i rozmiaru okien podglądu na żywo bez konieczności zapisu presetu; istniejące zapisane układy zachowane. Punkty 19, 22.

Decyzja użytkownika (0.3.6): dodać fizyczne sterowanie kamerą, w tym reflektor i zoom optyczny. Pierwszy adapter Reolink dla administratora; pozostałe protokoły i ruch kierunkowy do dalszej integracji. Punkty 19 i 80.

Decyzja administratora 0.3.12 (poz. 70): magazyn niezależny od fizycznego dysku, wymagany punkt montowania zamiast UUID; naprawa aktualizacji z terminalnego rollbacku bez kasowania danych.

Decyzja użytkownika 0.3.13 (poz. 35–36): przyrostowy import istniejących plików, ręczny zakres kamery i dat, wstrzymanie oraz postęp bez kasowania indeksu.
