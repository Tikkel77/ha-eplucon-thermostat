from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Callable, Iterable, Optional

import requests

from .errors import ApiError, AuthenticationError, NotFoundError, WriteError
from .models import FormInput, Module, ProgramForm, Zone
from .schedule import (
    compute_con_duration_minutes_from_next_start,
    get_next_schedule_start,
    get_next_schedule_start_from_program_form,
    split_minutes_to_hours_minutes,
)


class _ProgramFormsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._current: Optional[dict[str, Any]] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attrs_dict = {k: (v or "") for k, v in attrs}
        if tag == "form":
            action = attrs_dict.get("data-action", "")
            if "/scheduler/store" in action:
                self._current = {"attrs": attrs_dict, "inputs": []}
        elif tag == "input" and self._current is not None:
            self._current["inputs"].append(attrs_dict)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


class EpluconClient:
    def __init__(
        self,
        username: str,
        password: str,
        api_key: str,
        base_url: str = "https://portaal.eplucon.nl",
        request_timeout: int = 40,
        settle_seconds: int = 15,
        poll_interval_seconds: int = 5,
        verify_tls: bool = True,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.username = username
        self.password = password
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/v2"
        self.request_timeout = request_timeout
        self.settle_seconds = settle_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.verify_tls = verify_tls

        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            }
        )
        # Separate session for portal writes (login + CSRF + POST)
        # to avoid conflicts with Bearer API reads on _session
        self._portal_session = requests.Session()
        self._portal_session.headers.update(self._session.headers)
        self._portal_logged_in = False
        self._csrf_token_by_ami: dict[str, str] = {}

    # -------------------------------
    # Public read methods
    # -------------------------------

    def get_modules(self) -> list[Module]:
        data = self._api_get_json("/econtrol/modules")
        modules: list[Module] = []
        for row in data:
            modules.append(
                Module(
                    id=int(row["id"]),
                    account_module_index=str(row["account_module_index"]),
                    name=str(row["name"]),
                    type=str(row["type"]),
                )
            )
        return modules

    def get_zone_modules(self) -> list[Module]:
        return [m for m in self.get_modules() if m.type == "zones_system_controller"]

    def get_zones(self, module_id: Optional[int] = None) -> list[Zone]:
        modules = self.get_zone_modules()
        if module_id is not None:
            modules = [m for m in modules if m.id == module_id]
            if not modules:
                raise NotFoundError(f"Geen zones_system_controller module gevonden voor module_id={module_id}")

        zones: list[Zone] = []
        for module in modules:
            data = self._api_get_json(f"/econtrol/modules/{module.id}/zones")
            for row in data:
                zones.append(self._zone_from_row(module, row))

        zones.sort(key=lambda z: (z.module_name.lower(), z.name.lower(), z.zone_api_id))
        return zones

    def get_zone(
        self,
        *,
        zone_api_id: Optional[int] = None,
        zone_internal_id: Optional[int] = None,
        name: Optional[str] = None,
        module_id: Optional[int] = None,
    ) -> Zone:
        matches = self.find_zones(
            zone_api_id=zone_api_id,
            zone_internal_id=zone_internal_id,
            name=name,
            module_id=module_id,
        )
        if not matches:
            raise NotFoundError("Zone niet gevonden")
        if len(matches) > 1:
            names = ", ".join(f"{z.name}(api:{z.zone_api_id},module:{z.module_id})" for z in matches)
            raise NotFoundError(
                "Meerdere zones matchen; gebruik zone_api_id of module_id om te disambigueren: " + names
            )
        return matches[0]

    def find_zones(
        self,
        *,
        zone_api_id: Optional[int] = None,
        zone_internal_id: Optional[int] = None,
        name: Optional[str] = None,
        module_id: Optional[int] = None,
    ) -> list[Zone]:
        zones = self.get_zones(module_id=module_id)

        def _match(z: Zone) -> bool:
            if zone_api_id is not None and z.zone_api_id != zone_api_id:
                return False
            if zone_internal_id is not None and z.zone_internal_id != zone_internal_id:
                return False
            if name is not None and z.name.lower() != name.lower():
                return False
            return True

        return [z for z in zones if _match(z)]

    # -------------------------------
    # Public write methods
    # -------------------------------

    def set_constant_temperature(
        self,
        zone: Zone,
        temperature_c: float,
        *,
        wait: bool = True,
        wait_timeout_seconds: int = 240,
    ) -> Zone:
        target_deci = int(round(temperature_c * 10))
        self._write_set_constant_temp(
            zone=zone,
            mode="constantTemp",
            constant_temp_deci=target_deci,
            active_schedule=zone.schedule_index,
            hours=None,
            minutes=None,
        )
        if not wait:
            return self.get_zone(zone_api_id=zone.zone_api_id, module_id=zone.module_id)

        time.sleep(self.settle_seconds)
        return self._wait_for_zone(
            zone,
            lambda z: z.mode == "constantTemp" and z.set_temperature_deci == target_deci,
            timeout_seconds=wait_timeout_seconds,
            error_message="Constante temperatuur wijziging niet bevestigd binnen timeout",
        )

    def set_time_limit_temperature(
        self,
        zone: Zone,
        temperature_c: float,
        *,
        hours: int,
        minutes: int,
        wait: bool = True,
        wait_timeout_seconds: int = 240,
    ) -> Zone:
        if hours < 0:
            raise ValueError("hours moet >= 0 zijn")
        if minutes < 0 or minutes > 59:
            raise ValueError("minutes moet tussen 0 en 59 liggen")

        target_deci = int(round(temperature_c * 10))
        self._write_set_constant_temp(
            zone=zone,
            mode="timeLimit",
            constant_temp_deci=target_deci,
            active_schedule=zone.schedule_index,
            hours=hours,
            minutes=minutes,
        )
        if not wait:
            return self.get_zone(zone_api_id=zone.zone_api_id, module_id=zone.module_id)

        time.sleep(self.settle_seconds)
        return self._wait_for_zone(
            zone,
            lambda z: z.mode == "timeLimit" and z.set_temperature_deci == target_deci,
            timeout_seconds=wait_timeout_seconds,
            error_message="TimeLimit wijziging niet bevestigd binnen timeout",
        )

    def set_temperature_for_minutes(
        self,
        zone: Zone,
        temperature_c: float,
        total_minutes: int,
        *,
        wait: bool = True,
        wait_timeout_seconds: int = 240,
    ) -> Zone:
        total_minutes = max(1, int(total_minutes))
        hours, minutes = split_minutes_to_hours_minutes(total_minutes)
        return self.set_time_limit_temperature(
            zone,
            temperature_c,
            hours=hours,
            minutes=minutes,
            wait=wait,
            wait_timeout_seconds=wait_timeout_seconds,
        )

    def set_temperature_with_default_duration(
        self,
        zone: Zone,
        temperature_c: float,
        *,
        hours: Optional[int] = None,
        minutes: Optional[int] = None,
        default_minutes: int = 240,
        wait: bool = True,
        wait_timeout_seconds: int = 240,
    ) -> tuple[Zone, int]:
        if hours is None and minutes is None:
            total_minutes = int(default_minutes)
        else:
            total_minutes = int((hours or 0) * 60 + (minutes or 0))
            if total_minutes <= 0:
                total_minutes = int(default_minutes)

        updated = self.set_temperature_for_minutes(
            zone,
            temperature_c,
            total_minutes,
            wait=wait,
            wait_timeout_seconds=wait_timeout_seconds,
        )
        return updated, total_minutes

    def plan_con_duration_minutes(
        self,
        zone: Zone,
        *,
        default_minutes: int = 240,
        max_minutes: int = 480,
    ) -> int:
        next_start = self.get_next_schedule_start_for_zone(zone)
        return compute_con_duration_minutes_from_next_start(
            next_start,
            default_minutes=default_minutes,
            max_minutes=max_minutes,
        )

    def get_next_schedule_start_for_zone(self, zone: Zone):
        # Bij schedule_index >= 0 proberen we de actieve program-form,
        # omdat zone.raw_data.schedule vaak lokaal profiel toont.
        if zone.schedule_index >= 0:
            try:
                form = self._get_program_form(zone, zone.schedule_index)
                dt = get_next_schedule_start_from_program_form(form)
                if dt is not None:
                    return dt
            except Exception:
                pass

        return get_next_schedule_start(zone)

    def set_temperature_con_until_next_schedule(
        self,
        zone: Zone,
        temperature_c: float,
        *,
        default_minutes: int = 240,
        max_minutes: int = 480,
        wait: bool = True,
        wait_timeout_seconds: int = 240,
    ) -> tuple[Zone, int]:
        minutes = self.plan_con_duration_minutes(
            zone,
            default_minutes=default_minutes,
            max_minutes=max_minutes,
        )
        updated = self.set_temperature_for_minutes(
            zone,
            temperature_c,
            minutes,
            wait=wait,
            wait_timeout_seconds=wait_timeout_seconds,
        )
        return updated, minutes

    def get_program_forms(self, zone: Zone) -> list[ProgramForm]:
        self._ensure_portal_login()
        url = f"{self.base_url}/e-control/zones/{zone.zone_api_id}/ajax/programs"
        response = self._portal_session.get(
            url,
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=self.request_timeout,
            verify=self.verify_tls,
        )
        response.raise_for_status()
        obj = response.json()
        html = obj.get("html", "")
        if not html:
            raise ApiError("ajax/programs response bevat geen html")

        parser = _ProgramFormsParser()
        parser.feed(html)

        forms: list[ProgramForm] = []
        for f in parser.forms:
            attrs = f["attrs"]
            action_url = attrs.get("data-action", "").replace("\\/", "/")
            inputs = [
                FormInput(
                    name=str(i.get("name", "")),
                    value=str(i.get("value", "")),
                    input_type=str(i.get("type", "")),
                    checked=("checked" in i),
                )
                for i in f["inputs"]
                if i.get("name")
            ]

            as_dict: dict[str, list[str]] = {}
            for item in inputs:
                as_dict.setdefault(item.name, []).append(item.value)

            index = int(as_dict.get("index", ["-999"])[0])
            schedule_id = int(as_dict.get("schedule_id", ["0"])[0])
            mode_id = int(as_dict.get("mode_id", ["0"])[0])
            zone_id = int(as_dict.get("zone_id", ["0"])[0])
            schedule_name = as_dict.get("scheduleName", [""])[0]

            forms.append(
                ProgramForm(
                    index=index,
                    schedule_id=schedule_id,
                    mode_id=mode_id,
                    zone_id=zone_id,
                    action_url=action_url,
                    schedule_name=schedule_name,
                    inputs=inputs,
                )
            )

        forms.sort(key=lambda p: p.index)
        return forms

    def activate_program(
        self,
        zone: Zone,
        program_index: int,
        *,
        wait: bool = True,
        wait_timeout_seconds: int = 300,
    ) -> Zone:
        form = self._get_program_form(zone, program_index)
        self._submit_program_form(zone, form, overrides={})

        # In de praktijk activeert scheduler/store niet altijd direct de runtime mode.
        # Forceren naar globalSchedule maakt de activatie robuuster voor index >= 0.
        if program_index >= 0:
            fresh = self.get_zone(zone_api_id=zone.zone_api_id, module_id=zone.module_id)
            self._write_set_constant_temp(
                zone=fresh,
                mode="globalSchedule",
                constant_temp_deci=fresh.set_temperature_deci,
                active_schedule=program_index,
                hours=None,
                minutes=None,
            )

        if not wait:
            return self.get_zone(zone_api_id=zone.zone_api_id, module_id=zone.module_id)

        time.sleep(self.settle_seconds)
        expected_mode = "globalSchedule" if program_index >= 0 else None
        return self._wait_for_zone(
            zone,
            lambda z: (
                (expected_mode is None or z.mode == expected_mode)
                and (program_index < 0 or z.schedule_index == program_index)
            ),
            timeout_seconds=wait_timeout_seconds,
            error_message="Programma activatie niet bevestigd binnen timeout",
        )

    def set_program_setback(
        self,
        zone: Zone,
        program_index: int,
        *,
        p0_setback_c: Optional[float] = None,
        p1_setback_c: Optional[float] = None,
        wait: bool = True,
        wait_timeout_seconds: int = 240,
    ) -> Zone:
        if p0_setback_c is None and p1_setback_c is None:
            raise ValueError("Geef minimaal p0_setback_c of p1_setback_c op")

        form = self._get_program_form(zone, program_index)
        overrides: dict[str, str | list[str]] = {}
        if p0_setback_c is not None:
            overrides["p0SetbackTemp"] = str(int(round(p0_setback_c)))
        if p1_setback_c is not None:
            overrides["p1SetbackTemp"] = str(int(round(p1_setback_c)))

        self._submit_program_form(zone, form, overrides=overrides)
        if not wait:
            return self.get_zone(zone_api_id=zone.zone_api_id, module_id=zone.module_id)

        time.sleep(self.settle_seconds)
        return self._wait_for_zone(
            zone,
            lambda _z: True,
            timeout_seconds=wait_timeout_seconds,
            error_message="Schema update niet bevestigd binnen timeout",
        )

    def submit_program_form(
        self,
        zone: Zone,
        program_index: int,
        *,
        overrides: dict[str, str | list[str]],
        wait: bool = False,
        wait_timeout_seconds: int = 240,
    ) -> Zone:
        form = self._get_program_form(zone, program_index)
        self._submit_program_form(zone, form, overrides=overrides)
        if not wait:
            return self.get_zone(zone_api_id=zone.zone_api_id, module_id=zone.module_id)

        time.sleep(self.settle_seconds)
        return self._wait_for_zone(
            zone,
            lambda _z: True,
            timeout_seconds=wait_timeout_seconds,
            error_message="Custom program submit niet bevestigd binnen timeout",
        )

    # -------------------------------
    # Public zone update status methods
    # (used by webapp and HA integration)
    # -------------------------------

    def get_zones_update_status(self) -> dict[int, bool]:
        """Check which zones are currently being updated by the portal.

        Returns dict of {zone_api_id: is_updating}.
        Uses the internal AJAX refresh endpoint that the portal itself polls.
        """
        self._ensure_portal_login()
        # We need at least one account_module_index; get from modules
        modules = self.get_zone_modules()
        result: dict[int, bool] = {}

        for module in modules:
            try:
                url = f"{self.base_url}/e-control/zones/ajax/refresh?account_module_index={module.account_module_index}"
                response = self._portal_session.get(
                    url,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=self.request_timeout,
                    verify=self.verify_tls,
                )
                response.raise_for_status()
                html = response.json().get("html", "")
                # Parse: find data-zoneid and check if during-change has d-none or not
                import re as _re
                for m in _re.finditer(r'data-zoneid="(\d+)"', html):
                    zid = int(m.group(1))
                    # Find the during-change div after this zoneid
                    after = html[m.end():m.end() + 500]
                    dc_match = _re.search(r'during-change\s+([^"]*)"', after)
                    if dc_match:
                        classes = dc_match.group(1)
                        result[zid] = "d-none" not in classes
                    else:
                        result[zid] = False
            except Exception:
                pass

        return result

    def is_zone_updating(self, zone_api_id: int) -> bool:
        """Check if a specific zone is currently being updated."""
        statuses = self.get_zones_update_status()
        return statuses.get(zone_api_id, False)

    def wait_for_zone_idle(
        self,
        zone_api_id: int,
        *,
        timeout_seconds: int = 120,
        poll_interval_seconds: Optional[int] = None,
        initial_wait_seconds: int = 3,
    ) -> bool:
        """Wait until a zone is no longer being updated.

        Returns True if zone became idle, False if timeout.
        Useful after a write to wait for the portal to finish processing.

        The initial_wait_seconds ensures we don't poll before the portal
        has registered the update (there's a delay between our POST and
        the duringChange flag becoming visible).
        """
        interval = poll_interval_seconds or self.poll_interval_seconds

        # Wait for the portal to register the change
        time.sleep(initial_wait_seconds)

        # First, wait until zone IS updating (or timeout quickly)
        saw_updating = False
        detect_deadline = time.time() + 15  # max 15s to detect the update
        while time.time() < detect_deadline:
            try:
                if self.is_zone_updating(zone_api_id):
                    saw_updating = True
                    break
            except Exception:
                pass
            time.sleep(interval)

        if not saw_updating:
            # The update may already be done, or was never detected
            return True

        # Now wait until zone becomes idle again
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                if not self.is_zone_updating(zone_api_id):
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    # -------------------------------
    # Internal helpers
    # -------------------------------

    def _api_get_json(self, path: str) -> list[dict[str, Any]]:
        response = self._session.get(
            f"{self.api_base}{path}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            timeout=self.request_timeout,
            verify=self.verify_tls,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("auth") is False:
            raise AuthenticationError(f"Bearer auth mislukt voor {path}: {payload}")
        data = payload.get("data")
        if not isinstance(data, list):
            raise ApiError(f"Onverwachte JSON structuur voor {path}: {payload}")
        return data

    def _zone_from_row(self, module: Module, row: dict[str, Any]) -> Zone:
        raw_data = row.get("raw_data")
        if isinstance(raw_data, str):
            raw = json.loads(raw_data)
        elif isinstance(raw_data, dict):
            raw = raw_data
        else:
            raise ApiError(f"Onverwacht raw_data type in zone row: {type(raw_data)}")

        mode_obj = raw.get("mode", {})
        schedule_obj = raw.get("schedule", {})
        zone_obj = raw.get("zone", {})

        current_temp = row.get("current_temperature")
        current_temp_c = float(current_temp) if current_temp is not None else None

        return Zone(
            module_id=module.id,
            module_name=module.name,
            account_module_index=module.account_module_index,
            zone_api_id=int(row["id"]),
            zone_internal_id=int(zone_obj["id"]),
            mode_id=int(mode_obj["id"]),
            schedule_id=int(schedule_obj.get("id", 0)),
            name=str(row["name"]),
            mode=str(mode_obj.get("mode", row.get("mode", ""))),
            set_temperature_c=float(row["set_temperature"]),
            current_temperature_c=current_temp_c,
            schedule_index=int(mode_obj.get("scheduleIndex", -1)),
            const_temp_time=int(mode_obj.get("constTempTime", 0)),
            raw_data=raw,
        )

    def _ensure_portal_login(self) -> None:
        if self._portal_logged_in:
            return

        _logger = logging.getLogger(__name__)

        page = self._portal_session.get(
            f"{self.base_url}/login",
            timeout=self.request_timeout,
            verify=self.verify_tls,
        )
        page.raise_for_status()
        html = page.text

        token = self._extract_input_value(html, "_token")
        valid_from = self._extract_input_value(html, "valid_from")
        if not token:
            raise AuthenticationError("Login _token niet gevonden")

        payload = {
            "_token": token,
            "username": self.username,
            "password": self.password,
            "remember": "1",
            "valid_from": valid_from,
        }

        honeypot_name = self._extract_honeypot_name(html)
        if honeypot_name:
            payload[honeypot_name] = ""

        login_resp = self._portal_session.post(
            f"{self.base_url}/login",
            data=payload,
            headers={"Origin": self.base_url, "Referer": f"{self.base_url}/login"},
            allow_redirects=False,
            timeout=self.request_timeout,
            verify=self.verify_tls,
        )

        # Check redirect target to determine login success
        if login_resp.status_code in (301, 302, 303):
            location = login_resp.headers.get("Location", "")
            _logger.debug("Portal login redirect: %s", location)
            if "/login" in location:
                raise AuthenticationError(
                    f"Portal login failed: redirected back to {location}"
                )
            # Follow the redirect manually
            self._portal_session.get(
                location,
                timeout=self.request_timeout,
                verify=self.verify_tls,
            )
        else:
            login_resp.raise_for_status()

        # Verify we have a remember cookie (set only on successful login)
        remember_cookies = [k for k in self._portal_session.cookies.keys() if k.startswith("remember_web")]
        if not remember_cookies:
            cookie_names = list(self._portal_session.cookies.keys())
            _logger.warning("Portal login: no remember_web cookie. Cookies: %s", cookie_names)
            raise AuthenticationError(
                f"Portal login lijkt mislukt: geen remember_web cookie. "
                f"Cookies: {cookie_names}"
            )

        self._portal_logged_in = True
        _logger.info("Portal login successful")

    def _get_csrf_for_account_module(self, account_module_index: str, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = self._csrf_token_by_ami.get(account_module_index)
            if cached:
                return cached

        self._ensure_portal_login()
        page_url = f"{self.base_url}/e-control/zones?account_module_index={account_module_index}"
        response = self._portal_session.get(
            page_url,
            timeout=self.request_timeout,
            verify=self.verify_tls,
        )
        response.raise_for_status()
        m = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', response.text)
        if not m:
            raise AuthenticationError("CSRF token niet gevonden op zones pagina")

        token = m.group(1)
        self._csrf_token_by_ami[account_module_index] = token
        return token

    def _write_set_constant_temp(
        self,
        *,
        zone: Zone,
        mode: str,
        constant_temp_deci: int,
        active_schedule: int,
        hours: Optional[int],
        minutes: Optional[int],
    ) -> None:
        _logger = logging.getLogger(__name__)
        url = f"{self.base_url}/e-control/set_constant_temp?account_module_index={zone.account_module_index}"

        response = None
        for attempt in range(2):
            # Always invalidate + re-login to ensure fresh session
            self._invalidate_portal_auth()
            _logger.debug("Write attempt %d: invalidated auth, getting fresh CSRF", attempt)
            csrf = self._get_csrf_for_account_module(zone.account_module_index, force_refresh=True)
            _logger.debug("Write attempt %d: got CSRF %s..., logged_in=%s, cookies=%s",
                          attempt, csrf[:12], self._portal_logged_in,
                          list(self._portal_session.cookies.keys()))
            payload: dict[str, str] = {
                "_token": csrf,
                "mode": mode,
                "mode_id": str(zone.mode_id),
                "zone_id": str(zone.zone_internal_id),
                "parent_id": str(zone.zone_internal_id),
                "constant_temp": str(constant_temp_deci),
                "active_schedule": str(active_schedule),
            }
            if hours is not None:
                payload["hours"] = str(hours)
            if minutes is not None:
                payload["minutes"] = str(minutes)

            response = self._portal_session.post(
                url,
                data=payload,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "X-CSRF-TOKEN": csrf,
                    "Origin": self.base_url,
                    "Referer": f"{self.base_url}/e-control/zones?account_module_index={zone.account_module_index}",
                },
                timeout=self.request_timeout,
                verify=self.verify_tls,
            )

            _logger.debug("Write attempt %d: response status=%d, body=%s",
                          attempt, response.status_code, response.text[:200])

            if self._needs_portal_refresh(response) and attempt == 0:
                _logger.warning("Write attempt %d: needs portal refresh (status=%d), retrying",
                               attempt, response.status_code)
                self._invalidate_portal_auth()
                continue
            break

        if response is None:
            raise WriteError("Geen response ontvangen van set_constant_temp")
        response.raise_for_status()

        body = response.text.strip()
        if body not in ("1", ""):
            # sommige endpoints reageren met JSON, maar set_constant_temp hoort meestal "1" te geven
            try:
                parsed = response.json()
            except Exception:
                parsed = None
            if parsed is None:
                raise WriteError(f"Onverwachte set_constant_temp response: {body[:300]}")

    def _wait_for_zone(
        self,
        zone: Zone,
        predicate: Callable[[Zone], bool],
        *,
        timeout_seconds: int,
        error_message: str,
    ) -> Zone:
        deadline = time.time() + timeout_seconds
        last: Optional[Zone] = None
        while time.time() < deadline:
            last = self.get_zone(zone_api_id=zone.zone_api_id, module_id=zone.module_id)
            if predicate(last):
                return last
            time.sleep(self.poll_interval_seconds)

        if last is not None:
            raise WriteError(
                f"{error_message}. Laatste state: mode={last.mode}, "
                f"set={last.set_temperature_c}, schedule_index={last.schedule_index}, constTempTime={last.const_temp_time}"
            )
        raise WriteError(error_message)

    def _extract_input_value(self, html: str, input_name: str) -> str:
        m = re.search(rf'name="{re.escape(input_name)}"[^>]*value="([^"]*)"', html)
        return m.group(1) if m else ""

    def _extract_honeypot_name(self, html: str) -> str:
        text_inputs = re.findall(r'<input[^>]*type="text"[^>]*name="([^"]+)"', html)
        for name in text_inputs:
            if name != "username":
                return name
        return ""

    def _get_program_form(self, zone: Zone, program_index: int) -> ProgramForm:
        forms = self.get_program_forms(zone)
        for f in forms:
            if f.index == program_index:
                return f
        available = ", ".join(str(f.index) for f in forms)
        raise NotFoundError(f"Program index {program_index} niet gevonden. Beschikbaar: {available}")

    def _submit_program_form(
        self,
        zone: Zone,
        form: ProgramForm,
        *,
        overrides: dict[str, str | list[str]],
    ) -> None:
        _logger = logging.getLogger(__name__)
        # Re-fetch the form fresh to get a valid CSRF token and fresh form data
        fresh_forms = self.get_program_forms(zone)
        fresh_form = None
        for f in fresh_forms:
            if f.index == form.index:
                fresh_form = f
                break
        if fresh_form is None:
            fresh_form = form  # fallback to original

        overrides = self._round_schedule_times(overrides)
        pairs = list(fresh_form.serialize_pairs())
        pairs = self._apply_schedule_overrides(pairs, overrides)

        _logger.debug(
            "submit_program_form: zone=%s idx=%d action=%s overrides=%s",
            zone.name, form.index, form.action_url, overrides,
        )

        response = None
        for attempt in range(2):
            csrf = self._get_csrf_for_account_module(zone.account_module_index, force_refresh=True)

            # Replace _token in pairs with fresh CSRF
            final_pairs = [(k, v) if k != "_token" else (k, csrf) for k, v in pairs]

            data_serialize = urllib.parse.urlencode(final_pairs, doseq=True)
            data_json = json.dumps([{"name": k, "value": v} for k, v in final_pairs], separators=(",", ":"))
            payload: list[tuple[str, str]] = list(final_pairs)
            payload.append(("dataSerialize", data_serialize))
            payload.append(("data", data_json))

            response = self._portal_session.post(
                form.action_url,
                data=payload,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "X-CSRF-TOKEN": csrf,
                    "Origin": self.base_url,
                    "Referer": f"{self.base_url}/e-control/zones?account_module_index={zone.account_module_index}",
                },
                timeout=self.request_timeout,
                verify=self.verify_tls,
            )

            _logger.debug(
                "submit_program_form attempt %d: status=%d body=%s",
                attempt, response.status_code, response.text[:200],
            )

            if self._needs_portal_refresh(response) and attempt == 0:
                _logger.warning("submit_program_form: portal refresh needed, retrying")
                self._invalidate_portal_auth()
                continue
            break

        if response is None:
            raise WriteError("Geen response ontvangen van scheduler/store")
        response.raise_for_status()

        try:
            obj = response.json()
        except Exception as exc:
            raise WriteError(f"Onverwachte scheduler response: {response.text[:300]}") from exc

        if int(obj.get("success", 0)) != 1:
            raise WriteError(f"Scheduler submit mislukt: {obj}")

    @staticmethod
    def _round_time_to_quarter(time_str: str) -> str:
        """Round a HH:MM time string to the nearest quarter hour.

        The Eplucon portal only accepts times on quarter-hour boundaries
        (xx:00, xx:15, xx:30, xx:45). Any other value is silently rejected.
        """
        try:
            parts = time_str.strip().split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return time_str  # can't parse, return as-is

        # Round to nearest 15
        rounded_m = round(m / 15) * 15
        if rounded_m == 60:
            rounded_m = 0
            h += 1
        if h >= 24:
            h = 23
            rounded_m = 45

        return f"{h:02d}:{rounded_m:02d}"

    def _round_schedule_times(self, overrides: dict[str, str | list[str]]) -> dict[str, str | list[str]]:
        """Round all interval start/end times in overrides to nearest quarter hour."""
        result = dict(overrides)
        for key in list(result.keys()):
            if "Intervals[start][]" in key or "Intervals[end][]" in key:
                value = result[key]
                if isinstance(value, list):
                    result[key] = [self._round_time_to_quarter(v) for v in value]
                elif isinstance(value, str):
                    result[key] = self._round_time_to_quarter(value)
        return result

    def _apply_schedule_overrides(
        self,
        pairs: list[tuple[str, str]],
        overrides: dict[str, str | list[str]],
    ) -> list[tuple[str, str]]:
        """Apply overrides for schedule forms with correct interval grouping.

        The portal expects interval fields grouped per interval:
          p0Intervals[index][]=0, p0Intervals[start][]=09:00,
          p0Intervals[end][]=16:30, p0Intervals[temp][]=19,
          p0Intervals[index][]=1, p0Intervals[start][]=17:00, ...

        NOT all indices together, all starts together, etc.
        """
        # Separate interval overrides from other overrides
        interval_overrides: dict[str, dict[str, list[str]]] = {}  # prefix -> {field -> [values]}
        other_overrides: dict[str, str | list[str]] = {}

        for key, value in overrides.items():
            if "Intervals[" in key:
                # e.g. "p0Intervals[start][]" -> prefix="p0", field="start"
                prefix = key[:2]  # "p0" or "p1"
                field = key.split("[")[1].rstrip("]")  # "index", "start", "end", "temp"
                if prefix not in interval_overrides:
                    interval_overrides[prefix] = {}
                if isinstance(value, list):
                    interval_overrides[prefix][field] = value
                else:
                    interval_overrides[prefix][field] = [value]
            else:
                other_overrides[key] = value

        # Apply non-interval overrides normally
        result = list(pairs)
        result = self._apply_overrides(result, other_overrides)

        # Remove all existing interval fields
        for prefix in ("p0", "p1"):
            result = [p for p in result if not (p[0].startswith(f"{prefix}Intervals["))]

        # Re-insert intervals grouped per interval, after the SetbackTemp of same profile
        for prefix in ("p0", "p1"):
            iv_data = interval_overrides.get(prefix, {})
            indices = iv_data.get("index", [])
            starts = iv_data.get("start", [])
            ends = iv_data.get("end", [])
            temps = iv_data.get("temp", [])
            count = max(len(indices), len(starts), len(ends), len(temps))

            if count == 0:
                continue

            # Build grouped interval pairs
            grouped: list[tuple[str, str]] = []
            for i in range(count):
                if i < len(indices):
                    grouped.append((f"{prefix}Intervals[index][]", indices[i]))
                if i < len(starts):
                    grouped.append((f"{prefix}Intervals[start][]", starts[i]))
                if i < len(ends):
                    grouped.append((f"{prefix}Intervals[end][]", ends[i]))
                if i < len(temps):
                    grouped.append((f"{prefix}Intervals[temp][]", temps[i]))

            # Insert after SetbackTemp of this profile
            insert_pos = None
            for idx, (k, _) in enumerate(result):
                if k == f"{prefix}SetbackTemp":
                    insert_pos = idx + 1
                    break

            if insert_pos is not None:
                for j, entry in enumerate(grouped):
                    result.insert(insert_pos + j, entry)
            else:
                result.extend(grouped)

        return result

    def _apply_overrides(
        self,
        pairs: Iterable[tuple[str, str]],
        overrides: dict[str, str | list[str]],
    ) -> list[tuple[str, str]]:
        """Apply overrides while preserving correct field ordering.

        For schedule forms, the portal expects fields in this order per profile:
        p{n}Days[0..6], p{n}SetbackTemp, p{n}Intervals[index/start/end/temp][],
        then p{n+1}Days etc.

        Strategy: rebuild the pairs list by inserting overrides at the position
        of the first removed field, rather than appending at the end.
        """
        out = list(pairs)
        for key, value in overrides.items():
            # Find position of first existing entry for this key
            insert_pos = None
            for i, (k, _) in enumerate(out):
                if k == key:
                    insert_pos = i
                    break

            # Remove all existing entries for this key
            out = [item for item in out if item[0] != key]

            # Build new entries
            new_entries: list[tuple[str, str]] = []
            if isinstance(value, list):
                for v in value:
                    new_entries.append((key, str(v)))
            else:
                new_entries.append((key, str(value)))

            # Insert at original position, or find logical position, or append
            if insert_pos is not None:
                # Clamp to current length
                insert_pos = min(insert_pos, len(out))
                for j, entry in enumerate(new_entries):
                    out.insert(insert_pos + j, entry)
            elif new_entries:
                # Find logical position: interval fields go after SetbackTemp of same profile
                placed = False
                if "Intervals[" in key:
                    # e.g. p0Intervals[start][] -> look for p0SetbackTemp or last p0 field
                    prefix = key[:2]  # "p0" or "p1"
                    last_prefix_pos = None
                    for i, (k, _) in enumerate(out):
                        if k.startswith(prefix):
                            last_prefix_pos = i
                    if last_prefix_pos is not None:
                        pos = last_prefix_pos + 1
                        for j, entry in enumerate(new_entries):
                            out.insert(pos + j, entry)
                        placed = True
                if not placed:
                    out.extend(new_entries)

        return out

    def _invalidate_portal_auth(self) -> None:
        self._portal_logged_in = False
        self._csrf_token_by_ami.clear()
        # Create a completely fresh session to avoid stale cookies/state
        self._portal_session = requests.Session()
        self._portal_session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            }
        )

    def _needs_portal_refresh(self, response: requests.Response) -> bool:
        if response.status_code in (401, 419):
            return True

        content_type = (response.headers.get("content-type") or "").lower()
        if "text/html" not in content_type:
            return False

        body = response.text or ""
        body_l = body.lower()
        if "<title>eplucon portaal" in body_l and "login" in body_l:
            return True
        if "gebruikersnaam / e-mailadres" in body_l and "wachtwoord" in body_l:
            return True
        return False
