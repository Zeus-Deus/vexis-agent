# Capability prompt blocks

> Decomposing the `CAPABILITIES.md` monolith into per-capability docs
> that live next to the code they describe (issue #30).

## Why

`CAPABILITIES.md` used to be one ~1000-line block injected into every
system prompt. It was versioned with the wheel, not with the
capability it described. When a tool's engine changed (the browser
moving to Camoufox, say), its prose in the monolith didn't move with
it and went stale — the agent followed a documented surface that no
longer matched reality.

Now each core capability owns its own slice of the prompt as a small
module **next to the tool's code**. Change the tool, change its block,
same PR. No monolith drift.

This is the core-side analogue of what add-ons already do with
`ctx.register_system_prompt_block` — but those are *dynamic* "active
state" headers appended at the end of the prompt, whereas these are
the *static* per-tool how-to that slots in where `CAPABILITIES.md`
used to sit (after SOUL, before the skill-authoring block).

## How it assembles

`vexis_agent/core/capabilities/` is the registry.

- Each capability module calls `register_capability_block(name, *,
  order, provider)` at import time. `provider()` returns the block's
  markdown (or `None` to hide it).
- `assemble_capability_docs()` imports every module listed in
  `_BUILTIN_CAPABILITY_MODULES`, sorts the blocks by `order`, and joins
  them with a blank line — reproducing the exact text the monolith
  carried.
- Both brain prompt builders (`core/brain/claude_code.py` and
  `core/brain/opencode.py`) call `assemble_capability_docs()` instead
  of the old `read_capabilities()`.

`order` is a capability's position in the assembled doc. The blocks
extracted from the monolith kept their original chunk indices, with two
deliberate post-decomposition changes: `self-extension` was added at
order 1, and `codemux-orchestration` (order 15) moved out to the
codemux add-on. `order` 0 is the shrunk `CAPABILITIES.md` itself
(identity + the add-on/skill/MCP model), re-read from package data each
build.

## The golden contract

`tests/test_capability_blocks.py` asserts `assemble_capability_docs()`
equals `tests/data/capabilities_golden.md` — the frozen expected
assembly of the **builtins-only** blocks. Add-on blocks register at
load time and are NOT in the golden, so it stays stable whether or not
codemux is enabled. The model sees a stable prompt; only the *source
layout* changed.

If you intentionally edit capability prose, or add/move a block, the
golden test will fail. That failure is the signal the change is
deliberate: regenerate the golden in the same PR (set `PYTHONPATH` to
the repo root, call `assemble_capability_docs()` with a clean
builtins-only registry, write the result to the golden) and explain why
in the commit. (The golden is not auto-updated — drifting it silently
would defeat the point.)

## Adding a new core capability

You never edit `CAPABILITIES.md` for this anymore.

1. Drop a `*_capability.py` next to your tool (e.g.
   `vexis_agent/tools/foo/capability.py`). For a tool that has no
   package directory, a `foo_capability.py` next to its CLI is fine.
2. In it:

   ```python
   from vexis_agent.core.capabilities import register_capability_block

   _FOO_BLOCK = r"""## Foo — `vexis-foo`

   ...the agent-facing how-to for your tool...
   """

   def foo_block() -> str:
       return _FOO_BLOCK

   register_capability_block("foo", order=16, provider=foo_block)
   ```

   Use a raw string (`r"""..."""`) so shell line-continuations and
   backslashes survive verbatim. Pick a fresh `order` (the next free
   integer after the existing blocks; duplicates raise at import).

3. List the module in `_BUILTIN_CAPABILITY_MODULES` in
   `core/capabilities/__init__.py`.
4. Update the golden snapshot (the test will tell you the diff), since
   you are deliberately adding to what the agent sees.

A block must be importable without pulling in its tool's heavy runtime
— keep `capability.py` to the markdown string + the `register` call.
The docs live beside the code; they don't depend on it at import time.

## Adding a capability block from an add-on

Add-ons don't touch `_BUILTIN_CAPABILITY_MODULES`; they call
`ctx.register_capability_block(name, provider, *, order)` (see
[docs/addons.md](addons.md)). The hook delegates into this same core
registry at add-on load time, so add-on blocks share the global
`order` space and conflict checks with the core blocks and are merged
transparently by `assemble_capability_docs()` — there is no separate
add-on assembly path. Pick an `order` clear of the core builtins (0–14
today); the codemux add-on already owns 15. Add-on blocks are absent
from the builtins-only golden — they assemble only when the add-on is
enabled.

## Ownership map

### Core (builtin) blocks — in the golden

| Block(s) | Module | order |
|---|---|---|
| identity + add-on model | `data/CAPABILITIES.md` | 0 |
| self-extension | `core/capabilities/self_extension.py` | 1 |
| inbound images, omarchy-kb, web dashboard | `core/capabilities/builtin.py` | 4, 5, 12 |
| desktop capture / control / vision loop | `tools/desktop_capability.py` | 2, 6, 7 |
| sandboxes & headless displays | `tools/sandbox/capability.py` | 3 |
| live streaming | `tools/livestream_capability.py` | 8 |
| background tasks | `tools/background_capability.py` | 9 |
| memory | `tools/memory_capability.py` | 10 |
| skills | `tools/skills_capability.py` | 11 |
| web browsing | `tools/browser/capability.py` | 13 |
| scheduling | `tools/schedule_tool/capability.py` | 14 |

### Add-on blocks — NOT in the golden

Registered at add-on load time via `ctx.register_capability_block`, so
they assemble only when the add-on is enabled and are absent from the
builtins-only golden. They share the same global `order` space.

| Block | Module | order | Add-on |
|---|---|---|---|
| codemux orchestration | `addons/codemux/capability.py` | 15 | codemux |

### self-extension (order 1)

Teaches the agent which seam to use to add a capability to itself —
skill vs MCP server vs in-process add-on — and the hot-vs-restart cost
of each. It has no owning tool (it's framing, like the `builtin`
cross-cutting blocks), so it lives in the capabilities package. Its
longer-form procedural companion ships as the `self-extension`
**bundled skill**
(`vexis_agent/_bundled_skills/meta/self-extension/SKILL.md`),
auto-discovered into every workspace's `skills/` view on the next
session.

### Note on codemux — add-on-owned now

The codemux orchestration block used to live in core
(`tools/watch_capability.py`) as a documented leak: codemux is an
add-on, but its how-to assembled into every install's prompt. It now
lives in the codemux add-on (`addons/codemux/capability.py`) and
registers through `ctx.register_capability_block` at load time, so the
"Codemux orchestration" section appears ONLY when codemux is enabled —
and is absent from the builtins-only golden. The add-on legitimately
imports `vexis_agent.core.addons.context.PluginContext`; the
core/addons boundary holds because nothing under `core/` imports the
add-on (pinned by `tests/test_codemux_extraction_invariant.py`).

## Not in scope (yet)

This decomposition is about *docs* moving next to *code*. The research
notes on issue #30 sketch a larger runtime-capability-registration
surface (`register_browser_provider` for swappable engines,
`register_verb`/`register_tool` so an add-on can add a new
`vexis-<x>` primitive without a release). Those are real follow-ups
but separate from — and enabled by — this modular-docs groundwork.
