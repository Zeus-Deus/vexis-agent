# Streaming observability: tool spans + boundary text flush

The daemon's streaming path (`Brain.astream` on the claude-code brain,
surfaced over the dashboard's SSE chat route) carries two observability
signals so a slow turn is diagnosable instead of being one opaque
block:

1. **Timestamped tool-span events** — a `tool` frame when a tool call
   starts and a `tool_end` frame when it finishes, with per-tool
   duration.
2. **Boundary text flush** — inter-tool progress text ("Checking the OE
   catalog…") reaches the stream at the tool boundary instead of at
   turn end, even when the model batches it.

Both are brain-event-contract level: every transport benefits and no
consumer code is model-specific. A matching `tool-span` log line is
written for every tool on both the streaming (`astream`) and buffered
(`respond`) paths, so a slow turn is attributable from the daemon logs
alone regardless of which transport drove it.

## Event vocabulary

`Brain.astream` yields a discriminated union: `str` text deltas and
`dict` observability events. The SSE route serialises each dict verbatim
as one `data: {…}` frame; unknown `type` values are ignored by
consumers (the frontend and Telegram already drop them), so new event
types are additive.

### `tool` — a tool call started

| Field    | Type              | Meaning                                             |
| -------- | ----------------- | --------------------------------------------------- |
| `type`   | `"tool"`          | Discriminator.                                      |
| `name`   | `str`             | Tool name (`Read`, `Bash`, `mcp__browser__…`, …).   |
| `target` | `str \| null`     | Best-effort file path / command / pattern, or null. |
| `id`     | `str \| null`     | The brain's opaque per-call id (`toolu_…`).         |
| `ts`     | `int`             | Wall-clock start, epoch milliseconds.               |

### `tool_end` — that tool call finished

| Field         | Type                        | Meaning                                              |
| ------------- | --------------------------- | ---------------------------------------------------- |
| `type`        | `"tool_end"`                | Discriminator.                                       |
| `name`        | `str`                       | Replayed from the matching start (no join needed).   |
| `target`      | `str \| null`               | Replayed from the matching start.                    |
| `id`          | `str`                       | Same id as the start it closes.                      |
| `ts`          | `int`                       | Wall-clock end, epoch milliseconds.                  |
| `duration_ms` | `int`                       | Tool runtime, from a **monotonic** clock.            |
| `status`      | `"completed"` \| `"error"`  | `"error"` when the tool result was flagged an error. |

`ts` is wall-clock (epoch ms) purely so an event can be lined up against
the timestamped daemon logs. `duration_ms` is measured from
`time.monotonic()` deltas, NOT from `ts` arithmetic — a wall-clock jump
mid-call (NTP step, suspend/resume) can't corrupt it.

The `status` vocabulary (`"completed"` / `"error"`) is the same one the
`ToolEnd` dataclass in `core/brain/base.py` uses, but the field names
are NOT the dataclass fields — the wire dicts carry `id`/`name`/
`target`/`ts`/`duration_ms` where the dataclasses use `tool_id`/
`input`/`output`/`error`. The field tables above are the wire contract;
the brain yields plain dicts (consumers `isinstance`-check `dict` vs
`str`), never the dataclasses.

## Worked example: one tool-using turn over SSE

A turn where the model writes a progress marker, reads a file, then
answers produces this frame sequence:

```
data: {"type":"chunk","text":"Checking the OE catalog…"}

data: {"type":"tool","name":"Read","target":"catalog.json","id":"toolu_01A","ts":1751...040}

data: {"type":"tool_end","name":"Read","target":"catalog.json","id":"toolu_01A","ts":1751...185,"duration_ms":145,"status":"completed"}

data: {"type":"chunk","text":"The part number is 55-2137."}

data: {"type":"done","reply":"Checking the OE catalog…The part number is 55-2137."}
```

The progress text always precedes its `tool` frame. On a **batched**
model turn (claude-sonnet-5 in particular delivers inter-tool text as
a buffered block with no token deltas) the daemon flushes the
`"Checking the OE catalog…"` chunk immediately before emitting the
`tool` start frame, because the text block's per-block `assistant`
event precedes the tool_use block's event — see "Boundary text flush"
below. The wire order is therefore the same whether the model streamed
the text or batched it.

## Span log line

Every tool logs one line at INFO on close, on both the streaming and
buffered paths. Greppable on `tool-span`:

```
tool-span chat=-1 tool=Read duration_ms=145 status=completed target=catalog.json
```

`target` is free text — a Bash target is the whole command line,
spaces, `=` signs and all — so it is deliberately the LAST field:
every fixed-vocabulary key a parser matches on (`duration_ms=`,
`status=`) comes first, and a key=value tokenizer should treat
everything after `target=` as the value.

`chat=-1` is the web-chat id; a Telegram chat logs its numeric id.
A tool whose result never arrived (turn cancelled/timed out mid-tool)
logs `tool-span unclosed …` at DEBUG instead and emits no `tool_end`
event — there's no honest duration to report.

## Attributing a slow turn

With the frames + logs, the wall time of a slow turn decomposes cleanly:

- **Time to the first `chunk`/`tool` frame** = prompt processing +
  first-token latency (nothing you can shave locally).
- **A span's `duration_ms`** (or its `tool-span` log line) = that tool's
  own runtime — e.g. a slow browser navigation or a long shell command.
- **The gap between a `tool_end` and the next `tool` frame** = model
  latency: the model thinking about what to do next after seeing the
  previous tool's result. A 190s lookup that is mostly these gaps is a
  model-latency problem, not a tool-runtime problem — and vice-versa.

Sum the span durations and subtract from the turn's total to get the
model-latency share without opening the transcript.

## Boundary text flush

The model often writes short progress markers between tool calls. Some
models stream that text as token deltas (it arrives live already);
others **batch** it — the text shows up only as a buffered `assistant`
text block with no preceding deltas, which historically meant it was
never streamed and the turn's inter-tool text appeared all at once at
the very end.

The streaming path now reconciles each `assistant` text block against
the deltas already streamed for it (prefix-match dedup) and buffers any
un-streamed remainder. That remainder is flushed as a normal `chunk`:

- **at the next tool boundary** — right before the `tool` start frame,
  so the marker lands with the tool it introduces;
- **before the next token delta** — a buffered remainder always
  predates text the model generates after it, so a batched block
  followed by a streamed one (no tool call between) still lands in
  the true order; and
- **at end-of-stream** — for the final message on a batched turn.

Fully-streamed text is not re-emitted (the dedup yields an empty
remainder), so nothing is duplicated.

**Error text is deliberately excluded.** When `claude -p` fails
upstream (e.g. an Anthropic 500), the error wording arrives as a
buffered `assistant` text block with no deltas and no tool_use — the
same shape as batched progress text. It lands in the same pending
buffer, but the non-zero exit code raises **before** the end-of-stream
flush runs, so the buffer is discarded and never streamed. The error
reaches the handler only via the raised, classified exception
(`BrainTransientError` / `BrainPermanentError` / `BrainError`), exactly
as before. See `_classify_brain_failure` in `core/brain/claude_code.py`.

## Code pointers

- Span tracker + dedup helper: `vexis_agent/core/brain/claude_code.py`
  → `_ToolSpanTracker`, `_unstreamed_remainder`.
- Streaming emit + flush: same file → `ClaudeCodeBrain._attempt_astream`.
- Buffered span logs: same file → `_read_stream_events`.
- Event contract: `vexis_agent/core/brain/base.py` → `Brain.astream`
  docstring (`status` vocabulary shared with `ToolEnd`).
- SSE frames: `vexis_agent/core/web_server.py` → `post_chat_stream`.
- Tests: `tests/test_tool_spans.py`.
