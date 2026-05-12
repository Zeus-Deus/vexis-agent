"""Integration tests for ``/schedule`` slash command — Day 2.

Exercises ``TelegramTransport._on_schedule`` subcommand dispatch.
Uses the same fake PTB fixtures as ``test_goal_command.py`` so
patterns stay aligned across slash-command tests.

Coverage:

  * Auth gate — disallowed user gets dropped silently.
  * Disabled gate — replies with the disabled note.
  * No args → help text.
  * `list` / `status` — empty store reply + rendered rows.
  * `pause` / `resume` / `clear` — store mutations + error paths
    (unknown id, terminal status).
  * Create path — synthetic message lands in RunningTasks FIFO
    with ``origin="schedule_command"`` and the ``[user invoked
    /schedule]`` envelope.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.schedule_state import (
    ScheduleState,
    ScheduleStore,
    new_schedule_id,
)
from vexis_agent.tools.schedule_tool.parser import parse_schedule
from vexis_agent.transports.telegram import (
    TelegramTransport,
    _SCHEDULE_ACK,
    _SCHEDULE_CLEARED_TMPL,
    _SCHEDULE_DISABLED_NOTE,
    _SCHEDULE_HELP,
    _SCHEDULE_LIST_EMPTY,
    _SCHEDULE_NOT_FOUND_TMPL,
    _SCHEDULE_PAUSED_TMPL,
    _SCHEDULE_RESUMED_TMPL,
)


_USER = 99
_OTHER_USER = 88
_CHAT = 42


# ──────────────────────────────────────────────────────────────────
# Fakes (mirroring test_goal_command.py)
# ──────────────────────────────────────────────────────────────────


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(
        self, *, chat_id: int, text: str, parse_mode: Any = None, **_kw: Any
    ) -> None:
        self.sent.append((chat_id, text))

    async def send_chat_action(self, _chat_id: int, _action: Any) -> None:
        return None


class _FakeMessage:
    def __init__(self, text: str, chat_id: int, bot: _FakeBot) -> None:
        self.text = text
        self.chat_id = chat_id
        self._bot = bot
        self.replies: list[str] = []

    async def reply_text(self, text: str, **_kw: Any) -> None:
        self.replies.append(text)
        await self._bot.send_message(chat_id=self.chat_id, text=text)

    def get_bot(self) -> _FakeBot:
        return self._bot


class _FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _FakeUpdate:
    def __init__(self, message: _FakeMessage, user: _FakeUser) -> None:
        self.message = message
        self.effective_user = user


class _FakeCtx:
    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args or []


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> ScheduleStore:
    return ScheduleStore(tmp_path / "schedules.json")


@pytest.fixture
def schedules_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.schedules_enabled", lambda: True
    )


@pytest.fixture
def schedules_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.schedules_enabled", lambda: False
    )


@pytest.fixture
def transport(store: ScheduleStore) -> TelegramTransport:
    """Bare-bones transport with the minimum wiring /schedule needs.

    Mocks ``_dispatch_to_brain`` because the create-path now spawns
    that call via ``_spawn_background_dispatch`` — both the no-drain
    and drain-busy branches funnel through it. The mock lets us
    assert on the (bot, chat_id, user_id, synthetic, queue_origin)
    arguments without booting a real brain.
    """
    t = TelegramTransport.__new__(TelegramTransport)
    t._allowed_user_id = _USER  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    t._schedule_store = store  # type: ignore[attr-defined]
    # Track in-flight background-dispatch tasks (introduced when /goal
    # kickoff stopped blocking the PTB handler; /schedule now uses
    # the same path).
    t._background_dispatch_tasks = set()  # type: ignore[attr-defined]
    t._dispatch_to_brain = mock.AsyncMock()  # type: ignore[attr-defined]
    return t


def _update(text: str, user_id: int = _USER):
    bot = _FakeBot()
    msg = _FakeMessage(text=text, chat_id=_CHAT, bot=bot)
    upd = _FakeUpdate(msg, _FakeUser(user_id))
    return upd, bot, msg


def _ctx(*args: str) -> _FakeCtx:
    return _FakeCtx(list(args))


def _seed_schedule(
    store: ScheduleStore,
    *,
    id: str | None = None,
    status: str = "active",
    prompt: str = "test prompt",
) -> ScheduleState:
    parsed = parse_schedule("every 30m")
    state = ScheduleState(
        id=id or new_schedule_id(),
        chat_id=_CHAT,
        schedule=parsed,
        schedule_display=parsed["display"],
        prompt=prompt,
        status=status,
    )
    store.save(state)
    return state


# ──────────────────────────────────────────────────────────────────
# Auth + flag gates
# ──────────────────────────────────────────────────────────────────


def test_rejects_disallowed_user(transport, schedules_on):
    upd, _bot, msg = _update("/schedule", user_id=_OTHER_USER)
    asyncio.run(transport._on_schedule(upd, _ctx()))
    assert msg.replies == []  # silent reject


def test_disabled_replies_with_disabled_note(transport, schedules_off):
    upd, _bot, msg = _update("/schedule")
    asyncio.run(transport._on_schedule(upd, _ctx()))
    assert msg.replies == [_SCHEDULE_DISABLED_NOTE]


# ──────────────────────────────────────────────────────────────────
# Help (no args)
# ──────────────────────────────────────────────────────────────────


def test_no_args_shows_help(transport, schedules_on):
    upd, _bot, msg = _update("/schedule")
    asyncio.run(transport._on_schedule(upd, _ctx()))
    assert msg.replies == [_SCHEDULE_HELP]


# ──────────────────────────────────────────────────────────────────
# list / status
# ──────────────────────────────────────────────────────────────────


def test_list_empty(transport, schedules_on):
    upd, _bot, msg = _update("/schedule list")
    asyncio.run(transport._on_schedule(upd, _ctx("list")))
    assert msg.replies == [_SCHEDULE_LIST_EMPTY]


def test_list_renders_active_schedule(transport, schedules_on, store):
    state = _seed_schedule(store, id="abc123def456", prompt="standup brief")
    upd, _bot, msg = _update("/schedule list")
    asyncio.run(transport._on_schedule(upd, _ctx("list")))
    assert len(msg.replies) == 1
    reply = msg.replies[0]
    assert "abc123" in reply  # id prefix
    assert "every 30m" in reply  # schedule_display
    assert "standup brief" in reply  # prompt preview


def test_status_is_alias_for_list(transport, schedules_on, store):
    _seed_schedule(store)
    upd, _bot, msg = _update("/schedule status")
    asyncio.run(transport._on_schedule(upd, _ctx("status")))
    assert msg.replies[0].startswith("Schedules:")


# ──────────────────────────────────────────────────────────────────
# pause / resume / clear
# ──────────────────────────────────────────────────────────────────


def test_pause_flips_status(transport, schedules_on, store):
    state = _seed_schedule(store, id="aaaaaaaaaaaa")
    upd, _bot, msg = _update("/schedule pause aaa")
    asyncio.run(transport._on_schedule(upd, _ctx("pause", "aaa")))

    reloaded = store.load("aaaaaaaaaaaa")
    assert reloaded is not None
    assert reloaded.status == "paused"
    assert reloaded.next_fire_at is None
    assert msg.replies == [_SCHEDULE_PAUSED_TMPL.format(id="aaaaaa")]


def test_pause_unknown_id(transport, schedules_on, store):
    upd, _bot, msg = _update("/schedule pause unknown")
    asyncio.run(transport._on_schedule(upd, _ctx("pause", "unknown")))
    assert "No schedule matches" in msg.replies[0]


def test_pause_already_cleared(transport, schedules_on, store):
    state = _seed_schedule(store, id="bbbbbbbbbbbb")
    store.clear("bbbbbbbbbbbb")
    upd, _bot, msg = _update("/schedule pause bbb")
    asyncio.run(transport._on_schedule(upd, _ctx("pause", "bbb")))
    assert "already cleared" in msg.replies[0]


def test_resume_recomputes_next_fire(transport, schedules_on, store):
    state = _seed_schedule(store, id="cccccccccccc")
    # Pause first so resume has something to do.
    store.update_atomic(
        "cccccccccccc",
        lambda s: replace(s, status="paused", next_fire_at=None),
    )

    upd, _bot, msg = _update("/schedule resume ccc")
    asyncio.run(transport._on_schedule(upd, _ctx("resume", "ccc")))

    reloaded = store.load("cccccccccccc")
    assert reloaded is not None
    assert reloaded.status == "active"
    assert reloaded.next_fire_at is not None
    assert "Schedule cccccc resumed" in msg.replies[0]


def test_clear_marks_cleared_record_retained(transport, schedules_on, store):
    state = _seed_schedule(store, id="dddddddddddd")
    upd, _bot, msg = _update("/schedule clear ddd")
    asyncio.run(transport._on_schedule(upd, _ctx("clear", "ddd")))

    reloaded = store.load("dddddddddddd")
    assert reloaded is not None
    assert reloaded.status == "cleared"  # audit-retained
    assert msg.replies == [_SCHEDULE_CLEARED_TMPL.format(id="dddddd")]


def test_pause_no_args(transport, schedules_on):
    upd, _bot, msg = _update("/schedule pause")
    asyncio.run(transport._on_schedule(upd, _ctx("pause")))
    assert "Usage" in msg.replies[0]


# ──────────────────────────────────────────────────────────────────
# Create path — dispatches to brain via FIFO
# ──────────────────────────────────────────────────────────────────


def test_create_path_acks_then_dispatches_via_drain(
    transport, schedules_on
):
    """`/schedule remind me every 30m to stretch` →
    ack reply + synthetic message dispatched to brain via the
    background-dispatch spawn (so the PTB handler returns promptly).
    """
    upd, _bot, msg = _update("/schedule remind me every 30m to stretch")

    async def _run_and_drain() -> None:
        await transport._on_schedule(
            upd, _ctx("remind", "me", "every", "30m", "to", "stretch")
        )
        # The dispatch is now spawned as a background task; flush so
        # the mock observes the call before we assert on it.
        await transport.flush_background_dispatch()

    asyncio.run(_run_and_drain())

    # First reply is the ack.
    assert msg.replies[0] == _SCHEDULE_ACK

    # Dispatch was spawned and ran exactly once.
    assert transport._dispatch_to_brain.await_count == 1
    args = transport._dispatch_to_brain.await_args
    assert args is not None
    # Positional: (bot, chat_id, user_id, synthetic_text)
    bot_arg, chat_id, user_id, synthetic = args.args
    assert chat_id == _CHAT
    assert user_id == _USER
    assert synthetic.startswith("[user invoked /schedule]\n")
    assert "remind me every 30m to stretch" in synthetic
    # The kwarg is what lets the enqueue (drain-busy branch) tag the
    # message correctly.
    assert args.kwargs == {"queue_origin": "schedule_command"}


def test_create_path_enqueues_when_drain_active(
    transport, schedules_on
):
    """When drain is already active for the chat, the spawned dispatch
    still calls ``_dispatch_to_brain`` — but its internal claim() fails
    and it falls back to enqueue (with the ``queue_origin`` kwarg
    forwarded). Production keeps the same "Schedule queued behind
    the current turn" semantics.

    The mock for ``_dispatch_to_brain`` doesn't actually exercise the
    inner enqueue path; that's covered by the dedicated
    test_telegram_transport.py drain tests. Here we just pin that the
    handler hands off correctly with the right origin.
    """
    # Pre-claim the drain so claim() returns False.
    asyncio.run(transport._running_tasks.claim(_CHAT))

    upd, _bot, msg = _update("/schedule every weekday at 9am do standup")

    async def _run_and_drain() -> None:
        await transport._on_schedule(
            upd, _ctx("every", "weekday", "at", "9am", "do", "standup")
        )
        await transport.flush_background_dispatch()

    asyncio.run(_run_and_drain())

    # Ack happened.
    assert msg.replies[0] == _SCHEDULE_ACK
    # Dispatch was spawned (drain busy doesn't skip the spawn — the
    # spawn's internal enqueue handles that case).
    assert transport._dispatch_to_brain.await_count == 1
    args = transport._dispatch_to_brain.await_args
    assert args is not None
    bot_arg, chat_id, user_id, synthetic = args.args
    assert chat_id == _CHAT
    assert synthetic.startswith("[user invoked /schedule]\n")
    assert "every weekday at 9am do standup" in synthetic
    # The schedule_command origin must survive the round trip — it's
    # the tag that lets goal preemption skip schedule prompts.
    assert args.kwargs == {"queue_origin": "schedule_command"}


def test_schedule_kickoff_returns_before_brain_completes(
    transport, schedules_on
):
    """Regression pin for "/schedule blocks the chat".

    ``_on_schedule`` MUST return as soon as the ack is sent; the
    brain dispatch must run in the background. We make the mock slow
    and assert ``_on_schedule`` finishes in well under that delay.
    """
    delay_event = asyncio.Event()

    async def _slow_dispatch(*_args, **_kwargs):
        # Park here forever (until released by the test) so that any
        # accidental inline await on _dispatch_to_brain inside
        # _on_schedule would visibly hang.
        await delay_event.wait()

    transport._dispatch_to_brain = mock.AsyncMock(side_effect=_slow_dispatch)

    upd, _bot, _msg = _update("/schedule remind me to ship")

    async def _assert_non_blocking() -> None:
        # 1 second is generous — the handler should return in
        # microseconds. TimeoutError here means it's blocking.
        await asyncio.wait_for(
            transport._on_schedule(
                upd, _ctx("remind", "me", "to", "ship")
            ),
            timeout=1.0,
        )
        # The spawn must be in flight, not yet completed.
        assert transport._background_dispatch_tasks, (
            "_on_schedule returned but no background task was tracked — "
            "the dispatch was likely awaited inline"
        )
        # Release the mock and flush so the test exits cleanly.
        delay_event.set()
        await transport.flush_background_dispatch()

    asyncio.run(_assert_non_blocking())


def test_create_path_empty_after_subcommand_check(transport, schedules_on):
    """`/schedule garbageword` with garbageword not a subcommand →
    treated as create text. (Empty body would never reach this path
    because args is empty in that case.)
    """
    upd, _bot, msg = _update("/schedule nonsense")
    asyncio.run(transport._on_schedule(upd, _ctx("nonsense")))
    # Should ack + dispatch (it's treated as schedule text for the brain).
    assert msg.replies[0] == _SCHEDULE_ACK


# ──────────────────────────────────────────────────────────────────
# Store-missing safety
# ──────────────────────────────────────────────────────────────────


def test_no_store_wired_replies_disabled(schedules_on, monkeypatch):
    """If schedule_store wasn't wired into the transport, the handler
    falls back to the disabled note rather than crashing.
    """
    t = TelegramTransport.__new__(TelegramTransport)
    t._allowed_user_id = _USER  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    t._schedule_store = None  # type: ignore[attr-defined]

    upd, _bot, msg = _update("/schedule list")
    asyncio.run(t._on_schedule(upd, _ctx("list")))
    assert msg.replies == [_SCHEDULE_DISABLED_NOTE]
