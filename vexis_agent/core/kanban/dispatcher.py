"""Kanban dispatcher — ticks every N seconds, claims ready tasks,
spawns workers via ``brain.spawn_aux``.

Design lock: ``.plans/kanban-research.md`` §5 + §6.

Architecture summary:

  * One :class:`KanbanController` per daemon. Constructed at startup
    from ``main.py:_run()``; started after ``brain`` exists and stopped
    before ``brain`` is destructed.
  * The controller owns ONE asyncio task that ticks every
    ``kanban_dispatch_interval_seconds()`` (default 60s). Each tick:
      1. Cleanup stale claims (release claims whose TTL expired —
         worker probably crashed without heartbeating).
      2. Recompute ready (promote ``todo → ready`` when parents done).
      3. Spawn ready tasks up to ``max_concurrent_workers`` cap. Each
         spawn runs as its own asyncio.create_task so the dispatcher
         tick isn't blocked by ``brain.spawn_aux`` (which can take
         minutes).
  * Spawned worker tasks are NOT awaited by the dispatcher. They
    finalise their own run row + task status before exiting; if they
    crash mid-flight, the next tick's stale-claim cleanup releases
    the task and bumps consecutive_failures.
  * Workers communicate back via kanban_* MCP tools (Phase 4).
    Specifically: ``kanban_complete`` flips the task to ``done`` +
    finalises the run; ``kanban_block`` flips to ``blocked`` and
    notifies the user; ``kanban_heartbeat`` extends the claim TTL.

Concurrency invariants:

  * SQL CAS on claim_task — two dispatchers can't both grab the same
    task even if we ever ran a second instance.
  * count_in_flight() reads BEFORE the claim, so the cap is approximate
    under contention (we might spawn 1 over). Fine for single-user.
  * Spawn tasks tracked in ``_in_flight_tasks`` set so stop() can
    cancel them gracefully.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from vexis_agent.core.kanban.constants import (
    ENV_VAR_KANBAN,
    ENV_VAR_KANBAN_LANE,
    ENV_VAR_KANBAN_TASK_ID,
    EVENT_BLOCKED,
    EVENT_COMPLETED,
    EVENT_CRASHED,
    EVENT_FAILED,
    EVENT_TIMED_OUT,
    KANBAN_WORKER_PREFIX,
    RUN_STATUS_BLOCKED,
    RUN_STATUS_CRASHED,
    RUN_STATUS_DONE,
    RUN_STATUS_FAILED,
    RUN_STATUS_TIMED_OUT,
    STATUS_BLOCKED,
    STATUS_DONE,
)
from vexis_agent.core.kanban.db import (
    ClaimContentionError,
    KanbanStore,
    Task,
)
from vexis_agent.core.kanban.lanes import (
    LaneNotFoundError,
    LaneSpec,
    kanban_claim_ttl_seconds,
    kanban_default_max_runtime_seconds,
    kanban_dispatch_interval_seconds,
    kanban_enabled,
    kanban_failure_limit,
    kanban_max_concurrent_workers,
    resolve_lane,
)

if TYPE_CHECKING:
    from vexis_agent.core.brain.base import Brain

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────


# Caller hook: optional function the dispatcher invokes on every
# event the store emits (after the dispatcher's own writes). The
# notifier subscriber and dashboard WS broadcaster register through
# this so they get an immediate kick on dispatcher-driven events
# instead of waiting for their own poll cycle. The hook MUST be fast
# and non-blocking — it runs on the dispatcher's event loop.
EventHook = Callable[[], Awaitable[None]]


@dataclass
class DispatchTickResult:
    """What happened in one tick. Used by tests + diagnostics.

    All counts are inclusive of work the tick did itself (claimed,
    spawned, promoted) but exclude follow-on work running in spawn
    tasks (which finish independently).
    """

    stale_released: int = 0
    promoted: int = 0
    claimed: int = 0
    spawned: int = 0
    skipped_at_cap: bool = False
    error: str | None = None


# ──────────────────────────────────────────────────────────────────
# Worker prompt composition
# ──────────────────────────────────────────────────────────────────


def build_worker_prompt(task: Task, lane: LaneSpec) -> str:
    """Compose the worker's first user-turn message.

    Structure (each separated by a blank line):

      1. ``KANBAN_WORKER_PREFIX`` — content marker the learning
         curator's recursion guard skips. CLAUDE.md Invariant.
      2. The lane's ``system_prompt`` — tells the worker its persona.
      3. The task body — title + body verbatim.
      4. A short MCP-tool reminder so the worker knows how to declare
         completion.

    Returned string passes through ``brain.spawn_aux`` as ``prompt``.
    The brain prepends its own system prompt (SOUL.md + memories +
    skills) above this; we don't replace that — we add a per-spawn
    user-turn slice.
    """
    parts: list[str] = [KANBAN_WORKER_PREFIX]
    if lane.system_prompt.strip():
        parts.append(lane.system_prompt.strip())
    body = task.body or ""
    parts.append(f"Task #{task.id}: {task.title}\n\n{body}".rstrip())
    parts.append(
        "When you're done, call kanban_complete with a short structured "
        "summary. If you're blocked or need user input, call kanban_block "
        "with a clear reason instead. Long tasks should call "
        "kanban_heartbeat periodically so the dispatcher knows you're "
        "still alive."
    )
    return "\n\n".join(parts)


# ──────────────────────────────────────────────────────────────────
# dispatch_once — pure tick function
# ──────────────────────────────────────────────────────────────────


def dispatch_once(
    store: KanbanStore,
    *,
    max_concurrent: int,
    claim_ttl_seconds: int,
    default_max_runtime: int,
    spawn_fn: Callable[
        [Task, LaneSpec, str, int, int], "asyncio.Task[None] | None"
    ],
) -> DispatchTickResult:
    """One dispatcher tick. Returns a :class:`DispatchTickResult`.

    The function is synchronous because the underlying SQLite calls
    are synchronous; the controller wraps each tick in
    ``await asyncio.to_thread(...)`` if it needs to. ``spawn_fn`` is
    called synchronously and is expected to schedule its own
    ``asyncio.create_task`` for the actual brain spawn — so this
    function returns quickly even when the cap is at max.

    ``spawn_fn`` signature: ``(task, lane, claim_lock, run_id,
    max_runtime_seconds) → asyncio.Task | None``. Returning None
    means "don't track; this spawn won't be awaited" (test fakes
    that don't care).
    """
    result = DispatchTickResult()
    try:
        # 1. Stale claim cleanup. Workers that died mid-flight have
        # their claims expire; release them so they can re-run.
        released = store.cleanup_stale_claims()
        result.stale_released = len(released)
        for tid in released:
            log.info("kanban: released stale claim on %s", tid)

        # 2. Promote todo → ready for tasks whose parents are done.
        promoted = store.recompute_ready()
        result.promoted = len(promoted)
        if promoted:
            log.info("kanban: promoted %d task(s): %s", len(promoted), promoted)

        # 3. Spawn ready tasks up to the cap.
        in_flight = store.count_in_flight()
        capacity = max(0, max_concurrent - in_flight)
        if capacity == 0:
            result.skipped_at_cap = True
            return result
        ready = store.list_ready()
        for task in ready:
            if capacity == 0:
                result.skipped_at_cap = True
                break
            # Resolve the lane. Unknown lane → log + skip (the dashboard
            # surfaces the LaneNotFoundError; user fixes config or
            # reassigns the task).
            try:
                lane = resolve_lane(task.lane)
            except LaneNotFoundError as exc:
                log.warning(
                    "kanban: task %s has unresolvable lane %r: %s",
                    task.id, task.lane, exc,
                )
                continue
            # Atomic claim. Contention shouldn't happen with one
            # dispatcher but the CAS is cheap insurance.
            claim_lock = uuid.uuid4().hex
            try:
                claimed = store.claim_task(
                    task.id, claim_lock=claim_lock,
                    ttl_seconds=claim_ttl_seconds,
                )
            except ClaimContentionError:
                log.debug("kanban: lost claim race on %s", task.id)
                continue
            result.claimed += 1
            max_runtime = (
                task.max_runtime_seconds
                if task.max_runtime_seconds is not None
                else default_max_runtime
            )
            run_id = store.start_run(
                task.id, lane=lane.name, claim_lock=claim_lock,
                ttl_seconds=claim_ttl_seconds,
                max_runtime_seconds=max_runtime,
                worker_pid=os.getpid(),  # dispatcher pid; real worker
                                          # pid lands once spawn_aux exec'd
            )
            spawn_task = spawn_fn(claimed, lane, claim_lock, run_id, max_runtime)
            if spawn_task is not None:
                result.spawned += 1
            capacity -= 1
    except Exception as exc:
        log.exception("kanban: dispatch_once raised")
        result.error = str(exc)
    return result


# ──────────────────────────────────────────────────────────────────
# KanbanController — daemon-level facade
# ──────────────────────────────────────────────────────────────────


class KanbanController:
    """Owns the dispatcher loop + the in-flight spawn task set.

    Mirrors the lifecycle of :class:`ScheduleManager` /
    :class:`CuratorController` so ``main.py`` can wire it the same way.
    Construct at startup, call ``start(loop)`` once the asyncio loop
    is running, ``stop()`` during shutdown.
    """

    def __init__(
        self,
        *,
        store: KanbanStore,
        brain: "Brain",
        workspace: Path,
        event_hook: EventHook | None = None,
    ) -> None:
        self._store = store
        self._brain = brain
        self._workspace = workspace
        self._event_hook = event_hook
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tick_task: asyncio.Task[None] | None = None
        # Track in-flight spawn tasks so stop() can cancel them
        # gracefully and tests can assert on what's running.
        self._in_flight: set[asyncio.Task[None]] = set()
        self._stopped = asyncio.Event()

    @property
    def store(self) -> KanbanStore:
        return self._store

    def in_flight_count(self) -> int:
        """Number of spawn tasks the controller is currently tracking."""
        return len(self._in_flight)

    # ─── lifecycle ───────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Begin ticking. Idempotent — calling twice replaces the
        existing tick task with a fresh one.

        If ``kanban.enabled: false`` in user config, this no-ops with
        a single log line so an opt-out user doesn't pay the tick
        cost. Toggling enabled requires a daemon restart (we don't
        observe the flag on the hot path).
        """
        if not kanban_enabled():
            log.info("kanban: disabled via config (kanban.enabled=false)")
            return
        if self._tick_task and not self._tick_task.done():
            self._tick_task.cancel()
        self._loop = loop
        self._stopped.clear()
        self._tick_task = loop.create_task(self._run_forever())
        log.info(
            "kanban: dispatcher started (interval=%ds, max_concurrent=%d)",
            kanban_dispatch_interval_seconds(),
            kanban_max_concurrent_workers(),
        )

    async def stop(self) -> None:
        """Cancel the tick loop + all in-flight spawn tasks. Waits
        for them to drain (with a short grace window). Safe to call
        multiple times."""
        self._stopped.set()
        if self._tick_task and not self._tick_task.done():
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
        # Cancel + drain spawn tasks. We don't wait forever because
        # brain.spawn_aux may be mid-subprocess and need a few seconds
        # to clean up. Anything that exceeds the grace window logs.
        if self._in_flight:
            log.info(
                "kanban: cancelling %d in-flight spawn(s)",
                len(self._in_flight),
            )
            for task in list(self._in_flight):
                task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._in_flight, return_exceptions=True),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                log.warning(
                    "kanban: %d spawn(s) didn't finish within grace",
                    len(self._in_flight),
                )
        log.info("kanban: dispatcher stopped")

    # ─── tick loop ───────────────────────────────────────────────

    async def _run_forever(self) -> None:
        """Periodic tick loop. Catches and logs all exceptions so a
        bad tick doesn't stop the dispatcher."""
        while not self._stopped.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("kanban: tick raised (continuing)")
            interval = kanban_dispatch_interval_seconds()
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=interval,
                )
            except asyncio.TimeoutError:
                continue

    async def tick(self) -> DispatchTickResult:
        """One dispatcher tick. Public for tests + manual triggers
        (the dashboard ``/api/kanban/dispatch`` endpoint, future
        Phase 6)."""
        # SQLite calls are sync; run on a thread to avoid blocking
        # the event loop. spawn_fn schedules tasks on this same loop.
        loop = self._loop or asyncio.get_running_loop()

        def _spawn_fn(
            task: Task,
            lane: LaneSpec,
            claim_lock: str,
            run_id: int,
            max_runtime: int,
        ) -> asyncio.Task[None]:
            spawn_task = loop.create_task(
                self._spawn_worker(task, lane, claim_lock, run_id, max_runtime),
            )
            self._in_flight.add(spawn_task)
            spawn_task.add_done_callback(self._in_flight.discard)
            return spawn_task

        result = await asyncio.to_thread(
            dispatch_once,
            self._store,
            max_concurrent=kanban_max_concurrent_workers(),
            claim_ttl_seconds=kanban_claim_ttl_seconds(),
            default_max_runtime=kanban_default_max_runtime_seconds(),
            spawn_fn=_spawn_fn,
        )
        if (result.spawned or result.promoted or result.stale_released) and self._event_hook:
            try:
                await self._event_hook()
            except Exception:
                log.exception("kanban: event_hook raised (continuing)")
        return result

    # ─── spawn ───────────────────────────────────────────────────

    async def _spawn_worker(
        self,
        task: Task,
        lane: LaneSpec,
        claim_lock: str,
        run_id: int,
        max_runtime: int,
    ) -> None:
        """Spawn one worker via ``brain.spawn_aux``. Records the run
        outcome regardless of how the spawn returns.

        Outcome decision tree (matches ``.plans/kanban-research.md`` §6):

          * AuxResult with returncode == 0 AND task already moved out
            of ``in_progress`` (worker called kanban_complete or
            kanban_block via MCP) → trust the worker. Finalise run
            as ``done``.
          * AuxResult with returncode == 0 BUT task still in_progress
            → worker exited without declaring outcome. Finalise as
            ``gave_up``; release claim; bump consecutive_failures.
          * AuxResult with returncode != 0 → finalise as ``failed``;
            release claim; bump consecutive_failures.
          * BrainTimeoutError → finalise as ``timed_out``; bump
            consecutive_failures; auto-block if failure_limit hit.
          * BrainModelNotFoundError → finalise as ``spawn_failed``;
            auto-block with the suggested_fix from the exception.
          * Other BrainError → finalise as ``spawn_failed``; bump
            consecutive_failures.
        """
        # Heavy imports are deferred so unit tests can construct
        # KanbanController against BrainNull without touching the
        # real brain modules.
        from vexis_agent.core.brain.base import (
            BrainError,
            BrainModelNotFoundError,
            BrainTimeoutError,
        )

        log.info(
            "kanban: spawning worker for %s (lane=%s, run=%d, max_runtime=%ds)",
            task.id, lane.name, run_id, max_runtime,
        )
        prompt = build_worker_prompt(task, lane)
        env_overrides = {
            ENV_VAR_KANBAN: "1",
            ENV_VAR_KANBAN_TASK_ID: task.id,
            ENV_VAR_KANBAN_LANE: lane.name,
        }
        # cwd: task's workspace_path if set, else the daemon's
        # workspace. The kanban-worker MCP tools (Phase 4) need
        # the kanban DB to be reachable; the daemon-wide workspace
        # always has a stable path to it via ~/.vexis/kanban.db.
        cwd: Path | None = None
        if task.workspace_path:
            try:
                cwd = Path(task.workspace_path)
            except (TypeError, ValueError):
                cwd = None

        try:
            aux = await self._brain.spawn_aux(
                prompt,
                model_tier=lane.tier,
                timeout_seconds=float(max_runtime),
                env_overrides=env_overrides,
                allow_tools=True,
                cwd=cwd,
                subsystem="kanban_worker",
            )
        except BrainTimeoutError as exc:
            await self._on_spawn_timeout(task, run_id, str(exc))
            return
        except BrainModelNotFoundError as exc:
            await self._on_spawn_model_error(task, run_id, exc)
            return
        except BrainError as exc:
            await self._on_spawn_error(task, run_id, str(exc))
            return
        except asyncio.CancelledError:
            # Shutdown / /cancel — release the claim so the next
            # dispatcher tick can re-pick or so the user can re-assign.
            await asyncio.to_thread(
                self._finalize_cancelled, task.id, run_id, claim_lock,
            )
            raise

        # Spawn returned. Decide outcome based on returncode + task state.
        await asyncio.to_thread(
            self._finalize_normal_return, task.id, run_id, aux,
        )
        if self._event_hook:
            try:
                await self._event_hook()
            except Exception:
                log.exception("kanban: event_hook raised post-spawn")

    # ─── finalisers (sync, run via to_thread from spawn handlers) ─

    def _finalize_normal_return(
        self,
        task_id: str,
        run_id: int,
        aux: "AuxResultLike",
    ) -> None:
        # Re-read the task because the worker may have flipped status
        # via kanban_complete / kanban_block MCP calls.
        task = self._store.get_task(task_id)
        if task is None:
            log.warning(
                "kanban: task %s disappeared while worker was running",
                task_id,
            )
            return
        if aux.returncode == 0:
            if task.status in (STATUS_DONE, STATUS_BLOCKED):
                # Worker declared outcome via MCP tools — trust it.
                self._store.finalize_run(
                    run_id, outcome="completed",
                    summary=_truncate(aux.stdout, 4000),
                    new_status=RUN_STATUS_DONE,
                )
                # Reset consecutive_failures on success.
                self._store._conn.execute(
                    "UPDATE tasks SET consecutive_failures = 0 WHERE id = ?",
                    (task_id,),
                )
                log.info(
                    "kanban: worker for %s declared %s",
                    task_id, task.status,
                )
                return
            # Worker exited cleanly but didn't declare via MCP. Treat
            # as gave_up — release back to ready.
            self._store.finalize_run(
                run_id, outcome="gave_up",
                summary=_truncate(aux.stdout, 4000),
                error="worker exited without kanban_complete/kanban_block",
                new_status=RUN_STATUS_FAILED,
            )
            self._record_failure(
                task_id, "worker exited without declaring outcome",
            )
            return
        # Non-zero return code.
        self._store.finalize_run(
            run_id, outcome="failed",
            summary=_truncate(aux.stdout, 4000),
            error=_truncate(aux.stderr or "", 2000),
            new_status=RUN_STATUS_FAILED,
        )
        self._record_failure(
            task_id, f"worker exited with code {aux.returncode}",
        )

    def _finalize_cancelled(
        self, task_id: str, run_id: int, claim_lock: str,
    ) -> None:
        self._store.finalize_run(
            run_id, outcome="reclaimed",
            error="cancelled (controller shutdown or user /cancel)",
            new_status="released",
        )
        # Release the claim; do NOT bump failure counter (cancellation
        # is not the worker's fault).
        self._store.release_claim(task_id)

    async def _on_spawn_timeout(
        self, task: Task, run_id: int, message: str,
    ) -> None:
        log.warning("kanban: worker for %s timed out: %s", task.id, message)
        await asyncio.to_thread(
            self._finalize_timeout, task.id, run_id, message,
        )

    def _finalize_timeout(
        self, task_id: str, run_id: int, message: str,
    ) -> None:
        self._store.finalize_run(
            run_id, outcome="timed_out",
            error=_truncate(message, 2000),
            new_status=RUN_STATUS_TIMED_OUT,
        )
        self._store.append_event(
            task_id, EVENT_TIMED_OUT, {"message": message},
        )
        self._record_failure(task_id, f"timed out: {message}")

    async def _on_spawn_model_error(
        self, task: Task, run_id: int, exc: "BrainModelNotFoundError",
    ) -> None:
        await asyncio.to_thread(
            self._finalize_model_error, task.id, run_id,
            exc.subsystem, exc.model_id, exc.suggested_fix,
        )

    def _finalize_model_error(
        self,
        task_id: str,
        run_id: int,
        subsystem: str,
        model_id: str,
        suggested_fix: str,
    ) -> None:
        msg = (
            f"Model {model_id!r} rejected by brain for subsystem "
            f"{subsystem!r}. {suggested_fix}"
        )
        self._store.finalize_run(
            run_id, outcome="spawn_failed",
            error=_truncate(msg, 2000),
            new_status=RUN_STATUS_FAILED,
        )
        # Auto-block — the failure isn't transient, so don't retry.
        self._store.update_task(task_id, status=STATUS_BLOCKED)
        self._store.append_event(
            task_id, EVENT_BLOCKED,
            {"reason": "model_not_found", "suggested_fix": suggested_fix},
        )
        log.error("kanban: %s", msg)

    async def _on_spawn_error(
        self, task: Task, run_id: int, message: str,
    ) -> None:
        log.warning("kanban: worker for %s spawn failed: %s", task.id, message)
        await asyncio.to_thread(
            self._finalize_spawn_error, task.id, run_id, message,
        )

    def _finalize_spawn_error(
        self, task_id: str, run_id: int, message: str,
    ) -> None:
        self._store.finalize_run(
            run_id, outcome="spawn_failed",
            error=_truncate(message, 2000),
            new_status=RUN_STATUS_FAILED,
        )
        self._record_failure(task_id, f"spawn error: {message}")

    def _record_failure(self, task_id: str, error: str) -> None:
        """Bump consecutive_failures, release the claim, and auto-block
        if the failure limit is hit. Used by spawn-error and worker-
        exited-bad paths."""
        # Fetch the task to compute new counts under a transaction.
        task = self._store.get_task(task_id)
        if task is None:
            return
        new_count = task.consecutive_failures + 1
        # Per-task max_retries overrides global failure_limit.
        limit = (
            task.max_retries
            if task.max_retries is not None
            else kanban_failure_limit()
        )
        if new_count >= limit:
            # Auto-block — leave the run history visible.
            self._store._conn.execute(
                "UPDATE tasks SET status = ?, claim_lock = NULL, "
                "claim_expires = NULL, worker_pid = NULL, "
                "consecutive_failures = ?, last_failure_error = ? "
                "WHERE id = ?",
                (STATUS_BLOCKED, new_count, _truncate(error, 500), task_id),
            )
            self._store.append_event(
                task_id, EVENT_BLOCKED,
                {"reason": "failure_limit_reached", "error": error},
            )
            log.error(
                "kanban: task %s auto-blocked after %d consecutive failures",
                task_id, new_count,
            )
        else:
            # Release back to ready for retry.
            self._store._conn.execute(
                "UPDATE tasks SET status = 'ready', claim_lock = NULL, "
                "claim_expires = NULL, worker_pid = NULL, "
                "consecutive_failures = ?, last_failure_error = ? "
                "WHERE id = ?",
                (new_count, _truncate(error, 500), task_id),
            )
            self._store.append_event(
                task_id, EVENT_FAILED,
                {"error": error, "attempts": new_count},
            )


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _truncate(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


# ``AuxResultLike`` — a protocol-shape stub so the type checker accepts
# both the real ``AuxResult`` and BrainNull's canned shape without us
# having to import the real type at module level (deferred import keeps
# the dispatcher importable in test fixtures that don't construct a
# real brain).
class AuxResultLike:  # pragma: no cover — duck type only
    stdout: str
    stderr: str
    returncode: int


__all__ = [
    "DispatchTickResult",
    "KanbanController",
    "build_worker_prompt",
    "dispatch_once",
]
