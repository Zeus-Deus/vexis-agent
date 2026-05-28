"""Codemux orchestration watcher — bundled add-on.

This add-on owns:

  * The /codemux Telegram slash command (status of watched workspaces)
  * The watch_* control-socket dispatch handlers (consumed by
    vexis-watch)
  * The codemux-active-work system-prompt header injection
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


def register(ctx: PluginContext) -> None:
    """Wire the codemux add-on into vexis-agent."""
    # Skill auto-install — see docs/addons.md "register_skill".
    skill_file = ctx.addon_dir / "skills" / "codemux.md"
    if skill_file.is_file():
        ctx.register_skill(skill_file)

    # Watcher source registration into the AddonRuntime registry.
    # The CodemuxSource code now physically lives in this add-on
    # (mcp_client.py and source.py moved out of core/watcher/ in
    # this commit). The in-core ``register_source`` global registry
    # is still populated by WatcherController in main.py — the
    # B3-B4 cut flips that responsibility to the addon and lets us
    # delete the codemux auto-registration from WatcherController.
    # For now, ``ctx.register_watcher_source`` only updates the
    # AddonRuntime tracker (used by ``vexis-addons inspect`` and the
    # dashboard) — no conflict with the in-core registry.
    from vexis_agent.addons.codemux.mcp_client import CodemuxMcpClient
    from vexis_agent.addons.codemux.source import CodemuxSource

    _client = CodemuxMcpClient()
    ctx.register_watcher_source("codemux", CodemuxSource(_client))

    # NOTE: Still pending Phase B completion:
    #   * /codemux telegram command (moved from transports/telegram.py)
    #   * watch_register / watch_list / etc. dispatch handlers
    #   * codemux-watcher-poller background task
    #   * codemux-active-work header block
    #
    # Each one moves over once its hardcoded counterpart in
    # core/main.py or transports/telegram.py is reviewed and safe
    # to delete. Until then the hardcoded paths and this add-on
    # coexist — the watcher source is the only piece that's been
    # extracted cleanly so far.
    ctx.log.info("codemux add-on loaded (source-registered stage)")
