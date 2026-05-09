"""Unit tests for tools/desktop.py — pure logic only.

The grim/hyprctl integration paths require a live Wayland session and
are exercised by hand per Step 7's verification recipe."""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.tools.desktop import (
    CaptureError,
    _build_grim_argv,
    _build_state,
    _build_summary,
    _pretty_class,
)
from vexis_agent.transports.telegram import _extract_screenshot_paths


# ---------- state shape ----------


def _monitors():
    return [
        {
            "id": 0,
            "name": "DP-1",
            "focused": True,
            "activeWorkspace": {"id": 3, "name": "3"},
            "width": 2560,
            "height": 1440,
        },
        {
            "id": 1,
            "name": "HDMI-A-1",
            "focused": False,
            "activeWorkspace": {"id": 5, "name": "5"},
            "width": 1920,
            "height": 1080,
        },
    ]


def _client(
    *, ws_id, monitor=0, focus_id=1, klass="firefox", title="t", floating=False
):
    return {
        "title": title,
        "class": klass,
        "workspace": {"id": ws_id, "name": str(ws_id)},
        "monitor": monitor,
        "at": [10, 20],
        "size": [1000, 800],
        "focusHistoryID": focus_id,
        "floating": floating,
    }


def test_build_state_maps_monitor_id_to_name_and_marks_focus():
    monitors = _monitors()
    active_ws = {"id": 3, "name": "3"}
    clients = [
        _client(ws_id=3, monitor=0, focus_id=0, klass="firefox"),
        _client(ws_id=5, monitor=1, focus_id=2, klass="ghostty"),
    ]
    state = _build_state(monitors, active_ws, clients)

    assert state["active_workspace"] == {"id": 3, "name": "3"}
    assert state["focused_monitor"] == {"name": "DP-1", "width": 2560, "height": 1440}
    assert [w["focused"] for w in state["windows"]] == [True, False]
    assert state["windows"][0]["monitor"] == "DP-1"
    assert state["windows"][1]["monitor"] == "HDMI-A-1"
    assert state["windows"][0]["workspace_id"] == 3


# ---------- summary ----------


def test_summary_empty_desktop():
    state = _build_state(_monitors(), {"id": 7, "name": "7"}, [])
    assert _build_summary(state) == "Workspace 7 on DP-1 — empty desktop"


def test_summary_focused_only():
    clients = [_client(ws_id=3, focus_id=0, klass="firefox")]
    state = _build_state(_monitors(), {"id": 3, "name": "3"}, clients)
    assert _build_summary(state) == "Workspace 3 on DP-1 — Firefox"


def test_summary_focused_plus_one_other_says_tiled_with():
    clients = [
        _client(ws_id=3, focus_id=0, klass="com.mitchellh.ghostty"),
        _client(ws_id=3, focus_id=1, klass="firefox"),
    ]
    state = _build_state(_monitors(), {"id": 3, "name": "3"}, clients)
    assert _build_summary(state) == "Workspace 3 on DP-1 — Ghostty, tiled with Firefox"


def test_summary_focused_plus_many_others_says_plus_n():
    clients = [
        _client(ws_id=3, focus_id=0, klass="firefox"),
        _client(ws_id=3, focus_id=1, klass="code"),
        _client(ws_id=3, focus_id=2, klass="ghostty"),
    ]
    state = _build_state(_monitors(), {"id": 3, "name": "3"}, clients)
    assert (
        _build_summary(state)
        == "Workspace 3 on DP-1 — Firefox, +2 other windows visible"
    )


def test_summary_notes_other_workspaces_with_windows():
    clients = [
        _client(ws_id=3, focus_id=0, klass="firefox"),
        _client(ws_id=1, focus_id=2, klass="code"),
        _client(ws_id=5, focus_id=3, klass="ghostty"),
    ]
    state = _build_state(_monitors(), {"id": 3, "name": "3"}, clients)
    summary = _build_summary(state)
    assert summary.startswith("Workspace 3 on DP-1 — Firefox")
    assert summary.endswith(", 2 other workspaces have windows")


def test_summary_singular_other_workspace_uses_has():
    clients = [
        _client(ws_id=3, focus_id=0, klass="firefox"),
        _client(ws_id=1, focus_id=2, klass="code"),
    ]
    state = _build_state(_monitors(), {"id": 3, "name": "3"}, clients)
    summary = _build_summary(state)
    assert summary.endswith(", 1 other workspace has windows")


# ---------- pretty_class ----------


@pytest.mark.parametrize(
    "raw,pretty",
    [
        ("firefox", "Firefox"),
        ("brave-web.telegram.org__-Profile_1", "Brave"),
        ("brave-browser", "Brave"),
        ("com.mitchellh.ghostty", "Ghostty"),
        ("org.gnome.Nautilus", "Nautilus"),
        ("", "?"),
    ],
)
def test_pretty_class(raw: str, pretty: str):
    assert _pretty_class(raw) == pretty


# ---------- grim argv ----------


def test_grim_argv_focused_monitor_uses_focused_name():
    argv = _build_grim_argv("focused-monitor", _monitors(), [], Path("/tmp/x.png"))
    assert argv == ["grim", "-o", "DP-1", "/tmp/x.png"]


def test_grim_argv_all_monitors_no_scope_flag():
    argv = _build_grim_argv("all-monitors", _monitors(), [], Path("/tmp/x.png"))
    assert argv == ["grim", "/tmp/x.png"]


def test_grim_argv_focused_window_uses_active_geometry():
    clients = [
        _client(ws_id=3, focus_id=2),
        _client(ws_id=3, focus_id=0),
    ]
    clients[1]["at"] = [12, 38]
    clients[1]["size"] = [2536, 1390]
    argv = _build_grim_argv("focused-window", _monitors(), clients, Path("/tmp/x.png"))
    assert argv == ["grim", "-g", "12,38 2536x1390", "/tmp/x.png"]


def test_grim_argv_focused_window_without_active_raises():
    clients = [_client(ws_id=3, focus_id=2)]
    with pytest.raises(CaptureError):
        _build_grim_argv("focused-window", _monitors(), clients, Path("/tmp/x.png"))


def test_grim_argv_focused_monitor_without_focused_raises():
    monitors = [{**m, "focused": False} for m in _monitors()]
    with pytest.raises(CaptureError):
        _build_grim_argv("focused-monitor", monitors, [], Path("/tmp/x.png"))


# ---------- screenshot path extraction (transport-side) ----------


def test_extract_screenshot_paths_finds_one_path_and_strips_it():
    text = "Done. /tmp/vexis-screenshot-12345.png is the focused monitor."
    paths, cleaned = _extract_screenshot_paths(text)
    assert paths == [Path("/tmp/vexis-screenshot-12345.png")]
    assert "vexis-screenshot" not in cleaned


def test_extract_screenshot_paths_handles_multiple_and_dedupes():
    text = (
        "First /tmp/vexis-screenshot-1.png\n"
        "Second /tmp/vexis-screenshot-2.png\n"
        "Repeat /tmp/vexis-screenshot-1.png"
    )
    paths, cleaned = _extract_screenshot_paths(text)
    assert paths == [
        Path("/tmp/vexis-screenshot-1.png"),
        Path("/tmp/vexis-screenshot-2.png"),
    ]
    assert "vexis-screenshot" not in cleaned


def test_extract_screenshot_paths_no_match_returns_text_unchanged():
    text = "Nothing relevant here."
    paths, cleaned = _extract_screenshot_paths(text)
    assert paths == []
    assert cleaned == text


def test_extract_screenshot_paths_does_not_match_across_lines():
    text = "/tmp/vexis-screenshot-\n123.png"
    paths, _ = _extract_screenshot_paths(text)
    assert paths == []


def test_extract_screenshot_paths_only_matches_our_format():
    text = "Some other path /tmp/foo.png and /var/tmp/vexis-screenshot-1.png"
    paths, _ = _extract_screenshot_paths(text)
    assert paths == []
