---
name: kanban-worker
description: Pitfalls, examples, and edge cases for the agent when running as a kanban worker (when $VEXIS_KANBAN_TASK_ID is set). Load this when you find yourself spawned by the dispatcher and you need the deeper playbook on summary/metadata shapes, block-reason patterns, retry diagnostics, and the do-NOT list. The basic lifecycle is in your system prompt; this is the reference for tricky situations.
---

# Kanban Worker — Pitfalls and Examples

You're seeing this skill because you're running as a kanban worker — `$VEXIS_KANBAN_TASK_ID` is set in your environment, and the dispatcher spawned this process to work on one specific card. Your job: orient → work → declare outcome (`vexis-kanban complete` OR `vexis-kanban block`) → exit cleanly.

## Always orient first

Before doing anything, read your card. The dispatcher might have claimed you while the user changed the body, blocked the task, or archived it:

```bash
vexis-kanban show "$VEXIS_KANBAN_TASK_ID" --json
```

If `status` came back `blocked` or `archived`, **stop**. You shouldn't be running. Exit without calling complete.

If you see `runs[]` with prior closed runs, **you're a retry**. The previous `outcome` + `summary` + `error` tell you what didn't work. Don't repeat that path.

## Workspace handling

Your card has `workspace_kind` and `workspace_path`:

| Kind | What it is | How to behave |
|---|---|---|
| `dir` (default) | An absolute path to a real directory; persistent | Treat it like long-lived state. Other workers will read what you write. |
| `scratch` | Fresh tmp dir, yours alone | Read/write freely; gets garbage-collected when the card is archived. |

`cd` into `workspace_path` first if it's set — your CWD on spawn is the daemon's workspace, not the card's.

## Good summary + metadata shapes

The `summary` field on `vexis-kanban complete` is what downstream cards (and humans) read. Be structured.

**Coding work:**

```bash
vexis-kanban complete "$VEXIS_KANBAN_TASK_ID" \
  --summary "shipped the rate limiter — token bucket keyed on user_id with IP fallback. 14 tests pass, 0 fail."
```

**Research work:**

```bash
vexis-kanban complete "$VEXIS_KANBAN_TASK_ID" \
  --summary "Reviewed 3 libraries: vLLM wins on throughput, SGLang on latency, TensorRT-LLM on memory. Recommendation: vLLM for our QPS profile."
```

**Review work (when you're a `review`-lane reader, NOT an editor):**

```bash
vexis-kanban complete "$VEXIS_KANBAN_TASK_ID" \
  --summary "Reviewed branch X. 2 blocking findings: (1) raw SQL concat in api/search.py:42, (2) missing CSRF on api/settings.py. Filed remediation cards via kanban_create."
```

Shape the summary so the next reader doesn't have to re-read your prose to act. One sentence + key facts.

## When to block instead of complete

Block when you genuinely need user input AND can't proceed without it. Bad block reasons get ignored:

- **Bad:** `"stuck"` — gives the human zero context.
- **Good:** one specific decision the user needs to make.

```bash
vexis-kanban comment "$VEXIS_KANBAN_TASK_ID" \
  "Full context: I have user IPs from Cloudflare headers, but some users sit behind NATs with thousands of peers. Keying on IP alone causes false positives."
vexis-kanban block "$VEXIS_KANBAN_TASK_ID" \
  "Rate limit key: IP (simple, NAT-unsafe) or user_id (requires auth, skips anon endpoints)?"
```

The block reason shows in the dashboard badge and the Telegram notification. The longer context lives in the comment for when the user opens the card.

## Heartbeats — only for long-running work

If your card has `max_runtime_seconds` and you'll exceed ~5 minutes, beat your heart so the dispatcher knows you're still alive (otherwise stale-claim cleanup will release the card mid-flight):

```bash
vexis-kanban heartbeat "$VEXIS_KANBAN_TASK_ID" \
  --claim-lock "$(vexis-kanban show $VEXIS_KANBAN_TASK_ID --json | jq -r .data.task.claim_lock)" \
  --progress "scanned 1.2M / 2.4M rows"
```

Good heartbeats name progress: `"epoch 12/50, loss 0.31"`, `"uploaded 47/120 videos"`. Bad: `"still working"`, empty notes, sub-second intervals. Skip heartbeats entirely for cards that finish in < 2 minutes.

## Retry diagnostics

If `vexis-kanban show --json` returns `runs[]` with prior closed runs, read the `outcome` field:

| Prior outcome | What likely happened | What to try |
|---|---|---|
| `timed_out` | Hit `max_runtime_seconds` | Chunk the work; bump max_runtime if the user OKs |
| `crashed` | OOM / segfault / unhandled exc | Reduce memory footprint; pick a smaller model tier |
| `spawn_failed` | Brain CLI rejected the model id, or auth | Don't retry blindly — `vexis-kanban block` and ask for human help |
| `gave_up` | Previous worker exited without declaring outcome | Read the prior summary if any; don't repeat that path |
| `failed` | Worker exited non-zero | Read the prior `error` field for stderr; address the root cause |
| `blocked` | Previous attempt blocked, has been unblocked | Read the unblock comment in the thread for context |

## Fan-out from a worker

If your card's work IS to file more cards (e.g. you're on the `triage` lane and your job is to route), use `vexis-kanban create` and capture the ids:

```bash
T1=$(vexis-kanban create "research the API surface" --lane research --json | jq -r .data.id)
T2=$(vexis-kanban create "implement the change" --lane implementation --parent "$T1" --json | jq -r .data.id)

vexis-kanban complete "$VEXIS_KANBAN_TASK_ID" \
  --summary "Triaged: spawned T1 (research) → T2 (implementation, depends on T1)."
```

Always capture return values from `vexis-kanban create`. Never invent task ids in your summary — phantom ids confuse downstream parsers.

## Do NOT

- **Don't modify files outside your `workspace_path`** unless the card body says to.
- **Don't create follow-up cards assigned to your own lane** when a different lane is the right home.
- **Don't `complete` a card you didn't actually finish.** Block it instead.
- **Don't paste task ids from earlier runs or invent ids in prose.** Capture from `vexis-kanban create` return values only.
- **Don't shell out to `vexis-kanban` if you're inside a containerized terminal** that doesn't have it on PATH. The MCP-tool variant of this surface (future work) will be the safer choice when it lands.

## State can change between dispatch and your startup

Between when the dispatcher claimed and when your process actually booted, the card may have been blocked, reassigned, or archived. **Always `vexis-kanban show` first.** If it reports anything other than `in_progress`, stop.

## Workspace may have stale artifacts

Especially on `dir` workspaces, files from previous runs can linger. Read the comment thread — it usually explains why you're running again and what state the workspace is in.

## CLI quick reference

| Verb | Purpose |
|---|---|
| `vexis-kanban show <id>` | Read card detail, comments, runs, events |
| `vexis-kanban list` | Board overview (counts + tasks) |
| `vexis-kanban complete <id> [--summary]` | Mark done; optionally drop a summary comment |
| `vexis-kanban block <id> <reason>` | Mark blocked; reason required |
| `vexis-kanban unblock <id>` | Flip blocked → ready |
| `vexis-kanban comment <id> <body>` | Add free-form context |
| `vexis-kanban heartbeat <id> --claim-lock X` | Extend the claim TTL on long-running work |
| `vexis-kanban create "title" --lane X [--parent Y]` | Spawn a new card |
| `vexis-kanban assign <id> <lane>` | Move between lanes |
| `vexis-kanban archive <id>` | Soft-delete |

All accept `--json` for structured output. Exit code is 0 on success and 1 on failure.
