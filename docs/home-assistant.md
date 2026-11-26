# Home Assistant

Integracja wymaga testu w docelowej instalacji. Podane fragmenty są przykładami
konfiguracji Home Assistanta i nie są automatycznie dodawane do jego konfiguracji.

## Powiadomienia z rejestratora

W panelu Sunhill NVR ustaw adres Home Assistanta i jego token długotrwałego
dostępu, a następnie włącz wysyłanie zdarzeń. Rejestrator wysyła zdarzenia
`sunhill_nvr` z polami `camera` i `state`. Powrót do prawidłowego stanu również
jest zdarzeniem, więc można użyć go w osobnej automatyzacji.
Jeśli ostatnia próba przesłania się nie powiedzie, panel stanu usług pokaże
błąd, a oczekujące zdarzenie pozostanie w kolejce do ponowienia. Ostrzeżenie
znika po udanym wysłaniu albo wyłączeniu integracji.
Po ponownym włączeniu integracji wysyłane są aktualne awarie, a zdarzenia
sprzed włączenia są zastępowane bieżącym stanem. Powiadomienia są ponawiane,
więc po przerwaniu połączenia podczas wysyłki Home Assistant może otrzymać
zdarzenie więcej niż raz.
Cykl nie rozpoczyna kolejnej wysyłki, jeśli w jego budżecie 20 sekund zostało
mniej niż 5 sekund na standardowy limit sieciowy. Operacje bazy i rozpoczęte
połączenie mogą wydłużyć cały cykl. Niewysłane zdarzenia pozostają w kolejce.
Zmiana adresu aktywnego Home Assistanta również zastępuje oczekujące zdarzenia
zestawem aktualnych awarii. Sama zmiana tokenu dla tego samego adresu zachowuje
istniejącą kolejkę. Po awarii bramki obrazu stan poszczególnych kamer musi
zostać ponownie sprawdzony, zanim zostanie wysłany jako bieżący.

Stan `recording_audio_missing` oznacza brak ścieżki audio w ostatnio
sprawdzonym nagraniu FTP lub RTSP. Stan `recording_audio_ok` oznacza ponowne
wykrycie ścieżki; nie jest testem tego, czy nagranie zawiera słyszalny dźwięk.
Stan `recording_process_error` oznacza nieudane uruchomienie albo nieoczekiwane
zakończenie procesu FFmpeg. Przy nadal włączonym nagrywaniu RTSP zdarzenie
`recording_process_cleared` jest wysyłane po publikacji poprawnego segmentu
z bieżącej sesji; samo uruchomienie nowego procesu nie wystarcza. Zmiana trybu
zapisu lub wyłączenie kamery również kończą ten alarm. Wstrzymanie z powodu
braku miejsca nie jest traktowane jako potwierdzenie naprawy.

Po włączeniu integracji nie są przedstawiane jako bieżące przeterminowane
wyniki kontroli zapisu, obrazu, czasu ONVIF ani usług. Stan zapisu i usług
uznaje się za nieaktualny po 180 sekundach, obraz po czasie uwzględniającym
liczbę kamer (co najmniej 900 sekund), a czas ONVIF po co najmniej 1800
sekundach. Po ponownej kontroli potwierdzone awarie są zgłaszane ponownie.

Nowe alarmy różnicy czasu wykrytej przez ONVIF są wewnętrznie rozróżniane od
alarmów wynikających z daty nagrania. Po zmianie konfiguracji ONVIF usuwane są
oczekujące alarmy tego odczytu, ale nie alarmy daty nagrania. Do Home Assistanta
w obu przypadkach nadal wysyłany jest stan `clock_skew`. W kolejce po aktualizacji
mogą już istnieć starsze wpisy `clock_skew`, których źródła nie da się ustalić.

Przykładowa automatyzacja wysyłająca awarie na telefon Rafała:

```yaml
automation:
  - alias: Sunhill NVR - awaria monitoringu
    id: sunhill_nvr_awaria_monitoringu
    mode: queued
    triggers:
      - trigger: event
        event_type: sunhill_nvr
    conditions:
      - condition: template
        value_template: >-
          {{ trigger.event.data.state in
             ['offline', 'stale', 'clock_skew', 'clock_unavailable', 'disk_low',
              'disk_unavailable', 'rotation_blocked', 'rotation_error',
              'stream_service_error', 'service_down', 'monitor_error',
              'recording_corrupt', 'recording_publish_error', 'recording_staging_error', 'recording_process_error',
              'recording_audio_missing'] }}
    actions:
      - action: notify.mobile_app_iphone_rafal
        data:
          title: Monitoring - problem
          message: >-
            Źródło: {{ trigger.event.data.camera }}.
            Stan: {{ trigger.event.data.state }}.
```

## Znaczniki zdarzeń na osi czasu

W panelu rejestratora utwórz token do odbierania zdarzeń. Zapisz go w
`secrets.yaml` jako wartość zawierającą również słowo `Bearer`, na przykład:

```yaml
sunhill_nvr_bearer: "Bearer TUTAJ_TOKEN_Z_PANELU"
```

W `configuration.yaml` dodaj polecenie REST. Adres musi odpowiadać instalacji
rejestratora i mieć poprawny, zaufany certyfikat TLS:

```yaml
rest_command:
  sunhill_nvr_event:
    url: "https://kamery.slonecznewzgorze4.pl/api/integrations/events"
    method: POST
    headers:
      authorization: !secret sunhill_nvr_bearer
    content_type: "application/json"
    payload: >-
      {{ {'camera': camera, 'type': event_type, 'time': event_time} | tojson }}
```

Przykład wyzwalania znacznika po wykryciu osoby. Identyfikator encji trzeba
zastąpić rzeczywistą encją kamery w Twoim Home Assistancie. `brama` musi być
identyfikatorem skonfigurowanej kamery w Sunhill NVR.

```yaml
automation:
  - alias: Sunhill NVR - osoba przy bramie
    id: sunhill_nvr_osoba_brama
    mode: queued
    triggers:
      - trigger: state
        entity_id: binary_sensor.ZASTAP_RZECZYWISTA_ENCJA
        to: "on"
    actions:
      - action: rest_command.sunhill_nvr_event
        data:
          camera: brama
          event_type: person
          event_time: "{{ now().isoformat() }}"
```

Obsługiwane typy znaczników to `motion`, `person`, `vehicle` i `animal`.
Wyzwalacze zależą od encji udostępnianych przez konkretną kamerę i integrację.
Nie należy umieszczać tokenu w repozytorium.
