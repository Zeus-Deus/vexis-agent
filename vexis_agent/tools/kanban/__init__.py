"""Kanban tool surface — the operations Telegram, the dashboard, the
CLI, and (future) the MCP server wrapper all call into.

These are plain Python functions that take an open :class:`KanbanStore`
and return structured results. They handle validation, audit-event
emission, and the small bits of business logic that don't belong in
the bare DB layer (e.g. "completing a task also flips its run to done").

These functions are surfaced to workers as the ``vexis-kanban``
**CLI subcommands** (``vexis_agent/tools/kanban/cli.py``), NOT as MCP
tools — there is no ``kanban_complete`` tool. The abstract action name
from ``.plans/kanban-research.md`` §6, its ``vexis-kanban`` subcommand,
and the function here line up:

  * action ``kanban_create``     → ``vexis-kanban create``    → :func:`create_task`
  * action ``kanban_show``       → ``vexis-kanban show``      → :func:`show_task`
  * action ``kanban_list``       → ``vexis-kanban list``      → :func:`list_board`
  * action ``kanban_complete``   → ``vexis-kanban complete``  → :func:`complete_task`
  * action ``kanban_block``      → ``vexis-kanban block``     → :func:`block_task`
  * action ``kanban_unblock``    → ``vexis-kanban unblock``   → :func:`unblock_task`
  * action ``kanban_comment``    → ``vexis-kanban comment``   → :func:`comment_on_task`
  * action ``kanban_heartbeat``  → ``vexis-kanban heartbeat`` → :func:`heartbeat_task`
  * action ``kanban_archive``    → ``vexis-kanban archive``   → :func:`archive_task`
  * action ``kanban_link``       → ``vexis-kanban link``      → :func:`add_link`
  * action ``kanban_unlink``     → ``vexis-kanban unlink``    → :func:`remove_link`
  * action ``kanban_assign``     → ``vexis-kanban assign``    → :func:`assign_lane`

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
