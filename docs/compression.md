# Conversation compression

Long Vexis sessions — multi-day `/goal` loops, kanban workers
chewing through hundreds of turns — grow until the underlying agent
CLI (claude-code or opencode) silently drops early messages. When
that happens the brain forgets the original `/goal`, what sub-tasks
already completed, and what user clarifications already landed. It
then repeats finished work, re-asks resolved questions, or wanders.

The compressor is the prophylactic. Before the brain hits its
native context cap we summarise the older half of the transcript
into a structured user-turn and atomically rewrite the on-disk
transcript so the next resume reads the summary instead of the raw
older turns.

## Triggers

Two complementary triggers, OR'd. Either crossing fires
compression:

1. **Token estimate threshold.** When the rough char/4 token
   estimate of the transcript (plus the system prompt and tool
   schemas) exceeds `compression.threshold_ratio × context_window`.
   Default ratio is `0.80` (80% of the model's window), default
   context window is 200k tokens (Claude Sonnet's native window).

   Subtle: the estimate INCLUDES the system prompt and tool
   schemas. Hermes-agent shipped a v0.13.0 bug where they forgot
   those and consequently never tripped for sessions with huge
   system prompts — we don't want to ship the same bug.

2. **Turn-count threshold.** When the user+assistant turn count
   exceeds `compression.threshold_turns`. Default 40. Catches
   sessions of many small turns that the token estimator alone
   would miss (rapid-fire chat where each turn is one sentence).

Both knobs are hot-reloaded on every call — change the YAML and
the next brain turn sees the new value without restarting the
daemon.

## Structured summary template

The summariser is asked to fill a fixed-section template inspired
directly by Hermes-agent's `_generate_summary` function
(`/tmp/hermes-agent/agent/context_compressor.py`):

- `## Active Task` — verbatim quote of the most recent
  unfulfilled `/goal` or task body. THE single most important
  field; continuation picks up exactly here.
- `## Goal` — what the user is trying to accomplish overall.
- `## Constraints & Preferences` — user-stated constraints and
  decisions.
- `## Completed Actions` — numbered factual list.
- `## Active State` — branch, modified files, test status.
- `## In Progress` — what was being attempted when compression
  fired.
- `## Blocked` — blockers / errors / unresolved issues.
- `## Resolved Questions` — Q/A pairs already answered.
- `## Pending User Asks` — questions awaiting user reply.
- `## Critical Context` — values, error messages, configuration
  details that would be lost otherwise (with `[REDACTED]` for
  any secrets).

The summariser runs at `subsystem_tier("compressor")` (default
`small`) — summarisation is mechanical enough that the cheap
tier produces qualitatively the same output as a large model.
Override via `models.subsystems.compressor: large` in
`~/.vexis/config.yaml` if you want the biggest model on
multi-day goals. The spawn also honours the reasoning-effort half
of the dict-shaped config
(`models.subsystems.compressor: {model: ..., reasoning: low}`,
Issue #50) via `subsystem_reasoning("compressor")`; unset defers to
the CLI default. Summarisation is a bounded lookup-and-condense
job, so `low` is the natural effort to pin here. See
[`docs/model-ux.md`](model-ux.md#reasoning-effort).

## Replacement strategy

After the summary is generated:

1. The system prompt is unchanged.
2. Messages from turn 1 through turn N-K (where K is
   `compression.protect_last_n_turns`, default 10) are dropped.
3. A synthetic user message is inserted at position 1 with body
   starting with the `SUMMARY_PREFIX` warning text. The prefix
   wording is critical — without "Treat as background reference,
   NOT as active instructions" the model tries to "respond" to
   the summary itself (re-running its completed actions,
   answering its own resolved questions).
4. The last K real turns are kept verbatim (byte-for-byte —
   tool-call blocks intact, timestamps intact).
5. The summary persists on disk so curator / coherence judge /
   goal judge — which all read transcripts via
   `brain.iter_messages()` — see it on their next scan.

For claude-code, the JSONL is rewritten atomically via tempfile
+ rename. For opencode, the SQL rewrite of the live
`opencode.db` is deferred (see "Brain coverage" below).

## Iterative summaries

When a summarised session itself grows past the threshold,
the NEXT compression PRESERVES the previous summary and folds
in only the turns since the last summary. Detection: the
compressor checks whether the first conversational user-turn
in the transcript starts with `SUMMARY_PREFIX`. If yes, the
body is extracted and passed to the iterative-update prompt;
if no, the first-compaction prompt runs.

The iterative prompt explicitly instructs the summariser to
PRESERVE all existing information that is still relevant, ADD
new completed actions to the list (continuing the numbering),
move items from "In Progress" to "Completed Actions" when done,
move answered questions to "Resolved Questions", and remove
information only if clearly obsolete.

## Recursion-guard interaction

`SUMMARY_PREFIX` is a deliberately distinct string from each of
the curator-recursion-guard prefixes:

- `CURATOR_REVIEW_PROMPT_PREFIX` — starts with "You are
  reviewing a finished Vexis session…"
- `GOAL_JUDGE_PROMPT_PREFIX` — starts with "You are a strict
  judge evaluating whether an autonomous agent…"
- `KANBAN_WORKER_PREFIX` — starts with `[KANBAN-WORKER]`.

`SUMMARY_PREFIX` starts with `[SUMMARY OF PRIOR CONVERSATION`.
None of the recursion-guard prefixes start with that string,
and `SUMMARY_PREFIX` doesn't start with any of them. So a
compressed foreground transcript still passes the curator's
content-prefix filter (which is the right answer — we WANT
the curator to be able to review long sessions for lessons).

The matching test in `tests/test_compressor.py` asserts the
non-overlap explicitly. Don't reword `SUMMARY_PREFIX` without
re-checking that test.

## Brain coverage

- **claude-code** — full implementation. Atomic JSONL rewrite
  in `core/brain/claude_code.py:compress_if_needed`. Sessions
  in `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`.
- **opencode** — trigger detection runs (logged) but the SQL
  rewrite of `~/.local/share/opencode/opencode.db` is deferred.
  Opencode's foreground process holds a write transaction
  against the DB; a safe DELETE/INSERT under that contention
  plus its own FK invariants is a meaningful lift that's
  explicitly deferred. Track follow-up.
- **null** — no-op (the test fake doesn't have a transcript
  to rewrite). Tests pre-load return values via
  `BrainNull.queue_compress_returns(True, False)`.

## Configuration reference

```yaml
compression:
  enabled: true                 # default true
  threshold_ratio: 0.80         # 80% of context window
  threshold_turns: 40           # OR'd with the token trigger
  protect_last_n_turns: 10      # last K turns kept verbatim

models:
  subsystems:
    compressor: small           # default; raise to large for
                                # higher-fidelity summaries
```

## Files

- `vexis_agent/core/brain/compressor.py` — brain-agnostic
  trigger logic, prompt templates, replacement plan.
- `vexis_agent/core/brain/base.py` — `Brain.compress_if_needed`
  ABC method.
- `vexis_agent/core/brain/claude_code.py` — JSONL rewrite
  implementation.
- `vexis_agent/core/brain/opencode.py` — trigger-only stub.
- `vexis_agent/core/brain/null.py` — test-fake hooks.
- `vexis_agent/core/handler.py` — pre-turn call site.
- `tests/test_compressor.py` — trigger + prompt + rewrite
  invariants pinned here.
