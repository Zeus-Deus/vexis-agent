"""Unit tests for tools/desktop_control.py.

Subprocess invocations are mocked: we assert argv shape, env merging,
and the order of press/release pairs in key chords. The real ydotool/
wtype/hyprctl integration is exercised by hand per Step 9's recipe.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import pytest

from vexis_agent.tools import desktop_control


@pytest.fixture
def fake_run(monkeypatch):
    """Replace core.subprocess.run with a recorder that returns a scripted
    sequence of (rc, stdout, stderr) triples."""
    calls: list[dict[str, Any]] = []
    queue: list[tuple[int, bytes, bytes]] = []

    async def _run(name, argv, timeout, env=None, cwd=None):
        calls.append(
            {
                "name": name,
                "argv": list(argv),
                "timeout": timeout,
                "env": env,
                "cwd": cwd,
            }
        )
        if not queue:
            return 0, b"", b""
        return queue.pop(0)

    monkeypatch.setattr(desktop_control, "run", _run)
    return calls, queue


# ---------- dispatch ----------


def test_dispatch_builds_hyprctl_argv(fake_run):
    calls, _ = fake_run
    out = asyncio.run(desktop_control.dispatch("workspace 3"))
    assert out == ""
    assert calls[0]["argv"] == ["hyprctl", "dispatch", "workspace", "3"]
    assert calls[0]["timeout"] == desktop_control.DISPATCH_TIMEOUT_SECONDS
    assert calls[0]["env"] is None  # hyprctl uses default env


def test_dispatch_returns_stdout(fake_run):
    _, queue = fake_run
    queue.append((0, b"ok\n", b""))
    out = asyncio.run(desktop_control.dispatch("killactive"))
    assert out == "ok"


def test_dispatch_raises_on_nonzero(fake_run):
    _, queue = fake_run
    queue.append((1, b"", b"unknown dispatcher"))
    with pytest.raises(desktop_control.ActuationError, match="exited 1"):
        asyncio.run(desktop_control.dispatch("nope"))


def test_dispatch_raises_on_timeout(fake_run, monkeypatch):
    async def _timeout(*args, **kwargs):
        raise asyncio.TimeoutError

    monkeypatch.setattr(desktop_control, "run", _timeout)
    with pytest.raises(desktop_control.ActuationError, match="timed out"):
        asyncio.run(desktop_control.dispatch("workspace 3"))


# ---------- type_text ----------


def test_type_text_uses_double_dash_separator(fake_run):
    calls, _ = fake_run
    asyncio.run(desktop_control.type_text("-rf hello"))
    assert calls[0]["argv"] == ["wtype", "--", "-rf hello"]


def test_type_text_passes_utf8_unchanged(fake_run):
    calls, _ = fake_run
    asyncio.run(desktop_control.type_text("user@example.com — €5"))
    assert calls[0]["argv"][-1] == "user@example.com — €5"


# ---------- click ----------


@pytest.mark.parametrize(
    "button,code", [("left", "0xC0"), ("right", "0xC1"), ("middle", "0xC2")]
)
def test_click_emits_press_release_combined_code(fake_run, button, code):
    calls, _ = fake_run
    asyncio.run(desktop_control.click(button=button))
    assert calls[0]["argv"] == ["ydotool", "click", code]


def test_click_repeat_flag_for_count_gt_one(fake_run):
    calls, _ = fake_run
    asyncio.run(desktop_control.click(button="left", count=3))
    assert calls[0]["argv"] == ["ydotool", "click", "--repeat", "3", "0xC0"]


def test_click_unknown_button_raises():
    with pytest.raises(desktop_control.ActuationError):
        asyncio.run(desktop_control.click(button="thumb"))


def test_click_passes_ydotool_socket_env(fake_run):
    calls, _ = fake_run
    asyncio.run(desktop_control.click(button="left"))
    env = calls[0]["env"]
    assert env is not None
    assert "YDOTOOL_SOCKET" in env
    assert env["YDOTOOL_SOCKET"].endswith(".ydotool_socket")


# ---------- move_mouse ----------


def test_move_mouse_absolute_by_default(fake_run):
    calls, _ = fake_run
    asyncio.run(desktop_control.move_mouse(100, 200))
    assert calls[0]["argv"] == [
        "ydotool",
        "mousemove",
        "--absolute",
        "--",
        "100",
        "200",
    ]


def test_move_mouse_relative_omits_absolute_flag(fake_run):
    calls, _ = fake_run
    asyncio.run(desktop_control.move_mouse(10, -5, relative=True))
    assert calls[0]["argv"] == ["ydotool", "mousemove", "--", "10", "-5"]


# ---------- scroll ----------


def test_scroll_up_uses_positive_y(fake_run):
    calls, _ = fake_run
    asyncio.run(desktop_control.scroll("up", amount=3))
    assert calls[0]["argv"] == ["ydotool", "mousemove", "--wheel", "--", "0", "3"]


def test_scroll_down_uses_negative_y(fake_run):
    calls, _ = fake_run
    asyncio.run(desktop_control.scroll("down", amount=2))
    assert calls[0]["argv"] == ["ydotool", "mousemove", "--wheel", "--", "0", "-2"]


def test_scroll_invalid_direction():
    with pytest.raises(desktop_control.ActuationError):
        asyncio.run(desktop_control.scroll("sideways"))


# ---------- key_chord ----------


def test_key_chord_press_then_release_in_reverse_order(fake_run):
    calls, _ = fake_run
    asyncio.run(desktop_control.key_chord(["KEY_LEFTCTRL", "KEY_C"]))
    # 29:1 (ctrl down), 46:1 (c down), 46:0 (c up), 29:0 (ctrl up)
    assert calls[0]["argv"] == ["ydotool", "key", "29:1", "46:1", "46:0", "29:0"]


def test_key_chord_single_invocation_atomicity(fake_run):
    """The contract: one ydotool subprocess for the entire chord."""
    calls, _ = fake_run
    asyncio.run(desktop_control.key_chord(["KEY_LEFTALT", "KEY_TAB"]))
    assert len(calls) == 1


def test_key_chord_accepts_raw_int_strings_as_fallback(fake_run):
    calls, _ = fake_run
    asyncio.run(desktop_control.key_chord(["125", "KEY_L"]))
    assert calls[0]["argv"] == ["ydotool", "key", "125:1", "38:1", "38:0", "125:0"]


def test_key_chord_unknown_name_raises():
    with pytest.raises(desktop_control.ActuationError, match="unknown keycode"):
        asyncio.run(desktop_control.key_chord(["KEY_PROBABLY_NOT_REAL"]))


def test_key_chord_empty_raises():
    with pytest.raises(desktop_control.ActuationError):
        asyncio.run(desktop_control.key_chord([]))


# ---------- focus_and_wait ----------


def test_focus_and_wait_returns_true_when_class_matches(monkeypatch):
    async def _run(name, argv, timeout, env=None, cwd=None):
        return 0, json.dumps({"class": "brave-browser"}).encode(), b""

    monkeypatch.setattr(desktop_control, "run", _run)
    ok = asyncio.run(desktop_control.focus_and_wait("brave-browser", timeout=0.5))
    assert ok is True


def test_focus_and_wait_regex_substring_match(monkeypatch):
    async def _run(name, argv, timeout, env=None, cwd=None):
        return 0, json.dumps({"class": "com.mitchellh.ghostty"}).encode(), b""

    monkeypatch.setattr(desktop_control, "run", _run)
    ok = asyncio.run(desktop_control.focus_and_wait("ghostty", timeout=0.5))
    assert ok is True


def test_focus_and_wait_returns_false_on_timeout(monkeypatch):
    async def _run(name, argv, timeout, env=None, cwd=None):
        return 0, json.dumps({"class": "firefox"}).encode(), b""

    monkeypatch.setattr(desktop_control, "run", _run)
    monkeypatch.setattr(desktop_control, "FOCUS_POLL_INTERVAL", 0.01)
    ok = asyncio.run(desktop_control.focus_and_wait("brave", timeout=0.05))
    assert ok is False


def test_focus_and_wait_handles_non_dict_json(monkeypatch):
    async def _run(name, argv, timeout, env=None, cwd=None):
        return 0, b"[]", b""

    monkeypatch.setattr(desktop_control, "run", _run)
    monkeypatch.setattr(desktop_control, "FOCUS_POLL_INTERVAL", 0.01)
    ok = asyncio.run(desktop_control.focus_and_wait("brave", timeout=0.05))
    assert ok is False


def test_focus_and_wait_handles_hyprctl_failure(monkeypatch):
    async def _run(name, argv, timeout, env=None, cwd=None):
        return 1, b"", b"no focused window"

    monkeypatch.setattr(desktop_control, "run", _run)
    monkeypatch.setattr(desktop_control, "FOCUS_POLL_INTERVAL", 0.01)
    ok = asyncio.run(desktop_control.focus_and_wait("brave", timeout=0.05))
    assert ok is False


# ---------- env merging ----------


def test_ydotool_env_uses_xdg_runtime_dir(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1234")
    env = desktop_control._ydotool_env()
    assert env == {"YDOTOOL_SOCKET": "/run/user/1234/.ydotool_socket"}


def test_ydotool_env_falls_back_to_uid(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    env = desktop_control._ydotool_env()
    assert env["YDOTOOL_SOCKET"] == f"/run/user/{os.getuid()}/.ydotool_socket"


def test_core_subprocess_run_merges_env_over_os_environ(monkeypatch):
    """The shared runner must merge caller env over current env, not replace it.
    Otherwise YDOTOOL_SOCKET would clobber PATH/HOME and ydotool wouldn't even
    exec."""
    from vexis_agent.core import subprocess as csp

    captured: dict[str, Any] = {}

    async def fake_create_subprocess_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")

        class _Proc:
            pid = 12345
            returncode = 0

            async def communicate(self):
                return b"", b""

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setenv("PATH", "/sentinel/path")

    asyncio.run(csp.run("echo", ["echo", "hi"], 1, env={"YDOTOOL_SOCKET": "/x"}))
    env = captured["env"]
    assert env is not None
    assert env["YDOTOOL_SOCKET"] == "/x"
    assert env["PATH"] == "/sentinel/path"
