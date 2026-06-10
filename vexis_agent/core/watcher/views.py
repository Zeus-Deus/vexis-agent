"""Watcher status views for the cross-surface "what's running?" replies.

The watcher registry was historically visible only through its own
surfaces (``/codemux``, ``vexis-watch status``). The generic
"is anything working?" surfaces — Telegram ``/tasks`` + ``/status``
and the dashboard status API — read just the vexis-bg registry, so a
watched workspace mid-delegation (the brain's preferred path when it
autonomously backgrounds work) produced "No background tasks
running." while the brain truthfully reported the work was 9 minutes
in. These views close that gap: each surface composes them as an
extra block, the same pattern ``core/goal_background.py`` uses for
kanban-backed background goals.

Pure reads — no mutation, no disk I/O (the registry is in-memory
between writes). Every helper is None-safe: pass ``watcher=None``
(subsystem disabled via ``watcher.enabled: false``) and it returns
its empty value, so call sites need no gating.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from vexis_agent.core.watcher.registry import WatchStatus

if TYPE_CHECKING:  # pragma: no cover — import cycle guard only
    from vexis_agent.core.watcher import WatcherController
    from vexis_agent.core.watcher.registry import WatchedAgent

# Status → glyph + user-facing word. "quiet" (not "idle") for the
# Telegram lines — it matches the idle-ping copy ("went quiet") so the
# user reads one vocabulary across the ping and /tasks.
_STATUS_GLYPH: dict[str, str] = {
    WatchStatus.RUNNING.value: "▶",
    WatchStatus.IDLE.value: "◦",
    WatchStatus.DEAD.value: "✗",
}
_STATUS_WORD: dict[str, str] = {
    WatchStatus.RUNNING.value: "running",
    WatchStatus.IDLE.value: "quiet",
    WatchStatus.DEAD.value: "gone",
}

_HINT_LINE = "  (reply `tail <name>` / `peek <name>` / `unwatch <name>`)"


def _parse_iso(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Registry timestamps are written tz-aware (_utcnow_iso); treat
        # a hand-edited naive value as UTC rather than crashing a view.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _elapsed_short(seconds: float) -> str:
    """One-token elapsed: ``5s`` / ``19m`` / ``2h`` / ``3d``. Same vocab
    as the ``/codemux`` handler and ``/tasks`` durations."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h"
    return f"{int(seconds / 86400)}d"


def _state_anchor(agent: "WatchedAgent") -> Optional[datetime]:
    """When the agent's *current* state began.

    running → ``registered_at`` (the delegation was enrolled; "running
    9m" should mean nine minutes of delegated work, not nine minutes
    since the last byte). idle/dead → ``last_status_transition_at``
    (when it went quiet / vanished), falling back to
    ``last_output_at`` for registries written before the transition
    field existed.
    """
    if agent.status == WatchStatus.RUNNING.value:
        return _parse_iso(agent.registered_at)
    return _parse_iso(agent.last_status_transition_at) or _parse_iso(
        agent.last_output_at
    )


def _elapsed_for(agent: "WatchedAgent", now: datetime) -> tuple[str, Optional[int]]:
    anchor = _state_anchor(agent)
    if anchor is None:
        return "—", None
    seconds = max(0, int((now - anchor).total_seconds()))
    return _elapsed_short(seconds), seconds


def watched_work_payload(
    watcher: "WatcherController | None",
    *,
    now: Optional[datetime] = None,
) -> list[dict]:
    """JSON-safe rows for the dashboard status API (``watched_agents``).

    ``[]`` when the watcher is disabled or nothing is registered. Rows
    mirror ``background_goals_payload``'s shape philosophy: registry
    fields verbatim plus a humanised ``elapsed`` so the frontend
    renders without re-deriving durations.
    """
    if watcher is None:
        return []
    agents = watcher.list_agents()
    if not agents:
        return []
    now = now or datetime.now(timezone.utc)
    rows: list[dict] = []
    for agent in agents:
        elapsed, elapsed_seconds = _elapsed_for(agent, now)
        rows.append({
            "name": agent.name,
            "source_type": agent.source_type,
            "workspace_id": agent.workspace_id,
            "agent_kind": agent.agent_kind,
            "status": agent.status,
            "state": _STATUS_WORD.get(agent.status, agent.status),
            "muted": agent.muted,
            "goal_hint": agent.goal_hint,
            "registered_at": agent.registered_at,
            "last_output_at": agent.last_output_at,
            "last_line": agent.last_line,
            "elapsed": elapsed,
            "elapsed_seconds": elapsed_seconds,
        })
    return rows


def render_watched_status(
    watcher: "WatcherController | None",
    *,
    max_agents: int = 10,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Multi-line watched-workspace block for ``/tasks`` and ``/status``.

    ``None`` when there is nothing to show — the caller skips the block
    entirely, mirroring ``render_background_goals_status``. Running
    agents sort first so the answer to "is anything working?" is the
    first line.
    """
    if watcher is None:
        return None
    agents = watcher.list_agents()
    if not agents:
        return None
    now = now or datetime.now(timezone.utc)
    ordered = sorted(
        agents,
        key=lambda a: (a.status != WatchStatus.RUNNING.value, a.name),
    )
    shown = ordered[:max_agents]
    lines = ["👁 Watched workspaces:"]
    for agent in shown:
        glyph = _STATUS_GLYPH.get(agent.status, "•")
        word = _STATUS_WORD.get(agent.status, agent.status)
        elapsed, _ = _elapsed_for(agent, now)
        head = f"  {glyph} `{agent.name}` [{word} {elapsed}]"
        if agent.muted:
            head += " (muted)"
        if agent.goal_hint:
            head += f" {agent.goal_hint}"
        lines.append(head)
    extra = len(ordered) - len(shown)
    if extra > 0:
        lines.append(f"  (+{extra} more — /codemux)")
    lines.append(_HINT_LINE)
    return "\n".join(lines)


__all__ = [
    "render_watched_status",
    "watched_work_payload",
]
