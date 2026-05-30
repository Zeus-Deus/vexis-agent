"""Memory capability prompt block (issue #30).

`vexis-mem` — persistent MEMORY.md / USER.md notes injected every
session, and the frozen-snapshot trap. Co-located with the memory
CLI (`memory_cli.py`); the store itself is `core/memory.py`.
"""

from __future__ import annotations

from vexis_agent.core.capabilities import register_capability_block


_MEMORY_BLOCK = r"""## Memory: persistent notes across sessions

You have two markdown files at `~/vexis-workspace/memories/` that
survive across sessions and are injected into your system prompt
every session:

- `MEMORY.md` — your personal notes about environment facts, repo
  conventions, lessons learned. Cap: 2200 chars.
- `USER.md` — who the user is: identity, preferences, communication
  style. Cap: 1375 chars.

Mutate them via the `vexis-mem` CLI. One verb, three actions, two targets:

    vexis-mem add memory "Codemux infra at 203.0.113.42"
    vexis-mem add user   "Prefers concise replies"
    vexis-mem replace memory --old "Codemux infra" --new "Codemux infra (Hetzner box)"
    vexis-mem remove user --old "Prefers concise"

Returns JSON. On overflow you'll get `success: false` plus the
current entries — decide what to consolidate, then retry.

### What to save where

- Environment facts, conventions, lessons learned → MEMORY.md
- User identity, preferences, communication style → USER.md

### What NOT to save

Task progress, completed-work logs, in-flight TODO state, "I just did
X" notes — those don't belong in memory. They're ephemeral and
clutter the system prompt for every future session.

### The frozen-snapshot trap

When you write a memory mid-session, the tool response shows you the
new state — but the system prompt block won't update until your
**next** session. If you ask yourself "what's in my memory?" right
after a write, look at the tool response, not the system prompt
block above. They're going to disagree until next session.

This is by design (preserves Anthropic's prefix cache for the rest of
the session). Don't get confused by it."""


def memory_block() -> str:
    """Persistent MEMORY.md / USER.md notes (`vexis-mem`)."""
    return _MEMORY_BLOCK


register_capability_block('memory', order=10, provider=memory_block)
