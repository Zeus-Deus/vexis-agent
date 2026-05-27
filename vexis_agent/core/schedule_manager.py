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
import json
import logging
import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from vexis_agent.core.paths import schedules_scripts_dir
from vexis_agent.core.running_tasks import RunningTasks


# Signature of a transport-provided dispatch callback. Async, kwargs-only.
# When set on the manager, scheduled fires route through this instead of
# raw ``running_tasks.enqueue`` — the transport's implementation goes
# through ``claim() ? drain : enqueue``, which is what guarantees a drain
# loop actually runs the synthetic prompt. Without this hop, fires at
# idle wall-clock time (the 2:30 AM case) strand in the FIFO until a real
# user message wakes a fresh claim — the bug fixed in this commit.
DispatchFn = Callable[..., Awaitable[bool]]
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


# ──────────────────────────────────────────────────────────────────
# Pre-run script (Issue #12) — wake-gate constants
# ──────────────────────────────────────────────────────────────────

# Sentinel key on the script's last stdout line. ``{"wakeAgent": false}``
# tells the manager to skip the brain turn entirely; any other value
# (``true``, missing, invalid JSON, empty stdout) means "go ahead and
# wake the brain". Default-to-wake is deliberate — a typo in a user's
# script must not silently disable their monitor.
_WAKE_GATE_KEY = "wakeAgent"

# Markers wrapping the script's stdout when we prepend it to the
# schedule prompt. Distinct from CURATOR_REVIEW_PROMPT_PREFIX /
# SUMMARY_PREFIX / KANBAN_WORKER_PREFIX so the content-prefix
# recursion guard (CLAUDE.md Invariants) does NOT catch these as
# aux-fork transcripts — a scheduled fire whose prompt was enriched
# by a script is still a foreground user-shaped turn the curator
# may legitimately review for lessons.
_SCRIPT_OUTPUT_OPEN = "[script output]"
_SCRIPT_OUTPUT_CLOSE = "[end script output]"


class ScriptExecutionError(Exception):
    """Pre-run script failed in a way the manager treats as ``skip the
    brain turn``. Carries a short reason for the schedule's
    ``last_error`` field.

    Subclassed exceptions distinguish the failure modes but the manager
    treats them uniformly: skip the brain, advance next_fire_at, record
    the error in ``last_error``. We do NOT count script failures
    against ``consecutive_errors`` because the user's script breaking
    is a different signal from the brain failing — auto-pausing a
    legitimate schedule because a buggy gate keeps timing out would
    be hostile.
    """


class ScriptPathError(ScriptExecutionError):
    """Script path resolved outside ``~/.vexis/scripts/`` — rejected
    without running anything. Defense against ``--script ../etc/foo``
    and symlink-out shenanigans."""


class ScriptTimeoutError(ScriptExecutionError):
    """Script exceeded ``script_timeout_seconds``. Subprocess killed,
    brain skipped. A hung script must not pin an LLM turn open."""


class ScriptGatedError(ScriptExecutionError):
    """Sentinel — script's last stdout line was ``{"wakeAgent": false}``.
    Brain skipped on purpose; this is the happy-path of the wake gate
    (the whole point of the feature). Logged at INFO, not ERROR."""


def _resolve_script_path(script: str) -> Path:
    """Resolve ``script`` against ``~/.vexis/scripts/`` and assert it
    stays inside that directory.

    ``script`` is a name (``check_mail.sh``) or a relative path
    (``email/check_mail.sh``). Absolute paths, ``..`` traversal, and
    symlinks pointing outside are all rejected. ``resolve()`` follows
    symlinks; ``is_relative_to`` is the post-resolution containment
    check — combined this is the standard chroot-style guard.

    Raises :class:`ScriptPathError` if the script is missing or escapes
    the scripts dir.
    """
    base = schedules_scripts_dir().resolve()
    raw = script.strip()
    if not raw:
        raise ScriptPathError("script path is empty")

    # Absolute paths are rejected outright — even if they'd happen to
    # land inside the scripts dir (the user passed the full
    # ~/.vexis/scripts/foo.sh form), forcing the name-relative shape
    # keeps schedule definitions portable across daemon hosts where
    # $HOME might differ.
    if os.path.isabs(raw):
        raise ScriptPathError(
            f"script path must be relative to ~/.vexis/scripts/, got {raw!r}"
        )

    candidate = (base / raw).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ScriptPathError(
            f"script {raw!r} resolves outside ~/.vexis/scripts/ "
            f"({candidate}) — refusing to execute"
        ) from exc

    if not candidate.exists():
        raise ScriptPathError(
            f"script {raw!r} not found under ~/.vexis/scripts/"
        )
    if not candidate.is_file():
        raise ScriptPathError(
            f"script {raw!r} is not a regular file"
        )
    return candidate


def _build_script_command(path: Path) -> list[str]:
    """Pick interpreter by extension. ``.sh`` → bash, ``.py`` → python.

    Anything else falls back to executing the file directly (relying
    on the user's shebang + executable bit). Keeping the interpreter
    map small avoids reinventing a runner registry — the user can
    always write a ``.sh`` wrapper that calls something exotic.
    """
    suffix = path.suffix.lower()
    if suffix == ".sh":
        return ["bash", str(path)]
    if suffix == ".py":
        # ``sys.executable`` would pin the daemon's python; the
        # user's script may want a different env. Use ``python3`` so
        # the system PATH resolution wins.
        return ["python3", str(path)]
    return [str(path)]


def _build_script_env(
    *, schedule_id: str, schedule_name: str | None, tick_ts: datetime,
) -> dict[str, str]:
    """Build the curated env dict passed to the subprocess.

    Deliberately NOT passing the daemon's full ``os.environ`` —
    schedules' scripts run as the same uid as vexis but the cost of
    leaking ``TELEGRAM_BOT_TOKEN`` / ``ANTHROPIC_API_KEY`` to a buggy
    script that ``env | curl ...`` is too high. Pass only what a
    typical monitor script actually needs.
    """
    env: dict[str, str] = {
        "PATH": os.environ.get(
            "PATH", "/usr/local/bin:/usr/bin:/bin"
        ),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "VEXIS_SCHEDULE_ID": schedule_id,
        "VEXIS_SCHEDULE_NAME": schedule_name or "",
        "VEXIS_SCHEDULE_TICK_TS": tick_ts.astimezone(timezone.utc).isoformat(),
    }
    # LANG / LC_ALL pass through if set — without them many CLIs
    # default to ASCII which mangles unicode in stdout.
    for k in ("LANG", "LC_ALL", "LC_CTYPE"):
        v = os.environ.get(k)
        if v:
            env[k] = v
    return env


def _parse_wake_gate(stdout: str) -> tuple[bool, str]:
    """Inspect the last non-empty line of ``stdout`` for the wake-gate
    sentinel. Returns ``(wake, gate_line_text)``.

    Contract (matches Hermes upstream + issue spec):

      * Last non-empty line parses as JSON dict with ``wakeAgent: false``
        → ``(False, <that line>)``. SKIP.
      * Last non-empty line parses as JSON dict with ``wakeAgent: true``
        → ``(True, <that line>)``. WAKE. Drop the gate line when
        prepending so the brain doesn't see the literal JSON.
      * Last line is not JSON, not a dict, or missing ``wakeAgent``
        → ``(True, "")``. WAKE. The whole stdout is prepended.
      * Empty stdout → ``(True, "")``. WAKE with empty preamble.

    The empty-string gate signals to the caller "no gate line was
    present" so it knows whether to strip the last line from the
    prepended output.
    """
    if not stdout or not stdout.strip():
        return True, ""
    # Walk lines in reverse to find the last non-empty one. The
    # script may have trailing newlines or blank lines (some
    # subprocesses append a final newline; some don't).
    last_line = ""
    for raw_line in reversed(stdout.splitlines()):
        line = raw_line.strip()
        if line:
            last_line = line
            break
    if not last_line:
        return True, ""
    try:
        parsed = json.loads(last_line)
    except (json.JSONDecodeError, ValueError):
        return True, ""
    if not isinstance(parsed, dict):
        return True, ""
    if _WAKE_GATE_KEY not in parsed:
        return True, ""
    return bool(parsed.get(_WAKE_GATE_KEY)), last_line


def _strip_gate_line(stdout: str, gate_line: str) -> str:
    """Remove the trailing gate line from ``stdout`` if present.

    The script's ``echo '{"wakeAgent": true}'`` IS the gate, not
    payload — the brain should not see that literal JSON line in its
    prepended context (it'd be noise that could confuse the model
    about what to do). Strip it but preserve everything above.
    """
    if not gate_line or not stdout:
        return stdout
    lines = stdout.splitlines()
    # Walk from the end, drop the first non-blank match.
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == gate_line:
            del lines[i]
            break
    # Trailing blank lines were noise from the script; collapse them.
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def run_pre_run_script(
    schedule_id: str,
    *,
    script: str,
    timeout_seconds: float,
    schedule_name: str | None,
    tick_ts: datetime,
) -> tuple[str, bool]:
    """Execute the schedule's pre-run script and return ``(stdout, wake)``.

    The blocking subprocess is called synchronously — the schedule
    manager already runs on a daemon thread so blocking on a 120s
    timeout there is fine, and it keeps the wiring simple (no
    ``asyncio.create_subprocess_exec`` hop, no thread/event-loop
    handoff).

    ``stdout`` is the captured script output with the gate line (if
    any) stripped — ready to prepend to the prompt. ``wake`` is
    ``False`` if the gate explicitly vetoed the brain turn.

    Raises :class:`ScriptPathError` on path-confinement failure (the
    script never runs in this case), :class:`ScriptTimeoutError` if
    the script ran but exceeded the timeout, or
    :class:`ScriptExecutionError` for non-zero exit / subprocess
    failure. Non-zero exit defaults to WAKE+log so a script that
    crashes still fires the brain — silent skip on crash would mask
    monitoring outages.
    """
    path = _resolve_script_path(script)
    cmd = _build_script_command(path)
    env = _build_script_env(
        schedule_id=schedule_id,
        schedule_name=schedule_name,
        tick_ts=tick_ts,
    )

    log.info(
        "schedule %s: running pre-run script %s (timeout=%.0fs)",
        schedule_id, path, timeout_seconds,
    )

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            cwd=str(path.parent),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # ``run()`` already killed the child process before raising.
        # Preserve any captured stdout for the log so the user can see
        # what the script printed before hanging.
        partial = ""
        if exc.stdout:
            partial = exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", "replace")
        log.warning(
            "schedule %s: pre-run script timed out after %.0fs; brain SKIPPED. "
            "Partial stdout: %s",
            schedule_id, timeout_seconds, partial[:500],
        )
        raise ScriptTimeoutError(
            f"script timed out after {timeout_seconds:.0f}s"
        ) from exc
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise ScriptExecutionError(
            f"script execution failed: {exc}"
        ) from exc

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    rc = completed.returncode

    if stderr:
        # Stderr always goes to the daemon log so the user can debug.
        log.info(
            "schedule %s: pre-run script stderr: %s",
            schedule_id, stderr.strip()[:1000],
        )

    if rc != 0:
        # Non-zero exit → log and WAKE the brain anyway. A monitor
        # script that errors out is a real signal the user wants to
        # know about (the monitored system might be broken). Skipping
        # on rc!=0 would silently mask outages.
        log.warning(
            "schedule %s: pre-run script exited %d; waking brain with "
            "stderr included so the user can see the failure",
            schedule_id, rc,
        )
        combined = stdout
        if stderr:
            combined = (
                f"{stdout}\n[script exited {rc}; stderr:]\n{stderr}".lstrip(
                    "\n"
                )
            )
        return combined, True

    wake, gate_line = _parse_wake_gate(stdout)
    if not wake:
        log.info(
            "schedule %s: pre-run script gated wake (wakeAgent: false); "
            "brain SKIPPED",
            schedule_id,
        )
        raise ScriptGatedError("wakeAgent: false")

    cleaned = _strip_gate_line(stdout, gate_line)
    return cleaned, True


def prepend_script_output(prompt: str, script_stdout: str) -> str:
    """Wrap and prepend the script's stdout to the schedule's prompt.

    Format mirrors the issue spec:

        [script output]
        <stdout>
        [end script output]

        <original prompt>

    Blank stdout returns the original prompt unchanged (no point in
    showing the brain an empty banner). This lets the gate "wake but
    say nothing" pattern work cleanly — e.g. the script does a heavy
    check, the only verdict is "yes wake" with no payload.
    """
    if not script_stdout or not script_stdout.strip():
        return prompt
    return (
        f"{_SCRIPT_OUTPUT_OPEN}\n{script_stdout.rstrip()}\n"
        f"{_SCRIPT_OUTPUT_CLOSE}\n\n{prompt}"
    )


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
        dispatch_fn: DispatchFn | None = None,
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

        # Optional transport-provided dispatch callback (see DispatchFn
        # docstring above). Late-bindable via ``set_dispatch_fn`` because
        # the Telegram transport is constructed AFTER ScheduleManager in
        # main.py — by the time the first tick runs we want this wired.
        self._dispatch_fn: DispatchFn | None = dispatch_fn

    def set_dispatch_fn(self, fn: DispatchFn | None) -> None:
        """Late-bind the dispatch callback after construction.

        Called by ``main.py`` once the Telegram transport exists, since
        the transport's ``dispatch_scheduled_fire`` method is what makes
        scheduled fires go through the claim/drain protocol instead of
        stranding in the FIFO. Pass ``None`` to revert to the legacy
        raw-enqueue path (tests, alternate wirings).
        """
        self._dispatch_fn = fn

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

        # Step 1.5 (Issue #12): pre-run script + wake gate.
        # When ``schedule.script`` is set, run it BEFORE deciding to
        # spawn the brain turn. The wake-gate sentinel
        # ``{"wakeAgent": false}`` on the script's last stdout line
        # vetoes the brain — the schedule still counts as "fired" for
        # bookkeeping (last_fire_at advances, last_status="ok") but no
        # expensive LLM turn happens. Other script failures (timeout,
        # path-traversal) skip the brain but record an error in
        # ``last_error`` so the dashboard surfaces what went wrong.
        prompt_text = schedule.prompt
        if schedule.script:
            try:
                script_stdout, _wake = run_pre_run_script(
                    schedule.id,
                    script=schedule.script,
                    timeout_seconds=float(schedule.script_timeout_seconds),
                    schedule_name=schedule.name,
                    tick_ts=now,
                )
            except ScriptGatedError:
                # Happy path of the wake gate — script returned
                # ``wakeAgent: false``. Mark the fire as gated (ok,
                # not error) so the user can see in the dashboard /
                # `vexis-agent schedule show` how often the gate fires.
                self._record_gated_fire(schedule.id, fired_at=now)
                return False
            except ScriptPathError as exc:
                log.error(
                    "schedule %s: script path rejected (%s); brain SKIPPED",
                    schedule.id, exc,
                )
                self._record_script_failure(
                    schedule.id, fired_at=now, reason=f"script: {exc}",
                )
                return False
            except ScriptTimeoutError as exc:
                self._record_script_failure(
                    schedule.id, fired_at=now, reason=f"script: {exc}",
                )
                return False
            except ScriptExecutionError as exc:
                # Generic execution failure (subprocess crashed, file
                # not executable, etc.). Still skip the brain — we
                # don't trust unrecognised state.
                log.warning(
                    "schedule %s: script execution failed (%s); brain SKIPPED",
                    schedule.id, exc,
                )
                self._record_script_failure(
                    schedule.id, fired_at=now, reason=f"script: {exc}",
                )
                return False

            prompt_text = prepend_script_output(schedule.prompt, script_stdout)

        # Step 2: enqueue the synthetic user message.
        # Done from the daemon thread via run_coroutine_threadsafe;
        # the asyncio loop owns RunningTasks. ``schedule.id`` rides
        # along so the drain can call back ``report_fire_outcome``
        # with the real brain result.
        success = self._enqueue_synthetic(
            chat_id=schedule.chat_id,
            text=prompt_text,
            schedule_id=schedule.id,
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

    def _enqueue_synthetic(
        self, *, chat_id: int, text: str, schedule_id: str | None = None,
    ) -> bool:
        """Schedule the fire onto the asyncio loop. Returns True on
        success.

        Two paths:

        * ``self._dispatch_fn`` is set (production wiring) — call it.
          The transport's implementation goes through
          ``_dispatch_to_brain`` which does ``claim() ? drain : enqueue``,
          guaranteeing a drain loop actually consumes the prompt. This
          is the correct path for scheduled fires at idle wall-clock
          time (2:30 AM): without it, the prompt would land in the FIFO
          with no drain owner and sit there until a real user message
          woke a fresh claim. That was the v0.4.0 bug this branch fixes.

        * ``self._dispatch_fn`` is None (test fixtures, alternate
          wirings) — fall back to raw ``running_tasks.enqueue``. Same
          stranding hazard as before, but tests don't exercise an
          end-to-end drain so it's fine for them; the
          ``test_dispatch_fn_routes_through_transport`` regression test
          covers the production path.

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

        if self._dispatch_fn is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._dispatch_fn(
                        chat_id=chat_id,
                        user_id=self._user_id,
                        text=text,
                        schedule_id=schedule_id,
                    ),
                    self._loop,
                )
                # 5s is generous — the dispatch_fn just spawns a
                # background task (asyncio.create_task) and returns;
                # it does NOT await the brain turn.
                return bool(future.result(timeout=5.0))
            except TypeError:
                # Back-compat: a dispatch_fn that doesn't accept
                # ``schedule_id`` yet (older transport, test fakes).
                # Drop the kwarg and retry once so the wiring keeps
                # working — the outcome callback just won't fire for
                # this fire. Logged so the gap is visible.
                log.debug(
                    "dispatch_fn for chat=%d doesn't accept schedule_id; "
                    "outcome callback disabled for this fire",
                    chat_id,
                )
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._dispatch_fn(
                            chat_id=chat_id,
                            user_id=self._user_id,
                            text=text,
                        ),
                        self._loop,
                    )
                    return bool(future.result(timeout=5.0))
                except Exception as exc:
                    log.warning(
                        "ScheduleManager dispatch_fn (legacy) failed for "
                        "chat=%d: %s",
                        chat_id, exc,
                    )
                    return False
            except Exception as exc:
                log.warning(
                    "ScheduleManager dispatch_fn failed for chat=%d: %s",
                    chat_id,
                    exc,
                )
                return False

        # Legacy raw-enqueue path. Reached only when no dispatch_fn is
        # wired (tests, alt wirings). Note: this path WILL strand the
        # prompt if no drain is currently active — see the docstring's
        # 2:30 AM warning. main.py always wires dispatch_fn for the
        # Telegram transport.
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._running_tasks.enqueue(
                    chat_id=chat_id,
                    user_id=self._user_id,
                    text=text,
                    origin="scheduled_fire",
                    schedule_id=schedule_id,
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

    def _record_gated_fire(self, schedule_id: str, *, fired_at: datetime) -> None:
        """Record a "script gated the wake" outcome — Issue #12.

        Treated as a successful fire for accounting (last_status=ok,
        consecutive_errors reset, last_fire_at advanced) because the
        whole point of the wake gate is that "no change → no LLM
        turn" is the expected steady state for monitoring schedules.
        Counting a gated fire as an error would auto-pause every
        well-behaved monitor at the threshold.

        The ``running_at`` marker is cleared since no brain turn is in
        flight. ``last_error`` is cleared too — the gate is not an
        error.
        """
        from dataclasses import replace
        try:
            self._store.update_atomic(
                schedule_id,
                lambda s: replace(
                    s,
                    last_fire_at=fired_at,
                    last_status="ok",
                    last_error=None,
                    consecutive_errors=0,
                    running_at=None,
                ),
                refuse_terminal=False,
            )
        except KeyError:
            pass

    def _record_script_failure(
        self, schedule_id: str, *, fired_at: datetime, reason: str,
    ) -> None:
        """Record a script-side failure (timeout, path-traversal,
        subprocess crash) — Issue #12.

        Distinct from the brain-side error path: script failures do
        NOT count toward ``consecutive_errors`` because the user's
        gate breaking is a different signal from the brain failing.
        Auto-pausing a legitimate schedule because a buggy gate keeps
        timing out would be hostile — we'd hide their monitor from
        them. ``last_status`` is set to ``error`` and ``last_error``
        carries the reason so the dashboard surfaces it.
        """
        from dataclasses import replace
        try:
            self._store.update_atomic(
                schedule_id,
                lambda s: replace(
                    s,
                    last_fire_at=fired_at,
                    last_status="error",
                    last_error=reason[:240],
                    running_at=None,
                ),
                refuse_terminal=False,
            )
        except KeyError:
            pass

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

    # ----- post-fire outcome (called from the drain) ----------------

    def report_fire_outcome(
        self,
        schedule_id: str,
        *,
        success: bool,
        error_message: str | None = None,
        is_permanent: bool = False,
    ) -> None:
        """Apply the real brain outcome to a scheduled fire.

        The dispatch path writes ``last_status="ok"`` as soon as the
        prompt is enqueued (see :func:`_record_fire`) — pre-emptively
        optimistic, because the brain runs in a background task and
        the manager has no other way to know it finished. This
        overwrites that pre-emptive write with the truth once the
        drain has actually consumed the message and run the brain.

        Called from the transport drain after each brain turn whose
        :class:`QueuedMessage.schedule_id` is set. Safe to call from
        either the asyncio loop thread or any worker thread —
        ``ScheduleStore.update_atomic`` already handles cross-thread
        access via fcntl.

        Behaviour matrix:

          - ``success=True``: ``last_status="ok"``,
            ``consecutive_errors=0``, clear ``last_error``. Idempotent
            with the pre-emptive write — they agree.
          - ``success=False, is_permanent=False``: ``last_status=
            "error"``, increment ``consecutive_errors``, record
            ``error_message``. Auto-pause at threshold (same rule as
            enqueue-failure today, just with a real cause string).
          - ``success=False, is_permanent=True``: same plus
            immediate auto-pause regardless of counter, with
            ``paused_reason="permanent_failure: <short>"``. Retrying
            an auth-failed schedule daily until N consecutive errors
            is user-hostile; permanent errors mean stop now.

        Unknown ``schedule_id`` (deleted mid-flight) is logged and
        ignored — same defensive contract as
        :meth:`_advance_and_save`.
        """
        if not schedule_id:
            return
        from dataclasses import replace

        max_errors = self._max_consecutive_errors_fn()

        def _mutate(s: ScheduleState) -> ScheduleState:
            if success:
                return replace(
                    s,
                    last_status="ok",
                    last_error=None,
                    consecutive_errors=0,
                )
            new_errors = s.consecutive_errors + 1
            # Permanent failures bypass the threshold and pause now —
            # the user has to fix something (auth, model id) so
            # firing the broken prompt every day is just noise.
            if is_permanent and s.status == "active":
                short_reason = (error_message or "permanent failure")
                if len(short_reason) > 120:
                    short_reason = short_reason[:119] + "…"
                return replace(
                    s,
                    last_status="error",
                    last_error=error_message or "permanent brain failure",
                    consecutive_errors=new_errors,
                    status="paused",
                    paused_reason=f"permanent_failure: {short_reason}",
                )
            # Transient or unknown — count toward the existing
            # auto-pause threshold so repeated upstream outages
            # still take the schedule out of rotation eventually.
            if new_errors >= max_errors and s.status == "active":
                return replace(
                    s,
                    last_status="error",
                    last_error=error_message or "brain failure",
                    consecutive_errors=new_errors,
                    status="paused",
                    paused_reason="auto: errors",
                )
            return replace(
                s,
                last_status="error",
                last_error=error_message or "brain failure",
                consecutive_errors=new_errors,
            )

        try:
            self._store.update_atomic(
                schedule_id, _mutate, refuse_terminal=False,
            )
        except KeyError:
            log.info(
                "report_fire_outcome: schedule %s no longer exists; "
                "outcome dropped",
                schedule_id,
            )
        except Exception:
            log.exception(
                "report_fire_outcome failed for schedule %s", schedule_id,
            )

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
    "DispatchFn",
    "MIN_REFIRE_GAP_SECONDS",
    "ScheduleManager",
    "ScriptExecutionError",
    "ScriptGatedError",
    "ScriptPathError",
    "ScriptTimeoutError",
    "prepend_script_output",
    "run_pre_run_script",
]
