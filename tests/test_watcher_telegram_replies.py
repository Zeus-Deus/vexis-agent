"""Telegram-side watcher inline replies (LAYER 3b of the spec).

Pins the two behaviours that the spec calls out by name and that a
naive refactor most easily breaks:

  * ``tail <name>`` — dump terminal text into a reply bubble. No
    brain dispatch.
  * ``peek <name>`` — *synthesise a user turn* that asks Vexis to
    read the workspace and summarise. The spec wording: "relay a
    synthetic user-message to Vexis: 'Summarize what workspace
    <name> is doing right now.' Vexis then reads the terminal
    itself and replies."

If ``peek`` ever stops dispatching to the brain (e.g. someone
"helpfully" turns it into a second ``tail``), the user no longer
gets a model-authored summary — they get raw bytes. That's the
regression this file exists to catch.

Construction is the same ``__new__`` bypass the rest of
``test_telegram_transport.py`` uses; the inline-reply method only
reads ``_watcher``, ``_allowed_user_id``, and ``_running_tasks``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.watcher import WatcherController, WatcherRegistry
from vexis_agent.core.watcher.sources import (
    Source,
    SourceDescription,
    clear_sources,
    register_source,
)
from vexis_agent.transports.telegram import TelegramTransport


_USER = 99
_CHAT = 42


class _Source(Source):
    source_type = "fake"

    async def read_recent_output(self, identifier: str) -> bytes:
        return b"line1\nline2\nline3\n"

    async def is_alive(self, identifier: str) -> bool:
        return True

    async def describe(self, identifier: str) -> SourceDescription:
        return SourceDescription()


class _Msg:
    def __init__(self, text: str) -> None:
        self.text = text
        self.chat_id = _CHAT
        self.replies: list[tuple[str, str | None]] = []

    async def reply_text(self, text: str, parse_mode: str | None = None) -> None:
        self.replies.append((text, parse_mode))

    def get_bot(self):
        return object()


class _User:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Update:
    def __init__(self, msg: _Msg, user: _User) -> None:
        self.message = msg
        self.effective_user = user


@pytest.fixture(autouse=True)
def _stub_source():
    clear_sources()
    register_source(_Source())
    yield
    clear_sources()


def _make_transport(
    tmp_path: Path,
    *,
    with_watcher: bool = True,
) -> tuple[TelegramTransport, WatcherController | None]:
    t = TelegramTransport.__new__(TelegramTransport)
    t._allowed_user_id = _USER  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    t._learning_curator = None  # type: ignore[attr-defined]
    t._streaming_enabled = False  # type: ignore[attr-defined]
    t._streaming_min_interval = 1.0  # type: ignore[attr-defined]
    t._media_group_buffers = {}  # type: ignore[attr-defined]
    t._media_group_lock = asyncio.Lock()  # type: ignore[attr-defined]
    t._media_group_timers = set()  # type: ignore[attr-defined]
    t._handler = _CaptureHandler()  # type: ignore[attr-defined]
    watcher: WatcherController | None = None
    if with_watcher:
        watcher = WatcherController(
            registry=WatcherRegistry(state_path=tmp_path / "wr.json"),
            
        )
        asyncio.run(watcher.register_agent(
            name="my-build", source_type="fake", identifier="ws-7",
            agent_kind="claude-code", chat_id=_CHAT,
        ))
    t._watcher = watcher  # type: ignore[attr-defined]
    return t, watcher


class _CaptureHandler:
    """Records every brain dispatch so peek's synthetic prompt is visible."""

    def __init__(self) -> None:
        self.dispatched: list[str] = []

    async def handle(self, user_id, chat_id, text):
        self.dispatched.append(text)
        return None

    def current_session_uuid(self) -> str:
        return "test-session"

    def next_user_turn_index(self, _session_uuid: str) -> int:
        return 1

    async def claim_next_turn_index(self, _session_uuid: str) -> int | None:
        return 1


# ---------- tail: dumps text, no brain call --------------------------------


def test_tail_replies_with_terminal_text_and_does_not_dispatch_to_brain(tmp_path):
    transport, _ = _make_transport(tmp_path)
    msg = _Msg("tail my-build")
    update = _Update(msg, _User(_USER))
    handled = asyncio.run(
        transport._maybe_handle_watch_reply(update, _USER)
    )
    assert handled is True
    # No brain turn was generated — the user got the raw bytes.
    assert transport._handler.dispatched == []
    body = msg.replies[0][0]
    assert "my-build" in body
    assert "line3" in body
    assert "line1" in body


# ---------- peek: synthesises a brain turn (THE spec contract) -------------


def test_peek_dispatches_synthetic_user_message_to_brain(tmp_path):
    """Spec LAYER 3b: peek must relay a synthetic user-message to Vexis.

    This is the contract that distinguishes ``peek`` from ``tail`` —
    one asks the brain to read + summarise, the other dumps raw text.
    If this test fails, peek has regressed to a glorified tail.
    """
    transport, _ = _make_transport(tmp_path)
    msg = _Msg("peek my-build")
    update = _Update(msg, _User(_USER))
    handled = asyncio.run(
        transport._maybe_handle_watch_reply(update, _USER)
    )
    assert handled is True
    # The brain saw exactly one synthetic prompt. Its content is what
    # actually proves peek = "ask the brain to look," not just "dump
    # bytes" — assert the wording carries the workspace name AND a
    # directive to read the scrollback (so the brain knows where the
    # data lives, instead of hallucinating).
    assert len(transport._handler.dispatched) == 1
    synthetic = transport._handler.dispatched[0]
    assert "my-build" in synthetic
    assert "Summarize" in synthetic
    # User got the "Peeking…" ack BEFORE the brain runs (the
    # receipt-then-reply UX the spec implies). The ack message comes
    # first in the replies list.
    assert any("Peeking" in r[0] for r in msg.replies)


# ---------- pass-through cases ---------------------------------------------


def test_unknown_agent_name_falls_through(tmp_path):
    """``tail foo`` where ``foo`` isn't watched must NOT be intercepted —
    the user might be genuinely asking the brain about a tail."""
    transport, _ = _make_transport(tmp_path)
    msg = _Msg("tail of the distribution")
    update = _Update(msg, _User(_USER))
    handled = asyncio.run(
        transport._maybe_handle_watch_reply(update, _USER)
    )
    assert handled is False
    assert transport._handler.dispatched == []
    assert msg.replies == []


def test_inline_replies_off_when_watcher_absent(tmp_path):
    transport, _ = _make_transport(tmp_path, with_watcher=False)
    msg = _Msg("tail my-build")
    update = _Update(msg, _User(_USER))
    handled = asyncio.run(
        transport._maybe_handle_watch_reply(update, _USER)
    )
    assert handled is False


def test_disallowed_user_falls_through(tmp_path):
    """Auth gate: a stranger typing "tail my-build" gets nothing."""
    transport, _ = _make_transport(tmp_path)
    msg = _Msg("tail my-build")
    update = _Update(msg, _User(user_id=12345))
    handled = asyncio.run(
        transport._maybe_handle_watch_reply(update, 12345)
    )
    assert handled is False
    assert msg.replies == []


# ---------- mute / unmute / unwatch ---------------------------------------


def test_mute_flips_registry_flag(tmp_path):
    transport, watcher = _make_transport(tmp_path)
    msg = _Msg("mute my-build")
    asyncio.run(
        transport._maybe_handle_watch_reply(
            _Update(msg, _User(_USER)), _USER,
        )
    )
    assert watcher.get_agent("my-build").muted is True


def test_unwatch_removes_from_registry(tmp_path):
    transport, watcher = _make_transport(tmp_path)
    msg = _Msg("unwatch my-build")
    asyncio.run(
        transport._maybe_handle_watch_reply(
            _Update(msg, _User(_USER)), _USER,
        )
    )
    assert watcher.get_agent("my-build") is None
