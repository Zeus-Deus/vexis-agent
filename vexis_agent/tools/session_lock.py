"""Detect whether the host login session is locked.

Used by the screenshot/livestream router to annotate captures with a
"host is locked" hint when the user asks for the real desktop and the
real desktop is currently a lock-screen surface.

Two signals are checked in order; first hit wins:

1. ``loginctl show-session $XDG_SESSION_ID -p LockedHint --value`` — the
   systemd-logind property toggled by ``loginctl lock-session``. Works
   on Hyprland when hyprlock is launched via ``loginctl lock-session``
   (which is the default keybind ``omarchy-cmd-lock`` ships).
2. A live ``pgrep`` for a known lock-screen process
   (``hyprlock``, ``swaylock``, ``i3lock``, ``gnome-screensaver``,
   ``xscreensaver``). Backstop for users who launch the locker
   directly rather than going through ``loginctl``.

All errors swallow to ``False``. A failed probe must never poison the
screenshot path — the worst case is the user gets a lock-screen
capture without the helpful caption hint.
"""

from __future__ import annotations

import asyncio
import logging
import os

from vexis_agent.core.subprocess import run

log = logging.getLogger(__name__)

LOGINCTL_TIMEOUT_SECONDS = 2
PGREP_TIMEOUT_SECONDS = 2

# Names matched by ``pgrep -x``. Keep this list short and specific —
# matching on substrings would catch unrelated processes.
LOCK_PROCESS_NAMES = (
    "hyprlock",
    "swaylock",
    "i3lock",
    "gnome-screensaver",
    "xscreensaver",
)


async def is_session_locked() -> bool:
    """Return True iff the host session is currently locked.

    Defensive: any probe failure returns False rather than raising,
    because callers use this to decide whether to *add* a hint, not
    whether to capture at all.
    """
    if await _loginctl_locked_hint():
        return True
    if await _lock_process_running():
        return True
    return False


async def _loginctl_locked_hint() -> bool:
    session_id = os.environ.get("XDG_SESSION_ID")
    if not session_id:
        return False
    argv = [
        "loginctl",
        "show-session",
        session_id,
        "-p",
        "LockedHint",
        "--value",
    ]
    try:
        rc, stdout, _ = await run("loginctl", argv, LOGINCTL_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, FileNotFoundError, OSError) as exc:
        log.debug("loginctl LockedHint probe failed: %s", exc)
        return False
    if rc != 0:
        return False
    return stdout.decode(errors="replace").strip().lower() == "yes"


async def _lock_process_running() -> bool:
    # ``pgrep -x`` matches only the exact process name, not a substring.
    # We OR the names with ``-d ,`` instead of multiple invocations.
    argv = ["pgrep", "-x", ",".join(LOCK_PROCESS_NAMES), "-d", ","]
    # pgrep doesn't actually accept comma-separated names — fall back
    # to looping. Kept the comment above so a future contributor who
    # tries the "clever" form doesn't waste five minutes.
    for name in LOCK_PROCESS_NAMES:
        try:
            rc, _, _ = await run("pgrep", ["pgrep", "-x", name], PGREP_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, FileNotFoundError, OSError) as exc:
            log.debug("pgrep %s failed: %s", name, exc)
            continue
        if rc == 0:
            return True
    return False
