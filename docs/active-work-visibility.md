# Active-work visibility

The contract: **no user-facing surface may claim "nothing is
running" while any work registry has live work.**

## The bug this fixes

Vexis has three sanctioned ways for work to continue outside the
foreground chat turn, each with its own registry:

| Path | Spawned via | Registry |
|---|---|---|
| Background task | `vexis-bg spawn` | `$XDG_STATE_HOME/vexis-agent/background-tasks.json` |
| Watched delegation | Codemux workspace + `vexis-watch register` | `$XDG_STATE_HOME/vexis-agent/watcher-registry.json` |
| Background goal | `/goal <text>` (kanban worker) | `~/.vexis/kanban.db` |

Historically `/tasks` read only the first registry and `/status`
read the foreground status file + queue + goals. When the brain
*autonomously* delegated long work to a Codemux workspace and
registered a watch (its preferred path for terminal-attached
agents), the work was real and tracked — but `/tasks` replied
"No background tasks running." and `/status` said "Nothing
running", directly contradicting the brain's truthful "still
working, 9 minutes in" progress reports. Explicit "do it in the
background" requests were unaffected because the capability prompt
maps that phrasing to `vexis-bg spawn`, which was already visible.

The same class of bug was fixed for background goals in v0.12
(`fix(goals): make background goals run, report, and show on the
dashboard`); this extends the fix to the watcher registry and pins
the general contract.

## Surfaces and what they compose

- **Telegram `/tasks`** — vexis-bg tasks (running + recently
  finished), then the background-goals block
  (`core/goal_background.render_background_goals_status`), then the
  watched-workspaces block
  (`core/watcher/views.render_watched_status`). "No background
  tasks running." is returned ONLY when all three are empty.
- **Telegram `/status`** — foreground status file + queue depth +
  idle timestamp, then foreground goal line, background-goals
  block, watched-workspaces block. "Nothing running, sir." refers
  strictly to the foreground chat; live background work is listed
  below it.
- **Dashboard `/api/v1/status`** — `background_tasks`,
  `foreground_chats`, and `watched_agents`
  (`core/watcher/views.watched_work_payload`). The status page
  renders a "Watched workspaces" section when non-empty. Background
  goals live on the Goals page (`/api/v1/goals` → `background`).

Both watcher views are None-safe: a watcher-disabled deployment
(`watcher.enabled: false`) renders identically to "nothing
watched" with no call-site gating.

## The prompt-side contract

`vexis_agent/tools/background_capability.py` carries a
"visibility contract" section in the system prompt (mirrored in
the codemux orchestration block + `skills/codemux.md`):

1. Work that must outlive the reply MUST be tracked — `vexis-bg
   spawn` or `vexis-watch register`, registered BEFORE telling the
   user it's running.
2. In-turn parallelism (native subagents, background shells) is
   part of the reply, not a background task — never present it as
   one, never promise post-turn pings from it.
3. Progress claims must cite a tracked handle read this turn from
   `vexis-bg status` or `vexis-watch status`; otherwise the
   truthful answer is that nothing is running.

Rule 2 exists because the foreground brain runs with full native
tool access (`bypassPermissions`, no disallowed tools): it CAN
spawn claude-code sidechain subagents, and those are invisible by
design (`isSidechain: true` lines are filtered from
`iter_messages`). The prompt makes the brain treat them as what
they are — in-reply parallelism — instead of describing them to
the user as background work.

## Tests

`tests/test_active_work_visibility.py` — views, both Telegram
surfaces (including the "watched delegation must never be answered
with 'No background tasks running.'" regression), dashboard
payload. Capability prose is pinned by
`tests/data/capabilities_golden.md` (regenerate in the same PR as
any intentional edit).
