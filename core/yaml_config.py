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
DEFAULT_LEARNING_TICK_INTERVAL_MINUTES = 5
DEFAULT_LEARNING_IDLE_THRESHOLD_MINUTES = 25
DEFAULT_LEARNING_FAILURE_COOLDOWN_HOURS = 1
DEFAULT_LEARNING_MAX_ENTRIES_PER_SESSION = 2
# Day 4 v2 calibration: raised from 280 → 400. Day 4 eval surfaced
# the LLM consistently producing 290-340 char lessons for technical
# content (multilingual RAG, cinema-time-bound, code-review brevity).
# These were good lessons — specific without being manifestos —
# but the 280 cap rejected them. 400 keeps the manifesto defense
# intact (a single-paragraph rule fits comfortably) while admitting
# legitimate technical detail. The prompt still pushes for ≤300
# typical with 400 as the ceiling.
DEFAULT_LEARNING_MAX_ENTRY_CHARS = 400


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


def learning_enabled() -> bool:
    raw = _section("learning").get("enabled", True)
    return bool(raw)


def learning_tick_interval_minutes() -> int:
    return _int_or_default(
        _section("learning").get("tick_interval_minutes"),
        DEFAULT_LEARNING_TICK_INTERVAL_MINUTES,
        minimum=1,
    )


def learning_idle_threshold_minutes() -> int:
    return _int_or_default(
        _section("learning").get("idle_threshold_minutes"),
        DEFAULT_LEARNING_IDLE_THRESHOLD_MINUTES,
        minimum=1,
    )


def learning_failure_cooldown_hours() -> int:
    return _int_or_default(
        _section("learning").get("failure_cooldown_hours"),
        DEFAULT_LEARNING_FAILURE_COOLDOWN_HOURS,
        minimum=0,
    )


def learning_shadow_mode() -> bool:
    """Default True until the eval (§7.4) and one-week soak give a green-light.

    When True, the curator writes proposed entries to MEMORY-SHADOW.md
    (a non-injected file the user reviews). When False, writes go to
    MEMORY.md and land in every future session's system prompt — so
    flipping this is the live-mode switch.
    """
    raw = _section("learning").get("shadow_mode", True)
    return bool(raw)


def learning_max_entries_per_session() -> int:
    return _int_or_default(
        _section("learning").get("max_entries_per_session"),
        DEFAULT_LEARNING_MAX_ENTRIES_PER_SESSION,
        minimum=1,
    )


def learning_max_entry_chars() -> int:
    return _int_or_default(
        _section("learning").get("max_entry_chars"),
        DEFAULT_LEARNING_MAX_ENTRY_CHARS,
        minimum=32,
    )


def browser_screenshot_include_base64() -> bool:
    """Whether ``vexis-browse screenshot`` includes ``image_base64`` by
    default. Off because most harnesses (including Claude Code) read
    the image via the file path with the Read tool, and a multi-MB
    base64 line breaks asyncio.StreamReader's default buffer when it
    rides through the brain's stream-json output. CLI callers can opt
    in per-call with ``--include-base64``.
    """
    raw = _section("browser").get("screenshot_include_base64", False)
    return bool(raw)


# --------------------------------------------------------------------
# [models] — per-subsystem model tier for claude -p subprocess calls
# --------------------------------------------------------------------
#
# Without this block every internal claude -p call (learning review,
# coherence judge, migration classifier) runs against the account's
# default model — typically Opus 4.7 — and competes for plan tokens
# with the user-facing brain. The defaults below pin internal calls
# to Sonnet (cheaper, fast enough for these use cases) while leaving
# the brain on the account default.
#
# ``"default"`` is a sentinel meaning "do not pass --model; use whatever
# claude -p picks on its own". Use this for the brain so user
# conversations track the account's chosen capability tier without
# the daemon second-guessing it.

DEFAULT_MODEL_BRAIN = "default"
DEFAULT_MODEL_LEARNING_REVIEW = "sonnet"
DEFAULT_MODEL_COHERENCE_JUDGE = "sonnet"
DEFAULT_MODEL_MIGRATION_CLASSIFIER = "sonnet"
DEFAULT_MODEL_RELATIONSHIPS_CLASSIFIER = "sonnet"


def _model_tier(key: str, default: str) -> str:
    """Read one model-tier string from the ``[models]`` section.

    Falls back to ``default`` on missing key, non-string values, or
    empty strings. This matches the rest of yaml_config's posture —
    a malformed config never blocks the daemon, it just falls through.
    """
    raw = _read_raw().get("models")
    section = raw if isinstance(raw, dict) else {}
    value = section.get(key, default)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def model_brain() -> str:
    return _model_tier("brain", DEFAULT_MODEL_BRAIN)


def model_learning_review() -> str:
    return _model_tier("learning_review", DEFAULT_MODEL_LEARNING_REVIEW)


def model_coherence_judge() -> str:
    return _model_tier("coherence_judge", DEFAULT_MODEL_COHERENCE_JUDGE)


def model_migration_classifier() -> str:
    return _model_tier("migration_classifier", DEFAULT_MODEL_MIGRATION_CLASSIFIER)


def model_relationships_classifier() -> str:
    return _model_tier(
        "relationships_classifier", DEFAULT_MODEL_RELATIONSHIPS_CLASSIFIER
    )


def resolve_model_flag(tier: str) -> list[str]:
    """Translate a model-tier string into ``claude -p`` argv flags.

    Returns ``["--model", "<tier>"]`` for any concrete tier
    (``"sonnet"``, ``"haiku"``, ``"opus"``, or a full model id like
    ``"claude-sonnet-4-6"``). Returns ``[]`` for the ``"default"``
    sentinel (or any empty / falsy value), letting ``claude -p`` pick
    its own default.

    Empty list rather than a None return so callers can splat with
    ``*resolve_model_flag(...)`` directly into argv composition without
    a conditional.
    """
    if not isinstance(tier, str):
        return []
    cleaned = tier.strip()
    if not cleaned or cleaned.lower() == "default":
        return []
    return ["--model", cleaned]
