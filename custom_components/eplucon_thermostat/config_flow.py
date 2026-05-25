"""Config flow for Eplucon Thermostat."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a reconfiguration flow initialized by the user."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])

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
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=user_input,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=entry.data.get(CONF_USERNAME)): str,
                    vol.Required(CONF_PASSWORD, default=entry.data.get(CONF_PASSWORD)): str,
                    vol.Required(CONF_API_KEY, default=entry.data.get(CONF_API_KEY)): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return EpluconOptionsFlowHandler(config_entry)


class EpluconOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for Eplucon Thermostat."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("max_rpm", default=options.get("max_rpm", 5400)): int,
                    vol.Required("sh_a", default=options.get("sh_a", 0.03)): vol.Coerce(float),
                    vol.Required("sh_b", default=options.get("sh_b", 25.0)): vol.Coerce(float),
                    vol.Required("sh_c", default=options.get("sh_c", 0.0)): vol.Coerce(float),
                    vol.Required("ww_a", default=options.get("ww_a", 0.0)): vol.Coerce(float),
                    vol.Required("ww_b", default=options.get("ww_b", 40.0)): vol.Coerce(float),
                    vol.Required("ww_c", default=options.get("ww_c", 200.0)): vol.Coerce(float),
                }
            ),
        )
