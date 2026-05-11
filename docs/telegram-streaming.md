# Telegram streaming reply path

The Telegram transport edits a single bubble in place as the brain
generates its reply, instead of waiting for the full reply and
sending it all at once. The user sees text appearing live, the way
ChatGPT or Claude.ai does in a browser.

## How it works

Lifecycle for one user-typed message:

1. User sends text → drain loop claims the chat, fires the
   relationships hook, then dispatches the brain turn through
   `_dispatch_brain_turn`.
2. Streaming dispatch sends a placeholder bubble (`…`) so the user
   sees an instant ack.
3. The transport iterates `MessageHandler.stream(...)`, which proxies
   `Brain.astream(...)`. Each `("chunk", str)` event accumulates into
   the bubble; `bot.edit_message_text` fires on a throttle (default
   one edit per chat per second).
4. When the bubble would exceed 3800 chars, the transport seals it
   on the nearest paragraph (or line) break and starts a fresh
   placeholder bubble for the rest. Telegram's hard ceiling per
   message is 4096 chars; we leave 296 chars of headroom for the
   boundary search.
5. On `("done", str)` the transport does a final flush so the
   throttle never causes the last delta to be missing. If the brain
   referenced screenshot file paths in its reply, the transport
   strips them from the final bubble and sends each as a separate
   photo (or document, when oversize) AFTER the text.
6. On `("error", payload)` the transport replaces the bubble with
   the user-facing error message. Cancellations (empty message)
   stay silent — the `/cancel` handler is the source of truth for
   the cancel ack.

After the streaming reply finishes, the goal hook receives the same
final string the buffered path would have returned, so per-turn goal
judgment behaves identically.

## Config

In `~/.vexis/config.yaml`:

```yaml
telegram:
  streaming_enabled: true              # default true
  streaming_min_interval_seconds: 1.0  # default 1.0; clamped [0.5, 5.0]
```

`streaming_enabled: false` falls back to the pre-streaming buffered
path — same `MessageHandler.handle()` + chunked `send_message` flow
the transport used before this feature shipped. Use this as the
safety valve if streaming misbehaves on a given account; flip it
back to `true` once the issue is resolved.

`streaming_min_interval_seconds` controls the per-chat edit cadence.
The default 1.0s is the safe rule that keeps a single chat well
under both Telegram limits even on a tight chunk cadence:

- Telegram caps edits at ~6/sec bot-wide. With one chat in flight
  at 1 edit/sec we use ~17% of that bucket — leaving headroom for
  callback-driven edits (`/model`, `/goal status`, etc).
- Telegram caps writes at ~1 message/sec per chat. We're not sending
  new messages mid-stream (only edits), so we're nowhere near that.

Both knobs are read once at startup (`TelegramTransport.__init__`)
and bound to the instance. Changing them requires a daemon restart,
same as `brain.kind`. This intentionally trades hot-reload
convenience for a hot-path read elimination — every other transport
flag does the same.

## Trade-offs vs the buffered path

- **Screenshot ordering.** Buffered path sent screenshots BEFORE the
  text. Streaming path sends them AFTER, because the path tokens
  aren't extractable until the full reply is known. The cleaned
  text is rendered in the final bubble; the photos arrive as
  follow-up messages right after.
- **Brief path visibility.** During streaming, screenshot path
  tokens are visible in the bubble until the final flush replaces
  them with cleaned text. Single-user system, local file paths —
  not a privacy concern; documenting it for completeness.
- **Tool-use events are dropped.** The web dashboard renders
  `("tool", dict)` events as inline status lines because it has a
  separate UI lane for them. Telegram doesn't, and surfacing them
  inline would compete with the streamed text for the same edit
  budget. `/status` already exposes per-turn tool activity for
  users who want it.
- **Earlier rolled-over messages can't be retroactively edited.**
  In the rare case a screenshot path lands in a bubble that already
  rolled over, the path stays visible in that earlier bubble. In
  practice paths land at the tail of a turn (the "I just took a
  screenshot at ..." pattern), so a single tail-edit covers the
  common case.

## When to disable streaming

- The bot is hitting 429s consistently. First check
  `streaming_min_interval_seconds` — bumping it from 1.0 to 1.5 or
  2.0 should resolve transient throttling without losing the
  streaming feel. Fall back to `streaming_enabled: false` only if
  the throttle increase doesn't help.
- A specific chat keeps showing partial / corrupted bubbles.
  Buffered fallback is a clean switch — the user keeps seeing
  whole-reply-at-once messages, no UX regression beyond the wait.
- Migrating to a new python-telegram-bot major version that changes
  `edit_message_text` semantics. Disable, verify the buffered path
  still works, then re-enable after testing.

## Code pointers

- Config: `vexis_agent/core/yaml_config.py` →
  `telegram_streaming_enabled()`,
  `telegram_streaming_min_interval_seconds()`.
- Dispatcher router: `vexis_agent/transports/telegram.py` →
  `TelegramTransport._dispatch_brain_turn`.
- Streaming reply: same file →
  `TelegramTransport._send_brain_reply_streaming`.
- Boundary helper: same file → `_split_at_streaming_boundary`.
- Tests: `tests/test_telegram_streaming.py`.
