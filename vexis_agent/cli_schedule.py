"""``vexis-agent schedule …`` subcommand group.

This is the brain-callable surface for the /schedule feature. The
brain shells out to these commands (e.g. ``vexis-agent schedule
create --expr "0 9 * * *" --prompt "..."``) when the user asks for
a recurring task in chat; the Telegram slash command's local
management subcommands and the dashboard's POST endpoints also go
through this module so all three surfaces (brain, Telegram, web)
share one creation/mutation path.

Three invariants the brain must obey when calling ``create`` (also
documented in the command's ``--help`` text so a brain checking
``--help`` first sees them):

  1. **System clock.** The tool uses ``datetime.now()`` to compute
     ``next_fire_at`` and returns it. Echo the returned ISO and tz
     verbatim — do not reformat, do not compute "tomorrow".
  2. **Echo-confirmation.** Echo ``next_fire_at`` and ``tz`` back
     to the user in the reply. This is the user's safety net.
  3. **Recursion guard.** When called during a ``scheduled_fire``
     turn, create refuses with a clear error. The brain receiving
     this error should explain to the user that a scheduled task
     cannot create more schedules.

Design citation:
``.plans/scheduling-and-provider-abstraction-research.md`` Day 2
(MCP tool surface).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from vexis_agent.core.paths import vexis_dir
from vexis_agent.core.schedule_state import (
    ScheduleState,
    ScheduleStore,
    TerminalScheduleError,
    new_schedule_id,
)
from vexis_agent.tools.schedule_tool.parser import (
    ScheduleParseError,
    compute_next_fire,
    parse_schedule,
)

schedule_app = typer.Typer(
    name="schedule",
    help="Create and manage scheduled (cron / interval / one-shot) jobs.",
    no_args_is_help=True,
)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _schedules_path() -> Path:
    """Default location: ``~/.vexis/schedules.json``. Overridable for
    tests via the ``VEXIS_SCHEDULES_PATH`` env var.
    """
    override = os.environ.get("VEXIS_SCHEDULES_PATH")
    if override:
        return Path(override)
    return vexis_dir() / "schedules.json"


def _store() -> ScheduleStore:
    return ScheduleStore(_schedules_path())


def _is_scheduled_fire_context() -> bool:
    """Recursion-guard check: ``VEXIS_SCHEDULED_FIRE=1`` set on the
    brain's environment when it's processing a ``scheduled_fire``
    origin message. Set by the manager / drain loop (Day 2-3 wiring).

    Until the env-var wiring lands, this returns False — the
    recursion guard is a safety net, not the primary blocker (the
    canonical filter is the ``origin`` tag in the FIFO, per
    CLAUDE.md Invariants).
    """
    return os.environ.get("VEXIS_SCHEDULED_FIRE") == "1"


def _resolve_id(store: ScheduleStore, prefix_or_id: str) -> str:
    """Resolve a 3+-char id prefix to a full id, or exit 1 if no
    unambiguous match.

    Used by show/pause/resume/clear. The brain passes either a full
    12-char id (from a prior ``list`` call) or a short prefix the
    user mentioned. Either works.
    """
    if not prefix_or_id:
        typer.echo("error: id is required", err=True)
        raise typer.Exit(code=2)
    # Exact match first (no need to require prefix length when the
    # caller passed the full id).
    exact = store.load(prefix_or_id)
    if exact is not None:
        return exact.id
    resolved = store.resolve_id_prefix(prefix_or_id)
    if resolved is None:
        typer.echo(
            f"error: no schedule matches {prefix_or_id!r} "
            "(or prefix is ambiguous / shorter than 3 chars)",
            err=True,
        )
        raise typer.Exit(code=1)
    return resolved


def _render_text(state: ScheduleState, *, verbose: bool = False) -> str:
    """Human-readable single-line render. ``verbose`` adds last-fire
    + last-error on a second line.
    """
    prompt_preview = state.prompt
    if len(prompt_preview) > 60:
        prompt_preview = prompt_preview[:57] + "..."
    nfa = (
        state.next_fire_at.isoformat()
        if state.next_fire_at
        else "—"
    )
    line = (
        f"{state.id[:8]}  {state.status:<7}  {state.schedule_display}  "
        f"next: {nfa}  \"{prompt_preview}\""
    )
    if not verbose:
        return line
    lines = [line]
    if state.last_fire_at:
        lf = state.last_fire_at.isoformat()
        ls = state.last_status or "?"
        lines.append(f"  last fire: {lf} ({ls})")
    if state.last_error:
        lines.append(f"  last error: {state.last_error}")
    if state.paused_reason:
        lines.append(f"  paused: {state.paused_reason}")
    return "\n".join(lines)


def _render_json(state: ScheduleState) -> dict:
    """Brain-facing JSON shape. Stable for tool-use parsers."""
    return state.to_dict()


# ──────────────────────────────────────────────────────────────────
# create
# ──────────────────────────────────────────────────────────────────


@schedule_app.command("create")
def create(
    expr: str = typer.Option(
        ...,
        "--expr",
        "-e",
        help=(
            "Schedule expression. Four shapes: relative one-shot "
            "('30m', '2h', '1d'), recurring interval ('every 30m'), "
            "cron ('0 9 * * 1-5'), ISO timestamp ('2026-12-31T23:59'). "
            "Translate natural language to one of these — do NOT pass "
            "natural language directly."
        ),
    ),
    prompt: str = typer.Option(
        ...,
        "--prompt",
        "-p",
        help="The text that fires into the chat when the schedule is due.",
    ),
    chat_id: int = typer.Option(
        ...,
        "--chat-id",
        help="Chat that receives the fire. Required.",
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        "-n",
        help="Optional friendly label for /schedule list rendering.",
    ),
    tz: Optional[str] = typer.Option(
        None,
        "--tz",
        help=(
            "IANA timezone for cron / ISO interpretation. If the user "
            "mentions a timezone ('Tokyo time', 'PST', 'UK time'), pass "
            "the IANA equivalent (Asia/Tokyo, America/Los_Angeles, "
            "Europe/London). Omit to use daemon-local."
        ),
    ),
    output: str = typer.Option(
        "json",
        "--output",
        "-o",
        help="Output format: 'json' (default) or 'text'.",
    ),
) -> None:
    """Create a new schedule.

    REQUIRED INVARIANTS for the brain caller:

      1. SYSTEM CLOCK — this tool uses datetime.now() to compute
         next_fire_at. The returned 'next_fire_at' field is
         authoritative; do not compute it yourself.
      2. ECHO CONFIRMATION — your reply to the user MUST include the
         exact next_fire_at and tz this tool returns. This is the
         user's safety net against parse errors.
      3. RECURSION GUARD — if you're processing a scheduled_fire
         message, this tool refuses with TerminalScheduleError.
         Scheduled fires cannot create more schedules.
    """
    # Recursion guard — refuse if we're inside a scheduled fire.
    if _is_scheduled_fire_context():
        typer.echo(
            json.dumps(
                {
                    "error": "scheduled_fire_recursion",
                    "message": (
                        "scheduled fires cannot create schedules; the "
                        "current message originates from a previous "
                        "schedule and creating more would risk runaway "
                        "loops"
                    ),
                }
            ),
            err=True,
        )
        raise typer.Exit(code=3)

    # Load config for caps. Import inside the function so import-time
    # cost of `vexis-agent --help` doesn't pay for yaml load.
    from vexis_agent.core.yaml_config import (
        schedules_max_prompt_length,
        schedules_max_total,
    )

    max_prompt = schedules_max_prompt_length()
    if len(prompt) > max_prompt:
        typer.echo(
            json.dumps(
                {
                    "error": "prompt_too_long",
                    "message": (
                        f"prompt is {len(prompt)} chars; cap is "
                        f"{max_prompt}. shorten the prompt or raise "
                        "schedules.max_prompt_length in config.yaml"
                    ),
                }
            ),
            err=True,
        )
        raise typer.Exit(code=4)

    store = _store()
    max_total = schedules_max_total()
    if store.total_count() >= max_total:
        typer.echo(
            json.dumps(
                {
                    "error": "cap_reached",
                    "message": (
                        f"schedule cap reached: {max_total}. remove some "
                        f"with 'vexis-agent schedule clear <id>' or raise "
                        "schedules.max_total in config.yaml"
                    ),
                }
            ),
            err=True,
        )
        raise typer.Exit(code=5)

    try:
        parsed = parse_schedule(expr, tz=tz)
    except ScheduleParseError as exc:
        typer.echo(
            json.dumps(
                {
                    "error": "parse_error",
                    "message": str(exc),
                    "suggestion": exc.suggestion,
                }
            ),
            err=True,
        )
        raise typer.Exit(code=6)

    next_fire = compute_next_fire(parsed, last_fire_at=None)
    if next_fire is None and parsed.get("kind") == "once":
        # ISO timestamp in the past beyond grace.
        typer.echo(
            json.dumps(
                {
                    "error": "past_due",
                    "message": (
                        f"the time {expr!r} is more than 2 minutes in the "
                        "past; pick a future moment"
                    ),
                }
            ),
            err=True,
        )
        raise typer.Exit(code=7)

    state = ScheduleState(
        id=new_schedule_id(),
        chat_id=chat_id,
        schedule=parsed,
        schedule_display=parsed.get("display", expr),
        prompt=prompt,
        name=name,
        next_fire_at=next_fire,
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    store.save(state)

    payload = {
        "ok": True,
        "id": state.id,
        "next_fire_at": (
            state.next_fire_at.isoformat()
            if state.next_fire_at
            else None
        ),
        "schedule_display": state.schedule_display,
        "tz": parsed.get("tz"),
    }
    if output == "text":
        typer.echo(_render_text(state, verbose=True))
    else:
        typer.echo(json.dumps(payload))


# ──────────────────────────────────────────────────────────────────
# list
# ──────────────────────────────────────────────────────────────────


@schedule_app.command("list")
def list_(
    status_filter: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter by status: active|paused|expired|cleared|all (default: non-cleared).",
    ),
    output: str = typer.Option(
        "text",
        "--output",
        "-o",
        help="Output format: 'text' (default) or 'json'.",
    ),
) -> None:
    """List schedules. Default excludes cleared (audit-only) entries."""
    store = _store()
    rows = store.list_all()
    if status_filter == "all":
        pass
    elif status_filter:
        wanted = {status_filter}
        rows = [r for r in rows if r.status in wanted]
    else:
        rows = [r for r in rows if r.status != "cleared"]

    if output == "json":
        typer.echo(
            json.dumps([_render_json(r) for r in rows], default=str)
        )
        return

    if not rows:
        typer.echo("No schedules.")
        return
    for r in rows:
        typer.echo(_render_text(r))


# ──────────────────────────────────────────────────────────────────
# show
# ──────────────────────────────────────────────────────────────────


@schedule_app.command("show")
def show(
    id: str = typer.Argument(..., help="Schedule id or 3+-char prefix."),
    output: str = typer.Option(
        "text",
        "--output",
        "-o",
        help="Output format: 'text' (default) or 'json'.",
    ),
) -> None:
    """Print a single schedule's full record."""
    store = _store()
    sid = _resolve_id(store, id)
    state = store.load(sid)
    if state is None:
        typer.echo(f"error: schedule {sid} disappeared mid-read", err=True)
        raise typer.Exit(code=1)
    if output == "json":
        typer.echo(json.dumps(_render_json(state), default=str))
    else:
        typer.echo(_render_text(state, verbose=True))


# ──────────────────────────────────────────────────────────────────
# pause / resume / clear
# ──────────────────────────────────────────────────────────────────


@schedule_app.command("pause")
def pause(
    id: str = typer.Argument(..., help="Schedule id or 3+-char prefix."),
) -> None:
    """Pause a schedule (no fires until resume). Refuses on terminal status."""
    store = _store()
    sid = _resolve_id(store, id)
    try:
        result = store.update_atomic(
            sid,
            lambda s: replace(
                s,
                status="paused",
                paused_reason="user",
                next_fire_at=None,
            ),
        )
    except TerminalScheduleError as exc:
        typer.echo(
            json.dumps(
                {
                    "error": "terminal_status",
                    "message": f"schedule already {exc.status}",
                }
            ),
            err=True,
        )
        raise typer.Exit(code=8)
    typer.echo(
        json.dumps(
            {"ok": True, "id": result.id, "status": result.status}
        )
    )


@schedule_app.command("resume")
def resume(
    id: str = typer.Argument(..., help="Schedule id or 3+-char prefix."),
) -> None:
    """Resume a paused schedule. Recomputes next_fire_at from now."""
    store = _store()
    sid = _resolve_id(store, id)
    try:
        result = store.update_atomic(
            sid,
            lambda s: replace(
                s,
                status="active",
                paused_reason=None,
                next_fire_at=compute_next_fire(s.schedule, last_fire_at=None),
                consecutive_errors=0,  # fresh start
            ),
        )
    except TerminalScheduleError as exc:
        typer.echo(
            json.dumps(
                {
                    "error": "terminal_status",
                    "message": f"schedule already {exc.status}",
                }
            ),
            err=True,
        )
        raise typer.Exit(code=8)
    typer.echo(
        json.dumps(
            {
                "ok": True,
                "id": result.id,
                "status": result.status,
                "next_fire_at": (
                    result.next_fire_at.isoformat()
                    if result.next_fire_at
                    else None
                ),
            }
        )
    )


@schedule_app.command("clear")
def clear(
    id: str = typer.Argument(..., help="Schedule id or 3+-char prefix."),
) -> None:
    """Clear (soft-delete) a schedule. Record retained for audit."""
    store = _store()
    sid = _resolve_id(store, id)
    store.clear(sid)
    typer.echo(json.dumps({"ok": True, "id": sid, "status": "cleared"}))


# ──────────────────────────────────────────────────────────────────
# tick — debugging
# ──────────────────────────────────────────────────────────────────


@schedule_app.command("tick")
def tick(
    force: bool = typer.Option(
        False,
        "--force",
        help="Run a tick even if the daemon is suspected of running.",
    ),
) -> None:
    """Force one tick of the schedule manager (debugging).

    This bypasses the daemon's normal cadence. If the daemon is
    running, this can race with its tick — use --force to acknowledge
    the risk. The manager's at-most-once advance protects against
    double-firing the same schedule in the same tick window.

    Without a running daemon (or RunningTasks loop), schedules that
    fire here will fail at the enqueue step and increment
    consecutive_errors. The advance still happens — that's the
    correct at-most-once posture.
    """
    if not force:
        typer.echo(
            "tick is destructive; pass --force to confirm. "
            "Use 'vexis-agent schedule list' to inspect state without firing.",
            err=True,
        )
        raise typer.Exit(code=9)

    # Late import — pulling RunningTasks at module load would force
    # the asyncio stack on `--help`.
    from vexis_agent.core.running_tasks import RunningTasks
    from vexis_agent.core.schedule_manager import ScheduleManager

    # Inert RunningTasks + no event loop → fires fail at enqueue
    # (good — this CLI surface is debugging, not "actually deliver").
    # The advance still happens, which is what the user wants to verify.
    fake_running = RunningTasks()
    manager = ScheduleManager(
        _store(),
        fake_running,
        allowed_user_id=0,
        enabled_fn=lambda: True,
    )
    fired = manager._run_once()
    typer.echo(json.dumps({"ok": True, "fired": fired}))


__all__ = ["schedule_app"]
