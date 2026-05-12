"""ScheduleManager — daemon thread that fires due schedules into the chat.

Mirrors :class:`core.learning_curator.LearningCurator` in lifecycle
shape (daemon thread, ``stop_event.wait(interval)`` loop, exception-
isolated tick body). Fires schedules into the existing per-chat FIFO
queue (`core.running_tasks.RunningTasks.enqueue`) with
``origin="scheduled_fire"`` so the brain processes them like any
other user message — same prompt cache, same post-turn hooks.

At-most-once execution guarantee (mirrors the upstream pattern
`cron/scheduler.py:1476-1477` and the openclaw ``runningAtMs``
pattern): :meth:`_fire_one` advances ``next_fire_at`` to the next
future slot **before** calling enqueue. A crash between advance and
enqueue loses the missed fire — by design, since the alternative is
infinite crash-loop re-fires.

The manager does not handle one-shot expiration directly — that
happens implicitly because :func:`parser.compute_next_fire` returns
``None`` for fired one-shots, and a row with no ``next_fire_at`` is
never returned by ``ScheduleStore.list_due``.

Design citation:
``.plans/scheduling-and-provider-abstraction-research.md`` §4 (Tick
loop, Fire mechanism, Restart safety), Day 2.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.schedule_state import (
    DEFAULT_STUCK_RUN_TTL_SECONDS,
    ScheduleState,
    ScheduleStore,
    TerminalScheduleError,
)
from vexis_agent.tools.schedule_tool.parser import compute_next_fire

log = logging.getLogger(__name__)


# Minimum gap between consecutive fires of the same schedule. Mirrors
# openclaw's ``MIN_REFIRE_GAP_MS=2000`` but at chat-appropriate scale:
# any cron whose computed next-fire is within 60s of last-fire is
# bumped forward. Defends against ``* * * * *`` firing on every tick
# of a fast-cadence manager.
MIN_REFIRE_GAP_SECONDS = 60


def _utc_now() -> datetime:
    """Single chokepoint for "current time". Tests monkeypatch this."""
    return datetime.now(timezone.utc)


class ScheduleManager:
    """Background tick loop that fires due schedules into the chat FIFO.

    Lifecycle (mirrors LearningCurator):

      * :meth:`start` — spawn the daemon thread. Idempotent; safe to
        call multiple times (only the first spawn takes effect).
      * :meth:`stop` — signal the thread to exit at its next wakeup.
        Bounded by ``tick_interval_seconds`` worst-case shutdown delay.

    The manager is intentionally simple: no work queue, no per-fire
    background tasks, no priority. Each tick reads the disk store,
    enqueues all due schedules in deterministic order (by id), updates
    next_fire_at + last_fire_at + status fields under the store's
    fcntl lock, and sleeps.

    Concurrency: the daemon thread is the only writer of last_fire_at,
    running_at, and (for fire-driven updates) next_fire_at. The CLI /
    slash command write paths use ``ScheduleStore.update_atomic`` so
    pause/resume/clear races are safe — fcntl serializes everything.
    """

    def __init__(
        self,
        store: ScheduleStore,
        running_tasks: RunningTasks,
        *,
        allowed_user_id: int,
        tick_interval_seconds_fn=None,
        max_consecutive_errors_fn=None,
        enabled_fn=None,
        stuck_run_ttl_seconds: int = DEFAULT_STUCK_RUN_TTL_SECONDS,
    ) -> None:
        """Construct the manager. Does not spawn the thread — call
        :meth:`start` on the event loop.

        ``*_fn`` are nullary callables read once per tick so config
        edits hot-reload without restarting the daemon. Defaults pull
        from :mod:`vexis_agent.core.yaml_config`; tests inject
        constants. Mirrors the pattern in
        :class:`core.learning_curator.LearningCurator`.
        """
        self._store = store
        self._running_tasks = running_tasks
        self._user_id = allowed_user_id

        # Late import to keep schedule_manager.py importable when
        # yaml_config wiring isn't fully set up (e.g. unit tests).
        if tick_interval_seconds_fn is None:
            from vexis_agent.core.yaml_config import (
                schedules_tick_interval_seconds as _tick,
            )
            tick_interval_seconds_fn = _tick
        if max_consecutive_errors_fn is None:
            from vexis_agent.core.yaml_config import (
                schedules_max_consecutive_errors as _max,
            )
            max_consecutive_errors_fn = _max
        if enabled_fn is None:
            from vexis_agent.core.yaml_config import (
                schedules_enabled as _enabled,
            )
            enabled_fn = _enabled

        self._tick_interval_fn = tick_interval_seconds_fn
        self._max_consecutive_errors_fn = max_consecutive_errors_fn
        self._enabled_fn = enabled_fn
        self._stuck_run_ttl_seconds = stuck_run_ttl_seconds

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Tracks "did we already do the boot-time sweep?" so we don't
        # repeat it on every tick.
        self._booted = False

    # ----- lifecycle -------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Spawn the daemon thread. Idempotent.

        ``loop`` is the asyncio loop owned by the transport (Telegram).
        The manager schedules ``RunningTasks.enqueue`` onto it via
        ``run_coroutine_threadsafe`` from the tick thread.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._loop = loop
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="vexis-schedule-manager",
            daemon=True,
        )
        self._thread.start()
        log.info("ScheduleManager started")

    def stop(self) -> None:
        """Signal the daemon thread to exit at its next wakeup.

        Worst-case shutdown delay is ``tick_interval_seconds``. The
        thread is daemon, so process exit doesn't block on it; this
        method exists for clean test teardown and graceful shutdown.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._loop = None
        log.info("ScheduleManager stopped")

    # ----- tick loop -------------------------------------------------

    def _run_loop(self) -> None:
        """Main daemon body. Sleeps, wakes, calls ``_run_once``, repeats."""
        while not self._stop.is_set():
            # First iteration: do the boot sweep, then the normal tick.
            # Subsequent iterations: just the tick. Splitting these lets
            # tests call ``_run_once`` directly without firing the sweep.
            if not self._booted:
                try:
                    self._sweep_stuck_markers()
                except Exception:
                    log.exception("ScheduleManager boot sweep raised")
                self._booted = True
            try:
                if self._enabled_fn():
                    self._run_once()
            except Exception:
                log.exception("ScheduleManager tick raised")
            self._stop.wait(self._tick_interval_fn())

    def _run_once(self, *, now: datetime | None = None) -> int:
        """One tick. Returns the number of schedules fired.

        Public-ish for tests and the CLI's ``vexis-agent schedule tick``
        debug subcommand. Callers that bypass the daemon are
        responsible for ensuring no other tick is in flight (the
        ``stuck_marker`` sweep is the recovery rail for crashes
        during a fire; double-firing in a healthy daemon happens at
        most once per missed advance).
        """
        if now is None:
            now = _utc_now()
        due = self._store.list_due(now=now)
        if not due:
            return 0

        # Deterministic order by id so tests can assert and a tie at
        # 09:00:00 between three schedules always fires in the same
        # order (alphabetical id breaks the tie).
        due.sort(key=lambda s: s.id)

        fired = 0
        for schedule in due:
            try:
                if self._fire_one(schedule, now=now):
                    fired += 1
            except Exception:
                log.exception(
                    "Schedule %s fire raised; will retry on next tick",
                    schedule.id,
                )
        return fired

    # ----- fire one schedule -----------------------------------------

    def _fire_one(self, schedule: ScheduleState, *, now: datetime) -> bool:
        """Advance next_fire_at, mark running, enqueue. Returns True on
        success, False if skipped (paused mid-flight, drain cancelled,
        terminal status raced).
        """
        # If the drain was cancelled mid-tick (user typed /cancel),
        # drop this fire — advancing next_fire_at first per the
        # at-most-once contract.
        if self._is_drain_cancelled(schedule.chat_id):
            log.info(
                "Dropping scheduled fire %s — drain cancelled for chat %d",
                schedule.id,
                schedule.chat_id,
            )
            self._advance_and_save(schedule, fired_at=None)
            return False

        # Step 1: advance next_fire_at BEFORE enqueue.
        # at-most-once: a crash after this point loses the missed fire,
        # not re-fires forever.
        try:
            self._advance_and_save(schedule, fired_at=now)
        except TerminalScheduleError:
            # Schedule was paused/cleared between list_due and now;
            # treat as a non-fire.
            return False
        except KeyError:
            # Schedule was deleted between list_due and now; treat as
            # a non-fire.
            return False

        # Step 2: enqueue the synthetic user message.
        # Done from the daemon thread via run_coroutine_threadsafe;
        # the asyncio loop owns RunningTasks.
        success = self._enqueue_synthetic(
            chat_id=schedule.chat_id,
            text=schedule.prompt,
        )

        # Step 3: record fire status. update_atomic with
        # refuse_terminal=False so a concurrent /schedule clear doesn't
        # crash the post-fire bookkeeping.
        try:
            self._store.update_atomic(
                schedule.id,
                lambda s: _record_fire(
                    s,
                    fired_at=now,
                    success=success,
                    max_errors=self._max_consecutive_errors_fn(),
                ),
                refuse_terminal=False,
            )
        except KeyError:
            pass  # schedule deleted; nothing to record against

        return success

    def _advance_and_save(
        self,
        schedule: ScheduleState,
        *,
        fired_at: datetime | None,
    ) -> None:
        """Compute the next fire time and persist it BEFORE enqueue.

        ``fired_at`` is None when we're advancing past a dropped fire
        (e.g. drain cancelled) — the missed slot is still gone, but
        last_fire_at is not updated.
        """
        new_next = compute_next_fire(
            schedule.schedule,
            last_fire_at=fired_at if fired_at is not None else None,
        )
        # MIN_REFIRE_GAP_SECONDS defense — never advance to a slot
        # within 60s of the slot we just fired. Bumps fast crons to
        # the next slot they'd land on after the gap.
        if new_next is not None and fired_at is not None:
            min_next = fired_at + timedelta(seconds=MIN_REFIRE_GAP_SECONDS)
            while new_next < min_next:
                bumped = compute_next_fire(
                    schedule.schedule,
                    last_fire_at=new_next,
                )
                if bumped is None or bumped <= new_next:
                    new_next = None
                    break
                new_next = bumped

        # One-shots with new_next=None get expired. Recurring with
        # new_next=None means croniter returned nothing (degenerate
        # schedule); also expire defensively.
        new_status = "expired" if new_next is None else schedule.status

        def _mutate(s: ScheduleState) -> ScheduleState:
            from dataclasses import replace
            return replace(
                s,
                next_fire_at=new_next,
                running_at=fired_at,
                status=new_status,
            )

        try:
            self._store.update_atomic(
                schedule.id, _mutate, refuse_terminal=True
            )
        except TerminalScheduleError:
            raise
        except KeyError:
            raise

    def _enqueue_synthetic(self, *, chat_id: int, text: str) -> bool:
        """Schedule the enqueue on the asyncio loop. Returns True on
        success.

        Catches all exceptions so a transport-side failure (loop
        closed, RunningTasks not initialized) doesn't kill the
        daemon thread. The caller increments consecutive_errors on
        False.
        """
        if self._loop is None or self._loop.is_closed():
            log.warning(
                "ScheduleManager has no loop; cannot enqueue chat=%d",
                chat_id,
            )
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._running_tasks.enqueue(
                    chat_id=chat_id,
                    user_id=self._user_id,
                    text=text,
                    origin="scheduled_fire",
                ),
                self._loop,
            )
            # Block up to 5s for the enqueue to complete — the enqueue
            # itself is fast (just a deque append under a lock), so
            # this is mainly to surface errors synchronously.
            future.result(timeout=5.0)
            return True
        except Exception as exc:
            log.warning(
                "ScheduleManager failed to enqueue chat=%d: %s",
                chat_id,
                exc,
            )
            return False

    def _is_drain_cancelled(self, chat_id: int) -> bool:
        """Peek at the RunningTasks drain-cancelled flag for ``chat_id``.

        Best-effort: if RunningTasks doesn't expose the flag (older
        version, test fake), return False. The check is a defensive
        early-exit, not a correctness invariant — the FIFO will drop
        the message if the drain is truly dead.
        """
        getter = getattr(
            self._running_tasks, "is_drain_cancelled", None
        )
        if getter is None:
            return False
        try:
            return bool(getter(chat_id))
        except Exception:
            return False

    # ----- boot-time sweep -------------------------------------------

    def _sweep_stuck_markers(self, *, now: datetime | None = None) -> int:
        """Clear ``running_at`` markers older than the stuck TTL.

        Called once at thread startup. A marker older than 5min came
        from a fire that crashed between advance_next_run and the post-
        fire bookkeeping. ``next_fire_at`` is already correct (the
        advance happened); we just need to clear the stale marker so
        the schedule looks healthy.

        Returns the number of markers cleared. Does NOT recompute
        next_fire_at — the missed fire is genuinely missed (the upstream
        rule).
        """
        if now is None:
            now = _utc_now()
        ttl_cutoff = now - timedelta(seconds=self._stuck_run_ttl_seconds)
        cleared = 0
        for state in self._store.list_all():
            if state.running_at is None:
                continue
            ra = state.running_at
            if ra.tzinfo is None:
                ra = ra.replace(tzinfo=timezone.utc)
            if ra > ttl_cutoff:
                continue
            try:
                from dataclasses import replace
                self._store.update_atomic(
                    state.id,
                    lambda s: replace(s, running_at=None),
                    refuse_terminal=False,
                )
                cleared += 1
                log.info(
                    "Swept stuck running_at marker for schedule %s "
                    "(age %s)",
                    state.id,
                    (now - ra),
                )
            except (KeyError, TerminalScheduleError):
                continue
        return cleared


def _record_fire(
    state: ScheduleState,
    *,
    fired_at: datetime,
    success: bool,
    max_errors: int,
) -> ScheduleState:
    """Mutator helper: write last_fire_at/status, clear running_at,
    update consecutive_errors, auto-pause on threshold.

    Lives at module scope so tests can call it directly.
    """
    from dataclasses import replace

    if success:
        return replace(
            state,
            last_fire_at=fired_at,
            last_status="ok",
            last_error=None,
            consecutive_errors=0,
            running_at=None,
        )

    new_errors = state.consecutive_errors + 1
    if new_errors >= max_errors and state.status == "active":
        return replace(
            state,
            last_fire_at=fired_at,
            last_status="error",
            last_error="enqueue failed",
            consecutive_errors=new_errors,
            running_at=None,
            status="paused",
            paused_reason="auto: errors",
        )
    return replace(
        state,
        last_fire_at=fired_at,
        last_status="error",
        last_error="enqueue failed",
        consecutive_errors=new_errors,
        running_at=None,
    )


__all__ = [
    "MIN_REFIRE_GAP_SECONDS",
    "ScheduleManager",
]
