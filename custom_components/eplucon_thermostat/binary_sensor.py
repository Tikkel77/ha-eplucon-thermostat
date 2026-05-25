"""Binary sensor platform for Eplucon Thermostat."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Zone
from .const import (
    DOMAIN, MANUFACTURER,
    ONOFF_SENSOR_DEFS,
    EpluconBinarySensorEntityDescription, normalize_bool,
)
from .coordinator import EpluconDataCoordinator

_LOGGER = logging.getLogger(__name__)


BINARY_SENSOR_TYPES = [
    {
        "key": "heating_active",
        "name": "Heating active",
        "device_class": BinarySensorDeviceClass.HEAT,
        "value_fn": lambda z: z.raw_data.get("zone", {}).get("flags", {}).get("relayState") == "on",
        "icon_on": "mdi:fire",
        "icon_off": "mdi:fire-off",
    },
    {
        "key": "window_open",
        "name": "Window open",
        "device_class": BinarySensorDeviceClass.WINDOW,
        "value_fn": lambda z: z.raw_data.get("zone", {}).get("flags", {}).get("minOneWindowOpen", False),
    },
    {
        "key": "zone_alarm",
        "name": "Zone alarm",
        "device_class": BinarySensorDeviceClass.PROBLEM,
        "value_fn": lambda z: z.raw_data.get("zone", {}).get("zoneState") != "noAlarm",
    },
    {
        "key": "updating",
        "name": "Parameters updating",
        "device_class": BinarySensorDeviceClass.UPDATE,
        "value_fn": lambda z: z.raw_data.get("zone", {}).get("duringChange", False),
    },
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eplucon binary sensor entities."""
    coordinator: EpluconDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for zone_api_id in coordinator.data:
        for sensor_type in BINARY_SENSOR_TYPES:
            entities.append(
                EpluconBinarySensorEntity(coordinator, zone_api_id, sensor_type)
            )

    for module_id in coordinator.heatpumps:
        for def_dict in ONOFF_SENSOR_DEFS:
            desc = EpluconBinarySensorEntityDescription(
                key=def_dict["key"],
                name=def_dict["name"],
                device_class=def_dict.get("device_class"),
                value_fn=lambda device, attr=def_dict["attr"]: normalize_bool(getattr(device.realtime_info.common, attr)),
                exists_fn=lambda device, attr=def_dict["attr"]: getattr(device.realtime_info.common, attr) is not None,
            )
            entities.append(HeatpumpBinarySensor(coordinator, module_id, desc))

    async_add_entities(entities)


class EpluconBinarySensorEntity(
    CoordinatorEntity[EpluconDataCoordinator], BinarySensorEntity
):
    """Eplucon zone binary sensor."""

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
    def is_on(self) -> bool | None:
        zone = self._zone
        if zone is None:
            return None
        try:
            return self._sensor_type["value_fn"](zone)
        except Exception:
            return None

    @property
    def icon(self) -> str | None:
        if "icon_on" in self._sensor_type and "icon_off" in self._sensor_type:
            return self._sensor_type["icon_on"] if self.is_on else self._sensor_type["icon_off"]
        return None


class HeatpumpBinarySensor(CoordinatorEntity[EpluconDataCoordinator], BinarySensorEntity):
    """Eplucon Heatpump binary sensor."""

    _attr_has_entity_name = True
    entity_description: EpluconBinarySensorEntityDescription

    def __init__(self, coordinator: EpluconDataCoordinator, module_id: int, description: EpluconBinarySensorEntityDescription):
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
    def is_on(self) -> bool | None:
        hp = self._heatpump
        if not hp or not hp.realtime_info:
            return None
        return self.entity_description.value_fn(hp)
