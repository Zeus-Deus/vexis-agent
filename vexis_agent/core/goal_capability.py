"""Goals capability prompt block (issue #30 modular-docs model).

`/goal` — multi-step objectives the user hands off. As of v0.11 a goal
runs in the BACKGROUND by default (filed as a kanban task; the chat
stays free), and each foreground turn carries a live `[BACKGROUND
GOALS]` status block so the brain can report progress conversationally.
Co-located with the goal modules in `core/` (goal_manager, goal_state,
goal_background); registered into the shared capability registry.
"""

from __future__ import annotations

from vexis_agent.core.capabilities import register_capability_block


_GOALS_BLOCK = r"""## Goals — `/goal`

The user can hand you a multi-step objective with `/goal <text>`. As of
v0.11 a goal runs **in the background by default**: it's filed as a
kanban task and a background worker drives it, so the user's chat with
you stays free while it runs. The user can force the old in-chat loop
with `/goal --fg <text>`, or flip the default with
`goals.default_mode: foreground` in `~/.vexis/config.yaml`.

Controls (Telegram):

- `/goal <text>` — start a goal (background by default).
- `/goal --fg <text>` — run it in this chat instead (foreground loop).
- `/goal --bg <text>` — force background even if the default is foreground.
- `/goal status` — show the active goal(s), foreground and background.
- `/goal pause` · `/goal resume` · `/goal clear` — foreground-loop controls.

### Reporting progress on a background goal

When one or more background goals are active, every turn you receive is
prefixed with a `[BACKGROUND GOALS]` block listing each goal's short id,
state (`queued` / `working` / `blocked`), elapsed time, and a one-line
snippet of its latest activity. Treat that block as **ground truth**.

If the user asks "how's my goal going?", "is it done yet?", or similar,
answer from that block — don't claim you personally ran the work; a
separate kanban worker session did. For the full run detail of one goal:

    vexis-kanban show <id>

If there is **no** `[BACKGROUND GOALS]` block in your turn, no goal is
currently running in the background — say so plainly rather than
guessing or recalling a stale one. The block reflects live state each
turn, so a goal that just finished or was cleared simply won't appear."""


def goals_block() -> str:
    """Background-by-default `/goal` objectives + progress reporting."""
    return _GOALS_BLOCK


register_capability_block('goals', order=9.5, provider=goals_block)
