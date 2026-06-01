# Goals

`/goal <text>` lets you hand Vexis a multi-step task from your phone,
walk away, and come back to the result.

**As of v0.11, goals run in the background by default.** `/goal
<text>` files the goal as a kanban task: a background worker drives it
while your foreground chat stays completely free. You can keep
chatting, ask "how's my goal going?" at any time (Vexis sees the live
status and answers from it), check `/goal status`, or watch it on the
dashboard. See [Background goals](#background-goals-the-default) below.

`/goal --fg <text>` opts into the older **foreground** loop instead:
Vexis works the goal turn-by-turn inside the chat — after each brain
reply an auxiliary judge decides whether the goal is done; if not,
Vexis enqueues a continuation prompt and keeps going until it is, you
pause it, or the turn budget runs out. This is the "Ralph loop" port
from the upstream / Codex CLI 0.128.0, adapted to Vexis's Telegram +
`claude -p` shape. Flip the default permanently with
`goals.default_mode: foreground` in `~/.vexis/config.yaml`.

## Commands

| Command | What it does |
|---|---|
| `/goal <text>` | Start a goal. **Runs in the background by default** (filed as a kanban task; chat stays free). See [Background goals](#background-goals-the-default). |
| `/goal --fg <text>` | Force the in-chat **foreground** loop for this goal. |
| `/goal --bg <text>` | Force background even when `goals.default_mode: foreground`. |
| `/goal` (or `/goal status`) | Show current state — the foreground goal (if any) plus all active background goals. |
| `/goal pause` | *(Foreground)* Soft pause — the in-flight turn finishes, loop won't auto-continue after. |
| `/goal resume` | *(Foreground)* Resume the loop AND immediately advance one turn (resets the turn budget to a fresh 20). |
| `/goal clear` | *(Foreground)* Drop the goal. Subsequent messages are normal turns. Background goals are controlled via `/kanban` instead. |

Quick examples:

```
/goal ship the rocket and write the release notes   # background (default)
/goal --fg port the goal command to Vexis           # in-chat foreground loop
/goal status
/goal pause      # foreground loop only
/goal resume
/goal clear
```

## What you'll see

When Vexis sets a goal:

```
⊙ Goal set (20-turn budget): port the goal command to Vexis
I'll keep working until the goal is done, you pause/clear it, or the
budget is exhausted.
Controls: /goal status · /goal pause · /goal resume · /goal clear
```

Then the brain runs, replies, the judge fires, and either:

- **Goal done** — `✓ Goal achieved: <one-sentence reason>`
- **Continue** — `↻ Continuing toward goal (1/20): <one-sentence reason>`
  followed by the next brain turn (no "Picking up:" preview — the
  status line is enough).
- **Budget exhausted** — `⏸ Goal paused — 20/20 turns used. /goal resume
  to keep going, /goal clear to stop.`

`/status` mid-loop appends a one-liner like:

```
Working for 14s. used Bash; 1 tool used.
⊙ Goal (3/20 turns): port the goal command to Vexis
```

## Pause vs. cancel — what the difference means

There are three ways the loop stops, and they're not the same:

- **`/goal pause`** is a *soft* pause. The brain turn currently
  running finishes normally and you see its reply. The loop just
  doesn't auto-continue afterwards. The brain process is never
  killed. Use this when you want to read the latest reply
  carefully before deciding whether to keep going.
- **`/cancel`** is a *hard* cancel. It kills the in-flight brain
  process AND auto-pauses the goal, so you don't get a surprise
  continuation hours later when you send a casual follow-up. The
  goal stays paused; `/goal resume` re-engages.
- **`/goal clear`** drops the goal entirely. Subsequent messages
  are normal turns; the next `/goal status` says "no active goal".

If you really wanted to cancel: type `/cancel`. If you typed
`/goal cancel` (or `/goal stop`, `/goal abort`, `/goal kill`,
`/goal halt`) Vexis assumes a typo and replies with a hint
pointing at `/cancel` — it never sets a goal called "cancel".

## The 20-turn budget

Default: 20 turns. Override with `goals.max_turns: N` in
`~/.vexis/config.yaml`. Both real user turns AND auto-continuations
count against the budget — the budget is "max model spend on this
goal", not "max judge calls".

When the budget exhausts, Vexis pauses with a clear message:

```
⏸ Goal paused — 20/20 turns used. /goal resume to keep going,
/goal clear to stop.
```

`/goal resume` zeros `turns_used` so you get another full 20
turns. If you want a permanent higher ceiling, edit
`~/.vexis/config.yaml`.

## A worked example

```
you: /goal list the files in /tmp three different ways
vexis: ⊙ Goal set (20-turn budget): list the files in /tmp three
       different ways
       I'll keep working until …
vexis: Way 1 — `ls /tmp`:
       vexis-screenshot-1.png
       vexis-incoming-abc.png
       …
vexis: ↻ Continuing toward goal (1/20): one method shown, two more
       requested
vexis: Way 2 — `find /tmp -maxdepth 1 -type f`:
       /tmp/vexis-screenshot-1.png
       …
vexis: ↻ Continuing toward goal (2/20): two methods shown, one more
vexis: Way 3 — `echo /tmp/*` (shell glob expansion):
       /tmp/vexis-screenshot-1.png /tmp/vexis-incoming-abc.png …
       That's three distinct ways.
vexis: ✓ Goal achieved: three distinct file-listing methods produced
```

## Costs

Each judge call is a single sonnet `claude -p` invocation with a
~1 KB prompt and a ~50-token response. At sonnet pricing that's
roughly $0.005 per turn. Per goal at the default 20-turn ceiling:
**~$0.10 in judge calls**, on top of whatever the brain itself
costs (which depends entirely on what the goal is doing — file
listings are cheap, multi-step refactors are not).

In practice most goals terminate well before 20 turns. The budget
exists to bound runaway loops on judge false-negatives, not as a
typical-case ceiling.

## Persistence and restart

Goal state lives at `~/.vexis/goals.json`, keyed by your Claude
session UUID. Daemon restarts preserve the goal — the next message
you send picks the loop back up where it left off. Vexis does
**not** auto-fire on boot; restart safety is "next message
resumes", not "boot resumes". A goal that should genuinely stop
across a restart needs an explicit `/goal pause` or `/goal clear`
before the restart.

`/clear` (the session-clear command, NOT `/goal clear`) rotates
your active session UUID. The old session's goal record is
orphaned but stays on disk for audit. The new session has no goal
until you `/goal <text>` again.

## Notification policy

How chatty the **foreground** loop is on Telegram is controlled by
`goals.notify_policy` in `~/.vexis/config.yaml` (background goals use
kanban's own notification rules instead — see
[Background goals](#background-goals-the-default)):

| Value | Behaviour |
|---|---|
| `done_only` (default) | Suppress per-continuation pings. The chat stays quiet while the goal runs; you only see output on terminal verdicts: `✓ Goal achieved`, `⏸ Goal paused — N/N turns used`, or `⏸ Goal paused — judge model returning unparseable output`. On the terminal turn the brain's reply is flushed inline with the ✓/⏸ status so you still see the final output that produced the verdict. |
| `all` | Pre-v3d-UX behaviour. Every continue verdict pushes a `↻ Continuing toward goal (N/M): <reason>` line AND echoes the brain's reply text for that turn. Forensic but noisy — good for one-off debugging. |

```yaml
# ~/.vexis/config.yaml
goals:
  notify_policy: done_only  # the default; set to "all" for the old chatty mode
```

The policy reads disk per turn, so an edit takes effect at the
next continuation without a daemon restart.

## Background goals (the default)

`/goal <text>` files the goal as a single `ready`-state kanban task
in the `implementation` lane. The kanban dispatcher picks it up,
spawns a worker in a detached aux session, and runs it there. Your
foreground Telegram chat is left completely free — keep chatting,
ask for screenshots, kick off other goals.

### Asking how it's going

Every foreground turn, while a background goal is active, carries a
`[BACKGROUND GOALS]` block (goal id, state, elapsed time, latest
activity) that Vexis reads as ground truth. So you can just ask, in
plain language:

> **you:** how's my goal going?
> **vexis:** The "refactor the goal manager" goal is still working
> (about 4m in) — last activity: wrote the new continuation handler.

`/goal status` shows the same list explicitly, and the dashboard
kanban board renders every goal task live. For the full transcript
of one goal run, ask Vexis (it runs `vexis-kanban show <id>` for
you) or use the Telegram kanban commands directly:

```
/kanban show <task_id>     # full status + recent activity
/kanban list               # the whole board
/kanban complete <task_id> # mark done early if needed
```

### Notifications

Background goals don't use `goals.notify_policy` — they use kanban's
own rules (only ping on done / blocked / needs-input; see
`docs/kanban.md`). That's the point: the chat stays quiet while the
worker grinds, and you pull status when you want it.

### Foreground vs. background

`/goal --fg <text>` runs the in-chat continuation loop instead (see
the top of this doc); `goals.default_mode: foreground` flips the
default permanently. The two surfaces don't share state — a
foreground goal's pause/clear/resume don't affect a background
kanban task and vice versa, and you can run both at once.

## Configuration cheat sheet

`~/.vexis/config.yaml`:

```yaml
goals:
  # Default true — flipped on at v3d Day 4 release. Set to false
  # to silence the slash command and the post-turn hook entirely.
  enabled: true

  # Where `/goal <text>` runs when you don't pass --fg / --bg:
  #   background (default) — file as a kanban task; chat stays free,
  #                          progress via the [BACKGROUND GOALS] block
  #                          + /goal status + the dashboard.
  #   foreground           — in-chat continuation ("Ralph") loop.
  default_mode: background

  # Max continuation turns before auto-pause (foreground loop only).
  # /goal resume zeros the counter so you get another full budget
  # without editing this.
  max_turns: 20

models:
  # Default sonnet. The goal judge is strict and a false "done"
  # silently stalls the loop, so haiku is risky here. If you do
  # override, re-run the eval (below) before relying on it.
  goal_judge: sonnet
```

## Eval gate

The judge has a six-fixture release-gate eval at
`tests/test_goal_eval.py`. Run when prompts, the judge model, or
the §3 fold rules change:

```
pytest -m eval tests/test_goal_eval.py -v -s
```

Thresholds (per fixture):

- **(a) clear-done** — judge MUST return done.
- **(b) clear-continue** — judge MUST return continue.
- **(c) unachievable→done** — judge MUST return done with the
  block as the reason (the system prompt explicitly maps
  unachievable / blocked / needs-user-input → DONE).
- **(d) ambiguous** — advisory only. Verdict logged for review,
  not asserted.
- **(e) empty response → continue** — pre-spawn short-circuit,
  deterministic.
- **(f) error → continue** — fail-OPEN to continue when the
  subprocess errors. Deterministic.

100% on (a), (b), (c), (e), (f). LLM evals are noisy — a single
failed run on a borderline-passing fixture isn't conclusive,
re-run a couple of times before concluding. Approximate cost per
full run: ~$0.05.

## Diagnostic surfaces

- `/goal status` — current state, turns used, paused reason.
- `/status` — appends the goal summary to the existing brain-status
  reply when a goal is active.
- `~/.vexis/goals.json` — raw state file. One row per session UUID
  Vexis has ever had a goal under. Cleared / done records are
  retained for audit; only `status=active` rows count for
  `list_active`.

Full design: [`.plans/goal-command-research.md`](../.plans/goal-command-research.md).
