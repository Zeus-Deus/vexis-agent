"""Codemux-orchestration capability prompt block (issue #30).

`vexis-watch` registers terminal-attached agents and pings the
user when the inner agent goes idle.

Ownership note: the codemux *integration* ships as a bundled
add-on, but `vexis-watch` is a core console script and the watcher
controller is instantiated unconditionally in `main.py`, so this
how-to is documented in core to keep the assembled prompt
byte-identical to the pre-decomposition monolith. Moving it behind
the add-on's own prompt block (so it only appears when codemux is
enabled) is a deliberate follow-up — it would change the prompt
for codemux-disabled installs, which this PR intentionally avoids.
This module imports nothing from `vexis_agent.addons.*`.
"""

from __future__ import annotations

from vexis_agent.core.capabilities import register_capability_block


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


register_capability_block('codemux-orchestration', order=15, provider=codemux_orchestration_block)
