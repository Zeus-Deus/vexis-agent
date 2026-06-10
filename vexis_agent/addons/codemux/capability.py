"""Codemux-orchestration capability prompt block.

Owned by the codemux add-on. Registered onto the core capability
registry via ``ctx.register_capability_block`` from the add-on's
``register(ctx)`` (see :func:`register_capability` below) rather than
shipped as a core builtin — this is the modular-addons home for what
used to leak from ``vexis_agent/tools/watch_capability.py``.

Because the block now lives behind the add-on hook, it only appears in
the assembled system prompt when the codemux add-on is loaded. On a
codemux-disabled install the "Codemux orchestration" section is simply
absent. Order 15 — its historical position in the assembled doc, kept
so codemux still sorts after every core block.

The text is the longer-form companion to ``skills/codemux.md``; keep
the two in sync with the actual codemux tool surface.
"""

from __future__ import annotations

from vexis_agent.core.addons.context import PluginContext


_CODEMUX_ORCHESTRATION_BLOCK = r"""## Codemux orchestration — `vexis-watch`

When you delegate work to a Codemux workspace (claude-code,
opencode, aider, anything launched in a pane), the inner agent runs
in a PTY that never exits — so `vexis-bg`'s exit-notification path
can't tell you know when it goes quiet. The watcher closes this gap.
Ships as a bundled add-on (`vexis_agent/addons/codemux/`); active
ONLY when the user has run `vexis-addons enable codemux` AND the
`codemux` MCP is wired into `~/.vexis/mcp-servers.yaml`. On hosts
without either, none of this applies.

To enrol a workspace for monitoring:

    vexis-watch register \
      --name my-build \
      --workspace <codemux-workspace-id> \
      --agent-kind claude-code \
      --idle-after 30s \
      --goal "<one-liner of what's running>"

Walk away. When the inner agent stops emitting bytes for the idle
threshold, the user gets one Telegram message naming the workspace,
the goal hint, and the last line of output. Inline replies the user
can send back: `tail <name>` (last 20 lines), `peek <name>` (asks
Vexis to summarise), `mute <name>` / `unmute <name>`, `unwatch
<name>`. `/codemux` lists all watched workspaces.

Registering does double duty: the idle ping AND visibility. A watched
workspace appears in the user's `/tasks` and `/status` replies and on
the dashboard status page. An unregistered delegation is invisible
everywhere — the user runs `/tasks` and reads "nothing running" while
your delegate is mid-flight. Register first, then tell the user the
work is running; pass `--goal` so the listing says what it's doing.

If you start a fresh session and the system prompt says "Active
Codemux work: N workspaces — run 'vexis-watch status' for details.",
that's the lead — call `vexis-watch status` before answering
"what's building?" or similar. Per-workspace state is deliberately
NOT in the prompt; the CLI is where you go for details.

The CLI emits JSON to stdout. When the Codemux MCP isn't wired the
daemon returns "Codemux MCP not configured" and the CLI exits 0 —
safe to call from any skill without a pre-check. See
`vexis_agent/addons/codemux/docs/codemux-watcher.md` for the full reference."""


def codemux_orchestration_block() -> str:
    """Idle-detection for terminal-attached agents (`vexis-watch`)."""
    return _CODEMUX_ORCHESTRATION_BLOCK


def register_capability(ctx: PluginContext) -> None:
    """Register the codemux-orchestration capability block on ``ctx``.

    Slots into the shared "Capabilities" order space at 15 — same
    position the block held when it was a core builtin. Duplicate
    name/order against core or another add-on raises ``AddonConflictError``.
    """
    ctx.register_capability_block(
        "codemux-orchestration",
        codemux_orchestration_block,
        order=15,
    )
