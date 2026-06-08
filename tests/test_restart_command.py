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
import vexis_agent.transports.telegram as telegram_mod
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


def test_restart_argv_matches_systemd_execstart():
    # Must mirror the systemd unit's ExecStart (daemon/systemd.py:
    # "{python} -m vexis_agent.cli run") so the restart lands on the
    # same launch path production uses.
    argv = main_mod._restart_argv()
    assert argv == [sys.executable, "-m", "vexis_agent.cli", "run"]


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


# --- run() shuts down cleanly on a restart trip (no hang) ------------------
#
# The failure the user fears is "it shuts down but never comes back / never
# responds." That has two halves: (a) does run() actually RETURN when a
# restart is requested, completing its teardown without hanging, and (b)
# does the re-exec target boot real daemon code. (b) is covered by the
# Docker smoke + the verified `_restart_argv` entry point. This test nails
# (a): the REAL run() body runs against a faked PTB Application (we don't
# own PTB, we own the wiring), and we assert run() returns and tears the
# transport fully down — updater, app stop, and app shutdown all fire.


class _FakeBotApi:
    async def set_my_commands(self, _cmds: Any) -> None:
        pass


class _FakeUpdater:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def start_polling(self) -> None:
        self._events.append("updater.start_polling")

    async def stop(self) -> None:
        self._events.append("updater.stop")


class _FakeApp:
    """Minimal stand-in for PTB's Application — just the lifecycle hooks
    TelegramTransport.run() calls, recording order so we can assert a
    clean start→serve→teardown."""

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.bot = _FakeBotApi()
        self.updater = _FakeUpdater(events)

    async def initialize(self) -> None:
        self._events.append("initialize")

    async def start(self) -> None:
        self._events.append("start")

    async def stop(self) -> None:
        self._events.append("stop")

    async def shutdown(self) -> None:
        self._events.append("shutdown")


class _FakeNotifier:
    def bind_app(self, _app: Any) -> None:
        pass

    async def send(self, *_a: Any, **_kw: Any) -> None:
        pass


class _FakeBackgroundTasks:
    def set_notify(self, _fn: Any) -> None:
        pass

    async def detect_lost_from_previous_run(self) -> list:
        return []


def test_run_returns_and_tears_down_on_restart_trip(monkeypatch):
    events: list[str] = []
    transport = _make_transport()
    transport._app = _FakeApp(events)  # type: ignore[attr-defined]
    transport._addon_runtime = None  # type: ignore[attr-defined]
    transport._notifier = _FakeNotifier()  # type: ignore[attr-defined]
    transport._background_tasks = _FakeBackgroundTasks()  # type: ignore[attr-defined]

    # Neutralize the two real side-effecting helpers run() invokes so the
    # test stays hermetic (they touch the runtime dir / /tmp otherwise).
    monkeypatch.setattr(telegram_mod, "cleanup_status_files", lambda: 0)

    async def _never() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(telegram_mod, "_incoming_file_cleanup_loop", _never)

    async def scenario() -> None:
        run_task = asyncio.create_task(transport.run())
        # Wait until run() has fully started (polling began).
        for _ in range(200):
            if "updater.start_polling" in events:
                break
            await asyncio.sleep(0.01)
        assert "updater.start_polling" in events, "run() never finished startup"

        # Trip the restart exactly like /restart does.
        transport.request_restart()

        # run() must RETURN (no hang) once the event trips.
        await asyncio.wait_for(run_task, timeout=5.0)

    asyncio.run(scenario())

    # Full clean lifecycle: started, then every teardown hook fired.
    assert events[:4] == ["initialize", "start", "updater.start_polling", "updater.stop"] or (
        events.index("start") < events.index("updater.stop")
    )
    for hook in ("updater.stop", "stop", "shutdown"):
        assert hook in events, f"teardown hook {hook!r} never ran — restart would hang/leak"
    assert transport._restart_requested is True
