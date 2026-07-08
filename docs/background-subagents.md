# Background subagents (issue #61)

Vexis runs **one `claude -p` process per chat turn**. When the foreground
brain spawns a subagent via the Agent tool, recent claude-code versions
run that subagent **in the background by default** (`run_in_background`
became the default in CLI v2.1.198). The parent `claude -p` process emits
its terminal `result` event for the user's turn, then **lingers**, keeping
its process alive while the background subagent finishes.

Two problems fell out of that (issue #61):

1. **Silent death.** The headless CLI caps its end-of-run background wait
   at `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS` — default **600000 ms
   (10 min)** since CLI v2.1.182 — then SIGKILLs the process group. A
   longer-running autonomous subagent was killed mid-flight with no
   surfaced error.
2. **Held chat drain.** Vexis's streaming path kept blocking on the CLI's
   stdout until the process exited, so the chat drain (and the Telegram
   typing indicator) stayed pinned for the *entire* linger — up to the
   full ceiling — even though the user's reply was already done.

## The fix

### 1. Raise + configure the ceiling — `brain.background_agent_wait`

Every `claude -p` spawn (foreground `respond`/`astream` **and** every
`spawn_aux` call) now exports:

```
CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS = brain.background_agent_wait * 1000
```

`brain.background_agent_wait` is **seconds**, default **1800 (30 min)**,
`0` = unlimited (exported as the literal `"0"`). It reads
`~/.vexis/config.yaml` **per spawn**, so an edit hot-reloads at the next
turn — no daemon restart. Garbage / negative / boolean values fall back to
the default with a logged warning (a typo must never wedge the brain).

For `spawn_aux`, an explicit `env_overrides` entry for the same variable
still wins — the config value is seeded first, then `env_overrides` is
applied on top.

Config helper: `yaml_config.brain_background_agent_wait()`. Env writer:
`claude_code._apply_bg_wait_env(env)` (one source of truth, applied at all
three spawn sites).

### 2. Decouple the chat drain — linger detection + supervisor

In the streaming path (`ClaudeCodeBrain._attempt_astream`), once the
terminal `result` event has been seen, the per-line stdout read switches
from the full brain timeout to a short **linger grace**
(`_POST_RESULT_LINGER_GRACE_SECONDS`, 5s):

- **Common case (no background work):** stdout closes within milliseconds
  of the result event → EOF → the normal path runs **byte-for-byte** as
  before (same events, same order, same `finally` cleanup). The grace is
  pure slack that never elapses.
- **Lingering case:** the grace elapses with the process still alive → the
  turn is done but the CLI is holding a background subagent. The stream:
  1. flushes any batched trailing text,
  2. yields `{"type": "background_lingering", "wait_seconds": N}`,
  3. yields the canonical `{"type": "final", "text": ...}`,
  4. hands the still-running process to a **supervisor** asyncio task
     owned by the brain instance, and
  5. returns — so the `_attempt_astream` `finally` runs
     (`status_file.delete()` + `running_tasks.unregister()`), freeing the
     chat immediately. `/status` reflects idle; the drain moves on.

The **supervisor** (`_supervise_lingering`, tracked in
`self._linger_supervisors` keyed by `chat_id`):

- keeps draining stdout so the CLI's pipe writes never block on
  backpressure,
- awaits the process up to `brain.background_agent_wait` **measured from
  handoff** (`0` = unlimited),
- SIGTERM/SIGKILLs the process group on timeout (`_kill_group`), and
- logs the outcome at INFO — `finished` (completed) vs `killed`/`cancelled`
  — so a killed background run is greppable from the daemon logs alone.

`cancel_lingering_supervisors()` is the brain-close / daemon-shutdown hook:
it cancels every supervisor and its `finally` kills any still-running
process, so a clean shutdown leaves no orphan `claude -p`.

If a *subsequent* turn for the same chat also lingers while the previous
supervisor is still running, the brain logs a warning and lets both run.
This is safe because claude-code session transcripts are **append-only
JSONLs**: each `claude -p` owns its own turn's writes and neither forks the
other's session state, so concurrent writes are tolerable.

### 3. Surface it to the user

The handler forwards `background_lingering` as a distinct `("notice", …)`
tag (not `("tool", …)`, not folded into `("done", …)`). The Telegram
transport routes it through the Notifier:

> A background agent is still working — the chat is free; it has up to
> N minutes to finish.

(Or "no time limit" when `background_agent_wait: 0`.) The Notifier also
buffers a parallel context note, so the next brain turn knows a background
agent was still running. The web SSE route only maps
`chunk`/`tool`/`done`/`error`, so the unknown `notice` tag is silently
dropped there — graceful degrade, no browser change required.

## The buffered path keeps process-exit semantics

The non-streaming `respond()` path (used for goal-continuation turns and
when `telegram.streaming_enabled: false`) returns a single `str` and has
no event channel to emit `background_lingering` on. It still **injects the
env** like every other spawn, but it retains **process-exit semantics**:
it blocks on `proc.wait()` up to `BRAIN_TIMEOUT_SECONDS` (see below) and
then cleans up. In practice these turns are already background/low-priority
work where holding the drain matters less. Only the streaming path — the
default for Telegram and the sole path for the web chat — gets the linger
decoupling.

## Interaction with `BRAIN_TIMEOUT_SECONDS`

`BRAIN_TIMEOUT_SECONDS` (1800s) bounds the **foreground turn** — the time
from spawn until the result event. It is unrelated to
`background_agent_wait`, which bounds the **post-result linger** measured
from handoff. Once the supervisor takes over, the foreground turn is
already done, so the brain timeout no longer applies; the supervisor's own
`background_agent_wait` clock governs the background work.

## Recovery

The user isn't blocked while background work runs — the chat is free
immediately. When the background subagent finishes (or is killed at the
wait ceiling), the outcome is in the daemon logs, and the Notifier context
note means the next turn's brain has the "a background agent was still
working" context. To resume or check on long autonomous work, prefer
filing a **kanban task** or a background **`/goal`** (both are first-class,
persistent, and observable via the dashboard) rather than an in-chat
Agent-tool subagent. For an in-chat subagent you actually want to *watch*,
pass `run_in_background: false` so it runs inline within the turn.

## Pointers

- Config: `vexis_agent/core/yaml_config.py` →
  `brain_background_agent_wait()`.
- Lifecycle: `vexis_agent/core/brain/claude_code.py` →
  `_apply_bg_wait_env`, `_attempt_astream` (linger branch),
  `_handoff_lingering`, `_supervise_lingering`,
  `cancel_lingering_supervisors`.
- Forwarding: `core/handler.py` (`("notice", …)`) →
  `transports/telegram.py` (`_emit_stream_notice`,
  `_format_background_lingering_notice`).
- Tests: `tests/test_background_subagents.py`,
  `tests/test_telegram_streaming.py` (notice cases).
