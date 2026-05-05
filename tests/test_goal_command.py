"""Integration tests for /goal slash command in transports/telegram.py.

Exercises ``_on_goal``, ``_run_goal_hook`` (via the drain), and the
``_on_cancel`` auto-pause integration. Built around the same fake
PTB fixtures as ``tests/test_telegram_transport.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from core.goal_manager import GoalManager
from core.goal_state import GoalState, GoalStateStore
from core.running_tasks import QueuedMessage, RunningTasks
from transports.telegram import (
    TelegramTransport,
    _CANCEL_OK,
    _CANCEL_OK_GOAL_PAUSED_TMPL,
    _GOAL_CLEAR_REPLY,
    _GOAL_DISABLED_NOTE,
    _GOAL_KICKOFF_REPLY_TMPL,
    _GOAL_NO_ACTIVE,
    _GOAL_NO_GOAL_TO_PAUSE,
    _GOAL_REJECT_MIDRUN,
    _NOTHING_TO_CANCEL,
)


_USER = 99
_OTHER_USER = 88
_CHAT = 42
_SESSION = "test-session-abc"


# ──────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────


class _FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(
        self, *, chat_id: int, text: str, parse_mode: Any = None, **_kw: Any
    ) -> None:
        self.sent_messages.append((chat_id, text))

    async def send_chat_action(self, _chat_id: int, _action: Any) -> None:
        return None


class _FakeMessage:
    def __init__(
        self, text: str, chat_id: int, bot: _FakeBot
    ) -> None:
        self.text = text
        self.chat_id = chat_id
        self._bot = bot
        self.reply_log: list[str] = []

    async def reply_text(self, text: str, **_kw: Any) -> None:
        self.reply_log.append(text)
        # Mirror PTB's behavior: reply_text sends through the bot too,
        # so test code reading bot.sent_messages still sees the reply.
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


class _FakeHandler:
    """Minimal MessageHandler stub. Provides what /goal touches:
    ``current_session_uuid`` plus a ``_workspace`` attribute the
    transport reads via ``getattr`` when building a GoalManager."""

    def __init__(self, reply: str | None = "brain reply", workspace: Path | None = None) -> None:
        self.reply = reply
        self._workspace = workspace or Path("/tmp")
        self.calls: list[tuple[int, int, str]] = []

    async def handle(self, user_id: int, chat_id: int, text: str) -> str | None:
        self.calls.append((user_id, chat_id, text))
        return self.reply

    def current_session_uuid(self) -> str:
        return _SESSION

    def next_user_turn_index(self, _session_uuid: str) -> int:
        return 1

    async def claim_next_turn_index(self, _session_uuid: str) -> int | None:
        return 1


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def goals_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``core.paths.goals_path`` (which the transport's
    ``_build_goal_manager`` calls) to a tmp file so tests don't
    touch the user's real ~/.vexis/goals.json."""
    path = tmp_path / "goals.json"
    monkeypatch.setattr("core.paths.goals_path", lambda: path)
    return path


@pytest.fixture
def goals_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``goals_enabled()`` True regardless of the user's config."""
    monkeypatch.setattr("core.yaml_config.goals_enabled", lambda: True)


@pytest.fixture
def goals_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.yaml_config.goals_enabled", lambda: False)


@pytest.fixture
def transport(goals_file: Path) -> TelegramTransport:
    """A TelegramTransport with the minimum wiring /goal touches.
    Bypasses PTB Application like the other transport tests."""
    t = TelegramTransport.__new__(TelegramTransport)
    t._handler = _FakeHandler()  # type: ignore[attr-defined]
    t._allowed_user_id = _USER  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    t._learning_curator = None  # type: ignore[attr-defined]
    return t


def _update(text: str, user_id: int = _USER) -> tuple[_FakeUpdate, _FakeBot, _FakeMessage]:
    bot = _FakeBot()
    msg = _FakeMessage(text=text, chat_id=_CHAT, bot=bot)
    upd = _FakeUpdate(msg, _FakeUser(user_id))
    return upd, bot, msg


def _ctx(*args: str) -> _FakeCtx:
    return _FakeCtx(list(args))


# ──────────────────────────────────────────────────────────────────
# Auth gate
# ──────────────────────────────────────────────────────────────────


def test_on_goal_rejects_disallowed_user(
    transport: TelegramTransport, goals_on: None
) -> None:
    """Even with goals enabled, a non-allowed user gets nothing."""
    upd, bot, msg = _update("/goal status", user_id=_OTHER_USER)
    asyncio.run(transport._on_goal(upd, _ctx("status")))
    assert bot.sent_messages == []
    assert msg.reply_log == []


# ──────────────────────────────────────────────────────────────────
# Disabled flag short-circuit
# ──────────────────────────────────────────────────────────────────


def test_on_goal_disabled_replies_with_helpful_note(
    transport: TelegramTransport, goals_off: None
) -> None:
    """Goals disabled in config → single-line note pointing at the
    config flag. NOT a silent no-op (matches §6 Day 2 spec)."""
    upd, bot, msg = _update("/goal status")
    asyncio.run(transport._on_goal(upd, _ctx("status")))
    assert msg.reply_log == [_GOAL_DISABLED_NOTE]


# ──────────────────────────────────────────────────────────────────
# Status / pause / resume / clear control plane
# ──────────────────────────────────────────────────────────────────


def test_status_with_no_goal(transport: TelegramTransport, goals_on: None) -> None:
    upd, _bot, msg = _update("/goal")
    asyncio.run(transport._on_goal(upd, _ctx()))
    assert len(msg.reply_log) == 1
    assert "No active goal" in msg.reply_log[0]


def test_status_with_active_goal(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """Pre-seed an active goal in the store; /goal status renders the
    manager's status_line verbatim."""
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("port the goal command")

    upd, _bot, msg = _update("/goal status")
    asyncio.run(transport._on_goal(upd, _ctx("status")))
    assert len(msg.reply_log) == 1
    assert "active" in msg.reply_log[0].lower()
    assert "port the goal command" in msg.reply_log[0]


def test_pause_writes_state_and_drops_continuations(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """/goal pause flips status to paused AND drops any pending
    goal_continuation messages from the chat's queue. User messages
    queued behind continuations survive — verified by predicate."""
    # Seed a goal.
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("port goal")

    # Pre-load the chat's queue with: a goal continuation, a user
    # message, another goal continuation. We claim() first so enqueue
    # is allowed.
    async def seed() -> None:
        await transport._running_tasks.claim(_CHAT)
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "continuation 1", origin="goal_continuation"
        )
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "user msg", origin="user"
        )
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "continuation 2", origin="goal_continuation"
        )

    asyncio.run(seed())
    assert transport._running_tasks.queue_depth(_CHAT) == 3

    upd, _bot, msg = _update("/goal pause")
    asyncio.run(transport._on_goal(upd, _ctx("pause")))

    # State was paused on disk.
    state = store.load(_SESSION)
    assert state is not None
    assert state.status == "paused"
    assert state.paused_reason == "user-paused"
    # Reply mentions paused.
    assert any("paused" in r.lower() for r in msg.reply_log)
    # Two goal_continuation messages dropped; user msg survives.
    assert transport._running_tasks.queue_depth(_CHAT) == 1


def test_pause_with_no_goal_replies_no_goal(
    transport: TelegramTransport, goals_on: None
) -> None:
    upd, _bot, msg = _update("/goal pause")
    asyncio.run(transport._on_goal(upd, _ctx("pause")))
    assert msg.reply_log == [_GOAL_NO_GOAL_TO_PAUSE]


def test_resume_resets_turns_used(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    store = GoalStateStore(goals_file)
    mgr = GoalManager(session_uuid=_SESSION, workspace=Path("/tmp"), store=store)
    mgr.set("g")
    # Burn budget then pause.
    state = mgr.state
    assert state is not None
    state.turns_used = 7
    state.status = "paused"
    store.save(_SESSION, state)

    upd, _bot, msg = _update("/goal resume")
    asyncio.run(transport._on_goal(upd, _ctx("resume")))

    after = store.load(_SESSION)
    assert after is not None
    assert after.status == "active"
    assert after.turns_used == 0
    assert any("resumed" in r.lower() for r in msg.reply_log)


def test_clear_marks_status_and_drops_continuations(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("g")

    async def seed() -> None:
        await transport._running_tasks.claim(_CHAT)
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "cont", origin="goal_continuation"
        )
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "user", origin="user"
        )

    asyncio.run(seed())

    upd, _bot, msg = _update("/goal clear")
    asyncio.run(transport._on_goal(upd, _ctx("clear")))

    after = store.load(_SESSION)
    assert after is not None
    assert after.status == "cleared"
    assert msg.reply_log == [_GOAL_CLEAR_REPLY]
    # Goal continuation dropped, user message survives.
    assert transport._running_tasks.queue_depth(_CHAT) == 1


# ──────────────────────────────────────────────────────────────────
# /goal <text> — set + kickoff
# ──────────────────────────────────────────────────────────────────


def test_goal_text_midrun_rejected(
    transport: TelegramTransport, goals_on: None
) -> None:
    """Setting a new goal while a drain is in flight returns the §4
    reject string — /cancel first."""
    async def seed() -> None:
        # Mark the chat as in-flight by taking the drain claim.
        await transport._running_tasks.claim(_CHAT)

    asyncio.run(seed())

    upd, _bot, msg = _update("/goal port goal command")
    asyncio.run(transport._on_goal(upd, _ctx("port", "goal", "command")))

    assert msg.reply_log == [_GOAL_REJECT_MIDRUN]


def test_goal_text_kicks_off_first_turn(
    transport: TelegramTransport,
    goals_on: None,
    goals_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/goal <text> with no drain active: writes state, sends the
    kickoff confirmation, then dispatches the goal text to the brain
    via the same path a user message would take."""
    # Stub the goal hook so the brain reply doesn't trigger a real
    # judge call. Without this the kickoff turn would call
    # judge_goal which would try to spawn ``claude -p``.
    async def _no_hook(*args, **kwargs):
        return None

    monkeypatch.setattr(transport, "_run_goal_hook", _no_hook)

    upd, _bot, msg = _update("/goal port the goal command to vexis")
    asyncio.run(transport._on_goal(upd, _ctx("port", "the", "goal", "command", "to", "vexis")))

    # State persisted.
    store = GoalStateStore(goals_file)
    state = store.load(_SESSION)
    assert state is not None
    assert state.goal == "port the goal command to vexis"
    assert state.status == "active"

    # Kickoff confirmation sent.
    assert any("Goal set" in r for r in msg.reply_log)
    # Brain saw the goal text as the first turn.
    handler: _FakeHandler = transport._handler  # type: ignore[assignment]
    assert handler.calls
    assert handler.calls[0][2] == "port the goal command to vexis"


# ──────────────────────────────────────────────────────────────────
# /cancel auto-pause integration
# ──────────────────────────────────────────────────────────────────


def test_cancel_with_active_goal_auto_pauses(
    transport: TelegramTransport,
    goals_on: None,
    goals_file: Path,
) -> None:
    """The §4 decision: /cancel auto-pauses the goal so a casual
    follow-up message hours later doesn't kick off a continuation
    the user thought they cancelled. Reply mentions the paused
    state with N/budget — implementation reads the state AFTER
    pause, so turns_used is whatever the goal had at cancel time."""
    store = GoalStateStore(goals_file)
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("port goal")
    # Burn 3 turns so the reply has interesting numbers.
    state = mgr.state
    assert state is not None
    state.turns_used = 3
    store.save(_SESSION, state)

    # Set up some queued state for /cancel to clear.
    async def seed() -> None:
        await transport._running_tasks.claim(_CHAT)
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "cont", origin="goal_continuation"
        )

    asyncio.run(seed())

    upd, _bot, msg = _update("/cancel")
    asyncio.run(transport._on_cancel(upd, _ctx()))

    # Goal flipped to paused with the user-cancelled reason.
    after = store.load(_SESSION)
    assert after is not None
    assert after.status == "paused"
    assert after.paused_reason == "user-cancelled"
    # Reply uses the paused-state suffix template.
    assert msg.reply_log
    final_reply = msg.reply_log[-1]
    assert "Goal paused at 3/" in final_reply
    assert "/goal resume" in final_reply
    # Continuation dropped from queue.
    assert transport._running_tasks.queue_depth(_CHAT) == 0


def test_cancel_without_active_goal_uses_existing_path(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """No active goal → standard _CANCEL_OK / _NOTHING_TO_CANCEL
    paths preserved. The auto-pause block is skipped silently."""
    # Set up a running drain so cancel returns True.
    async def seed() -> None:
        await transport._running_tasks.claim(_CHAT)

    asyncio.run(seed())

    upd, _bot, msg = _update("/cancel")
    asyncio.run(transport._on_cancel(upd, _ctx()))

    # Standard cancel reply, not the goal-paused variant.
    assert msg.reply_log == [_CANCEL_OK]


def test_cancel_with_goals_disabled_skips_auto_pause(
    transport: TelegramTransport, goals_off: None, goals_file: Path
) -> None:
    """Even with a goal record on disk, when goals are globally
    disabled /cancel does not consult the manager — keeps cancel
    cheap and avoids surprising the user with a status they can't
    control via /goal."""
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("g")

    async def seed() -> None:
        await transport._running_tasks.claim(_CHAT)

    asyncio.run(seed())

    upd, _bot, msg = _update("/cancel")
    asyncio.run(transport._on_cancel(upd, _ctx()))

    # Standard cancel reply.
    assert msg.reply_log == [_CANCEL_OK]
    # Goal still active on disk — cancel didn't touch it.
    after = store.load(_SESSION)
    assert after is not None
    assert after.status == "active"
