"""Create input helper entities on HA for the schedule editor.

For each zone:
- input_number.eplucon_duration_{slug}: Default override duration (minutes)
- input_select.eplucon_sched_{slug}_profile: weekday/weekend selector
- For each profile (weekday, weekend) × 3 intervals:
  - input_text.eplucon_sched_{slug}_{profile}_start_{1-3}: Start time HH:MM
  - input_text.eplucon_sched_{slug}_{profile}_end_{1-3}: End time HH:MM
  - input_number.eplucon_sched_{slug}_{profile}_temp_{1-3}: Temperature °C
- input_number.eplucon_sched_{slug}_{profile}_setback: Setback temp °C
"""

import json
import ssl
import websocket
import sys
import time

HA_WS = "wss://homeassistant.groosman.nl/api/websocket"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjMjgyZTY3ZGFlZTE0MGNiYWEzZjY1ODBmYzllYzE0MyIsImlhdCI6MTc3NTY3ODAxOSwiZXhwIjoyMDkxMDM4MDE5fQ.uF2QcQ8vbsj-Ir1hQtyvJ8zRAnAaCJsAYK2j8Z9lmgE"

# Zone definitions: (api_id, slug, display_name, default_duration_min)
ZONES = [
    (6811, "kantoor", "Kantoor", 480),
    (6812, "bijkeuken", "Bijkeuken", 120),
    (6813, "woonkamer", "Woonkamer", 480),
    (6814, "keuken", "Keuken", 480),
    (6815, "badkamer_1e", "Badkamer 1e", 120),
    (6816, "slaapkamer_master", "Slaapkamer Master", 120),
    (6817, "kantoor_1e", "Kantoor 1e", 480),
    (6818, "slaapkamer_lotte", "Slaapkamer Lotte", 120),
    (6819, "zolderkamer", "Zolderkamer", 120),
    (6820, "slaapkamer_tiebe", "Slaapkamer Tiebe", 120),
    (6821, "badkamer_zolder", "Badkamer Zolder", 120),
]


def ws_send(ws, msg_id, payload):
    """Send a WS message and return the response."""
    payload["id"] = msg_id
    ws.send(json.dumps(payload))
    resp = json.loads(ws.recv())
    # Skip event messages
    while resp.get("type") == "event" or resp.get("id") != msg_id:
        resp = json.loads(ws.recv())
    return resp


def create_input_number(ws, msg_id, name, object_id, min_val, max_val, step, initial, unit="", mode="box", icon=None):
    """Create an input_number helper."""
    payload = {
        "type": "input_number/create",
        "name": name,
        "min": min_val,
        "max": max_val,
        "step": step,
        "initial": initial,
        "mode": mode,
    }
    if unit:
        payload["unit_of_measurement"] = unit
    if icon:
        payload["icon"] = icon
    resp = ws_send(ws, msg_id, payload)
    entity_id = f"input_number.{object_id}"
    if resp.get("success"):
        print(f"  Created {entity_id}")
    else:
        err = resp.get("error", {}).get("message", "")
        if "already exists" in err.lower() or "unique" in err.lower():
            print(f"  Exists  {entity_id}")
        else:
            print(f"  FAILED  {entity_id}: {err}")
    return msg_id + 1


def create_input_text(ws, msg_id, name, object_id, initial="", pattern="", min_len=0, max_len=5, icon=None):
    """Create an input_text helper."""
    payload = {
        "type": "input_text/create",
        "name": name,
        "min": min_len,
        "max": max_len,
        "initial": initial,
        "mode": "text",
    }
    if pattern:
        payload["pattern"] = pattern
    if icon:
        payload["icon"] = icon
    resp = ws_send(ws, msg_id, payload)
    entity_id = f"input_text.{object_id}"
    if resp.get("success"):
        print(f"  Created {entity_id}")
    else:
        err = resp.get("error", {}).get("message", "")
        if "already exists" in err.lower() or "unique" in err.lower():
            print(f"  Exists  {entity_id}")
        else:
            print(f"  FAILED  {entity_id}: {err}")
    return msg_id + 1


def create_input_select(ws, msg_id, name, object_id, options, initial=None, icon=None):
    """Create an input_select helper."""
    payload = {
        "type": "input_select/create",
        "name": name,
        "options": options,
    }
    if initial:
        payload["initial"] = initial
    if icon:
        payload["icon"] = icon
    resp = ws_send(ws, msg_id, payload)
    entity_id = f"input_select.{object_id}"
    if resp.get("success"):
        print(f"  Created {entity_id}")
    else:
        err = resp.get("error", {}).get("message", "")
        if "already exists" in err.lower() or "unique" in err.lower():
            print(f"  Exists  {entity_id}")
        else:
            print(f"  FAILED  {entity_id}: {err}")
    return msg_id + 1


def main():
    ws = websocket.create_connection(HA_WS, sslopt={"cert_reqs": ssl.CERT_NONE})

    # Auth
    msg = json.loads(ws.recv())
    assert msg["type"] == "auth_required"
    ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
    msg = json.loads(ws.recv())
    assert msg["type"] == "auth_ok", f"Auth failed: {msg}"
    print("Authenticated to HA\n")

    msg_id = 1

    for api_id, slug, display_name, default_dur in ZONES:
        print(f"\n{display_name} ({slug}):")

        # Duration input_number
        msg_id = create_input_number(
            ws, msg_id,
            name=f"Eplucon duration {display_name}",
            object_id=f"eplucon_duration_{slug}",
            min_val=15, max_val=1440, step=15,
            initial=default_dur,
            unit="min",
            icon="mdi:timer-outline",
        )

        # Profile selector
        msg_id = create_input_select(
            ws, msg_id,
            name=f"Eplucon sched {display_name} profile",
            object_id=f"eplucon_sched_{slug}_profile",
            options=["weekday", "weekend"],
            initial="weekday",
            icon="mdi:calendar-week",
        )

        # Per-profile helpers
        for profile in ("weekday", "weekend"):
            for i in range(1, 4):
                # Start time
                msg_id = create_input_text(
                    ws, msg_id,
                    name=f"Eplucon sched {display_name} {profile} start {i}",
                    object_id=f"eplucon_sched_{slug}_{profile}_start_{i}",
                    initial="",
                    max_len=5,
                    icon="mdi:clock-start",
                )

                # End time
                msg_id = create_input_text(
                    ws, msg_id,
                    name=f"Eplucon sched {display_name} {profile} end {i}",
                    object_id=f"eplucon_sched_{slug}_{profile}_end_{i}",
                    initial="",
                    max_len=5,
                    icon="mdi:clock-end",
                )

                # Temperature
                msg_id = create_input_number(
                    ws, msg_id,
                    name=f"Eplucon sched {display_name} {profile} temp {i}",
                    object_id=f"eplucon_sched_{slug}_{profile}_temp_{i}",
                    min_val=5, max_val=35, step=0.5,
                    initial=15,
                    unit="°C",
                    icon="mdi:thermometer",
                )

            # Setback temperature
            msg_id = create_input_number(
                ws, msg_id,
                name=f"Eplucon sched {display_name} {profile} setback",
                object_id=f"eplucon_sched_{slug}_{profile}_setback",
                min_val=5, max_val=35, step=0.5,
                initial=15,
                unit="°C",
                icon="mdi:thermometer-low",
            )

    print(f"\nDone. Created helpers for {len(ZONES)} zones ({msg_id - 1} WS calls).")
    ws.close()


if __name__ == "__main__":
    main()
