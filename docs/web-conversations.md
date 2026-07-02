# Per-conversation web sessions

The web chat API used to collapse every caller into ONE shared brain
session. All `/api/v1/chat/*` traffic ran as `chat_id = WEB_CHAT_ID = -1`
and every brain turn resolved the single global "active" session in
`SessionStore`. On a multi-user web-only deploy that meant context bleed
between concurrent chats, global resets ("it forgot everything" after one
tab rotated the session), and background-task notifications lost because
the notifier context note was buffered against a chat whose conversation
had already moved on.

Issue [#48](https://github.com/Zeus-Deus/vexis-agent/issues/48) is the
fix. A caller can now attach an opaque `conversation_id` to a chat
request; each conversation gets its own brain session and its own
notifier context buffer, isolated from every other conversation and from
the Telegram active-session UX. Requests **without** a `conversation_id`
behave exactly as before — byte for byte.

## The core seam (general, not web-specific)

The fix lives in core as a general capability, not a web hack. Two
pieces:

- **`SessionView`** (`core/sessions.py`) — the brain-facing session API
  (`get` / `is_initialized` / `mark_initialized` / `rotate` / `set`)
  bound to one *named* session instead of the store's single `_active`
  pointer. It holds no state; it delegates to the store's per-name
  primitives, so a `rotate`/`set` through a view persists to disk exactly
  like the active variant and shows up immediately in
  `SessionStore.list()`. `SessionStore.ensure(name)` creates a named
  session on first use **without** touching the active pointer (unlike
  `create`, which always flips active).

- **An optional `session` parameter** threaded handler → brain.
  `MessageHandler.handle` / `.stream` and `Brain.respond` / `.astream`
  grew a keyword-only `session: SessionLike | None = None`. `None` (the
  default) drives the brain's bound active-session store — the historical
  behaviour. The handler only forwards the kwarg to the brain when it is
  non-`None`, so the default call is byte-identical and legacy/third-party
  brains whose signatures never grew the kwarg keep working. A non-`None`
  `SessionView` runs *that* turn against *that* session: the session-id
  pin/resume decision, `mark_initialized`, pre-turn compression, and the
  session-lost rotate-and-raise recovery all follow the handle.

Core never learns the word "conversation". It only ever sees a named
`SessionView` and an `int` chat_id.

## The web mapping (policy lives in `transports/web.py`)

`transports/web.py` — and only that module — maps its transport-level
`conversation_id` onto the core seam. Three pure, deterministic,
restart-safe helpers:

- **`validate_conversation_id(cid) -> str`** — must be a string,
  non-empty after `.strip()`, ≤ 200 chars. Returns the stripped value
  (so `" abc "` and `"abc"` are the same conversation). Raises
  `ValueError` (routes turn it into a 400).

- **`conversation_session_name(cid) -> str`** — the `SessionStore` name.
  Slug = the id with every run of non-`[A-Za-z0-9_-]` collapsed to `-`,
  stripped, truncated to 40 chars. Name = `web-{slug}-{hash8}`, or
  `web-{hash8}` when the slug is empty. `hash8` is the first 8 hex of
  `sha256(cid)`. Always ≤ 64 chars, always matches `SessionStore`'s
  `_NAME_RE`. The hash suffix keeps two ids with the same slug (`a/b` vs
  `a-b`) distinct.

- **`conversation_chat_id(cid) -> int`** — the private notifier /
  RunningTasks chat id. `n = int.from_bytes(sha256(cid)[:6], "big")` (a
  48-bit prefix); returns `-(2**52 + n)`.

The chat-id band is disjoint from everything else:

```
      Telegram groups            conversation band
   ~ -10**13 .. -10**12                    │
            │                              ▼
   ─────────┼──────────────┼──────────────┼──────────────► more negative
            │              │        [ -(2**52) .. -(2**52 + 2**48) ]
          WEB_CHAT_ID = -1  Telegram users (positive, off-axis)
```

`-(2**52)` ≈ `-4.5e15`, far below the Telegram group band (~`-1e12`),
never equal to `WEB_CHAT_ID = -1`, and every value fits in a signed
int64. Determinism means the same id resolves to the same session and
chat id across daemon restarts; a fresh restart re-derives, it never
re-mints.

## API reference

All routes are token-gated (`Authorization: Bearer <token>`) and 503 when
the chat transport wasn't constructed. `conversation_id` is optional on
every one of them — omit it for the legacy shared web chat.

### Send (buffered)

```bash
curl -sX POST https://<host>/api/v1/chat/send \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"text": "hello", "conversation_id": "tab-7"}'
# → {"reply": "...", "conversation_id": "tab-7"}
```

The reply echoes `conversation_id` back when you sent one, so a client
can confirm the turn was filed against the conversation it intended.
Legacy (no `conversation_id`) returns just `{"reply": "..."}`.

### Stream (SSE)

```bash
curl -NsX POST https://<host>/api/v1/chat/stream \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"text": "hello", "conversation_id": "tab-7"}'
# data: {"type":"chunk","text":"..."}
# data: {"type":"done","reply":"..."}
```

### Clear

```bash
curl -sX POST https://<host>/api/v1/chat/clear \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"conversation_id": "tab-7"}'
# → {"reply": "Conversation cleared."}
```

Rotates only that conversation's session to a fresh id — a delete of that
conversation's memory. Clearing a conversation that was never used is a
harmless fresh rotate. The active Telegram/dashboard session is untouched.

### Cancel

```bash
curl -sX POST https://<host>/api/v1/chat/cancel \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"conversation_id": "tab-7"}'
# → {"cancelled": true|false}
```

Kills the in-flight turn for that conversation's derived chat id (or
`WEB_CHAT_ID` with no id). `false` means nothing was in flight.

### History

```bash
curl -sG https://<host>/api/v1/chat/history \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "conversation_id=tab-7" --data-urlencode "limit=50"
# → {"messages": [{"role":"user","content":"...","ts":1234567890123}, ...]}
```

Query params — conversation ids are arbitrary strings and don't belong in
the URL path. An unknown / never-used conversation returns
`{"messages": []}` (200) and does **not** create a session as a side
effect. Invalid `conversation_id` → 400.

### Attach

```bash
curl -sX POST https://<host>/api/v1/chat/attach \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@cat.png" -F "conversation_id=tab-7"
```

The upload lands under `<workspace>/uploads/<conversation-session-name>/`
so an attachment is filed with the conversation it was uploaded for.
Without a `conversation_id` it uses the active session's name, as before.

## Busy semantics

Two concurrent sends to the *same* conversation collide: `RunningTasks`
already has an in-flight turn for that chat id, so the second
`reserve()` raises `TaskAlreadyRunning`. The handler catches it before
the generic error path and surfaces a distinct signal:

- Buffered `send` → the toast *"⚠️ A reply is already in progress for
  this conversation — wait for it to finish or cancel it."*
- Streaming → `("error", {"code": "busy", "message": ...})`, which the
  SSE route forwards as `data: {"type":"error","code":"busy",...}`.
- `TurnOutcome.kind == "busy"`, and `is_brain_failure` is **False** — the
  brain never ran, so it's a client-side collision, not a brain failure.

Telegram never hits this: its per-chat drain discipline serialises turns.
It fires only on parallel web sends to one conversation (two tabs, a
double-tapped Send).

## Notification isolation + web-only wiring

The notifier's per-chat context buffer is now keyed by the conversation's
derived chat id, so a background-task completion (or any
`notifier.send`/`append_context`) buffered for conversation A is injected
as a `[SYSTEM CONTEXT]` block into A's next turn only — a turn on B never
consumes it.

In a **web-only** deployment (Telegram disabled), `main._run` wires
`background_tasks.set_notify(notifier.send)` itself. Previously only the
Telegram transport's `start()` wired that callback, so on a web-only
daemon background-task completions were dropped entirely. Now the
notifier's context-buffer half still fires; the Telegram *delivery* half
degrades to a logged warning when no app is bound (exactly the web-only
case). When Telegram is enabled its `start()` wires the same callback, so
core skips it there to keep that path byte-identical.

## Dashboard interplay

Conversation sessions are **ordinary named `SessionStore` sessions** —
they show up in the dashboard sidebar's session list (with a `web-`
prefix), and they are renameable and deletable like any other. Deleting a
conversation's session is how that conversation "forgets". The frontend
owns what a `conversation_id` *is* (a tab id, a "New chat" uuid); core
just namespaces on whatever opaque string it's handed.

## Auth note

`conversation_id` namespaces **context, not access**. Vexis is
single-user; the dashboard token is one trust domain. Anyone holding the
token can read or drive any conversation — the ids partition memory, not
permissions. Do not treat a conversation id as an authorization boundary.

## Cleanup

Conversation sessions accumulate the same way any named session does.
Clear one to wipe its memory (`/chat/clear` with its id), delete its
session from the sidebar to remove it entirely, and `rm -rf` its
`uploads/<name>/` subdir to drop its attachments. There's no automatic
GC — a single-user deploy grows slowly and the sidebar makes stale
conversations easy to prune by hand.
