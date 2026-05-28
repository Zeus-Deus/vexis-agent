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

    # NOTE: This first cut of the codemux add-on is intentionally
    # minimal. Subsequent commits in Phase B add:
    #
    #   * watcher_source registration (CodemuxSource → register_watcher_source)
    #   * /codemux telegram command (moved from transports/telegram.py)
    #   * watch_register / watch_list / etc. dispatch handlers
    #   * codemux-watcher-poller background task
    #   * codemux-active-work header block
    #
    # Each of those gets added once the corresponding hardcoded path
    # in core/main.py and transports/telegram.py is reviewed and
    # safe to delete. Today the hardcoded paths still own the
    # behaviour; this add-on just installs the skill and proves the
    # discovery + load + register pipeline works end-to-end for a
    # bundled add-on.
    ctx.log.info("codemux add-on loaded (skill-only stage)")
