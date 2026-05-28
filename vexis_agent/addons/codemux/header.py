"""System-prompt header — "Active Codemux work: N workspaces".

Moved from ``WatcherController.header_block`` as part of the
Phase B extraction. Looks at the watcher's registry via the
runtime's service lookup and emits the same one-line header the
hardcoded path used to.
"""

from __future__ import annotations

from typing import Any, Optional


def build_codemux_header_provider(ctx: Any):
    """Return a ``() -> Optional[str]`` provider bound to the
    add-on's context. Called by the brain's prompt builder per
    session start.

    Context-budget guarantee: returns EXACTLY ONE line regardless
    of how many agents are registered. The brain learns the count
    and the CLI to query for details. Per-agent state does NOT
    enter the system prompt — that's what ``/codemux`` and
    ``vexis-watch status`` are for.
    """
    from vexis_agent.core.watcher.registry import WatchStatus

    def _header() -> Optional[str]:
        watcher = ctx.get_service("watcher")
        if watcher is None:
            return None
        active = [
            a for a in watcher.list_agents()
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

    return _header
