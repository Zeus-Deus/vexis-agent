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

from vexis_agent.core.goal_manager import GoalManager
from vexis_agent.core.goal_state import GoalState, GoalStateStore
from vexis_agent.core.running_tasks import QueuedMessage, RunningTasks
from vexis_agent.transports.telegram import (
    TelegramTransport,
    _CANCEL_OK,
    _CANCEL_OK_GOAL_PAUSED_TMPL,
    _GOAL_ALREADY_TERMINAL_TMPL,
    _GOAL_BAREWORD_HINT,
    _GOAL_CLEAR_REPLY,
    _GOAL_DISABLED_NOTE,
    _GOAL_KICKOFF_REPLY_TMPL,
    _GOAL_NO_ACTIVE,
    _GOAL_NO_GOAL_TO_PAUSE,
    _GOAL_REJECT_MIDRUN,
    _NOTHING_TO_CANCEL,
    _PICKING_UP_PREFIX as _GOAL_PICKING_UP_PREFIX,
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
    ``current_session_uuid``, a ``_workspace`` attribute the
    transport reads via ``getattr`` when building a GoalManager,
    and a ``_brain`` attribute the goal hook threads into
    ``GoalManager.evaluate_after_turn`` (Phase B of the brain
    abstraction — the post-turn hook spawns the goal judge via
    ``brain.spawn_aux``, so tests need a brain reference). The
    default ``_brain`` is a ``BrainNull`` configured to return
    ``"continue"`` for every judge call so existing tests that
    don't care about judge behaviour see the default
    keep-going outcome."""

    def __init__(
        self,
        reply: str | None = "brain reply",
        workspace: Path | None = None,
        brain=None,
    ) -> None:
        from vexis_agent.core.brain.base import AuxResult
        from vexis_agent.core.brain.null import BrainNull

        self.reply = reply
        self._workspace = workspace or Path("/tmp")
        self.calls: list[tuple[int, int, str]] = []
        # Default: every judge call returns "continue" (done=false).
        # Tests that need different verdicts pass an explicit brain.
        self._brain = brain or BrainNull(
            aux_results=[
                AuxResult(
                    stdout='{"done": false, "reason": "test default"}',
                    stderr="",
                    returncode=0,
                )
            ]
            * 50  # plenty for any test's judge-call volume
        )

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
    monkeypatch.setattr("vexis_agent.core.paths.goals_path", lambda: path)
    return path


@pytest.fixture
def goals_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``goals_enabled()`` True regardless of the user's config."""
    monkeypatch.setattr("vexis_agent.core.yaml_config.goals_enabled", lambda: True)


@pytest.fixture
def goals_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vexis_agent.core.yaml_config.goals_enabled", lambda: False)


@pytest.fixture
def transport(goals_file: Path) -> TelegramTransport:
    """A TelegramTransport with the minimum wiring /goal touches.
    Bypasses PTB Application like the other transport tests."""
    t = TelegramTransport.__new__(TelegramTransport)
    t._handler = _FakeHandler()  # type: ignore[attr-defined]
    t._allowed_user_id = _USER  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    t._learning_curator = None  # type: ignore[attr-defined]
    # /goal kickoff is now spawned as a background task; the
    # transport tracks the handle so asyncio doesn't GC it
    # mid-flight. ``__new__`` skips ``__init__`` so we mirror the
    # one field the kickoff path reads.
    t._background_dispatch_tasks = set()  # type: ignore[attr-defined]
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
    via the same path a user message would take.

    The dispatch now runs in a tracked background task — the test
    flushes ``_background_dispatch_tasks`` after ``_on_goal`` returns
    so the brain-call assertion can observe the spawned turn.
    """
    # Stub the goal hook so the brain reply doesn't trigger a real
    # judge call. Without this the kickoff turn would call
    # judge_goal which would try to spawn ``claude -p``.
    async def _no_hook(*args, **kwargs):
        return None

    monkeypatch.setattr(transport, "_run_goal_hook", _no_hook)

    upd, _bot, msg = _update("/goal port the goal command to vexis")

    async def _run_and_drain() -> None:
        await transport._on_goal(
            upd, _ctx("port", "the", "goal", "command", "to", "vexis")
        )
        # The kickoff dispatch is now a background task; flush it
        # before asserting on brain calls.
        await transport.flush_background_dispatch()

    asyncio.run(_run_and_drain())

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


def test_goal_kickoff_returns_before_brain_completes(
    transport: TelegramTransport,
    goals_on: None,
    goals_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression pin for the "/goal blocks the chat" bug.

    `_on_goal` MUST return as soon as the kickoff ack is sent; it
    must NOT await the kickoff dispatch. We test that by giving the
    fake handler a slow-running `handle()` and asserting the
    `_on_goal` coroutine completes while the dispatch is still
    in-flight — measured by:

      1. ``_background_dispatch_tasks`` contains the spawned task,
      2. the task has not yet finished,
      3. the brain handler has not yet been called.

    Without the background-task spawn the await would block here
    for the full handle delay, making all three checks fail.
    """
    started_event = asyncio.Event()
    release_event = asyncio.Event()

    class _BlockingHandler(_FakeHandler):
        async def handle(self, user_id: int, chat_id: int, text: str) -> str | None:
            started_event.set()
            await release_event.wait()
            self.calls.append((user_id, chat_id, text))
            return self.reply

    blocking_handler = _BlockingHandler()
    transport._handler = blocking_handler  # type: ignore[attr-defined]

    # Stub the goal hook — we only care about the kickoff path here.
    async def _no_hook(*args, **kwargs):
        return None

    monkeypatch.setattr(transport, "_run_goal_hook", _no_hook)

    upd, _bot, _msg = _update("/goal ship the rocket")

    async def _assert_non_blocking() -> None:
        # _on_goal must return promptly even though the brain
        # handle() is blocked. asyncio.wait_for raises TimeoutError
        # on regression — that is the failure signal we want.
        await asyncio.wait_for(
            transport._on_goal(upd, _ctx("ship", "the", "rocket")),
            timeout=1.0,
        )
        # The spawned dispatch should be in the tracking set and
        # in-flight (or at least past claim, awaiting handle).
        assert transport._background_dispatch_tasks, (
            "kickoff did not spawn a background task — the dispatch "
            "was likely awaited inline and the chat is now blocked"
        )
        # Wait for the brain handle to actually start (so we know
        # the dispatch reached the brain-call site and is parked on
        # release_event), then verify no calls landed yet.
        await asyncio.wait_for(started_event.wait(), timeout=1.0)
        assert blocking_handler.calls == [], (
            "brain handle returned before release_event — "
            "test fake is wrong, not the production code"
        )
        # Release the brain and let the background task complete so
        # the test exits cleanly. Failure to flush would leave a
        # pending task at asyncio.run shutdown and emit a noisy
        # warning that masks the real assertion failure (if any).
        release_event.set()
        await transport.flush_background_dispatch()
        assert blocking_handler.calls, (
            "kickoff task ran but never reached brain.handle — "
            "the background task crashed before dispatching"
        )

    asyncio.run(_assert_non_blocking())


def test_user_message_preempts_pending_goal_continuation(
    transport: TelegramTransport,
    goals_on: None,
    goals_file: Path,
) -> None:
    """When a goal continuation is already sitting in the queue and a
    real user message lands, the continuation must be dropped so the
    user's message runs next instead of behind the continuation.

    The user's message still has to wait for the in-flight brain turn
    to complete (we can't preempt mid-turn without ``/cancel``), but
    capping the wait at "current turn only" — instead of "current
    turn + N queued continuations" — is the latency improvement
    that makes the chat feel responsive during a goal loop.

    The post-turn hook re-enqueues a continuation after the user's
    turn finishes (tested separately in
    ``test_hook_dedupes_existing_continuation_on_enqueue``).
    """
    # Seed: drain claimed by a notional in-flight turn, one
    # continuation queued ahead.
    async def seed() -> None:
        await transport._running_tasks.claim(_CHAT)
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "old continuation prompt",
            origin="goal_continuation",
        )
        # Also enqueue an unrelated user message that must survive
        # the preemption (only goal_continuation items get dropped).
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "earlier user message", origin="user",
        )

    asyncio.run(seed())

    # User types a new message — _on_text fires preemption.
    upd, _bot, _msg = _update("urgent: what time is it")

    async def _exercise() -> None:
        await transport._on_text(upd, _FakeCtx())

    asyncio.run(_exercise())

    # The continuation is gone; the earlier user message and the new
    # one survive in arrival order.
    state = transport._running_tasks._chats[_CHAT]
    texts = [m.text for m in state.queue]
    origins = [m.origin for m in state.queue]
    assert "old continuation prompt" not in texts
    assert texts == ["earlier user message", "urgent: what time is it"]
    assert origins == ["user", "user"]


def test_hook_dedupes_existing_continuation_on_enqueue(
    transport: TelegramTransport,
    goals_on: None,
    goals_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_goal_hook`` must drop any pre-existing goal_continuation
    before enqueuing a new one. Without this, a stretch of user turns
    (each fires the post-turn hook → each appends a continuation)
    leaves a backlog of identical continuation prompts that the drain
    burns through once the user stops typing.

    Hermes' invariant: at most ONE continuation queued at a time.
    """
    # Seed an active goal and an existing continuation in the queue.
    store = GoalStateStore(goals_file)
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("complete the rocket")

    async def seed() -> None:
        await transport._running_tasks.claim(_CHAT)
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "stale continuation A",
            origin="goal_continuation",
        )
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "stale continuation B",
            origin="goal_continuation",
        )

    asyncio.run(seed())

    # Stub evaluate_after_turn so the hook reaches the enqueue branch
    # without spawning a real judge.
    async def _fake_evaluate(self, last_response, brain):  # noqa: ARG001
        return {
            "status": "active",
            "should_continue": True,
            "continuation_prompt": "fresh continuation",
            "verdict": "continue",
            "reason": "judge says keep going",
            "message": "",  # suppress chat status line
        }

    monkeypatch.setattr(GoalManager, "evaluate_after_turn", _fake_evaluate)

    class _FakeBot:
        async def send_message(self, **_kw):
            return None

    # Run the hook directly.
    asyncio.run(transport._run_goal_hook(_FakeBot(), _CHAT, "reply text"))

    state = transport._running_tasks._chats[_CHAT]
    texts = [m.text for m in state.queue]
    origins = [m.origin for m in state.queue]
    # Both stale continuations dropped; exactly one fresh one queued.
    assert texts == ["fresh continuation"]
    assert origins == ["goal_continuation"]


def test_goal_kickoff_drains_subsequent_user_messages(
    transport: TelegramTransport,
    goals_on: None,
    goals_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """While the kickoff dispatch runs, a regular `_on_text` message
    must FIFO-enqueue (not block) and run after the kickoff turn.

    This is the FIFO interleaving guarantee that makes "chat is
    responsive during a goal" actually true: real user messages
    sit behind the goal in the same per-chat deque, exactly like
    goal continuations do, and the drain picks them up in arrival
    order. Mirrors Hermes' single-FIFO behaviour described in
    `.plans/goal-command-research.md`.
    """
    started_event = asyncio.Event()
    release_event = asyncio.Event()

    class _BlockingHandler(_FakeHandler):
        async def handle(self, user_id: int, chat_id: int, text: str) -> str | None:
            # The first call (kickoff) blocks. Subsequent calls
            # (the queued user message) run as soon as the drain
            # reaches them — they do NOT re-block.
            if not self.calls and not started_event.is_set():
                started_event.set()
                await release_event.wait()
            self.calls.append((user_id, chat_id, text))
            return self.reply

    blocking_handler = _BlockingHandler()
    transport._handler = blocking_handler  # type: ignore[attr-defined]

    async def _no_hook(*args, **kwargs):
        return None

    monkeypatch.setattr(transport, "_run_goal_hook", _no_hook)

    goal_upd, _gbot, _gmsg = _update("/goal ship rockets")
    text_upd, _tbot, _tmsg = _update("hey can you also water plants")

    async def _exercise() -> None:
        # Kick off the goal — returns immediately, dispatch is
        # parked inside blocking_handler.handle waiting on
        # release_event.
        await transport._on_goal(goal_upd, _ctx("ship", "rockets"))
        await asyncio.wait_for(started_event.wait(), timeout=1.0)

        # User sends a regular message while the goal is "running".
        # _on_text → _dispatch_to_brain → claim() fails (the
        # kickoff task owns the drain) → enqueue. Returns quickly.
        await asyncio.wait_for(
            transport._on_text(text_upd, _FakeCtx()),
            timeout=1.0,
        )
        assert transport._running_tasks.queue_depth(_CHAT) == 1, (
            "user message during goal kickoff should sit in the "
            "per-chat FIFO, not block the handler"
        )

        # Release the kickoff turn. The drain pops the queued user
        # message and runs it as a second turn.
        release_event.set()
        await transport.flush_background_dispatch()

    asyncio.run(_exercise())

    # Both turns ran, in arrival order.
    assert len(blocking_handler.calls) == 2
    assert blocking_handler.calls[0][2] == "ship rockets"
    assert blocking_handler.calls[1][2] == "hey can you also water plants"


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
        with mock.patch("vexis_agent.core.goal_manager.judge_goal") as fake_judge:
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

    from vexis_agent.core.goal_manager import CONTINUATION_PROMPT_TEMPLATE, GoalManager as _GM

    async def evaluate_with_concurrent_pause(self, last_response: str, brain) -> dict:
        # Stand in for evaluate_after_turn: do the manager's work
        # (turns_used++, save), then perform a paused-status write
        # via a separate manager instance to mimic /goal pause
        # arriving from another handler.
        #
        # ``brain`` parameter ignored — the mock pre-decides "continue"
        # without spawning an aux call. Phase B widened
        # evaluate_after_turn's signature to accept the brain; the
        # mock matches that signature so the transport's call site
        # binds correctly.
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


# ──────────────────────────────────────────────────────────────────
# Day 3 — preemption / restart / lifecycle edge cases
# ──────────────────────────────────────────────────────────────────


def test_user_message_arrives_first_during_goal_hook(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """§3 line 332 — user-arrives-first race.

    The user types a message during a brain turn. By the time the
    goal hook fires post-turn, the user message is already enqueued
    (origin=user). Hook judges, gets continue, enqueues continuation
    (origin=goal_continuation). Resulting deque: [user, continuation].
    pop_or_release returns the user message first, then the
    continuation — preserving the user-wins-on-arrival-order
    invariant. Both flow through the drain in order.
    """
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("port the goal command")
    bot = _FakeBot()

    async def scenario() -> None:
        # Drain claimed (mimics current brain turn in flight).
        await transport._running_tasks.claim(_CHAT)
        # Real user message arrived while brain was busy → enqueued.
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "user follow-up", origin="user"
        )
        # Brain returns reply; hook fires with judge=continue.
        # Phase B: judge_goal is async — use AsyncMock so the awaited
        # call returns the (verdict, reason) tuple.
        with mock.patch(
            "vexis_agent.core.goal_manager.judge_goal",
            new=mock.AsyncMock(return_value=("continue", "more", False)),
        ):
            await transport._run_goal_hook(bot, _CHAT, "brain reply")

        # Queue now holds [user, continuation] in arrival order.
        assert transport._running_tasks.queue_depth(_CHAT) == 2
        first = await transport._running_tasks.pop_or_release(_CHAT)
        second = await transport._running_tasks.pop_or_release(_CHAT)

        assert first is not None and first.origin == "user"
        assert first.text == "user follow-up"
        assert second is not None and second.origin == "goal_continuation"
        assert "Continuing toward your standing goal" in second.text

    asyncio.run(scenario())


def test_continuation_arrives_first_then_user_message(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """§3 line 334 — the race we explicitly accepted.

    Hook finishes judge before the user's message lands. Queue
    becomes [continuation, user]; the continuation runs first,
    the user message after. We document this as accepted behaviour
    rather than fighting it — peek-then-enqueue would introduce a
    TOCTOU window of its own (`.plans/goal-command-research.md`
    §5 "Race: continuation enqueue vs. real user message").

    Test verifies the deque order, the continuation's prompt shape,
    and that a subsequent judge call would re-evaluate the goal
    after the user's turn (judge fires again on the user-turn reply).
    """
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("ship feature X")
    bot = _FakeBot()
    judge_calls: list[str] = []

    async def judge_capture(workspace, goal, last_response, brain):
        # Phase B: judge_goal is async and accepts ``brain`` as the
        # 4th positional. The captured ``brain`` is a ``BrainNull``
        # from the FakeHandler; this mock ignores it and returns a
        # pre-decided verdict so the test stays deterministic.
        judge_calls.append(last_response)
        # First call: continue. Second (after user turn): done.
        # Day 5: judge_goal returns ``(verdict, reason, parse_failed)``;
        # parse_failed=False here because both replies are well-formed.
        if len(judge_calls) == 1:
            return ("continue", "needs more work", False)
        return ("done", "user closed it out", False)

    async def scenario() -> None:
        # Drain claimed; queue empty.
        await transport._running_tasks.claim(_CHAT)
        with mock.patch(
            "vexis_agent.core.goal_manager.judge_goal", side_effect=judge_capture
        ):
            # First hook call → enqueues continuation.
            await transport._run_goal_hook(bot, _CHAT, "first reply")
            # User message lands AFTER the continuation.
            await transport._running_tasks.enqueue(
                _CHAT, _USER, "user follow-up", origin="user"
            )
            assert transport._running_tasks.queue_depth(_CHAT) == 2

            # Pop in order — continuation first, user second.
            first = await transport._running_tasks.pop_or_release(_CHAT)
            second = await transport._running_tasks.pop_or_release(_CHAT)
            assert first is not None and first.origin == "goal_continuation"
            assert second is not None and second.origin == "user"
            assert second.text == "user follow-up"

            # Simulate the drain processing the user turn and the
            # hook firing again on its reply — judge should fire a
            # SECOND time, this time mapping the user's response to
            # done.
            await transport._run_goal_hook(bot, _CHAT, "user-driven final reply")

        # Two judge calls: first on the brain's first reply (continue),
        # second on the user-driven turn's reply (done).
        assert len(judge_calls) == 2
        assert judge_calls[0] == "first reply"
        assert judge_calls[1] == "user-driven final reply"
        # Goal ends in done state — user's turn closed the goal.
        final = store.load(_SESSION)
        assert final is not None
        assert final.status == "done"

    asyncio.run(scenario())


def test_post_cancel_resume_kicks_loop_again(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """After /cancel auto-pauses, normal user messages run as plain
    turns — no continuation enqueues because status=paused. Then
    /goal resume zeros turns_used and the next post-turn hook
    enqueues a continuation again.
    """
    store = GoalStateStore(goals_file)
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("port the goal command")
    # Burn 3 turns to make the post-resume reset visible.
    state = mgr.state
    assert state is not None
    state.turns_used = 3
    store.save(_SESSION, state)
    # Simulate /cancel auto-pause having already happened.
    state.status = "paused"
    state.paused_reason = "user-cancelled"
    store.save(_SESSION, state)

    bot = _FakeBot()

    async def scenario() -> None:
        # 1) Plain user message after the paused state. Hook should
        #    NOT fire (state is paused → is_active False at top).
        with mock.patch("vexis_agent.core.goal_manager.judge_goal") as fake_judge:
            await transport._run_goal_hook(bot, _CHAT, "casual reply")
            fake_judge.assert_not_called()
        assert transport._running_tasks.queue_depth(_CHAT) == 0

        # 2) /goal resume — flips active, resets turns_used to 0.
        upd, _b, _m = _update("/goal resume")
        await transport._on_goal(upd, _ctx("resume"))
        resumed = store.load(_SESSION)
        assert resumed is not None
        assert resumed.status == "active"
        assert resumed.turns_used == 0

        # 3) Next post-turn hook now DOES enqueue a continuation.
        await transport._running_tasks.claim(_CHAT)
        with mock.patch(
            "vexis_agent.core.goal_manager.judge_goal",
            new=mock.AsyncMock(return_value=("continue", "more", False)),
        ):
            await transport._run_goal_hook(bot, _CHAT, "brain reply after resume")
        # Continuation is in the queue.
        assert transport._running_tasks.queue_depth(_CHAT) == 1
        msg = await transport._running_tasks.pop_or_release(_CHAT)
        assert msg is not None
        assert msg.origin == "goal_continuation"
        # turns_used is now 1 (the brain turn after resume burned one).
        after = store.load(_SESSION)
        assert after is not None
        assert after.turns_used == 1

    asyncio.run(scenario())


def test_session_clear_orphans_old_goal(
    transport: TelegramTransport,
    goals_on: None,
    goals_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§4 — /clear (the session-clear command) rotates the active
    session UUID via core/sessions.py:172-177. The old session's
    goal record stays on disk keyed by the OLD UUID. The new session
    has no goal until the user types /goal <text> again.

    Test simulates the rotation by changing what
    ``handler.current_session_uuid()`` returns. Asserts:
      - /goal status on the new UUID returns "no active goal"
      - ``store.load(old_uuid)`` still returns the orphaned record
      - ``store.list_active()`` returns the orphan only if its
        status is still active (it is — /clear doesn't touch goals).
    """
    old_uuid = "old-session-uuid"
    new_uuid = "new-session-uuid"

    # Seed a goal under the OLD UUID.
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=old_uuid, workspace=Path("/tmp"), store=store
    ).set("orphan-able goal")

    # Rotate the handler's reported session UUID — same effect as
    # SessionStore.rotate() on a real /clear.
    transport._handler.current_session_uuid = lambda: new_uuid  # type: ignore[method-assign]

    upd, _bot, msg = _update("/goal status")
    asyncio.run(transport._on_goal(upd, _ctx("status")))
    # New session sees no goal.
    assert msg.reply_log
    assert "No active goal" in msg.reply_log[0]

    # Old record still on disk under its original UUID.
    old = store.load(old_uuid)
    assert old is not None
    assert old.goal == "orphan-able goal"
    assert old.status == "active"

    # New session has nothing.
    assert store.load(new_uuid) is None

    # list_active includes the orphan (its status is still active).
    actives = dict(store.list_active())
    assert old_uuid in actives
    assert new_uuid not in actives


def test_budget_exhaustion_renders_pause_message_at_transport_layer(
    transport: TelegramTransport,
    goals_on: None,
    goals_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§5 — turn-budget backstop. With max_turns=2, two consecutive
    continue-verdict hooks burn the budget; the third evaluation
    flips status to paused with a budget-exhausted reason and the
    transport sends the §3 budget-exhaustion status line verbatim
    (NOT "✓ Goal achieved" — budget exhaustion is a pause, not a
    completion).

    Pins the exact wording at the user-visible level (the manager
    test only asserts ``"paused" in message.lower()`` — drift in
    the exact phrasing wouldn't fail there). Mirrors the §3
    template: ``"⏸ Goal paused — N/N turns used. /goal resume to
    keep going, /goal clear to stop."``
    """
    # Override goals_max_turns to 2 so we can exhaust the budget
    # in a few hook calls.
    monkeypatch.setattr("vexis_agent.core.yaml_config.goals_max_turns", lambda: 2)

    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION,
        workspace=Path("/tmp"),
        store=store,
        default_max_turns=2,
    ).set("budget-test goal")

    bot = _FakeBot()

    async def scenario() -> None:
        await transport._running_tasks.claim(_CHAT)
        with mock.patch(
            "vexis_agent.core.goal_manager.judge_goal",
            new=mock.AsyncMock(return_value=("continue", "not yet", False)),
        ):
            # Turn 1 → continue, enqueues continuation.
            await transport._run_goal_hook(bot, _CHAT, "reply 1")
            # Drain consumed the continuation in real life; here we
            # just clear it manually so the next hook starts clean.
            await transport._running_tasks.pop_or_release(_CHAT)

            # Turn 2 → still continue per the mocked judge, BUT the
            # manager checks budget AFTER incrementing turns_used to 2,
            # so it auto-pauses. No continuation enqueued.
            await transport._run_goal_hook(bot, _CHAT, "reply 2")

        # State: paused, turns_used=2, budget-exhausted reason.
        state = store.load(_SESSION)
        assert state is not None
        assert state.status == "paused"
        assert state.turns_used == 2
        assert state.paused_reason is not None
        assert "budget exhausted" in state.paused_reason.lower()
        assert "2/2" in state.paused_reason

        # Transport sent the §3 budget-exhaustion reply verbatim.
        sent = [t for _cid, t in bot.sent_messages]
        budget_lines = [t for t in sent if t.startswith("⏸")]
        assert len(budget_lines) == 1
        line = budget_lines[0]
        assert "2/2 turns used" in line
        assert "/goal resume" in line
        assert "/goal clear" in line
        # And NOT a "Goal achieved" line — budget exhaustion isn't a win.
        assert not any("Goal achieved" in t for t in sent)

        # No leftover continuation in the queue.
        assert transport._running_tasks.queue_depth(_CHAT) == 0

    asyncio.run(scenario())


# ──────────────────────────────────────────────────────────────────
# Day 3.5 — soft-pause invariant
# ──────────────────────────────────────────────────────────────────


class _FakeBrainProc:
    """Stand-in for ``asyncio.subprocess.Process`` that records every
    kill / terminate call so the test can assert no proc-control
    happened. Only the surface ``RunningTasks`` and ``_kill_group``
    might touch is implemented (pid, returncode, kill, terminate)."""

    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.kill_calls: int = 0
        self.terminate_calls: int = 0

    def kill(self) -> None:
        self.kill_calls += 1

    def terminate(self) -> None:
        self.terminate_calls += 1

    async def wait(self) -> int:
        return 0


def test_pause_does_not_cancel_running_brain_proc(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """**Soft-pause invariant** (§4): /goal pause is queue + state
    only. It MUST NOT kill an in-flight brain subprocess, and it
    MUST NOT call ``running_tasks.cancel`` (which would tear the
    proc down via SIGTERM). The user's intent in pausing mid-turn
    is "let this turn finish, just don't auto-continue after" —
    not "kill the work in flight".

    Setup:
      - Active goal in the store.
      - Drain claimed and a brain "subprocess" attached to the
        running_tasks slot — mimics the moment the user types
        /goal pause while the brain is processing the previous
        turn.
      - Spy on ``RunningTasks.cancel`` via mock to detect any
        accidental call. Spy on ``_kill_group`` (the SIGTERM
        helper) too — defense in depth in case a future refactor
        bypasses ``cancel`` and calls the killer directly.

    Action: /goal pause via the transport handler.

    Assert:
      - Goal state on disk: status=paused, reason=user-paused.
      - ``drop_messages_matching`` ran (queue continuations cleared
        — verified by enqueueing a continuation pre-pause and
        observing it dropped).
      - ``RunningTasks.cancel`` was NEVER called.
      - ``_kill_group`` was NEVER called.
      - The fake brain proc records zero kill / terminate calls.
      - The slot is still attached after the pause — the brain
        keeps running until the drain naturally reaches it.
    """
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("port goal command")

    fake_proc = _FakeBrainProc(pid=42424)

    async def scenario() -> None:
        # Mimic a brain turn in flight: drain claimed, slot reserved,
        # proc attached. Mirrors the state transports/telegram.py
        # leaves behind during a normal brain dispatch (`_dispatch_to_brain`
        # → `running_tasks.reserve` → `running_tasks.attach`).
        await transport._running_tasks.claim(_CHAT)
        reservation = await transport._running_tasks.reserve(_CHAT)
        attached = await transport._running_tasks.attach(reservation, fake_proc)
        assert attached is True

        # Queue a continuation that the pause should drop, so the
        # drop_messages_matching contract is exercised end-to-end.
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "stale continuation",
            origin="goal_continuation",
        )
        assert transport._running_tasks.queue_depth(_CHAT) == 1

        # Spy on cancel + the proc-killer. Either firing during a
        # pause would be a regression of the soft-pause invariant.
        with mock.patch.object(
            transport._running_tasks, "cancel", wraps=transport._running_tasks.cancel
        ) as cancel_spy, \
                mock.patch("vexis_agent.core.running_tasks._kill_group") as kill_spy:
            upd, _bot, msg = _update("/goal pause")
            await transport._on_goal(upd, _ctx("pause"))

            # Hard invariants — neither path may run on /goal pause.
            cancel_spy.assert_not_called()
            kill_spy.assert_not_called()

        # State: paused on disk with the user-paused reason.
        state = store.load(_SESSION)
        assert state is not None
        assert state.status == "paused"
        assert state.paused_reason == "user-paused"

        # Queue continuation was dropped (drop_messages_matching ran).
        assert transport._running_tasks.queue_depth(_CHAT) == 0

        # The fake brain proc saw zero kill / terminate calls — the
        # in-flight turn is preserved.
        assert fake_proc.kill_calls == 0
        assert fake_proc.terminate_calls == 0

        # Slot is still attached. The drain owns the chat, the brain
        # is still "running" (in our fake sense). Pause has not
        # touched the spawn machinery at all.
        snapshot = await transport._running_tasks.snapshot()
        chat_entry = next((e for e in snapshot if e["chat_id"] == _CHAT), None)
        assert chat_entry is not None
        assert chat_entry["slot_reserved"] is True
        assert chat_entry["slot_pid"] == 42424
        assert chat_entry["cancelled"] is False
        assert chat_entry["drain_active"] is True

    asyncio.run(scenario())


# ──────────────────────────────────────────────────────────────────
# Day 4a — /status goal-summary surfacing
# ──────────────────────────────────────────────────────────────────


def test_goal_status_line_active(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """An active goal renders as ``⊙ Goal (N/M turns): <text>``."""
    store = GoalStateStore(goals_file)
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("port the goal command")
    state = mgr.state
    assert state is not None
    state.turns_used = 3
    store.save(_SESSION, state)

    line = transport._goal_status_line()
    assert line is not None
    assert line.startswith("⊙ Goal (3/20 turns):")
    assert "port the goal command" in line


def test_goal_status_line_paused_with_reason(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """A paused goal renders the paused_reason inline."""
    store = GoalStateStore(goals_file)
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("ship feature X")
    state = mgr.state
    assert state is not None
    state.turns_used = 7
    state.status = "paused"
    state.paused_reason = "user-cancelled"
    store.save(_SESSION, state)

    line = transport._goal_status_line()
    assert line is not None
    assert "⏸ Goal (paused, 7/20 turns — user-cancelled):" in line
    assert "ship feature X" in line


def test_goal_status_line_done(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    store = GoalStateStore(goals_file)
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("g")
    mgr.mark_done("delivered")
    line = transport._goal_status_line()
    assert line is not None
    assert line.startswith("✓ Goal (done):")
    assert "g" in line


def test_goal_status_line_omitted_when_cleared(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """A cleared record stays on disk for audit but doesn't show up
    in /status — same posture as ``status_line`` on the manager."""
    store = GoalStateStore(goals_file)
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("g")
    mgr.clear()
    assert transport._goal_status_line() is None


def test_goal_status_line_omitted_when_no_goal(
    transport: TelegramTransport, goals_on: None
) -> None:
    assert transport._goal_status_line() is None


def test_goal_status_line_omitted_when_disabled(
    transport: TelegramTransport, goals_off: None, goals_file: Path
) -> None:
    """Even with an active goal on disk, the /status surface is
    suppressed when goals are globally disabled — keeps the feature
    invisible when the flag is off."""
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("would-show-when-on")
    assert transport._goal_status_line() is None


def test_goal_status_line_truncates_long_goal(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """Long goal text is truncated at 80 chars with an ellipsis so a
    novella in /goal <text> doesn't blow up the /status reply."""
    store = GoalStateStore(goals_file)
    long_text = "ship the thing " * 20  # 300 chars
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set(long_text)
    line = transport._goal_status_line()
    assert line is not None
    # Truncated body at most 80 chars; "…" present.
    assert "…" in line


# ──────────────────────────────────────────────────────────────────
# Day 4b — Picking-up preview suppression for goal continuations
# ──────────────────────────────────────────────────────────────────


def test_drain_suppresses_pickup_preview_for_goal_continuation(
    transport: TelegramTransport, goals_on: None, goals_file: Path
) -> None:
    """A queued goal continuation should NOT trigger the
    ``Picking up:`` preview — the goal hook's own
    ``↻ Continuing toward goal (N/M): <reason>`` line conveys the
    same info more usefully. Real user messages still get the
    preview unchanged.

    Verified end-to-end through ``_drain_chat`` with two queued
    items: a goal continuation followed by a user message. Drain
    processes both; only the user-origin item's pickup line lands
    in the bot output.
    """
    bot = _FakeBot()

    async def scenario() -> None:
        # Pre-load the queue: goal continuation, then user message.
        await transport._running_tasks.claim(_CHAT)
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "[Continuing toward your standing goal] ...",
            origin="goal_continuation",
        )
        await transport._running_tasks.enqueue(
            _CHAT, _USER, "real follow-up", origin="user",
        )

        # Stub the goal hook so it's a no-op (we don't want it
        # running in this test — it would call judge_goal).
        async def _no_hook(*args: Any, **kwargs: Any) -> None:
            return None

        transport._run_goal_hook = _no_hook  # type: ignore[method-assign]

        # Drive the drain manually with first_text="<initial>" so we
        # exercise the loop's ordering. is_first=True for the
        # initial, so the preview never fires for it; the suppression
        # is tested on iteration 2 (continuation) and iteration 3
        # (user message).
        await transport._drain_chat(bot, _CHAT, _USER, "<initial>")

    asyncio.run(scenario())

    pickup_lines = [
        text for _cid, text in bot.sent_messages
        if text.startswith(_GOAL_PICKING_UP_PREFIX)
    ]
    assert len(pickup_lines) == 1, (
        f"expected exactly one Picking-up preview (for the user msg), "
        f"got {pickup_lines}"
    )
    assert "real follow-up" in pickup_lines[0]
    # And the goal-continuation message went through the brain handler
    # WITHOUT a preceding Picking-up preview.
    assert "Continuing toward your standing goal" not in " ".join(pickup_lines)


# ──────────────────────────────────────────────────────────────────
# Day 5.5 — Telegram pause/resume on terminal goal
# ──────────────────────────────────────────────────────────────────


def test_telegram_pause_after_done_replies_already_done(
    transport: TelegramTransport,
    goals_on: None,
    goals_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the user types ``/goal pause`` on a goal whose disk
    state has flipped to ``done`` (from evaluate_after_turn) since
    this command's manager init, the handler must NOT confirm a
    pause that didn't happen. It surfaces the
    :class:`TerminalGoalError` as an explicit "Goal is already done"
    reply.

    Simulated by patching ``_build_goal_manager`` to flip disk to
    done out-of-band before mgr.pause runs — same race-injection
    pattern as the dashboard 409 race tests."""
    store = GoalStateStore(goals_file)
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("g")

    real_build = transport._build_goal_manager

    def racing_build(session_uuid: str):
        mgr = real_build(session_uuid)
        # Concurrent writer flips disk to done — the manager's
        # in-memory still says active.
        disk = store.load(session_uuid)
        assert disk is not None
        disk.status = "done"
        disk.last_verdict = "done"
        disk.last_reason = "concurrent finish"
        store.save(session_uuid, disk)
        return mgr

    transport._build_goal_manager = racing_build  # type: ignore[method-assign]

    upd, _bot, msg = _update("/goal pause")
    asyncio.run(transport._on_goal(upd, _ctx("pause")))

    expected = _GOAL_ALREADY_TERMINAL_TMPL.format(status="done")
    assert msg.reply_log == [expected]

    # No queue mutation — pause didn't happen.
    assert transport._running_tasks.queue_depth(_CHAT) == 0
    # Disk still done.
    final = store.load(_SESSION)
    assert final is not None
    assert final.status == "done"
    assert final.paused_reason is None


def test_telegram_resume_after_done_replies_already_done(
    transport: TelegramTransport,
    goals_on: None,
    goals_file: Path,
) -> None:
    """Resume on a done goal also surfaces the terminal reply."""
    store = GoalStateStore(goals_file)
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("g")
    state = mgr.state
    assert state is not None
    state.status = "paused"
    store.save(_SESSION, state)

    real_build = transport._build_goal_manager

    def racing_build(session_uuid: str):
        mgr = real_build(session_uuid)
        disk = store.load(session_uuid)
        assert disk is not None
        disk.status = "done"
        disk.last_verdict = "done"
        disk.last_reason = "concurrent finish"
        store.save(session_uuid, disk)
        return mgr

    transport._build_goal_manager = racing_build  # type: ignore[method-assign]

    upd, _bot, msg = _update("/goal resume")
    asyncio.run(transport._on_goal(upd, _ctx("resume")))

    expected = _GOAL_ALREADY_TERMINAL_TMPL.format(status="done")
    assert msg.reply_log == [expected]

    final = store.load(_SESSION)
    assert final is not None
    assert final.status == "done"
