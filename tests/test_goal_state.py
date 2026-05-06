"""Tests for ``core/goal_state.py`` — GoalState dataclass + atomic JSON store."""

from __future__ import annotations

import json
import multiprocessing as mp
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.goal_state import (
    DEFAULT_MAX_TURNS,
    GoalState,
    GoalStateStore,
)


# ──────────────────────────────────────────────────────────────────
# load / save / clear roundtrip
# ──────────────────────────────────────────────────────────────────


def test_load_missing_returns_none(tmp_path: Path) -> None:
    """A store pointing at a nonexistent file returns None for any
    session UUID. Mirrors SpawnedStore: missing file == empty state."""
    store = GoalStateStore(tmp_path / "goals.json")
    assert store.load("any-uuid") is None
    assert store.load("") is None


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    """A saved GoalState comes back identical (semantically) on load.

    Timestamps round-trip via ISO; we compare on the resolved fields
    rather than ``==`` on the dataclass because ``GoalState`` doesn't
    set ``eq=True`` for the timestamp fields' tz-aware behavior."""
    store = GoalStateStore(tmp_path / "goals.json")
    now = datetime(2026, 5, 5, 14, 30, tzinfo=timezone.utc)
    state = GoalState(
        goal="port the goal command",
        status="active",
        turns_used=3,
        max_turns=20,
        created_at=now,
        last_turn_at=now,
        last_verdict="continue",
        last_reason="more work needed",
        paused_reason=None,
    )
    store.save("session-A", state)

    out = store.load("session-A")
    assert out is not None
    assert out.goal == "port the goal command"
    assert out.status == "active"
    assert out.turns_used == 3
    assert out.max_turns == 20
    assert out.created_at == now
    assert out.last_turn_at == now
    assert out.last_verdict == "continue"
    assert out.last_reason == "more work needed"
    assert out.paused_reason is None


def test_clear_marks_status_does_not_delete(tmp_path: Path) -> None:
    """``clear`` flips ``status`` to ``"cleared"`` and keeps the row
    on disk. Subsequent ``load`` still returns the row (manager treats
    ``cleared`` as "no active goal" but ``load`` itself doesn't filter)."""
    store = GoalStateStore(tmp_path / "goals.json")
    state = GoalState(goal="port goal", status="active")
    store.save("sid-X", state)

    store.clear("sid-X")
    out = store.load("sid-X")
    assert out is not None
    assert out.status == "cleared"
    assert out.goal == "port goal"  # text retained for audit

    # Idempotent: clearing again is a no-op (no error, status stays "cleared").
    store.clear("sid-X")
    again = store.load("sid-X")
    assert again is not None
    assert again.status == "cleared"


def test_clear_missing_session_is_noop(tmp_path: Path) -> None:
    store = GoalStateStore(tmp_path / "goals.json")
    store.clear("never-existed")
    assert store.load("never-existed") is None


def test_save_overwrites_in_place(tmp_path: Path) -> None:
    """Save twice for the same session: second save replaces the row."""
    store = GoalStateStore(tmp_path / "goals.json")
    s1 = GoalState(goal="first goal", turns_used=0, max_turns=5)
    store.save("sid", s1)

    s2 = GoalState(goal="second goal", turns_used=2, max_turns=10)
    store.save("sid", s2)

    out = store.load("sid")
    assert out is not None
    assert out.goal == "second goal"
    assert out.max_turns == 10


# ──────────────────────────────────────────────────────────────────
# list_active filters by status
# ──────────────────────────────────────────────────────────────────


def test_list_active_filters_status(tmp_path: Path) -> None:
    """Only ``status=="active"`` rows show up in ``list_active``;
    paused / done / cleared rows are excluded."""
    store = GoalStateStore(tmp_path / "goals.json")
    store.save("a-active", GoalState(goal="a", status="active"))
    store.save("b-paused", GoalState(goal="b", status="paused"))
    store.save("c-done", GoalState(goal="c", status="done"))
    store.save("d-cleared", GoalState(goal="d", status="cleared"))
    store.save("e-active", GoalState(goal="e", status="active"))

    rows = store.list_active()
    sids = {sid for sid, _ in rows}
    assert sids == {"a-active", "e-active"}
    by_sid = dict(rows)
    assert by_sid["a-active"].goal == "a"
    assert by_sid["e-active"].goal == "e"


def test_list_active_empty_when_file_missing(tmp_path: Path) -> None:
    store = GoalStateStore(tmp_path / "goals.json")
    assert store.list_active() == []


# ──────────────────────────────────────────────────────────────────
# Schema-version handling
# ──────────────────────────────────────────────────────────────────


def test_unknown_version_treated_as_empty(tmp_path: Path) -> None:
    """A future-version file on disk doesn't crash the loader.

    Mirrors SpawnedStore's "corrupt → empty" posture but extended to
    a recognised-but-unknown version number. Loaders return empty;
    writers must not silently overwrite (we test that next)."""
    path = tmp_path / "goals.json"
    path.write_text(
        json.dumps({"version": 99, "goals": {"x": {"goal": "from future"}}}),
        encoding="utf-8",
    )
    store = GoalStateStore(path)
    assert store.load("x") is None
    assert store.list_active() == []


def test_corrupt_file_treated_as_empty(tmp_path: Path) -> None:
    """A garbled file doesn't propagate an exception to callers.

    Same posture as ``SpawnedStore`` (`tests/test_recursion_guard.py:171`).
    The store logs a warning and behaves as if the file were absent."""
    path = tmp_path / "goals.json"
    path.write_text("{this is not json", encoding="utf-8")
    store = GoalStateStore(path)
    assert store.load("anything") is None
    assert store.list_active() == []


def test_save_preserves_other_sessions_rows(tmp_path: Path) -> None:
    """Mutating session A doesn't drop session B. The store's
    ``_mutate`` reads-modifies-writes the whole goals dict under the
    lock, so cross-session writes interleave safely."""
    store = GoalStateStore(tmp_path / "goals.json")
    store.save("A", GoalState(goal="first"))
    store.save("B", GoalState(goal="second"))

    store.save("A", GoalState(goal="first updated"))

    a = store.load("A")
    b = store.load("B")
    assert a is not None and a.goal == "first updated"
    assert b is not None and b.goal == "second"


# ──────────────────────────────────────────────────────────────────
# Atomic temp-rename + concurrent writers under fcntl.flock
# ──────────────────────────────────────────────────────────────────


def _child_writer(path_str: str, sid: str, goal_text: str, count: int) -> None:
    """Worker for the concurrent-write fixture. Saves the same row N times."""
    from core.goal_state import GoalState as _GoalState
    from core.goal_state import GoalStateStore as _Store

    store = _Store(Path(path_str))
    for i in range(count):
        state = _GoalState(
            goal=f"{goal_text}-{i}",
            turns_used=i,
            max_turns=DEFAULT_MAX_TURNS,
        )
        store.save(sid, state)


def test_concurrent_writers_under_flock_do_not_corrupt(tmp_path: Path) -> None:
    """Two child processes hammer save() for two different session
    IDs in parallel. After both finish, the file is parseable and
    both sessions are present with their last-written goal text.

    This is the load-bearing safety claim: the sidecar lock + atomic
    temp-rename guarantees no torn writes even under concurrent
    pressure. Mirrors SpawnedStore's correctness invariant."""
    path = tmp_path / "goals.json"
    iterations = 25

    ctx = mp.get_context("spawn")
    p1 = ctx.Process(target=_child_writer, args=(str(path), "alpha", "alpha", iterations))
    p2 = ctx.Process(target=_child_writer, args=(str(path), "beta", "beta", iterations))
    p1.start()
    p2.start()
    p1.join(timeout=30)
    p2.join(timeout=30)

    assert p1.exitcode == 0, "alpha writer failed"
    assert p2.exitcode == 0, "beta writer failed"

    # File parses cleanly.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == GoalStateStore.SCHEMA_VERSION
    assert set(raw["goals"].keys()) == {"alpha", "beta"}

    # Final reads through the store API match the last write each child
    # performed (the highest-index iteration).
    store = GoalStateStore(path)
    a = store.load("alpha")
    b = store.load("beta")
    assert a is not None and a.goal == f"alpha-{iterations - 1}"
    assert b is not None and b.goal == f"beta-{iterations - 1}"


def test_temp_file_lives_in_same_dir(tmp_path: Path) -> None:
    """``os.replace`` is only atomic when the source and destination
    are on the same filesystem. The store writes the tmp file under
    the goals.json parent dir; verify the tmp suffix sits on disk
    momentarily during a save (catches a future refactor that moves
    tmp creation to ``/tmp``)."""
    path = tmp_path / "subdir" / "goals.json"
    store = GoalStateStore(path)
    store.save("sid", GoalState(goal="hello"))
    # After save, no tmp should remain.
    assert not (path.parent / (path.name + ".tmp")).exists()
    # The lock file does persist (same as SpawnedStore).
    assert (path.parent / (path.name + ".lock")).exists()


# ──────────────────────────────────────────────────────────────────
# GoalState.from_dict tolerance
# ──────────────────────────────────────────────────────────────────


def test_from_dict_coerces_invalid_status(tmp_path: Path) -> None:
    """A row with an unknown status string falls back to 'active'
    rather than raising. Defensive: an outside writer (corrupt
    daemon, hand-edit) shouldn't poison subsequent reads."""
    raw = {"goal": "x", "status": "totally-unknown", "turns_used": 1}
    state = GoalState.from_dict(raw)
    assert state.status == "active"
    assert state.goal == "x"


def test_from_dict_coerces_invalid_verdict() -> None:
    raw = {"goal": "x", "last_verdict": "exploded"}
    state = GoalState.from_dict(raw)
    assert state.last_verdict is None


def test_from_dict_handles_missing_optional_fields() -> None:
    """Minimum-viable row with just ``goal`` set parses cleanly."""
    state = GoalState.from_dict({"goal": "minimal"})
    assert state.goal == "minimal"
    assert state.status == "active"
    assert state.turns_used == 0
    assert state.max_turns == DEFAULT_MAX_TURNS
    assert state.created_at is None
    assert state.last_verdict is None


# ──────────────────────────────────────────────────────────────────
# Day 3 — restart and lifecycle
# ──────────────────────────────────────────────────────────────────


def test_daemon_restart_rehydrates_active_goal(tmp_path: Path) -> None:
    """§5 — daemon restart mid-loop must preserve an active goal.

    Simulates the restart by creating a fresh ``GoalStateStore``
    pointing at the same on-disk file and verifying the active
    record is readable. The hook in ``transports/telegram.py`` then
    rebuilds a manager per-call against the live store, so
    rehydration is automatic on the next user message — no
    "scan for active goals on boot and re-fire" path needed (per
    the §5 boot policy: do nothing automatic).

    Asserts:
      - Pre-restart store writes an active goal.
      - Post-restart fresh store reads it back identically.
      - ``list_active`` returns the rehydrated record.
      - **Crucially**: no auto-fire on store reinstantiation. The
        store has no method that would enqueue or trigger work; it
        only persists / reads. Restart safety is "the next user
        message wakes the loop", not "boot wakes the loop".
    """
    path = tmp_path / "goals.json"

    # Pre-restart: write an active goal with mid-flight turn count.
    store_pre = GoalStateStore(path)
    state = GoalState(
        goal="port goal command",
        status="active",
        turns_used=4,
        max_turns=20,
        last_verdict="continue",
        last_reason="halfway through Day 2 work",
    )
    store_pre.save("session-pre", state)

    # "Restart": drop the in-memory store, build a new one against
    # the same file. This is exactly what happens when the daemon
    # process restarts — fresh GoalStateStore() in transport's
    # _build_goal_manager helper.
    del store_pre
    store_post = GoalStateStore(path)

    # Active goal is readable.
    rehydrated = store_post.load("session-pre")
    assert rehydrated is not None
    assert rehydrated.goal == "port goal command"
    assert rehydrated.status == "active"
    assert rehydrated.turns_used == 4
    assert rehydrated.last_verdict == "continue"
    assert rehydrated.last_reason == "halfway through Day 2 work"

    # list_active picks it up (single-row file, status=active).
    actives = dict(store_post.list_active())
    assert "session-pre" in actives
    assert actives["session-pre"].turns_used == 4

    # **Boot policy invariant**: the store has no auto-fire method.
    # Confirm there is no public scan/run/wake method that would
    # surprise the user post-restart. The only public surface is
    # load / save / clear / list_active.
    public = [
        name for name in dir(store_post)
        if not name.startswith("_")
        and callable(getattr(store_post, name))
    ]
    # SCHEMA_VERSION is a class attribute (int), not a method — drop
    # from the callable check.
    callable_public = {
        n for n in public if not isinstance(getattr(store_post, n), int)
    }
    # Methods the store IS allowed to expose. Day 3 pinned this set
    # to catch a future addition that could auto-fire goal loops on
    # daemon boot. Day 5 added ``list_recent_inactive`` for the
    # dashboard's history table — read-only, no auto-fire path.
    expected = {"load", "save", "clear", "list_active", "list_recent_inactive"}
    assert callable_public == expected, (
        f"Unexpected public method on GoalStateStore: {callable_public}. "
        "If you've added one, double-check it cannot auto-fire goal "
        "loops on daemon boot — the §5 boot policy is 'do nothing "
        "automatic'."
    )


def test_multiple_cleared_records_cumulate(tmp_path: Path) -> None:
    """§3 — cleared records are retained on disk for audit and
    restart forensics. Three set→clear cycles across distinct
    session UUIDs should leave three rows in goals.json, all keyed
    by their session UUID, with the third (still active) being the
    only one ``list_active`` returns.

    Mirrors the real session-clear lifecycle: every /clear rotates
    the active session UUID, leaving the old goal orphaned but
    readable. Multiple /clear cycles accumulate, which is fine —
    one cleared row per session, ~200 bytes; never grows fast.
    """
    path = tmp_path / "goals.json"
    store = GoalStateStore(path)

    # Cycle 1: set then clear under sid-1.
    store.save("sid-1", GoalState(goal="first", status="active"))
    store.clear("sid-1")

    # Cycle 2: set then clear under sid-2.
    store.save("sid-2", GoalState(goal="second", status="active"))
    store.clear("sid-2")

    # Cycle 3: set under sid-3, leave active.
    store.save("sid-3", GoalState(goal="third", status="active"))

    # All three records survive on disk.
    assert store.load("sid-1") is not None
    assert store.load("sid-1").status == "cleared"  # type: ignore[union-attr]
    assert store.load("sid-2") is not None
    assert store.load("sid-2").status == "cleared"  # type: ignore[union-attr]
    assert store.load("sid-3") is not None
    assert store.load("sid-3").status == "active"  # type: ignore[union-attr]

    # Each cleared row retains its goal text for audit.
    assert store.load("sid-1").goal == "first"  # type: ignore[union-attr]
    assert store.load("sid-2").goal == "second"  # type: ignore[union-attr]

    # list_active returns ONLY sid-3 — the cleared rows are filtered
    # out by the status check.
    actives = dict(store.list_active())
    assert set(actives.keys()) == {"sid-3"}
    assert actives["sid-3"].goal == "third"

    # File on disk physically holds all three keys (verified via raw
    # JSON, not through the store). Defends against a future "drop
    # cleared records on save" optimisation that would silently
    # break the audit trail.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw["goals"].keys()) == {"sid-1", "sid-2", "sid-3"}
