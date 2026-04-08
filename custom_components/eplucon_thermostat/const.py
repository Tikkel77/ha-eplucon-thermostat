"""Constants for the Eplucon Thermostat integration."""

DOMAIN = "eplucon_thermostat"
MANUFACTURER = "TECH Sterowniki / Eplucon"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_API_KEY = "api_key"

DEFAULT_SCAN_INTERVAL = 300  # 5 minutes

PLATFORMS = ["climate", "sensor", "binary_sensor"]
