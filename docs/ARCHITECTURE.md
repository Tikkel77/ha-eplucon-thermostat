# Eplucon Project Architecture

## Repository Structure

There are two related repositories:

### 1. `Eplucon/` — Python Library + Webapp (local development)

```
Eplucon/
├── eplucon/                    # Python package
│   ├── __init__.py
│   ├── client.py              # EpluconClient — main API client (sync, uses requests)
│   ├── models.py              # Data models (Module, Zone, ProgramForm, etc.)
│   ├── errors.py              # Exception classes
│   ├── schedule.py            # Schedule/duration calculation helpers
│   ├── settings.py            # Config file (TOML) loader
│   ├── cli.py                 # Command-line interface
│   └── webapp.py              # Flask web application (test UI)
├── eplucon.toml               # Configuration file (credentials + settings)
├── zone_settings.json         # Per-zone default duration settings
├── requirements.txt           # Python dependencies
├── docs/                      # Documentation
└── README.md
```

### 2. `ha-eplucon-thermostat/` — Home Assistant Custom Integration

```
ha-eplucon-thermostat/
├── custom_components/
│   └── eplucon_thermostat/
│       ├── __init__.py        # HA entry setup
│       ├── climate.py         # Climate entity per zone
│       ├── sensor.py          # Sensor entities (temp, humidity, signal, battery)
│       ├── binary_sensor.py   # Binary sensors (heating, window, alarm, updating)
│       ├── config_flow.py     # UI configuration flow
│       ├── coordinator.py     # DataUpdateCoordinator
│       ├── portal.py          # Async portal write client (subprocess-based)
│       ├── const.py           # Constants
│       ├── strings.json       # UI strings
│       ├── manifest.json      # HA integration manifest
│       ├── translations/
│       │   └── en.json
│       └── api/               # Embedded copy of the Python client
│           ├── __init__.py
│           ├── client.py      # Same as eplucon/client.py
│           ├── models.py
│           ├── errors.py
│           ├── schedule.py
│           └── settings.py
├── hacs.json                  # HACS metadata
└── README.md
```

## Key Components

### EpluconClient (client.py)

The core API client. Handles both read (Bearer API) and write (portal session) operations.

**Read operations** use the REST API v2 with Bearer token authentication:
- `get_modules()` — list all connected modules
- `get_zones()` — list all thermostat zones with full data
- `get_zone()` — find a specific zone by API ID, internal ID, or name

**Write operations** use portal session login + CSRF tokens:
- `set_constant_temperature()` — set a fixed temperature
- `set_time_limit_temperature()` — set temperature with duration
- `set_temperature_for_minutes()` — convenience wrapper
- `activate_program()` — activate a schedule program
- `submit_program_form()` — save schedule changes

**Status checking:**
- `get_zones_update_status()` — check which zones are currently being updated
- `is_zone_updating()` — check a specific zone
- `wait_for_zone_idle()` — poll until a zone finishes updating

### Portal Write Client (portal.py) — HA Integration

The HA integration uses a **subprocess-based** portal client because direct HTTP calls from within HA's event loop context result in 401 errors due to session/cookie handling issues.

**Current approach:** Writes are delegated to `/config/eplucon_write_helper.py` which runs as an isolated subprocess.

### Schedule Module (schedule.py)

Handles schedule-related calculations:
- `get_next_schedule_start()` — find the next schedule transition point
- `compute_con_duration_minutes()` — calculate "con" mode duration
- `split_minutes_to_hours_minutes()` — convert total minutes to hours:minutes

### Webapp (webapp.py)

Flask-based test UI with:
- Zone overview with temperature display
- Debounced +/- buttons (2s per zone)
- Duration modes: fixed, con (until next schedule start)
- Schedule editor popup with profile/interval management
- Per-zone default duration settings
- Live status indicators (pending/done/error)
- Background write processing with duringChange polling
- Heating/cooling status icons

## Data Flow

### Read Path
```
HA/Webapp → EpluconClient.get_zones()
         → GET /api/v2/econtrol/modules/{id}/zones (Bearer token)
         → Parse raw_data JSON → Zone objects
```

### Write Path (Webapp)
```
User clicks +0.5 → JS debounce (2s) → POST /api/set_temp
                 → _fire_write() → background thread
                 → EpluconClient.set_constant_temperature()
                 → Portal login → CSRF → POST set_constant_temp
                 → wait_for_zone_idle() → status update
```

### Write Path (HA Integration)
```
User sets temp → climate.async_set_temperature()
              → coordinator.async_set_temperature()
              → portal._run_write() via hass.async_add_executor_job
              → subprocess: /config/eplucon_write_helper.py
              → Portal login → CSRF → POST set_constant_temp
              → delayed refresh (20s) → coordinator polls zones
```

## Configuration

### eplucon.toml (Webapp/CLI)

```toml
[auth]
username = "user@email.com"
password = "password"
api_key = "bearer_token"

[connection]
base_url = "https://portaal.eplucon.nl"
request_timeout = 40
settle_seconds = 15
poll_interval_seconds = 5

[webapp]
host = "0.0.0.0"
port = 8080
```

### HA Config Entry

Credentials are stored in the HA config entry (via config flow UI):
- `username` (CONF_USERNAME)
- `password` (CONF_PASSWORD)
- `api_key` (CONF_API_KEY)
