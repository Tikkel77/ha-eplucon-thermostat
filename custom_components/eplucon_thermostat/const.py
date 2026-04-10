"""Constants for the Eplucon Thermostat integration."""

DOMAIN = "eplucon_thermostat"
MANUFACTURER = "TECH Sterowniki / Eplucon"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_API_KEY = "api_key"

DEFAULT_SCAN_INTERVAL = 300  # 5 minutes

PLATFORMS = ["climate", "sensor", "binary_sensor"]

# Default duration (minutes) when setting a temperature override per zone.
# Keyed by zone_api_id (int).  Zones not listed get DEFAULT_OVERRIDE_MINUTES.
DEFAULT_OVERRIDE_MINUTES = 240  # 4 hours fallback
ZONE_DEFAULT_DURATIONS: dict[int, int] = {
    6813: 480,   # Woonkamer     — 8 h
    6814: 480,   # Keuken        — 8 h
    6812: 120,   # Bijkeuken     — 2 h
    6811: 480,   # Kantoor       — 8 h
    6815: 120,   # Badkamer 1e   — 2 h
    6817: 480,   # Kantoor 1e    — 8 h
    6816: 120,   # Slpk Master   — 2 h
    6820: 120,   # Slpk Tiebe    — 2 h
    6818: 120,   # Slpk Lotte    — 2 h
    6821: 120,   # Badkamer zolder — 2 h
    6819: 120,   # Zolderkamer   — 2 h
}

# Con mode: maximum duration (8 hours)
CON_MAX_MINUTES = 480
