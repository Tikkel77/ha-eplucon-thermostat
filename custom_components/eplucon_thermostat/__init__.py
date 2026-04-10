"""The Eplucon Thermostat integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import EpluconClient
from .portal import EpluconPortalClient
from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_API_KEY
from .coordinator import EpluconDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR]


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

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: EpluconDataCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.portal.async_close()
    return unload_ok
