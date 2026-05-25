from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import Any, Optional

from .models import ProgramForm, Zone


def get_next_schedule_start(zone: Zone, now: Optional[datetime] = None, search_days: int = 8) -> Optional[datetime]:
    """Return the next schedule start datetime for this zone.

    It scans p0/p1 day masks and interval starts from zone.raw_data["schedule"].
    """
    if now is None:
        now = datetime.now().astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)

    schedule = zone.raw_data.get("schedule", {})
    if not isinstance(schedule, dict):
        return None
    return _get_next_schedule_start_from_schedule(schedule, now=now, search_days=search_days)


def compute_con_duration_minutes(
    zone: Zone,
    now: Optional[datetime] = None,
    *,
    default_minutes: int = 240,
    max_minutes: int = 480,
) -> int:
    """Compute 'con' duration: until next schedule start, capped.

    If no next schedule start can be determined, fallback to default_minutes.
    """
    if now is None:
        now = datetime.now().astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)

    next_start = get_next_schedule_start(zone, now=now)
    return compute_con_duration_minutes_from_next_start(
        next_start,
        now=now,
        default_minutes=default_minutes,
        max_minutes=max_minutes,
    )


def compute_con_duration_minutes_from_next_start(
    next_start: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    default_minutes: int = 240,
    max_minutes: int = 480,
) -> int:
    if now is None:
        now = datetime.now().astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)

    if next_start is None:
        return _clamp_minutes(default_minutes, max_minutes=max_minutes)

    total_seconds = (next_start - now).total_seconds()
    if total_seconds <= 0:
        return 1

    minutes = int(math.ceil(total_seconds / 60.0))
    return _clamp_minutes(minutes, max_minutes=max_minutes)


def get_next_schedule_start_from_program_form(
    form: ProgramForm,
    *,
    now: Optional[datetime] = None,
    search_days: int = 8,
) -> Optional[datetime]:
    schedule = _program_form_to_schedule_dict(form)
    return _get_next_schedule_start_from_schedule(schedule, now=now, search_days=search_days)


def split_minutes_to_hours_minutes(total_minutes: int) -> tuple[int, int]:
    total_minutes = max(0, int(total_minutes))
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return hours, minutes


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return fallback


def _clamp_minutes(minutes: int, *, max_minutes: int) -> int:
    minutes = max(1, int(minutes))
    return min(minutes, max(1, int(max_minutes)))


def _get_next_schedule_start_from_schedule(
    schedule: dict[str, Any],
    *,
    now: Optional[datetime],
    search_days: int,
) -> Optional[datetime]:
    if now is None:
        now = datetime.now().astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)

    tzinfo = now.tzinfo
    candidates: list[datetime] = []

    for day_offset in range(0, max(1, search_days) + 1):
        day = (now + timedelta(days=day_offset if day_offset > 0 else 0)).date()
        weekday = day.weekday()

        for profile in ("p0", "p1"):
            days = schedule.get(f"{profile}Days", [])
            if not isinstance(days, list) or len(days) < 7:
                continue
            if str(days[weekday]) != "1":
                continue

            intervals = schedule.get(f"{profile}Intervals", [])
            if not isinstance(intervals, list):
                continue

            for interval in intervals:
                if not isinstance(interval, dict):
                    continue
                # Both start and stop of an interval are setpoint transitions
                for edge_key in ("start", "stop"):
                    edge = _safe_int(interval.get(edge_key), fallback=6100)
                    if edge < 0 or edge >= 1440:
                        continue
                    dt = datetime.combine(day, time.min, tzinfo=tzinfo) + timedelta(minutes=edge)
                    if dt > now:
                        candidates.append(dt)

    if not candidates:
        return None
    return min(candidates)


def _program_form_to_schedule_dict(form: ProgramForm) -> dict[str, Any]:
    values: dict[str, list[str]] = {}
    for item in form.inputs:
        if item.input_type == "checkbox" and not item.checked:
            continue
        values.setdefault(item.name, []).append(item.value)

    out: dict[str, Any] = {
        "p0Days": ["0"] * 7,
        "p1Days": ["0"] * 7,
        "p0Intervals": [],
        "p1Intervals": [],
    }

    for profile in ("p0", "p1"):
        for day_idx in range(7):
            key = f"{profile}Days[{day_idx}]"
            day_val = values.get(key, ["0"])[0]
            out[f"{profile}Days"][day_idx] = "1" if str(day_val) == "1" else "0"

        starts = values.get(f"{profile}Intervals[start][]", [])
        stops = values.get(f"{profile}Intervals[end][]", [])
        temps = values.get(f"{profile}Intervals[temp][]", [])

        count = max(len(starts), len(stops), len(temps))
        intervals: list[dict[str, int]] = []
        for i in range(count):
            start_minutes = _parse_hhmm_to_minutes(starts[i]) if i < len(starts) else None
            stop_minutes = _parse_hhmm_to_minutes(stops[i]) if i < len(stops) else None
            if start_minutes is None:
                start_minutes = 6100
            if stop_minutes is None:
                stop_minutes = 6100
            temp_val = _safe_int(temps[i], fallback=0) if i < len(temps) else 0
            intervals.append({"start": start_minutes, "stop": stop_minutes, "temp": temp_val})

        out[f"{profile}Intervals"] = intervals

    return out


def _parse_hhmm_to_minutes(value: str) -> Optional[int]:
    try:
        hh_s, mm_s = str(value).strip().split(":", 1)
        hh = int(hh_s)
        mm = int(mm_s)
    except (ValueError, TypeError):
        return None
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return hh * 60 + mm
