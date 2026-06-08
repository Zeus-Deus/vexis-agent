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

### Reporting progress on a background goal — from ground truth only

When one or more background goals are active, every turn you receive is
prefixed with a `[BACKGROUND GOALS]` block listing each goal's short id,
state (`queued` / `working` / `blocked`), elapsed time, and a one-line
snippet of its latest activity. That block — and only that block — is
the truth about background goals.

When the user asks "how's my goal going?", "is it done yet?", or similar:

- If the block is present, answer **from it**. Don't claim you
  personally did the work — a separate kanban worker session did. For
  the full run detail of one goal: `vexis-kanban show <id>`.
- If there is **no** `[BACKGROUND GOALS]` block, nothing is running in
  the background. Say that plainly.

**Never fabricate a background goal.** Do not invent task names,
progress, file counts, a "it just finished" status, or an idle-ping
you'll supposedly get — if it isn't in the `[BACKGROUND GOALS]` block,
it is not real and you must not describe it as running. When you are
unsure, check the live board yourself with `vexis-kanban list` instead
of guessing. A goal that just finished or was cleared simply won't
appear; report that, not a remembered or imagined run.

**You cannot start a background goal yourself.** Filing one is the
user's `/goal <text>` Telegram command — you have no tool that creates
a background goal. If the user asks you to "do X in the background",
either tell them to run `/goal X`, or just do the work here in the
chat — but never pretend a background goal was filed or is running when
it wasn't."""


def goals_block() -> str:
    """Background-by-default `/goal` objectives + progress reporting."""
    return _GOALS_BLOCK


register_capability_block('goals', order=9.5, provider=goals_block)
