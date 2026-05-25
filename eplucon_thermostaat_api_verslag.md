# Eplucon Thermostaat API — Reverse Engineering Verslag (getest)

Dit verslag focust alleen op **thermostaat-zones** en is praktisch bruikbaar als basis voor een eigen app of Home Assistant integratie.

Ik heb de write-calls sequentieel getest met wachttijd/polling (geen parallelle POSTs), vanwege de trage verwerking in het portaal.

## 1. Belangrijkste conclusie

- Lezen gaat stabiel via `api/v2` (Bearer token).
- Schrijven van thermostaten gaat via interne portal endpoints met sessie + CSRF.
- **Constante temperatuur instellen**: werkt via `POST /e-control/set_constant_temp`.
- **Tijdelijke temperatuur (timeLimit)**: werkt via dezelfde endpoint met `mode=timeLimit` + `hours/minutes`.
- **Schema instellen/wijzigen**: werkt via scheduler-flow:
  - `GET /e-control/zones/{zoneApiId}/ajax/programs`
  - `POST /e-control/zones/zone/{moduleId}/{zoneApiId}/scheduler/store`
- `mode=off` en `mode=con` zijn getest op thermostaat-zones maar geven **geen modewijziging** op dit systeem.

## 2. Auth & sessie

### 2.1 Read API (Bearer)

- Base: `https://portaal.eplucon.nl/api/v2`
- Header: `Authorization: Bearer <API_KEY>`

### 2.2 Portal sessie (voor writes)

1. `GET /login`
2. `POST /login` met formvelden:
   - `_token`
   - `username`
   - `password`
   - `valid_from` (verborgen veld)
   - dynamische honeypot text-input (leeg laten)
3. Cookies bevatten o.a.:
   - `portaal_eplucon_session`
   - `XSRF-TOKEN`
4. CSRF token uit pagina:
   - `<meta name="csrf-token" content="...">`

## 3. IDs en datamodellen (cruciaal)

Per zone heb je meerdere IDs:

- `zone_api_id` (bijv. `6819`) — uit `/api/v2/.../zones`
- `raw_data.zone.id` (intern `zone_id`, bijv. `9298`) — nodig voor writes
- `raw_data.mode.id` (`mode_id`, bijv. `9300`) — nodig voor writes
- `raw_data.schedule.id` (`schedule_id`) — nodig voor scheduler store

Temperaturen:

- API overzicht: graden als decimaal (bijv. `15.8`)
- Write/raw mode: deci-graden int (bijv. `158`)

## 4. Vast temperatuur instellen (bevestigd)

Endpoint:

- `POST /e-control/set_constant_temp?account_module_index=<account_module_index>`

Minimale payload:

```x-www-form-urlencoded
_token=<csrf>
mode=constantTemp
mode_id=<raw_data.mode.id>
zone_id=<raw_data.zone.id>
parent_id=<raw_data.zone.id>
constant_temp=<deci_graden>
active_schedule=<huidige scheduleIndex>
```

Response:

- Body: `1` (succes)

Observatie:

- Wijziging wordt vaak pas na ~15–60 sec zichtbaar in `/api/v2/.../zones`.

## 5. Tijdelijke temperatuur (time limit) incl. "off/con" vraag

## 5.1 Tijdelijke temperatuur zetten (bevestigd)

Zelfde endpoint als hierboven, met:

```x-www-form-urlencoded
mode=timeLimit
hours=<0..23>
minutes=<0..59>
```

Verder dezelfde velden (`mode_id`, `zone_id`, `parent_id`, `constant_temp`, `active_schedule`, `_token`).

Resultaat:

- Zone mode wordt `timeLimit`
- `raw_data.mode.constTempTime` telt af in minuten
- Voor `1u15m` werd `constTempTime` ~`74` zichtbaar (door verwerkingstijd)

## 5.2 Off en con (expliciet getest)

Getest:

- `mode=off`
- `mode=con`

Resultaat op thermostaat-zones:

- Request geeft wel HTTP 200 + body `1`
- Maar mode bleef ongewijzigd (`constantTemp` in tests)

Conclusie:

- Voor thermostaten lijken `off`/`con` **geen geldige/actieve modes** in deze write-flow.
- Gebruik:
  - `constantTemp` voor vast
  - `timeLimit` voor tijdelijk
  - `globalSchedule`/scheduler-flow voor programma

## 6. Schema instellen (bevestigd)

## 6.1 Schema ophalen voor een zone

- `GET /e-control/zones/{zoneApiId}/ajax/programs`
- Header: `X-Requested-With: XMLHttpRequest`
- Response: JSON met `html` fragment waarin alle schedule forms zitten.

## 6.2 Schema opslaan

Endpoint (uit form `data-action`):

- `POST /e-control/zones/zone/{moduleId}/{zoneApiId}/scheduler/store`

Belangrijke velden in form:

- `_token`, `mode_id`, `zone_id`, `index`, `schedule_id`
- `p0Days[0..6]`, `p1Days[0..6]`
- `p0SetbackTemp`, `p1SetbackTemp` (in **hele** graden C in form)
- `p0Intervals[...]`, `p1Intervals[...]`
  - `start/end` als `HH:mm`
  - `temp` als hele graden C
- Voor globale profielen: `setInZoneId[]` (waarden zoals `<zone_id>-<mode_id>`)

Extra velden die in praktijk nodig zijn:

- `dataSerialize=<urlencoded serialize() payload>`
- `data=<json serializeArray()>`

Zonder deze extra velden gaf de endpoint in tests:

- `{"success":0,"error_code":500}`

Met deze extra velden:

- `{"success":1,"error_code":200}`

## 6.3 Wat is bevestigd veranderd

- Wijziging van `p0SetbackTemp` in index `-17` (lokale schedule) veranderde `raw_data.schedule.p0SetbackTemp` (bijv. `260 -> 250`) en kon weer terug.
- Posten van profiel `index=0` schakelde zone naar `globalSchedule` (met `scheduleIndex=0`) en setpoint wijzigde volgens programma.

## 7. Modi gedrag (zoals waargenomen)

- `constantTemp`: direct vaste setpoint.
- `timeLimit`: tijdelijke setpoint + countdown (`constTempTime`).
- `globalSchedule`: actief na scheduler profielactivatie (index `0..4` in tests).
- `localSchedule`: mode-string bestaat in read API, maar directe mode-switch via `set_constant_temp` met `mode=localSchedule` werkte in tests niet betrouwbaar (200/`1` zonder feitelijke omschakeling).

## 8. Betrouwbaarheidsregels voor implementatie

Aanrader voor app/HA integratie:

1. **Single-flight queue per module**
   - Nooit meerdere writes tegelijk.
2. **Na elke POST pollen tot state convergeert**
   - Om de 5 sec lezen via `/api/v2/econtrol/modules/{moduleId}/zones`
   - Timeout 90-180 sec
3. **Pas volgende POST na bevestiging**
   - Of na expliciete timeout/foutafhandeling.
4. **Werk intern met deci-graden**
   - UI omzetting pas aan rand.
5. **Gebruik de juiste interne IDs uit `raw_data`**
   - Niet de externe zone API id voor write velden.

## 9. Praktische endpoint samenvatting

- Lezen zones:
  - `GET /api/v2/econtrol/modules/{moduleId}/zones` (Bearer)
- Vast temp:
  - `POST /e-control/set_constant_temp?account_module_index=...`
  - `mode=constantTemp`
- Tijdelijk temp:
  - `POST /e-control/set_constant_temp?account_module_index=...`
  - `mode=timeLimit` + `hours` + `minutes`
- Schema ophalen:
  - `GET /e-control/zones/{zoneApiId}/ajax/programs`
- Schema opslaan/activeren:
  - `POST /e-control/zones/zone/{moduleId}/{zoneApiId}/scheduler/store`
  - inclusief `dataSerialize` + `data`

## 10. Open punten

- De precieze intended semantiek van `off` en `con` voor thermostaat-zones is niet bevestigd; in deze omgeving niet functioneel als mode-switch.
- `localSchedule` activeren lijkt niet via simpele `mode=localSchedule` op `set_constant_temp` te gaan; waarschijnlijk via specifieke scheduler/UI route.

---

Laatste gecontroleerde zone-status na tests is teruggezet naar:

- `mode=constantTemp`
- `setTemperature=158` (15.8 C)
- `scheduleIndex=0`
