"""Codemux source plugin — dual-dialect.

Speaks BOTH codemux MCP dialects with autodetect on first call:

  * **Desktop** ``codemux mcp`` — the Tauri app's in-process MCP
    server. Workspaces *own* panes; panes hold terminal sessions.
    A workspace has a focused surface whose root pane carries a
    ``session_id``. The watcher reads via
    ``terminal_read({session_id, lines})``. ``app_status`` returns
    ``active_workspace_id`` + ``focused_pane_id`` + a one-line
    workspaces array. ``workspace_info`` takes no args and returns
    the *currently active* workspace's metadata + ``surfaces[]``;
    you cannot inspect a non-active workspace's panes over MCP.

  * **Remote** ``codemux-remote mcp`` — the standalone headless
    daemon (the binary that runs on a server / remote dev host).
    Workspaces and terminals are *independent* entities. A
    workspace is a worktree record (``id``, ``path``, ``branch``);
    terminals are bare PTYs spawned via ``terminal_spawn({cwd})``
    and tracked by ``terminal_id``. The watcher reads via
    ``terminal_read({terminal_id, max_bytes})``. ``app_status``
    returns ``host_id`` + ``mode`` + counts. ``workspace_info``
    takes ``{"id": ...}`` and returns ``{"workspace": {...}}``.

Dialect is detected by probing ``app_status`` once on the source
plugin's first call and cached for the process lifetime. Swapping
codemux binaries underneath a running vexis-agent requires a daemon
restart to repick — the lookup is not invalidated on respawn.

A Codemux ``identifier`` is whichever id the active dialect uses —
``session_id`` on desktop, ``terminal_id`` on remote. The Source
plugin abstraction treats it as opaque; the watcher's registry
just stores the string.

The plugin is constructed with a single :class:`CodemuxMcpClient`
shared across all watched workspaces — the MCP subprocess is the
expensive resource, the per-workspace state lives in the registry.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from vexis_agent.addons.codemux.mcp_client import (
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

# Dialect labels — used as opaque sentinels, never displayed.
DIALECT_DESKTOP = "desktop"
DIALECT_REMOTE = "remote"

# Terminal scrollback per poll (DESKTOP dialect). 200 lines matches
# the desktop MCP tool's own default and is plenty for an idle-
# detection hash + a 20-line ``tail`` reply. Don't crank this — the
# bigger the read the more bytes round-trip through stdio per poll.
TERMINAL_READ_DESKTOP_LINES = 200

# Terminal tail cap per poll (REMOTE dialect, in bytes). The remote
# daemon's PTY buffer tops out at 1 MiB; 16 KiB is well past the
# idle-detection hash window (we hash the tail, not the whole
# buffer) and leaves headroom for a 20-line ``tail`` reply with
# ANSI escapes. Bigger reads inflate per-poll stdio cost linearly.
TERMINAL_READ_REMOTE_MAX_BYTES = 16384

# Markers Codemux returns in ``terminal_read``'s isError content
# when the id we hold is no longer valid (PTY closed, daemon
# restarted, pane killed). A false positive only costs us a
# marked-dead agent the user can re-register — not data loss.
#
# The first entry is the remote dialect's structured error code
# (``{"kind": "not_found", "message": "<bare id>"}`` JSON-encoded
# inside the MCP text content). The remaining entries are
# desktop-dialect prose strings — they vary by codemux version
# but all carry the word "session" or "not found".
_DEAD_SESSION_MARKERS = (
    '"kind": "not_found"',
    "terminal session ",
    "no active terminal session",
    "session not found",
    "terminal not found",
)


@dataclass(frozen=True)
class WorkspaceResolution:
    """Result of mapping a workspace_id → terminal/session id.

    ``session_id`` is the historic field name; it now carries:
      * a desktop ``session_id`` when the active dialect is desktop;
      * a remote ``terminal_id`` when the active dialect is remote.

    Renaming the dataclass field would churn the registry's
    ``identifier`` plumbing for no behavioural gain — the source-
    plugin abstraction treats it as an opaque handle.

    ``repo_path`` and ``title`` go into the description so the
    registry has friendly UI labels without a follow-up MCP call.
    """

    session_id: str
    workspace_id: str
    repo_path: Optional[str]
    title: Optional[str]


def _classify_dialect(status: Any) -> str:
    """Decide whether a codemux MCP is the desktop or remote build
    based on its ``app_status`` reply shape.

    Discriminators (chosen because they NEVER appear in the other
    dialect, so misclassification is essentially impossible against
    any current codemux version):

      * ``host_id`` + ``mode`` → REMOTE (the headless daemon's
        identity + its ``"headless"`` mode flag).
      * ``active_workspace_id`` OR ``focused_pane_id`` → DESKTOP
        (the Tauri app's focus state — meaningless to a headless
        daemon that has no UI).

    On an unrecognised shape we default to REMOTE because that's
    where today's ``mcp-servers.yaml`` template points; a misrouted
    call on remote produces a clearer ``not_found`` error than a
    desktop misroute does (which silently returns the wrong
    workspace).
    """
    if isinstance(status, dict):
        if "host_id" in status and "mode" in status:
            return DIALECT_REMOTE
        if "active_workspace_id" in status or "focused_pane_id" in status:
            return DIALECT_DESKTOP
    return DIALECT_REMOTE


async def detect_dialect(client: CodemuxMcpClient) -> str:
    """Probe ``app_status`` and return ``DIALECT_DESKTOP`` /
    ``DIALECT_REMOTE``. Raises :class:`SourceUnavailable` when the
    MCP isn't reachable so callers can surface a clean error
    instead of crashing on a downstream KeyError.
    """
    try:
        status = await client.call("app_status", {})
    except (CodemuxMcpUnavailable, CodemuxMcpError) as exc:
        raise SourceUnavailable(
            f"codemux app_status failed: {exc}"
        ) from exc
    return _classify_dialect(status)


class CodemuxSource(Source):
    source_type = "codemux"

    def __init__(self, client: CodemuxMcpClient) -> None:
        self._client = client
        self._dialect: Optional[str] = None
        # Single-flight the first probe — overlapping callers must
        # not double-probe ``app_status`` (cheap, but pointless).
        self._dialect_lock = asyncio.Lock()

    @property
    def client(self) -> CodemuxMcpClient:
        return self._client

    @property
    def dialect(self) -> Optional[str]:
        """The detected dialect, or ``None`` if no call has been
        made yet. Public so the dashboard / control-socket
        diagnostics can surface "which codemux are we on" without
        forcing a probe."""
        return self._dialect

    async def _get_dialect(self) -> str:
        """Return the cached dialect, probing once on first call."""
        if self._dialect is not None:
            return self._dialect
        async with self._dialect_lock:
            if self._dialect is not None:
                return self._dialect
            dialect = await detect_dialect(self._client)
            self._dialect = dialect
            log.info("codemux MCP dialect detected: %s", dialect)
            return dialect

    def _read_args(self, identifier: str, dialect: str) -> dict:
        """Build the ``terminal_read`` arguments for ``dialect``."""
        if dialect == DIALECT_DESKTOP:
            return {"session_id": identifier, "lines": TERMINAL_READ_DESKTOP_LINES}
        return {"terminal_id": identifier, "max_bytes": TERMINAL_READ_REMOTE_MAX_BYTES}

    def _alive_probe_args(self, identifier: str, dialect: str) -> dict:
        """Cheapest possible ``terminal_read`` payload for ``dialect``."""
        if dialect == DIALECT_DESKTOP:
            return {"session_id": identifier, "lines": 1}
        return {"terminal_id": identifier, "max_bytes": 1}

    async def read_recent_output(self, identifier: str) -> bytes:
        try:
            dialect = await self._get_dialect()
            data = await self._client.call(
                "terminal_read", self._read_args(identifier, dialect),
            )
        except CodemuxMcpUnavailable as exc:
            raise SourceUnavailable(str(exc)) from exc
        except CodemuxMcpError as exc:
            # Codemux returns isError when the session/terminal
            # went away (pane killed, PTY closed, daemon restarted).
            # Treat as unavailable so the watcher marks the agent
            # ``dead`` cleanly. We surface ALL CodemuxMcpError here
            # because a backoff-cooldown error or a transient stdio
            # blip is also "can't read right now," and the poller's
            # death-or-not decision flows from is_alive (called
            # first) anyway.
            raise SourceUnavailable(str(exc)) from exc
        text = _extract_terminal_text(data)
        return text.encode("utf-8", errors="replace")

    async def is_alive(self, identifier: str) -> bool:
        """True iff the terminal/session for ``identifier`` still
        exists. Cheapest accurate probe: smallest ``terminal_read``
        the dialect allows. ``workspace_list`` isn't sufficient
        because workspaces can outlive their terminals (user
        closed the PTY but kept the workspace registered).
        """
        try:
            dialect = await self._get_dialect()
            await self._client.call(
                "terminal_read", self._alive_probe_args(identifier, dialect),
            )
            return True
        except (CodemuxMcpUnavailable, SourceUnavailable):
            # Whole MCP gone — every watched agent will look "dead",
            # which is technically right (we can't reach them) but
            # we don't want the poller to mass-reap a flapping
            # client. Conservative: keep them alive so the next
            # successful poll resurrects them. SourceUnavailable
            # appears when the dialect probe itself can't reach the
            # MCP — ``detect_dialect`` wraps the underlying
            # CodemuxMcpUnavailable for the resolver's benefit.
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
        # repo_path / title were captured at registration via
        # :func:`resolve_workspace_to_session` and persisted on the
        # WatchedAgent record. Per-poll re-lookup would cost two
        # extra MCP calls and add nothing — the worktree path
        # doesn't change once chosen.
        return SourceDescription(last_line=last_line)


async def resolve_workspace_to_session(
    client: CodemuxMcpClient,
    workspace_id: str,
) -> WorkspaceResolution:
    """Resolve a Codemux workspace_id to its current terminal handle.

    Dispatches on the codemux MCP dialect detected via
    ``app_status``:

      * **Desktop** — the workspace MUST be the currently active
        workspace in the Tauri UI (the MCP only exposes pane/
        session metadata for the active workspace). We confirm
        active via ``app_status.active_workspace_id``, pull
        ``workspace_info`` (no args), and extract the focused
        surface's root pane's ``session_id``.

      * **Remote** — workspaces and terminals are independent;
        match by worktree path. Pull the workspace's ``path`` via
        ``workspace_info({"id": ...})``, list terminals, pick the
        unique one whose ``cwd`` equals that path.

    Raises :class:`SourceUnavailable` on any unrecoverable failure
    (MCP unreachable, workspace not active / not found, terminal
    missing or ambiguous). The error message embeds an actionable
    next step so the CLI / dashboard can surface it verbatim.
    """
    dialect = await detect_dialect(client)
    if dialect == DIALECT_DESKTOP:
        return await _resolve_desktop(client, workspace_id)
    return await _resolve_remote(client, workspace_id)


async def _resolve_desktop(
    client: CodemuxMcpClient,
    workspace_id: str,
) -> WorkspaceResolution:
    """Desktop dialect: confirm the workspace is active, then read
    the focused surface's root pane ``session_id`` from
    ``workspace_info``."""
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


async def _resolve_remote(
    client: CodemuxMcpClient,
    workspace_id: str,
) -> WorkspaceResolution:
    """Remote dialect: pull the workspace's worktree ``path``, list
    terminals, pick the unique one whose ``cwd`` matches."""
    try:
        info = await client.call("workspace_info", {"id": workspace_id})
    except (CodemuxMcpUnavailable, CodemuxMcpError) as exc:
        # NotFound from the daemon comes through as a CodemuxMcpError
        # ("workspace_info failed: ..."); we collapse to
        # SourceUnavailable so the CLI doesn't have to branch on kind.
        raise SourceUnavailable(
            f"codemux workspace_info failed: {exc}"
        ) from exc
    if not isinstance(info, dict):
        raise SourceUnavailable(
            "codemux workspace_info returned an unexpected shape"
        )
    # Remote wraps the workspace under a "workspace" key
    # (``Ok(json!({"workspace": ws}))``). Accept a top-level shape
    # too — defends against a misrouted client (desktop dialect) so
    # the user gets a "no terminal in cwd" error instead of a
    # KeyError on ``path``.
    workspace = info.get("workspace") if "workspace" in info else info
    if not isinstance(workspace, dict):
        raise SourceUnavailable(
            "codemux workspace_info returned a workspace of unexpected shape"
        )
    repo_path = workspace.get("path")
    if not isinstance(repo_path, str) or not repo_path:
        raise SourceUnavailable(
            f"workspace {workspace_id!r} has no recorded worktree path"
        )
    try:
        listing = await client.call("terminal_list", {})
    except (CodemuxMcpUnavailable, CodemuxMcpError) as exc:
        raise SourceUnavailable(
            f"codemux terminal_list failed: {exc}"
        ) from exc
    terminals = _extract_terminal_list(listing)
    matches = [
        t for t in terminals
        if isinstance(t, dict) and t.get("cwd") == repo_path
    ]
    if not matches:
        raise SourceUnavailable(
            f"workspace {workspace_id!r} has no terminal in {repo_path!r}. "
            f"Spawn one with `terminal_spawn` (or open a shell in that "
            f"directory inside Codemux), then re-register — or pass "
            f"`--session-id <terminal_id>` to skip auto-resolution."
        )
    if len(matches) > 1:
        ids = ", ".join(str(t.get("id")) for t in matches)
        raise SourceUnavailable(
            f"workspace {workspace_id!r} has multiple terminals in "
            f"{repo_path!r} ({ids}); pass `--session-id <terminal_id>` "
            f"to disambiguate."
        )
    terminal_id = matches[0].get("id")
    if not isinstance(terminal_id, str) or not terminal_id:
        raise SourceUnavailable(
            f"codemux terminal_list returned a terminal with no id for "
            f"workspace {workspace_id!r}"
        )
    title = workspace.get("name")
    return WorkspaceResolution(
        session_id=terminal_id,
        workspace_id=workspace_id,
        repo_path=repo_path,
        title=title if isinstance(title, str) else None,
    )


def _extract_terminal_text(data) -> str:
    """``terminal_read`` returns ``{"data": "<scrollback>", …}`` on
    both dialects. Some legacy desktop builds returned the raw
    string at top level — handle that too for forward/back-compat."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("data"), str):
            return data["data"]
        for key in ("text", "output", "buffer", "content"):
            value = data.get(key)
            if isinstance(value, str):
                return value
    return ""


def _extract_terminal_list(data) -> list:
    """``terminal_list`` (remote dialect) returns ``{"terminals":
    [...]}``; accept a bare list for forward compatibility."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        terminals = data.get("terminals")
        if isinstance(terminals, list):
            return terminals
    return []
