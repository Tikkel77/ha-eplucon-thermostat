# Eplucon API — Reverse Engineering Documentation

## Overview

The Eplucon portal (`portaal.eplucon.nl`) is built on top of the **eModul** platform by **TECH Sterowniki** (Polish company). It is white-labeled by Eplucon for thermostat zone control.

## Architecture

There are two communication paths:

1. **Public REST API v2** — read-only (GET), uses Bearer token
2. **Internal portal endpoints** — read+write (POST), uses session-based login with CSRF tokens

### Key URLs

| URL | Purpose |
|-----|---------|
| `https://portaal.eplucon.nl/api/v2/` | REST API base |
| `https://portaal.eplucon.nl/login` | Portal login page |
| `https://portaal.eplucon.nl/e-control/zones` | Zone overview page |
| `https://portaal.eplucon.nl/e-control/set_constant_temp` | Temperature write endpoint |
| `https://portaal.eplucon.nl/e-control/zones/{zoneApiId}/ajax/programs` | Schedule forms |
| `https://portaal.eplucon.nl/e-control/zones/zone/{moduleId}/{zoneApiId}/scheduler/store` | Schedule save endpoint |
| `https://portaal.eplucon.nl/e-control/zones/ajax/refresh` | AJAX refresh (duringChange detection) |

## Authentication

### Bearer Token (REST API)

```
Authorization: Bearer <API_KEY>
```

The API key can be obtained from the Eplucon portal under "My Account" → "API".

### Portal Session (for writes)

1. `GET /login` → extract `_token`, `valid_from`, honeypot field
2. `POST /login` with form data → receive `portaal_eplucon_session` cookie
3. `GET /e-control/zones?account_module_index=<ami>` → extract CSRF token from `<meta name="csrf-token">`
4. `POST /e-control/set_constant_temp` with CSRF token + session cookie

**Important portal login fields:**
- `_token` — Laravel CSRF token from login form
- `username` — email address
- `password` — account password
- `remember` — "1"
- `valid_from` — hidden field from login form
- Honeypot field — dynamic text input (not "username"), must be empty

## REST API Endpoints

### `GET /api/v2/econtrol/modules`

Returns all connected modules (heat pump, zone controller).

**Response fields:**
- `id` — numeric module ID
- `account_module_index` — hash string used in portal URLs
- `name` — module name
- `type` — `zones_system_controller` or `heat_pump`

### `GET /api/v2/econtrol/modules/{moduleId}/zones`

Returns all zones for a zone controller module.

**Response fields per zone:**
- `id` — zone API ID (used in URL paths)
- `name` — zone name
- `set_temperature` — target temperature (decimal, e.g. 19.5)
- `current_temperature` — current temperature (decimal)
- `mode` — current mode string
- `raw_data` — JSON string with detailed zone data

### Zone Modes

| Mode | Description |
|------|-------------|
| `constantTemp` | Fixed temperature, manually set |
| `timeLimit` | Temporary temperature with countdown |
| `localSchedule` | Following the zone's own schedule |
| `globalSchedule` | Following a shared schedule program |

## Raw Data Structure

The `raw_data` field contains nested JSON with these sections:

### `zone` object
- `id` — **internal zone ID** (different from API zone ID, needed for writes!)
- `currentTemperature` — in deci-degrees (161 = 16.1°C)
- `setTemperature` — in deci-degrees
- `flags.relayState` — "on" or "off" (is the zone actively heating?)
- `flags.algorithm` — "heating" or "cooling"
- `flags.minOneWindowOpen` — boolean
- `zoneState` — "noAlarm" or alarm state
- `signalStrength` — WiFi signal percentage
- `batteryLevel` — battery percentage
- `humidity` — humidity percentage
- `duringChange` — boolean (parameters being updated)

### `mode` object
- `id` — **mode ID** (needed for writes!)
- `mode` — mode string
- `constTempTime` — minutes remaining for timeLimit mode
- `setTemperature` — in deci-degrees
- `scheduleIndex` — -1 for local, 0-4 for global programs

### `schedule` object
- `p0Days` / `p1Days` — arrays of "0"/"1" for days Mon-Sun
- `p0Intervals` / `p1Intervals` — array of {start, stop, temp}
  - `start`/`stop` in minutes from midnight (540 = 9:00)
  - `temp` in deci-degrees
  - Value 6100 = unused/empty interval
- `p0SetbackTemp` / `p1SetbackTemp` — in deci-degrees

## ID Mapping (Critical for Writes)

| ID Type | Example | Usage |
|---------|---------|-------|
| Zone API ID | `6819` | Used in API URLs |
| Internal Zone ID (`raw_data.zone.id`) | `9298` | Used as `zone_id` / `parent_id` in writes |
| Mode ID (`raw_data.mode.id`) | `9300` | Used as `mode_id` in writes |

## Write Endpoints

### Set Constant Temperature / Time Limit

```
POST /e-control/set_constant_temp?account_module_index=<ami>
```

**Headers:**
- `X-Requested-With: XMLHttpRequest`
- `X-CSRF-TOKEN: <csrf_token>`
- `Cookie: portaal_eplucon_session=<session>; XSRF-TOKEN=<xsrf>`

**Form data:**
| Field | Value |
|-------|-------|
| `_token` | CSRF token |
| `mode` | `constantTemp`, `timeLimit`, or `globalSchedule` |
| `mode_id` | From `raw_data.mode.id` |
| `zone_id` | From `raw_data.zone.id` (internal!) |
| `parent_id` | Same as zone_id |
| `constant_temp` | Temperature in deci-degrees |
| `active_schedule` | Current schedule index |
| `hours` | (timeLimit only) 0-23 |
| `minutes` | (timeLimit only) 0-59 |

**Response:** Body `1` on success.

**Important:**
- Maximum timeLimit duration: 1439 minutes (23:59)
- `mode=off` and `mode=con` do NOT work for thermostat zones
- `mode=localSchedule` direct switch is unreliable; use scheduler/store instead

### Save Schedule

```
POST /e-control/zones/zone/{moduleId}/{zoneApiId}/scheduler/store
```

**Form data must include:**
- All form fields from the schedule form HTML
- `dataSerialize` — URL-encoded serialization of all fields
- `data` — JSON serialization of all fields

**Interval fields must be grouped per interval:**
```
p0Intervals[index][]=0
p0Intervals[start][]=09:00
p0Intervals[end][]=16:30
p0Intervals[temp][]=19
p0Intervals[index][]=1
p0Intervals[start][]=17:00
p0Intervals[end][]=23:00
p0Intervals[temp][]=14
```

NOT grouped by field type (all starts together, etc.).

**Schedule constraints:**
- Times must be on **15-minute boundaries** (xx:00, xx:15, xx:30, xx:45)
- Non-quarter times are silently rejected
- Each profile supports up to 3 intervals

### AJAX Refresh (duringChange Detection)

```
GET /e-control/zones/ajax/refresh?account_module_index=<ami>
```

Returns JSON with `html` field containing zone tiles. Parse for `during-change` CSS class:
- `d-none` = idle
- `d-flex` = updating

This is the same mechanism the Eplucon portal uses to show "Updaten van de parameters".

## MQTT Discovery

The zone controller communicates with an MQTT broker:

| Detail | Value |
|--------|-------|
| Broker | `13.81.241.237:1883` (plain, no TLS) |
| Username | `tech` |
| Password | `M!zpe3aS` |
| Client ID | Device MAC-based hash |
| Subscribe | `<device_id>/in/#` |
| Publish | `<device_id>/diagnostic`, `<device_id>/status` |

MQTT is used for **diagnostics and status** only, not for temperature control commands. The actual control data goes through a **proprietary TCP connection** on port 2000 (`172.211.216.31`).

## Local Device HTTP API

Both devices (zone controller and ThTouch) have port 80 open with Basic Auth ("Configuration Portal" realm). Default credentials are unknown. The MQTT credentials (`tech`/`M!zpe3aS`) do not work for the local HTTP API.

## Temperature Conversion

```
API response  → decimal:      19.5  (°C)
raw_data      → deci-degrees: 195   (integer)
POST body     → deci-degrees: 195   (integer)

Formula: deci_degrees = temperature_celsius × 10
```
