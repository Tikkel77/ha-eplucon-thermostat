from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

from .client import EpluconClient
from .errors import EpluconError
from .models import Zone
from .schedule import get_next_schedule_start, split_minutes_to_hours_minutes
from .settings import env_or_config, load_config_dict


def _add_auth_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--username", default=os.getenv("EPLUCON_USERNAME"), help="Portal username/email")
    parser.add_argument("--password", default=os.getenv("EPLUCON_PASSWORD"), help="Portal password")
    parser.add_argument("--api-key", default=os.getenv("EPLUCON_API_KEY"), help="Eplucon API key (Bearer token)")
    parser.add_argument("--base-url", default=os.getenv("EPLUCON_BASE_URL", "https://portaal.eplucon.nl"))
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=int(os.getenv("EPLUCON_REQUEST_TIMEOUT", "40")),
        help="HTTP request timeout in seconds",
    )
    parser.add_argument(
        "--settle-seconds",
        type=int,
        default=int(os.getenv("EPLUCON_SETTLE_SECONDS", "15")),
        help="Wait time after each write before polling",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=int(os.getenv("EPLUCON_POLL_INTERVAL_SECONDS", "5")),
        help="Polling interval for write confirmation",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification (not recommended)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="epluconctl", description="Eplucon thermostaat CLI")
    _add_auth_args(parser)
    parser.add_argument("--config", default=None, help="Path to eplucon.toml config file")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-modules", help="List all modules")

    p_zones = sub.add_parser("list-zones", help="List zones for all thermostat modules")
    p_zones.add_argument("--module-id", type=int, default=None)

    p_show_zone = sub.add_parser("show-zone", help="Show one zone")
    _add_zone_selector_args(p_show_zone)

    sub.add_parser("list-heatpumps", help="List heatpumps")

    p_show_hp = sub.add_parser("show-heatpump", help="Show one heatpump")
    p_show_hp.add_argument("--module-id", type=int, required=True)

    p_set_const = sub.add_parser("set-constant", help="Set constant temperature")
    _add_zone_selector_args(p_set_const)
    p_set_const.add_argument("--temp", type=float, required=True, help="Temperature in Celsius")
    p_set_const.add_argument("--wait-timeout", type=int, default=300)

    p_set_time = sub.add_parser("set-time-limit", help="Set temporary temperature with duration")
    _add_zone_selector_args(p_set_time)
    p_set_time.add_argument("--temp", type=float, required=True, help="Temperature in Celsius")
    p_set_time.add_argument("--hours", type=int, required=True)
    p_set_time.add_argument("--minutes", type=int, required=True)
    p_set_time.add_argument("--wait-timeout", type=int, default=300)

    p_set_for = sub.add_parser("set-for", help="Set temperature for duration (default 4h if omitted)")
    _add_zone_selector_args(p_set_for)
    p_set_for.add_argument("--temp", type=float, required=True, help="Temperature in Celsius")
    p_set_for.add_argument("--hours", type=int, default=None)
    p_set_for.add_argument("--minutes", type=int, default=None)
    p_set_for.add_argument("--default-minutes", type=int, default=240)
    p_set_for.add_argument("--wait-timeout", type=int, default=300)

    p_set_con = sub.add_parser(
        "set-con",
        help="Set temperature until next schedule start (max 8h by default)",
    )
    _add_zone_selector_args(p_set_con)
    p_set_con.add_argument("--temp", type=float, required=True, help="Temperature in Celsius")
    p_set_con.add_argument("--default-minutes", type=int, default=240)
    p_set_con.add_argument("--max-minutes", type=int, default=480)
    p_set_con.add_argument("--wait-timeout", type=int, default=300)

    p_plan_con = sub.add_parser("plan-con", help="Show planned con duration without writing")
    _add_zone_selector_args(p_plan_con)
    p_plan_con.add_argument("--default-minutes", type=int, default=240)
    p_plan_con.add_argument("--max-minutes", type=int, default=480)

    p_list_prog = sub.add_parser("list-programs", help="List schedule program forms for zone")
    _add_zone_selector_args(p_list_prog)

    p_activate_prog = sub.add_parser("activate-program", help="Activate program index for zone")
    _add_zone_selector_args(p_activate_prog)
    p_activate_prog.add_argument("--index", type=int, required=True, help="Program index (-17 local, 0..4 global)")
    p_activate_prog.add_argument("--wait-timeout", type=int, default=360)

    p_set_setback = sub.add_parser("set-program-setback", help="Update setback temperatures for program")
    _add_zone_selector_args(p_set_setback)
    p_set_setback.add_argument("--index", type=int, required=True)
    p_set_setback.add_argument("--p0", type=float, default=None, help="Weekday setback in Celsius")
    p_set_setback.add_argument("--p1", type=float, default=None, help="Weekend setback in Celsius")
    p_set_setback.add_argument("--wait-timeout", type=int, default=300)

    p_submit_prog = sub.add_parser("submit-program", help="Submit arbitrary overrides for a program form")
    _add_zone_selector_args(p_submit_prog)
    p_submit_prog.add_argument("--index", type=int, required=True)
    p_submit_prog.add_argument(
        "--overrides-json",
        help=(
            "JSON object with form overrides, e.g. "
            "'{\"p0SetbackTemp\":\"19\",\"p0Intervals[start][]\":[\"06:00\",\"12:00\"]}'"
        ),
    )
    p_submit_prog.add_argument("--overrides-file", help="Path to JSON file with overrides object")
    p_submit_prog.add_argument("--wait", action="store_true", help="Wait until polling cycle completes")
    p_submit_prog.add_argument("--wait-timeout", type=int, default=300)

    return parser


def _add_zone_selector_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--zone-api-id", type=int, default=None)
    parser.add_argument("--zone-internal-id", type=int, default=None)
    parser.add_argument("--name", default=None, help="Zone name")
    parser.add_argument("--module-id", type=int, default=None)


def _require_auth(args: argparse.Namespace) -> None:
    missing = []
    if not args.username:
        missing.append("--username or EPLUCON_USERNAME")
    if not args.password:
        missing.append("--password or EPLUCON_PASSWORD")
    if not args.api_key:
        missing.append("--api-key or EPLUCON_API_KEY")
    if missing:
        raise SystemExit("Ontbrekende authenticatie: " + ", ".join(missing))


def _create_client(args: argparse.Namespace) -> EpluconClient:
    config, _ = load_config_dict(args.config)

    username = args.username or env_or_config(
        "EPLUCON_USERNAME",
        config,
        "auth",
        "username",
        default=None,
        cast=str,
    )
    password = args.password or env_or_config(
        "EPLUCON_PASSWORD",
        config,
        "auth",
        "password",
        default=None,
        cast=str,
    )
    api_key = args.api_key or env_or_config(
        "EPLUCON_API_KEY",
        config,
        "auth",
        "api_key",
        default=None,
        cast=str,
    )

    base_url = env_or_config(
        "EPLUCON_BASE_URL",
        config,
        "connection",
        "base_url",
        default=args.base_url,
        cast=str,
    )
    request_timeout = env_or_config(
        "EPLUCON_REQUEST_TIMEOUT",
        config,
        "connection",
        "request_timeout",
        default=args.request_timeout,
        cast=int,
    )
    settle_seconds = env_or_config(
        "EPLUCON_SETTLE_SECONDS",
        config,
        "connection",
        "settle_seconds",
        default=args.settle_seconds,
        cast=int,
    )
    poll_interval = env_or_config(
        "EPLUCON_POLL_INTERVAL_SECONDS",
        config,
        "connection",
        "poll_interval_seconds",
        default=args.poll_interval_seconds,
        cast=int,
    )
    insecure = env_or_config(
        "EPLUCON_INSECURE",
        config,
        "connection",
        "insecure",
        default=args.insecure,
        cast=bool,
    )

    args.username = username
    args.password = password
    args.api_key = api_key
    _require_auth(args)

    return EpluconClient(
        username=username,
        password=password,
        api_key=api_key,
        base_url=base_url,
        request_timeout=request_timeout,
        settle_seconds=settle_seconds,
        poll_interval_seconds=poll_interval,
        verify_tls=not insecure,
    )


def _select_zone(client: EpluconClient, args: argparse.Namespace) -> Zone:
    if args.zone_api_id is None and args.zone_internal_id is None and args.name is None:
        raise SystemExit("Selecteer een zone via --zone-api-id of --zone-internal-id of --name")

    return client.get_zone(
        zone_api_id=args.zone_api_id,
        zone_internal_id=args.zone_internal_id,
        name=args.name,
        module_id=args.module_id,
    )


def _print_zone(zone: Zone) -> None:
    print(
        json.dumps(
            {
                "module_id": zone.module_id,
                "module_name": zone.module_name,
                "account_module_index": zone.account_module_index,
                "zone_api_id": zone.zone_api_id,
                "zone_internal_id": zone.zone_internal_id,
                "mode_id": zone.mode_id,
                "schedule_id": zone.schedule_id,
                "name": zone.name,
                "mode": zone.mode,
                "set_temperature_c": zone.set_temperature_c,
                "set_temperature_deci": zone.set_temperature_deci,
                "current_temperature_c": zone.current_temperature_c,
                "schedule_index": zone.schedule_index,
                "const_temp_time": zone.const_temp_time,
            },
            ensure_ascii=True,
            indent=2,
        )
    )


def _load_overrides(args: argparse.Namespace) -> dict[str, Any]:
    raw: Optional[str] = None
    if args.overrides_json:
        raw = args.overrides_json
    elif args.overrides_file:
        with open(args.overrides_file, "r", encoding="utf-8") as f:
            raw = f.read()

    if not raw:
        raise SystemExit("Geef --overrides-json of --overrides-file")

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Ongeldige JSON voor overrides: {exc}") from exc

    if not isinstance(obj, dict):
        raise SystemExit("Overrides JSON moet een object zijn")
    return obj


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        client = _create_client(args)

        if args.command == "list-modules":
            modules = client.get_modules()
            for m in modules:
                print(
                    json.dumps(
                        {
                            "id": m.id,
                            "name": m.name,
                            "type": m.type,
                            "account_module_index": m.account_module_index,
                        },
                        ensure_ascii=True,
                    )
                )
            return 0

        if args.command == "list-zones":
            zones = client.get_zones(module_id=args.module_id)
            for z in zones:
                print(
                    json.dumps(
                        {
                            "module_id": z.module_id,
                            "module_name": z.module_name,
                            "zone_api_id": z.zone_api_id,
                            "zone_internal_id": z.zone_internal_id,
                            "name": z.name,
                            "mode": z.mode,
                            "set_temperature_c": z.set_temperature_c,
                            "current_temperature_c": z.current_temperature_c,
                            "schedule_index": z.schedule_index,
                            "const_temp_time": z.const_temp_time,
                        },
                        ensure_ascii=True,
                    )
                )
            return 0

        if args.command == "show-zone":
            zone = _select_zone(client, args)
            _print_zone(zone)
            return 0

        if args.command == "list-heatpumps":
            hps = client.get_heatpumps()
            for hp in hps:
                print(
                    json.dumps(
                        {
                            "module_id": hp.module_id,
                            "name": hp.name,
                            "account_module_index": hp.account_module_index,
                        },
                        ensure_ascii=True,
                    )
                )
            return 0

        if args.command == "show-heatpump":
            import dataclasses
            rt_info = client.get_heatpump_realtime_info(args.module_id)
            hl_status = client.get_heatpump_heatloading_status(args.module_id)
            print(
                json.dumps(
                    {
                        "realtime_info": dataclasses.asdict(rt_info),
                        "heatloading_status": dataclasses.asdict(hl_status),
                    },
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return 0

        if args.command == "set-constant":
            zone = _select_zone(client, args)
            updated = client.set_constant_temperature(
                zone,
                args.temp,
                wait=True,
                wait_timeout_seconds=args.wait_timeout,
            )
            _print_zone(updated)
            return 0

        if args.command == "set-time-limit":
            zone = _select_zone(client, args)
            updated = client.set_time_limit_temperature(
                zone,
                args.temp,
                hours=args.hours,
                minutes=args.minutes,
                wait=True,
                wait_timeout_seconds=args.wait_timeout,
            )
            _print_zone(updated)
            return 0

        if args.command == "set-for":
            zone = _select_zone(client, args)
            updated, used = client.set_temperature_with_default_duration(
                zone,
                args.temp,
                hours=args.hours,
                minutes=args.minutes,
                default_minutes=args.default_minutes,
                wait=True,
                wait_timeout_seconds=args.wait_timeout,
            )
            _print_zone(updated)
            h, m = split_minutes_to_hours_minutes(used)
            print(json.dumps({"used_minutes": used, "used_duration": f"{h}h{m:02d}"}, ensure_ascii=True))
            return 0

        if args.command == "set-con":
            zone = _select_zone(client, args)
            updated, used = client.set_temperature_con_until_next_schedule(
                zone,
                args.temp,
                default_minutes=args.default_minutes,
                max_minutes=args.max_minutes,
                wait=True,
                wait_timeout_seconds=args.wait_timeout,
            )
            _print_zone(updated)
            h, m = split_minutes_to_hours_minutes(used)
            print(json.dumps({"used_minutes": used, "used_duration": f"{h}h{m:02d}"}, ensure_ascii=True))
            return 0

        if args.command == "plan-con":
            zone = _select_zone(client, args)
            used = client.plan_con_duration_minutes(
                zone,
                default_minutes=args.default_minutes,
                max_minutes=args.max_minutes,
            )
            next_start = get_next_schedule_start(zone)
            h, m = split_minutes_to_hours_minutes(used)
            print(
                json.dumps(
                    {
                        "zone_api_id": zone.zone_api_id,
                        "name": zone.name,
                        "used_minutes": used,
                        "used_duration": f"{h}h{m:02d}",
                        "next_schedule_start": (next_start.isoformat() if next_start else None),
                    },
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return 0

        if args.command == "list-programs":
            zone = _select_zone(client, args)
            programs = client.get_program_forms(zone)
            for p in programs:
                print(
                    json.dumps(
                        {
                            "index": p.index,
                            "schedule_id": p.schedule_id,
                            "mode_id": p.mode_id,
                            "zone_id": p.zone_id,
                            "schedule_name": p.schedule_name,
                            "action_url": p.action_url,
                        },
                        ensure_ascii=True,
                    )
                )
            return 0

        if args.command == "activate-program":
            zone = _select_zone(client, args)
            updated = client.activate_program(
                zone,
                program_index=args.index,
                wait=True,
                wait_timeout_seconds=args.wait_timeout,
            )
            _print_zone(updated)
            return 0

        if args.command == "set-program-setback":
            zone = _select_zone(client, args)
            updated = client.set_program_setback(
                zone,
                program_index=args.index,
                p0_setback_c=args.p0,
                p1_setback_c=args.p1,
                wait=True,
                wait_timeout_seconds=args.wait_timeout,
            )
            _print_zone(updated)
            return 0

        if args.command == "submit-program":
            zone = _select_zone(client, args)
            overrides = _load_overrides(args)
            updated = client.submit_program_form(
                zone,
                program_index=args.index,
                overrides=overrides,
                wait=args.wait,
                wait_timeout_seconds=args.wait_timeout,
            )
            _print_zone(updated)
            return 0

        raise SystemExit(f"Onbekend command: {args.command}")

    except EpluconError as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
