"""``/codemux`` Telegram slash command — moved from
``vexis_agent.transports.telegram`` as part of the Phase B
extraction.

The handler talks to the watcher controller via the AddonRuntime's
service registry (``ctx.get_service("watcher")``) rather than
holding a direct reference at registration time — the watcher is
constructed by main.py AFTER the add-on's ``register()`` runs.

Auth is handled by the runtime's standard add-on wrapper
(``TelegramTransport._wrap_addon_handler``); this module only
implements the success path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _fmt_elapsed_short(seconds: float) -> str:
    """One-token elapsed: ``19m`` / ``2h`` / ``5s``. Same vocab the
    transport's hardcoded version used, kept in lockstep so users
    don't see a copy that drifts."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h"
    return f"{int(seconds / 86400)}d"


def build_codemux_handler(ctx: Any):
    """Return the async ``/codemux`` handler bound to this add-on's
    context. ``ctx`` is the PluginContext passed to ``register()``;
    we capture it for the service lookup at call time."""

    async def _on_codemux(update, telegram_ctx):  # noqa: ANN001
        msg = update.message
        if msg is None:
            return
        watcher = ctx.get_service("watcher")
        if watcher is None:
            await msg.reply_text(
                "Watcher subsystem not wired in this build."
            )
            return
        agents = watcher.list_agents()
        if not agents:
            await msg.reply_text(
                "No Codemux workspaces watched. Vexis (or you) can "
                "register one via `vexis-watch register --name <h> "
                "--workspace <id> --agent-kind <kind>`."
            )
            return
        lines = [f"*Watched Codemux workspaces ({len(agents)}):*"]
        now = datetime.now(timezone.utc)
        for agent in agents:
            elapsed = "—"
            if agent.last_output_at:
                try:
                    last = datetime.fromisoformat(agent.last_output_at)
                    seconds = max(0, (now - last).total_seconds())
                    elapsed = _fmt_elapsed_short(seconds)
                except ValueError:
                    pass
            tag = "muted" if agent.muted else agent.status
            line = (
                f"`{agent.name}` — {agent.agent_kind} — {tag} — "
                f"last activity {elapsed} ago"
            )
            if agent.last_line:
                line += f"\n   `{agent.last_line[:140]}`"
            lines.append(line)
        lines.append(
            "Reply `tail <name>` / `peek <name>` / `mute <name>` / "
            "`unwatch <name>`."
        )
        await msg.reply_text("\n".join(lines), parse_mode="Markdown")

    return _on_codemux
