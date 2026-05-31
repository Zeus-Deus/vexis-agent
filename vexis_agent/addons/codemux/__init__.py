"""Codemux orchestration watcher — bundled add-on.

This add-on owns:

  * The /codemux Telegram slash command (status of watched workspaces)
  * The watch_* control-socket dispatch handlers (consumed by
    vexis-watch)
  * The codemux-active-work system-prompt header injection
  * The codemux-orchestration capability prompt block (order 15)
  * The codemux watcher source plugin
  * The codemux-watcher-poller background task
  * The codemux.md skill auto-installed into every workspace

It is gated on the Codemux MCP being configured in
~/.vexis/mcp-servers.yaml (via the manifest's requires.mcp_servers).
``vexis-addons doctor`` surfaces the requirement when missing.

This phase of the migration keeps the heavy-lifting code in
``vexis_agent.core.watcher.*`` for backwards compatibility while
the codemux extraction lands incrementally. ``register(ctx)``
delegates to those modules but registers everything through the
PluginContext so the daemon's add-on-runtime path is exercised
end-to-end. A later commit physically moves the files into this
add-on directory and removes the core-watcher imports.
"""

from __future__ import annotations

from vexis_agent.core.addons import PluginContext

# Back-compat string the in-core watcher re-exports — kept so the
# legacy ``describe_unavailable_reason`` callers still print the
# wording users are used to.
UNAVAILABLE_MESSAGE = (
    "Codemux MCP not configured. Add the 'codemux' MCP via "
    "`vexis-agent mcp add` (or by editing ~/.vexis/mcp-servers.yaml) "
    "and restart the daemon to enable the watcher."
)


def register(ctx: PluginContext) -> None:
    """Wire the codemux add-on into vexis-agent.

    Full ownership of every codemux-specific surface (Phase B):

    1. Skill auto-install (``ctx.register_skill``)
    2. CodemuxSource registered with the in-core watcher so the
       polling loop can poll codemux workspaces (also tracked in
       the AddonRuntime via ``ctx.register_watcher_source``)
    3. ``/codemux`` Telegram slash command
       (``ctx.register_telegram_command``)
    4. ``watch_register`` control-socket dispatch handler
       (``ctx.register_dispatch_handler``) — owns the workspace_id
       → session_id resolver
    5. ``codemux-active-work`` system-prompt header
       (``ctx.register_system_prompt_block``)

    The watcher controller itself is now codemux-agnostic — it's
    instantiated unconditionally in main.py and consults the
    in-core source registry; this add-on supplies the only
    shipping source. Future watcher sources (raw PTY, tmux pane)
    plug in the same way through their own add-ons.

    The codemux-orchestration capability block (the system-prompt
    "Codemux orchestration" how-to) is registered here too via
    ``ctx.register_capability_block`` — so it appears in the assembled
    Capabilities section ONLY when this add-on is loaded, instead of
    leaking into core for every install.
    """
    from vexis_agent.addons.codemux.capability import register_capability
    from vexis_agent.addons.codemux.dispatch import (
        build_watch_register_handler,
    )
    from vexis_agent.addons.codemux.header import (
        build_codemux_header_provider,
    )
    from vexis_agent.addons.codemux.mcp_client import CodemuxMcpClient
    from vexis_agent.addons.codemux.source import CodemuxSource
    from vexis_agent.addons.codemux.telegram import build_codemux_handler
    from vexis_agent.core.watcher.sources.base import register_source

    # 1. Skill auto-install — see docs/addons.md "register_skill".
    skill_file = ctx.addon_dir / "skills" / "codemux.md"
    if skill_file.is_file():
        ctx.register_skill(skill_file)

    # 1b. Codemux-orchestration capability prompt block (order 15).
    register_capability(ctx)

    # 2. Watcher source registration. Two registries:
    #    - AddonRuntime tracker (vexis-addons inspect, dashboard)
    #    - In-core source registry (consumed by the polling loop)
    client = CodemuxMcpClient()
    source = CodemuxSource(client)
    ctx.register_watcher_source("codemux", source)
    register_source(source)

    # 3. /codemux Telegram slash command.
    ctx.register_telegram_command(
        "codemux",
        build_codemux_handler(ctx),
        menu_description="Status of watched Codemux workspaces",
    )

    # 4. watch_register dispatch handler — owns workspace_id
    #    resolution. Generic watch_list / unregister / mute / tail
    #    stay in main._build_dispatch because they're source-
    #    agnostic; the codemux add-on only owns ops that need
    #    codemux-specific MCP lookups.
    ctx.register_dispatch_handler(
        "watch_register",
        build_watch_register_handler(ctx),
    )

    # 5. System-prompt header block.
    ctx.register_system_prompt_block(
        "codemux-active-work",
        build_codemux_header_provider(ctx),
    )

    ctx.log.info("codemux add-on loaded")
