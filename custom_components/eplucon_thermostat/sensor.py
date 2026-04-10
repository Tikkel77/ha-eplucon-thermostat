"""Sensor platform for Eplucon Thermostat."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Zone
from .api.schedule import get_next_schedule_start
from .const import DOMAIN, MANUFACTURER
from .coordinator import EpluconDataCoordinator

_LOGGER = logging.getLogger(__name__)


def _next_schedule_change_iso(zone: Zone) -> datetime | None:
    """Return datetime of the next schedule transition for this zone."""
    return get_next_schedule_start(zone)


def _setback_temp_c(zone: Zone, profile: str) -> float | None:
    """Return setback temperature in °C for profile p0 or p1."""
    schedule = zone.raw_data.get("schedule", {})
    raw = schedule.get(f"{profile}SetbackTemp")
    if raw is None:
        return None
    try:
        return int(raw) / 10.0
    except (ValueError, TypeError):
        return None


def _schedule_summary(zone: Zone) -> str | None:
    """Return a human-readable summary of the active schedule intervals."""
    schedule = zone.raw_data.get("schedule", {})
    if not schedule:
        return None

    parts: list[str] = []
    for profile, label in (("p0", "Weekday"), ("p1", "Weekend")):
        intervals = schedule.get(f"{profile}Intervals", [])
        active = [iv for iv in intervals if isinstance(iv, dict) and iv.get("start", 6100) < 1440]
        if not active:
            continue
        iv_strs = []
        for iv in active:
            s = iv["start"]
            e = iv["stop"] if iv.get("stop", 6100) < 1440 else None
            t = iv.get("temp", 0) / 10.0
            s_str = f"{s // 60:02d}:{s % 60:02d}"
            e_str = f"{e // 60:02d}:{e % 60:02d}" if e is not None else "—"
            iv_strs.append(f"{s_str}-{e_str} {t:.0f}°C")
        parts.append(f"{label}: {', '.join(iv_strs)}")

    return "; ".join(parts) if parts else "No intervals"


SENSOR_TYPES = [
    {
        "key": "current_temperature",
        "name": "Current temperature",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "value_fn": lambda z: z.current_temperature_c,
    },
    {
        "key": "set_temperature",
        "name": "Set temperature",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "value_fn": lambda z: z.set_temperature_c,
    },
    {
        "key": "humidity",
        "name": "Humidity",
        "device_class": SensorDeviceClass.HUMIDITY,
        "unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "value_fn": lambda z: z.raw_data.get("zone", {}).get("humidity"),
    },
    {
        "key": "signal_strength",
        "name": "Signal strength",
        "device_class": None,
        "unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "value_fn": lambda z: z.raw_data.get("zone", {}).get("signalStrength"),
        "icon": "mdi:wifi",
    },
    {
        "key": "battery_level",
        "name": "Battery level",
        "device_class": SensorDeviceClass.BATTERY,
        "unit": PERCENTAGE,
        "state_class": SensorStateClass.MEASUREMENT,
        "value_fn": lambda z: z.raw_data.get("zone", {}).get("batteryLevel"),
    },
    {
        "key": "time_remaining",
        "name": "Time limit remaining",
        "device_class": None,
        "unit": "min",
        "state_class": SensorStateClass.MEASUREMENT,
        "value_fn": lambda z: z.const_temp_time if z.mode == "timeLimit" else None,
        "icon": "mdi:timer-outline",
    },
    {
        "key": "mode",
        "name": "Operating mode",
        "device_class": None,
        "unit": None,
        "state_class": None,
        "value_fn": lambda z: z.mode,
        "icon": "mdi:thermostat",
    },
    {
        "key": "next_schedule_change",
        "name": "Next schedule change",
        "device_class": SensorDeviceClass.TIMESTAMP,
        "unit": None,
        "state_class": None,
        "value_fn": lambda z: _next_schedule_change_iso(z),
        "icon": "mdi:calendar-clock",
    },
    {
        "key": "setback_weekday",
        "name": "Setback temperature weekday",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "value_fn": lambda z: _setback_temp_c(z, "p0"),
        "icon": "mdi:thermometer-low",
    },
    {
        "key": "setback_weekend",
        "name": "Setback temperature weekend",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "value_fn": lambda z: _setback_temp_c(z, "p1"),
        "icon": "mdi:thermometer-low",
    },
    {
        "key": "schedule_summary",
        "name": "Schedule summary",
        "device_class": None,
        "unit": None,
        "state_class": None,
        "value_fn": lambda z: _schedule_summary(z),
        "icon": "mdi:calendar-text",
    },
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eplucon sensor entities."""
    coordinator: EpluconDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for zone_api_id in coordinator.data:
        for sensor_type in SENSOR_TYPES:
            entities.append(
                EpluconSensorEntity(coordinator, zone_api_id, sensor_type)
            )
    async_add_entities(entities)


class EpluconSensorEntity(CoordinatorEntity[EpluconDataCoordinator], SensorEntity):
    """Eplucon zone sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EpluconDataCoordinator,
        zone_api_id: int,
        sensor_type: dict,
    ) -> None:
        super().__init__(coordinator)
        self._zone_api_id = zone_api_id
        self._sensor_type = sensor_type
        zone = coordinator.data[zone_api_id]

        key = sensor_type["key"]
        self._attr_unique_id = f"eplucon_{zone_api_id}_{key}"
        self._attr_name = sensor_type["name"]
        self._attr_device_class = sensor_type.get("device_class")
        self._attr_native_unit_of_measurement = sensor_type.get("unit")
        self._attr_state_class = sensor_type.get("state_class")
        if "icon" in sensor_type:
            self._attr_icon = sensor_type["icon"]

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(zone_api_id))},
            name=zone.name,
            manufacturer=MANUFACTURER,
        )

    @property
    def _zone(self) -> Zone | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._zone_api_id)

    @property
    def available(self) -> bool:
        return super().available and self._zone is not None

    @property
    def native_value(self):
        zone = self._zone
        if zone is None:
            return None
        try:
            return self._sensor_type["value_fn"](zone)
        except Exception:
            return None
