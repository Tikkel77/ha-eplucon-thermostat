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
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Zone
from .api.schedule import get_next_schedule_start
from .const import (
    DOMAIN, MANUFACTURER,
    RAW_SENSOR_DEFS, FRIENDLY_TEXT_SENSOR_DEFS, HEATLOADING_SENSOR_DEFS,
    EpluconSensorEntityDescription, normalize_number,
)
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


def _schedule_intervals_attr(zone: Zone) -> dict | None:
    """Return structured schedule intervals for use in extra_state_attributes."""
    schedule = zone.raw_data.get("schedule", {})
    if not schedule:
        return None

    result: dict = {}
    for profile, label in (("p0", "weekday"), ("p1", "weekend")):
        intervals = schedule.get(f"{profile}Intervals", [])
        active = []
        for iv in intervals:
            if not isinstance(iv, dict):
                continue
            s = iv.get("start", 6100)
            e = iv.get("stop", 6100)
            if s >= 1440:
                continue
            active.append({
                "start": f"{s // 60:02d}:{s % 60:02d}",
                "end": f"{e // 60:02d}:{e % 60:02d}" if e < 1440 else None,
                "temp": iv.get("temp", 0) / 10.0,
            })
        result[label] = active

        # Setback temp
        raw_sb = schedule.get(f"{profile}SetbackTemp")
        if raw_sb is not None:
            try:
                result[f"{label}_setback"] = int(raw_sb) / 10.0
            except (ValueError, TypeError):
                pass

    return result


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

    for module_id in coordinator.heatpumps:
        entities.append(HeatpumpPowerSensor(coordinator, module_id, entry, is_water_heating=True))
        entities.append(HeatpumpPowerSensor(coordinator, module_id, entry, is_water_heating=False))

        for def_dict in RAW_SENSOR_DEFS:
            desc = EpluconSensorEntityDescription(
                key=def_dict["key"],
                name=def_dict["name"],
                native_unit_of_measurement=def_dict.get("unit"),
                state_class=def_dict.get("state_class"),
                device_class=def_dict.get("device_class"),
                value_fn=lambda device, attr=def_dict["attr"]: normalize_number(getattr(device.realtime_info.common, attr)),
                exists_fn=lambda device, attr=def_dict["attr"]: getattr(device.realtime_info.common, attr) is not None,
            )
            entities.append(HeatpumpSensor(coordinator, module_id, desc))
            
        for def_dict in FRIENDLY_TEXT_SENSOR_DEFS:
            desc = EpluconSensorEntityDescription(
                key=def_dict["key"],
                name=def_dict["name"],
                value_fn=def_dict["value_fn"],
            )
            entities.append(HeatpumpSensor(coordinator, module_id, desc))
            
        for def_dict in HEATLOADING_SENSOR_DEFS:
            if "value_fn" in def_dict:
                desc = EpluconSensorEntityDescription(
                    key=def_dict["key"],
                    name=def_dict["name"],
                    device_class=def_dict.get("device_class"),
                    value_fn=def_dict["value_fn"],
                    exists_fn=def_dict.get("exists_fn", lambda _: True),
                )
                entities.append(HeatpumpSensor(coordinator, module_id, desc))

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

    @property
    def extra_state_attributes(self) -> dict | None:
        """Expose structured schedule data on the schedule_summary sensor."""
        if self._sensor_type["key"] != "schedule_summary":
            return None
        zone = self._zone
        if zone is None:
            return None
        intervals = _schedule_intervals_attr(zone)
        if intervals is None:
            return None
        return {"schedule_intervals": intervals}


class HeatpumpSensor(CoordinatorEntity[EpluconDataCoordinator], SensorEntity):
    """Eplucon Heatpump sensor."""

    _attr_has_entity_name = True
    entity_description: EpluconSensorEntityDescription

    def __init__(self, coordinator: EpluconDataCoordinator, module_id: int, description: EpluconSensorEntityDescription):
        super().__init__(coordinator)
        self._module_id = module_id
        self.entity_description = description
        
        hp = coordinator.heatpumps[module_id]
        self._attr_unique_id = f"eplucon_hp_{module_id}_{description.key}"
        
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"hp_{module_id}")},
            name=hp.name,
            manufacturer=MANUFACTURER,
            model="Heatpump",
        )

    @property
    def _heatpump(self):
        return self.coordinator.heatpumps.get(self._module_id)

    @property
    def available(self) -> bool:
        hp = self._heatpump
        if not super().available or not hp or not hp.realtime_info:
            return False
        return self.entity_description.exists_fn(hp)

    @property
    def native_value(self):
        hp = self._heatpump
        if not hp or not hp.realtime_info:
            return None
        return self.entity_description.value_fn(hp)


class HeatpumpPowerSensor(CoordinatorEntity[EpluconDataCoordinator], SensorEntity):
    """Estimated power usage sensor based on compressor RPM."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: EpluconDataCoordinator,
        module_id: int,
        entry: ConfigEntry,
        is_water_heating: bool,
    ) -> None:
        super().__init__(coordinator)
        self._module_id = module_id
        self._entry = entry
        self._is_water_heating = is_water_heating

        hp = coordinator.heatpumps[module_id]
        kind = "water_heating" if is_water_heating else "space_heating"
        self._attr_unique_id = f"eplucon_hp_{module_id}_power_{kind}"
        self._attr_name = "Power Usage Water Heating" if is_water_heating else "Power Usage Space Heating"
        
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"hp_{module_id}")},
            name=hp.name,
            manufacturer=MANUFACTURER,
            model="Heatpump",
        )

    @property
    def _heatpump(self):
        return self.coordinator.heatpumps.get(self._module_id)

    @property
    def available(self) -> bool:
        hp = self._heatpump
        return super().available and hp is not None and hp.realtime_info is not None

    @property
    def native_value(self):
        hp = self._heatpump
        if not hp or not hp.realtime_info:
            return None

        # Check if doing Domestic Hot Water
        active_ww_str = str(hp.realtime_info.common.active_requests_ww).upper()
        active_ww = active_ww_str in ["1", "ON", "TRUE"]

        if self._is_water_heating and not active_ww:
            return 0.0
        if not self._is_water_heating and active_ww:
            return 0.0

        try:
            rpm = float(hp.realtime_info.common.compressor_speed)
        except (TypeError, ValueError):
            return 0.0

        if rpm <= 0:
            return 0.0

        opts = self._entry.options
        max_rpm = opts.get("max_rpm", 5400)
        pct = (rpm / max_rpm) * 100

        if self._is_water_heating:
            a = opts.get("ww_a", 0.0)
            b = opts.get("ww_b", 40.0)
            c = opts.get("ww_c", 200.0)
        else:
            a = opts.get("sh_a", 0.03)
            b = opts.get("sh_b", 25.0)
            c = opts.get("sh_c", 0.0)

        power = (a * (pct ** 2)) + (b * pct) + c
        return round(power, 1)
