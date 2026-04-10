"""The Eplucon Thermostat integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from .api import EpluconClient
from .portal import EpluconPortalClient
from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_API_KEY
from .coordinator import EpluconDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR]

SERVICE_SET_SETBACK = "set_setback_temperature"
SERVICE_SET_SCHEDULE = "set_schedule"

ATTR_ZONE_API_ID = "zone_api_id"
ATTR_PROGRAM_INDEX = "program_index"
ATTR_WEEKDAY_SETBACK = "weekday_setback"
ATTR_WEEKEND_SETBACK = "weekend_setback"
ATTR_ACTIVATE = "activate"
ATTR_PROFILE = "profile"
ATTR_INTERVALS = "intervals"

SET_SETBACK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ZONE_API_ID): cv.positive_int,
        vol.Optional(ATTR_PROGRAM_INDEX): int,
        vol.Optional(ATTR_WEEKDAY_SETBACK): vol.Coerce(float),
        vol.Optional(ATTR_WEEKEND_SETBACK): vol.Coerce(float),
    }
)

INTERVAL_SCHEMA = vol.Schema(
    {
        vol.Required("start"): cv.string,
        vol.Required("end"): cv.string,
        vol.Required("temp"): vol.Coerce(float),
    }
)

SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ZONE_API_ID): cv.positive_int,
        vol.Optional(ATTR_PROGRAM_INDEX): int,
        vol.Required(ATTR_PROFILE): vol.In(["weekday", "weekend"]),
        vol.Required(ATTR_INTERVALS): vol.All(cv.ensure_list, [INTERVAL_SCHEMA]),
        vol.Optional(ATTR_ACTIVATE, default=True): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eplucon Thermostat from a config entry."""
    # Read-only client (Bearer API, runs in executor for sync requests)
    client = EpluconClient(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        api_key=entry.data[CONF_API_KEY],
    )

    # Write client (portal session, subprocess-based)
    portal = EpluconPortalClient(
        hass=hass,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )
    await portal.async_init()

    coordinator = EpluconDataCoordinator(hass, client, portal)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (only once, even if multiple config entries)
    if not hass.services.has_service(DOMAIN, SERVICE_SET_SETBACK):
        _register_services(hass)

    return True


def _get_coordinator(hass: HomeAssistant) -> EpluconDataCoordinator:
    """Get the first coordinator from hass.data."""
    coordinators = hass.data.get(DOMAIN, {})
    if not coordinators:
        raise ValueError("No Eplucon integration configured")
    return next(iter(coordinators.values()))


def _detect_local_program_index(
    coordinator: EpluconDataCoordinator, zone,
) -> int:
    """Auto-detect the local schedule program index for a zone.

    Local programs have negative indices. Returns the first negative index
    found from the zone's program forms, or falls back to zone.schedule_index.
    """
    try:
        forms = coordinator.client.get_program_forms(zone)
        local = [f.index for f in forms if f.index < 0]
        if local:
            return local[0]
    except Exception:
        _LOGGER.debug("Could not detect local program index for %s", zone.name)
    # Fall back to the zone's current schedule index
    idx = getattr(zone, "schedule_index", -1)
    return idx if idx < 0 else -1


def _register_services(hass: HomeAssistant) -> None:
    """Register custom services for schedule management."""

    async def handle_set_setback(call: ServiceCall) -> None:
        """Handle set_setback_temperature service call."""
        coordinator = _get_coordinator(hass)
        zone_api_id = call.data[ATTR_ZONE_API_ID]
        program_index = call.data.get(ATTR_PROGRAM_INDEX)
        weekday = call.data.get(ATTR_WEEKDAY_SETBACK)
        weekend = call.data.get(ATTR_WEEKEND_SETBACK)

        if weekday is None and weekend is None:
            raise ValueError("Provide at least weekday_setback or weekend_setback")

        zone = coordinator.get_zone(zone_api_id)
        if zone is None:
            raise ValueError(f"Zone {zone_api_id} not found")

        _LOGGER.info(
            "Setting setback temps for %s: weekday=%s weekend=%s",
            zone.name, weekday, weekend,
        )

        def _do_setback() -> None:
            idx = program_index
            if idx is None:
                idx = _detect_local_program_index(coordinator, zone)
            coordinator.client.set_program_setback(
                zone, idx,
                p0_setback_c=weekday,
                p1_setback_c=weekend,
                wait=False,
            )

        await hass.async_add_executor_job(_do_setback)
        coordinator._schedule_delayed_refresh(zone_api_id)

    async def handle_set_schedule(call: ServiceCall) -> None:
        """Handle set_schedule service call.

        Sets schedule intervals for a zone's weekday or weekend profile.
        Each interval has start (HH:MM), end (HH:MM), and temp (°C).
        """
        coordinator = _get_coordinator(hass)
        zone_api_id = call.data[ATTR_ZONE_API_ID]
        program_index = call.data.get(ATTR_PROGRAM_INDEX)
        profile = call.data[ATTR_PROFILE]
        intervals = call.data[ATTR_INTERVALS]
        activate = call.data[ATTR_ACTIVATE]

        zone = coordinator.get_zone(zone_api_id)
        if zone is None:
            raise ValueError(f"Zone {zone_api_id} not found")

        prefix = "p0" if profile == "weekday" else "p1"

        # Build overrides dict for submit_program_form
        overrides: dict[str, str | list[str]] = {}
        indices: list[str] = []
        starts: list[str] = []
        ends: list[str] = []
        temps: list[str] = []

        for i, iv in enumerate(intervals):
            indices.append(str(i))
            starts.append(iv["start"])
            ends.append(iv["end"])
            temps.append(str(int(round(iv["temp"]))))

        overrides[f"{prefix}Intervals[index][]"] = indices
        overrides[f"{prefix}Intervals[start][]"] = starts
        overrides[f"{prefix}Intervals[end][]"] = ends
        overrides[f"{prefix}Intervals[temp][]"] = temps

        _LOGGER.info(
            "Setting schedule for %s (%s): %d intervals, activate=%s",
            zone.name, profile, len(intervals), activate,
        )

        def _do_submit() -> None:
            idx = program_index
            if idx is None:
                idx = _detect_local_program_index(coordinator, zone)
            coordinator.client.submit_program_form(
                zone, idx, overrides=overrides, wait=False,
            )
            if activate:
                import time as _time
                _time.sleep(5)
                fresh = coordinator.client.get_zone(
                    zone_api_id=zone.zone_api_id, module_id=zone.module_id,
                )
                mode = "globalSchedule" if idx >= 0 else "localSchedule"
                coordinator.client._write_set_constant_temp(
                    zone=fresh,
                    mode=mode,
                    constant_temp_deci=fresh.set_temperature_deci,
                    active_schedule=idx,
                    hours=None,
                    minutes=None,
                )

        await hass.async_add_executor_job(_do_submit)
        coordinator._schedule_delayed_refresh(zone_api_id)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_SETBACK, handle_set_setback, schema=SET_SETBACK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_SCHEDULE, handle_set_schedule, schema=SET_SCHEDULE_SCHEMA,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: EpluconDataCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.portal.async_close()
        # Remove services if no more entries
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_SET_SETBACK)
            hass.services.async_remove(DOMAIN, SERVICE_SET_SCHEDULE)
    return unload_ok
