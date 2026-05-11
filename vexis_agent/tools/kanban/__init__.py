"""Kanban tool surface — the operations Telegram, the dashboard, the
CLI, and (future) the MCP server wrapper all call into.

These are plain Python functions that take an open :class:`KanbanStore`
and return structured results. They handle validation, audit-event
emission, and the small bits of business logic that don't belong in
the bare DB layer (e.g. "completing a task also flips its run to done").

The MCP-tool naming convention from ``.plans/kanban-research.md`` §6
maps to module-level functions here:

  * ``kanban_create``      → :func:`create_task`
  * ``kanban_show``        → :func:`show_task`
  * ``kanban_list``        → :func:`list_board`
  * ``kanban_complete``    → :func:`complete_task`
  * ``kanban_block``       → :func:`block_task`
  * ``kanban_unblock``     → :func:`unblock_task`
  * ``kanban_comment``     → :func:`comment_on_task`
  * ``kanban_heartbeat``   → :func:`heartbeat_task`
  * ``kanban_archive``     → :func:`archive_task`
  * ``kanban_link``        → :func:`add_link`
  * ``kanban_unlink``      → :func:`remove_link`
  * ``kanban_assign``      → :func:`assign_lane`

Each function returns a JSON-serialisable dict suitable for direct
return from a REST endpoint, an MCP tool result, or a Telegram reply
formatter. They never raise on user error — the dict carries an
``error`` key with the message instead, so the caller can render a
clean message without exception handling.

The unhappy path uses ``ToolError`` only for caller-side mistakes
(wrong types, missing required args) — domain errors (TaskNotFound,
ClaimContention, etc) come back via the result dict.
"""
from vexis_agent.tools.kanban.api import (
    ToolError,
    add_link,
    archive_task,
    assign_lane,
    block_task,
    comment_on_task,
    complete_task,
    create_task,
    heartbeat_task,
    list_board,
    list_events,
    list_lanes_info,
    list_runs,
    open_default_store,
    remove_link,
    show_task,
    unblock_task,
)

__all__ = [
    "ToolError",
    "add_link",
    "archive_task",
    "assign_lane",
    "block_task",
    "comment_on_task",
    "complete_task",
    "create_task",
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
