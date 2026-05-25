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
from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_API_KEY, ZONE_SLUGS
from .coordinator import EpluconDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR]

SERVICE_SET_SETBACK = "set_setback_temperature"
SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_CONVERT_CONSTANT = "convert_constant_to_timed"
SERVICE_LOAD_SCHEDULE = "load_schedule"
SERVICE_SAVE_SCHEDULE = "save_schedule"

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

LOAD_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ZONE_API_ID): cv.positive_int,
    }
)

SAVE_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ZONE_API_ID): cv.positive_int,
        vol.Optional(ATTR_PROFILE, default="weekday"): vol.In(["weekday", "weekend"]),
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

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry on options change."""
    await hass.config_entries.async_reload(entry.entry_id)


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

    async def handle_load_schedule(call: ServiceCall) -> None:
        """Load zone schedule data into input helpers for editing.

        Reads the current schedule intervals, setback temps, and
        populates the input_number/input_text/input_select helpers so
        the dashboard schedule editor shows the current values.
        """
        coordinator = _get_coordinator(hass)
        zone_api_id = call.data[ATTR_ZONE_API_ID]
        zone = coordinator.get_zone(zone_api_id)
        if zone is None:
            raise ValueError(f"Zone {zone_api_id} not found")

        slug = ZONE_SLUGS.get(zone_api_id)
        if not slug:
            raise ValueError(f"No slug mapping for zone {zone_api_id}")

        schedule = zone.raw_data.get("schedule", {})

        # Load weekday + weekend intervals into helpers
        for profile, prefix in (("weekday", "p0"), ("weekend", "p1")):
            intervals = schedule.get(f"{prefix}Intervals", [])
            active = [
                iv for iv in intervals
                if isinstance(iv, dict) and iv.get("start", 6100) < 1440
            ]

            for i in range(3):
                start_eid = f"input_text.eplucon_sched_{slug}_{profile}_start_{i+1}"
                end_eid = f"input_text.eplucon_sched_{slug}_{profile}_end_{i+1}"
                temp_eid = f"input_number.eplucon_sched_{slug}_{profile}_temp_{i+1}"

                if i < len(active):
                    iv = active[i]
                    s = iv["start"]
                    e = iv.get("stop", 6100)
                    t = iv.get("temp", 0) / 10.0
                    start_val = f"{s // 60:02d}:{s % 60:02d}"
                    end_val = f"{e // 60:02d}:{e % 60:02d}" if e < 1440 else ""
                    temp_val = t
                else:
                    start_val = ""
                    end_val = ""
                    temp_val = 15.0

                await hass.services.async_call(
                    "input_text", "set_value",
                    {"entity_id": start_eid, "value": start_val},
                    blocking=True,
                )
                await hass.services.async_call(
                    "input_text", "set_value",
                    {"entity_id": end_eid, "value": end_val},
                    blocking=True,
                )
                await hass.services.async_call(
                    "input_number", "set_value",
                    {"entity_id": temp_eid, "value": temp_val},
                    blocking=True,
                )

            # Setback temperature
            setback_eid = f"input_number.eplucon_sched_{slug}_{profile}_setback"
            raw_sb = schedule.get(f"{prefix}SetbackTemp")
            if raw_sb is not None:
                try:
                    sb_val = int(raw_sb) / 10.0
                except (ValueError, TypeError):
                    sb_val = 15.0
            else:
                sb_val = 15.0
            await hass.services.async_call(
                "input_number", "set_value",
                {"entity_id": setback_eid, "value": sb_val},
                blocking=True,
            )

        # Set active profile selector
        profile_eid = f"input_select.eplucon_sched_{slug}_profile"
        await hass.services.async_call(
            "input_select", "select_option",
            {"entity_id": profile_eid, "option": "weekday"},
            blocking=True,
        )

        _LOGGER.info("Loaded schedule for %s into input helpers", zone.name)

    async def handle_save_schedule(call: ServiceCall) -> None:
        """Save input helper values as schedule to the Eplucon portal.

        Reads values from input_number/input_text helpers and calls
        the set_schedule service with those intervals.
        """
        coordinator = _get_coordinator(hass)
        zone_api_id = call.data[ATTR_ZONE_API_ID]
        profile = call.data.get(ATTR_PROFILE, "weekday")
        activate = call.data.get(ATTR_ACTIVATE, True)

        zone = coordinator.get_zone(zone_api_id)
        if zone is None:
            raise ValueError(f"Zone {zone_api_id} not found")

        slug = ZONE_SLUGS.get(zone_api_id)
        if not slug:
            raise ValueError(f"No slug mapping for zone {zone_api_id}")

        # Read intervals from helpers
        intervals = []
        for i in range(3):
            start_eid = f"input_text.eplucon_sched_{slug}_{profile}_start_{i+1}"
            end_eid = f"input_text.eplucon_sched_{slug}_{profile}_end_{i+1}"
            temp_eid = f"input_number.eplucon_sched_{slug}_{profile}_temp_{i+1}"

            start_state = hass.states.get(start_eid)
            end_state = hass.states.get(end_eid)
            temp_state = hass.states.get(temp_eid)

            start_val = start_state.state if start_state else ""
            end_val = end_state.state if end_state else ""
            temp_val = float(temp_state.state) if temp_state and temp_state.state not in ("unknown", "unavailable") else 15.0

            # Skip empty intervals
            if not start_val or start_val in ("unknown", "unavailable", ""):
                continue

            if not end_val or end_val in ("unknown", "unavailable", ""):
                continue

            intervals.append({
                "start": start_val,
                "end": end_val,
                "temp": temp_val,
            })

        # Also save setback temperature
        setback_eid = f"input_number.eplucon_sched_{slug}_{profile}_setback"
        setback_state = hass.states.get(setback_eid)
        if setback_state and setback_state.state not in ("unknown", "unavailable"):
            setback_val = float(setback_state.state)
            prefix_map = {"weekday": "weekday_setback", "weekend": "weekend_setback"}
            await hass.services.async_call(
                DOMAIN, SERVICE_SET_SETBACK,
                {
                    ATTR_ZONE_API_ID: zone_api_id,
                    prefix_map[profile]: setback_val,
                },
                blocking=True,
            )

        # Save intervals via set_schedule service
        if intervals:
            await hass.services.async_call(
                DOMAIN, SERVICE_SET_SCHEDULE,
                {
                    ATTR_ZONE_API_ID: zone_api_id,
                    ATTR_PROFILE: profile,
                    ATTR_INTERVALS: intervals,
                    ATTR_ACTIVATE: activate,
                },
                blocking=True,
            )
        else:
            _LOGGER.info("No intervals to save for %s (%s)", zone.name, profile)

        _LOGGER.info("Saved schedule for %s (%s): %d intervals", zone.name, profile, len(intervals))

    hass.services.async_register(
        DOMAIN, SERVICE_LOAD_SCHEDULE, handle_load_schedule, schema=LOAD_SCHEDULE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SAVE_SCHEDULE, handle_save_schedule, schema=SAVE_SCHEDULE_SCHEMA,
    )

    async def handle_convert_constant(call: ServiceCall) -> None:
        """Convert all zones in constantTemp mode to timeLimit with default durations."""
        coordinator = _get_coordinator(hass)
        if coordinator.data is None:
            return

        converted = []
        for zone_api_id, zone in coordinator.data.items():
            if zone.mode != "constantTemp":
                continue
            # Skip zones at min temp (effectively "off")
            if zone.set_temperature_c <= 5.5:
                continue
            try:
                await coordinator.async_set_temperature(zone, zone.set_temperature_c)
                converted.append(zone.name)
            except Exception as err:
                _LOGGER.error("Failed to convert %s to timed: %s", zone.name, err)

        if converted:
            _LOGGER.info("Converted %d zones from Constant to Time limit: %s", len(converted), ", ".join(converted))
        else:
            _LOGGER.debug("No zones in Constant mode to convert")

    hass.services.async_register(
        DOMAIN, SERVICE_CONVERT_CONSTANT, handle_convert_constant, schema=vol.Schema({}),
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
            hass.services.async_remove(DOMAIN, SERVICE_CONVERT_CONSTANT)
            hass.services.async_remove(DOMAIN, SERVICE_LOAD_SCHEDULE)
            hass.services.async_remove(DOMAIN, SERVICE_SAVE_SCHEDULE)
    return unload_ok
