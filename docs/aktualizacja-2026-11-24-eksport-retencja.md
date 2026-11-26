# Aktualizacja - eksport, dostęp i rotacja

Poniższe punkty z `SPECIFICATION.md` zostały objęte tą aktualizacją. Oznacza
to poprawę wymienionego zachowania i testy lokalne, a nie potwierdzenie
ukończenia całego punktu lub gotowości na produkcyjnym Ubuntu.

| Punkt | Wprowadzona zmiana lub sprawdzony scenariusz |
| --- | --- |
| 11 | Sprawdzenie aktualnej sesji i uprawnień przed zapisaniem ochrony, w tej samej transakcji co zmiana i audyt. |
| 44 | Po przygotowaniu miniatury ponownie sprawdzane jest uprawnienie do kamery. |
| 49 | Cofnięcie prawa pobierania w czasie przygotowywania zdjęcia blokuje odpowiedź. |
| 52 | Żądanie eksportu zachowuje kamerę i zakres wybrane podczas przygotowania. |
| 53 | Plik pośredni dłuższy niż doba może być ponownie użyty w łączeniu; jego długość jest sprawdzana przez ffprobe. |
| 54 | Kontrola rzeczywistej długości materiału źródłowego pozostaje wymagana przy przycinaniu. |
| 55 | Podgląd zakresu nie uruchamia eksportu dla innej kamery po przełączeniu widoku. |
| 56 | Usunięto limit doby dla pośrednich plików eksportu obejmującego również przerwy. |
| 58 | Kilkakrotne kliknięcie podczas przygotowania tego samego zadania nie tworzy równoległych żądań. |
| 59 | Status starego zadania nie nadpisuje widoku nowej kamery; błędy braku nagrań, czasu i mediów są rozróżniane. |
| 68 | Ochrona zakresu działa partiami w jednej transakcji, również dla ponad 5000 plików. |
| 69 | Podgląd liczy wszystkie rozpoznane chronione pliki i pliki z nieistniejącym czasem, również po osiągnięciu celu. |
| 71 | W podglądzie rotacji liczba plików chronionych jest widoczna także wtedy, gdy rotacja nie jest potrzebna. |
| 79 | Planowanie zakresu porównuje momenty w UTC, także dla dwóch wystąpień tej samej godziny po zmianie czasu. |

Test z 5001 plikami używa małych plików testowych. Test wielodniowego pliku
pośredniego symuluje wynik ffprobe i uruchomienie FFmpeg, nie koduje rzeczywistego
materiału przez wiele godzin. Lokalny test HTTP używa syntetycznych nagrań.
Przegląd kodu i testy awarii dotyczą zmian z tej aktualizacji, a nie zastępują
dwóch końcowych przeglądów całego projektu ani testów Ubuntu i kamer.
