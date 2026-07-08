"""Background-subagent lifecycle capability prompt block (issue #61).

The Agent tool's `run_in_background: true` subagents (the default since
CLI v2.1.198) are part of the CURRENT turn's `claude -p` process, not
durable background work. They outlive the reply only up to a bounded
wait after the turn's result, then get killed. This block teaches the
foreground brain that lifecycle so it routes long autonomous work to
kanban / `/goal` instead. Complements — does not duplicate — the
in-turn-parallelism note in `background_capability.py` (order 9); the
wait ceiling itself is owned by the brain (`brain.background_agent_wait`
→ `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`).
"""

from __future__ import annotations

from vexis_agent.core.capabilities import register_capability_block


_BACKGROUND_SUBAGENTS_BLOCK = r"""## Background subagents (Agent tool)

Each chat turn runs as its own headless `claude -p` process. When you
call the Agent tool with `run_in_background: true` (the default since
CLI v2.1.198), that subagent belongs to the current turn's process — it
outlives your reply only up to a bounded wait after the turn's result,
then it is killed. The ceiling is `brain.background_agent_wait` in
`~/.vexis/config.yaml` (seconds; default 1800, `0` = unlimited),
enforced via the `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS` env var. Treat
that window as a grace period for in-flight work to land, not as durable
background work: nothing tracks, notifies on, or relaunches a subagent
that runs past it.

### Where the work belongs

The choice is duration-based.

- **Long autonomous work** — multi-step, more than ~10 minutes, deploys,
  migrations — does NOT belong in a background subagent. File it as a
  kanban task, or suggest `/goal <text>`; those own their own process
  lifecycle and survive the turn.
- **Work you genuinely want inside this turn** — call the Agent tool
  with `run_in_background: false` so it finishes before you reply
  (bounded by the 30-minute turn timeout).
- **Reserve `run_in_background: true`** for work that comfortably fits
  inside the configured wait.

### When you do dispatch one

Tell the user roughly how long it has — the `brain.background_agent_wait`
window. If a later turn surfaces a task-notification saying the agent
stopped with no completion record, it was cut off at the ceiling: say so
plainly. Resume it only if the remaining work fits the window;
otherwise refile it as a kanban task."""


def background_subagents_block() -> str:
    """Agent-tool background subagents under the per-turn `claude -p` harness."""
    return _BACKGROUND_SUBAGENTS_BLOCK


register_capability_block(
    'background-subagents', order=9.25, provider=background_subagents_block
)
