"""Codemux source plugin.

A Codemux ``identifier`` is the terminal **session id**
(``session-NNNN``), NOT the workspace id. Codemux's MCP tools split
two concerns: a *workspace* is the user-facing container with a cwd,
title, and git info; a *session* is the underlying PTY a terminal
pane is attached to. ``terminal_read`` takes a session id, and the
session id is stable across the user switching the active workspace
in the Codemux UI — which is exactly what the watcher needs (poll
many workspaces' scrollbacks without yanking the user's focus).

Mapping workspace_id → session_id is a one-shot lookup at register
time (see :func:`resolve_workspace_to_session`). Codemux's MCP only
exposes pane/session metadata for the *currently active* workspace
(``workspace_info`` / ``pane_list`` don't take a workspace_id arg),
so resolution requires the target workspace to be active at the
moment of registration. The natural call-site — a Vexis brain
running inside the workspace it wants to monitor — satisfies that
constraint trivially; for the manual-from-a-shell case the user
focuses the workspace in Codemux before invoking ``vexis-watch``.

The plugin is constructed with a single :class:`CodemuxMcpClient`
shared across all watched workspaces — the MCP subprocess is the
expensive resource, the per-workspace state lives in the registry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from vexis_agent.core.watcher.mcp_client import (
    CodemuxMcpClient,
    CodemuxMcpError,
    CodemuxMcpUnavailable,
)
from vexis_agent.core.watcher.sources.base import (
    Source,
    SourceDescription,
    SourceUnavailable,
)

log = logging.getLogger(__name__)

# Terminal scrollback we ask for per poll. 200 lines matches the MCP
# tool's own default and is plenty for an idle-detection hash + a 20-
# line ``tail`` reply. Don't crank this — the bigger the read the
# more bytes have to round-trip through stdio per workspace per poll.
TERMINAL_READ_LINES = 200

# Markers Codemux returns in ``terminal_read``'s isError content when
# the session id we hold is no longer valid (pane closed, workspace
# closed, codemux restarted). String-match is brittle but it's the
# only signal the MCP exposes — there's no error code field — and a
# false positive only costs us a marked-dead agent that the user can
# re-register, not data loss.
_DEAD_SESSION_MARKERS = (
    "terminal session ",
    "no active terminal session",
    "session not found",
    "not found",
)


@dataclass(frozen=True)
class WorkspaceResolution:
    """Result of mapping a workspace_id → session_id.

    ``session_id`` is what the source plugin's ``identifier`` field
    needs; ``repo_path`` and ``title`` go into the description so
    the registry has friendly UI labels without a follow-up MCP call.
    """

    session_id: str
    workspace_id: str
    repo_path: Optional[str]
    title: Optional[str]


class CodemuxSource(Source):
    source_type = "codemux"

    def __init__(self, client: CodemuxMcpClient) -> None:
        self._client = client

    @property
    def client(self) -> CodemuxMcpClient:
        return self._client

    async def read_recent_output(self, identifier: str) -> bytes:
        try:
            data = await self._client.call(
                "terminal_read",
                {"session_id": identifier, "lines": TERMINAL_READ_LINES},
            )
        except CodemuxMcpUnavailable as exc:
            raise SourceUnavailable(str(exc)) from exc
        except CodemuxMcpError as exc:
            # Codemux returns isError when the session went away
            # (workspace closed, pane killed). Treat as unavailable so
            # the watcher can mark the agent ``dead`` cleanly. We
            # surface ALL CodemuxMcpError here because a backoff-
            # cooldown error or a transient stdio blip is also
            # "can't read right now," and the poller's death-or-not
            # decision flows from is_alive (called first) anyway.
            raise SourceUnavailable(str(exc)) from exc
        text = _extract_terminal_text(data)
        return text.encode("utf-8", errors="replace")

    async def is_alive(self, identifier: str) -> bool:
        """True iff the terminal session for ``identifier`` still exists.

        Cheapest accurate probe: call terminal_read with lines=1 and
        observe whether the MCP raised an isError "session not found"
        verdict. workspace_list isn't sufficient because workspaces
        can outlive their terminal sessions (think: user closed the
        last pane but kept the workspace open).
        """
        try:
            await self._client.call(
                "terminal_read",
                {"session_id": identifier, "lines": 1},
            )
            return True
        except CodemuxMcpUnavailable:
            # Whole MCP gone — every watched agent will look "dead",
            # which is technically right (we can't reach them) but
            # we don't want the poller to mass-reap a flapping
            # client. Conservative: keep them alive so the next
            # successful poll resurrects them.
            return True
        except CodemuxMcpError as exc:
            msg = str(exc).lower()
            return not any(marker in msg for marker in _DEAD_SESSION_MARKERS)

    async def describe(self, identifier: str) -> SourceDescription:
        last_line: Optional[str] = None
        try:
            output = await self.read_recent_output(identifier)
        except SourceUnavailable:
            output = b""
        if output:
            for raw in reversed(output.splitlines()):
                candidate = raw.decode("utf-8", errors="replace").strip()
                if candidate:
                    last_line = candidate
                    break
        # Per-poll repo_path / title would require switching the
        # active workspace (workspace_info doesn't take a target
        # arg). Skip — those fields are filled at registration via
        # :func:`resolve_workspace_to_session` and persisted on the
        # WatchedAgent record.
        return SourceDescription(last_line=last_line)


async def resolve_workspace_to_session(
    client: CodemuxMcpClient,
    workspace_id: str,
) -> WorkspaceResolution:
    """Resolve a Codemux workspace_id to its current terminal session_id.

    Codemux's MCP only exposes pane/session metadata for the
    *currently active* workspace. We confirm via ``app_status`` that
    the target workspace IS active, then pull ``workspace_info`` and
    extract the focused surface's root pane's session id. If the
    workspace isn't active, we surface a structured error rather
    than silently calling ``workspace_open`` (which would yank the
    user's UI focus — bad surprise).

    Raises :class:`SourceUnavailable` when:
      * The MCP isn't reachable.
      * The workspace_id isn't currently active.
      * The active workspace has no terminal sessions.
    """
    try:
        status = await client.call("app_status", {})
    except (CodemuxMcpUnavailable, CodemuxMcpError) as exc:
        raise SourceUnavailable(
            f"codemux app_status failed: {exc}"
        ) from exc
    active = status.get("active_workspace_id") if isinstance(status, dict) else None
    if active != workspace_id:
        raise SourceUnavailable(
            f"workspace {workspace_id!r} is not the active Codemux "
            f"workspace (active is {active!r}). Open the workspace "
            f"in Codemux first, then re-register — the watcher needs "
            f"to read the focused pane's session id one time at "
            f"registration."
        )
    try:
        info = await client.call("workspace_info", {})
    except (CodemuxMcpUnavailable, CodemuxMcpError) as exc:
        raise SourceUnavailable(
            f"codemux workspace_info failed: {exc}"
        ) from exc
    if not isinstance(info, dict):
        raise SourceUnavailable(
            "codemux workspace_info returned an unexpected shape"
        )
    surfaces = info.get("surfaces") or []
    session_id: Optional[str] = None
    for surface in surfaces:
        if not isinstance(surface, dict):
            continue
        root = surface.get("root") or {}
        if isinstance(root, dict) and root.get("session_id"):
            session_id = str(root["session_id"])
            break
    if not session_id:
        raise SourceUnavailable(
            f"workspace {workspace_id!r} has no terminal session "
            f"(no panes? open one before registering)"
        )
    return WorkspaceResolution(
        session_id=session_id,
        workspace_id=workspace_id,
        repo_path=info.get("cwd") if isinstance(info.get("cwd"), str) else None,
        title=info.get("title") if isinstance(info.get("title"), str) else None,
    )


def _extract_terminal_text(data) -> str:
    """``terminal_read`` returns ``{"data": "<scrollback>", …}`` on
    success; some Codemux builds return the raw scrollback string at
    top level. Handle both."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        # Field name observed against codemux 0.7.1.
        if isinstance(data.get("data"), str):
            return data["data"]
        for key in ("text", "output", "buffer", "content"):
            value = data.get(key)
            if isinstance(value, str):
                return value
    return ""
