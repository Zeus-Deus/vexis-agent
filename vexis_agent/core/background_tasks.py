"""Registry and lifecycle management for background Claude Code tasks.

Background tasks are long-running ``claude -p`` sessions Vexis spawns
to do work the user doesn't want to wait for. They live at the daemon
level (not the conversation session level), get human-readable names,
and notify the originating Telegram chat on completion.

Each task gets its own log file at
``$XDG_STATE_HOME/vexis-agent/background-logs/<name>.log`` containing the
``--output-format stream-json --verbose`` events the spawned subprocess
emits. ``tail_log`` is what the foreground brain reads to give the user
a "how's that going" status update.

Limits:
  - At most ``MAX_CONCURRENT`` tasks may be RUNNING at once (default 3).
  - Names are kebab-case, 3-30 chars, validated.
  - Daemon restart kills running tasks. Their last-known state is
    persisted only so the next daemon can warn the user.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from vexis_agent.core.paths import state_dir
from vexis_agent.core.sandbox_runner import (
    SandboxRunner,
    SandboxVerifyResult,
    should_sandbox,
)

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,29}$")
DEFAULT_MAX_CONCURRENT = 3
KILL_GRACE_SECONDS = 2.0
RECENTLY_FINISHED_WINDOW_SECONDS = 60 * 60  # 1 hour
LOG_DIR_NAME = "background-logs"
STATE_FILENAME = "background-tasks.json"

NotifyFn = Callable[[int, str], Awaitable[None]]
SystemPromptFn = Callable[[], str]


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BackgroundTaskError(Exception):
    """Base for handled background-task failures."""


class BackgroundTaskLimitReached(BackgroundTaskError):
    """Raised when MAX_CONCURRENT is already running."""


class NameAlreadyInUse(BackgroundTaskError):
    """Raised when the requested name is in use by a running task."""


class TaskNotFound(BackgroundTaskError):
    """Raised when a name is referenced that the registry doesn't know."""


class InvalidTaskName(BackgroundTaskError):
    """Raised when a name fails NAME_RE validation."""


@dataclass
class BackgroundTask:
    name: str
    chat_id: int
    prompt: str
    spawned_at: datetime
    log_path: Path
    status: TaskStatus = TaskStatus.PENDING
    pid: int | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    # Cancellation observed in the registry — when a /cancel races a
    # running task, we mark this so the watcher can suppress the
    # success/failure notification it would otherwise fire.
    cancelled_marker: bool = field(default=False, repr=False)
    # Build-and-test loop wiring. ``sandbox_enabled`` records the final
    # decision (heuristic-default OR explicit caller choice) at spawn
    # time so the watcher can match start↔stop without re-running the
    # heuristic. ``verify_checks_path`` is the path to a YAML file the
    # agent dropped during the task; verify runs once the agent exits.
    sandbox_enabled: bool = field(default=False)
    verify_checks_path: str | None = field(default=None)
    verify_summary: str | None = field(default=None)

    def to_summary(self) -> dict:
        return {
            "name": self.name,
            "chat_id": self.chat_id,
            "status": self.status.value,
            "spawned_at": self.spawned_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "exit_code": self.exit_code,
            "pid": self.pid,
            "log_path": str(self.log_path),
            "sandbox_enabled": self.sandbox_enabled,
            "verify_checks_path": self.verify_checks_path,
            "verify_summary": self.verify_summary,
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BackgroundTasks:
    """Registry of background ``claude -p`` subprocesses.

    The class is fully usable without a notify callback (it logs and
    drops the message); the daemon wires the real callback in just
    after Telegram has been initialised.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        system_prompt_provider: SystemPromptFn,
        notify: NotifyFn | None = None,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        log_dir: Path | None = None,
        state_file: Path | None = None,
        sandbox_runner: SandboxRunner | None = None,
    ) -> None:
        self._workspace = workspace
        self._system_prompt_provider = system_prompt_provider
        self._notify = notify
        self._max_concurrent = max_concurrent
        self._log_dir = log_dir or (state_dir() / LOG_DIR_NAME)
        self._state_file = state_file or (state_dir() / STATE_FILENAME)
        self._tasks: dict[str, BackgroundTask] = {}
        self._watchers: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._log_dir.mkdir(parents=True, exist_ok=True)
        # ``None`` falls back to the real CLI runner; tests inject a
        # FakeSandboxRunner. We never construct one *lazily* — that would
        # let the heuristic flip "sandbox=True" on a host that has no
        # Docker, silently producing a task that can't start. The daemon
        # is responsible for handing in a runner only when the host
        # actually has docker + vexis-sandbox available.
        self._sandbox = sandbox_runner

    # ----- public configuration -----

    def set_notify(self, notify: NotifyFn) -> None:
        """Bind the notify callback after Telegram has finished init."""
        self._notify = notify

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    # ----- name validation -----

    @staticmethod
    def validate_name(name: str) -> None:
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise InvalidTaskName(
                f"Invalid task name '{name}'. Use 3-30 chars: lowercase "
                "letters, digits, hyphens; must start with a letter."
            )

    # ----- introspection -----

    def running_count(self) -> int:
        # PENDING counts toward the cap so two concurrent spawn() calls
        # racing on the limit can't both squeeze past — the first one
        # has already inserted a PENDING entry under the lock by the
        # time the second's check runs.
        return sum(
            1
            for t in self._tasks.values()
            if t.status in (TaskStatus.RUNNING, TaskStatus.PENDING)
        )

    def list_running_names(self) -> list[str]:
        return [
            t.name
            for t in self._tasks.values()
            if t.status in (TaskStatus.RUNNING, TaskStatus.PENDING)
        ]

    async def get(self, name: str) -> BackgroundTask | None:
        async with self._lock:
            return self._tasks.get(name)

    async def status_summary(self) -> list[dict]:
        """All known tasks (running + recently-finished within the window)."""
        async with self._lock:
            now = _utcnow()
            results: list[dict] = []
            for task in self._tasks.values():
                if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                    results.append(task.to_summary())
                    continue
                if task.finished_at is None:
                    continue
                age = (now - task.finished_at).total_seconds()
                if age <= RECENTLY_FINISHED_WINDOW_SECONDS:
                    results.append(task.to_summary())
            return results

    async def tail_log(self, name: str, n_lines: int = 50) -> str:
        task = await self.get(name)
        if task is None:
            raise TaskNotFound(f"No background task named '{name}'.")
        try:
            data = task.log_path.read_bytes()
        except FileNotFoundError:
            return ""
        text = data.decode(errors="replace")
        lines = text.splitlines()
        return "\n".join(lines[-n_lines:])

    # ----- spawn / cancel -----

    async def spawn(
        self,
        chat_id: int,
        name: str,
        prompt: str,
        *,
        sandbox: bool | None = None,
        verify_checks: str | None = None,
    ) -> BackgroundTask:
        """Spawn a new background task. Raises on validation/limit/conflict.

        ``sandbox`` controls whether the task runs against a Docker
        sandbox (see :mod:`vexis_agent.core.sandbox_runner`). ``None``
        defers to the per-prompt heuristic — build-or-test prompts opt
        in, pure research prompts opt out. The decision is recorded on
        the task so the watcher can match start↔stop. If sandbox routing
        is requested but no runner is wired in, we fall back to direct
        execution and warn — the caller's task still runs.

        ``verify_checks`` is the path (inside the workspace) to a YAML
        check spec. When set AND sandbox is enabled, the watcher invokes
        ``vexis-verify run <name>`` after the agent process exits and
        flips the task to FAILED if any check fails.
        """
        self.validate_name(name)
        # Final sandbox decision: explicit > heuristic > availability check.
        if sandbox is None:
            sandbox = should_sandbox(prompt)
        if sandbox and self._sandbox is None:
            log.warning(
                "Sandbox requested for task '%s' but no SandboxRunner is "
                "wired in; falling back to direct execution.",
                name,
            )
            sandbox = False
        if verify_checks and not sandbox:
            # Verify only makes sense inside a sandbox; if the caller
            # asked for one without the other, we keep them honest.
            log.warning(
                "Task '%s' specified --verify but sandbox is off; "
                "ignoring verify path.",
                name,
            )
            verify_checks = None
        async with self._lock:
            running = self.running_count()
            if running >= self._max_concurrent:
                names = ", ".join(self.list_running_names())
                raise BackgroundTaskLimitReached(
                    f"Already running {running} background tasks ({names}). "
                    "Cancel one or wait for it to finish."
                )
            existing = self._tasks.get(name)
            if existing is not None and existing.status == TaskStatus.RUNNING:
                raise NameAlreadyInUse(
                    f"A background task named '{name}' is already running."
                )
            task = BackgroundTask(
                name=name,
                chat_id=chat_id,
                prompt=prompt,
                spawned_at=_utcnow(),
                log_path=self._log_dir / f"{name}.log",
                status=TaskStatus.PENDING,
                sandbox_enabled=sandbox,
                verify_checks_path=verify_checks,
            )
            # Replace any stale finished record under the same name so the
            # new spawn owns the slot. The log file is appended to, not
            # truncated, so prior runs remain visible until the user
            # cleans them up.
            self._tasks[name] = task

        try:
            proc = await self._launch(task)
        except Exception:
            async with self._lock:
                # Pull the placeholder so the user can retry the same name.
                if self._tasks.get(name) is task and task.status == TaskStatus.PENDING:
                    del self._tasks[name]
            log.exception("Spawn failed for background task '%s'", name)
            raise

        async with self._lock:
            task.pid = proc.pid
            task.status = TaskStatus.RUNNING
            self._persist()

        watcher = asyncio.create_task(
            self._watch(task, proc), name=f"vexis-bg-watch-{name}"
        )
        self._watchers[name] = watcher
        log.info(
            "Spawned background task '%s' pid=%d chat=%d",
            name,
            proc.pid,
            chat_id,
        )
        return task

    async def cancel(self, name: str) -> bool:
        """Cancel a named running task. Returns False if it's not running."""
        async with self._lock:
            task = self._tasks.get(name)
            if task is None:
                raise TaskNotFound(f"No background task named '{name}'.")
            if task.status != TaskStatus.RUNNING or task.pid is None:
                return False
            task.status = TaskStatus.CANCELLED
            task.cancelled_marker = True
            pid = task.pid
            self._persist()
        await _kill_with_escalation(pid, KILL_GRACE_SECONDS)
        return True

    # ----- spawn helpers -----

    async def _launch(self, task: BackgroundTask) -> asyncio.subprocess.Process:
        # Sandbox boot — happens *before* we kick off claude -p so the
        # agent's first turn already sees a running sandbox. A failure
        # here surfaces to the caller (and bubbles to spawn() which
        # pulls the placeholder task), so the user sees the docker error
        # at /bg spawn time rather than a silently broken task.
        if task.sandbox_enabled and self._sandbox is not None:
            start_res = await self._sandbox.start(task.name)
            if not start_res.ok:
                raise BackgroundTaskError(
                    f"Sandbox start failed for '{task.name}': "
                    f"{start_res.error or 'unknown error'}"
                )

        system_prompt = self._system_prompt_provider()
        if task.sandbox_enabled:
            system_prompt = system_prompt + "\n\n" + _sandbox_prompt_addendum(task)

        argv = [
            "claude",
            "-p",
            task.prompt,
            "--output-format",
            "stream-json",
            "--verbose",  # required by stream-json
            "--append-system-prompt",
            system_prompt,
            "--permission-mode",
            "bypassPermissions",
        ]
        env = {**os.environ, "VEXIS_CHAT_ID": str(task.chat_id)}
        if task.sandbox_enabled:
            # Let the agent's tools (`vexis-sandbox exec`, future
            # `vexis-display`/`vexis-ui`) discover the right task-id
            # without having to parse it from the system prompt.
            env["VEXIS_SANDBOX_TASK_ID"] = task.name
        log_fh = task.log_path.open("ab", buffering=0)
        try:
            log_fh.write(
                f"# vexis-bg task '{task.name}' spawned "
                f"{task.spawned_at.isoformat()} chat={task.chat_id} "
                f"sandbox={task.sandbox_enabled} "
                f"verify={task.verify_checks_path!r}\n"
                f"# prompt: {task.prompt!r}\n".encode()
            )
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self._workspace),
                stdout=log_fh,
                stderr=log_fh,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
        finally:
            # The subprocess holds its own dup of the fd; we can release ours.
            log_fh.close()
        return proc

    async def _watch(
        self,
        task: BackgroundTask,
        proc: asyncio.subprocess.Process,
    ) -> None:
        try:
            rc = await proc.wait()
        except asyncio.CancelledError:
            log.info("Watcher for '%s' cancelled (daemon shutdown)", task.name)
            raise

        # Post-claim verification. Only runs if the agent exited cleanly
        # AND the caller wired a checks path AND we have a sandbox runner.
        # On verify failure we flip the task to FAILED *before* taking
        # the lock-protected status update so the user-visible status is
        # consistent with the verify_summary that lands in the notification.
        verify_result: SandboxVerifyResult | None = None
        if (
            rc == 0
            and task.sandbox_enabled
            and task.verify_checks_path
            and self._sandbox is not None
            and not task.cancelled_marker
        ):
            try:
                verify_result = await self._sandbox.verify(
                    task.name, task.verify_checks_path
                )
            except Exception:
                # The runner already maps subprocess errors to ok=False
                # results; if a bug still escapes we don't want to
                # poison the watcher.
                log.exception("Verify failed for task '%s'", task.name)
                verify_result = None

        async with self._lock:
            task.exit_code = rc
            task.finished_at = _utcnow()
            already_cancelled = task.cancelled_marker
            if not already_cancelled:
                if verify_result is not None:
                    task.verify_summary = verify_result.summary
                    if verify_result.all_passed and rc == 0:
                        task.status = TaskStatus.FINISHED
                    else:
                        # Verify failure dominates exit code: a clean
                        # claude exit that doesn't satisfy the checks is
                        # still a failure for the user.
                        task.status = TaskStatus.FAILED
                else:
                    task.status = TaskStatus.FINISHED if rc == 0 else TaskStatus.FAILED
            self._persist()

        # Sandbox teardown — always, even on cancel/failure, so we don't
        # leak containers. Best-effort; we log on error but don't fail.
        if task.sandbox_enabled and self._sandbox is not None:
            try:
                await self._sandbox.stop(task.name)
            except Exception:
                log.exception("Sandbox stop failed for task '%s'", task.name)

        try:
            if already_cancelled:
                return
            msg = _completion_message(task, rc, verify_result)
            await self._maybe_notify(task.chat_id, msg)
        finally:
            self._watchers.pop(task.name, None)

    async def _maybe_notify(self, chat_id: int, text: str) -> None:
        if self._notify is None:
            log.warning(
                "notify callback not set; dropping message for chat %d: %s",
                chat_id,
                text,
            )
            return
        try:
            await self._notify(chat_id, text)
        except Exception:
            log.exception("Notification to chat %d failed", chat_id)

    # ----- restart recovery -----

    async def detect_lost_from_previous_run(self) -> list[dict]:
        """Read the persisted state from the previous daemon, find tasks
        whose PID no longer exists, and return ``{name, chat_id}`` for each.

        The state file is consumed (deleted) afterwards so the warning
        only fires once per restart.
        """
        path = self._state_file
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            log.warning("Background tasks state file unreadable; ignoring")
            try:
                path.unlink()
            except OSError:
                pass
            return []
        lost: list[dict] = []
        for entry in payload.get("tasks", []):
            if entry.get("status") != TaskStatus.RUNNING.value:
                continue
            pid = entry.get("pid")
            chat_id = entry.get("chat_id")
            name = entry.get("name")
            if not isinstance(pid, int) or not isinstance(chat_id, int):
                continue
            if not isinstance(name, str):
                continue
            if not _pid_alive(pid):
                lost.append({"name": name, "chat_id": chat_id})
        try:
            path.unlink()
        except OSError:
            log.debug("could not remove %s after restart check", path)
        return lost

    # ----- shutdown -----

    async def shutdown(self) -> None:
        """Kill running subprocesses and tear down watchers.

        Spec: daemon restart kills background tasks (acknowledged
        limitation). Because we spawn with ``start_new_session=True``
        the subprocesses would otherwise outlive the daemon as
        orphans, so we actively SIGTERM/SIGKILL each process group.
        The watchers are then cancelled so we don't leak Tasks. Any
        sandbox containers attached to live tasks are also stopped —
        otherwise we'd leak a Docker container per daemon restart.
        """
        async with self._lock:
            running_tasks = [
                t
                for t in self._tasks.values()
                if t.status == TaskStatus.RUNNING and t.pid is not None
            ]
        for task in running_tasks:
            try:
                await _kill_with_escalation(task.pid, KILL_GRACE_SECONDS)
            except Exception:
                log.exception("shutdown kill failed for pid=%s", task.pid)
            if task.sandbox_enabled and self._sandbox is not None:
                try:
                    await self._sandbox.stop(task.name)
                except Exception:
                    log.exception(
                        "sandbox stop on shutdown failed for task '%s'", task.name
                    )
        watchers = list(self._watchers.values())
        for w in watchers:
            w.cancel()
        for w in watchers:
            try:
                await w
            except (asyncio.CancelledError, Exception):
                pass
        self._watchers.clear()

    # ----- state persistence (call under self._lock) -----

    def _persist(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"tasks": [t.to_summary() for t in self._tasks.values()]}
            tmp = self._state_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2))
            tmp.replace(self._state_file)
        except OSError:
            log.exception("Failed to persist background task state")


def _sandbox_prompt_addendum(task: BackgroundTask) -> str:
    """Brief system-prompt insert telling the agent which sandbox it's
    running in and how to use it. Kept terse on purpose — long system
    prompts blow the model's first-turn token budget."""
    lines = [
        "## Build-and-test sandbox",
        "",
        f"You are running inside vexis-bg task '{task.name}' with a "
        f"dedicated Docker sandbox. All filesystem mutations, builds, "
        f"and test runs must go through:",
        "",
        f"    vexis-sandbox exec {task.name} -- <command...>",
        "",
        "The container has /workspace mounted from the host workspace "
        "and /scratch for ephemeral outputs. State persists across exec "
        "calls within this task.",
    ]
    if task.verify_checks_path:
        lines += [
            "",
            f"When you believe the task is done, ensure the YAML check "
            f"spec at '{task.verify_checks_path}' covers the acceptance "
            f"criteria — the daemon runs `vexis-verify run {task.name} "
            f"--checks {task.verify_checks_path}` after you exit, and "
            f"any failing check flips this task to FAILED.",
        ]
    return "\n".join(lines)


def _completion_message(
    task: BackgroundTask,
    rc: int,
    verify_result: SandboxVerifyResult | None,
) -> str:
    """Compose the user-facing finished-message. Mirrors the original
    success/failure phrasing so existing Telegram-side regex / hint
    code doesn't have to change, but appends a verify summary when
    relevant so the user knows whether checks passed."""
    if rc == 0 and (verify_result is None or verify_result.all_passed):
        base = f"✅ Background task `{task.name}` finished."
        if verify_result is not None:
            base += f" Checks: {verify_result.summary}."
        return base + " Want details?"
    if verify_result is not None and not verify_result.all_passed:
        return (
            f"❌ Background task `{task.name}` failed at verify: "
            f"{verify_result.summary}. Want me to look at the log?"
        )
    return (
        f"❌ Background task `{task.name}` failed (exit {rc}). "
        "Want me to look at the log?"
    )


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but isn't ours; treat as dead since we can't
        # talk to it anyway.
        return False
    return True


async def _kill_with_escalation(pid: int, grace: float) -> None:
    """SIGTERM the process group, then SIGKILL after `grace` seconds."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        await asyncio.sleep(0.05)
    log.info("background task pid=%d ignored SIGTERM, escalating to SIGKILL", pid)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
