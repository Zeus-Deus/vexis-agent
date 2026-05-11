# Kanban

Multi-task durable work queue. Vexis files tasks here when you ask
it to (`/kanban add ...` from Telegram or the dashboard quick-add)
or when you decide to break a goal into discrete steps; a background
dispatcher claims ready tasks and spawns one worker per task using
the brain. Workers report outcomes back through `vexis-kanban`
(shell) or directly via the action layer (Python).

This is the operational reference. For the design rationale (why
lanes instead of profiles, why goal/kanban stay separate, etc) see
`.plans/kanban-research.md`. For the in-tree TL;DR see CLAUDE.md
`## Kanban`.

## Concepts

**Task.** A unit of work. Has a title (required), optional body,
optional `lane`, status, priority, parent/child links, and a per-
task circuit breaker (`max_retries`).

**Status (column).** One of:

```
triage → todo → ready → in_progress → blocked → done
                                                 archived (hidden)
```

The dispatcher promotes `todo → ready` when **every parent is
done**. `ready → in_progress` happens when the dispatcher claims a
task and starts a worker run. Workers flip `in_progress → done` by
calling `kanban_complete`, or `→ blocked` by calling `kanban_block`.

**Lane.** vexis's lightweight replacement for Hermes profiles. A
lane is `(system_prompt_slice, skills, tier_override)` — same brain,
different hat. Defaults bundled in code: `research`,
`implementation`, `review`, `ops`, `triage`, `default`. Override
under `kanban.lanes:` in `~/.vexis/config.yaml`.

**Run.** One attempt at executing a task. Multiple per task on retry.
Carries the worker PID, claim lock, heartbeat, and final outcome
(`completed | blocked | crashed | timed_out | spawn_failed |
gave_up | reclaimed`).

**Event.** Append-only audit row for every state change. Both the
dashboard WebSocket and the Telegram notifier subscribe to this
single stream — keep one source of truth, two viewers.

## Telegram surface

```
/kanban                          board summary (counts per column)
/kanban list [lane]              list active tasks (optional filter)
/kanban show <id>                detail card + inline action buttons
/kanban add <title>              create in triage
/kanban add <title> @lane        create + assign lane
/kanban add <title> !            create directly in ready (skip triage)
/kanban add <title> @lane !      both
/kanban complete <id> [summary]  flip to done
/kanban block <id> <reason>      flip to blocked (reason required)
/kanban unblock <id>             flip blocked → ready
/kanban comment <id> <body>      add a comment
/kanban archive <id>             soft-delete (hidden from default board)
/kanban assign <id> <lane>       move between lanes
/kanban lanes                    list available lanes
```

The `/kanban show` reply carries inline buttons for `Complete`,
`Block`, `Comment`, and `Archive`. Tapping `Block` or `Comment`
captures your **next text message** as the input — type the reason
or comment body and send. Pending capture state is per-chat in
`ctx.user_data`; daemon restart clears it (silent fall-through to
the brain).

## Dashboard surface

`#kanban` tab. Six-column board (Triage → Todo → Ready → In Progress
→ Blocked → Done) with drag-drop between columns, lane filter chips
across the top, a quick-add bar that mirrors the Telegram `@lane`/`!`
suffixes, a goal-pad sidebar that read-only renders the active
`/goal`, and a task-detail modal with the same actions the Telegram
inline buttons trigger.

Live updates flow through a WebSocket at
`/api/v1/kanban/events?since=<cursor>&token=<bearer>`. The page
holds the cursor across reconnects (exponential backoff up to 30s)
and refreshes the board on every event burst (debounced to ~250ms
to absorb dispatcher-driven multi-event ticks).

REST routes (all under `/api/v1/kanban`, all bearer-auth):

- `GET /board` (`?lane=` `?status=` `?archived=`)
- `GET /lanes`
- `GET /tasks/{id}`
- `GET /tasks/{id}/events`
- `POST /tasks` — create
- `POST /tasks/{id}/status` — drag-drop column flip
- `POST /tasks/{id}/{complete,block,unblock,archive,assign,comment}`
- `POST /links` — add parent → child
- `POST /links/delete` — remove link

## Worker contract (kanban-worker)

When the dispatcher spawns a worker:

- env: `VEXIS_KANBAN=1`, `VEXIS_KANBAN_TASK_ID=<id>`,
  `VEXIS_KANBAN_LANE=<name>` — forensic markers; the recursion
  guard does NOT read them (it filters by **content prefix**).
- system-prompt-tail (after the brain's normal prompt + the lane's
  `system_prompt`): begins with `KANBAN_WORKER_PREFIX`. The
  learning curator's `list_eligible_sessions` skips JSONLs whose
  first user turn starts with this string so worker transcripts
  don't get scraped as "lessons."
- tools: lane.skills + the kanban_* MCP-shaped tools. Today the
  worker reaches them via the `vexis-kanban` shell CLI; a real
  MCP-server wrapper around the action layer is a future polish.

The worker MUST call **one of** `vexis-kanban complete <id>` or
`vexis-kanban block <id> <reason>` before exiting. If it exits
clean without doing so, the dispatcher records `outcome=gave_up`,
releases the claim, bumps `consecutive_failures`, and the task
returns to `ready` for retry (or auto-blocks if `failure_limit` is
hit).

## Configuration

`~/.vexis/config.yaml`:

```yaml
kanban:
  enabled: true                       # set false to disable entirely
  max_concurrent_workers: 2           # bound by your brain rate limit
  dispatch_interval_seconds: 60       # tick cadence; floor 5s
  failure_limit: 3                    # auto-block after N failures
  default_max_runtime_seconds: 900    # per-worker hard wall
  claim_ttl_seconds: 150              # heartbeat TTL on claim lock
  lanes:
    research:
      tier: medium
      skills: []
      system_prompt: |
        You research. ...
    # add or override per-lane definitions here
```

## Notification policy (Telegram)

The Telegram notifier subscribes to `task_events` and applies a
filter:

- `completed` of user-created tasks → notify
- `blocked` always → notify
- `failed | timed_out | crashed` always → notify
- everything else (including agent-spawned subtasks) → silent

Override per task with `/kanban watch <id>` (planned; v1 ships
without). The dashboard WebSocket has no filter — it streams
every event to the connected client which renders its own view.

## Goal-pad (read-only goal projection)

Goals and kanban are **two separate state machines** that share one
viewing surface. The dashboard's kanban page renders the active
`/goal` in a right-sidebar pad with status, turn count, last
verdict, and last reason. Mutation still routes through
`goal_manager.py`; the kanban code never touches goal state.

If you ever want `/goal` to **decompose into kanban tasks** (so the
planner's step list is visible), that's a future feature: a
`goal_kanban_planner` aux call would emit `kanban_create` calls.
**Not in v1.** See `.plans/kanban-research.md` §9 for the design.

## Pitfalls (read before tuning)

- **Daemon-must-be-alive UX.** Tasks added while the daemon is
  down sit in `ready` forever. The future `vexis-agent doctor`
  check should warn when no dispatcher is alive.
- **Prompt-cache cost.** Each worker spawn is a fresh subprocess
  → fresh KV cache. For 10 parallel tasks you pay the system
  prompt 10×. Mitigate by sharing a prefix across worker spawns
  (the lane's `system_prompt` is constant per lane); raise
  `max_concurrent_workers` only after measuring real cost.
- **Curator recursion.** If you rename `KANBAN_WORKER_PREFIX`,
  update both `core/transcripts.py:_is_curator_owned` and
  `core/brain/opencode.py:is_brain_owned_session` in the same
  edit. The CLAUDE.md `## Invariants` section pins this.
- **Cancellation.** `/cancel` while a worker is in flight gets
  cleaned up by the next stale-claim sweep (no immediate worker
  kill in v1). `consecutive_failures` is NOT bumped on
  user-cancellation.
