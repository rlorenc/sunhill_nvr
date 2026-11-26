# Aktualizacja - monitoring, integracje i panel

Poniżej wyszczególniono zmiany w kodzie z tej aktualizacji. Nie oznaczają one
zakończenia integracyjnych testów całego rejestratora.

1. Walidacja adresu Home Assistanta sprawdza port, znaki sterujące i białe znaki.
2. Token Home Assistanta jest sprawdzany przed zapisaniem i użyciem w nagłówku.
3. Nieprawidłowy typ lub zawartość nowego tokenu są odrzucane również przy wyłączonej integracji.
4. Panel i klient ONVIF używają tej samej walidacji adresu, portu i ścieżki.
5. Równoczesne edycje ONVIF nie przywracają starszego hasła ani trybu uwierzytelniania.
6. Zapis ustawień ONVIF i Home Assistanta ponownie sprawdza sesję i rolę administratora wewnątrz transakcji.
7. Zmiana adresu aktywnego Home Assistanta wysyła do nowego serwera aktualne awarie.
8. Zmiana samego tokenu dla tego samego adresu zachowuje kolejkę powiadomień.
9. Po awarii bramki stary stan kamery nie trafia do Home Assistanta jako aktualny.
10. Kamera nadal niedostępna po powrocie bramki jest ponownie zgłaszana po sprawdzeniu.
11. Panel pokazuje nieznany stan obrazu do czasu sprawdzenia kamery po powrocie bramki.
12. Cofnięcie zegara serwera nie pozostawia odczytów obrazu z przyszłości jako bieżących.
13. Cofnięcie zegara nie przedłuża okresu oczekiwania na pierwsze nagranie o czas korekty.
14. Zapis klatki wymaga załadowanego obrazu z aktualnie wybranego pliku.
15. Zakończenie ochrony pojedynczego pliku nie odświeża przypadkowo innej kamery lub dnia.
16. Spóźniony błąd kalendarza nie nadpisuje komunikatu po zmianie miesiąca lub kamery.
17. Wolna odpowiedź listy eksportów jest wyświetlana mimo kolejnych żądań odświeżenia.
18. Błąd ładowania kamer nie ukrywa panelu już zalogowanego użytkownika.
19. Dodano przycisk ponawiający pobranie listy kamer.

Walidację, wyścigi ustawień, powrót bramki i korekty czasu pokrywają testy
Pythona. Przepływy panelu są dodatkowo sprawdzane w symulowanym DOM, co nie
zastępuje testu Safari, iPhone'a ani rzeczywistej instalacji Ubuntu i kamer.
