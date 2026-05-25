# Eplucon Thermostaat Library + CLI

Deze repo bevat een generieke Python library en CLI voor Eplucon thermostaat-zones.

De client leest automatisch alle zone-modules en zones; hij is dus niet hardcoded op jouw zone IDs.

## Inhoud

- Library: `eplucon/client.py`
- CLI: `eplucon/cli.py`
- Webapp: `eplucon/webapp.py`
- API reverse engineering verslag: `eplucon_thermostaat_api_verslag.md`

## Vereisten

- Python 3.10+
- `requests`
- `Flask`
- `waitress` (webserver)

Installeren:

```bash
pip install -r requirements.txt
```

## Authenticatie

Je hebt nodig:

- `username` (portal account)
- `password`
- `api_key` (Bearer token)

CLI pakt deze uit flags of environment variables:

- `EPLUCON_USERNAME`
- `EPLUCON_PASSWORD`
- `EPLUCON_API_KEY`
- Optioneel: `EPLUCON_BASE_URL`, `EPLUCON_REQUEST_TIMEOUT`, `EPLUCON_SETTLE_SECONDS`, `EPLUCON_POLL_INTERVAL_SECONDS`

## Config-bestand (aanbevolen)

Je kunt credentials en defaults in een TOML config zetten i.p.v. losse env vars.

Standaard zoekt de app naar:

1. `./eplucon.toml` (in huidige map)
2. `~/.eplucon.toml`

Of geef een expliciet pad via env:

```bash
set EPLUCON_CONFIG=C:\pad\naar\eplucon.toml
```

In deze repo staat al een voorbeeld/basissetup in:

- `eplucon.toml`

Structuur:

```toml
[auth]
username = "..."
password = "..."
api_key = "..."

[connection]
base_url = "https://portaal.eplucon.nl"
request_timeout = 40
settle_seconds = 15
poll_interval_seconds = 5
insecure = false

[webapp]
host = "0.0.0.0"
port = 8080
debug = false
server = "auto"
threads = 6
```

Env vars overrulen config-waarden als beide gezet zijn.

## Simpele webapp (test UI)

Starten:

```bash
python -m eplucon.webapp
```

Met config-bestand hoef je dan geen losse `EPLUCON_USERNAME/PASSWORD/API_KEY` meer te zetten.

Dan open je:

- `http://127.0.0.1:8080`

Voor telefoon op hetzelfde netwerk:

1. Zoek je PC-IP (bijv. `192.168.1.25`), bijvoorbeeld via `ipconfig`
2. Open op je telefoon: `http://<pc-ip>:8080`
3. Zorg dat firewall inbound op poort `8080` toestaat

Instelbare env vars voor webapp:

- `EPLUCON_WEBAPP_HOST` (default `127.0.0.1`)
- `EPLUCON_WEBAPP_HOST` (default `0.0.0.0`, dus LAN-toegang)
- `EPLUCON_WEBAPP_PORT` (default `8080`)
- `EPLUCON_WEBAPP_DEBUG` (`1` of `0`)
- `EPLUCON_WEBAPP_SERVER` (`auto`, `waitress`, `flask`)
- `EPLUCON_WEBAPP_THREADS` (waitress threads, default `6`)

Webapp gedrag:

- Temperatuur verhogen/verlagen met `+0.5` en `-0.5`.
- Bij toepassen zonder duur wordt standaard `4 uur` gebruikt.
- "con" modus = tot eerstvolgende schema-start met maximum `8 uur`.
- "Alleen vast temp" zet een echte `constantTemp` zonder time limit.
- Writes worden met lock sequentieel verwerkt (geen gelijktijdige POSTs).

Opmerking servermodus:

- In normale modus start de app automatisch met `waitress` (stabieler voor langdurig draaien).
- In debug mode (`EPLUCON_WEBAPP_DEBUG=1`) gebruikt hij Flask dev server.

## CLI gebruik

Alle commands via:

```bash
python -m eplucon.cli <command> [args]
```

### Zones/module informatie

```bash
python -m eplucon.cli list-modules
python -m eplucon.cli list-zones
python -m eplucon.cli show-zone --zone-api-id 6819
python -m eplucon.cli show-zone --name "Woonkamer"
```

### Temperatuur zetten

Constante temperatuur:

```bash
python -m eplucon.cli set-constant --zone-api-id 6819 --temp 19.0
```

Tijdelijke temperatuur (time limit):

```bash
python -m eplucon.cli set-time-limit --zone-api-id 6819 --temp 20.0 --hours 1 --minutes 30
```

Temperatuur voor duur, met default 4 uur als je niets invult:

```bash
python -m eplucon.cli set-for --zone-api-id 6819 --temp 20.0
python -m eplucon.cli set-for --zone-api-id 6819 --temp 20.0 --hours 2 --minutes 15
```

"con" modus (tot volgende schema-start, max 8 uur):

```bash
python -m eplucon.cli plan-con --zone-api-id 6819
python -m eplucon.cli set-con --zone-api-id 6819 --temp 20.0
```

### Programma's/schedules

Beschikbare programma-formulieren:

```bash
python -m eplucon.cli list-programs --zone-api-id 6819
```

Programma activeren (`index -17` lokaal, `0..4` globale profielen):

```bash
python -m eplucon.cli activate-program --zone-api-id 6819 --index 0
```

Setback temperatuur aanpassen voor een programma:

```bash
python -m eplucon.cli set-program-setback --zone-api-id 6819 --index -17 --p0 19 --p1 17
```

Arbitraire form overrides sturen:

```bash
python -m eplucon.cli submit-program --zone-api-id 6819 --index -17 --overrides-json "{\"p0SetbackTemp\":\"19\"}"
```

## Library voorbeeld

```python
from eplucon import EpluconClient

client = EpluconClient(
    username="user@example.com",
    password="secret",
    api_key="token",
)

# Alle zones van alle zone-modules
zones = client.get_zones()
for z in zones:
    print(z.module_name, z.name, z.mode, z.set_temperature_c)

# Zone selecteren op naam (kan fout geven bij dubbele namen)
zone = client.get_zone(name="Woonkamer")

# Vast temperatuur
client.set_constant_temperature(zone, 19.5)

# Tijdelijke temperatuur
client.set_time_limit_temperature(zone, 21.0, hours=2, minutes=0)

# Voor duur (default 4 uur)
client.set_temperature_with_default_duration(zone, 21.0)

# con: tot eerstvolgende schema-start, max 8 uur
client.set_temperature_con_until_next_schedule(zone, 21.0)

# Programma activeren
client.activate_program(zone, program_index=0)

# Setback aanpassen op lokaal programma
client.set_program_setback(zone, program_index=-17, p0_setback_c=19, p1_setback_c=17)
```

## Belangrijke runtime-notes

- Writes worden sequentieel gedaan.
- Na elke write wacht de client (`settle_seconds`, default 15s) en pollt hij de zone-state.
- Eplucon write-verwerking is traag; gebruik geen hoge request rates.

## Bekende beperkingen

- `mode=off` en `mode=con` zijn op thermostaat-zones niet werkend bevestigd.
- `localSchedule` als directe mode-switch via `set_constant_temp` is niet betrouwbaar.
- Schema-write gebruikt form-gebaseerde internals en kan bij UI/backend wijzigingen aangepast moeten worden.
