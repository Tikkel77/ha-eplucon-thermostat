"""DataUpdateCoordinator for Eplucon Thermostat."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed

from .api import EpluconClient, AuthenticationError, EpluconError, Zone
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class EpluconDataCoordinator(DataUpdateCoordinator[dict[int, Zone]]):
    """Coordinator that polls Eplucon zone data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EpluconClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client

    async def _async_update_data(self) -> dict[int, Zone]:
        """Fetch zone data from Eplucon portal."""
        try:
            zones = await self.hass.async_add_executor_job(self.client.get_zones)
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except EpluconError as err:
            raise UpdateFailed(f"Error communicating with Eplucon: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

        # Return as dict keyed by zone_api_id for easy lookup
        return {z.zone_api_id: z for z in zones}

    def get_zone(self, zone_api_id: int) -> Zone | None:
        """Get a zone from the latest data."""
        if self.data is None:
            return None
        return self.data.get(zone_api_id)

    async def async_set_temperature(self, zone: Zone, temperature: float) -> None:
        """Set constant temperature for a zone."""
        await self.hass.async_add_executor_job(
            self.client.set_constant_temperature, zone, temperature,
        )
        # Wait for the portal to process
        await self.hass.async_add_executor_job(
            self.client.wait_for_zone_idle, zone.zone_api_id,
        )
        await self.async_request_refresh()

    async def async_set_time_limit_temperature(
        self, zone: Zone, temperature: float, total_minutes: int
    ) -> None:
        """Set temperature with time limit."""
        await self.hass.async_add_executor_job(
            self.client.set_temperature_for_minutes, zone, temperature, total_minutes,
        )
        await self.hass.async_add_executor_job(
            self.client.wait_for_zone_idle, zone.zone_api_id,
        )
        await self.async_request_refresh()

    async def async_activate_program(self, zone: Zone, program_index: int) -> None:
        """Activate a schedule program for a zone."""
        await self.hass.async_add_executor_job(
            self.client.activate_program, zone, program_index,
        )
        await self.hass.async_add_executor_job(
            self.client.wait_for_zone_idle, zone.zone_api_id,
        )
        await self.async_request_refresh()
