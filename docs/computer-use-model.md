# Computer-use model selection

Per-feature model selection for desktop computer-use turns —
clicking buttons, reading windows, driving native apps. The
desktop analogue of voice call mode: strictly opt-in, and inert
for plain chat.

Two layers, both off by default:

- **Pinned model** (`computer_use.model`) — a per-turn model
  override used when a foreground turn is doing computer-use
  work.
- **Dynamic model** (`computer_use.dynamic`) — the Codex-style
  trick. When the last `vexis-ui` snapshot was a *rich* AT-SPI
  textual tree (no screenshot fallback, indexed element count
  over the threshold), the whole interface is described in
  text — so the turn can run on a faster model with no vision
  needed.

## Why it stays inert for plain chat

The override only bites when the turn is *actually* doing
computer-use work. `UIDriver.snapshot()` / `vision_snapshot()`
write a small runtime-state file
(`~/.vexis/computer-use-runtime.json`); `MessageHandler`
consults it at turn start via
`core.computer_use.resolve_computer_use_override()`. If no
`vexis-ui` snapshot landed within `ACTIVITY_TTL_SECONDS`
(default 600s), the turn is not a computer-use turn and the
override resolves to `(None, None)` — Telegram and text chat
are bit-for-bit unchanged.

An explicit caller override always wins: voice call mode
passes its model directly, so the handler never substitutes
over it. The voice-isolation contract is untouched.

## The decision

`resolve_computer_use_override()` returns `(model, reasoning)`:

1. No pinned model and dynamic disabled → `(None, None)`.
2. No fresh `vexis-ui` snapshot → `(None, None)`.
3. Dynamic on **and** the snapshot is rich (not stale, not a
   vision fallback, `element_count >= dynamic.min_elements`)
   → the dynamic model (if set).
4. Otherwise → the pinned model (if set), else `(None, None)`.

"Rich" deliberately excludes the screenshot fallback: if
`vision_snapshot()` was the last call, vision *is* needed, so
the pinned (vision-capable) model applies, not the fast one.

## Config

```yaml
computer_use:
  model: claude-haiku-4-5      # pinned model; omit / "default" = brain default
  reasoning_level: medium      # only when the pinned model supports it
  dynamic:
    enabled: true              # opt-in to the fast-model switch
    model: claude-haiku-4-5    # fast model for rich-tree turns
    reasoning_level: low
    min_elements: 5            # indexed-widget floor for "rich" (default 5)
```

Reads are per-call (hot-reload at the next turn). `model` /
`reasoning_level` use the same null / empty / `default`
sentinels as `voice.call_mode.*`. `reasoning_level` is
meaningful only with a model — the writer drops the orphan.

## Dashboard

The **Computer Use** tab (`/api/v1/computer-use`) is the
canonical writer — same atomic + comment-preserving path as the
Voice and Models tabs. It mirrors the Voice tab's model picker
(shared component, `web/src/components/ModelPicker.tsx`) and
surfaces a live readout of the last `vexis-ui` snapshot so you
can see which model would apply right now.

## Brain-agnostic, payoff brain-dependent

The mechanism works for any brain — the override is a plain
per-turn model id, resolved the same way as voice call mode.
The *size* of the win depends on the brain's model lineup:
claude-code has no non-multimodal model, so the dynamic layer
buys "faster + cheaper" (Haiku vs Sonnet), not a step change.
The config and UI are identical regardless.

**Pointers:** `core/computer_use.py` (decision + runtime
state + payload/set) · `tools/ui/ui.py` (the snapshot →
state-file write) · `core/handler.py`
(`_apply_computer_use_override`) · `tests/test_computer_use.py`.
