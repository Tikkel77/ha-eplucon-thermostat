"""DataUpdateCoordinator for Eplucon Thermostat."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import EpluconClient, AuthenticationError, EpluconError, Zone
from .api.schedule import (
    compute_con_duration_minutes,
    get_next_schedule_start,
    split_minutes_to_hours_minutes,
)
from .portal import EpluconPortalClient, PortalAuthError, PortalWriteError
from .const import (
    DOMAIN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_OVERRIDE_MINUTES,
    ZONE_DEFAULT_DURATIONS,
    ZONE_SLUGS,
    CON_MAX_MINUTES,
    DEBOUNCE_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class EpluconDataCoordinator(DataUpdateCoordinator[dict[int, Zone]]):
    """Coordinator that polls Eplucon zone data and handles writes."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EpluconClient,
        portal: EpluconPortalClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.portal = portal
        # Debounce state: per-zone pending temperature and timer handle
        self._debounce_timers: dict[int, asyncio.TimerHandle] = {}
        self._pending_temps: dict[int, float] = {}
        # Zones currently being written to the portal
        self._writing_zones: set[int] = set()

    async def _async_update_data(self) -> dict[int, Zone]:
        """Fetch zone data from Eplucon REST API (Bearer token, read-only)."""
        try:
            zones = await self.hass.async_add_executor_job(self.client.get_zones)
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except EpluconError as err:
            raise UpdateFailed(f"Error communicating with Eplucon: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

        return {z.zone_api_id: z for z in zones}

    def get_zone(self, zone_api_id: int) -> Zone | None:
        """Get a zone from the latest data."""
        if self.data is None:
            return None
        return self.data.get(zone_api_id)

    def _get_default_minutes(self, zone: Zone) -> int:
        """Get per-zone default override duration in minutes.

        First checks for an input_number helper entity
        (input_number.eplucon_duration_{slug}). If it exists and has a
        valid numeric state, use that. Otherwise fall back to the
        hardcoded ZONE_DEFAULT_DURATIONS map.
        """
        slug = ZONE_SLUGS.get(zone.zone_api_id)
        if slug:
            entity_id = f"input_number.eplucon_duration_{slug}"
            state = self.hass.states.get(entity_id)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    return int(float(state.state))
                except (ValueError, TypeError):
                    pass
        return ZONE_DEFAULT_DURATIONS.get(zone.zone_api_id, DEFAULT_OVERRIDE_MINUTES)

    def _cap_minutes_at_schedule(self, zone: Zone, requested_minutes: int) -> int:
        """Cap duration so it doesn't exceed the next schedule setpoint.

        If no schedule info is available, return requested_minutes unchanged.
        """
        next_start = get_next_schedule_start(zone)
        if next_start is None:
            return requested_minutes

        from datetime import datetime
        now = datetime.now().astimezone()
        remaining = (next_start - now).total_seconds()
        if remaining <= 0:
            return requested_minutes

        import math
        schedule_minutes = int(math.ceil(remaining / 60.0))
        return min(requested_minutes, schedule_minutes)

    async def async_set_temperature(self, zone: Zone, temperature: float) -> None:
        """Set temperature with debounce.

        Each call resets a per-zone timer. The actual portal write fires
        only after DEBOUNCE_SECONDS of inactivity on that zone.
        The pending temperature is tracked for optimistic UI display.
        """
        zone_id = zone.zone_api_id
        self._pending_temps[zone_id] = temperature

        # Cancel any existing debounce timer for this zone
        existing = self._debounce_timers.pop(zone_id, None)
        if existing is not None:
            existing.cancel()

        # Notify listeners immediately so climate entity shows optimistic temp
        self.async_update_listeners()

        # Schedule the actual write after debounce period
        loop = self.hass.loop

        @callback
        def _fire_write() -> None:
            self._debounce_timers.pop(zone_id, None)
            self.hass.async_create_task(self._execute_set_temperature(zone, temperature))

        handle = loop.call_later(DEBOUNCE_SECONDS, _fire_write)
        self._debounce_timers[zone_id] = handle

    async def _execute_set_temperature(self, zone: Zone, temperature: float) -> None:
        """Actually send the temperature write to the portal (post-debounce)."""
        zone_id = zone.zone_api_id
        # Use the latest pending temp (user may have clicked multiple times)
        final_temp = self._pending_temps.get(zone_id, temperature)

        default_mins = self._get_default_minutes(zone)
        total_minutes = self._cap_minutes_at_schedule(zone, default_mins)
        hours, minutes = split_minutes_to_hours_minutes(total_minutes)

        target_deci = int(round(final_temp * 10))
        _LOGGER.info(
            "Debounce complete: setting %s to %.1f°C for %dh%02dm",
            zone.name, final_temp, hours, minutes,
        )

        self._writing_zones.add(zone_id)
        self.async_update_listeners()

        try:
            await self.portal.async_set_constant_temp(
                account_module_index=zone.account_module_index,
                mode="timeLimit",
                mode_id=zone.mode_id,
                zone_internal_id=zone.zone_internal_id,
                constant_temp_deci=target_deci,
                active_schedule=zone.schedule_index,
                hours=hours,
                minutes=minutes,
            )
        except (PortalAuthError, PortalWriteError) as err:
            _LOGGER.error("Failed to set temperature: %s", err)
            # Clear pending on failure so UI reverts
            self._pending_temps.pop(zone_id, None)
            self._writing_zones.discard(zone_id)
            self.async_update_listeners()
            raise
        finally:
            self._writing_zones.discard(zone_id)

        # Keep pending temp visible until portal confirms (poll loop clears it)
        self.async_update_listeners()
        self._schedule_delayed_refresh(zone_id, expected_temp=final_temp)

    def get_pending_temp(self, zone_api_id: int) -> float | None:
        """Return the pending (optimistic) temperature for a zone, or None."""
        return self._pending_temps.get(zone_api_id)

    def is_writing(self, zone_api_id: int) -> bool:
        """Return True if a portal write is in progress for this zone."""
        return zone_api_id in self._writing_zones

    async def async_set_constant_temperature(self, zone: Zone, temperature: float) -> None:
        """Set constant temperature (no time limit) for a zone."""
        target_deci = int(round(temperature * 10))
        try:
            await self.portal.async_set_constant_temp(
                account_module_index=zone.account_module_index,
                mode="constantTemp",
                mode_id=zone.mode_id,
                zone_internal_id=zone.zone_internal_id,
                constant_temp_deci=target_deci,
                active_schedule=zone.schedule_index,
            )
        except (PortalAuthError, PortalWriteError) as err:
            _LOGGER.error("Failed to set constant temperature: %s", err)
            raise
        self._schedule_delayed_refresh(zone.zone_api_id, expected_temp=temperature)

    async def async_set_time_limit_temperature(
        self, zone: Zone, temperature: float, total_minutes: int
    ) -> None:
        """Set temperature with explicit time limit via the portal."""
        target_deci = int(round(temperature * 10))
        hours, minutes = split_minutes_to_hours_minutes(total_minutes)
        try:
            await self.portal.async_set_constant_temp(
                account_module_index=zone.account_module_index,
                mode="timeLimit",
                mode_id=zone.mode_id,
                zone_internal_id=zone.zone_internal_id,
                constant_temp_deci=target_deci,
                active_schedule=zone.schedule_index,
                hours=hours,
                minutes=minutes,
            )
        except (PortalAuthError, PortalWriteError) as err:
            _LOGGER.error("Failed to set time limit temperature: %s", err)
            raise
        self._schedule_delayed_refresh(zone.zone_api_id, expected_temp=temperature)

    async def async_set_con_temperature(self, zone: Zone, temperature: float) -> None:
        """Set temperature until next schedule start (Con mode), max 8h.

        Computes duration from the zone's schedule data. If no schedule info,
        falls back to the zone's default duration.
        """
        default_mins = self._get_default_minutes(zone)
        total_minutes = compute_con_duration_minutes(
            zone,
            default_minutes=default_mins,
            max_minutes=CON_MAX_MINUTES,
        )
        hours, minutes = split_minutes_to_hours_minutes(total_minutes)

        target_deci = int(round(temperature * 10))
        _LOGGER.info(
            "Con mode: %s to %.1f°C for %dh%02dm (until next schedule, max %dh)",
            zone.name, temperature, hours, minutes, CON_MAX_MINUTES // 60,
        )
        try:
            await self.portal.async_set_constant_temp(
                account_module_index=zone.account_module_index,
                mode="timeLimit",
                mode_id=zone.mode_id,
                zone_internal_id=zone.zone_internal_id,
                constant_temp_deci=target_deci,
                active_schedule=zone.schedule_index,
                hours=hours,
                minutes=minutes,
            )
        except (PortalAuthError, PortalWriteError) as err:
            _LOGGER.error("Failed to set con temperature: %s", err)
            raise
        self._schedule_delayed_refresh(zone.zone_api_id, expected_temp=temperature)

    async def async_activate_program(self, zone: Zone, program_index: int) -> None:
        """Activate a schedule program via the portal."""
        if program_index >= 0:
            target_deci = zone.set_temperature_deci
            try:
                await self.portal.async_set_constant_temp(
                    account_module_index=zone.account_module_index,
                    mode="globalSchedule",
                    mode_id=zone.mode_id,
                    zone_internal_id=zone.zone_internal_id,
                    constant_temp_deci=target_deci,
                    active_schedule=program_index,
                )
            except (PortalAuthError, PortalWriteError) as err:
                _LOGGER.error("Failed to activate program: %s", err)
                raise
        else:
            try:
                await self.hass.async_add_executor_job(
                    lambda: self.client.activate_program(zone, program_index, wait=False),
                )
            except Exception as err:
                _LOGGER.error("Failed to activate local program: %s", err)
                raise
        self._schedule_delayed_refresh(zone.zone_api_id)

    def _schedule_delayed_refresh(self, zone_api_id: int, expected_temp: float | None = None) -> None:
        """Poll until the portal confirms the temperature change.

        Checks every 15 seconds for up to 3 minutes. Stops early if
        the zone's set temperature matches the expected value.
        Clears the pending temp once confirmed.
        """

        async def _poll_until_confirmed() -> None:
            max_attempts = 12  # 12 × 15s = 3 minutes
            for attempt in range(max_attempts):
                await asyncio.sleep(15)
                try:
                    await self.async_request_refresh()
                except Exception:
                    continue
                # Check if change propagated
                if expected_temp is not None:
                    zone = self.get_zone(zone_api_id)
                    if zone and abs(zone.set_temperature_c - expected_temp) < 0.05:
                        _LOGGER.debug(
                            "Zone %s confirmed at %.1f°C after %ds",
                            zone_api_id, expected_temp, (attempt + 1) * 15,
                        )
                        self._pending_temps.pop(zone_api_id, None)
                        self.async_update_listeners()
                        return
            # Timed out — clear pending anyway to stop flashing
            self._pending_temps.pop(zone_api_id, None)
            self.async_update_listeners()
            _LOGGER.debug("Zone %s: gave up waiting for confirmation after 3min", zone_api_id)

        self.hass.async_create_task(_poll_until_confirmed())
