"""Tests for core/status.py — atomic write, read-back, target extraction.

Tests follow the codebase convention of sync test functions (no
pytest-asyncio); status reads/writes are themselves synchronous.

The runtime_dir() helper is monkeypatched to point at a tmp_path so
tests don't touch /run/user/$UID.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from core import paths, status


@pytest.fixture
def patch_runtime(monkeypatch, tmp_path) -> Path:
    """Redirect runtime_dir() at a tmpdir for both core.paths and the
    re-imported reference inside core.status."""
    monkeypatch.setattr(paths, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(status, "runtime_dir", lambda: tmp_path)
    return tmp_path


# --- target extraction ------------------------------------------------------


def test_extract_target_for_edit_uses_file_path():
    assert (
        status.extract_tool_target("Edit", {"file_path": "src/foo.py"})
        == "src/foo.py"
    )


def test_extract_target_for_bash_uses_command():
    assert (
        status.extract_tool_target("Bash", {"command": "git status"})
        == "git status"
    )


def test_extract_target_truncates_long_values():
    long = "a" * 200
    out = status.extract_tool_target("Bash", {"command": long})
    assert out is not None
    assert out.endswith("…")
    assert len(out) == 61  # 60 + ellipsis


def test_extract_target_collapses_newlines_in_command():
    out = status.extract_tool_target(
        "Bash", {"command": "echo line1\nline2\nline3"}
    )
    assert out == "echo line1 line2 line3"


def test_extract_target_returns_none_for_unknown_tool():
    assert status.extract_tool_target("Mystery", {"file_path": "x"}) is None


def test_extract_target_returns_none_for_missing_key():
    assert status.extract_tool_target("Edit", {}) is None


def test_extract_target_returns_none_for_empty_value():
    assert status.extract_tool_target("Edit", {"file_path": ""}) is None
    assert status.extract_tool_target("Edit", {"file_path": "   "}) is None


def test_extract_target_returns_none_for_non_string_value():
    assert status.extract_tool_target("Edit", {"file_path": 42}) is None


# --- StatusFile lifecycle ---------------------------------------------------


def test_start_writes_initial_payload(patch_runtime):
    sf = status.StatusFile(chat_id=42)
    sf.start()
    snapshot = status.read_status(42)
    assert snapshot is not None
    assert snapshot.chat_id == 42
    assert snapshot.tool_count == 0
    assert snapshot.last_tool is None
    assert snapshot.last_target is None
    assert isinstance(snapshot.started_at, datetime)


def test_record_tool_increments_and_updates_last(patch_runtime):
    sf = status.StatusFile(chat_id=43)
    sf.start()
    sf.record_tool("Edit", "core/foo.py")
    sf.record_tool("Bash", "git status")
    snap = status.read_status(43)
    assert snap is not None
    assert snap.tool_count == 2
    assert snap.last_tool == "Bash"
    assert snap.last_target == "git status"


def test_record_tool_with_no_target_omits_field(patch_runtime):
    sf = status.StatusFile(chat_id=44)
    sf.start()
    sf.record_tool("TodoWrite", None)
    snap = status.read_status(44)
    assert snap is not None
    assert snap.last_tool == "TodoWrite"
    assert snap.last_target is None
    # Underlying JSON should not contain the key at all.
    raw = (patch_runtime / "status-44.json").read_text()
    payload = json.loads(raw)
    assert "last_target" not in payload


def test_delete_removes_file(patch_runtime):
    sf = status.StatusFile(chat_id=45)
    sf.start()
    assert (patch_runtime / "status-45.json").exists()
    sf.delete()
    assert not (patch_runtime / "status-45.json").exists()


def test_delete_is_idempotent_when_file_missing(patch_runtime):
    sf = status.StatusFile(chat_id=46)
    # Never called start(); delete() must not raise.
    sf.delete()


def test_write_before_start_is_a_noop(patch_runtime):
    sf = status.StatusFile(chat_id=47)
    sf.record_tool("Edit", "foo.py")  # no start() yet
    assert not (patch_runtime / "status-47.json").exists()


# --- read_status edge cases -------------------------------------------------


def test_read_status_returns_none_for_missing_file(patch_runtime):
    assert status.read_status(99) is None


def test_read_status_handles_corrupt_json(patch_runtime):
    (patch_runtime / "status-50.json").write_text("not json {{{")
    assert status.read_status(50) is None


def test_read_status_handles_missing_required_keys(patch_runtime):
    (patch_runtime / "status-51.json").write_text(json.dumps({"chat_id": 51}))
    assert status.read_status(51) is None


# --- atomic-write guarantee -------------------------------------------------


def test_write_uses_tmpfile_then_rename(patch_runtime):
    """Verify a partial write isn't visible: there should be no torn
    file. Easiest check — after a successful write, only the final
    file exists, and parsing it gives a complete snapshot."""
    sf = status.StatusFile(chat_id=60)
    sf.start()
    final = patch_runtime / "status-60.json"
    tmp = patch_runtime / "status-60.json.tmp"
    assert final.exists()
    assert not tmp.exists()
    snap = status.read_status(60)
    assert snap is not None and snap.chat_id == 60


# --- cleanup_all ------------------------------------------------------------


def test_cleanup_all_removes_status_files_and_tmp_files(patch_runtime):
    (patch_runtime / "status-1.json").write_text("{}")
    (patch_runtime / "status-2.json").write_text("{}")
    (patch_runtime / "status-3.json.tmp").write_text("partial")
    (patch_runtime / "unrelated.txt").write_text("keep me")

    removed = status.cleanup_all()

    assert removed == 2
    assert not (patch_runtime / "status-1.json").exists()
    assert not (patch_runtime / "status-2.json").exists()
    assert not (patch_runtime / "status-3.json.tmp").exists()
    assert (patch_runtime / "unrelated.txt").exists()


def test_cleanup_all_on_empty_dir_returns_zero(patch_runtime):
    assert status.cleanup_all() == 0
