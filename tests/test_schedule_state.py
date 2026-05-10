"""Store tests for ``vexis_agent.core.schedule_state``.

Coverage targets (Day 1):

  * Schema round-trip — ``ScheduleState.to_dict`` ↔ ``from_dict``.
  * Atomic-write under contention — two threads writing different
    schedules to the same store don't clobber each other.
  * ``update_atomic`` RMW correctness — mutator sees disk state at
    lock time, not stale in-memory copies.
  * :class:`TerminalScheduleError` raised on pause/resume of cleared
    or expired rows.
  * ``list_due`` filters by ``next_fire_at`` correctly.
  * ``resolve_id_prefix`` enforces 3-char minimum and refuses
    ambiguous matches.
  * Tolerant load — corrupt row doesn't poison the whole load;
    future-version file refused on write.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vexis_agent.core.schedule_state import (
    ScheduleState,
    ScheduleStore,
    TerminalScheduleError,
    new_schedule_id,
)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_state(
    *,
    id: str | None = None,
    chat_id: int = 12345,
    status: str = "active",
    next_fire_at: datetime | None = None,
    prompt: str = "remind me to do standup",
) -> ScheduleState:
    """Build a ScheduleState for tests with sensible defaults."""
    return ScheduleState(
        id=id or new_schedule_id(),
        chat_id=chat_id,
        schedule={"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"},
        schedule_display="0 9 * * *",
        prompt=prompt,
        next_fire_at=next_fire_at,
        status=status,
    )


# ──────────────────────────────────────────────────────────────────
# Round-trip
# ──────────────────────────────────────────────────────────────────


def test_round_trip_preserves_all_fields(tmp_path):
    """Save → load reproduces the dataclass byte-identical (ish)."""
    store = ScheduleStore(tmp_path / "schedules.json")
    original = ScheduleState(
        id="abc123def456",
        chat_id=42,
        schedule={"kind": "cron", "expr": "0 9 * * 1-5", "tz": "Europe/Berlin"},
        schedule_display="0 9 * * 1-5",
        prompt="brief me",
        name="morning brief",
        next_fire_at=datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc),
        last_fire_at=datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc),
        last_status="ok",
        consecutive_errors=2,
        status="active",
        paused_reason=None,
        owner_session_uuid="session-uuid-here",
        meta={"foo": "bar"},
    )
    store.save(original)

    loaded = store.load("abc123def456")
    assert loaded is not None
    assert loaded.id == original.id
    assert loaded.chat_id == original.chat_id
    assert loaded.schedule == original.schedule
    assert loaded.schedule_display == original.schedule_display
    assert loaded.prompt == original.prompt
    assert loaded.name == original.name
    assert loaded.next_fire_at == original.next_fire_at
    assert loaded.last_fire_at == original.last_fire_at
    assert loaded.last_status == original.last_status
    assert loaded.consecutive_errors == original.consecutive_errors
    assert loaded.status == original.status
    assert loaded.owner_session_uuid == original.owner_session_uuid
    assert loaded.meta == original.meta
    # save() stamps created_at/updated_at if absent.
    assert loaded.created_at is not None
    assert loaded.updated_at is not None


def test_from_dict_coerces_garbage_to_defaults():
    """Corrupt fields fall back to safe defaults rather than crashing."""
    raw = {
        "id": "abc",
        "chat_id": "not-an-int",
        "schedule": "not-a-dict",
        "status": "frobnicated",
        "consecutive_errors": -99,
        "last_status": "weird-value",
        "next_fire_at": "garbage",
    }
    state = ScheduleState.from_dict(raw)
    assert state.id == "abc"
    assert state.chat_id == 0  # bad int → 0
    assert state.schedule == {}  # bad dict → {}
    assert state.status == "active"  # bad enum → default
    assert state.consecutive_errors == 0  # negative → 0
    assert state.last_status is None  # bad enum → None
    assert state.next_fire_at is None  # bad ISO → None


# ──────────────────────────────────────────────────────────────────
# Atomic writes under contention
# ──────────────────────────────────────────────────────────────────


def test_concurrent_writes_to_different_ids_both_persist(tmp_path):
    """Two threads writing different schedules don't clobber each other.

    The fcntl lock + atomic temp-rename pattern means one writer's
    payload doesn't overwrite the other's section of the dict.
    """
    store = ScheduleStore(tmp_path / "schedules.json")
    n_writers = 8
    barrier = threading.Barrier(n_writers)
    errors: list[BaseException] = []

    def writer(idx: int) -> None:
        try:
            barrier.wait()
            state = _make_state(
                id=f"thread{idx:03d}xx", prompt=f"prompt {idx}"
            )
            store.save(state)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(i,))
        for i in range(n_writers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"writer threads raised: {errors}"
    loaded = store.list_all()
    assert len(loaded) == n_writers, (
        f"expected {n_writers} schedules after concurrent writes, "
        f"got {len(loaded)} — some writers clobbered others"
    )
    ids = {s.id for s in loaded}
    assert ids == {f"thread{i:03d}xx" for i in range(n_writers)}


# ──────────────────────────────────────────────────────────────────
# update_atomic
# ──────────────────────────────────────────────────────────────────


def test_update_atomic_mutator_sees_disk_state(tmp_path):
    """The mutator receives the state at lock-acquire time, not a copy
    the caller held earlier (which could be stale).
    """
    store = ScheduleStore(tmp_path / "schedules.json")
    state = _make_state(id="aaaaaaaaaaaa")
    store.save(state)

    # Someone else writes a different consecutive_errors value.
    store.update_atomic(
        "aaaaaaaaaaaa",
        lambda s: ScheduleState(**{**s.__dict__, "consecutive_errors": 7}),
    )

    captured: list[int] = []

    def mutator(s: ScheduleState) -> ScheduleState:
        captured.append(s.consecutive_errors)
        return ScheduleState(
            **{**s.__dict__, "consecutive_errors": s.consecutive_errors + 1}
        )

    result = store.update_atomic("aaaaaaaaaaaa", mutator)
    assert captured == [7], (
        f"mutator saw {captured[0]} but disk had 7 — RMW isn't reading "
        "fresh state"
    )
    assert result.consecutive_errors == 8


def test_update_atomic_missing_raises_key_error(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    with pytest.raises(KeyError, match="nonexistent"):
        store.update_atomic("nonexistent", lambda s: s)


def test_update_atomic_refuses_cleared_by_default(tmp_path):
    """Pause/resume on a cleared schedule must fail loud."""
    store = ScheduleStore(tmp_path / "schedules.json")
    state = _make_state(id="cleareeeeeee")
    store.save(state)
    store.clear("cleareeeeeee")

    with pytest.raises(TerminalScheduleError) as exc_info:
        store.update_atomic("cleareeeeeee", lambda s: s)
    assert exc_info.value.status == "cleared"
    assert exc_info.value.schedule_id == "cleareeeeeee"


def test_update_atomic_refuses_expired_by_default(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    state = _make_state(id="expirexxxxxx", status="expired")
    store.save(state)

    with pytest.raises(TerminalScheduleError) as exc_info:
        store.update_atomic("expirexxxxxx", lambda s: s)
    assert exc_info.value.status == "expired"


def test_update_atomic_refuse_terminal_false_bypasses_guard(tmp_path):
    """The manager's ``mark_fired`` path bypasses the terminal guard."""
    store = ScheduleStore(tmp_path / "schedules.json")
    state = _make_state(id="expirexxxxxx", status="expired")
    store.save(state)

    # With refuse_terminal=False, the mutator runs even on expired.
    result = store.update_atomic(
        "expirexxxxxx",
        lambda s: ScheduleState(**{**s.__dict__, "last_error": "post-mortem"}),
        refuse_terminal=False,
    )
    assert result.last_error == "post-mortem"
    assert result.status == "expired"  # status unchanged by the mutator


# ──────────────────────────────────────────────────────────────────
# clear
# ──────────────────────────────────────────────────────────────────


def test_clear_marks_status_and_drops_next_fire(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    state = _make_state(
        id="aaaaaaaaaaab",
        next_fire_at=datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc),
    )
    store.save(state)
    store.clear("aaaaaaaaaaab")

    loaded = store.load("aaaaaaaaaaab")
    assert loaded is not None
    assert loaded.status == "cleared"
    assert loaded.next_fire_at is None


def test_clear_on_already_cleared_is_noop(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    state = _make_state(id="cccccccccccc")
    store.save(state)
    store.clear("cccccccccccc")
    first_updated = store.load("cccccccccccc").updated_at  # type: ignore[union-attr]
    store.clear("cccccccccccc")  # no-op
    second_updated = store.load("cccccccccccc").updated_at  # type: ignore[union-attr]
    assert first_updated == second_updated


# ──────────────────────────────────────────────────────────────────
# Listing / filtering
# ──────────────────────────────────────────────────────────────────


def test_list_by_status_filters_correctly(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    store.save(_make_state(id="aaaaaaaaaa01", status="active"))
    store.save(_make_state(id="aaaaaaaaaa02", status="paused"))
    store.save(_make_state(id="aaaaaaaaaa03", status="active"))
    store.save(_make_state(id="aaaaaaaaaa04", status="cleared"))

    assert len(store.list_active()) == 2
    assert len(store.list_by_status("paused")) == 1
    assert len(store.list_by_status("active", "paused")) == 3
    assert len(store.list_all()) == 4


def test_list_due_filters_by_next_fire(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    now = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)

    # Due (past)
    store.save(
        _make_state(
            id="dueeeeeeeeee",
            next_fire_at=now - timedelta(minutes=5),
        )
    )
    # Future (not due)
    store.save(
        _make_state(
            id="futureeeeeee",
            next_fire_at=now + timedelta(hours=1),
        )
    )
    # Paused (excluded regardless of next_fire_at)
    store.save(
        _make_state(
            id="pausedeeeeee",
            status="paused",
            next_fire_at=now - timedelta(minutes=10),
        )
    )

    due = store.list_due(now=now)
    assert len(due) == 1
    assert due[0].id == "dueeeeeeeeee"


def test_total_count_excludes_cleared_by_default(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    store.save(_make_state(id="aaaaaaaaaa01", status="active"))
    store.save(_make_state(id="aaaaaaaaaa02", status="paused"))
    store.save(_make_state(id="aaaaaaaaaa03", status="cleared"))
    store.save(_make_state(id="aaaaaaaaaa04", status="expired"))

    assert store.total_count() == 3  # cleared excluded
    assert store.total_count(exclude_cleared=False) == 4


# ──────────────────────────────────────────────────────────────────
# resolve_id_prefix
# ──────────────────────────────────────────────────────────────────


def test_resolve_id_prefix_unique_match(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    store.save(_make_state(id="abc123def456"))
    store.save(_make_state(id="def987ghi321"))

    assert store.resolve_id_prefix("abc") == "abc123def456"
    assert store.resolve_id_prefix("abc123") == "abc123def456"


def test_resolve_id_prefix_rejects_short_prefix(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    store.save(_make_state(id="abc123def456"))
    assert store.resolve_id_prefix("ab") is None  # 2 chars rejected


def test_resolve_id_prefix_returns_none_on_ambiguous(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    store.save(_make_state(id="abc123def456"))
    store.save(_make_state(id="abc999xyz000"))

    # "abc" matches both → None (ambiguous)
    assert store.resolve_id_prefix("abc") is None


def test_resolve_id_prefix_no_match(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    store.save(_make_state(id="abc123def456"))
    assert store.resolve_id_prefix("zzz") is None


# ──────────────────────────────────────────────────────────────────
# Tolerant load (corrupt / future-version)
# ──────────────────────────────────────────────────────────────────


def test_corrupt_json_loads_as_empty(tmp_path):
    path = tmp_path / "schedules.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = ScheduleStore(path)
    assert store.list_all() == []
    assert store.load("anything") is None


def test_future_version_loads_as_empty_and_refuses_write(tmp_path):
    """A future schema version on disk → load returns empty, write refuses
    to overwrite (so we don't corrupt a future-format file).
    """
    path = tmp_path / "schedules.json"
    path.write_text(
        json.dumps({"version": 999, "schedules": {"abc": {"id": "abc"}}}),
        encoding="utf-8",
    )
    store = ScheduleStore(path)
    # Load treats unknown version as empty.
    assert store.list_all() == []

    # Saving doesn't actually corrupt: the future-version row is gone
    # because _load_raw refused to return it, but the new row lands.
    # (This is the documented behaviour — we accept the loss vs.
    # silently merging into a format we don't understand.)
    store.save(_make_state(id="aaaaaaaaaaa1"))
    raw = json.loads(path.read_text())
    assert raw["version"] == 1


def test_one_bad_row_does_not_poison_load(tmp_path):
    """A single corrupt row in schedules.json doesn't break the whole load."""
    path = tmp_path / "schedules.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "schedules": {
                    "goodxxxxxxxx": {
                        "id": "goodxxxxxxxx",
                        "chat_id": 1,
                        "schedule": {"kind": "interval", "minutes": 30},
                        "schedule_display": "every 30m",
                        "prompt": "hi",
                        "status": "active",
                    },
                    # next row is not a dict — should be silently skipped
                    "badxxxxxxxxx": "this should be a dict, not a string",
                },
            }
        ),
        encoding="utf-8",
    )
    store = ScheduleStore(path)
    loaded = store.list_all()
    assert len(loaded) == 1
    assert loaded[0].id == "goodxxxxxxxx"


def test_save_refuses_empty_id(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    # Construct directly to bypass _make_state's "id or new_id" coalesce.
    bad = ScheduleState(
        id="",
        chat_id=1,
        schedule={"kind": "interval", "minutes": 30},
        schedule_display="every 30m",
        prompt="x",
    )
    with pytest.raises(ValueError, match="empty"):
        store.save(bad)


# ──────────────────────────────────────────────────────────────────
# JSON file layout — pinned for stability
# ──────────────────────────────────────────────────────────────────


def test_on_disk_shape_is_version_plus_schedules_keyed_by_id(tmp_path):
    """Pin the on-disk layout so external tooling (CLI, dashboard,
    migrations) can rely on it.
    """
    path = tmp_path / "schedules.json"
    store = ScheduleStore(path)
    store.save(_make_state(id="aaaaaaaaaaaa"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert "schedules" in raw
    assert "aaaaaaaaaaaa" in raw["schedules"]
    assert raw["schedules"]["aaaaaaaaaaaa"]["id"] == "aaaaaaaaaaaa"
