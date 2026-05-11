"""Tests for ``ensure_opencode_safety_plugin`` — opencode path of Step 6.5.

Covers:
  * Fresh-write: plugin file lands at the canonical path; opencode.json
    is created with a ``plugin: [...]`` entry pointing at it.
  * Plugin source byte-parity with the shipped data file (catches a
    future bug where the installer renders a stale copy).
  * Idempotency: second call writes nothing to disk.
  * Merge with user-owned plugin entries (preserved verbatim) and
    user-owned top-level opencode.json keys (preserved).
  * Sentinel-based update-in-place: an entry with a stale path
    containing the sentinel substring is rewritten in place rather
    than duplicated.
  * Tuple-shaped plugin entries (``[path, options]``) preserved.
  * End-to-end brain wiring: constructing ``OpenCodeBrain`` runs
    the installer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.safety_install import (
    _OPENCODE_PLUGIN_FILENAME,
    _OPENCODE_PLUGIN_SENTINEL,
    _read_plugin_source,
    ensure_opencode_safety_plugin,
)
from vexis_agent.core.sessions import SessionStore


def _opencode_json(workspace: Path) -> dict:
    return json.loads(
        (workspace / "opencode.json").read_text(encoding="utf-8")
    )


def _plugin_file(workspace: Path) -> Path:
    return workspace / _OPENCODE_PLUGIN_FILENAME


# ---------- fresh-write ----------


def test_fresh_workspace_installs_plugin(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    changed = ensure_opencode_safety_plugin(workspace)
    assert changed is True

    # Plugin file copied verbatim from package data.
    plugin = _plugin_file(workspace)
    assert plugin.exists()
    assert plugin.read_text(encoding="utf-8") == _read_plugin_source()

    # opencode.json references it.
    settings = _opencode_json(workspace)
    plugin_list = settings["plugin"]
    assert len(plugin_list) == 1
    assert plugin_list[0] == f"./{_OPENCODE_PLUGIN_FILENAME}"


def test_plugin_filename_contains_sentinel() -> None:
    # Defensive guard against a future rename that breaks update-
    # in-place behaviour. The sentinel-based merge relies on
    # ``_OPENCODE_PLUGIN_SENTINEL`` being a substring of the
    # filename our installer emits.
    assert _OPENCODE_PLUGIN_SENTINEL in _OPENCODE_PLUGIN_FILENAME


# ---------- idempotency ----------


def test_second_call_is_noop(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    first = ensure_opencode_safety_plugin(workspace)
    second = ensure_opencode_safety_plugin(workspace)

    assert first is True
    assert second is False


def test_repeated_calls_dont_duplicate_plugin_entry(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    ensure_opencode_safety_plugin(workspace)
    ensure_opencode_safety_plugin(workspace)
    ensure_opencode_safety_plugin(workspace)

    plugin_list = _opencode_json(workspace)["plugin"]
    matching = [
        e for e in plugin_list
        if isinstance(e, str) and _OPENCODE_PLUGIN_SENTINEL in e
    ]
    assert len(matching) == 1


# ---------- merge with user-owned config ----------


def test_unrelated_keys_preserved(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "opencode.json").write_text(
        json.dumps({
            "mcp": {"user-server": {"type": "local", "command": ["x"]}},
            "agent": {"build": {"prompt": "..."}},
            "theme": "github-dark",
        }),
        encoding="utf-8",
    )

    ensure_opencode_safety_plugin(workspace)
    settings = _opencode_json(workspace)

    assert settings["mcp"] == {
        "user-server": {"type": "local", "command": ["x"]},
    }
    assert settings["agent"] == {"build": {"prompt": "..."}}
    assert settings["theme"] == "github-dark"
    assert "plugin" in settings


def test_user_plugin_entries_preserved(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "opencode.json").write_text(
        json.dumps({
            "plugin": [
                "user-plugin",
                "./local-plugin.mjs",
                ["configurable-plugin", {"verbose": True}],
            ],
        }),
        encoding="utf-8",
    )

    ensure_opencode_safety_plugin(workspace)
    plugin_list = _opencode_json(workspace)["plugin"]

    assert "user-plugin" in plugin_list
    assert "./local-plugin.mjs" in plugin_list
    assert ["configurable-plugin", {"verbose": True}] in plugin_list
    assert any(
        isinstance(e, str) and _OPENCODE_PLUGIN_SENTINEL in e
        for e in plugin_list
    )
    assert len(plugin_list) == 4


def test_plugin_field_wrong_type_replaced(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "opencode.json").write_text(
        json.dumps({"plugin": "this should be a list", "other": 42}),
        encoding="utf-8",
    )

    ensure_opencode_safety_plugin(workspace)
    settings = _opencode_json(workspace)

    assert settings["other"] == 42
    assert isinstance(settings["plugin"], list)
    assert any(
        isinstance(e, str) and _OPENCODE_PLUGIN_SENTINEL in e
        for e in settings["plugin"]
    )


# ---------- sentinel-based update-in-place ----------


def test_stale_string_entry_updated_in_place(tmp_path: Path) -> None:
    """Simulate vexis upgrading and shifting the plugin's relpath:
    existing opencode.json has our sentinel filename but at an
    out-of-date relative path. Re-installer rewrites it, no dup."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    stale = "/old/path/.vexis-opencode-safety.mjs"
    (workspace / "opencode.json").write_text(
        json.dumps({"plugin": [stale]}),
        encoding="utf-8",
    )

    changed = ensure_opencode_safety_plugin(workspace)
    assert changed is True

    plugin_list = _opencode_json(workspace)["plugin"]
    assert len(plugin_list) == 1
    assert plugin_list[0] == f"./{_OPENCODE_PLUGIN_FILENAME}"
    assert plugin_list[0] != stale


def test_stale_tuple_entry_preserves_options(tmp_path: Path) -> None:
    """If a user (or a future vexis version) wrapped our entry as
    ``[path, {options}]``, update the path in place but preserve
    the options dict."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    stale_entry = [
        "/old/path/.vexis-opencode-safety.mjs",
        {"debug": True},
    ]
    (workspace / "opencode.json").write_text(
        json.dumps({"plugin": [stale_entry]}),
        encoding="utf-8",
    )

    ensure_opencode_safety_plugin(workspace)
    plugin_list = _opencode_json(workspace)["plugin"]

    assert len(plugin_list) == 1
    assert plugin_list[0] == [
        f"./{_OPENCODE_PLUGIN_FILENAME}",
        {"debug": True},
    ]


# ---------- plugin file freshness ----------


def test_outdated_plugin_file_rewritten(tmp_path: Path) -> None:
    """Simulate a vexis upgrade that ships a newer plugin file:
    the installer must overwrite the workspace's stale copy."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    plugin = _plugin_file(workspace)
    plugin.write_text("// stale\n", encoding="utf-8")
    # Pre-existing opencode.json with our sentinel — so the only
    # thing the installer should be doing is refreshing the plugin
    # file itself (not the JSON).
    (workspace / "opencode.json").write_text(
        json.dumps({"plugin": [f"./{_OPENCODE_PLUGIN_FILENAME}"]}, indent=2) + "\n",
        encoding="utf-8",
    )

    changed = ensure_opencode_safety_plugin(workspace)
    assert changed is True
    assert plugin.read_text(encoding="utf-8") == _read_plugin_source()


def test_plugin_data_file_is_packaged() -> None:
    """If the wheel ever loses ``data/opencode_safety_plugin.mjs``,
    this test fires before users hit it at runtime."""
    src = _read_plugin_source()
    assert "tool.execute.before" in src
    assert "vexis-safety" in src  # plugin id


# ---------- corrupt-input recovery ----------


def test_corrupt_opencode_json_replaced(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "opencode.json").write_text(
        "{not valid json", encoding="utf-8",
    )

    changed = ensure_opencode_safety_plugin(workspace)
    assert changed is True

    settings = _opencode_json(workspace)
    assert any(
        isinstance(e, str) and _OPENCODE_PLUGIN_SENTINEL in e
        for e in settings["plugin"]
    )


# ---------- end-to-end brain wiring ----------


def test_brain_construction_installs_plugin(tmp_path: Path) -> None:
    """The full smoke: instantiating OpenCodeBrain on a fresh
    workspace writes both the plugin file AND the opencode.json
    entry. Catches regressions where a future refactor of
    ``__init__`` forgets the install call."""
    pytest.importorskip("vexis_agent.core.brain.opencode")
    from vexis_agent.core.brain.opencode import OpenCodeBrain

    workspace = tmp_path / "ws"
    workspace.mkdir()
    session = SessionStore(tmp_path / "sessions.json")

    OpenCodeBrain(
        workspace=workspace,
        session=session,
        running_tasks=RunningTasks(),
    )

    assert _plugin_file(workspace).exists()
    plugin_list = _opencode_json(workspace)["plugin"]
    assert any(
        isinstance(e, str) and _OPENCODE_PLUGIN_SENTINEL in e
        for e in plugin_list
    )
