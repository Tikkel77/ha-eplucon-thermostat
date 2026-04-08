"""Config flow for Eplucon Thermostat."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .api import EpluconClient, AuthenticationError, EpluconError
from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_API_KEY

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_API_KEY): str,
    }
)


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate the user input and return info for the config entry."""
    client = EpluconClient(
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        api_key=data[CONF_API_KEY],
    )

    # Test API key (Bearer token) by fetching modules
    modules = await hass.async_add_executor_job(client.get_modules)
    if not modules:
        raise ValueError("No modules found")

    # Test portal login
    await hass.async_add_executor_job(client._ensure_portal_login)

    zone_modules = [m for m in modules if m.type == "zones_system_controller"]
    if not zone_modules:
        raise ValueError("No zone controller modules found")

    # Use first zone controller as unique identifier
    module = zone_modules[0]
    return {
        "title": module.name,
        "unique_id": module.account_module_index,
    }


class EpluconThermostatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eplucon Thermostat."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_input(self.hass, user_input)
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except EpluconError:
                errors["base"] = "cannot_connect"
            except ValueError as err:
                errors["base"] = "no_modules"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["title"],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
