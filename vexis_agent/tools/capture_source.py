"""Routing decisions for desktop screenshot / livestream sources.

Pure logic, no I/O. Given a user-supplied modifier (or ``None`` for
auto), the current task context, and the set of active sandbox
task-ids, returns a :class:`CaptureSource` saying where to capture
from and why — plus helpers for caption labels and lock-screen hints.

Routing rule (matches the design locked in with the user):

* Explicit ``host`` → host, always.
* Explicit ``sandbox`` (no id) → most-recently-active sandbox; error
  if there are none.
* Explicit ``sandbox:<id>`` → that sandbox specifically; error if it
  isn't in the active list.
* No modifier (auto):
    1. If the current task-id (typically ``VEXIS_SANDBOX_TASK_ID``) is
       in the active-sandbox list → that sandbox.
    2. Else → host.

The router never touches disk or runs subprocesses. Callers are
responsible for collecting ``active_sandbox_task_ids`` (see
:meth:`vexis_agent.tools.sandbox.Sandbox.list_all`) and the host's
lock state (see :mod:`vexis_agent.tools.session_lock`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


CaptureKind = Literal["host", "sandbox"]


class CaptureSourceError(ValueError):
    """Raised when the requested source can't be honoured.

    Examples:
    * user said ``sandbox`` but no sandboxes are active;
    * user said ``sandbox:foo`` but ``foo`` isn't an active sandbox;
    * the modifier string itself was malformed.
    """


@dataclass(frozen=True)
class CaptureSource:
    """Where a capture should pull pixels from, and why."""

    kind: CaptureKind
    task_id: str | None = None
    # Tag used by callers for telemetry and caption building. One of:
    #   "user-explicit"        — modifier was set explicitly
    #   "task-context"         — auto-routed to current task's sandbox
    #   "default-host"         — auto-routed to host (no sandbox in ctx)
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind == "sandbox" and not self.task_id:
            raise CaptureSourceError(
                "CaptureSource(kind='sandbox') requires a task_id"
            )
        if self.kind == "host" and self.task_id:
            raise CaptureSourceError(
                "CaptureSource(kind='host') must not carry a task_id"
            )


@dataclass(frozen=True)
class RouterContext:
    requested: str | None = None
    current_task_id: str | None = None
    # Most-recent first when the order matters (only used to pick the
    # default when modifier is bare ``sandbox`` and no current task).
    active_sandbox_task_ids: tuple[str, ...] = field(default_factory=tuple)
    host_locked: bool = False


def parse_source_modifier(raw: str | None) -> tuple[str | None, str | None]:
    """Parse a user-typed source modifier into ``(kind, task_id)``.

    Accepted forms (case-insensitive on the kind, case-sensitive on
    the task-id since docker container names are case-sensitive):

    * ``None`` / ``""`` → ``(None, None)`` — auto
    * ``"host"`` → ``("host", None)``
    * ``"sandbox"`` → ``("sandbox", None)`` — pick latest at resolve time
    * ``"sandbox:<id>"`` or ``"sandbox <id>"`` → ``("sandbox", "<id>")``

    Anything else raises :class:`CaptureSourceError`.
    """
    if raw is None:
        return (None, None)
    text = raw.strip()
    if not text:
        return (None, None)

    # Split on the first ':' or whitespace so both `sandbox:foo` and
    # `sandbox foo` work. Telegram users type the space form; CLI
    # users type the colon form.
    separator_index = -1
    for i, ch in enumerate(text):
        if ch == ":" or ch.isspace():
            separator_index = i
            break

    if separator_index == -1:
        head, tail = text, ""
    else:
        head, tail = text[:separator_index], text[separator_index + 1 :].strip()

    kind = head.lower()
    if kind == "host":
        if tail:
            raise CaptureSourceError(
                f"'host' source takes no task-id (got {tail!r})"
            )
        return ("host", None)
    if kind == "sandbox":
        return ("sandbox", tail or None)
    raise CaptureSourceError(
        f"Unknown source {raw!r}; expected 'host', 'sandbox', or 'sandbox:<task-id>'"
    )


def resolve_source(ctx: RouterContext) -> CaptureSource:
    """Apply the routing rule to a context. See module docstring."""
    kind, task_id = parse_source_modifier(ctx.requested)

    if kind == "host":
        return CaptureSource(kind="host", reason="user-explicit")

    if kind == "sandbox":
        if task_id is not None:
            if task_id not in ctx.active_sandbox_task_ids:
                raise CaptureSourceError(
                    f"No active sandbox with task-id {task_id!r}. "
                    f"Active sandboxes: "
                    f"{', '.join(ctx.active_sandbox_task_ids) or '(none)'}"
                )
            return CaptureSource(
                kind="sandbox", task_id=task_id, reason="user-explicit"
            )
        # Bare 'sandbox' modifier: pick latest active.
        if not ctx.active_sandbox_task_ids:
            raise CaptureSourceError(
                "No active sandboxes. Start one via /kanban or "
                "`vexis-sandbox start <task-id>`."
            )
        return CaptureSource(
            kind="sandbox",
            task_id=ctx.active_sandbox_task_ids[0],
            reason="user-explicit",
        )

    # Auto: prefer current-task sandbox if present, else fall back to host.
    if ctx.current_task_id and ctx.current_task_id in ctx.active_sandbox_task_ids:
        return CaptureSource(
            kind="sandbox",
            task_id=ctx.current_task_id,
            reason="task-context",
        )
    return CaptureSource(kind="host", reason="default-host")


def caption_label(source: CaptureSource) -> str:
    """Short emoji-prefixed tag for screenshot captions and dashboard
    tiles. Stable, advertised in :doc:`/docs/screenshot-routing`."""
    if source.kind == "host":
        return "Host"
    return f"Sandbox {source.task_id}"


def caption_hint(source: CaptureSource, ctx: RouterContext) -> str | None:
    """An optional one-line nudge appended after the caption.

    Currently only emitted for the "host capture, host is locked, user
    didn't explicitly ask for host" case — exactly the scenario where
    the user would otherwise wonder why they're staring at the lock
    screen.
    """
    if source.kind != "host" or not ctx.host_locked:
        return None
    if source.reason == "user-explicit":
        # They asked for the host knowing it could be locked. No nag.
        return None
    if ctx.active_sandbox_task_ids:
        return (
            "Host is locked. Reply `sandbox` to switch to "
            f"`{ctx.active_sandbox_task_ids[0]}`, "
            "or `sandbox:<task-id>` for a specific one."
        )
    return (
        "Host is locked. Start a sandbox via /kanban or "
        "`vexis-sandbox start <task-id>` to capture inside it."
    )
