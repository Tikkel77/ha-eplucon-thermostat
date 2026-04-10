"""DataUpdateCoordinator for Eplucon Thermostat."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
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
    CON_MAX_MINUTES,
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
        """Get per-zone default override duration in minutes."""
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
        """Set temperature with time-limited override (default duration, capped at schedule)."""
        default_mins = self._get_default_minutes(zone)
        total_minutes = self._cap_minutes_at_schedule(zone, default_mins)
        hours, minutes = split_minutes_to_hours_minutes(total_minutes)

        target_deci = int(round(temperature * 10))
        _LOGGER.info(
            "Setting %s to %.1f°C for %dh%02dm (default=%dm, capped at schedule)",
            zone.name, temperature, hours, minutes, default_mins,
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
            _LOGGER.error("Failed to set temperature: %s", err)
            raise
        self._schedule_delayed_refresh(zone.zone_api_id)

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
        self._schedule_delayed_refresh(zone.zone_api_id)

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
        self._schedule_delayed_refresh(zone.zone_api_id)

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
        self._schedule_delayed_refresh(zone.zone_api_id)

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

    def _schedule_delayed_refresh(self, zone_api_id: int) -> None:
        """Schedule a data refresh after the portal has processed the write.

        The Eplucon portal takes 60-90 seconds to propagate changes,
        so we wait 90 seconds before refreshing.
        """

        async def _delayed_refresh() -> None:
            await asyncio.sleep(90)
            await self.async_request_refresh()

        self.hass.async_create_task(_delayed_refresh())
