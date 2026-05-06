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
    _GOAL_BAREWORD_HINT,
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


# ──────────────────────────────────────────────────────────────────
# Race guards on _run_goal_hook (Day 2 bugfix)
# ──────────────────────────────────────────────────────────────────


def test_cancel_mid_kickoff_does_not_run_goal_hook(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """**The bug**: /cancel during a brain turn used to let
    ``_run_goal_hook`` fire after the cancelled brain returned None,
    judging the empty reply as "continue" per the §3 fold rule, and
    enqueueing a surprise continuation that ``take_over_if_pending``
    would then run after the post-cancel drain release.

    Fix: ``_run_goal_hook`` checks ``running_tasks.is_drain_cancelled``
    at top and bails — no judge call, no enqueue, no status message.
    The ``/cancel`` reply is still produced by ``_on_cancel`` and the
    auto-pause integration writes ``status=paused``.
    """
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("g")

    bot = _FakeBot()

    async def scenario() -> None:
        # Mimic a drain in flight (claim taken, then /cancel hits).
        await transport._running_tasks.claim(_CHAT)
        # Issue /cancel — runs the auto-pause path.
        upd, _b, _msg = _update("/cancel")
        # /cancel uses its own bot via msg.reply_text; we capture
        # those messages on the fake message's reply_log already.
        await transport._on_cancel(upd, _ctx())
        # Drain now reaches the goal hook with reply="" because the
        # brain raised BrainCancelled and handler returned None.
        with mock.patch("core.goal_manager.judge_goal") as fake_judge:
            await transport._run_goal_hook(bot, _CHAT, "")
            # Judge MUST NOT be called — bail before that.
            fake_judge.assert_not_called()

        # No continuation enqueued, no "Continuing" status message.
        assert transport._running_tasks.queue_depth(_CHAT) == 0
        assert not any("Continuing" in t for _cid, t in bot.sent_messages)
        # Goal state: paused with the user-cancelled reason.
        state = store.load(_SESSION)
        assert state is not None
        assert state.status == "paused"
        assert state.paused_reason == "user-cancelled"

    asyncio.run(scenario())


def test_pause_during_judge_call_drops_continuation(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """Inverse race: ``/goal pause`` lands AFTER
    ``evaluate_after_turn`` returned but BEFORE the hook enqueues
    the continuation. The reload guard at the enqueue site catches
    it — no continuation lands, no "↻ Continuing" status message
    sent (state changed under us, the user already saw their pause
    reply).

    Simulated by patching ``GoalManager.evaluate_after_turn`` to
    side-effect a paused-status write into the store before
    returning a continue decision. Equivalent to a real concurrent
    pause that landed during the judge's await window.
    """
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("g")

    from core.goal_manager import CONTINUATION_PROMPT_TEMPLATE, GoalManager as _GM

    def evaluate_with_concurrent_pause(self, last_response: str) -> dict:
        # Stand in for evaluate_after_turn: do the manager's work
        # (turns_used++, save), then perform a paused-status write
        # via a separate manager instance to mimic /goal pause
        # arriving from another handler.
        state = self._state
        assert state is not None
        state.turns_used += 1
        state.last_verdict = "continue"
        state.last_reason = "more work"
        self._store.save(self._session_uuid, state)
        # Concurrent pause writer — second manager, same store.
        external = _GM(
            session_uuid=self._session_uuid,
            workspace=self._workspace,
            store=self._store,
        )
        external.pause(reason="user-paused")
        return {
            "status": "active",
            "should_continue": True,
            "continuation_prompt": CONTINUATION_PROMPT_TEMPLATE.format(
                goal=state.goal
            ),
            "verdict": "continue",
            "reason": "more work",
            "message": "↻ Continuing toward goal (1/20): more work",
        }

    bot = _FakeBot()

    async def scenario() -> None:
        with mock.patch.object(
            _GM, "evaluate_after_turn", evaluate_with_concurrent_pause
        ):
            await transport._run_goal_hook(bot, _CHAT, "brain reply")

    asyncio.run(scenario())

    # No continuation enqueued.
    assert transport._running_tasks.queue_depth(_CHAT) == 0
    # No status messages sent (state flipped under us).
    assert not any("Continuing" in t for _cid, t in bot.sent_messages)
    # Final disk state: paused (the concurrent writer's write).
    state = store.load(_SESSION)
    assert state is not None
    assert state.status == "paused"
    assert state.paused_reason == "user-paused"


# ──────────────────────────────────────────────────────────────────
# Bareword-typo guard
# ──────────────────────────────────────────────────────────────────


def test_goal_bareword_redirects_to_hint(
    transport: TelegramTransport,
    goals_on: None,
    goals_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/goal cancel / /goal stop / /goal abort / /goal kill / /goal halt
    are almost certainly typos for /cancel. The handler returns the
    hint instead of either treating the bareword as goal text or
    letting the mid-run reject hide the intent.

    Asserted three ways:
      1. /goal cancel mid-drain → hint (NOT "Brain is busy").
      2. /goal stop with no drain → hint (NOT goal-set with text "stop").
      3. /goal foobar → proceeds normally to set the goal.
    """
    store = GoalStateStore(goals_file)

    # Case 1: /goal cancel mid-drain → hint, regardless of busy state.
    async def seed_drain() -> None:
        await transport._running_tasks.claim(_CHAT)

    asyncio.run(seed_drain())
    upd, _bot, msg = _update("/goal cancel")
    asyncio.run(transport._on_goal(upd, _ctx("cancel")))
    assert msg.reply_log == [_GOAL_BAREWORD_HINT]
    assert store.load(_SESSION) is None  # NOT set as a goal

    # Reset the running_tasks so case 2 isn't mid-drain.
    transport._running_tasks = type(transport._running_tasks)()  # type: ignore[assignment]

    # Case 2: /goal stop outside a drain → still the hint, not goal-set.
    upd, _bot, msg = _update("/goal stop")
    asyncio.run(transport._on_goal(upd, _ctx("stop")))
    assert msg.reply_log == [_GOAL_BAREWORD_HINT]
    assert store.load(_SESSION) is None  # NOT set as a goal

    # Case 3: a real goal text proceeds normally. Stub the kickoff
    # dispatch path so we don't go all the way through the brain.
    async def _no_dispatch(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(transport, "_dispatch_to_brain", _no_dispatch)
    upd, _bot, msg = _update("/goal foobar")
    asyncio.run(transport._on_goal(upd, _ctx("foobar")))
    state = store.load(_SESSION)
    assert state is not None
    assert state.goal == "foobar"
    assert state.status == "active"


def test_goal_uppercase_bareword_hits_hint(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """Bareword check is case-insensitive — /goal STOP / /goal Cancel
    both hit the hint. Multi-word phrases starting with the same
    word (e.g. /goal stop the timer) are real goal text and bypass
    the check."""
    store = GoalStateStore(goals_file)

    upd, _bot, msg = _update("/goal STOP")
    asyncio.run(transport._on_goal(upd, _ctx("STOP")))
    assert msg.reply_log == [_GOAL_BAREWORD_HINT]
    assert store.load(_SESSION) is None


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
