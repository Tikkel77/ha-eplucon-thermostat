"""Deploy minimalist thermostat dashboard to HA."""
import json, ssl, websocket, sys

HA_WS = "wss://homeassistant.groosman.nl/api/websocket"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjMjgyZTY3ZGFlZTE0MGNiYWEzZjY1ODBmYzllYzE0MyIsImlhdCI6MTc3NTY3ODAxOSwiZXhwIjoyMDkxMDM4MDE5fQ.uF2QcQ8vbsj-Ir1hQtyvJ8zRAnAaCJsAYK2j8Z9lmgE"
DASHBOARD_URL = "dashboard-climate"

# Zone definitions: (climate_entity_id, sensor_prefix, zone_api_id, slug)
# Display names are fetched from HA at deploy time (friendly_name attribute)
ZONES_BY_FLOOR = {
    "Ground Floor": [
        ("climate.woonkamer_2", "woonkamer", 6813, "woonkamer"),
        ("climate.keuken_2", "keuken", 6814, "keuken"),
        ("climate.bijkeuken_2", "bijkeuken", 6812, "bijkeuken"),
        ("climate.kantoor_2", "kantoor", 6811, "kantoor"),
    ],
    "First Floor": [
        ("climate.slaapkamer_master", "slaapkamer_master", 6816, "slaapkamer_master"),
        ("climate.kantoor_1e_verd", "kantoor_1e_verd", 6817, "kantoor_1e"),
        ("climate.badkamer_1e", "badkamer_1e", 6815, "badkamer_1e"),
        ("climate.slaapkamer_lotte", "slaapkamer_lotte", 6818, "slaapkamer_lotte"),
    ],
    "Attic": [
        ("climate.badkamer_zolder", "badkamer_zolder", 6821, "badkamer_zolder"),
        ("climate.zolderkamer_voor", "zolderkamer_voor", 6819, "zolderkamer"),
        ("climate.slaapkamer_tiebe", "slaapkamer_tiebe", 6820, "slaapkamer_tiebe"),
    ],
}


def _fetch_friendly_names(ws):
    """Fetch friendly_name for all climate entities from HA."""
    ws.send(json.dumps({"id": 99, "type": "get_states"}))
    msg = json.loads(ws.recv())
    while msg.get("id") != 99:
        msg = json.loads(ws.recv())
    names = {}
    for state in msg.get("result", []):
        eid = state.get("entity_id", "")
        if eid.startswith("climate."):
            names[eid] = state.get("attributes", {}).get("friendly_name", eid)
    return names


def make_schedule_popup_card(slug, zone_api_id, name):
    """Build the schedule editor popup content card."""
    profile_entity = f"input_select.eplucon_sched_{slug}_profile"
    duration_entity = f"input_number.eplucon_duration_{slug}"

    rows = []

    # Current schedule summary (read-only)
    rows.append({
        "type": "entity",
        "entity": f"sensor.{slug}_schedule_summary",
        "name": "Current schedule",
        "icon": "mdi:calendar-text",
    })

    # Default duration (configurable)
    rows.append({
        "type": "entities",
        "entities": [
            {"entity": duration_entity, "name": "Default duration (min)"},
        ],
    })

    # Profile selector
    rows.append({
        "type": "entities",
        "entities": [
            {"entity": profile_entity, "name": "Editing profile"},
        ],
    })

    # Load button
    rows.append({
        "type": "custom:button-card",
        "name": "Load current schedule",
        "icon": "mdi:download",
        "tap_action": {
            "action": "call-service",
            "service": "eplucon_thermostat.load_schedule",
            "service_data": {"zone_api_id": zone_api_id},
        },
        "styles": {
            "card": [{"height": "40px"}, {"background": "var(--primary-color)"}, {"border-radius": "8px"}],
            "name": [{"font-size": "12px"}, {"color": "white"}],
            "icon": [{"color": "white"}, {"width": "16px"}],
        },
    })

    # Interval editors for both weekday and weekend
    for profile in ("weekday", "weekend"):
        interval_entities = []
        for i in range(1, 4):
            interval_entities.extend([
                {"entity": f"input_text.eplucon_sched_{slug}_{profile}_start_{i}", "name": f"Interval {i} start"},
                {"entity": f"input_text.eplucon_sched_{slug}_{profile}_end_{i}", "name": f"Interval {i} end"},
                {"entity": f"input_number.eplucon_sched_{slug}_{profile}_temp_{i}", "name": f"Interval {i} temp"},
            ])
        interval_entities.append(
            {"entity": f"input_number.eplucon_sched_{slug}_{profile}_setback", "name": "Setback temp"}
        )

        rows.append({
            "type": "conditional",
            "conditions": [{"entity": profile_entity, "state": profile}],
            "card": {
                "type": "entities",
                "title": f"{profile.title()} intervals",
                "entities": interval_entities,
            },
        })

    # Save buttons
    for profile in ("weekday", "weekend"):
        rows.append({
            "type": "conditional",
            "conditions": [{"entity": profile_entity, "state": profile}],
            "card": {
                "type": "custom:button-card",
                "name": f"Save {profile} schedule",
                "icon": "mdi:content-save",
                "tap_action": {
                    "action": "call-service",
                    "service": "eplucon_thermostat.save_schedule",
                    "service_data": {
                        "zone_api_id": zone_api_id,
                        "profile": profile,
                        "activate": True,
                    },
                },
                "styles": {
                    "card": [{"height": "44px"}, {"background": "var(--success-color, #4caf50)"}, {"border-radius": "8px"}],
                    "name": [{"font-size": "13px"}, {"color": "white"}, {"font-weight": "600"}],
                    "icon": [{"color": "white"}, {"width": "18px"}],
                },
            },
        })

    return rows


def _make_temp_button(ce, icon, delta):
    """Build a +/- temperature button — compact, distinct background, close to center."""
    return {
        "type": "custom:button-card",
        "entity": ce,
        "show_icon": True,
        "show_name": False,
        "show_state": False,
        "icon": icon,
        "variables": {
            "new_temp": f"[[[ return Math.round((entity.attributes.temperature + ({delta})) * 10) / 10; ]]]"
        },
        "tap_action": {
            "action": "call-service",
            "service": "climate.set_temperature",
            "service_data": {
                "entity_id": ce,
                "temperature": "[[[ return variables.new_temp; ]]]",
            },
        },
        "styles": {
            "card": [
                {"height": "34px"},
                {"width": "34px"},
                {"border-radius": "50%"},
                {"background": "rgba(var(--rgb-primary-color, 3,169,244), 0.15)"},
                {"box-shadow": "none"},
                {"border": "2px solid rgba(var(--rgb-primary-color, 3,169,244), 0.3)"},
                {"transition": "background 0.15s, transform 0.1s"},
                {"overflow": "hidden"},
            ],
            "icon": [{"color": "var(--primary-color)"}, {"width": "18px"}],
        },
        "card_mod": {
            "style": (
                "ha-card:active { "
                "  background: rgba(var(--rgb-primary-color, 3,169,244), 0.3) !important; "
                "  transform: scale(0.9) !important; "
                "}"
            ),
        },
    }


def make_zone_card(ce, sp, name, zone_api_id, slug):
    """Build a single zone thermostat card."""

    # --- Header: flame/thermometer icon + name + current temp (color-coded) + humidity ---
    header_name = (
        "[[[  const h = entity.attributes.hvac_action === 'heating';"
        "  var n = entity.attributes.friendly_name || 'Unknown';"
        "  return '<div style=\"display:flex;align-items:center;gap:4px\">"
        "<ha-icon icon=\"' + (h ? 'mdi:fire' : 'mdi:thermometer') + '\" "
        "style=\"--mdc-icon-size:14px;color:' + (h ? '#ff6b00' : 'var(--secondary-text-color)') + '\"></ha-icon>"
        "<span style=\"font-size:12px;font-weight:600\">' + n + '</span></div>'"
        "]]]"
    )

    # Current temp: smaller font (16px), color-coded vs set temp ±0.5
    header_temp = (
        "[[[  var cur = entity.attributes.current_temperature;"
        "  var set = entity.attributes.temperature;"
        "  var hu = states['sensor.SENSOR_PREFIX_humidity'];"
        "  var hv = hu && hu.state !== 'unavailable' ? hu.state : '';"
        "  var color = 'var(--primary-text-color)';"
        "  if (cur != null && set != null) {"
        "    if (cur < set - 0.5) color = '#2196F3';"
        "    else if (cur > set + 0.5) color = '#F44336';"
        "  }"
        "  return '<div style=\"text-align:right\">"
        "<span style=\"font-size:16px;font-weight:700;color:' + color + '\">' + (cur != null ? cur : '--') + '°</span>"
        "<span style=\"font-size:9px;color:var(--secondary-text-color);margin-left:2px\">' + (hv ? hv + '%' : '') + '</span>"
        "</div>'"
        "]]]"
    ).replace("SENSOR_PREFIX", sp)

    header = {
        "type": "custom:button-card",
        "entity": ce,
        "show_icon": False,
        "show_name": False,
        "show_state": False,
        "tap_action": {"action": "more-info"},
        "styles": {
            "card": [
                {"border-radius": "0"},
                {"padding": "10px 10px 2px 10px"},
                {"box-shadow": "none"},
                {"background": (
                    "[[[  return entity.attributes.hvac_action === 'heating'"
                    "  ? 'linear-gradient(135deg, rgba(255,140,0,0.12), rgba(255,107,0,0.05))'"
                    "  : 'transparent'"
                    "]]]"
                )},
            ],
            "grid": [
                {"grid-template-areas": '"n t"'},
                {"grid-template-columns": "1fr auto"},
            ],
            "custom_fields": {
                "n": [{"justify-self": "start"}, {"align-self": "center"}],
                "t": [{"justify-self": "end"}, {"align-self": "center"}],
            },
        },
        "custom_fields": {"n": header_name, "t": header_temp},
    }

    # --- Controls row: single card with CSS grid, 19 fr columns ---

    temp_display = {
        "type": "custom:button-card",
        "entity": ce,
        "show_icon": False,
        "show_name": False,
        "show_state": False,
        "tap_action": {"action": "more-info"},
        "styles": {
             "card": [
                {"height": "38px"},
                {"box-shadow": "none"},
                {"background": "transparent"},
                {"border-radius": "0"},
                {"padding": "0"},
                {"margin": "0"},
                {"overflow": "visible"},
             ],
            "grid": [{"grid-template-areas": '"temp"'}],
            "custom_fields": {
                "temp": [{"justify-self": "center"}, {"align-self": "center"}]
            },
        },
        "custom_fields": {
            "temp": (
                "[[[  var t = entity.attributes.temperature;"
                "  var pending = entity.attributes.temperature_pending || entity.attributes.temperature_writing;"
                "  var upd = states['binary_sensor.SENSOR_PREFIX_parameters_updating'];"
                "  var updating = upd && upd.state === 'on';"
                "  var isPending = pending || updating;"
                "  var style = isPending"
                "    ? 'font-size:18px;font-weight:600;color:var(--primary-color);animation:pulse 1.2s ease-in-out infinite'"
                "    : 'font-size:18px;font-weight:600';"
                "  return '<style>@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}</style>"
                "<span style=\"' + style + '\">' + (t != null ? t : '--') + '°C</span>'"
                "]]]"
            ).replace("SENSOR_PREFIX", sp)
        },
        "card_mod": {
            "style": "ha-card { overflow: visible !important; } :host { overflow: visible !important; }",
        },
    }

    # Controls row: single card with CSS grid
    # Each button is a nested button-card inside custom_fields (card: syntax)
    # This is the ONLY way to get per-field tap_action in button-card
    btn_style_base = (
        "width:32px;height:32px;border-radius:50%;"
        "background:rgba(var(--rgb-primary-color,3,169,244),0.15);"
        "border:2px solid rgba(var(--rgb-primary-color,3,169,244),0.3);"
        "display:flex;align-items:center;justify-content:center;"
        "cursor:pointer"
    )
    pending_template = (
        "[[[  var t = entity.attributes.temperature;"
        "  var pending = entity.attributes.temperature_pending || entity.attributes.temperature_writing;"
        "  var upd = states['binary_sensor.SENSOR_PREFIX_parameters_updating'];"
        "  var updating = upd && upd.state === 'on';"
        "  var isPending = pending || updating;"
        "  var style = isPending"
        "    ? 'font-size:18px;font-weight:600;color:var(--primary-color);animation:pulse 1.2s ease-in-out infinite'"
        "    : 'font-size:18px;font-weight:600';"
        "  return '<style>@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}</style>"
        "<span style=\"' + style + '\">' + (t != null ? t : '--') + '°C</span>'"
        "]]]"
    ).replace("SENSOR_PREFIX", sp)

    def _nested_btn(icon, delta):
        return {
            "card": {
                "type": "custom:button-card",
                "entity": ce,
                "show_icon": False,
                "show_name": False,
                "show_state": False,
                "tap_action": {
                    "action": "call-service",
                    "service": "climate.set_temperature",
                    "service_data": {
                        "entity_id": ce,
                        "temperature": f"[[[ return Math.round((entity.attributes.temperature + ({delta})) * 10) / 10; ]]]",
                    },
                },
                "custom_fields": {
                    "btn": (
                        f'[[[ return \'<div style="{btn_style_base}">'
                        f'<ha-icon icon=\\"{icon}\\" style=\\"--mdc-icon-size:18px;color:var(--primary-color)\\"></ha-icon>'
                        f"</div>' ]]]"
                    ),
                },
                "styles": {
                    "card": [
                        {"box-shadow": "none"},
                        {"background": "transparent"},
                        {"padding": "0"},
                        {"border-radius": "0"},
                        {"overflow": "visible"},
                        {"min-width": "32px"},
                    ],
                    "grid": [{"grid-template-areas": '"btn"'}],
                    "custom_fields": {"btn": [{"justify-self": "center"}, {"overflow": "visible"}]},
                },
            },
        }

    controls = {
        "type": "custom:button-card",
        "entity": ce,
        "show_icon": False,
        "show_name": False,
        "show_state": False,
        "tap_action": {"action": "none"},
        "styles": {
            "card": [
                {"box-shadow": "none"},
                {"background": "transparent"},
                {"padding": "8px 0"},
                {"border-radius": "0"},
                {"overflow": "visible"},
            ],
            "grid": [
                {"grid-template-areas": '". . . . minus . . . . temp . . . . plus . . . ."'},
                {"grid-template-columns": "repeat(19, 1fr)"},
                {"align-items": "center"},
                {"justify-items": "center"},
            ],
            "custom_fields": {
                "minus": [{"justify-self": "center"}, {"overflow": "visible"}],
                "temp": [{"overflow": "visible"}, {"z-index": "2"}, {"white-space": "nowrap"}],
                "plus": [{"justify-self": "center"}, {"overflow": "visible"}],
            },
        },
        "custom_fields": {
            "minus": _nested_btn("mdi:minus", -0.5),
            "temp": pending_template,
            "plus": _nested_btn("mdi:plus", 0.5),
        },
    }

    # --- Mode indicator — tapping opens schedule popup ---
    mode_template = (
        "[[[  var mode = entity.state;"
        "  var icon, text;"
        "  if (mode === 'localSchedule' || mode === 'globalSchedule') {"
        "    icon = 'mdi:calendar-clock'; text = 'Schedule';"
        "  } else if (mode === 'constantTemp') {"
        "    icon = 'mdi:lock'; text = 'Constant';"
        "  } else if (mode === 'timeLimit') {"
        "    icon = 'mdi:timer-outline';"
        "    var rs = states['sensor.SENSOR_PREFIX_time_limit_remaining'];"
        "    var r = rs ? parseInt(rs.state) || 0 : 0;"
        "    var h = Math.floor(r / 60); var m = r % 60;"
        "    text = 'Time' + (r > 0 ? ' · ' + (h > 0 ? h + 'h ' : '') + m + 'm' : '');"
        "  } else {"
        "    icon = 'mdi:help-circle-outline'; text = mode || 'Unknown';"
        "  }"
        "  return '<div style=\"display:flex;align-items:center;gap:3px;font-size:10px;color:var(--secondary-text-color)\">"
        "<ha-icon icon=\"' + icon + '\" style=\"--mdc-icon-size:12px\"></ha-icon>"
        "<span>' + text + '</span></div>'"
        "]]]"
    ).replace("SENSOR_PREFIX", sp)

    mode_row = {
        "type": "custom:button-card",
        "entity": "sensor." + sp + "_operating_mode",
        "show_icon": False,
        "show_name": False,
        "show_state": False,
        "tap_action": {
            "action": "fire-dom-event",
            "browser_mod": {
                "service": "browser_mod.popup",
                "data": {
                    "title": f"Schedule — {name}",
                    "content": {
                        "type": "vertical-stack",
                        "cards": make_schedule_popup_card(slug, zone_api_id, name),
                    },
                },
            },
        },
        "styles": {
            "card": [
                {"box-shadow": "none"},
                {"background": "transparent"},
                {"padding": "0 10px 2px 10px"},
                {"border-radius": "0"},
            ],
            "grid": [{"grid-template-areas": '"mode"'}],
            "custom_fields": {
                "mode": [{"justify-self": "start"}],
            },
        },
        "custom_fields": {"mode": mode_template},
    }

    # --- Mini graph: 12h temperature history ---
    graph = {
        "type": "custom:mini-graph-card",
        "entities": [
            {"entity": "sensor." + sp + "_current_temperature", "color": "#ff6b00"}
        ],
        "hours_to_show": 12,
        "points_per_hour": 4,
        "line_width": 2,
        "height": 40,
        "show": {
            "name": False,
            "icon": False,
            "state": False,
            "labels": False,
            "points": False,
        },
        "card_mod": {
            "style": "ha-card { box-shadow: none !important; border-radius: 0 !important; padding: 0 4px !important; margin: 0 !important; }"
        },
    }

    # --- Assemble zone card ---
    return {
        "type": "custom:vertical-stack-in-card",
        "cards": [header, controls, mode_row, graph],
        "card_mod": {
            "style": "ha-card { padding: 4px 0 6px 0 !important; }",
        },
    }


def build_dashboard_config(friendly_names):
    """Build the full dashboard config.

    Uses layout-card with CSS grid for responsive layout:
    - Desktop (>768px): 4 columns
    - Phone (<=768px): 2 columns
    Floor headings are plain markdown cards spanning all columns.
    """
    all_cards = []
    for floor_name, zones in ZONES_BY_FLOOR.items():
        # Floor heading — own row, spans full width
        all_cards.append({
            "type": "markdown",
            "content": f"## {floor_name}",
            "view_layout": {
                "grid-column": "1 / -1",
            },
            "card_mod": {
                "style": (
                    "ha-card { "
                    "  background: transparent !important; "
                    "  box-shadow: none !important; "
                    "  border: none !important; "
                    "  padding: 12px 4px 0 4px !important; "
                    "}"
                ),
            },
        })
        for ce, sp, zone_api_id, slug in zones:
            name = friendly_names.get(ce, ce.split(".")[-1].replace("_", " ").title())
            all_cards.append(make_zone_card(ce, sp, name, zone_api_id, slug))

    return {
        "views": [
            {
                "title": "Climate",
                "type": "panel",
                "cards": [
                    {
                        "type": "custom:layout-card",
                        "layout_type": "grid",
                        "layout": {
                            "grid-template-columns": "repeat(4, 1fr)",
                            "grid-gap": "8px",
                            "padding": "8px",
                            "mediaquery": {
                                "(max-width: 768px)": {
                                    "grid-template-columns": "1fr 1fr",
                                },
                            },
                        },
                        "cards": all_cards,
                    }
                ],
            }
        ]
    }


def _connect():
    """Connect and authenticate to HA WebSocket."""
    ws = websocket.create_connection(HA_WS, sslopt={"cert_reqs": ssl.CERT_NONE})
    msg = json.loads(ws.recv())
    assert msg["type"] == "auth_required"
    ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
    msg = json.loads(ws.recv())
    assert msg["type"] == "auth_ok", f"Auth failed: {msg}"
    print("Authenticated")
    return ws


def deploy(ws, config):
    """Deploy dashboard config via WebSocket."""
    ws.send(json.dumps({
        "id": 100,
        "type": "lovelace/config/save",
        "url_path": DASHBOARD_URL,
        "config": config,
    }))
    msg = json.loads(ws.recv())
    while msg.get("id") != 100:
        msg = json.loads(ws.recv())
    if msg.get("success"):
        print(f"Dashboard deployed to {DASHBOARD_URL}")
    else:
        print(f"FAILED: {json.dumps(msg, indent=2)}")
        sys.exit(1)


if __name__ == "__main__":
    ws = _connect()

    # Fetch friendly names from HA
    friendly_names = _fetch_friendly_names(ws)
    print(f"Fetched {len(friendly_names)} climate entity names:")
    for ce, sp, zid, slug in [z for zones in ZONES_BY_FLOOR.values() for z in zones]:
        print(f"  {ce} -> {friendly_names.get(ce, '(not found)')}")

    config = build_dashboard_config(friendly_names)
    # Write config to file for inspection
    with open("dashboard_config.json", "w") as f:
        json.dump(config, f, indent=2)
    print(f"Config written to dashboard_config.json ({len(json.dumps(config))} bytes)")

    if "--dry-run" in sys.argv:
        print("Dry run - not deploying")
    else:
        deploy(ws, config)

    ws.close()
