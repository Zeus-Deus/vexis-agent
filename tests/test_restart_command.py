"""Tests for the /restart self-restart command.

Covers the three moving parts of the in-place daemon restart:

  1. ``main._restart_argv`` — the pure re-exec argv builder.
  2. ``TelegramTransport.request_restart`` — flag + shutdown-event trip.
  3. ``TelegramTransport._on_restart`` — user gating, ack, and that an
     authorized call ends with both the flag set and ``run()``'s wait
     released.

The actual ``os.execv`` and the run-loop teardown are exercised
end-to-end by the Docker smoke driver (scripts/restart_smoke.py), not
here — unit tests must never replace the running process image.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import vexis_agent.main as main_mod
from vexis_agent.transports.telegram import TelegramTransport, _RESTART_ACK


_ALLOWED = 99
_CHAT = 42


class _Bot:
    def __init__(self) -> None:
        self.replies: list[str] = []


class _Message:
    def __init__(self, chat_id: int, bot: _Bot) -> None:
        self.chat_id = chat_id
        self._bot = bot

    def get_bot(self) -> _Bot:
        return self._bot

    async def reply_text(self, text: str, **_kw: Any) -> None:
        self._bot.replies.append(text)


class _User:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Update:
    def __init__(self, chat_id: int, user_id: int, bot: _Bot) -> None:
        self.message = _Message(chat_id, bot)
        self.effective_user = _User(user_id)


def _make_transport() -> TelegramTransport:
    """Build a transport without PTB's Application — only the attributes
    the restart path touches."""
    t = TelegramTransport.__new__(TelegramTransport)
    t._allowed_user_id = _ALLOWED  # type: ignore[attr-defined]
    t._restart_requested = False  # type: ignore[attr-defined]
    t._shutdown_event = asyncio.Event()  # type: ignore[attr-defined]
    return t


# --- _restart_argv ---------------------------------------------------------


def test_restart_argv_uses_module_entry_and_current_interpreter():
    argv = main_mod._restart_argv()
    assert argv == [sys.executable, "-m", "vexis_agent.main"]


def test_restart_argv_is_pure():
    # No side effects, stable across calls.
    assert main_mod._restart_argv() == main_mod._restart_argv()


# --- request_restart -------------------------------------------------------


def test_request_restart_sets_flag_and_trips_event():
    transport = _make_transport()

    async def scenario() -> bool:
        # Mirrors run(): a coroutine parked on the shutdown event.
        waiter = asyncio.create_task(transport._shutdown_event.wait())
        await asyncio.sleep(0)  # let the waiter park
        transport.request_restart()
        # call_soon defers the trip one tick — event not set synchronously.
        assert transport._restart_requested is True
        await asyncio.wait_for(waiter, timeout=1.0)
        return transport._shutdown_event.is_set()

    assert asyncio.run(scenario()) is True


def test_request_restart_outside_loop_sets_event_directly():
    # The non-async fallback path (no running loop) still trips the event
    # so callers in sync contexts (tests, signal-ish paths) work.
    transport = _make_transport()
    transport.request_restart()
    assert transport._restart_requested is True
    assert transport._shutdown_event.is_set() is True


def test_request_restart_is_idempotent():
    transport = _make_transport()
    transport.request_restart()
    transport.request_restart()
    assert transport._restart_requested is True
    assert transport._shutdown_event.is_set() is True


# --- _on_restart -----------------------------------------------------------


def test_on_restart_authorized_acks_and_requests_restart():
    transport = _make_transport()
    bot = _Bot()
    update = _Update(_CHAT, _ALLOWED, bot)

    async def scenario() -> None:
        waiter = asyncio.create_task(transport._shutdown_event.wait())
        await asyncio.sleep(0)
        await transport._on_restart(update, None)
        await asyncio.wait_for(waiter, timeout=1.0)

    asyncio.run(scenario())

    assert bot.replies == [_RESTART_ACK]
    assert transport._restart_requested is True
    assert transport._shutdown_event.is_set() is True


def test_on_restart_rejects_unauthorized_user():
    transport = _make_transport()
    bot = _Bot()
    update = _Update(_CHAT, user_id=12345, bot=bot)

    asyncio.run(transport._on_restart(update, None))

    # No ack, no restart, event untouched — silent rejection like the
    # other gated commands.
    assert bot.replies == []
    assert transport._restart_requested is False
    assert transport._shutdown_event.is_set() is False


def test_on_restart_ignores_update_without_message():
    transport = _make_transport()

    class _NoMsg:
        message = None
        effective_user = _User(_ALLOWED)

    asyncio.run(transport._on_restart(_NoMsg(), None))
    assert transport._restart_requested is False
