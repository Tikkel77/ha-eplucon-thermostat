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

# Zone API ID → slug used for input helper entity IDs
ZONE_SLUGS: dict[int, str] = {
    6811: "kantoor",
    6812: "bijkeuken",
    6813: "woonkamer",
    6814: "keuken",
    6815: "badkamer_1e",
    6816: "slaapkamer_master",
    6817: "kantoor_1e",
    6818: "slaapkamer_lotte",
    6819: "zolderkamer",
    6820: "slaapkamer_tiebe",
    6821: "badkamer_zolder",
}

# Con mode: maximum duration (8 hours)
CON_MAX_MINUTES = 480

# Debounce: seconds to wait after last +/- press before sending to portal
DEBOUNCE_SECONDS = 2.0


# ------------------------------------------------------------------------------------
# Heatpump Sensor definitions and constants
# ------------------------------------------------------------------------------------

from collections.abc import Callable
from typing import Any
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntityDescription,
    BinarySensorDeviceClass,
)

from homeassistant.components.sensor import (
    SensorEntityDescription,
    SensorDeviceClass,
    SensorStateClass,
)

from homeassistant.const import (
    UnitOfTemperature,
    UnitOfPressure,
    UnitOfEnergy,
    UnitOfTime,
    UnitOfPower,
    REVOLUTIONS_PER_MINUTE,
    PERCENTAGE,
)

def get_friendly_operation_mode_text(device) -> str:
    try:
        operation_mode = int(device.realtime_info.common.operation_mode)
    except (TypeError, ValueError):
        return "Unavailable"

    return {
        1: "Koeling",
        2: "Verwarming",
        3: "Auto th-TOUCH",
        4: "Auto Wp",
        5: "Haard",
    }.get(operation_mode, "Unknown operation mode")

def get_friendly_heating_mode_text(device) -> str:
    try:
        heating_mode = int(device.realtime_info.common.heating_mode)
    except (TypeError, ValueError):
        return "Unavailable"

    return {
        0: "Off",
        1: "On",
        2: "Emergency operation",
        3: "APX",
    }.get(heating_mode, "Unknown operation mode")

def normalize_bool(value) -> bool:
    return str(value).upper() in ["1", "ON", "TRUE"]

def normalize_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0

@dataclass(kw_only=True)
class EpluconSensorEntityDescription(SensorEntityDescription):
    value_fn: Callable[[Any], Any]
    exists_fn: Callable[[Any], bool] = lambda _: True

@dataclass(kw_only=True)
class EpluconBinarySensorEntityDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Any], bool] | None = None
    exists_fn: Callable[[Any], bool] = lambda _: True

RAW_SENSOR_DEFS = [
    {"key": "indoor_temperature", "name": "Indoor Temperature", "attr": "indoor_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "outdoor_temperature", "name": "Outdoor Temperature", "attr": "outdoor_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "brine_in_temperature", "name": "Brine In Temperature", "attr": "brine_in_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "brine_out_temperature", "name": "Brine Out Temperature", "attr": "brine_out_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "configured_indoor_temperature", "name": "Configured Indoor Temperature", "attr": "configured_indoor_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "heating_in_temperature", "name": "Heating In Temperature", "attr": "heating_in_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "heating_out_temperature", "name": "Heating Out Temperature", "attr": "heating_out_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "ww_temperature", "name": "WW Temperature", "attr": "ww_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "ww_temperature_configured", "name": "WW Temperature Configured", "attr": "ww_temperature_configured", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "brine_pressure", "name": "Brine Pressure", "attr": "brine_pressure", "unit": UnitOfPressure.BAR, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.PRESSURE},
    {"key": "cv_pressure", "name": "CV Pressure", "attr": "cv_pressure", "unit": UnitOfPressure.BAR, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.PRESSURE},
    {"key": "suction_gas_pressure", "name": "Suction Gas Pressure", "attr": "suction_gas_pressure", "unit": UnitOfPressure.BAR, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.PRESSURE},
    {"key": "press_gas_pressure", "name": "Press Gas Pressure", "attr": "press_gas_pressure", "unit": UnitOfPressure.BAR, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.PRESSURE},
    {"key": "suction_gas_temperature", "name": "Suction Gas Temperature", "attr": "suction_gas_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "press_gas_temperature", "name": "Press Gas Temperature", "attr": "press_gas_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "condensation_temperature", "name": "Condensation Temperature", "attr": "condensation_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "evaporation_temperature", "name": "Evaporation Temperature", "attr": "evaporation_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "inverter_temperature", "name": "Inverter Temperature", "attr": "inverter_temperature", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "compressor_speed", "name": "Compressor Speed", "attr": "compressor_speed", "unit": REVOLUTIONS_PER_MINUTE, "state_class": SensorStateClass.MEASUREMENT},
    {"key": "brine_circulation_pump", "name": "Brine Circulation Pump", "attr": "brine_circulation_pump", "unit": PERCENTAGE, "state_class": SensorStateClass.MEASUREMENT},
    {"key": "production_circulation_pump", "name": "Production Circulation Pump", "attr": "production_circulation_pump", "unit": PERCENTAGE, "state_class": SensorStateClass.MEASUREMENT},
    {"key": "act_vent_rpm", "name": "Act Vent RPM", "attr": "act_vent_rpm", "unit": REVOLUTIONS_PER_MINUTE, "state_class": SensorStateClass.MEASUREMENT},
    {"key": "total_active_power", "name": "Total Active Power", "attr": "total_active_power", "unit": UnitOfPower.KILO_WATT, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.POWER},
    {"key": "energy_usage", "name": "Energy Usage", "attr": "energy_usage", "unit": UnitOfEnergy.KILO_WATT_HOUR, "state_class": SensorStateClass.TOTAL_INCREASING, "device_class": SensorDeviceClass.ENERGY},
    {"key": "energy_delivered", "name": "Energy Delivered", "attr": "energy_delivered", "unit": UnitOfEnergy.KILO_WATT_HOUR, "state_class": SensorStateClass.TOTAL_INCREASING, "device_class": SensorDeviceClass.ENERGY},
    {"key": "import_energy", "name": "Import Energy", "attr": "import_energy", "unit": UnitOfEnergy.KILO_WATT_HOUR, "state_class": SensorStateClass.TOTAL_INCREASING, "device_class": SensorDeviceClass.ENERGY},
    {"key": "export_energy", "name": "Export Energy", "attr": "export_energy", "unit": UnitOfEnergy.KILO_WATT_HOUR, "state_class": SensorStateClass.TOTAL_INCREASING, "device_class": SensorDeviceClass.ENERGY},
    {"key": "spf", "name": "Seasonal Performance Factor (SPF)", "attr": "spf", "state_class": SensorStateClass.MEASUREMENT},
    {"key": "overheating", "name": "Overheating", "attr": "overheating", "unit": UnitOfTemperature.CELSIUS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.TEMPERATURE},
    {"key": "position_expansion_ventil", "name": "Position Expansion Ventil", "attr": "position_expansion_ventil", "unit": PERCENTAGE, "state_class": SensorStateClass.MEASUREMENT},
    {"key": "number_of_starts", "name": "Number of Starts", "attr": "number_of_starts", "state_class": SensorStateClass.TOTAL_INCREASING},
    {"key": "operating_hours", "name": "Operating Hours", "attr": "operating_hours", "unit": UnitOfTime.HOURS, "state_class": SensorStateClass.MEASUREMENT, "device_class": SensorDeviceClass.DURATION},
]

ONOFF_SENSOR_DEFS = [
    {"key": "dg1", "name": "Direct Outlet (DG1)", "attr": "dg1", "device_class": BinarySensorDeviceClass.RUNNING},
    {"key": "sg2", "name": "Mixture Outlet (SG2)", "attr": "sg2", "device_class": BinarySensorDeviceClass.RUNNING},
    {"key": "sg3", "name": "Mixture Outlet (SG3)", "attr": "sg3", "device_class": BinarySensorDeviceClass.RUNNING},
    {"key": "sg4", "name": "Mixture Outlet (SG4)", "attr": "sg4", "device_class": BinarySensorDeviceClass.RUNNING},
    {"key": "warmwater", "name": "Warm Water", "attr": "warmwater", "device_class": None},
    {"key": "alarm_active", "name": "Alarm Active", "attr": "alarm_active", "device_class": BinarySensorDeviceClass.PROBLEM},
    {"key": "current_heating_pump_state", "name": "Current Heating Pump State", "attr": "current_heating_pump_state", "device_class": None},
    {"key": "current_heating_state", "name": "Current Heating State", "attr": "current_heating_state", "device_class": BinarySensorDeviceClass.HEAT},
    {"key": "active_requests_ww", "name": "Active WW request", "attr": "active_requests_ww", "device_class": None},
]

HEATLOADING_SENSOR_DEFS = [
    {"key": "heatloading_active", "name": "Heatloading Active", "attr": "heatloading_active", "device_class": None, "value_fn": lambda d: d.heatloading_status.heatloading_active, "exists_fn": lambda d: d.heatloading_status is not None,},
    {"key": "domestic_hot_water", "name": "Domestic Hot Water", "attr": "domestic_hot_water", "device_class": None, "value_fn": lambda d: d.heatloading_status.configurations.get("domestic_hot_water"), "exists_fn": lambda d: (d.heatloading_status is not None and d.heatloading_status.configurations is not None and "domestic_hot_water" in d.heatloading_status.configurations),},
    {"key": "heatloading_for_heating", "name": "Heatloading for Heating", "attr": "heatloading_for_heating", "device_class": None, "value_fn": lambda d: d.heatloading_status.configurations.get("heatloading_for_heating"), "exists_fn": lambda d: (d.heatloading_status is not None and d.heatloading_status.configurations is not None and "heatloading_for_heating" in d.heatloading_status.configurations),},
]

FRIENDLY_TEXT_SENSOR_DEFS = [
    {"key": "operation_mode_text", "name": "Operation Mode Text", "value_fn": get_friendly_operation_mode_text},
    {"key": "heating_mode_text", "name": "Heating Mode Text", "value_fn": get_friendly_heating_mode_text},
]
