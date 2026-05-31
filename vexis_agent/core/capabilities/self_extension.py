"""Self-extension capability prompt block (issue #30 follow-up).

Teaches the agent the correct seam to use when it needs a capability
it doesn't yet have: which extension mechanism (skill / MCP server /
in-process add-on) fits, and the hot-vs-restart cost of each. Like the
``builtin`` cross-cutting blocks this has no single owning tool — it's
framing that points the model at the cheapest seam — so it lives here
in the capabilities package. Assembled at order 1, right after the
stable-core block (0) and before the per-tool how-to blocks.

The longer-form procedural companion ships as the ``self-extension``
bundled skill (``vexis_agent/_bundled_skills/meta/self-extension/``),
which the brain reads on the next session.
"""

from __future__ import annotations

from vexis_agent.core.capabilities import register_capability_block


_SELF_EXTENSION_BLOCK = r"""## Extending yourself

When you need a capability you don't have, pick the cheapest seam that
fits, in this order: skill, then MCP server, then in-process add-on.
Don't reach for a restart-level change when a next-session or next-turn
one will do.

### Decision tree

- Need a new callable TOOL (a browser, a scraper, an API client)? Add
  or point at an MCP server. It's live on your NEXT TURN — no daemon
  restart.
- Learned a repeatable PROCEDURE or how-to? Write a SKILL: markdown
  under `<workspace>/skills/`. It's live on your NEXT SESSION — no
  restart.
- Need a Telegram command, a dashboard tab, a watcher source, or
  daemon-resident state? That requires an in-process ADD-ON, which
  loads once at daemon startup — it needs a RESTART (ask the user, or
  use `/restart`).

### Hot-vs-restart matrix

- MCP server (new or changed tool) → next turn.
- Skill (markdown procedure) → next session.
- In-process add-on (command / tab / watcher / daemon state) → restart.
- System-prompt / SOUL / MEMORY edits → next session, never mid-turn.

### Guardrails

- Never touch the recursion-guard prefixes or the curator
  content-prefix filter — they're what keep aux spawns from reviewing
  each other and looping.
- Respect aux tool allowlists. An aux subsystem only gets the narrow
  tool surface it was granted; don't try to widen it from a transcript.
- VERIFY a new tool before you swap out a working one: add it
  alongside, test it, then cut over. Never replace something that works
  with something untried.
- Prefer the cheapest seam that fits: skill > MCP > in-process add-on."""


def self_extension_block() -> str:
    """How to extend yourself: pick the cheapest seam (skill/MCP/add-on)."""
    return _SELF_EXTENSION_BLOCK


register_capability_block('self-extension', order=1, provider=self_extension_block)
