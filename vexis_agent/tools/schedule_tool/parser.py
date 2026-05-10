"""Schedule expression parser — brain-tool-internal, never user-facing.

Ports Hermes' ``cron/jobs.py:parse_schedule`` (`cron/jobs.py:124-270`) and
``compute_next_run`` (`:351-394`) into a vexis-shaped module. The
slash command does NOT import this — the slash command dispatches
the user's raw text into the brain FIFO and lets the brain decide
what cron expression to call ``schedule_create`` with. This parser
validates what the *brain* produces; it never sees raw user text.

Four accepted input shapes (mirroring Hermes):

  * ``30m`` / ``2h`` / ``1d`` → one-shot from now
  * ``every 30m`` / ``every 2h`` → recurring interval
  * ``0 9 * * 1-5`` → cron expression (5 fields, croniter-validated)
  * ``2026-02-03T14:00`` → one-shot at ISO timestamp

System-clock invariant (`.plans/scheduling-and-provider-abstraction-research.md`
Decisions locked block): every code path here that needs "now"
calls ``datetime.now(tz=resolved)`` at the moment it runs. No
function accepts a brain-supplied "now" parameter — the brain
cannot fabricate the current date.

Sub-minute reject: ``every 30s`` / ``every 0m`` / ``every 0`` are
rejected at parse time with a clear message pointing to the
1-minute floor. Cron is wall-clock-aligned to whole minutes;
silently rounding ``30s`` up to ``1m`` would confuse users.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────

# Match Hermes (`cron/jobs.py:46`) — one-shot jobs scheduled more than
# 2 minutes ago at first-tick time fast-forward / expire rather than
# fire. Anything within 2 minutes still fires once.
DEFAULT_ONESHOT_GRACE_SECONDS = 120

# Mirror Hermes (`cron/jobs.py:326-327`). Grace window is
# ``min(max(period/2, 120), 7200)`` — half the schedule period clamped
# to [2 min, 2 hr]. Daily jobs missed by ≤2 hours catch up; very
# frequent jobs fast-forward quickly.
MIN_GRACE_SECONDS = 120
MAX_GRACE_SECONDS = 7200

# Sub-minute rejection floor. Cron's native resolution is 1 minute;
# accepting "every 30s" would silently round up and confuse the user.
_MIN_INTERVAL_MINUTES = 1


def _now(tz: Optional[ZoneInfo] = None) -> datetime:
    """Single chokepoint for "current wall-clock time".

    Every function in this module that needs ``now`` calls this helper.
    Tests monkeypatch ``parser._now`` to inject a frozen time without
    having to mock the entire ``datetime`` module (which breaks croniter
    because it relies on ``isinstance(base, datetime)`` internally —
    a MagicMock can't satisfy that check).

    System-clock invariant: there is no caller-supplied "now". The
    brain cannot fabricate the current date by passing a parameter —
    this function reads the OS clock directly.
    """
    if tz is None:
        return datetime.now(timezone.utc)
    return datetime.now(tz=tz)


class ScheduleParseError(ValueError):
    """Raised when ``parse_schedule`` cannot interpret its input.

    Subclasses :class:`ValueError` so existing exception handlers that
    catch ``ValueError`` keep working. The ``suggestion`` attribute
    carries the brain-facing hint the MCP tool surfaces back to the
    model — kept separate from the message so tests can pin the hint
    text without depending on the full message string.
    """

    def __init__(self, message: str, *, suggestion: str = "") -> None:
        self.suggestion = suggestion
        super().__init__(message)


# ──────────────────────────────────────────────────────────────────
# Duration parsing (``30m`` / ``2h`` / ``1d``)
# ──────────────────────────────────────────────────────────────────

_DURATION_RE = re.compile(
    r"^(\d+)\s*(s|sec|secs|second|seconds|"
    r"m|min|mins|minute|minutes|"
    r"h|hr|hrs|hour|hours|"
    r"d|day|days)$",
    re.IGNORECASE,
)


def _parse_duration_minutes(s: str) -> int:
    """Convert ``30m`` / ``2h`` / ``1d`` → minutes.

    Raises :class:`ScheduleParseError` on malformed input or on
    sub-minute resolution (``30s``, ``0m``, ``0h``). The error message
    points to the 1-minute floor so the brain knows to round up
    explicitly rather than retrying.
    """
    text = s.strip().lower()
    match = _DURATION_RE.match(text)
    if not match:
        raise ScheduleParseError(
            f"invalid duration: {s!r}. use '30m', '2h', or '1d'",
            suggestion="duration must look like '30m', '2h', or '1d'",
        )

    value = int(match.group(1))
    unit_word = match.group(2)[0]  # first char: s/m/h/d

    if unit_word == "s":
        # Seconds → reject. Cron's native resolution is 1 minute;
        # rounding 30s up to 1m would silently mis-fire.
        raise ScheduleParseError(
            f"schedule resolution is 1 minute (cron's native floor); "
            f"use 'every 1m' or longer, not {s!r}",
            suggestion="use 'every 1m' or longer; sub-minute schedules are not supported",
        )

    multipliers = {"m": 1, "h": 60, "d": 1440}
    minutes = value * multipliers[unit_word]

    if minutes < _MIN_INTERVAL_MINUTES:
        raise ScheduleParseError(
            f"schedule resolution is 1 minute; {s!r} rounds to "
            f"{minutes} minute(s) which is below the floor",
            suggestion="use 'every 1m' or longer",
        )

    return minutes


# ──────────────────────────────────────────────────────────────────
# Schedule parsing — the four shapes
# ──────────────────────────────────────────────────────────────────

# Cron field charset: digits, *, comma, dash, slash, ?, L, W, # (the
# last four are used by croniter's extended syntax for "last day of
# month", "weekday nearest", "nth weekday", etc.).
_CRON_FIELD_RE = re.compile(r"^[\d\*\-,/\?LW#]+$", re.IGNORECASE)

# ISO timestamp heuristic: starts with YYYY-MM-DD, possibly with T or space.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _resolve_tz(tz: Optional[str]) -> tuple[ZoneInfo, str]:
    """Resolve the caller's tz argument to a ZoneInfo + canonical name.

    ``None`` → daemon-local timezone (captured via
    ``datetime.now().astimezone().tzinfo``). The returned name is the
    IANA string so it's stable across daemon migration to a different
    box (we store the resolved name, not "wherever the OS says now").

    Raises :class:`ScheduleParseError` with a suggestion when the
    caller passes an invalid IANA name.
    """
    if tz is None:
        # Capture daemon-local tz at parse time. astimezone() with no
        # arg returns local; .tzinfo gives the zoneinfo. On Linux/macOS
        # this is typically a ZoneInfo; we coerce to ZoneInfo via the
        # tzname() string to keep the canonical IANA path.
        local = _now().astimezone().tzinfo
        # Try to extract an IANA name. ZoneInfo has .key; pytz/others
        # may not. Fall back to UTC offset string if we can't.
        name = getattr(local, "key", None) or str(local)
        try:
            return ZoneInfo(name), name
        except ZoneInfoNotFoundError:
            # System tz isn't a named IANA zone (rare — bare offset).
            # Fall back to UTC and log; this is preferable to crashing
            # on a daemon-local schedule whose tz we can't name.
            log.warning(
                "could not resolve daemon-local timezone %r as IANA; "
                "falling back to UTC for schedule storage",
                name,
            )
            return ZoneInfo("UTC"), "UTC"

    try:
        return ZoneInfo(tz), tz
    except ZoneInfoNotFoundError as exc:
        raise ScheduleParseError(
            f"unknown timezone: {tz!r}. use an IANA name like "
            f"'Europe/Berlin', 'America/New_York', or 'Asia/Tokyo'",
            suggestion=(
                f"{tz!r} is not a valid IANA timezone; common examples: "
                "Europe/Berlin, America/New_York, Asia/Tokyo, UTC"
            ),
        ) from exc


def parse_schedule(spec: str, *, tz: Optional[str] = None) -> dict[str, Any]:
    """Parse ``spec`` into the canonical schedule dict.

    ``spec`` is whatever the brain emits from its ``schedule_create``
    tool call. ``tz`` is the brain's explicit timezone override
    (``"Asia/Tokyo"``) or ``None`` for daemon-local.

    Returns a dict with at minimum ``kind`` and a kind-specific payload:

    * ``{"kind": "once", "run_at": ISO, "tz": "<iana>", "display": "..."}``
    * ``{"kind": "interval", "minutes": N, "display": "every Nm"}`` (tz omitted — interval is wall-clock-agnostic)
    * ``{"kind": "cron", "expr": "0 9 * * *", "tz": "<iana>", "display": "0 9 * * *"}``

    Raises :class:`ScheduleParseError` on any malformed input. The
    error's ``suggestion`` attribute carries a brain-facing hint.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ScheduleParseError(
            "schedule expression is empty",
            suggestion="provide a duration ('30m'), interval ('every 2h'), "
            "cron ('0 9 * * 1-5'), or ISO timestamp ('2026-02-03T14:00')",
        )

    text = spec.strip()
    original = text
    lower = text.lower()

    # ── interval: "every <duration>" ─────────────────────────────
    if lower.startswith("every "):
        duration = text[6:].strip()
        minutes = _parse_duration_minutes(duration)
        return {
            "kind": "interval",
            "minutes": minutes,
            "display": f"every {minutes}m",
        }

    # ── cron: 5 or 6 space-separated fields ──────────────────────
    parts = text.split()
    if len(parts) >= 5 and all(_CRON_FIELD_RE.match(p) for p in parts[:5]):
        # Croniter validates the expression syntax against base time.
        zone, zone_name = _resolve_tz(tz)
        try:
            croniter(text, _now(zone))
        except Exception as exc:
            raise ScheduleParseError(
                f"invalid cron expression {text!r}: {exc}",
                suggestion="cron format is '<minute> <hour> <day-of-month> "
                "<month> <day-of-week>', e.g. '0 9 * * 1-5' for 9am weekdays",
            ) from exc
        return {
            "kind": "cron",
            "expr": text,
            "tz": zone_name,
            "display": text,
        }

    # ── one-shot: ISO timestamp ──────────────────────────────────
    if "T" in text or _ISO_DATE_RE.match(text):
        # Accept trailing 'Z' as UTC marker.
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ScheduleParseError(
                f"invalid ISO timestamp {text!r}: {exc}",
                suggestion="ISO timestamp format is 'YYYY-MM-DDTHH:MM:SS', "
                "optionally with 'Z' or '+HH:MM' suffix",
            ) from exc
        # Naive → caller-tz (defaulting to daemon-local). Aware → kept.
        if dt.tzinfo is None:
            zone, zone_name = _resolve_tz(tz)
            dt = dt.replace(tzinfo=zone)
        else:
            # Aware input — capture its tz name for storage. If the
            # caller passed an explicit tz, that's a conflict; warn but
            # honor the input's tz (it was explicit).
            zone_name = (
                getattr(dt.tzinfo, "key", None)
                or dt.tzinfo.tzname(dt)
                or "UTC"
            )
            if tz is not None and tz != zone_name:
                log.warning(
                    "ISO timestamp %s carries tz %s; ignoring caller tz=%r",
                    text,
                    zone_name,
                    tz,
                )
        return {
            "kind": "once",
            "run_at": dt.isoformat(),
            "tz": zone_name,
            "display": f"once at {dt.strftime('%Y-%m-%d %H:%M %Z').strip()}",
        }

    # ── one-shot: relative duration ──────────────────────────────
    #
    # If the input *looks like* a duration attempt (matches the regex),
    # propagate any parse error — including the sub-minute reject —
    # rather than falling through to the generic shape-help error. Only
    # truly unrecognised inputs ("hello world") get the generic error.
    if _DURATION_RE.match(text):
        minutes = _parse_duration_minutes(text)  # may raise ScheduleParseError
        zone, zone_name = _resolve_tz(tz)
        run_at = _now(zone) + timedelta(minutes=minutes)
        return {
            "kind": "once",
            "run_at": run_at.isoformat(),
            "tz": zone_name,
            "display": f"once in {original}",
        }

    raise ScheduleParseError(
        f"invalid schedule {original!r}",
        suggestion=(
            "supported shapes:\n"
            "  - duration: '30m', '2h', '1d' (one-shot from now)\n"
            "  - interval: 'every 30m', 'every 2h' (recurring)\n"
            "  - cron: '0 9 * * 1-5' (cron expression)\n"
            "  - timestamp: '2026-02-03T14:00' (one-shot at time)"
        ),
    )


# ──────────────────────────────────────────────────────────────────
# Next-fire computation
# ──────────────────────────────────────────────────────────────────


def compute_next_fire(
    schedule: dict[str, Any],
    last_fire_at: Optional[datetime] = None,
) -> Optional[datetime]:
    """Return the next time this schedule should fire, or ``None``.

    ``schedule`` is the dict produced by :func:`parse_schedule`.
    ``last_fire_at`` is the last fire time, used to anchor recurring
    schedules across restarts (mirrors Hermes `cron/jobs.py:383-389`).

    Returns ``None`` for:
      * One-shot schedules that have already fired.
      * One-shot schedules whose ``run_at`` is past the 2-minute grace.

    System-clock invariant: this function reads ``datetime.now()`` at
    invocation time. Callers do NOT pass a "now" parameter — the
    brain cannot fabricate the current date.
    """
    kind = schedule.get("kind")

    if kind == "once":
        if last_fire_at is not None:
            return None  # one-shot already fired
        run_at_raw = schedule.get("run_at")
        if not run_at_raw:
            return None
        try:
            run_at = datetime.fromisoformat(str(run_at_raw))
        except ValueError:
            return None
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        # Grace window for past-due one-shots: within 2 min of now → fire.
        now = _now(run_at.tzinfo)
        if run_at < now - timedelta(seconds=DEFAULT_ONESHOT_GRACE_SECONDS):
            return None
        return run_at

    if kind == "interval":
        minutes = int(schedule.get("minutes", 0))
        if minutes < _MIN_INTERVAL_MINUTES:
            return None
        if last_fire_at is not None:
            base = last_fire_at
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
        else:
            base = _now(timezone.utc)
        return base + timedelta(minutes=minutes)

    if kind == "cron":
        expr = schedule.get("expr")
        tz_name = schedule.get("tz") or "UTC"
        try:
            zone = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            log.warning(
                "schedule tz %r unknown at compute time; falling back to UTC",
                tz_name,
            )
            zone = ZoneInfo("UTC")
        if last_fire_at is not None:
            base = last_fire_at
            if base.tzinfo is None:
                base = base.replace(tzinfo=zone)
            else:
                base = base.astimezone(zone)
        else:
            base = _now(zone)
        try:
            cron = croniter(expr, base)
            return cron.get_next(datetime)
        except Exception as exc:
            log.warning(
                "croniter could not compute next fire for %r: %s", expr, exc
            )
            return None

    return None


def compute_grace_seconds(schedule: dict[str, Any]) -> int:
    """How late a schedule can be and still catch up vs. fast-forward.

    Mirrors Hermes (`cron/jobs.py:319-348`). Returns
    ``min(max(period/2, MIN_GRACE_SECONDS), MAX_GRACE_SECONDS)``.

    One-shot schedules get :data:`DEFAULT_ONESHOT_GRACE_SECONDS`
    regardless of period (Hermes' rule).
    """
    kind = schedule.get("kind")

    if kind == "once":
        return DEFAULT_ONESHOT_GRACE_SECONDS

    if kind == "interval":
        period = int(schedule.get("minutes", 1)) * 60
        return max(MIN_GRACE_SECONDS, min(period // 2, MAX_GRACE_SECONDS))

    if kind == "cron":
        expr = schedule.get("expr")
        tz_name = schedule.get("tz") or "UTC"
        try:
            zone = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        try:
            now = _now(zone)
            cron = croniter(expr, now)
            first = cron.get_next(datetime)
            second = cron.get_next(datetime)
            period = int((second - first).total_seconds())
            return max(MIN_GRACE_SECONDS, min(period // 2, MAX_GRACE_SECONDS))
        except Exception:
            return MIN_GRACE_SECONDS

    return MIN_GRACE_SECONDS
