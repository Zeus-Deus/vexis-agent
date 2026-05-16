"""Unit tests for tools/session_lock.py.

Covers:
* loginctl LockedHint='yes' → True
* loginctl LockedHint='no' → falls back to pgrep, returns False when
  no lock process is running
* pgrep matches a known lock-screen process name → True
* Any subprocess error → False (defensive)
* No XDG_SESSION_ID env → loginctl probe skipped, pgrep still runs

Convention: sync `def test_*` calling asyncio.run, matching the
test_livestream.py convention rather than pytest-asyncio.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from vexis_agent.tools import session_lock


def _fake_run(rc_map: dict[str, tuple[int, bytes, bytes]]):
    """Build an async fake for ``vexis_agent.core.subprocess.run``.

    Picks responses by matching the second arg (the argv list) on
    its first element (``loginctl`` / ``pgrep``) + lookup key in
    ``rc_map``. Unmapped calls return ``(1, b"", b"unmatched")``.
    """

    async def fake(name: str, argv: list[str], timeout: int):
        key = name
        if name == "pgrep":
            # Encode the target process name so we can vary per-process.
            target = argv[2] if len(argv) > 2 else ""
            key = f"pgrep:{target}"
        return rc_map.get(key, (1, b"", b"unmatched"))

    return fake


def test_locked_hint_yes_returns_true():
    fake = _fake_run({"loginctl": (0, b"yes\n", b"")})
    with patch.dict("os.environ", {"XDG_SESSION_ID": "5"}, clear=False):
        with patch.object(session_lock, "run", fake):
            assert asyncio.run(session_lock.is_session_locked()) is True


def test_locked_hint_no_and_no_lock_process_returns_false():
    # loginctl says no; every pgrep returns rc=1 (no match).
    fake = _fake_run({"loginctl": (0, b"no\n", b"")})
    with patch.dict("os.environ", {"XDG_SESSION_ID": "5"}, clear=False):
        with patch.object(session_lock, "run", fake):
            assert asyncio.run(session_lock.is_session_locked()) is False


def test_pgrep_match_returns_true_when_loginctl_says_no():
    # loginctl says no; pgrep finds hyprlock.
    fake = _fake_run(
        {
            "loginctl": (0, b"no\n", b""),
            "pgrep:hyprlock": (0, b"12345\n", b""),
        }
    )
    with patch.dict("os.environ", {"XDG_SESSION_ID": "5"}, clear=False):
        with patch.object(session_lock, "run", fake):
            assert asyncio.run(session_lock.is_session_locked()) is True


def test_loginctl_missing_session_id_skips_to_pgrep():
    fake = _fake_run({"pgrep:swaylock": (0, b"42\n", b"")})
    env = {k: v for k, v in __import__("os").environ.items() if k != "XDG_SESSION_ID"}
    with patch.dict("os.environ", env, clear=True):
        with patch.object(session_lock, "run", fake):
            assert asyncio.run(session_lock.is_session_locked()) is True


def test_loginctl_rc_nonzero_falls_back_to_pgrep():
    fake = _fake_run(
        {
            "loginctl": (1, b"", b"err"),
            "pgrep:i3lock": (0, b"99\n", b""),
        }
    )
    with patch.dict("os.environ", {"XDG_SESSION_ID": "5"}, clear=False):
        with patch.object(session_lock, "run", fake):
            assert asyncio.run(session_lock.is_session_locked()) is True


def test_subprocess_oserror_swallows_to_false():
    async def boom(name, argv, timeout):
        raise OSError("simulated")

    with patch.dict("os.environ", {"XDG_SESSION_ID": "5"}, clear=False):
        with patch.object(session_lock, "run", boom):
            # Defensive: any error must produce False, never raise.
            assert asyncio.run(session_lock.is_session_locked()) is False


def test_locked_hint_yes_case_insensitive():
    fake = _fake_run({"loginctl": (0, b"YES\n", b"")})
    with patch.dict("os.environ", {"XDG_SESSION_ID": "5"}, clear=False):
        with patch.object(session_lock, "run", fake):
            assert asyncio.run(session_lock.is_session_locked()) is True
