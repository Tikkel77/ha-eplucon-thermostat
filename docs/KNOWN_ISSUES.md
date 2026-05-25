# Known Issues & Discoveries

## Portal Session Issues in HA

### Problem
HTTP POST requests to `https://portaal.eplucon.nl/e-control/set_constant_temp` return 401 ("Unauthenticated") when executed from within the Home Assistant event loop context, despite the same code working perfectly when run:
- From the same HA Docker container via `docker exec`
- Via `subprocess.run()` from a standalone Python script in the same container
- From the webapp on a separate PC

### What Has Been Tried
1. **requests in executor thread** — 401 on every attempt
2. **Separate requests.Session** (`_portal_session`) — 401
3. **Fresh EpluconClient per write** — 401
4. **aiohttp with own CookieJar** — 401 (login succeeds, CSRF succeeds, POST fails)
5. **asyncio.create_subprocess_exec** — 401 (subprocess runs but write fails)
6. **subprocess.run via run_in_executor** — 401

### What Works
- `docker exec -i homeassistant python3 /config/eplucon_write_helper.py` — **WORKS** (200, body=1)
- `subprocess.run()` called from a script launched via `docker exec` — **WORKS**
- The same Python version (3.14.2), same requests library (2.32.5), same environment variables

### Root Cause Analysis
The exact root cause is unknown. No differences were found in:
- Environment variables
- Python version
- requests library version
- Cookie values
- CSRF tokens

The suspicion is that HA's event loop or process context somehow interferes with the HTTP session state management, possibly through:
- Signal handler inheritance affecting cookie handling
- HA's internal HTTP middleware intercepting outbound requests
- Race condition with the coordinator's Bearer API polling

### Current Workaround
The `portal.py` delegates writes to `/config/eplucon_write_helper.py` via `subprocess.run()` called from `hass.async_add_executor_job()`. This script handles login + CSRF + POST in complete isolation.

**Status:** The subprocess approach still shows 401 when called from HA's executor. The next step is to investigate if there's a timing/concurrency issue with the coordinator polling.

## Schedule Save Quirks

### CSRF Token Expiry
The portal's CSRF tokens expire quickly. The `_submit_program_form` method re-fetches the form fresh before each submit to get a valid token.

### Interval Field Ordering
Portal expects interval fields **grouped per interval**:
```
index[]=0, start[]=09:00, end[]=16:30, temp[]=19
index[]=1, start[]=17:00, end[]=23:00, temp[]=14
```
NOT grouped by field type. The `_apply_schedule_overrides` method handles this.

### 15-Minute Time Boundaries
Schedule times must be on quarter-hour boundaries. The `_round_time_to_quarter` method automatically rounds non-quarter times before submission.

### dataSerialize + data Fields
Schedule submissions require both `dataSerialize` (URL-encoded) and `data` (JSON serialization) fields in addition to the normal form fields. Without these, the portal returns `{"success":0,"error_code":500}`.

## Eplucon Portal Rate Limiting

The portal is very slow and doesn't handle concurrent requests well:
- Write processing takes 10-30 seconds
- Too many rapid requests cause 500 errors or dropped connections
- A single write at a time (sequential) is required
- The webapp uses a `WRITE_LOCK` to serialize all writes

## MQTT Findings

- MQTT broker: `13.81.241.237:1883` (plain, no TLS)
- Credentials: `tech` / `M!zpe3aS`
- Used for **diagnostics only** (topic: `<device_id>/diagnostic`)
- Temperature control commands do NOT go through MQTT
- Control goes through proprietary TCP on port 2000 (encrypted/binary)
- Publishing to `<device_id>/in/<zone_id>/mode` does NOT reliably change thermostat settings

## Local Device HTTP API

Both the zone controller (192.168.4.66) and ThTouch (192.168.1.19) have HTTP port 80 with Basic Auth "Configuration Portal". No known credentials work. The MQTT credentials do not work for the local API.

## Maximum Time Limit Duration

The maximum `timeLimit` duration is **1439 minutes (23:59)**. Values above this are capped by the thermostat firmware. The physical thermostat UI limits to 8 hours, but the API accepts up to 23:59.
