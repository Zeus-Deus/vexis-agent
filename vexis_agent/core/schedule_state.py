"""Persistent state for the /schedule command — Vexis's cron equivalent.

A schedule is a user-defined cron / interval / one-shot job whose
prompt fires into the chat FIFO when due. State persists at
``~/.vexis/schedules.json`` keyed by 12-char id, mirroring upstream
(`cron/jobs.py:43-65, 401-447`) on storage shape and
:class:`core.goal_state.GoalStateStore` on locking semantics.

This module is deliberately thin and brain-agnostic. It owns:

  * the :class:`ScheduleState` dataclass and its (de)serialization,
  * the :class:`ScheduleStore` IO surface (load / save / list / update_atomic).

Everything orchestrational — the tick loop, the MCP tool, the
slash-command handler, the dashboard endpoints — lives elsewhere.

The store does NOT import the parser. It stores the already-parsed
``schedule`` dict produced by
:func:`vexis_agent.tools.schedule_tool.parser.parse_schedule` and
treats ``next_fire_at`` as an opaque ISO string — the manager (Day 2)
calls ``parser.compute_next_fire`` and feeds the result back in. This
split keeps Day 1's storage code free of croniter and free of any
"how do schedules compute" knowledge — easier to test, easier to
swap parsers later if needed.

Locking model mirrors :class:`core.goal_state.GoalStateStore`:
sidecar ``.lock`` file + ``fcntl.flock(LOCK_EX)`` for writers,
atomic temp-rename so readers see either old or new state. Reads
do not lock.

Design citation: ``.plans/scheduling-and-provider-abstraction-research.md``
§4 (Storage), Day 1.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class TerminalScheduleError(Exception):
    """Raised when a pause / resume / clear tried to mutate a schedule
    whose on-disk status is already terminal (``expired`` or ``cleared``).

    The dashboard surfaces this as ``409 Conflict``; Telegram replies
    with a short "Schedule already cleared" line. Both refuse to revive
    a terminal schedule — once ``expired`` lands (one-shot fired or
    cron auto-paused after consecutive errors), the user must create
    a new schedule; once ``cleared`` lands, it stays cleared for audit
    even if the same prompt is re-scheduled.

    Mirrors :class:`core.goal_state.TerminalGoalError` so the dashboard
    and Telegram surfaces can use the same shape.
    """

    def __init__(self, status: str, *, schedule_id: str = "") -> None:
        self.status = status
        self.schedule_id = schedule_id
        super().__init__(f"schedule already {status}")


# ──────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────

# Hard cap on total schedules across all statuses except ``cleared``
# (cleared entries are audit-retained and don't count against the cap).
# Mirrors `.plans/.../Operational polish` decision: 100 active+paused+expired,
# enforced by the MCP tool's create path, not by the store itself —
# the store would let you write a million if you tried.
DEFAULT_MAX_TOTAL = 100

# Hard cap on prompt length (chars). The MCP tool enforces this; the
# store accepts whatever it's given so manual recovery / migrations
# can write arbitrary lengths.
DEFAULT_MAX_PROMPT_LENGTH = 2000

# After this many consecutive enqueue failures the manager auto-pauses
# the schedule with ``paused_reason="auto: errors"``. Mirrors openclaw's
# ``MAX_CONSECUTIVE_ERRORS`` posture (`src/cron/service/timer.ts`).
DEFAULT_MAX_CONSECUTIVE_ERRORS = 5

# Stuck-marker TTL — a ``running_at`` marker older than this came
# from a crashed fire; the manager clears it on next tick / startup.
# 5 min mirrors the design doc.
DEFAULT_STUCK_RUN_TTL_SECONDS = 300

# Status enum. ``active`` → eligible to fire; ``paused`` → tick skips;
# ``expired`` → one-shot fired OR consecutive errors auto-paused
# (terminal); ``cleared`` → user clear (terminal, audit-retained).
_VALID_STATUSES: frozenset[str] = frozenset(
    {"active", "paused", "expired", "cleared"}
)

_TERMINAL_STATUSES: frozenset[str] = frozenset({"expired", "cleared"})

_VALID_LAST_STATUSES: frozenset[str] = frozenset({"ok", "error"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def new_schedule_id() -> str:
    """Generate a fresh 12-char hex id. Mirrors the upstream pattern uuid4().hex[:12]."""
    return uuid.uuid4().hex[:12]


# ──────────────────────────────────────────────────────────────────
# ScheduleState dataclass
# ──────────────────────────────────────────────────────────────────


@dataclass
class ScheduleState:
    """One scheduled job. Mirror of upstream ``jobs.json`` row, slimmed.

    upstream fields we DROPPED (out of scope for v1):
      * ``skills`` / ``model`` / ``provider`` / ``base_url`` — vexis is
        single-brain per daemon; per-schedule model override deferred.
      * ``script`` / ``no_agent`` / ``context_from`` / ``enabled_toolsets``
        / ``workdir`` — productivity-suite features; vexis fires into
        the existing chat.
      * ``deliver`` — vexis has one delivery target (the chat), so the
        field is implicit.
      * ``repeat`` — the upstream "fire N times then stop" is one-shot's
        natural fall-out plus recurring's natural infinite; the in-
        between case isn't worth the dataclass field for v1.

    Status flow:

      * ``active`` — tick loop will fire when ``next_fire_at <= now``.
      * ``paused`` — tick loop skips; ``schedule_resume`` flips back.
      * ``expired`` — one-shot fired OR consecutive errors auto-paused
        with ``paused_reason="auto: errors"``. Terminal.
      * ``cleared`` — user issued ``schedule_clear`` / ``/schedule clear``.
        Terminal. Record retained for audit.
    """

    id: str
    chat_id: int
    schedule: dict[str, Any]  # parser output: {kind, ...}
    schedule_display: str
    prompt: str
    name: str | None = None
    next_fire_at: datetime | None = None
    last_fire_at: datetime | None = None
    last_status: str | None = None  # "ok" | "error" | None
    last_error: str | None = None
    consecutive_errors: int = 0
    running_at: datetime | None = None
    status: str = "active"
    paused_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    owner_session_uuid: str | None = None
    # Free-form metadata sink for future fields without bumping schema.
    # Day 1 leaves this empty; Day 2+ may add e.g. ``goal_uuid`` or
    # ``brain_kind_at_create`` here.
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["next_fire_at"] = _iso(self.next_fire_at)
        d["last_fire_at"] = _iso(self.last_fire_at)
        d["running_at"] = _iso(self.running_at)
        d["created_at"] = _iso(self.created_at)
        d["updated_at"] = _iso(self.updated_at)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ScheduleState":
        """Tolerant inverse of :meth:`to_dict`. Coerces malformed
        scalars to safe defaults rather than raising — one bad row
        shouldn't poison a multi-schedule load.
        """
        status_raw = raw.get("status", "active")
        status = status_raw if status_raw in _VALID_STATUSES else "active"

        last_status_raw = raw.get("last_status")
        last_status: str | None = (
            last_status_raw if last_status_raw in _VALID_LAST_STATUSES else None
        )

        try:
            consecutive_errors = int(raw.get("consecutive_errors", 0) or 0)
        except (TypeError, ValueError):
            consecutive_errors = 0
        if consecutive_errors < 0:
            consecutive_errors = 0

        try:
            chat_id = int(raw.get("chat_id", 0) or 0)
        except (TypeError, ValueError):
            chat_id = 0

        schedule_raw = raw.get("schedule")
        schedule = schedule_raw if isinstance(schedule_raw, dict) else {}

        meta_raw = raw.get("meta")
        meta = meta_raw if isinstance(meta_raw, dict) else {}

        return cls(
            id=str(raw.get("id", "") or ""),
            chat_id=chat_id,
            schedule=schedule,
            schedule_display=str(raw.get("schedule_display", "") or ""),
            prompt=str(raw.get("prompt", "") or ""),
            name=raw.get("name") or None,
            next_fire_at=_parse_iso(raw.get("next_fire_at")),
            last_fire_at=_parse_iso(raw.get("last_fire_at")),
            last_status=last_status,
            last_error=raw.get("last_error") or None,
            consecutive_errors=consecutive_errors,
            running_at=_parse_iso(raw.get("running_at")),
            status=status,
            paused_reason=raw.get("paused_reason") or None,
            created_at=_parse_iso(raw.get("created_at")),
            updated_at=_parse_iso(raw.get("updated_at")),
            owner_session_uuid=raw.get("owner_session_uuid") or None,
            meta=meta,
        )


# ──────────────────────────────────────────────────────────────────
# ScheduleStore — persistence
# ──────────────────────────────────────────────────────────────────


class ScheduleStore:
    """Owns ``schedules.json``. One row per schedule id.

    Mirrors :class:`core.goal_state.GoalStateStore` 1:1 in locking
    semantics: sidecar ``.lock`` + ``fcntl.flock(LOCK_EX)`` for
    writers, atomic temp-rename. Reads do not lock; the atomic rename
    guarantees a consistent snapshot.

    On-disk shape (``SCHEMA_VERSION = 1``):

    .. code-block:: json

       {
         "version": 1,
         "schedules": {
           "<id>": { ...ScheduleState.to_dict()... }
         }
       }

    Higher schema versions on disk are treated as empty (writers refuse
    to overwrite a future-version file rather than corrupting it).
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_suffix(path.suffix + ".lock")

    # ----- read paths -------------------------------------------------

    def _load_raw(self) -> dict[str, dict[str, Any]]:
        """Return the ``schedules`` section as a plain dict, or ``{}``
        on missing / corrupt / future-version file. Never raises.
        """
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning(
                "schedules.json corrupt at %s; treating as empty", self._path
            )
            return {}
        if not isinstance(data, dict):
            return {}
        version = data.get("version")
        if version != self.SCHEMA_VERSION:
            log.warning(
                "schedules.json at %s has unrecognised version %r; "
                "treating as empty (writers will refuse to overwrite "
                "to avoid corrupting a future-format file)",
                self._path,
                version,
            )
            return {}
        schedules = data.get("schedules")
        if not isinstance(schedules, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for k, v in schedules.items():
            if isinstance(k, str) and isinstance(v, dict):
                out[k] = v
        return out

    def load(self, schedule_id: str) -> ScheduleState | None:
        """Return the schedule for ``schedule_id``, or ``None`` if absent.

        Terminal (``expired``/``cleared``) rows ARE returned — callers
        decide how to treat them. ``list_active`` etc. filter.
        """
        if not schedule_id:
            return None
        raw = self._load_raw().get(schedule_id)
        if not isinstance(raw, dict):
            return None
        try:
            return ScheduleState.from_dict(raw)
        except Exception as exc:
            log.warning(
                "schedules.json: could not parse row for %s: %s",
                schedule_id,
                exc,
            )
            return None

    def list_all(self) -> list[ScheduleState]:
        """Return every parseable row, in insertion order.

        Used by the dashboard and by ``/schedule list``. Sorting and
        status filtering happen at the call site.
        """
        out: list[ScheduleState] = []
        for raw in self._load_raw().values():
            try:
                out.append(ScheduleState.from_dict(raw))
            except Exception:
                continue
        return out

    def list_by_status(self, *statuses: str) -> list[ScheduleState]:
        """Return rows whose ``status`` is in ``statuses``."""
        wanted = frozenset(statuses)
        return [s for s in self.list_all() if s.status in wanted]

    def list_active(self) -> list[ScheduleState]:
        """Convenience: ``list_by_status("active")``."""
        return self.list_by_status("active")

    def list_due(self, now: datetime | None = None) -> list[ScheduleState]:
        """Return active schedules whose ``next_fire_at <= now``.

        ``now`` defaults to ``datetime.now(timezone.utc)`` — the
        system-clock invariant applies here too. Caller can pass a
        frozen now for testing.
        """
        if now is None:
            now = _utc_now()
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        out: list[ScheduleState] = []
        for state in self.list_active():
            if state.next_fire_at is None:
                continue
            nfa = state.next_fire_at
            if nfa.tzinfo is None:
                nfa = nfa.replace(tzinfo=timezone.utc)
            if nfa <= now:
                out.append(state)
        return out

    def total_count(self, *, exclude_cleared: bool = True) -> int:
        """Total schedule rows. Used by the MCP tool's cap check."""
        rows = self.list_all()
        if exclude_cleared:
            return sum(1 for s in rows if s.status != "cleared")
        return len(rows)

    def resolve_id_prefix(self, prefix: str) -> str | None:
        """Resolve a 3+-char prefix to a full id, or ``None`` on
        no-match / ambiguous-match. Mirrors `git checkout abc` UX.

        Returns ``None`` if the prefix is shorter than 3 chars, no
        schedule matches, or multiple schedules match. The caller
        distinguishes "no match" from "ambiguous" by checking
        ``list_all`` itself; this method just gives the single hit
        when one exists.
        """
        if not prefix or len(prefix) < 3:
            return None
        prefix = prefix.lower()
        hits = [s.id for s in self.list_all() if s.id.startswith(prefix)]
        if len(hits) == 1:
            return hits[0]
        return None

    # ----- write paths ------------------------------------------------

    def save(self, state: ScheduleState) -> None:
        """Write/replace the row for ``state.id``. Atomic.

        Stamps ``updated_at`` to now-UTC. Caller is responsible for
        ``created_at`` on first save (and for ``next_fire_at`` — the
        store doesn't compute it).
        """
        if not state.id:
            raise ValueError("ScheduleState.id is empty; cannot save")
        if state.created_at is None:
            state.created_at = _utc_now()
        state.updated_at = _utc_now()
        self._mutate(
            lambda schedules: schedules.__setitem__(state.id, state.to_dict())
        )

    def update_atomic(
        self,
        schedule_id: str,
        mutator: Callable[[ScheduleState], ScheduleState],
        *,
        refuse_terminal: bool = True,
    ) -> ScheduleState:
        """Read-modify-write under fcntl.flock + atomic temp-rename.

        ``mutator`` is called with the **disk** state at lock-acquire
        time (NOT any in-memory copy the caller holds) and must return
        the new :class:`ScheduleState` to persist. The whole sequence
        runs under ``LOCK_EX`` so concurrent writers serialize.

        ``refuse_terminal=True`` raises :class:`TerminalScheduleError`
        when the disk state's status is ``expired`` or ``cleared`` at
        lock-acquire time — used by pause/resume/clear which must
        refuse to revive a terminal schedule.

        ``refuse_terminal=False`` skips the guard for callers (like
        the manager's ``mark_fired``) whose terminal verdicts must
        overwrite any concurrent non-terminal write.

        Raises :class:`KeyError` if no row exists for ``schedule_id``.
        """
        captured: list[ScheduleState | None] = [None]
        terminal: list[str | None] = [None]
        missing: list[bool] = [False]

        def _do(schedules: dict) -> None:
            row = schedules.get(schedule_id)
            if row is None:
                missing[0] = True
                return
            try:
                current = ScheduleState.from_dict(row)
            except Exception:
                missing[0] = True
                return
            if refuse_terminal and current.status in _TERMINAL_STATUSES:
                terminal[0] = current.status
                return
            new_state = mutator(current)
            new_state.updated_at = _utc_now()
            schedules[schedule_id] = new_state.to_dict()
            captured[0] = new_state

        self._mutate(_do)
        if missing[0]:
            raise KeyError(schedule_id)
        if terminal[0] is not None:
            raise TerminalScheduleError(
                terminal[0], schedule_id=schedule_id
            )
        result = captured[0]
        assert result is not None
        return result

    def clear(self, schedule_id: str) -> None:
        """Mark a schedule as cleared (soft delete, audit-retained).

        Mirrors :meth:`core.goal_state.GoalStateStore.clear`. Already-
        terminal rows are no-ops (we don't re-clear a cleared row, and
        we leave ``expired`` rows alone — they're already terminal).
        """
        if not schedule_id:
            return
        existing = self.load(schedule_id)
        if existing is None:
            return
        if existing.status in _TERMINAL_STATUSES:
            return
        existing.status = "cleared"
        existing.next_fire_at = None
        existing.updated_at = _utc_now()
        # Direct save — clear is not subject to refuse_terminal since
        # we just checked above. Saves us the update_atomic dance.
        self._mutate(
            lambda schedules: schedules.__setitem__(
                schedule_id, existing.to_dict()
            )
        )

    # ----- internal ---------------------------------------------------

    def _mutate(self, mutator) -> None:
        """Read-modify-write under fcntl.flock with atomic temp-rename.

        Identical idiom to
        :meth:`core.goal_state.GoalStateStore._mutate` — sidecar
        ``.lock`` file, ``LOCK_EX``, ``json.dump`` to ``.tmp``,
        ``fsync``, ``os.replace``.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            current = self._load_raw()
            mutator(current)
            payload = {
                "version": self.SCHEMA_VERSION,
                "schedules": current,
            }
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(
                    payload, fh, indent=2, sort_keys=True, ensure_ascii=False
                )
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


__all__ = [
    "DEFAULT_MAX_CONSECUTIVE_ERRORS",
    "DEFAULT_MAX_PROMPT_LENGTH",
    "DEFAULT_MAX_TOTAL",
    "DEFAULT_STUCK_RUN_TTL_SECONDS",
    "ScheduleState",
    "ScheduleStore",
    "TerminalScheduleError",
    "new_schedule_id",
]
