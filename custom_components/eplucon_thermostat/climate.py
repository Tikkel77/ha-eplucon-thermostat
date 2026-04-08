"""Climate platform for Eplucon Thermostat."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Zone
from .const import DOMAIN, MANUFACTURER
from .coordinator import EpluconDataCoordinator

_LOGGER = logging.getLogger(__name__)

# Map Eplucon modes to HA preset modes
PRESET_CONSTANT = "Constant"
PRESET_TIME_LIMIT = "Time limit"
PRESET_SCHEDULE = "Schedule"

EPLUCON_MODE_TO_PRESET = {
    "constantTemp": PRESET_CONSTANT,
    "timeLimit": PRESET_TIME_LIMIT,
    "localSchedule": PRESET_SCHEDULE,
    "globalSchedule": PRESET_SCHEDULE,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eplucon climate entities."""
    coordinator: EpluconDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        EpluconClimateEntity(coordinator, zone_api_id)
        for zone_api_id in coordinator.data
    ]
    async_add_entities(entities)


class EpluconClimateEntity(CoordinatorEntity[EpluconDataCoordinator], ClimateEntity):
    """Eplucon zone thermostat as HA climate entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 5.0
    _attr_max_temp = 35.0
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO]
    _attr_preset_modes = [PRESET_CONSTANT, PRESET_TIME_LIMIT, PRESET_SCHEDULE]

    def __init__(
        self,
        coordinator: EpluconDataCoordinator,
        zone_api_id: int,
    ) -> None:
        super().__init__(coordinator)
        self._zone_api_id = zone_api_id
        zone = coordinator.data[zone_api_id]
        self._attr_unique_id = f"eplucon_{zone_api_id}_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(zone_api_id))},
            name=zone.name,
            manufacturer=MANUFACTURER,
            model=zone.raw_data.get("description", {}).get("styleIcon", "thermostat"),
            via_device=(DOMAIN, str(zone.module_id)),
        )

    @property
    def _zone(self) -> Zone | None:
        return self.coordinator.get_zone(self._zone_api_id)

    @property
    def available(self) -> bool:
        return super().available and self._zone is not None

    @property
    def current_temperature(self) -> float | None:
        zone = self._zone
        return zone.current_temperature_c if zone else None

    @property
    def target_temperature(self) -> float | None:
        zone = self._zone
        return zone.set_temperature_c if zone else None

    @property
    def current_humidity(self) -> int | None:
        zone = self._zone
        if zone and zone.raw_data:
            return zone.raw_data.get("zone", {}).get("humidity")
        return None

    @property
    def hvac_mode(self) -> HVACMode:
        zone = self._zone
        if zone is None:
            return HVACMode.OFF
        mode = zone.mode
        if mode in ("localSchedule", "globalSchedule"):
            return HVACMode.AUTO
        # For constantTemp and timeLimit, derive from algorithm
        algo = zone.raw_data.get("zone", {}).get("flags", {}).get("algorithm", "heating")
        if algo == "cooling":
            return HVACMode.COOL
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction:
        zone = self._zone
        if zone is None:
            return HVACAction.OFF
        flags = zone.raw_data.get("zone", {}).get("flags", {})
        relay = flags.get("relayState", "off")
        if relay != "on":
            return HVACAction.IDLE
        algo = flags.get("algorithm", "heating")
        if algo == "cooling":
            return HVACAction.COOLING
        return HVACAction.HEATING

    @property
    def preset_mode(self) -> str | None:
        zone = self._zone
        if zone is None:
            return None
        return EPLUCON_MODE_TO_PRESET.get(zone.mode, PRESET_CONSTANT)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        zone = self._zone
        if zone is None:
            return
        await self.coordinator.async_set_temperature(zone, temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        zone = self._zone
        if zone is None:
            return

        if hvac_mode == HVACMode.AUTO:
            # Activate schedule (use current schedule_index, or local if none)
            idx = zone.schedule_index if zone.schedule_index >= 0 else zone.schedule_index
            # Try to find the local schedule index
            try:
                forms = await self.hass.async_add_executor_job(
                    self.coordinator.client.get_program_forms, zone
                )
                local = [f for f in forms if f.index < 0]
                if local:
                    idx = local[0].index
            except Exception:
                pass
            await self.coordinator.async_activate_program(zone, idx)

        elif hvac_mode in (HVACMode.HEAT, HVACMode.COOL):
            # Set constant temperature at current setpoint
            await self.coordinator.async_set_temperature(zone, zone.set_temperature_c)

        elif hvac_mode == HVACMode.OFF:
            # Set to minimum temperature as "off"
            await self.coordinator.async_set_temperature(zone, self._attr_min_temp)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode."""
        zone = self._zone
        if zone is None:
            return

        if preset_mode == PRESET_SCHEDULE:
            # Activate local schedule
            try:
                forms = await self.hass.async_add_executor_job(
                    self.coordinator.client.get_program_forms, zone
                )
                local = [f for f in forms if f.index < 0]
                if local:
                    await self.coordinator.async_activate_program(zone, local[0].index)
            except Exception as err:
                _LOGGER.error("Failed to activate schedule: %s", err)

        elif preset_mode == PRESET_CONSTANT:
            await self.coordinator.async_set_temperature(zone, zone.set_temperature_c)

        elif preset_mode == PRESET_TIME_LIMIT:
            # Set time limit with default 4 hours at current temperature
            await self.coordinator.async_set_time_limit_temperature(
                zone, zone.set_temperature_c, 240
            )
