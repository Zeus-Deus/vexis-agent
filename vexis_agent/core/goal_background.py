"""Read-only projection of background goals over the kanban store.

As of v0.11 ``/goal <text>`` files the goal as a kanban task by default
(``goals.default_mode: background`` — see
:func:`core.yaml_config.goals_default_mode`) instead of running an
in-chat continuation loop. The foreground Telegram chat stays free; the
kanban dispatcher claims the task and a background worker drives it.

This module is the read side that makes that surface *conversational*:

  * :func:`render_background_goal_block` renders a compact
    ``[BACKGROUND GOALS]`` block that ``core.handler`` injects into every
    foreground brain turn — so when the user asks "how's my goal going?"
    the brain already has the live status in context and answers from it.
  * :func:`render_background_goals_status` renders the multi-line list
    that ``/goal status`` and ``/status`` append.

Goal-filed kanban tasks are tagged ``created_by`` starting with
:data:`GOAL_TASK_CREATED_BY`. Plain ``/kanban add`` tasks are NOT goal
tasks and never appear in these surfaces — the goal and kanban views
stay distinct even though they share one store.

Everything here is a pure read over a ``KanbanStore`` (SQLite reads, no
writes, no spawns). Safe to call once per foreground turn. Every public
function fails soft (returns ``[]`` / ``None``) on any store error so a
transient DB hiccup never breaks a chat turn.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from vexis_agent.core.kanban.constants import (
    EVENT_HEARTBEAT,
    EVENT_PROGRESS,
    STATUS_BLOCKED,
    STATUS_IN_PROGRESS,
    STATUS_READY,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from vexis_agent.core.kanban.db import KanbanStore, Task

log = logging.getLogger(__name__)


#: ``created_by`` marker prefix for kanban tasks filed via ``/goal``
#: (default background mode OR explicit ``--bg``). The goal surfaces
#: filter on this prefix so they never show plain ``/kanban add`` tasks.
#: A ``startswith`` match keeps older ``"user:/goal --bg"`` rows visible
#: alongside the current ``"user:/goal"`` tag.
GOAL_TASK_CREATED_BY = "user:/goal"

#: Statuses a background goal is considered "in flight" under. ``done``
#: and ``archived`` are terminal and excluded — a finished goal is
#: announced once by the kanban notifier, not kept as a standing line.
_ACTIVE_GOAL_STATUSES: tuple[str, ...] = (
    STATUS_READY,
    STATUS_IN_PROGRESS,
    STATUS_BLOCKED,
)

_BLOCK_HEADER = "[BACKGROUND GOALS — running outside this chat right now]"

_STATUS_WORD = {
    STATUS_READY: "queued",
    STATUS_IN_PROGRESS: "working",
    STATUS_BLOCKED: "blocked",
}

_STATUS_GLYPH = {
    STATUS_READY: "•",
    STATUS_IN_PROGRESS: "▸",
    STATUS_BLOCKED: "⛔",
}

# One activity snippet is one line; cap so a chatty worker note can't
# bloat the per-turn prompt.
_SNIPPET_CHARS = 160


def _is_goal_task(task: "Task") -> bool:
    return bool((task.created_by or "").startswith(GOAL_TASK_CREATED_BY))


def list_background_goals(
    store: "KanbanStore",
    *,
    statuses: tuple[str, ...] = _ACTIVE_GOAL_STATUSES,
    limit: int = 100,
) -> list["Task"]:
    """Active goal-filed kanban tasks.

    Ordered by the store's default (priority desc, created_at desc).
    Returns ``[]`` on any error or when ``store`` is ``None``.
    """
    if store is None:
        return []
    try:
        tasks = store.list_tasks(limit=limit)
    except Exception:  # pragma: no cover - defensive
        log.debug("background-goal: list_tasks failed", exc_info=True)
        return []
    return [t for t in tasks if _is_goal_task(t) and t.status in statuses]


def _fmt_elapsed(since_unix: int | None, now: int) -> str:
    """Compact elapsed-time string ("45s", "12m", "3h07m", "2d04h")."""
    if not since_unix:
        return ""
    secs = max(0, now - int(since_unix))
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h{mins % 60:02d}m"
    days = hrs // 24
    return f"{days}d{hrs % 24:02d}h"


def _latest_activity(store: "KanbanStore", task: "Task") -> str:
    """Best one-line description of the task's most recent activity.

    Prefers a worker heartbeat/progress note; falls back to the latest
    run's summary, then its error. Returns "" when nothing useful exists
    (e.g. a freshly-filed task the dispatcher hasn't claimed yet).
    """
    try:
        events = store.list_events(task.id, limit=15)  # newest first
    except Exception:  # pragma: no cover - defensive
        events = []
    for ev in events:
        if ev.kind in (EVENT_HEARTBEAT, EVENT_PROGRESS):
            note = (ev.payload or {}).get("progress")
            if note:
                return str(note).strip().splitlines()[0][:_SNIPPET_CHARS]
    try:
        runs = store.list_runs(task.id)  # newest first
    except Exception:  # pragma: no cover - defensive
        runs = []
    if runs:
        run = runs[0]
        if run.summary:
            return str(run.summary).strip().splitlines()[0][:_SNIPPET_CHARS]
        if run.error:
            snippet = str(run.error).strip().splitlines()[0]
            return ("error: " + snippet)[:_SNIPPET_CHARS]
    return ""


def _line_head(task: "Task", now: int) -> str:
    elapsed = _fmt_elapsed(task.started_at or task.created_at, now)
    state = _STATUS_WORD.get(task.status, task.status)
    suffix = f" {elapsed}" if elapsed else ""
    return f"`{task.id}` [{state}{suffix}] {task.title}"


def render_background_goal_block(
    store: "KanbanStore",
    *,
    max_goals: int = 5,
    now: int | None = None,
) -> str | None:
    """Compact ``[BACKGROUND GOALS]`` block for foreground turn injection.

    ``None`` when no background goals are active (so the handler injects
    nothing). Otherwise one line per goal plus a one-line latest-activity
    snippet, capped at ``max_goals``, followed by a short instruction so
    the brain knows how to talk about them.
    """
    goals = list_background_goals(store)
    if not goals:
        return None
    now = int(time.time()) if now is None else now
    shown = goals[:max_goals]
    lines = [_BLOCK_HEADER]
    for task in shown:
        lines.append(f"- {_line_head(task, now)}")
        note = _latest_activity(store, task)
        if note:
            lines.append(f"    last: {note}")
    extra = len(goals) - len(shown)
    if extra > 0:
        lines.append(f"- (+{extra} more — `vexis-kanban list`)")
    lines.append(
        "These are running in the background as kanban workers — a "
        "separate session did the work, not this chat. If the user asks "
        "how a goal is going, answer from the status above; for the full "
        "run detail use `vexis-kanban show <id>`."
    )
    return "\n".join(lines)


def background_goals_payload(
    store: "KanbanStore",
    *,
    now: int | None = None,
) -> list[dict]:
    """Structured background-goal rows for the dashboard ``/api/v1/goals``.

    The dashboard's foreground goal payload (``active`` / ``history``)
    reads the per-session ``goals.json`` store and is blind to the
    kanban-backed background goals filed by ``/goal <text>`` (the v0.11
    default). This is the read the dashboard uses to also surface those,
    so a background goal is visible on the Goals page + goal-pad — not
    only on the raw kanban board.

    Returns ``[]`` when no background goals are active or the store is
    unavailable. Each row is JSON-safe (ints + strings + None).
    """
    goals = list_background_goals(store)
    if not goals:
        return []
    now = int(time.time()) if now is None else now
    rows: list[dict] = []
    for task in goals:
        started = task.started_at or task.created_at
        rows.append({
            "id": task.id,
            "title": task.title,
            "status": task.status,                       # ready/in_progress/blocked
            "state": _STATUS_WORD.get(task.status, task.status),  # queued/working/blocked
            "lane": task.lane,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "elapsed": _fmt_elapsed(started, now),
            "elapsed_seconds": (
                max(0, now - int(started)) if started else None
            ),
            "last_activity": _latest_activity(store, task) or None,
        })
    return rows


def render_background_goals_status(
    store: "KanbanStore",
    *,
    max_goals: int = 10,
    now: int | None = None,
) -> str | None:
    """Multi-line background-goal list for ``/goal status`` and ``/status``.

    ``None`` when no background goals are active.
    """
    goals = list_background_goals(store)
    if not goals:
        return None
    now = int(time.time()) if now is None else now
    shown = goals[:max_goals]
    lines = ["📋 Background goals:"]
    for task in shown:
        glyph = _STATUS_GLYPH.get(task.status, "•")
        lines.append(f"  {glyph} {_line_head(task, now)}")
    extra = len(goals) - len(shown)
    if extra > 0:
        lines.append(f"  (+{extra} more — /kanban list)")
    return "\n".join(lines)


__all__ = [
    "GOAL_TASK_CREATED_BY",
    "background_goals_payload",
    "list_background_goals",
    "render_background_goal_block",
    "render_background_goals_status",
]
