"""Generic watcher subsystem.

Public surface for the daemon:

  - :class:`WatcherController` — owns the registry + poller.
    Lifecycle is ``await start()`` / ``await stop()``. Source
    plugins are registered via
    :func:`~vexis_agent.core.watcher.sources.base.register_source`;
    the codemux add-on does this in its ``register(ctx)``.

  - :func:`describe_unavailable_reason` — kept for back-compat
    with ``vexis-watch`` callers that print the legacy
    "Codemux MCP not configured" message; future revisions of
    the CLI surface a generic "no source registered" message
    instead.

The Source plugin protocol lives in ``.sources``; new transports
(raw PTY, tmux pane) plug in there with zero changes to the
controller / registry / poller. The codemux source itself is
now owned by the ``codemux`` bundled add-on under
``vexis_agent.addons.codemux``.
"""

from __future__ import annotations

import logging
from typing import Optional

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

log = logging.getLogger(__name__)

# Legacy back-compat constant — Phase B kept it for one release so the
# /codemux Telegram handler's "not configured" reply doesn't change
# wording mid-migration. The codemux add-on now owns the actual
# string; this re-exports it from the addon if importable, falls
# back to a generic message otherwise.
try:
    from vexis_agent.addons.codemux import UNAVAILABLE_MESSAGE  # noqa: F401
except (ImportError, AttributeError):
    UNAVAILABLE_MESSAGE = (
        "Watcher source 'codemux' is not registered. Enable the "
        "codemux add-on (``vexis-addons enable codemux``) and "
        "restart vexis-agent."
    )


def describe_unavailable_reason() -> str:
    """Legacy helper retained for back-compat with ``vexis-watch``
    callers that print a wordy "why isn't this working" message."""
    return UNAVAILABLE_MESSAGE


class WatcherController:
    """Generic registry + poller. No codemux-specific code.

    Source plugins are registered via
    :func:`~vexis_agent.core.watcher.sources.base.register_source`,
    typically from inside an add-on's ``register(ctx)``. Today the
    only shipping source is the ``codemux`` add-on; future sources
    (raw PTY, tmux pane, SSH session) plug in the same way.
    """

    def __init__(
        self,
        *,
        registry: Optional[WatcherRegistry] = None,
        notify: Optional[NotifyFn] = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        oscillation_window_seconds: float = DEFAULT_OSCILLATION_WINDOW_SECONDS,
    ) -> None:
        self._registry = registry or WatcherRegistry()
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

    # ---- header injection ----------------------------------------------
    # Per-source "active work" headers are owned by the corresponding
    # add-on via ``ctx.register_system_prompt_block``. The codemux
    # add-on supplies its own count-of-active-codemux-workspaces
    # block; future watcher source plugins do the same for their
    # types.

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
    "describe_unavailable_reason",
    "get_source",
    "list_source_types",
    "register_source",
    "render_idle_notification",
]
