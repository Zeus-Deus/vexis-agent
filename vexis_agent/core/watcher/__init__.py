"""Codemux orchestration watcher.

Public surface for the daemon:

  - :class:`WatcherController` — owns the registry + poller + the
    Codemux source plugin instance. Lifecycle is ``await start()`` /
    ``await stop()``. ``header_block()`` returns the (single)
    system-prompt line to inject at session spawn.

  - :func:`codemux_mcp_configured` — boolean. Reads the user's
    ``~/.vexis/mcp-servers.yaml`` and the built-in MCP detector
    list. Used by the daemon to decide whether to instantiate this
    package at all. Returning ``False`` means: don't construct
    WatcherController, don't register /codemux, don't inject
    the system-prompt header. Zero cost.

  - :func:`describe_unavailable_reason` — used by ``vexis-watch``
    when the daemon dispatches an op while the watcher is inactive,
    so the CLI can print a precise message instead of an
    indistinguishable "unknown op" error.

The Source plugin protocol lives in ``.sources``; new transports
(raw PTY, tmux pane) plug in there with zero changes to the
controller / registry / poller.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from vexis_agent.core.watcher.mcp_client import (
    CODEMUX_BINARY,
    CodemuxMcpClient,
)
from vexis_agent.core.watcher.poller import (
    DEFAULT_OSCILLATION_WINDOW_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    NotifyFn,
    WatcherPoller,
    render_idle_notification,
)
from vexis_agent.core.watcher.registry import (
    DEFAULT_IDLE_AFTER_SECONDS,
    DuplicateName,
    UnknownName,
    WatchStatus,
    WatchedAgent,
    WatcherRegistry,
    _utcnow_iso,
)
from vexis_agent.core.watcher.sources import (
    Source,
    SourceDescription,
    SourceUnavailable,
    get_source,
    list_source_types,
    register_source,
)
from vexis_agent.core.watcher.sources.codemux import (
    CodemuxSource,
    WorkspaceResolution,
    resolve_workspace_to_session,
)

log = logging.getLogger(__name__)

CODEMUX_MCP_NAME = "codemux"
UNAVAILABLE_MESSAGE = (
    "Codemux MCP not configured. Add the 'codemux' MCP via "
    "`vexis-agent mcp add` (or by editing ~/.vexis/mcp-servers.yaml) "
    "and restart the daemon to enable the watcher."
)


def codemux_mcp_configured() -> bool:
    """Daemon-startup check: is the Codemux MCP wired into vexis?

    Two paths qualify:
      * The user listed a server named ``codemux`` in
        ``~/.vexis/mcp-servers.yaml``.
      * A future built-in detector returns a spec named ``codemux``.

    Both are read via the same path the rest of vexis uses
    (``setup_wizard.detect_mcp_servers``) so this stays in lockstep
    with what the brain's own MCP wiring actually sees. We avoid the
    standalone PATH probe (``shutil.which('codemux')``) because the
    user may have the binary installed without WANTING the watcher
    on — the YAML wiring is the affirmative opt-in.
    """
    try:
        from vexis_agent.setup_wizard import detect_mcp_servers
    except Exception:  # pragma: no cover — defensive
        return False
    try:
        servers = detect_mcp_servers()
    except Exception:
        log.exception("detect_mcp_servers raised during watcher gate")
        return False
    return any(
        isinstance(spec, dict) and spec.get("name") == CODEMUX_MCP_NAME
        for spec in servers
    )


def describe_unavailable_reason() -> str:
    return UNAVAILABLE_MESSAGE


class WatcherController:
    """Daemon-side facade.

    Constructor parameters are conventional defaults that test code
    overrides freely; production wiring lives in
    ``vexis_agent.main._run``.
    """

    def __init__(
        self,
        *,
        registry: Optional[WatcherRegistry] = None,
        notify: Optional[NotifyFn] = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        oscillation_window_seconds: float = DEFAULT_OSCILLATION_WINDOW_SECONDS,
        codemux_mcp_client: Optional[CodemuxMcpClient] = None,
        register_codemux_source: bool = True,
    ) -> None:
        self._registry = registry or WatcherRegistry()
        self._codemux_client = codemux_mcp_client
        if register_codemux_source:
            client = self._codemux_client or CodemuxMcpClient()
            self._codemux_client = client
            register_source(CodemuxSource(client))
        self._poller = WatcherPoller(
            self._registry,
            notify=notify,
            poll_interval_seconds=poll_interval_seconds,
            oscillation_window_seconds=oscillation_window_seconds,
        )

    @property
    def registry(self) -> WatcherRegistry:
        return self._registry

    @property
    def poller(self) -> WatcherPoller:
        return self._poller

    def set_notify(self, notify: NotifyFn) -> None:
        self._poller.set_notify(notify)

    async def start(self) -> None:
        await self._poller.start()

    async def stop(self) -> None:
        await self._poller.stop()
        if self._codemux_client is not None:
            await self._codemux_client.close()

    # ---- header injection (Layer 2 of the spec) -----------------------

    def header_block(self) -> Optional[str]:
        """One-line system-prompt header, or None if registry is empty.

        Context-budget guarantee: this returns EXACTLY ONE line
        regardless of how many agents are registered. The brain learns
        the count and the CLI to query for details. Per-agent state
        does NOT enter the system prompt — that's what `/codemux` and
        `vexis-watch status` are for. See LAYER 2 in the spec.
        """
        active = [
            a for a in self._registry.list()
            if a.status != WatchStatus.DEAD.value
        ]
        if not active:
            return None
        n = len(active)
        noun = "workspace" if n == 1 else "workspaces"
        return (
            f"Active Codemux work: {n} {noun} — "
            f"run 'vexis-watch status' for details."
        )

    # ---- registration helpers used by the control-socket dispatch ----

    async def register_agent(
        self,
        *,
        name: str,
        source_type: str,
        identifier: str,
        agent_kind: str,
        chat_id: int,
        idle_after_seconds: int = DEFAULT_IDLE_AFTER_SECONDS,
        goal_hint: Optional[str] = None,
        repo_path: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> WatchedAgent:
        if source_type not in list_source_types():
            raise ValueError(
                f"unknown source_type {source_type!r}; "
                f"known: {list_source_types() or '<none>'}"
            )
        if get_source(source_type) is None:
            raise ValueError(f"no source plugin registered for {source_type!r}")
        agent = WatchedAgent(
            name=name,
            source_type=source_type,
            identifier=identifier,
            agent_kind=agent_kind,
            chat_id=chat_id,
            registered_at=_utcnow_iso(),
            idle_after_seconds=idle_after_seconds,
            goal_hint=goal_hint,
            repo_path=repo_path,
            workspace_id=workspace_id,
        )
        return await self._registry.register(agent)

    async def register_codemux_workspace(
        self,
        *,
        name: str,
        workspace_id: str,
        agent_kind: str,
        chat_id: int,
        idle_after_seconds: int = DEFAULT_IDLE_AFTER_SECONDS,
        goal_hint: Optional[str] = None,
    ) -> WatchedAgent:
        """One-shot registration that resolves workspace_id → session_id.

        Wraps the generic ``register_agent`` flow: do the live MCP
        lookup for the focused-pane session id, persist the
        WatchedAgent with that session id as ``identifier`` and the
        original workspace_id as metadata for ``/codemux`` display.

        Raises :class:`SourceUnavailable` (propagated from
        ``resolve_workspace_to_session``) when the target workspace
        isn't currently active in Codemux or has no terminal session.
        Callers higher up (the control-socket dispatch) translate it
        to a user-friendly ``WorkspaceNotActive`` payload.
        """
        if self._codemux_client is None:
            raise RuntimeError(
                "controller has no Codemux MCP client wired; "
                "instantiate WatcherController(register_codemux_source=True) "
                "(default) to enable workspace_id auto-resolution."
            )
        resolution = await resolve_workspace_to_session(
            self._codemux_client, workspace_id,
        )
        return await self.register_agent(
            name=name,
            source_type="codemux",
            identifier=resolution.session_id,
            agent_kind=agent_kind,
            chat_id=chat_id,
            idle_after_seconds=idle_after_seconds,
            goal_hint=goal_hint,
            repo_path=resolution.repo_path,
            workspace_id=resolution.workspace_id,
        )

    async def unregister_agent(self, name: str) -> WatchedAgent:
        return await self._registry.unregister(name)

    async def mute_agent(self, name: str, muted: bool = True) -> WatchedAgent:
        return await self._registry.set_muted(name, muted)

    def list_agents(self) -> list[WatchedAgent]:
        return self._registry.list()

    def get_agent(self, name: str) -> Optional[WatchedAgent]:
        return self._registry.get(name)

    async def tail(self, name: str, lines: int = 20) -> str:
        """Return the last ``lines`` lines of the agent's terminal scrollback.

        Used by Telegram inline ``tail <name>`` replies and the
        ``vexis-watch tail`` CLI. The read goes through the source
        plugin — so any source that wires up ``read_recent_output``
        Just Works for tail without extra code.
        """
        agent = self._registry.get(name)
        if agent is None:
            raise UnknownName(f"no watched agent named {name!r}")
        source = get_source(agent.source_type)
        if source is None:
            raise RuntimeError(
                f"source plugin for {agent.source_type!r} is not loaded"
            )
        output = await source.read_recent_output(agent.identifier)
        if not output:
            return ""
        text = output.decode("utf-8", errors="replace")
        out_lines = text.splitlines()[-lines:]
        return "\n".join(out_lines)


__all__ = [
    "CODEMUX_BINARY",
    "CODEMUX_MCP_NAME",
    "DEFAULT_IDLE_AFTER_SECONDS",
    "DEFAULT_OSCILLATION_WINDOW_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DuplicateName",
    "Source",
    "SourceDescription",
    "SourceUnavailable",
    "UnknownName",
    "UNAVAILABLE_MESSAGE",
    "WatchStatus",
    "WatchedAgent",
    "WatcherController",
    "WatcherPoller",
    "WatcherRegistry",
    "WorkspaceResolution",
    "codemux_mcp_configured",
    "describe_unavailable_reason",
    "get_source",
    "list_source_types",
    "register_source",
    "render_idle_notification",
    "resolve_workspace_to_session",
]
