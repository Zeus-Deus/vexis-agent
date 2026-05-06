"""Persistent state for the /goal command — per-session standing goals.

A standing goal is a free-form user objective that survives across
turns. After every brain turn the goal hook calls
``core.goal_judge.judge_goal`` to ask whether the goal is satisfied;
if not (and the budget is intact) the hook enqueues a continuation
prompt back into the same Telegram chat. State persists at
``~/.vexis/goals.json`` keyed by Claude session UUID, so a daemon
restart leaves the goal in place — the next user message rebinds
the manager and resumes the loop.

This module is deliberately thin. It owns:

  * the :class:`GoalState` dataclass and its (de)serialization shape,
  * the :class:`GoalStateStore` IO surface (load / save / clear / list_active).

Everything orchestrational — the manager, the post-turn hook, the
slash-command surface — lives elsewhere (Day 2). The split mirrors
``core/learning_curator.py:SpawnedStore`` (state) vs. the curator
controller (orchestration).

Locking model is identical to :class:`SpawnedStore` at
``core/learning_curator.py:342-447``: sidecar ``.lock`` file +
``fcntl.flock(LOCK_EX)`` for writers, atomic temp-rename so readers
see either old or new state, never a tear. Reads do not lock.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class TerminalGoalError(Exception):
    """Raised when a pause / resume tried to mutate a goal whose
    on-disk status is already terminal (``done`` or ``cleared``).

    The dashboard surfaces this as ``409 Conflict``; Telegram replies
    with a short "Goal already done" line. Both refuse to revive a
    finished goal — once ``done`` lands, it stays done; once
    ``cleared`` lands, it stays cleared until the user types
    ``/goal <text>`` to set a new one.

    Lives in ``core.goal_state`` (not ``core.goal_manager``) so the
    store can raise it from inside its locked update path without
    importing the manager — keeps the dependency direction clean.
    """

    def __init__(self, status: str, *, session_uuid: str = "") -> None:
        self.status = status
        self.session_uuid = session_uuid
        super().__init__(f"goal already {status}")


# ──────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────

# Default turn ceiling. Mirrors Hermes (`hermes_cli/goals.py:46`) and
# the §3 design. The brain (and judge) consume budget; a runaway loop
# burns at most this many turns before auto-pausing.
DEFAULT_MAX_TURNS = 20

# Allowed status enum. Mirrors Hermes ``GoalState.status``.
_VALID_STATUSES: frozenset[str] = frozenset({
    "active", "paused", "done", "cleared",
})

# Allowed verdict enum (the slot in ``last_verdict`` we cache from the
# most recent judge call). Mirrors `core/goal_judge.py`'s return shape.
# ``None`` is also valid before the first judge call has run.
_VALID_VERDICTS: frozenset[str] = frozenset({
    "done", "continue", "skipped",
})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    """Serialize a datetime to ISO-8601 UTC, or pass through ``None``.

    Mirrors :func:`core.learning_curator._iso` so timestamps in
    ``goals.json`` are byte-identical to those in ``spawned.json``.
    """
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    """Inverse of :func:`_iso`. Returns ``None`` on missing/garbage."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ──────────────────────────────────────────────────────────────────
# GoalState dataclass
# ──────────────────────────────────────────────────────────────────


@dataclass
class GoalState:
    """One standing goal, attached to a Claude session UUID.

    ``status`` flow:

      * ``active`` — goal hook will judge after the next brain reply.
      * ``paused`` — goal hook short-circuits; ``/goal resume`` flips
        it back to active and resets ``turns_used``.
      * ``done`` — judge declared the goal satisfied (or unachievable);
        the loop has stopped. Record retained for audit/restart.
      * ``cleared`` — user issued ``/goal clear``; record retained for
        audit but treated as "no active goal" by the manager.

    ``last_verdict`` / ``last_reason`` cache the most recent judge
    response so ``/goal status`` can show why the loop did or didn't
    continue without re-running the judge.
    """

    goal: str
    status: str = "active"
    turns_used: int = 0
    max_turns: int = DEFAULT_MAX_TURNS
    created_at: datetime | None = None
    last_turn_at: datetime | None = None
    last_verdict: str | None = None
    last_reason: str | None = None
    paused_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render to the ``goals.json`` row shape (see module docstring)."""
        d = asdict(self)
        d["created_at"] = _iso(self.created_at)
        d["last_turn_at"] = _iso(self.last_turn_at)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "GoalState":
        """Tolerant inverse of :meth:`to_dict`. Coerces malformed scalars
        to safe defaults rather than raising — the store reads other
        sessions' rows in :meth:`GoalStateStore.list_active` and a
        single bad row shouldn't poison the whole load.
        """
        status_raw = raw.get("status", "active")
        status = status_raw if status_raw in _VALID_STATUSES else "active"
        verdict_raw = raw.get("last_verdict")
        verdict: str | None = (
            verdict_raw if verdict_raw in _VALID_VERDICTS else None
        )
        try:
            turns_used = int(raw.get("turns_used", 0) or 0)
        except (TypeError, ValueError):
            turns_used = 0
        try:
            max_turns = int(raw.get("max_turns", DEFAULT_MAX_TURNS) or DEFAULT_MAX_TURNS)
        except (TypeError, ValueError):
            max_turns = DEFAULT_MAX_TURNS
        return cls(
            goal=str(raw.get("goal", "") or ""),
            status=status,
            turns_used=turns_used,
            max_turns=max_turns,
            created_at=_parse_iso(raw.get("created_at")),
            last_turn_at=_parse_iso(raw.get("last_turn_at")),
            last_verdict=verdict,
            last_reason=raw.get("last_reason") or None,
            paused_reason=raw.get("paused_reason") or None,
        )


# ──────────────────────────────────────────────────────────────────
# GoalStateStore — persistence
# ──────────────────────────────────────────────────────────────────


class GoalStateStore:
    """Owns ``goals.json``. Single file, one row per session UUID.

    Mirrors :class:`core.learning_curator.SpawnedStore` 1:1 in locking
    semantics: sidecar ``.lock`` + ``fcntl.flock(LOCK_EX)`` for
    writers, atomic temp-rename. Reads do not lock; the atomic rename
    guarantees a consistent snapshot.

    Schema (``SCHEMA_VERSION = 1``):

    .. code-block:: json

       {
         "version": 1,
         "goals": {
           "<session-uuid>": { ...GoalState.to_dict()... }
         }
       }

    Higher schema versions on disk are treated as empty (we refuse to
    overwrite a future-version file rather than corrupting it).
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_suffix(path.suffix + ".lock")

    # ----- read paths -------------------------------------------------

    def _load_raw(self) -> dict[str, dict[str, Any]]:
        """Return the ``goals`` section as a plain dict, or ``{}`` on
        missing / corrupt / future-version file. Never raises.
        """
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning(
                "goals.json corrupt at %s; treating as empty", self._path
            )
            return {}
        if not isinstance(data, dict):
            return {}
        version = data.get("version")
        if version != self.SCHEMA_VERSION:
            log.warning(
                "goals.json at %s has unrecognised version %r; "
                "treating as empty (writers will refuse to overwrite "
                "to avoid corrupting a future-format file)",
                self._path,
                version,
            )
            return {}
        goals = data.get("goals")
        if not isinstance(goals, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for k, v in goals.items():
            if isinstance(k, str) and isinstance(v, dict):
                out[k] = v
        return out

    def load(self, session_uuid: str) -> GoalState | None:
        """Return the goal for ``session_uuid``, or ``None`` if absent.

        ``cleared`` rows ARE returned (the caller decides how to treat
        them — the manager treats cleared as "no active goal" but
        ``/learning audit``-style tooling may want to read them).
        """
        if not session_uuid:
            return None
        raw_goals = self._load_raw()
        row = raw_goals.get(session_uuid)
        if not isinstance(row, dict):
            return None
        try:
            return GoalState.from_dict(row)
        except Exception as exc:  # defensive — from_dict is tolerant
            log.warning(
                "goals.json: could not parse row for %s: %s",
                session_uuid,
                exc,
            )
            return None

    def list_active(self) -> list[tuple[str, GoalState]]:
        """All rows whose ``status == "active"``.

        Used by ``/status`` and the daemon-restart audit. Returns an
        empty list when the file is missing/corrupt rather than
        raising. Order is whatever ``json.loads`` preserves (typically
        insertion order on CPython 3.7+).
        """
        out: list[tuple[str, GoalState]] = []
        for sid, row in self._load_raw().items():
            try:
                state = GoalState.from_dict(row)
            except Exception:
                continue
            if state.status == "active":
                out.append((sid, state))
        return out

    def list_recent_inactive(
        self, limit: int = 20
    ) -> list[tuple[str, GoalState]]:
        """All non-active rows (paused / done / cleared) sorted by
        ``last_turn_at`` desc, capped at ``limit``.

        Used by the dashboard's history table. ``last_turn_at`` is the
        right sort key because it captures the most recent activity
        regardless of status — a goal cleared 5 minutes ago should
        outrank one done yesterday. Rows with no ``last_turn_at``
        (set but never evaluated) sort last via a min-datetime
        fallback so they stay readable but don't push real entries
        out of the cap.
        """
        rows: list[tuple[str, GoalState]] = []
        for sid, row in self._load_raw().items():
            try:
                state = GoalState.from_dict(row)
            except Exception:
                continue
            if state.status == "active":
                continue
            rows.append((sid, state))
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        rows.sort(
            key=lambda pair: pair[1].last_turn_at or epoch,
            reverse=True,
        )
        return rows[:limit]

    # ----- write paths ------------------------------------------------

    def save(self, session_uuid: str, state: GoalState) -> None:
        """Write/replace the row for ``session_uuid``. Atomic.

        Refuses to overwrite a future-version file: if the on-disk
        ``version`` is unknown the load returns ``{}`` and we'd merge
        on top of that, silently dropping the unknown rows. Guard
        against that by re-reading the raw payload under the lock and
        bailing if the version mismatches our own.
        """
        if not session_uuid:
            return
        self._mutate(lambda goals: goals.__setitem__(session_uuid, state.to_dict()))

    def clear(self, session_uuid: str) -> None:
        """Mark a session's goal as cleared. Does NOT delete the row.

        Mirrors Hermes (`hermes_cli/goals.py:198-204`) — keep cleared
        records around for audit. A future Day 4+ sweep can drop very
        old cleared records.
        """
        if not session_uuid:
            return
        existing = self.load(session_uuid)
        if existing is None:
            return
        if existing.status == "cleared":
            return
        existing.status = "cleared"
        self.save(session_uuid, existing)

    def update_atomic(
        self,
        session_uuid: str,
        mutator: Callable[["GoalState"], "GoalState"],
        *,
        refuse_terminal: bool = True,
    ) -> "GoalState":
        """Read-modify-write under fcntl.flock + atomic temp-rename.

        ``mutator`` is called with the **disk** state at lock-acquire
        time (NOT the manager's in-memory state) and must return the
        new ``GoalState`` to persist. The whole sequence runs under
        ``LOCK_EX`` so concurrent writers serialize.

        ``refuse_terminal=True`` (the default) raises
        :class:`TerminalGoalError` when the disk state's status is
        ``"done"`` or ``"cleared"`` at lock-acquire time — used by
        ``GoalManager.pause`` / ``GoalManager.resume`` which must
        refuse to revive a finished goal. ``refuse_terminal=False``
        skips the guard for callers (like ``evaluate_after_turn``)
        whose terminal verdicts must overwrite any concurrent
        non-terminal write.

        Raises :class:`KeyError` if no row exists for the session
        (caller decides whether to recreate or bail).

        Returns the post-mutation :class:`GoalState`.
        """
        captured: list[GoalState | None] = [None]
        terminal: list[str | None] = [None]
        missing: list[bool] = [False]

        def _do(goals: dict) -> None:
            row = goals.get(session_uuid)
            if row is None:
                missing[0] = True
                return
            try:
                current = GoalState.from_dict(row)
            except Exception:
                # Corrupt row — treat as missing so the caller bails
                # rather than overwriting unintelligible data.
                missing[0] = True
                return
            if refuse_terminal and current.status in ("done", "cleared"):
                terminal[0] = current.status
                return
            new_state = mutator(current)
            goals[session_uuid] = new_state.to_dict()
            captured[0] = new_state

        self._mutate(_do)
        if missing[0]:
            raise KeyError(session_uuid)
        if terminal[0] is not None:
            raise TerminalGoalError(
                terminal[0], session_uuid=session_uuid
            )
        result = captured[0]
        assert result is not None  # one of {missing, terminal, captured} set
        return result

    # ----- internal ---------------------------------------------------

    def _mutate(self, mutator) -> None:
        """Read-modify-write under fcntl.flock with atomic temp-rename.

        ``mutator`` takes the current goals dict and mutates it in
        place. The whole sequence (read → mutate → write → fsync →
        rename) is held under ``LOCK_EX``.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            current = self._load_raw()
            mutator(current)
            payload = {
                "version": self.SCHEMA_VERSION,
                "goals": current,
            }
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


__all__ = [
    "DEFAULT_MAX_TURNS",
    "GoalState",
    "GoalStateStore",
    "TerminalGoalError",
]
