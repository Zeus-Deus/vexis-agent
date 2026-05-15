"""Schedule outcome plumbing — regression suite for the May 2026
"dashboard shows ok, Telegram shows error" drift.

Background: when a scheduled fire failed (the 15 May 2026 Anthropic
500), the schedule's ``last_status`` stayed ``"ok"`` because the
dispatcher's ``_record_fire`` wrote success the moment the prompt
was enqueued, with no path for the real brain outcome to overwrite
it later.

This suite pins the post-fix behaviour:

  1. ``ScheduleManager.report_fire_outcome`` overwrites
     ``last_status``, ``last_error``, and ``consecutive_errors`` on
     real brain outcome.
  2. ``success=False, is_permanent=True`` auto-pauses immediately
     instead of waiting for N consecutive errors.
  3. ``success=False, is_permanent=False`` increments the counter
     and auto-pauses at the threshold (same rule as the existing
     enqueue-failure path, applied to brain-failure cause).
  4. ``QueuedMessage.schedule_id`` propagates through
     ``RunningTasks.enqueue``.
  5. ``MessageHandler.handle`` populates :class:`TurnOutcome`
     correctly for each brain exception type.
  6. End-to-end: TelegramTransport drain calls
     ``schedule_outcome_cb`` exactly once per scheduled-fire turn
     with the correct flags.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vexis_agent.core.brain.base import (
    BrainCancelled,
    BrainPermanentError,
    BrainTimeoutError,
    BrainTransientError,
    SessionLost,
)
from vexis_agent.core.brain.null import BrainNull
from vexis_agent.core.handler import MessageHandler, TurnOutcome
from vexis_agent.core.running_tasks import QueuedMessage, RunningTasks
from vexis_agent.core.schedule_manager import ScheduleManager
from vexis_agent.core.schedule_state import (
    ScheduleState,
    ScheduleStore,
)
from vexis_agent.core.sessions import SessionStore


# ─── QueuedMessage carries schedule_id ───────────────────────────


def test_queued_message_default_schedule_id_is_none():
    """Real-user messages don't carry a schedule_id. Defaulting to
    None lets every existing call site keep working unchanged."""
    msg = QueuedMessage(user_id=1, text="hi", origin="user")
    assert msg.schedule_id is None


def test_running_tasks_enqueue_threads_schedule_id():
    """The enqueue API accepts schedule_id and stamps it on the
    stored QueuedMessage so the drain can read it back."""
    rt = RunningTasks()

    async def scenario():
        await rt.enqueue(
            chat_id=42, user_id=999, text="run skill-sync",
            origin="scheduled_fire", schedule_id="sched-abc",
        )
        msg = await rt.pop_or_release(42)
        return msg

    # claim first so pop returns instead of releasing immediately
    asyncio.run(rt.claim(42))
    msg = asyncio.run(scenario())
    assert msg is not None
    assert msg.schedule_id == "sched-abc"
    assert msg.origin == "scheduled_fire"


# ─── TurnOutcome population matrix ───────────────────────────────


def _make_handler(brain, tmp_path: Path) -> MessageHandler:
    """One-shot MessageHandler wired against a tmp-path session store."""
    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = tmp_path / "sessions.json"
    sessions._active = "test"
    sessions._sessions = {
        "test": {
            "uuid": "00000000-0000-0000-0000-000000000000",
            "initialized": True,
            "created_at": "2026-05-09T00:00:00+00:00",
        },
    }
    return MessageHandler(
        brain=brain, sessions=sessions, allowed_user_id=12345,
    )


@pytest.mark.parametrize("exc,expected_kind", [
    (BrainTransientError("API Error: 500 hiccup"), "transient"),
    (BrainPermanentError("API Error: 401 unauthorized"), "permanent"),
    (BrainTimeoutError("brain ran past ceiling"), "timeout"),
    (BrainCancelled("user cancelled"), "cancelled"),
    (SessionLost("session vanished"), "session_lost"),
])
def test_handle_populates_outcome_per_exception(exc, expected_kind, tmp_path):
    """Each brain exception maps to the matching TurnOutcome.kind so
    the schedule manager can apply the right policy."""

    class _ExcBrain(BrainNull):
        async def respond(self, *a, **kw):
            raise exc

    h = _make_handler(_ExcBrain(responses=[]), tmp_path)
    out = TurnOutcome()
    asyncio.run(h.handle(12345, 1, "x", outcome=out))
    assert out.kind == expected_kind
    if expected_kind in ("transient", "permanent"):
        assert out.error_message and str(exc) in out.error_message


def test_handle_populates_outcome_on_success(tmp_path):
    """A real reply lands as kind=ok with no error_message."""
    h = _make_handler(BrainNull(responses=["hello sir"]), tmp_path)
    out = TurnOutcome()
    reply = asyncio.run(h.handle(12345, 1, "x", outcome=out))
    assert reply == "hello sir"
    assert out.kind == "ok"
    assert out.error_message is None


def test_handle_populates_outcome_on_empty(tmp_path):
    """The brain returning whitespace-only text counts as "empty"
    (a different kind from "ok") but still ``succeeded == True``."""
    h = _make_handler(BrainNull(responses=["   \n"]), tmp_path)
    out = TurnOutcome()
    asyncio.run(h.handle(12345, 1, "x", outcome=out))
    assert out.kind == "empty"
    assert out.succeeded is True


def test_handle_populates_outcome_on_rejected(tmp_path):
    """Auth gate denials get a distinct kind so the schedule manager
    knows the brain wasn't actually invoked."""
    h = _make_handler(BrainNull(responses=["whatever"]), tmp_path)
    out = TurnOutcome()
    # Wrong user_id → rejected
    asyncio.run(h.handle(99999, 1, "x", outcome=out))
    assert out.kind == "rejected"


def test_handle_outcome_kind_predicates():
    """The helper booleans on TurnOutcome agree with the kind."""
    assert TurnOutcome(kind="ok").succeeded is True
    assert TurnOutcome(kind="empty").succeeded is True
    assert TurnOutcome(kind="transient").succeeded is False
    assert TurnOutcome(kind="transient").is_brain_failure is True
    assert TurnOutcome(kind="permanent").is_brain_failure is True
    assert TurnOutcome(kind="permanent").is_permanent_failure is True
    assert TurnOutcome(kind="transient").is_permanent_failure is False
    assert TurnOutcome(kind="cancelled").is_brain_failure is False
    assert TurnOutcome(kind="cancelled").is_permanent_failure is False
    assert TurnOutcome(kind="rejected").is_brain_failure is False


# ─── ScheduleManager.report_fire_outcome ─────────────────────────


def _make_schedule(
    store: ScheduleStore,
    *,
    consecutive_errors: int = 0,
    status: str = "active",
    last_status: str | None = "ok",
) -> ScheduleState:
    """Create an active schedule with pre-set error counters so the
    test can drive ``report_fire_outcome`` without needing to fire
    a real schedule first."""
    now = datetime(2026, 5, 15, 0, 30, tzinfo=timezone.utc)
    state = ScheduleState(
        id="sched-test",
        chat_id=42,
        schedule={"kind": "cron", "expr": "30 0 * * *", "tz": "UTC", "display": "30 0 * * *"},
        schedule_display="30 0 * * *",
        prompt="run skill-sync",
        name="skill-sync-daily",
        next_fire_at=now + timedelta(days=1),
        last_fire_at=now,
        last_status=last_status,
        consecutive_errors=consecutive_errors,
        status=status,
        created_at=now,
        updated_at=now,
    )
    store.update_atomic(state.id, lambda _s: state, refuse_terminal=False) if False else None
    # Use save_new — store.create
    raw = state.to_dict()
    # Persist via update_atomic on a fresh insert: ScheduleStore has
    # a ``create`` API; use it if available, otherwise write directly.
    if hasattr(store, "create"):
        try:
            store.create(state)
            return state
        except Exception:
            pass
    # Fallback: write raw via the store's write path.
    import json
    path = store._path
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": store.SCHEMA_VERSION, "schedules": {state.id: raw}}
    path.write_text(json.dumps(data, indent=2))
    return state


def _make_manager(
    store: ScheduleStore,
    *,
    max_errors: int = 3,
) -> ScheduleManager:
    rt = RunningTasks()
    return ScheduleManager(
        store, running_tasks=rt, allowed_user_id=999,
        enabled_fn=lambda: True,
        tick_interval_seconds_fn=lambda: 30,
        max_consecutive_errors_fn=lambda: max_errors,
    )


def test_report_fire_outcome_success_resets_counters(tmp_path):
    """A success outcome clears any prior error state — same as the
    pre-existing ``_record_fire(success=True)``, just driven by the
    real brain outcome instead of the optimistic dispatch-time write."""
    store = ScheduleStore(tmp_path / "schedules.json")
    state = _make_schedule(
        store, consecutive_errors=2, last_status="error",
    )
    manager = _make_manager(store)

    manager.report_fire_outcome(state.id, success=True)

    reloaded = store.load(state.id)
    assert reloaded is not None
    assert reloaded.last_status == "ok"
    assert reloaded.last_error is None
    assert reloaded.consecutive_errors == 0
    assert reloaded.status == "active"


def test_report_fire_outcome_transient_increments_counter(tmp_path):
    """A transient brain failure (Anthropic 500) flips last_status
    from "ok" to "error" and increments the counter. The schedule
    stays active — the next daily fire still runs."""
    store = ScheduleStore(tmp_path / "schedules.json")
    state = _make_schedule(store, consecutive_errors=0, last_status="ok")
    manager = _make_manager(store, max_errors=3)

    manager.report_fire_outcome(
        state.id,
        success=False,
        error_message="API Error: 500 Internal server error",
        is_permanent=False,
    )

    reloaded = store.load(state.id)
    assert reloaded is not None
    assert reloaded.last_status == "error"
    assert reloaded.last_error == "API Error: 500 Internal server error"
    assert reloaded.consecutive_errors == 1
    assert reloaded.status == "active"  # not paused yet


def test_report_fire_outcome_transient_auto_pauses_at_threshold(tmp_path):
    """Repeated transient failures eventually take the schedule out
    of rotation — same threshold the enqueue-failure path uses, just
    counting brain failures instead."""
    store = ScheduleStore(tmp_path / "schedules.json")
    state = _make_schedule(store, consecutive_errors=2, last_status="error")
    manager = _make_manager(store, max_errors=3)

    manager.report_fire_outcome(
        state.id, success=False,
        error_message="API Error: 503 Service Unavailable",
    )

    reloaded = store.load(state.id)
    assert reloaded is not None
    assert reloaded.status == "paused"
    assert reloaded.paused_reason == "auto: errors"
    assert reloaded.consecutive_errors == 3


def test_report_fire_outcome_permanent_pauses_immediately(tmp_path):
    """A permanent error (auth, bad model id) takes the schedule out
    of rotation NOW — the user has to fix something before the next
    fire could succeed."""
    store = ScheduleStore(tmp_path / "schedules.json")
    state = _make_schedule(store, consecutive_errors=0, last_status="ok")
    manager = _make_manager(store, max_errors=10)  # generous threshold

    manager.report_fire_outcome(
        state.id, success=False,
        error_message="claude -p exited 1: API Error: 401 Unauthorized",
        is_permanent=True,
    )

    reloaded = store.load(state.id)
    assert reloaded is not None
    assert reloaded.status == "paused"
    assert reloaded.paused_reason is not None
    assert reloaded.paused_reason.startswith("permanent_failure:")
    # The reason string carries enough context for the user to
    # understand without reading the daemon log.
    assert "401" in reloaded.paused_reason


def test_report_fire_outcome_permanent_truncates_long_reason(tmp_path):
    """The paused_reason string is bounded — a multi-paragraph error
    body would overflow the dashboard's reason chip."""
    store = ScheduleStore(tmp_path / "schedules.json")
    state = _make_schedule(store)
    manager = _make_manager(store)

    long_msg = "API Error: 401 " + ("very long detail " * 30)
    manager.report_fire_outcome(
        state.id, success=False, error_message=long_msg, is_permanent=True,
    )

    reloaded = store.load(state.id)
    assert reloaded is not None
    assert reloaded.paused_reason is not None
    assert len(reloaded.paused_reason) <= 200  # generous headroom
    assert reloaded.paused_reason.endswith("…") or len(long_msg) < 120


def test_report_fire_outcome_ignores_unknown_schedule_id(tmp_path):
    """A schedule deleted mid-flight (race between dispatch and drain)
    must not crash the drain. The outcome is logged and dropped."""
    store = ScheduleStore(tmp_path / "schedules.json")
    manager = _make_manager(store)

    # No schedule exists — should be a no-op, no exception.
    manager.report_fire_outcome(
        "does-not-exist", success=True,
    )
    manager.report_fire_outcome(
        "does-not-exist", success=False, error_message="boom",
    )


def test_report_fire_outcome_empty_schedule_id_is_noop(tmp_path):
    """Defensive: empty / None schedule_id silently drops. The drain
    shouldn't call ``report_fire_outcome`` without a schedule_id, but
    if it does (bug), no crash."""
    store = ScheduleStore(tmp_path / "schedules.json")
    manager = _make_manager(store)
    manager.report_fire_outcome("", success=True)
    # No assertions needed — we just want this not to raise.


# ─── End-to-end: drain reports outcome to manager ────────────────


def test_drain_calls_outcome_callback_on_scheduled_fire(tmp_path):
    """When a QueuedMessage carries a ``schedule_id``, the drain
    invokes ``transport._schedule_outcome_cb`` with the brain's
    actual outcome after the turn finishes. The integration point
    that wires last_status to the truth."""
    # Use a stub transport-ish object to isolate the drain's outcome
    # call from the rest of TelegramTransport (PTB Application, etc).
    from vexis_agent.transports.telegram import TelegramTransport

    captured: list[tuple[str, bool, str | None, bool]] = []

    def _capture(sid, *, success, error_message, is_permanent):
        captured.append((sid, success, error_message, is_permanent))

    # Build a minimal handler that raises a transient error.
    class _BoomBrain(BrainNull):
        async def respond(self, *a, **kw):
            raise BrainTransientError(
                "claude -p exited 1: API Error: 500 transient blip"
            )

    handler = _make_handler(_BoomBrain(responses=[]), tmp_path)

    # _make a transport without PTB by bypassing __init__.
    transport = TelegramTransport.__new__(TelegramTransport)
    transport._handler = handler  # type: ignore[attr-defined]
    transport._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    transport._background_tasks = None  # type: ignore[attr-defined]
    transport._notifier = None  # type: ignore[attr-defined]
    transport._allowed_user_id = 12345  # type: ignore[attr-defined]
    transport._curator = None  # type: ignore[attr-defined]
    transport._learning_curator = None  # type: ignore[attr-defined]
    transport._dashboard = None  # type: ignore[attr-defined]
    transport._schedule_store = None  # type: ignore[attr-defined]
    transport._kanban_store = None  # type: ignore[attr-defined]
    transport._pending_deletes = {}  # type: ignore[attr-defined]
    transport._picker_pending = {}  # type: ignore[attr-defined]
    transport._streaming_enabled = False  # type: ignore[attr-defined]
    transport._background_dispatch_tasks = set()  # type: ignore[attr-defined]
    transport._schedule_outcome_cb = _capture  # type: ignore[attr-defined]

    # Stub the methods _drain_chat depends on so we don't need PTB.
    async def _noop(*a, **kw): return None
    transport._run_relationships_hook = _noop  # type: ignore[attr-defined]
    transport._run_goal_hook = _noop  # type: ignore[attr-defined]
    transport._send_brain_reply = _noop  # type: ignore[attr-defined]
    transport._keep_typing = _noop  # type: ignore[attr-defined]

    class _FakeBot:
        async def send_message(self, *a, **kw):
            class _M:
                message_id = 1
            return _M()

    bot = _FakeBot()

    async def scenario():
        await transport._running_tasks.claim(42)
        await transport._drain_chat(
            bot, chat_id=42, user_id=12345,
            first_text="run skill-sync",
            first_schedule_id="sched-abc",
        )

    asyncio.run(scenario())

    assert len(captured) == 1, (
        f"expected exactly one outcome callback, got {len(captured)}: {captured}"
    )
    sid, success, error_message, is_permanent = captured[0]
    assert sid == "sched-abc"
    assert success is False
    assert error_message and "API Error: 500" in error_message
    assert is_permanent is False  # 500 is transient, not permanent


def test_drain_skips_outcome_callback_for_real_user_messages(tmp_path):
    """No callback fires for a non-scheduled drain pass. The hot
    path for real-user messages stays unchanged."""
    from vexis_agent.transports.telegram import TelegramTransport

    captured: list = []

    handler = _make_handler(BrainNull(responses=["normal reply"]), tmp_path)

    transport = TelegramTransport.__new__(TelegramTransport)
    transport._handler = handler  # type: ignore[attr-defined]
    transport._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    transport._allowed_user_id = 12345  # type: ignore[attr-defined]
    transport._streaming_enabled = False  # type: ignore[attr-defined]
    transport._schedule_outcome_cb = lambda *a, **kw: captured.append(a)  # type: ignore[attr-defined]

    async def _noop(*a, **kw): return None
    transport._run_relationships_hook = _noop  # type: ignore[attr-defined]
    transport._run_goal_hook = _noop  # type: ignore[attr-defined]
    transport._send_brain_reply = _noop  # type: ignore[attr-defined]

    class _FakeBot:
        async def send_message(self, *a, **kw):
            class _M:
                message_id = 1
            return _M()

    async def scenario():
        await transport._running_tasks.claim(42)
        await transport._drain_chat(
            _FakeBot(), chat_id=42, user_id=12345,
            first_text="hi from user",
        )

    asyncio.run(scenario())

    assert captured == [], (
        f"outcome callback fired for real-user drain: {captured}"
    )
