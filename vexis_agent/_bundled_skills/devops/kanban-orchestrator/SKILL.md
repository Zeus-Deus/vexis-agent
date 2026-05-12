---
name: kanban-orchestrator
description: Decomposition playbook + lane-roster conventions + anti-temptation rules for the agent when routing work through the kanban board. Load this when the user asks to plan or break down a multi-step ask, when /goal would benefit from being decomposed into discrete cards, or when you're already running as a triage-lane worker and need to fan out.
---

# Kanban Orchestrator — Decomposition Playbook

## When to use the kanban board (vs `/goal` vs answering directly)

File a kanban task when ANY of these are true:

1. **Multiple lanes are needed.** Research + implementation + review = three workers, ideally in parallel.
2. **The work should survive a daemon restart.** SQLite-backed; the dispatcher resumes on next tick.
3. **The user might want to interject mid-flight.** Comment, block, redirect — kanban gives you the surface.
4. **Subtasks can run in parallel.** Fan-out for speed (bounded by `max_concurrent_workers`, default 2).
5. **Iteration is expected.** Reviewer lane loops on implementer output via parent/child links.
6. **The audit trail matters.** `task_events` rows persist forever.

Use **`/goal`** when it's a single multi-step task that benefits from continuous turn-by-turn iteration with one brain (it's the Ralph loop — same session, same cache, same context).

Just **answer directly** when it's a one-shot reasoning task. Kanban + goal both add latency (60s dispatcher tick, fresh subprocess, no shared cache); skip the overhead when nothing's gained.

## The anti-temptation rules

The agent's natural instinct is "I'll just do this quickly." That's almost always wrong when you're playing orchestrator. Enforce on yourself:

- **Don't execute the work yourself when you've decided the board is right.** If you find yourself shelling out to `vexis-kanban` to mark a task done that you also did the work for inside the same turn, you should have either (a) skipped the kanban step entirely, or (b) actually fanned it out.
- **For each concrete sub-step, create a kanban card and assign it to the right lane.** Every single time. Even when you "know" you could do it yourself.
- **If no lane fits, ASK the user.** Don't default to doing it yourself under "close enough."
- **Decompose, route, summarize. That's the whole job in this mode.**

## The standard lane roster (vexis defaults)

| Lane | Does | Tier |
|---|---|---|
| `research` | Reads sources, gathers facts, summarises with citations | medium |
| `implementation` | Writes code, runs tests, reports what changed | large |
| `review` | Reads work, critiques, identifies risk, gates approval (read-only) | medium |
| `ops` | Runs commands, checks service health, reports status | medium |
| `triage` | Classifies + routes; does NOT do the work itself | small |

Users may have customised these in `~/.vexis/config.yaml` under `kanban.lanes:`. Run `vexis-kanban lanes --json` to see what's actually configured before assuming a roster.

## Decomposition playbook

### Step 1 — Understand the goal

Ask clarifying questions if the ask is ambiguous. Cheap to ask now, expensive to spawn the wrong fleet of workers.

### Step 2 — Sketch the task graph in your reply

BEFORE creating any cards, draft the graph in plain prose so the user can correct it. Example for "Should we migrate to Postgres?":

```
T1  research        survey: Postgres cost vs current
T2  research        survey: Postgres performance vs current
T3  research        synthesise: read T1 + T2, write a 1-page recommendation   (parents: T1, T2)
T4  implementation  draft: turn the recommendation into a CTO-ready memo      (parents: T3)
```

Show this. Wait for the user to ack or correct it. Only then create cards.

### Step 3 — Create cards and link them

Use `vexis-kanban create` from the shell (the worker has shell access via the lane's tools). Capture the returned task ids:

```bash
T1=$(vexis-kanban create "survey: Postgres cost vs current" \
  --lane research --body "Compare 3-year infra + migration costs; sources: pricing pages, peer benchmarks." \
  --json | jq -r '.data.id')

T2=$(vexis-kanban create "survey: Postgres performance vs current" \
  --lane research --body "Compare query latency + throughput at our 500GB/10k QPS scale." \
  --json | jq -r '.data.id')

T3=$(vexis-kanban create "synthesise migration recommendation" \
  --lane research --body "Read findings from $T1 and $T2; produce 1-page recommendation with explicit trade-offs." \
  --parent "$T1" --parent "$T2" --json | jq -r '.data.id')

T4=$(vexis-kanban create "draft CTO memo" \
  --lane implementation --body "Turn $T3's recommendation into a 2-page CTO memo." \
  --parent "$T3" --json | jq -r '.data.id')
```

Parents gate promotion — children stay in `todo` until every parent reaches `done`, then auto-promote to `ready` on the next dispatcher tick.

### Step 4 — If you were spawned as a card yourself, complete it

If you're running because the dispatcher claimed YOU on a card (look at `$VEXIS_KANBAN_TASK_ID`), call `vexis-kanban complete` on YOUR id with a structured summary of what you fanned out:

```bash
vexis-kanban complete "$VEXIS_KANBAN_TASK_ID" \
  --summary "Decomposed into 4 cards: 2 parallel research surveys (T1, T2), 1 synthesis (T3), 1 memo draft (T4)."
```

### Step 5 — Tell the user what you queued

Plain prose. The user reads this in Telegram or the dashboard:

> Queued 4 cards:
> - T1 (research): cost survey
> - T2 (research): performance survey, parallel with T1
> - T3 (research): synthesises T1+T2
> - T4 (implementation): drafts the CTO memo
>
> The dispatcher will pick up T1+T2 on the next tick (~60s). T3 starts after both finish. You'll see updates in the dashboard live or get a Telegram ping when T4 completes.

## Common patterns

- **Fan-out + fan-in**: N research cards with no parents, one synthesis card with all of them as parents.
- **Pipeline**: `research → implementation → review`. Each stage's `--parent` is the previous card.
- **Same-lane queue**: Many cards all on `implementation` lane, no dependencies. Dispatcher serialises through the `max_concurrent_workers` cap.
- **Human-in-the-loop**: Any worker can `vexis-kanban block <id> "<reason>"` to pause and ask for input. User unblocks via Telegram or dashboard.

## Pitfalls

- **Don't pre-create the full graph if T3's shape depends on T1+T2's findings.** Let T3 be a "synthesise findings" card whose own first step is to read parent outputs and decide what comes next. Orchestrators can spawn orchestrators.
- **Reassign vs new card.** If a `review` lane card blocks with "needs changes," create a NEW card linked from the reviewer's card — don't re-run the same card with a sterner prompt. New card goes back to `implementation`.
- **Lane mismatch is silent contention.** Assigning a code-change card to `research` won't error; the worker will just do its best with the wrong system prompt. Verify lanes match the work.
- **Don't list `created_cards` you don't actually have ids for.** If a `vexis-kanban create` failed (non-zero exit), the card was NOT created — don't reference its hypothetical id in your summary or downstream parsers will choke.

## When the user asks for "all my tasks for today"

That's the morning-dump pattern. Don't decompose — just file each ask as its own card under the appropriate lane:

```bash
vexis-kanban add "research: vibe coding tools comparison" --lane research
vexis-kanban add "draft Q3 roadmap blurb" --lane implementation
vexis-kanban add "review the kanban PR feedback" --lane review !  # ! = skip triage
```

Then tell the user: "Queued N cards. The dispatcher works through them in priority+FIFO order; check the dashboard to follow along."
