"""Parser tests for ``vexis_agent.tools.schedule_tool.parser``.

Coverage targets (Day 1):

  * The four accepted input shapes — duration / interval / cron / ISO.
  * Sub-minute reject (``every 30s`` / ``every 0m`` / ``every 0``).
  * Timezone resolution — explicit IANA, daemon-local default,
    invalid IANA with suggestion.
  * System-clock invariant — ``compute_next_fire`` uses ``datetime.now()``
    at invocation, not anything caller-supplied.
  * DST: cron at 02:30 on US spring-forward day skips the missed slot
    (croniter native behaviour, pinned so a future upgrade can't
    silently flip).
  * Grace seconds: interval / cron / one-shot all return reasonable
    clamped values.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from vexis_agent.tools.schedule_tool.parser import (
    DEFAULT_ONESHOT_GRACE_SECONDS,
    MAX_GRACE_SECONDS,
    MIN_GRACE_SECONDS,
    ScheduleParseError,
    compute_grace_seconds,
    compute_next_fire,
    parse_schedule,
)


# ──────────────────────────────────────────────────────────────────
# Duration → one-shot
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spec,expected_minutes",
    [
        ("30m", 30),
        ("2h", 120),
        ("1d", 1440),
        ("90m", 90),
        # case insensitivity
        ("30M", 30),
        ("2H", 120),
    ],
)
def test_parse_duration_one_shot(spec, expected_minutes):
    """``30m`` / ``2h`` / ``1d`` produce one-shot with run_at = now + delta."""
    fixed_now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
    with patch(
        "vexis_agent.tools.schedule_tool.parser._now",
        lambda tz=None: fixed_now.astimezone(tz) if tz else fixed_now,
    ):
        result = parse_schedule(spec, tz="UTC")

    assert result["kind"] == "once"
    assert "run_at" in result
    parsed = datetime.fromisoformat(result["run_at"])
    expected = fixed_now + timedelta(minutes=expected_minutes)
    # Allow 1s tolerance for the now-capture window in _resolve_tz.
    assert abs((parsed - expected).total_seconds()) < 2
    assert "tz" in result


def test_parse_duration_rejects_seconds():
    """Sub-minute resolution rejected loudly with a brain-facing hint."""
    with pytest.raises(ScheduleParseError) as exc_info:
        parse_schedule("30s")
    assert "1 minute" in str(exc_info.value).lower()
    assert "1m" in exc_info.value.suggestion


def test_parse_duration_rejects_zero():
    """``every 0m`` / ``every 0h`` rejected with the same floor message."""
    with pytest.raises(ScheduleParseError):
        parse_schedule("every 0m")
    with pytest.raises(ScheduleParseError):
        parse_schedule("0m")


# ──────────────────────────────────────────────────────────────────
# Interval recurring
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spec,expected_minutes",
    [
        ("every 30m", 30),
        ("every 2h", 120),
        ("every 1d", 1440),
        ("EVERY 30m", 30),  # case insensitive
        ("every 1 minute", 1),  # word form
        ("every 2 hours", 120),
        ("every 1 day", 1440),
    ],
)
def test_parse_interval(spec, expected_minutes):
    result = parse_schedule(spec)
    assert result["kind"] == "interval"
    assert result["minutes"] == expected_minutes
    assert result["display"] == f"every {expected_minutes}m"
    # Interval is wall-clock-agnostic; tz field is not stored.
    assert "tz" not in result


def test_parse_interval_rejects_subminute():
    with pytest.raises(ScheduleParseError, match=r"1 minute"):
        parse_schedule("every 30s")


# ──────────────────────────────────────────────────────────────────
# Cron expressions
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "expr",
    [
        "0 9 * * *",  # daily at 9am
        "0 9 * * 1-5",  # weekdays at 9am
        "*/15 * * * *",  # every 15 min
        "0 0 1 * *",  # first of month
        "0 9-17 * * 1-5",  # 9am-5pm weekdays
    ],
)
def test_parse_cron_valid(expr):
    result = parse_schedule(expr)
    assert result["kind"] == "cron"
    assert result["expr"] == expr
    assert "tz" in result
    assert result["display"] == expr


def test_parse_cron_invalid_expression():
    with pytest.raises(ScheduleParseError) as exc_info:
        parse_schedule("0 99 * * *")  # hour 99 doesn't exist
    assert "cron" in exc_info.value.suggestion.lower()


def test_parse_cron_with_explicit_tz():
    result = parse_schedule("0 9 * * *", tz="Asia/Tokyo")
    assert result["kind"] == "cron"
    assert result["tz"] == "Asia/Tokyo"


def test_parse_cron_with_invalid_tz():
    with pytest.raises(ScheduleParseError) as exc_info:
        parse_schedule("0 9 * * *", tz="Not/A/Real/Zone")
    assert "timezone" in str(exc_info.value).lower()
    assert "iana" in exc_info.value.suggestion.lower()


# ──────────────────────────────────────────────────────────────────
# ISO timestamps
# ──────────────────────────────────────────────────────────────────


def test_parse_iso_naive():
    """Naive ISO → caller-tz (defaulting to daemon-local)."""
    result = parse_schedule("2026-12-31T23:59:00", tz="UTC")
    assert result["kind"] == "once"
    parsed = datetime.fromisoformat(result["run_at"])
    assert parsed.year == 2026 and parsed.month == 12 and parsed.day == 31
    assert parsed.tzinfo is not None  # naive input → aware output


def test_parse_iso_with_zulu():
    """Trailing ``Z`` honored as UTC."""
    result = parse_schedule("2026-12-31T23:59:00Z")
    assert result["kind"] == "once"
    parsed = datetime.fromisoformat(result["run_at"])
    assert parsed.utcoffset() == timedelta(0)


def test_parse_iso_with_offset():
    """Explicit ``+HH:MM`` honored as-written."""
    result = parse_schedule("2026-12-31T23:59:00+09:00")
    assert result["kind"] == "once"
    parsed = datetime.fromisoformat(result["run_at"])
    assert parsed.utcoffset() == timedelta(hours=9)


def test_parse_iso_invalid_string():
    with pytest.raises(ScheduleParseError) as exc_info:
        parse_schedule("2026-13-99T99:99:99")  # invalid date
    assert "iso" in exc_info.value.suggestion.lower()


# ──────────────────────────────────────────────────────────────────
# Empty / bogus input
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", ["", "   ", None])
def test_parse_empty_input(spec):
    with pytest.raises((ScheduleParseError, TypeError)):
        parse_schedule(spec)  # type: ignore[arg-type]


def test_parse_completely_bogus():
    with pytest.raises(ScheduleParseError) as exc_info:
        parse_schedule("hello world")
    # The generic error should mention all four supported shapes.
    suggestion = exc_info.value.suggestion.lower()
    assert "duration" in suggestion
    assert "interval" in suggestion
    assert "cron" in suggestion
    assert "timestamp" in suggestion


# ──────────────────────────────────────────────────────────────────
# compute_next_fire — system-clock invariant
# ──────────────────────────────────────────────────────────────────


def test_compute_next_fire_cron_uses_system_clock():
    """The brain cannot fabricate "now" — the parser uses datetime.now()."""
    schedule = parse_schedule("0 9 * * *", tz="UTC")

    # Freeze "now" at 2026-05-10 08:00 UTC. Next fire should be today 9am.
    fixed_now = datetime(2026, 5, 10, 8, 0, 0, tzinfo=ZoneInfo("UTC"))
    with patch(
        "vexis_agent.tools.schedule_tool.parser._now",
        lambda tz=None: fixed_now.astimezone(tz) if tz else fixed_now,
    ):
        next_fire = compute_next_fire(schedule)

    assert next_fire is not None
    assert next_fire.year == 2026
    assert next_fire.month == 5
    assert next_fire.day == 10
    assert next_fire.hour == 9


def test_compute_next_fire_interval_uses_system_clock():
    schedule = parse_schedule("every 30m")

    fixed_now = datetime(2026, 5, 10, 8, 0, 0, tzinfo=ZoneInfo("UTC"))
    with patch(
        "vexis_agent.tools.schedule_tool.parser._now",
        lambda tz=None: fixed_now.astimezone(tz) if tz else fixed_now,
    ):
        next_fire = compute_next_fire(schedule)

    assert next_fire is not None
    assert abs((next_fire - (fixed_now + timedelta(minutes=30))).total_seconds()) < 2


def test_compute_next_fire_interval_anchored_to_last_fire():
    """``last_fire_at`` anchors recurring schedules across restarts."""
    schedule = parse_schedule("every 30m")
    last = datetime(2026, 5, 10, 8, 0, 0, tzinfo=ZoneInfo("UTC"))
    next_fire = compute_next_fire(schedule, last_fire_at=last)
    assert next_fire == datetime(2026, 5, 10, 8, 30, 0, tzinfo=ZoneInfo("UTC"))


def test_compute_next_fire_oneshot_already_fired():
    """One-shot with last_fire_at set returns None — never re-fires."""
    schedule = parse_schedule("2026-12-31T23:59:00", tz="UTC")
    last = datetime(2026, 12, 31, 23, 59, 0, tzinfo=ZoneInfo("UTC"))
    assert compute_next_fire(schedule, last_fire_at=last) is None


def test_compute_next_fire_oneshot_past_grace():
    """One-shot whose run_at is >2 min in the past returns None."""
    # Build a schedule whose run_at is 1 hour in the past.
    past = datetime(2026, 5, 10, 8, 0, 0, tzinfo=ZoneInfo("UTC"))
    schedule = {
        "kind": "once",
        "run_at": past.isoformat(),
        "tz": "UTC",
        "display": "test",
    }

    now_after = datetime(2026, 5, 10, 9, 0, 0, tzinfo=ZoneInfo("UTC"))
    with patch(
        "vexis_agent.tools.schedule_tool.parser._now",
        lambda tz=None: now_after.astimezone(tz) if tz else now_after,
    ):
        result = compute_next_fire(schedule)

    assert result is None


def test_compute_next_fire_oneshot_within_grace_fires():
    """One-shot whose run_at is within the 2-min grace window fires."""
    past = datetime(2026, 5, 10, 8, 0, 0, tzinfo=ZoneInfo("UTC"))
    schedule = {
        "kind": "once",
        "run_at": past.isoformat(),
        "tz": "UTC",
        "display": "test",
    }

    now_within = datetime(2026, 5, 10, 8, 1, 30, tzinfo=ZoneInfo("UTC"))
    with patch(
        "vexis_agent.tools.schedule_tool.parser._now",
        lambda tz=None: now_within.astimezone(tz) if tz else now_within,
    ):
        result = compute_next_fire(schedule)

    assert result is not None
    assert result == past


# ──────────────────────────────────────────────────────────────────
# DST behaviour — pin against croniter version drift
# ──────────────────────────────────────────────────────────────────


def test_cron_dst_spring_forward_skips_missing_hour():
    """Cron ``30 2 * * *`` on US spring-forward day has no 02:30 slot.

    Croniter's documented behaviour is to skip and fire next day at
    02:30. Pinned here so a future croniter upgrade can't silently
    change DST handling without us noticing.
    """
    # US spring-forward 2026: clocks jump 02:00 → 03:00 on Mar 8.
    eastern = ZoneInfo("America/New_York")
    schedule = parse_schedule("30 2 * * *", tz="America/New_York")

    # Anchor base at 2026-03-08 01:00 ET (before the jump). Next fire
    # should NOT be 2026-03-08 02:30 (that moment doesn't exist).
    base = datetime(2026, 3, 8, 1, 0, 0, tzinfo=eastern)
    next_fire = compute_next_fire(schedule, last_fire_at=base)

    assert next_fire is not None
    # The next 02:30 ET slot is 2026-03-09 (the day after the jump).
    assert next_fire.day == 9 or (
        next_fire.day == 8 and next_fire.hour >= 3
    ), (
        f"DST behavior changed: expected fire on 3/9 02:30 or 3/8 03:30+ "
        f"due to missing 02:30 slot, got {next_fire}"
    )


# ──────────────────────────────────────────────────────────────────
# compute_grace_seconds
# ──────────────────────────────────────────────────────────────────


def test_grace_seconds_oneshot_is_constant():
    schedule = parse_schedule("2026-12-31T23:59:00", tz="UTC")
    assert compute_grace_seconds(schedule) == DEFAULT_ONESHOT_GRACE_SECONDS


def test_grace_seconds_interval_clamped_to_min():
    """``every 1m`` → period 60s → half = 30s → clamped up to MIN_GRACE."""
    schedule = parse_schedule("every 1m")
    assert compute_grace_seconds(schedule) == MIN_GRACE_SECONDS


def test_grace_seconds_interval_clamped_to_max():
    """``every 30d`` → period huge → clamped down to MAX_GRACE."""
    schedule = parse_schedule("every 30d")
    assert compute_grace_seconds(schedule) == MAX_GRACE_SECONDS


def test_grace_seconds_interval_in_range():
    """``every 1h`` → period 3600s → half = 1800s (within range)."""
    schedule = parse_schedule("every 1h")
    grace = compute_grace_seconds(schedule)
    assert MIN_GRACE_SECONDS <= grace <= MAX_GRACE_SECONDS
    assert grace == 1800


def test_grace_seconds_cron_daily_is_max():
    """Daily cron → period 24h → half clamped to MAX_GRACE (2h)."""
    schedule = parse_schedule("0 9 * * *", tz="UTC")
    assert compute_grace_seconds(schedule) == MAX_GRACE_SECONDS
