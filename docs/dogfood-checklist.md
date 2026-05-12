# Phase C dogfood checklist

The 12 manual flows that gate declaring a brain ready for daily use
on a fresh install. Run on each brain (claude-code AND opencode) by
flipping `brain.kind` in `~/.vexis/config.yaml` and walking through
the list end-to-end.

A pass on all 12 for both brains is the gate for declaring Phase C
done. Steps 11 and 12 are non-negotiable: if the recursion guard
fails on opencode, the curator would loop on its own aux outputs the
moment learning is enabled (same failure mode as the May 2026 upstream
fanout bug). If a flow is fragile on one brain but solid on the
other, document the limitation in `docs/brains.md` and decide
whether to fix or punt per-flow.

> **Originally lived at `.plans/brain-abstraction-research.md` §7.**
> Relocated here at Phase C Day 7 so it's reproducible across
> machines, not just on the original author's local. The `.plans/`
> directory is gitignored.

---

## 1. Cold-boot turn

First `/start` after daemon launch; verify the brain spawns, replies,
and the session persists across `/status`.

## 2. Multi-turn conversation

Three back-to-back messages; verify session resume works (no "session
lost" errors, cumulative context).

## 3. Tool call

Ask the brain to do something tool-shaped ("read CAPABILITIES.md and
tell me what's in section 3"); verify the tool fires and the response
is grounded.

## 4. MCP server tool

Ask the brain to use omarchy-kb (`"what's the omarchy keybind for
fullscreen?"`); verify the MCP tool is reachable and returns content.

## 5. `/cancel` mid-turn

Long-running tool call (e.g. `"summarize every file in /home/zeus"`);
send `/cancel`; verify the subprocess dies, the chat is restored, and
the next user message works.

## 6. `/goal` set + continuation

`/goal write a haiku then improve it three times`; verify
continuation prompts fire, the goal judge decides done, no infinite
loop.

## 7. `/schedule` create + fire

`/schedule every 2m heartbeat`; verify the fire lands within the tick
window, the brain replies, the next fire happens too. Then
`/schedule clear <id>`.

## 8. Daemon restart mid-session

Send a message; immediately kill `vexis`; restart; send another
message; verify the session resumes (or rotates with a clean error
message — not a crash).

## 9. Bad auth

Temporarily revoke the brain's credentials; spawn a turn; verify
`BrainAuthRequired` is raised and surfaced as a friendly message in
Telegram (not a stack trace).

## 10. Bad install

Move the brain binary off PATH; restart vexis; verify startup logs
`BrainNotInstalled` with the install hint, AND the daemon doesn't
crash (it stays up so the user can fix it).

## 11. Curator-recursion guard against the brain's session storage

Spawn one aux call via:

```python
await brain.spawn_aux(
    prompt=CURATOR_REVIEW_PROMPT_PREFIX + "[test transcript here]",
    model_tier="small",
)
```

Wait for it to land in the brain's session storage:

- claude-code: a JSONL appears under
  `~/.claude/projects/<encoded-cwd>/`
- opencode: a row appears in the `session` table after the SQLite
  write commit.

Then run `list(brain.iter_session_metas())` and find the new session
id by `last_message_timestamp`. Call
`brain.is_brain_owned_session(<id>)`; assert `True`.

If `False`: the prefix-match needs adjustment — for opencode this is
most likely an unwrapping issue with the `data` JSON column on the
`message` table (the first user message's text part is nested inside
`message.parts[0].text`, not directly readable from the row's top
level). The fix lives in `OpenCodeBrain.is_brain_owned_session`'s SQL
row decoder.

## 12. `/cancel` arriving while the goal judge is awaiting

Phase B made `evaluate_after_turn` async, so the await on
`brain.spawn_aux(...)` yields back to the event loop. Pre-Phase-B
`subprocess.run` blocked the loop synchronously — this race could
not happen.

Verification flow:

1. Set a goal: `/goal write a haiku then iterate`
2. Wait for the brain reply to arrive and the post-turn judge to
   start spawning (~2-5 s into the spawn).
3. Send `/cancel`.

Expected outcome:

- The chat receives the `/cancel` ack ("OK, stopping").
- The goal flips to `paused` with `paused_reason="user-cancelled"`.
- **No continuation message arrives** ("↻ Continuing toward
  goal...").

The transport-layer reload-guard at `transports/telegram.py:1310-1329`
catches the late cancel by re-reading state from disk after
`evaluate_after_turn` returns and bailing if not active.

If a continuation DOES land, `evaluate_after_turn`'s save path is
overwriting the cancel-induced paused state — surface as a real bug
and fix at the GoalManager layer (likely needs a CAS-style read-and-
update inside `evaluate_after_turn`'s save).

This step replaces the pulled
`test_cancel_during_async_judge_drops_continuation` automated test
(commit `f07bdc7`'s comment block in `tests/test_aux_spawn_routing.py`
documents why); the dogfood exercise IS the verification path.

---

## Where to record results

Per-brain pass/fail goes inline in `docs/brains.md` under
"Known limitations" if any flow is fragile. A clean dogfood pass on
both brains is documented as a single line in the Day 8 release
notes and doesn't need its own entry — silence is the default.
