from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import tomllib


def load_config_dict(explicit_path: Optional[str] = None) -> tuple[dict[str, Any], Path]:
    path = _resolve_config_path(explicit_path)
    if not path.exists():
        return {}, path
    with path.open("rb") as f:
        data = tomllib.load(f)
    if not isinstance(data, dict):
        return {}, path
    return data, path


def env_or_config(
    env_name: str,
    config: dict[str, Any],
    *keys: str,
    default: Any = None,
    cast: type = str,
) -> Any:
    env_val = os.getenv(env_name)
    if env_val is not None and env_val != "":
        return _cast_value(env_val, cast, default)

    cfg_val = _get_nested(config, *keys)
    if cfg_val is not None:
        return _cast_value(cfg_val, cast, default)

    return default


def _resolve_config_path(explicit_path: Optional[str]) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()

    env_path = os.getenv("EPLUCON_CONFIG")
    if env_path:
        return Path(env_path).expanduser().resolve()

    cwd_path = (Path.cwd() / "eplucon.toml").resolve()
    if cwd_path.exists():
        return cwd_path

    home_path = (Path.home() / ".eplucon.toml").resolve()
    if home_path.exists():
        return home_path

    return cwd_path


def _get_nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _cast_value(value: Any, cast: type, default: Any) -> Any:
    try:
        if cast is bool:
            return _to_bool(value)
        return cast(value)
    except Exception:
        return default


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text in ("1", "true", "yes", "on", "y")
