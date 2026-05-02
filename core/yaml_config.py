"""Tiny optional config layer at ``~/.vexis/config.yaml``.

The env-var / .env config in ``core/config.py`` is the source of
truth for daemon credentials and workspace location. This file
supplements it with values that are nicer to keep in YAML:

    memory:
      memory_char_limit: 2200
      user_char_limit: 1375

    curator:
      enabled: true
      interval_hours: 168
      min_idle_hours: 2
      stale_after_days: 30
      archive_after_days: 90

All keys are optional. Missing file → all defaults. Malformed file
logs a warning and falls through to defaults; we don't want a
corrupt config to brick the daemon when sane defaults will do.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from core.paths import vexis_dir

log = logging.getLogger(__name__)

DEFAULT_MEMORY_CHAR_LIMIT = 2200
DEFAULT_USER_CHAR_LIMIT = 1375
DEFAULT_CURATOR_INTERVAL_HOURS = 168
DEFAULT_CURATOR_MIN_IDLE_HOURS = 2
DEFAULT_CURATOR_STALE_AFTER_DAYS = 30
DEFAULT_CURATOR_ARCHIVE_AFTER_DAYS = 90
DEFAULT_BROWSER_INACTIVITY_TIMEOUT_SECONDS = 120
DEFAULT_BROWSER_ACTION_TIMEOUT_SECONDS = 120


def _config_path() -> Path:
    return vexis_dir() / "config.yaml"


def _read_raw() -> dict[str, Any]:
    path = _config_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("Could not parse %s (%s); using defaults", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _section(name: str) -> dict[str, Any]:
    section = _read_raw().get(name)
    return section if isinstance(section, dict) else {}


def _int_or_default(value: Any, default: int, *, minimum: int = 1) -> int:
    if isinstance(value, bool):
        return default
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return default
    return ivalue if ivalue >= minimum else default


def memory_char_limit() -> int:
    return _int_or_default(
        _section("memory").get("memory_char_limit"),
        DEFAULT_MEMORY_CHAR_LIMIT,
        minimum=64,
    )


def user_char_limit() -> int:
    return _int_or_default(
        _section("memory").get("user_char_limit"),
        DEFAULT_USER_CHAR_LIMIT,
        minimum=64,
    )


def curator_enabled() -> bool:
    raw = _section("curator").get("enabled", True)
    return bool(raw)


def curator_interval_hours() -> int:
    return _int_or_default(
        _section("curator").get("interval_hours"),
        DEFAULT_CURATOR_INTERVAL_HOURS,
    )


def curator_min_idle_hours() -> int:
    return _int_or_default(
        _section("curator").get("min_idle_hours"),
        DEFAULT_CURATOR_MIN_IDLE_HOURS,
        minimum=0,
    )


def curator_stale_after_days() -> int:
    return _int_or_default(
        _section("curator").get("stale_after_days"),
        DEFAULT_CURATOR_STALE_AFTER_DAYS,
    )


def curator_archive_after_days() -> int:
    return _int_or_default(
        _section("curator").get("archive_after_days"),
        DEFAULT_CURATOR_ARCHIVE_AFTER_DAYS,
    )


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def browser_profiles_dir() -> str | None:
    return _str_or_none(_section("browser").get("profiles_dir"))


def browser_default_profile() -> str | None:
    return _str_or_none(_section("browser").get("default_profile"))


def browser_headless() -> bool:
    raw = _section("browser").get("headless", False)
    return bool(raw)


def browser_inactivity_timeout_seconds() -> int:
    return _int_or_default(
        _section("browser").get("inactivity_timeout_seconds"),
        DEFAULT_BROWSER_INACTIVITY_TIMEOUT_SECONDS,
        minimum=10,
    )


def browser_action_timeout_seconds() -> int:
    return _int_or_default(
        _section("browser").get("action_timeout_seconds"),
        DEFAULT_BROWSER_ACTION_TIMEOUT_SECONDS,
        minimum=5,
    )


def browser_chromium_path() -> str | None:
    return _str_or_none(_section("browser").get("chromium_path"))


def browser_cdp_url() -> str | None:
    """When set, attach to a user-launched Chrome instead of spawning one.

    Example value: ``http://localhost:9222``. The user is responsible
    for launching Chrome with ``--remote-debugging-port=9222`` and
    keeping it alive; Vexis will not kill the externally-launched
    process on shutdown.
    """
    return _str_or_none(_section("browser").get("cdp_url"))
