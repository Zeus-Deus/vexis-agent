"""Kanban tool actions — the unified surface Telegram, the dashboard,
the CLI, and the (future) MCP server all wrap.

See ``__init__.py`` for the MCP-name → function mapping. Each function
returns a JSON-serialisable dict. Domain errors land in the dict's
``error`` key; only caller-misuse (wrong type, missing required arg)
raises :class:`ToolError`.

Design note: these functions deliberately take a :class:`KanbanStore`
argument rather than reading a singleton. The store is owned by the
daemon (one instance per process). Tests construct their own per-test
store; the daemon's transport layers all share the daemon's instance.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from vexis_agent.core.kanban.constants import (
    DEFAULT_CLAIM_TTL_SECONDS,
    EVENT_BLOCKED,
    EVENT_COMPLETED,
    EVENT_HEARTBEAT,
    EVENT_UNBLOCKED,
    RUN_STATUS_BLOCKED,
    RUN_STATUS_DONE,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_READY,
    STATUS_TODO,
    STATUS_TRIAGE,
    VALID_STATUSES,
)
from vexis_agent.core.kanban.db import (
    ClaimContentionError,
    InvalidStatusError,
    KanbanError,
    KanbanStore,
    TaskNotFoundError,
)
from vexis_agent.core.kanban.lanes import (
    LaneNotFoundError,
    list_lanes,
    resolve_lane,
)
from vexis_agent.core.paths import vexis_dir

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Errors + result helpers
# ──────────────────────────────────────────────────────────────────


class ToolError(Exception):
    """Caller-side misuse: wrong type, missing required arg, etc.

    Domain errors (task not found, claim contention) flow through the
    result dict's ``error`` key — those are recoverable user-facing
    states the caller renders directly. ``ToolError`` is for bugs that
    should fail loud during development."""


def _ok(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Success-shape dict. ``payload`` becomes ``data`` in the result."""
    out: dict[str, Any] = {"ok": True}
    if payload is not None:
        out["data"] = payload
    return out


def _err(message: str, *, kind: str = "Error") -> dict[str, Any]:
    """Domain-error result dict. ``kind`` carries the exception class
    name so a JSON-aware caller can switch on it (the dashboard maps
    ``TaskNotFoundError`` → 404, ``ClaimContentionError`` → 409, etc).
    """
    return {"ok": False, "error": message, "kind": kind}


# ──────────────────────────────────────────────────────────────────
# Store factory
# ──────────────────────────────────────────────────────────────────


_DEFAULT_DB_FILENAME = "kanban.db"


def default_db_path() -> Path:
    """``~/.vexis/kanban.db`` (test-isolated via the conftest fixture)."""
    return vexis_dir() / _DEFAULT_DB_FILENAME


def open_default_store() -> KanbanStore:
    """Open the daemon-default kanban DB. Used by CLI + tests that
    don't already hold a store. Daemon code uses its own pre-opened
    instance and passes that into these functions directly — DO NOT
    call this from request-handler code."""
    return KanbanStore(default_db_path())


# ──────────────────────────────────────────────────────────────────
# Create / read / list
# ──────────────────────────────────────────────────────────────────


def create_task(
    store: KanbanStore,
    *,
    title: str,
    body: str | None = None,
    lane: str | None = None,
    status: str | None = None,
    priority: int = 0,
    created_by: str = "user",
    workspace_path: str | None = None,
    max_runtime_seconds: int | None = None,
    skills: list[str] | None = None,
    max_retries: int | None = None,
    parents: list[str] | None = None,
) -> dict[str, Any]:
    """Create a new task. Returns ``{ok: True, data: <task dict>}``.

    Defaults: ``status="triage"`` (user must explicitly move to todo/
    ready); ``priority=0``; ``created_by="user"``. The dispatcher uses
    ``created_by="agent:<lane>"`` when fanning out from another task.

    Lane validation happens here (not at DB layer) so the error
    surfaces with the actionable hint listing known lanes.
    """
    if not isinstance(title, str) or not title.strip():
        raise ToolError("title is required (non-empty string)")
    if lane is not None and lane != "":
        try:
            resolve_lane(lane)
        except LaneNotFoundError as exc:
            return _err(str(exc), kind="LaneNotFoundError")
    if status is not None and status not in VALID_STATUSES:
        return _err(
            f"invalid status {status!r}; expected one of "
            f"{sorted(VALID_STATUSES)}",
            kind="InvalidStatusError",
        )
    try:
        task = store.create_task(
            title=title.strip(),
            body=body,
            lane=lane or None,
            status=status or STATUS_TRIAGE,
            priority=priority,
            created_by=created_by,
            workspace_path=workspace_path,
            max_runtime_seconds=max_runtime_seconds,
            skills=skills,
            max_retries=max_retries,
            parents=parents,
        )
    except TaskNotFoundError as exc:
        # Parent reference missing.
        return _err(str(exc), kind="TaskNotFoundError")
    except KanbanError as exc:
        return _err(str(exc), kind=type(exc).__name__)
    return _ok(task.to_dict())


def show_task(store: KanbanStore, task_id: str) -> dict[str, Any]:
    """Full task details: row + parents + children + recent events +
    comments + runs. Powers the dashboard detail modal and Telegram's
    ``/kanban show <id>`` reply."""
    if not isinstance(task_id, str) or not task_id:
        raise ToolError("task_id is required (non-empty string)")
    task = store.get_task(task_id)
    if task is None:
        return _err(f"task not found: {task_id}", kind="TaskNotFoundError")
    parents = store.get_parents(task_id)
    children = store.get_children(task_id)
    comments = store.list_comments(task_id)
    runs = store.list_runs(task_id)
    events = store.list_events(task_id, limit=30)
    return _ok({
        "task": task.to_dict(),
        "parents": parents,
        "children": children,
        "comments": [c.to_dict() for c in comments],
        "runs": [r.to_dict() for r in runs],
        "events": [e.to_dict() for e in events],
    })


def list_board(
    store: KanbanStore,
    *,
    status: str | None = None,
    lane: str | None = None,
    include_archived: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """List tasks for the board view. Returns counts (board_summary)
    AND the full task list so a single round-trip populates both the
    column counts and the column contents on the dashboard.

    ``status=None + include_archived=False`` (default) is the
    "active board" view — every column except archived.
    """
    if status is not None and status not in VALID_STATUSES:
        return _err(
            f"invalid status {status!r}",
            kind="InvalidStatusError",
        )
    if lane is not None and lane != "":
        try:
            resolve_lane(lane)
        except LaneNotFoundError as exc:
            return _err(str(exc), kind="LaneNotFoundError")
    try:
        tasks = store.list_tasks(
            status=status,
            lane=lane or None,
            include_archived=include_archived,
            limit=limit,
        )
    except InvalidStatusError as exc:
        return _err(str(exc), kind="InvalidStatusError")
    return _ok({
        "summary": store.board_summary(),
        "tasks": [t.to_dict() for t in tasks],
    })


def list_lanes_info(store: KanbanStore) -> dict[str, Any]:
    """Available lanes — defaults + user overrides. Powers the
    dashboard's lane picker and the Telegram ``/kanban add --lane=?``
    autocomplete hint. ``store`` is unused but accepted for API parity
    so the dashboard route can pass it without thinking."""
    del store  # signature parity; nothing to read from the store here
    return _ok({
        "lanes": [lane.to_dict() for lane in list_lanes()],
    })


def list_events(
    store: KanbanStore,
    *,
    since: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    """Audit-log slice for the dashboard WS + Telegram notifier. Cursor
    pagination via ``since``."""
    events = store.events_since(since, limit=limit)
    latest = store.latest_event_id()
    return _ok({
        "events": [e.to_dict() for e in events],
        "cursor": events[-1].id if events else since,
        "latest": latest,
    })


def list_runs(store: KanbanStore, task_id: str) -> dict[str, Any]:
    """All runs for a task, newest first."""
    if not isinstance(task_id, str) or not task_id:
        raise ToolError("task_id is required")
    if store.get_task(task_id) is None:
        return _err(f"task not found: {task_id}", kind="TaskNotFoundError")
    runs = store.list_runs(task_id)
    return _ok({"runs": [r.to_dict() for r in runs]})


# ──────────────────────────────────────────────────────────────────
# Worker-facing actions (kanban_complete / kanban_block / heartbeat)
# ──────────────────────────────────────────────────────────────────


def complete_task(
    store: KanbanStore,
    task_id: str,
    *,
    summary: str | None = None,
    author: str = "agent",
) -> dict[str, Any]:
    """Mark a task as done. The dispatcher's spawn handler observes
    this on its next state read after the worker exits and finalises
    the run.

    Adds an audit event so the dashboard / Telegram notifier see it
    live; also drops a comment with the summary if one was provided
    (so the dashboard detail modal has the worker's wrap-up message
    inline)."""
    if not isinstance(task_id, str) or not task_id:
        raise ToolError("task_id is required")
    task = store.get_task(task_id)
    if task is None:
        return _err(f"task not found: {task_id}", kind="TaskNotFoundError")
    try:
        store.update_task(task_id, status=STATUS_DONE)
    except KanbanError as exc:
        return _err(str(exc), kind=type(exc).__name__)
    if summary and summary.strip():
        store.add_comment(
            task_id, author=author, body=summary.strip()[:4000],
        )
    # Explicit COMPLETED event in addition to the EDITED-status-done
    # event update_task produces, so the notifier can filter on a
    # dedicated kind without parsing payload.
    run_id = task.current_run_id
    store.append_event(
        task_id, EVENT_COMPLETED,
        {"summary": (summary or "")[:1000]},
        run_id=run_id,
    )
    if run_id is not None:
        store.finalize_run(
            run_id, outcome="completed",
            summary=summary or "completed by worker",
            new_status=RUN_STATUS_DONE,
        )
    return _ok(store.require_task(task_id).to_dict())


def block_task(
    store: KanbanStore,
    task_id: str,
    *,
    reason: str,
    author: str = "agent",
) -> dict[str, Any]:
    """Mark a task as blocked. ``reason`` is required and surfaces in
    the dashboard's red-border badge plus the Telegram notification."""
    if not isinstance(task_id, str) or not task_id:
        raise ToolError("task_id is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ToolError("reason is required (non-empty string)")
    task = store.get_task(task_id)
    if task is None:
        return _err(f"task not found: {task_id}", kind="TaskNotFoundError")
    try:
        store.update_task(task_id, status=STATUS_BLOCKED)
    except KanbanError as exc:
        return _err(str(exc), kind=type(exc).__name__)
    store.add_comment(
        task_id, author=author, body=f"[blocked] {reason.strip()[:4000]}",
    )
    run_id = task.current_run_id
    store.append_event(
        task_id, EVENT_BLOCKED,
        {"reason": reason.strip()[:1000]},
        run_id=run_id,
    )
    if run_id is not None:
        store.finalize_run(
            run_id, outcome="blocked",
            summary=reason.strip(),
            new_status=RUN_STATUS_BLOCKED,
        )
    return _ok(store.require_task(task_id).to_dict())


def unblock_task(
    store: KanbanStore,
    task_id: str,
    *,
    new_status: str = STATUS_READY,
    author: str = "user",
) -> dict[str, Any]:
    """Flip a blocked task back to ``new_status`` (default ``ready``,
    so the dispatcher re-picks). Used by the user from Telegram or
    the dashboard."""
    if not isinstance(task_id, str) or not task_id:
        raise ToolError("task_id is required")
    if new_status not in VALID_STATUSES:
        return _err(
            f"invalid status {new_status!r}",
            kind="InvalidStatusError",
        )
    task = store.get_task(task_id)
    if task is None:
        return _err(f"task not found: {task_id}", kind="TaskNotFoundError")
    if task.status != STATUS_BLOCKED:
        return _err(
            f"task {task_id} is not blocked (status={task.status})",
            kind="InvalidStateError",
        )
    try:
        store.update_task(task_id, status=new_status)
    except KanbanError as exc:
        return _err(str(exc), kind=type(exc).__name__)
    store.append_event(
        task_id, EVENT_UNBLOCKED,
        {"new_status": new_status, "by": author},
    )
    return _ok(store.require_task(task_id).to_dict())


def comment_on_task(
    store: KanbanStore,
    task_id: str,
    *,
    body: str,
    author: str = "user",
) -> dict[str, Any]:
    """Add a comment. Worker uses this for progress notes during long
    tasks; user uses this from the dashboard / Telegram for human
    annotations."""
    if not isinstance(task_id, str) or not task_id:
        raise ToolError("task_id is required")
    if not isinstance(body, str) or not body.strip():
        raise ToolError("body is required (non-empty string)")
    if store.get_task(task_id) is None:
        return _err(f"task not found: {task_id}", kind="TaskNotFoundError")
    comment = store.add_comment(
        task_id, author=author, body=body.strip()[:4000],
    )
    return _ok(comment.to_dict())


def heartbeat_task(
    store: KanbanStore,
    task_id: str,
    *,
    claim_lock: str,
    ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
    progress: str | None = None,
) -> dict[str, Any]:
    """Worker beats its heart. Bumps ``last_heartbeat_at`` and
    extends ``claim_expires`` by ``ttl_seconds``. Returns ``{ok:
    False, kind: "ClaimLost"}`` if another worker took over.

    ``progress`` (optional) appends a HEARTBEAT event with a short
    progress note so the dashboard can show "still working: <note>"."""
    if not isinstance(task_id, str) or not task_id:
        raise ToolError("task_id is required")
    if not isinstance(claim_lock, str) or not claim_lock:
        raise ToolError("claim_lock is required")
    if store.get_task(task_id) is None:
        return _err(f"task not found: {task_id}", kind="TaskNotFoundError")
    if ttl_seconds < 30:
        ttl_seconds = DEFAULT_CLAIM_TTL_SECONDS
    ok = store.heartbeat(task_id, claim_lock=claim_lock, ttl_seconds=ttl_seconds)
    if not ok:
        return _err(
            f"claim lost on task {task_id}; another worker holds it now",
            kind="ClaimLost",
        )
    payload = {"progress": (progress or "")[:500]} if progress else None
    store.append_event(task_id, EVENT_HEARTBEAT, payload)
    return _ok({"task_id": task_id, "ttl_seconds": ttl_seconds})


# ──────────────────────────────────────────────────────────────────
# Lifecycle (archive, assign, link)
# ──────────────────────────────────────────────────────────────────


def archive_task(store: KanbanStore, task_id: str) -> dict[str, Any]:
    """Soft-delete: status → ``archived``. Hidden from default board."""
    if not isinstance(task_id, str) or not task_id:
        raise ToolError("task_id is required")
    try:
        store.archive_task(task_id)
    except TaskNotFoundError:
        return _err(f"task not found: {task_id}", kind="TaskNotFoundError")
    return _ok({"task_id": task_id, "archived": True})


def assign_lane(
    store: KanbanStore,
    task_id: str,
    *,
    lane: str | None,
) -> dict[str, Any]:
    """Move a task to a different lane (or clear assignment with
    ``lane=None``). Validates the lane name; returns the updated task."""
    if not isinstance(task_id, str) or not task_id:
        raise ToolError("task_id is required")
    if lane is not None and lane != "":
        try:
            resolve_lane(lane)
        except LaneNotFoundError as exc:
            return _err(str(exc), kind="LaneNotFoundError")
    try:
        task = store.update_task(task_id, lane=lane or "")
        # update_task with lane="" stores empty string, but we want NULL.
        # The DB column accepts NULL; map empty → NULL here for cleanliness.
        if not lane:
            store._conn.execute(
                "UPDATE tasks SET lane = NULL WHERE id = ?", (task_id,),
            )
            task = store.require_task(task_id)
    except TaskNotFoundError:
        return _err(f"task not found: {task_id}", kind="TaskNotFoundError")
    return _ok(task.to_dict())


def add_link(
    store: KanbanStore,
    *,
    parent_id: str,
    child_id: str,
) -> dict[str, Any]:
    """Add a parent → child dependency."""
    if not isinstance(parent_id, str) or not parent_id:
        raise ToolError("parent_id is required")
    if not isinstance(child_id, str) or not child_id:
        raise ToolError("child_id is required")
    try:
        store.add_link(parent_id, child_id)
    except TaskNotFoundError as exc:
        return _err(str(exc), kind="TaskNotFoundError")
    except KanbanError as exc:
        return _err(str(exc), kind=type(exc).__name__)
    return _ok({"parent_id": parent_id, "child_id": child_id})


def remove_link(
    store: KanbanStore,
    *,
    parent_id: str,
    child_id: str,
) -> dict[str, Any]:
    """Remove a parent → child dependency."""
    if not isinstance(parent_id, str) or not parent_id:
        raise ToolError("parent_id is required")
    if not isinstance(child_id, str) or not child_id:
        raise ToolError("child_id is required")
    try:
        store.remove_link(parent_id, child_id)
    except KanbanError as exc:
        return _err(str(exc), kind=type(exc).__name__)
    return _ok({"parent_id": parent_id, "child_id": child_id})


__all__ = [
    "ToolError",
    "add_link",
    "archive_task",
    "assign_lane",
    "block_task",
    "comment_on_task",
    "complete_task",
    "create_task",
    "default_db_path",
    "heartbeat_task",
    "list_board",
    "list_events",
    "list_lanes_info",
    "list_runs",
    "open_default_store",
    "remove_link",
    "show_task",
    "unblock_task",
]
