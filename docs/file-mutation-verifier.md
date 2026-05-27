# File-mutation verifier footer

Defends against silent write-failure hallucination ("I edited
foo.py" when no such edit happened) and gives the goal judge
ground truth about what the brain actually changed.

## TL;DR

Each brain turn snapshots the workspace before the turn fires
and again after it returns. The diff (`added` / `modified` /
`deleted` paths) is stashed per chat-id; on the **next** user
turn the handler prepends a `[turn-N verifier]` block to that
message before the brain sees it:

```
[turn-3 verifier] Files changed last turn:
  modified: vexis_agent/core/handler.py
  added:    tests/test_handler_inject.py
```

The model now sees its own ground truth one turn later and
will self-correct on the next reply if its prior message
overclaimed an edit that never landed.

## Where it lives

| Concern | Module |
|---|---|
| Snapshot walk (os.scandir + prune set) | `core/workspace_snapshot.py` |
| Pre/post hooks around the brain turn | `core/brain/claude_code.py`, `core/brain/opencode.py` |
| Diff stash + `peek_files_changed` accessor | `core/brain/base.py` |
| Footer injection into the next user message | `core/handler.py::_inject_context` |
| Goal judge integration | `core/goal_judge.py` |
| Telegram goal hook read | `transports/telegram.py` |

Prune set covers `.git`, `node_modules`, `__pycache__`, build
dirs, and dot-directories — the same shape `vexis-agent backup`
walks. Snapshot work runs through `asyncio.to_thread` so a
large workspace can't block the event loop.

## Performance

- ~1.5ms on the vexis-agent repo itself.
- Under 200ms on a synthetic 10k-file workspace (covered by
  `tests/test_brain_file_mutation_footer.py`).
- Two snapshots per turn (pre and post). Net overhead at typical
  workspace sizes is in the noise floor of an LLM round-trip.

If you have a workspace where the walk shows up in profiling,
disable the footer (see Configuration) rather than trying to
narrow the prune set — the prune set is a shared invariant with
backup, and divergence would surprise you.

## Goal-judge ground truth

When `/goal` is active, after each brain turn the goal judge
runs to decide whether the goal is satisfied. The judge now
pulls the per-chat diff via `brain.peek_files_changed()` and
folds it into the judge prompt as a `Files actually changed:`
ground-truth block. A brain reply that claims "fixed the bug
in handler.py" when the diff shows zero modifications gets
weighed against the diff, not the claim.

The goal hook in `transports/telegram.py` is the only public
reader of `peek_files_changed`. The accessor is non-draining
(reading it doesn't consume the stash) so the footer injection
on the next user turn still works.

## First-turn behaviour

The very first user turn in a fresh chat has no prior brain
turn to diff against, so the handler short-circuits and
suppresses an empty `[turn-0 verifier] Files changed last
turn:` block. The footer only appears starting on user turn 2.

## Configuration

```yaml
# ~/.vexis/config.yaml
brain:
  file_mutation_footer: true   # default. Set to false to disable.
```

The flag is re-read from disk on every turn, so flipping it
takes effect on the next user turn without a daemon restart —
same hot-reload shape as the tier maps. The example file at
`config.example.yaml` ships the key commented.

## When to turn it off

Three cases where disabling is reasonable:

1. **Massive workspaces.** > 100k files, fully outside the
   default prune set. Profile first; if you see consistent
   multi-hundred-ms snapshot times, disable.
2. **Read-only chat sessions.** A bot used purely for Q&A with
   no file edits will still pay the snapshot cost on every turn
   for a footer that's always empty. Cheap to leave on, but
   nothing breaks if you turn it off.
3. **Debugging an injection bug.** If you suspect the verifier
   block is confusing the model in a specific transcript,
   toggle off to confirm.

The goal judge degrades cleanly when the flag is off: the
ground-truth section is omitted from the judge prompt and the
judge falls back to evaluating the brain's reply on its own
claims.

## Recursion-guard interaction

The `[turn-N verifier]` marker is **not** in the recursion-
guard prefix set (`CURATOR_REVIEW_PROMPT_PREFIX`,
`GOAL_JUDGE_PROMPT_PREFIX`, `KANBAN_WORKER_PREFIX`,
`SUMMARY_PREFIX`). A user turn that carries a verifier footer
still looks like a normal foreground turn to the learning
curator's content-prefix filter, which is the right answer —
we want the curator to be able to review long edit-heavy
sessions for procedural lessons.

## Reference

- Tests: `tests/test_brain_file_mutation_footer.py` —
  snapshot correctness, prune set, perf budget, injection
  shape, goal-judge integration, hot-reload of the flag.
