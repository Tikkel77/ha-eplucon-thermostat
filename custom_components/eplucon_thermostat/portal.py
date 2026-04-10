"""Async portal client that delegates writes to a helper script.

The helper script (/config/eplucon_write_helper.py) runs as a
subprocess and handles login + CSRF + POST in a clean context.
"""
from __future__ import annotations

import json
import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Path to the write helper script (installed in /config/)
WRITE_HELPER_PATH = "/config/eplucon_write_helper.py"


class EpluconPortalClient:
    """Portal client that runs writes via subprocess helper."""

    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
        base_url: str = "https://portaal.eplucon.nl",
        request_timeout: int = 40,
    ) -> None:
        self._hass = hass
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._timeout = request_timeout

    async def async_init(self) -> None:
        """No-op."""

    async def async_close(self) -> None:
        """No-op."""

    async def async_set_constant_temp(
        self,
        *,
        account_module_index: str,
        mode: str,
        mode_id: int,
        zone_internal_id: int,
        constant_temp_deci: int,
        active_schedule: int,
        hours: int | None = None,
        minutes: int | None = None,
    ) -> bool:
        """Set temperature by running the helper script as subprocess."""
        args = json.dumps({
            "base_url": self._base_url,
            "username": self._username,
            "password": self._password,
            "timeout": self._timeout,
            "account_module_index": account_module_index,
            "mode": mode,
            "mode_id": mode_id,
            "zone_internal_id": zone_internal_id,
            "constant_temp_deci": constant_temp_deci,
            "active_schedule": active_schedule,
            "hours": hours,
            "minutes": minutes,
        })

        _LOGGER.debug(
            "Portal write: mode=%s temp_deci=%s via %s",
            mode, constant_temp_deci, WRITE_HELPER_PATH,
        )

        result = await self._hass.async_add_executor_job(
            self._run_subprocess, args,
        )
        return result

    @staticmethod
    def _run_subprocess(args_json: str) -> bool:
        """Run the write helper in a subprocess (called from executor thread)."""
        import subprocess as sp

        proc = sp.run(
            ["/usr/local/bin/python3", WRITE_HELPER_PATH],
            input=args_json.encode(),
            capture_output=True,
            timeout=70,
            start_new_session=True,
            close_fds=True,
        )

        stdout_str = proc.stdout.decode().strip()
        stderr_str = proc.stderr.decode().strip()

        _LOGGER.debug(
            "Portal write result: rc=%d stdout=%s stderr=%s",
            proc.returncode, stdout_str[:100], stderr_str[:300],
        )

        if proc.returncode != 0:
            _LOGGER.error("Portal write failed (rc=%d): %s", proc.returncode, stderr_str)
            raise PortalWriteError(f"Portal write failed: {stderr_str}")

        return stdout_str == "OK"


class PortalAuthError(Exception):
    """Portal authentication error."""


class PortalWriteError(Exception):
    """Portal write error."""
