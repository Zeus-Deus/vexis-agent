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
extracted from the monolith kept their original chunk indices (2–15)
so the assembled output is **byte-identical** to the pre-decomposition
file. `order` 0 is the shrunk `CAPABILITIES.md` itself (identity + the
add-on/skill/MCP model), re-read from package data each build.

## The byte-identity contract

`tests/test_capability_blocks.py` asserts `assemble_capability_docs()`
equals `tests/data/capabilities_golden.md` — a frozen snapshot of the
monolith taken at decomposition time. The model sees the same prompt;
only the *source layout* changed.

If you intentionally edit capability prose, the golden test will fail.
That failure is the signal the change is deliberate: regenerate the
golden in the same PR and explain why in the commit. (The golden is
not auto-updated — drifting it silently would defeat the point.)

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

## Ownership map

| Block(s) | Module | order |
|---|---|---|
| identity + add-on model | `data/CAPABILITIES.md` | 0 |
| inbound images, omarchy-kb, web dashboard | `core/capabilities/builtin.py` | 4, 5, 12 |
| desktop capture / control / vision loop | `tools/desktop_capability.py` | 2, 6, 7 |
| sandboxes & headless displays | `tools/sandbox/capability.py` | 3 |
| live streaming | `tools/livestream_capability.py` | 8 |
| background tasks | `tools/background_capability.py` | 9 |
| memory | `tools/memory_capability.py` | 10 |
| skills | `tools/skills_capability.py` | 11 |
| web browsing | `tools/browser/capability.py` | 13 |
| scheduling | `tools/schedule_tool/capability.py` | 14 |
| codemux orchestration | `tools/watch_capability.py` | 15 |

### Note on codemux

`vexis-watch` orchestration is documented in core (`watch_capability.py`)
rather than the codemux add-on, even though codemux ships as a bundled
add-on. `vexis-watch` is a core console script and the watcher
controller is instantiated unconditionally, so the how-to is
always-present — and keeping it in core is what makes the assembled
prompt byte-identical to the old monolith for *every* install,
including ones with codemux disabled. Moving it behind the add-on's
own prompt block (so it only shows when codemux is enabled) is a clean
follow-up; it would change the prompt for codemux-disabled installs,
which the decomposition PR intentionally avoided. `watch_capability.py`
imports nothing from `vexis_agent.addons.*`.

## Not in scope (yet)

This decomposition is about *docs* moving next to *code*. The research
notes on issue #30 sketch a larger runtime-capability-registration
surface (`register_browser_provider` for swappable engines,
`register_verb`/`register_tool` so an add-on can add a new
`vexis-<x>` primitive without a release). Those are real follow-ups
but separate from — and enabled by — this modular-docs groundwork.
