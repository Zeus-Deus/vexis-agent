"""Tests for core/sessions.py — multi-session store + Step 3 migration."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from core.sessions import SessionStore, _gen_name, _validate_name


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "session.json"


@pytest.fixture
def with_tz():
    """Set the process timezone for the duration of the test."""
    saved = os.environ.get("TZ")

    def _set(tz: str) -> None:
        os.environ["TZ"] = tz
        time.tzset()

    yield _set
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


# ---------- fresh init ----------


def test_fresh_init_creates_state_file(state_path: Path) -> None:
    store = SessionStore(state_path)
    assert state_path.exists()
    data = _read(state_path)
    assert data["active"] in data["sessions"]
    assert len(data["sessions"]) == 1
    info = store.list()[0]
    assert info.is_active
    assert not info.initialized


def test_fresh_init_uuid_is_valid(state_path: Path) -> None:
    store = SessionStore(state_path)
    uuid.UUID(store.get())


def test_fresh_init_name_matches_auto_format(state_path: Path) -> None:
    store = SessionStore(state_path)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}(-\d+)?", store.active_name())


# ---------- migration from Step 3 ----------


def test_migration_preserves_uuid_and_initialized(state_path: Path) -> None:
    state_path.write_text(
        json.dumps({"session_id": "old-abc-123", "initialized": True})
    )
    store = SessionStore(state_path)
    assert store.get() == "old-abc-123"
    assert store.is_initialized() is True
    data = _read(state_path)
    assert "session_id" not in data
    active_meta = data["sessions"][data["active"]]
    assert active_meta["uuid"] == "old-abc-123"
    assert active_meta["initialized"] is True


def test_migration_preserves_uninitialized_flag(state_path: Path) -> None:
    state_path.write_text(json.dumps({"session_id": "fresh-xyz", "initialized": False}))
    store = SessionStore(state_path)
    assert not store.is_initialized()


def test_migration_logs_info(state_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="core.sessions")
    state_path.write_text(json.dumps({"session_id": "u", "initialized": False}))
    SessionStore(state_path)
    assert any("Migrated single session" in r.message for r in caplog.records)


# ---------- brain-facing API ----------


def test_get_returns_active_uuid(state_path: Path) -> None:
    store = SessionStore(state_path)
    assert store.get() == store.list()[0].uuid


def test_mark_initialized_persists_across_load(state_path: Path) -> None:
    store = SessionStore(state_path)
    assert not store.is_initialized()
    store.mark_initialized()
    assert SessionStore(state_path).is_initialized()


def test_rotate_changes_uuid_and_resets_initialized(state_path: Path) -> None:
    store = SessionStore(state_path)
    store.mark_initialized()
    old = store.get()
    new = store.rotate()
    assert new != old
    assert store.get() == new
    assert not store.is_initialized()


# ---------- create ----------


def test_create_named_switches_active(state_path: Path) -> None:
    store = SessionStore(state_path)
    first = store.active_name()
    assert store.create("design") == "design"
    assert store.active_name() == "design"
    names = {i.name for i in store.list()}
    assert names == {first, "design"}


def test_create_auto_named_format(state_path: Path) -> None:
    store = SessionStore(state_path)
    name = store.create()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}(-\d+)?", name)


def test_create_duplicate_raises(state_path: Path) -> None:
    store = SessionStore(state_path)
    store.create("foo")
    with pytest.raises(ValueError, match="already exists"):
        store.create("foo")


@pytest.mark.parametrize(
    "bad",
    ["", " ", "has spaces", "has/slash", "has.dot", "x" * 65, "ünicode", "no\nnewline"],
)
def test_create_invalid_name_raises(state_path: Path, bad: str) -> None:
    store = SessionStore(state_path)
    with pytest.raises(ValueError, match="Invalid"):
        store.create(bad)


@pytest.mark.parametrize("good", ["a", "x" * 64, "with-hyphen_and_under-123"])
def test_create_valid_edge_cases(state_path: Path, good: str) -> None:
    store = SessionStore(state_path)
    store.create(good)
    assert good in {i.name for i in store.list()}


# ---------- switch ----------


def test_switch_existing(state_path: Path) -> None:
    store = SessionStore(state_path)
    first = store.active_name()
    store.create("alpha")
    assert store.switch(first)
    assert store.active_name() == first


def test_switch_missing_returns_false(state_path: Path) -> None:
    store = SessionStore(state_path)
    assert not store.switch("does-not-exist")


# ---------- rename ----------


def test_rename_succeeds(state_path: Path) -> None:
    store = SessionStore(state_path)
    store.create("alpha")
    assert store.rename("alpha", "beta")
    names = {i.name for i in store.list()}
    assert "beta" in names and "alpha" not in names


def test_rename_updates_active_pointer(state_path: Path) -> None:
    store = SessionStore(state_path)
    store.create("alpha")  # alpha is now active
    store.rename("alpha", "alpha-v2")
    assert store.active_name() == "alpha-v2"


def test_rename_missing_old_returns_false(state_path: Path) -> None:
    store = SessionStore(state_path)
    assert not store.rename("nope", "still-nope")


def test_rename_new_taken_returns_false(state_path: Path) -> None:
    store = SessionStore(state_path)
    first = store.active_name()
    store.create("beta")
    assert not store.rename("beta", first)


def test_rename_invalid_new_raises(state_path: Path) -> None:
    store = SessionStore(state_path)
    store.create("alpha")
    with pytest.raises(ValueError, match="Invalid"):
        store.rename("alpha", "bad name")


def test_rename_same_name_is_noop(state_path: Path) -> None:
    store = SessionStore(state_path)
    name = store.active_name()
    assert store.rename(name, name)
    assert store.active_name() == name


# ---------- delete ----------


def test_delete_succeeds(state_path: Path) -> None:
    store = SessionStore(state_path)
    store.create("alpha")
    store.create("beta")
    store.switch("alpha")
    assert store.delete("beta")
    assert "beta" not in {i.name for i in store.list()}


def test_delete_missing_returns_false(state_path: Path) -> None:
    store = SessionStore(state_path)
    store.create("alpha")
    assert not store.delete("does-not-exist")


def test_delete_active_raises_when_others_exist(state_path: Path) -> None:
    store = SessionStore(state_path)
    store.create("alpha")  # active
    with pytest.raises(ValueError, match="active"):
        store.delete("alpha")


def test_delete_last_raises(state_path: Path) -> None:
    """Single remaining session is also active — 'last' takes precedence."""
    store = SessionStore(state_path)
    with pytest.raises(ValueError, match="last"):
        store.delete(store.active_name())


# ---------- corrupt recovery ----------


def test_corrupt_json_backed_up_and_fresh(state_path: Path) -> None:
    state_path.write_text("{not json at all")
    store = SessionStore(state_path)
    backups = list(state_path.parent.glob("session.json.corrupt-*"))
    assert len(backups) == 1
    assert state_path.exists()
    assert store.get()


def test_invalid_shape_backed_up(state_path: Path) -> None:
    state_path.write_text(json.dumps({"unrelated": "data"}))
    SessionStore(state_path)
    assert len(list(state_path.parent.glob("session.json.corrupt-*"))) == 1


def test_active_pointer_to_missing_session_backed_up(state_path: Path) -> None:
    state_path.write_text(
        json.dumps(
            {
                "active": "ghost",
                "sessions": {
                    "real": {
                        "uuid": "u",
                        "initialized": False,
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                },
            }
        )
    )
    SessionStore(state_path)
    assert len(list(state_path.parent.glob("session.json.corrupt-*"))) == 1


def test_non_dict_root_backed_up(state_path: Path) -> None:
    state_path.write_text(json.dumps(["not", "an", "object"]))
    SessionStore(state_path)
    assert len(list(state_path.parent.glob("session.json.corrupt-*"))) == 1


# ---------- persistence ----------


def test_persistence_roundtrip(state_path: Path) -> None:
    s1 = SessionStore(state_path)
    s1.mark_initialized()
    s1.create("alpha")
    s1.create("beta")
    s1.switch("alpha")
    uuid_alpha = s1.get()

    s2 = SessionStore(state_path)
    assert s2.active_name() == "alpha"
    assert s2.get() == uuid_alpha
    assert {"alpha", "beta"}.issubset({i.name for i in s2.list()})


def test_save_does_not_leave_tmp_file(state_path: Path) -> None:
    store = SessionStore(state_path)
    store.create("alpha")
    store.mark_initialized()
    assert not (state_path.parent / "session.tmp").exists()


# ---------- timezone behavior (Step 4.5) ----------


def test_auto_name_uses_local_time(with_tz) -> None:
    with_tz("Asia/Tokyo")  # UTC+9, far from UTC
    expected_prefix = datetime.now().astimezone().strftime("%Y-%m-%d-%H%M")
    assert _gen_name(set()).startswith(expected_prefix)


def test_created_at_stays_utc_regardless_of_tz(state_path: Path, with_tz) -> None:
    with_tz("Asia/Tokyo")
    store = SessionStore(state_path)
    info = store.list()[0]
    assert info.created_at.utcoffset().total_seconds() == 0
    raw = _read(state_path)
    assert raw["sessions"][info.name]["created_at"].endswith("+00:00")


def test_auto_name_collision_appends_suffix() -> None:
    # Two calls in the same minute: the second should suffix with -2.
    # If the minute rolls between calls, the test would flake — re-run.
    base = _gen_name(set())
    assert _gen_name({base}) == f"{base}-2"
    assert _gen_name({base, f"{base}-2"}) == f"{base}-3"


# ---------- name validation helper ----------


def test_validate_name_rejects_non_str() -> None:
    with pytest.raises(ValueError):
        _validate_name(123)  # type: ignore[arg-type]


def test_validate_name_accepts_max_length() -> None:
    _validate_name("x" * 64)  # 64 is the max


def test_validate_name_rejects_over_max() -> None:
    with pytest.raises(ValueError):
        _validate_name("x" * 65)
