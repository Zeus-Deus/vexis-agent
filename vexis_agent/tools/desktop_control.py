"""Mouse, keyboard, and Hyprland actuation.

Three actuators behind one façade:

- `dispatch()` runs `hyprctl dispatch <command>` for window/workspace ops.
- `type_text()` runs `wtype` for layout-aware UTF-8 text input.
- `click()`, `move_mouse()`, `scroll()`, `key_chord()` run `ydotool` for
  mouse and modifier-key chords.

Use `dispatch` whenever a Hyprland-native operation exists — it is faster
and immune to focus race conditions. Reach for keystrokes only for things
the compositor cannot do.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

from vexis_agent.core.subprocess import run

log = logging.getLogger(__name__)

DISPATCH_TIMEOUT_SECONDS = 5
TYPE_TIMEOUT_SECONDS = 30
CLICK_TIMEOUT_SECONDS = 2
MOUSE_TIMEOUT_SECONDS = 2
KEY_TIMEOUT_SECONDS = 2
HYPRCTL_TIMEOUT_SECONDS = 2
FOCUS_POLL_INTERVAL = 0.05

_BUTTON_CODES = {
    "left": "0xC0",
    "right": "0xC1",
    "middle": "0xC2",
}

# Linux KEY_* → integer keycode (subset of /usr/include/linux/input-event-codes.h).
# Covers the chords Vexis is plausibly going to issue. For anything outside
# this map, callers may pass raw decimal-string keycodes (e.g. "125").
_KEYCODES: dict[str, int] = {
    "KEY_ESC": 1,
    "KEY_1": 2,
    "KEY_2": 3,
    "KEY_3": 4,
    "KEY_4": 5,
    "KEY_5": 6,
    "KEY_6": 7,
    "KEY_7": 8,
    "KEY_8": 9,
    "KEY_9": 10,
    "KEY_0": 11,
    "KEY_MINUS": 12,
    "KEY_EQUAL": 13,
    "KEY_BACKSPACE": 14,
    "KEY_TAB": 15,
    "KEY_Q": 16,
    "KEY_W": 17,
    "KEY_E": 18,
    "KEY_R": 19,
    "KEY_T": 20,
    "KEY_Y": 21,
    "KEY_U": 22,
    "KEY_I": 23,
    "KEY_O": 24,
    "KEY_P": 25,
    "KEY_LEFTBRACE": 26,
    "KEY_RIGHTBRACE": 27,
    "KEY_ENTER": 28,
    "KEY_LEFTCTRL": 29,
    "KEY_A": 30,
    "KEY_S": 31,
    "KEY_D": 32,
    "KEY_F": 33,
    "KEY_G": 34,
    "KEY_H": 35,
    "KEY_J": 36,
    "KEY_K": 37,
    "KEY_L": 38,
    "KEY_SEMICOLON": 39,
    "KEY_APOSTROPHE": 40,
    "KEY_GRAVE": 41,
    "KEY_LEFTSHIFT": 42,
    "KEY_BACKSLASH": 43,
    "KEY_Z": 44,
    "KEY_X": 45,
    "KEY_C": 46,
    "KEY_V": 47,
    "KEY_B": 48,
    "KEY_N": 49,
    "KEY_M": 50,
    "KEY_COMMA": 51,
    "KEY_DOT": 52,
    "KEY_SLASH": 53,
    "KEY_RIGHTSHIFT": 54,
    "KEY_KPASTERISK": 55,
    "KEY_LEFTALT": 56,
    "KEY_SPACE": 57,
    "KEY_CAPSLOCK": 58,
    "KEY_F1": 59,
    "KEY_F2": 60,
    "KEY_F3": 61,
    "KEY_F4": 62,
    "KEY_F5": 63,
    "KEY_F6": 64,
    "KEY_F7": 65,
    "KEY_F8": 66,
    "KEY_F9": 67,
    "KEY_F10": 68,
    "KEY_F11": 87,
    "KEY_F12": 88,
    "KEY_HOME": 102,
    "KEY_UP": 103,
    "KEY_PAGEUP": 104,
    "KEY_LEFT": 105,
    "KEY_RIGHT": 106,
    "KEY_END": 107,
    "KEY_DOWN": 108,
    "KEY_PAGEDOWN": 109,
    "KEY_INSERT": 110,
    "KEY_DELETE": 111,
    "KEY_RIGHTCTRL": 97,
    "KEY_RIGHTALT": 100,
    "KEY_LEFTMETA": 125,
    "KEY_RIGHTMETA": 126,
}


class ActuationError(Exception):
    """Raised when an actuator subprocess fails or is misconfigured."""


def _ydotool_env() -> dict[str, str]:
    """Build the YDOTOOL_SOCKET env override.

    Why: the daemon is launched from a context where the env may not have
    been imported into systemd. Setting it per-call is robust without
    requiring user shell hygiene.
    How to apply: passed to every ydotool invocation; ignored elsewhere.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    socket = (
        Path(runtime) / ".ydotool_socket"
        if runtime
        else Path(f"/run/user/{os.getuid()}/.ydotool_socket")
    )
    return {"YDOTOOL_SOCKET": str(socket)}


_YDOTOOL_ENV = _ydotool_env()


# ---------- hyprctl dispatch ----------


async def dispatch(command: str) -> str:
    """Run `hyprctl dispatch <command>`. Returns stdout (typically 'ok')."""
    argv = ["hyprctl", "dispatch", *command.split()]
    stdout = await _run_actuator("hyprctl", argv, DISPATCH_TIMEOUT_SECONDS)
    return stdout.decode(errors="replace").strip()


# ---------- wtype: text ----------


async def type_text(text: str) -> None:
    """Type `text` via wtype (Wayland virtual-keyboard protocol).

    Layout-aware and UTF-8 safe. The `--` sentinel is required so text
    starting with `-` isn't parsed as a flag.
    """
    await _run_actuator("wtype", ["wtype", "--", text], TYPE_TIMEOUT_SECONDS)


# ---------- ydotool: mouse + chords ----------


async def click(button: str = "left", count: int = 1) -> None:
    """Click a mouse button `count` times in a single ydotool invocation.

    `button` is "left", "right", or "middle". A single invocation keeps the
    full press-release sequence atomic, which matters when the daemon dies
    mid-action.
    """
    code = _BUTTON_CODES.get(button)
    if code is None:
        raise ActuationError(f"unknown button: {button!r}")
    if count < 1:
        raise ActuationError(f"count must be >= 1, got {count}")
    argv = ["ydotool", "click"]
    if count > 1:
        argv += ["--repeat", str(count)]
    argv.append(code)
    await _run_actuator("ydotool", argv, CLICK_TIMEOUT_SECONDS, env=_YDOTOOL_ENV)


async def move_mouse(x: int, y: int, relative: bool = False) -> None:
    """Move the cursor. Absolute by default; pass relative=True for delta moves."""
    argv = ["ydotool", "mousemove"]
    if not relative:
        argv.append("--absolute")
    argv += ["--", str(x), str(y)]
    await _run_actuator("ydotool", argv, MOUSE_TIMEOUT_SECONDS, env=_YDOTOOL_ENV)


async def scroll(direction: str, amount: int = 1) -> None:
    """Scroll vertically by `amount` ticks.

    `direction` is "up" or "down". Implemented via `ydotool mousemove --wheel`
    (ydotool 1.0.x has no scroll-specific click codes).
    """
    if direction not in ("up", "down"):
        raise ActuationError(f"direction must be 'up' or 'down', got {direction!r}")
    if amount < 1:
        raise ActuationError(f"amount must be >= 1, got {amount}")
    delta = amount if direction == "up" else -amount
    argv = ["ydotool", "mousemove", "--wheel", "--", "0", str(delta)]
    await _run_actuator("ydotool", argv, MOUSE_TIMEOUT_SECONDS, env=_YDOTOOL_ENV)


async def key_chord(keys: list[str]) -> None:
    """Press all `keys` in order, then release in reverse order — atomically.

    `keys` are KEY_* names from input-event-codes.h (e.g. ["KEY_LEFTCTRL",
    "KEY_C"]). For obscure keys, raw decimal strings work too ("125").
    The single ydotool invocation prevents a crashed Python process from
    leaving a modifier stuck held in the kernel virtual keyboard.
    """
    if not keys:
        raise ActuationError("key_chord requires at least one key")
    codes: list[int] = []
    for k in keys:
        codes.append(_resolve_keycode(k))
    pairs = [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]
    await _run_actuator(
        "ydotool", ["ydotool", "key", *pairs], KEY_TIMEOUT_SECONDS, env=_YDOTOOL_ENV
    )


def _resolve_keycode(name: str) -> int:
    if name in _KEYCODES:
        return _KEYCODES[name]
    try:
        return int(name)
    except ValueError as exc:
        raise ActuationError(f"unknown keycode: {name!r}") from exc


# ---------- focus settling ----------


async def focus_and_wait(target_class: str, timeout: float = 2.0) -> bool:
    """Poll hyprctl until the focused window's class matches `target_class`.

    `target_class` is treated as a regex (so both "brave" and
    "^(brave-browser)$" work). Returns True if focus settled within
    `timeout`, False otherwise. Use after any focus-changing dispatcher
    and before subsequent type_text / key_chord calls.
    """
    pattern = re.compile(target_class)
    deadline = time.monotonic() + timeout
    while True:
        klass = await _active_window_class()
        if klass is not None and pattern.search(klass):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(FOCUS_POLL_INTERVAL)


async def _active_window_class() -> str | None:
    try:
        rc, stdout, stderr = await run(
            "hyprctl",
            ["hyprctl", "activewindow", "-j"],
            HYPRCTL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return None
    if rc != 0:
        log.debug("hyprctl activewindow failed: %s", stderr.decode(errors="replace"))
        return None
    body = stdout.decode(errors="replace").strip()
    if not body:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    klass = data.get("class")
    return klass if isinstance(klass, str) else None


# ---------- internal ----------


async def _run_actuator(
    name: str,
    argv: list[str],
    timeout: int,
    env: dict[str, str] | None = None,
) -> bytes:
    try:
        rc, stdout, stderr = await run(name, argv, timeout, env=env)
    except asyncio.TimeoutError as exc:
        raise ActuationError(f"{name} timed out after {timeout}s") from exc

    if rc != 0:
        err = stderr.decode(errors="replace").strip()
        raise ActuationError(f"{name} exited {rc}: {err or '(no stderr)'}")
    return stdout
