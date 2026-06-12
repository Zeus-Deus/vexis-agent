# Vexis-Agent

Standalone Python daemon. Telegram bot + agent CLI bridge for
controlling an Omarchy (Hyprland/Wayland) desktop from a phone.
Transport layer in front of an agent CLI (claude-code by
default; opencode optional) — Telegram in, MCP tools out, agent
CLI in the middle. Not a new agent.

## How to edit this file

CLAUDE.md is a behaviour manual — short, direct, prescriptive.
It is NOT a codebase reference. When working on a feature:

- **TL;DR + defaults + override knobs + pointers go HERE.**
  ~30 lines per feature section maximum.
- **Implementation details, idioms, file:line citations →
  code comments at the call site.**
- **Test pins, paths, case counts → test file docstrings.**
  Counts drift; docstrings stay adjacent to the truth.
- **Operational walkthroughs, slash grammar, dashboard
  layouts → `docs/<feature>.md`.**
- **Historical archaeology, audit findings, design rationale
  → `.plans/<feature>-research.md`.**
- **Cross-feature contracts → the `## Invariants` section
  below.** Stays under ~40 lines; if it grows past that,
  re-examine each entry — feature-specific contracts belong in
  their feature section's TL;DR, NOT here.
- **When in doubt: write it in `docs/<feature>.md` + add a
  one-line pointer here.** If a section has grown past ~30
  lines, that's a signal to extract, NOT to keep going.

A line-count tripwire test
(`tests/test_claude_md_invariants.py`) fails when this file
grows past 220 lines. The fix is extraction — bump only when
the growth comes from new cross-feature contracts in
Invariants AND Invariants is itself under ~40 lines. Never
bump for per-feature bloat.

## Repo layout
- `vexis_agent/` — installable package; `cli.py` Typer entry (the
  `vexis-agent` console script), `main.py` daemon entry, `core/` (loop +
  brain adapters + curators + goals), `transports/`, `tools/`.
- `web/` — dashboard frontend (built via `npm run build`).

## Local dev environment
- Miniconda env: `vexis-agent_env`. Activate before any `pip install` or running code.
- Never install to global Python.
- Python 3.11+, async-first, type hints required.

## Secrets
- All sensitive values live in `.env`. Never commit secrets, user IDs, tokens, or personal paths.
- Read user identifiers and tokens from env or `~/.config/vexis-agent/config.toml`. Hardcode nothing user-specific in source.

## Conventions
- Single-user by design. No multi-tenancy.
- Audit before changing. Read the relevant module fully before editing.
- Eval runs (`scripts/eval_learning.py`) are expensive (~50 LLM calls per run). Only invoke when prompts or fixtures change. Treat as a release gate, not a CI step.

## Invariants

Cross-feature contracts. Read these before touching any feature
section — violating them is breaking the codebase.

- **Content-prefix is the canonical recursion-guard filter.**
  `list_eligible_sessions` skips JSONLs whose first user message
  starts with `CURATOR_REVIEW_PROMPT_PREFIX`, `GOAL_JUDGE_PROMPT_PREFIX`,
  or `KANBAN_WORKER_PREFIX`. Env vars set on aux spawns
  (`VEXIS_CURATOR=1`, `VEXIS_GOAL_JUDGE=1`, `VEXIS_KANBAN=1`,
  etc.) are forensic markers for audit logs only.
- **Aux subsystems route through `brain.spawn_aux` with an
  explicit tool allowlist.** Never shell out directly. Tier
  choice is subsystem-owned (`model_tier="small"`); tier→native
  translation is brain-owned (each brain reads
  `models.tiers.<kind>.<tier>` or its `DEFAULT_TIER_MAP_<KIND>`).
  Every shipping caller passes `allowed_tools=[...]` declaring
  the narrowest tool surface it needs — judges and extractors
  text-only, the skill curator Read/Write/Edit/Glob/Grep (no
  Bash, no WebFetch). A poisoned transcript can't argue an aux
  into a tool it wasn't given.
- **Config reads disk per call; `brain.kind` is read once at
  startup.** `subsystem_tier()` and `model_for_tier()` re-read
  `~/.vexis/config.yaml` every invocation — tier edits hot-
  reload at the next aux spawn. The brain instance is bound at
  startup; changing `brain.kind` requires a restart. The
  dashboard surfaces a canary warning when on-disk diverges
  from the running brain.
- **Validator vocabulary is shared.** The `suggested_fix` copy
  the model validator emits is the same string the dashboard
  refusal toast renders AND the string
  `BrainModelNotFoundError.suggested_fix` carries on the spawn-
  site backstop. Single source at `core.model_validator`. Drift
  = test failure.
- **Comment-preservation backup is on-disk-state-triggered.**
  `backup_if_commented` runs when `~/.vexis/config.yaml` has
  YAML comments — not when an in-memory flag says first edit.
  Flag pattern destroys comments after daemon restart; on-disk
  trigger is self-managing.
- **Transcript reads route through `brain.iter_messages()`.**
  Only `core/transcripts.py` + `core/brain/claude_code.py` may
  touch `claude_session_jsonl_dir`; opencode has no JSONL
  (sessions live in `opencode.db`). Guarded by `test_brain_parity`.
- **Screenshot/livestream source picks go through
  `capture_source.resolve_source()`.** `/screenshot`, `vexis-screenshot`,
  and `vexis-livestream start` all build a `RouterContext` (modifier,
  `VEXIS_SANDBOX_TASK_ID`, live `Sandbox.list_all`, lock probe) and
  call the same pure router. Sandbox capture moves the PNG to
  `/tmp/vexis-screenshot-<ts>.png` so the Telegram path regex stays
  source-agnostic. See `docs/screenshot-routing.md`.
- **Add-ons own their integration; core stays add-on-agnostic.**
  No file under `vexis_agent/core/` may import from
  `vexis_agent.addons.*`. Add-ons register via `PluginContext`
  hooks (telegram_command, dispatch_handler, watcher_source,
  header_block, skill, etc.) — never by patching core. Guarded
  by `tests/test_codemux_extraction_invariant.py`. The one
  sanctioned exception is the watcher's back-compat re-export of
  `UNAVAILABLE_MESSAGE` from the codemux add-on, allowlisted in
  the test.
- **Core subsystems are individually gated, default-on (issue
  #39).** Background tasks, watcher, scheduling, goals, the two
  learning systems, and the two transports each carry an `enabled`
  switch read in `main._run` (or per-call); absent keys = today's
  behaviour byte-for-byte. Skill/memory (`learning.enabled`) and
  relationship/user-fact learning (`relationships.enabled`) are
  DISTINCT switches. No shipped preset — composition is per-
  deployment config. See `docs/modular-subsystems.md`.

## Model selection

Two-step resolution: subsystems pick an abstract size tier
(`tiny` / `small` / `medium` / `large`) via
`subsystem_tier(<name>)`; the active brain translates tier →
native model id via `models.tiers.<brain-kind>.<tier>` config
or `DEFAULT_TIER_MAP_<BRAIN>`. Foreground (chat) turn — the model
you talk to — resolves `models.brain` tier-or-raw per turn;
`default`/unset → no `--model` flag → account default. Settable via
`/model set foreground` + dashboard (the chat-model knob; matters
most on opencode, no account default). Per-turn overrides beat it: voice
call mode (`voice.call_mode.model`) or the computer-use selector
(`computer_use.*` — pinned model + optional dynamic fast-model
layer, gated on recent `vexis-ui` activity). Per-subsystem override
under `models.subsystems.<name>`. Legacy raw-string keys (e.g.
`models.coherence_judge: sonnet`) work on claude-code via
back-compat, break on opencode. `default` = no flag.

**Pointers:** `docs/model-ux.md` (resolution, slash, dashboard,
hot-reload matrix) · `docs/migration.md` (legacy-keys trap) ·
`docs/computer-use-model.md` (per-feature + dynamic switch).

## Learning curator

A background daemon reviews finished sessions and routes any
lesson found by class:

- **PROCEDURAL** (workflow / how-to rules) → a skill under
  `<workspace>/skills/`.
- **IDENTITY** (durable preferences) → `USER.md`, after the
  same claim appears in ≥2 distinct sessions within 30 days.
- **SITUATIONAL** (environment / setup facts) → `MEMORY.md`,
  with exact-evidence dedup against existing entries.
- **VOLATILE** (one-shot or temporary) → dropped.

Pinned skills are read-only to the curator.

**Pointers:** `docs/learning-curator-runbook.md` (recursion
guard, two-tier review, shadow mode, soak windows, eval gate)
· `.plans/learning-curator-v2-research.md` (full design).

## Coherence curator (v3a)

Inline judge that runs after every verified lesson the learning
curator writes. Decides whether the lesson body is grounded in
the cited evidence string. Three verdicts: COHERENT (silent),
NEAR_MISS_REVIEW (soft annotation), INCOHERENT (hard
`Coherence: FLAGGED` annotation in the shadow file).
**Advisory-only — never blocks a write.**

**Pointers:** `docs/learning-curator-runbook.md#coherence-curator-v3a`
· `.plans/coherence-curator-research.md`.

## Relationships (v3c)

Vexis silently extracts third-party facts (sonnet-default
extractor) and queues them for approval. Approved facts land
in `<workspace>/RELATIONSHIPS.md` which the brain reads on
next session spawn. The brain never sees the candidate queue.

Approve via Learning tab → Relationships panel OR Telegram
`/learning relationships-{pending,approve,reject,digest}`.
Strong qualifier cues (mom, dad, partner, sibling) eligible
after 1 session; soft + weak cues after ≥2 sessions in 30 days.
Override extractor via `models.relationships_extractor`;
suppress the brain-cache `/clear` hint via
`relationships.approval_hint_enabled: false`.

**Pointers:** `docs/relationships.md` (eval gate + Day 5
sonnet-flip context) · `.plans/relationships-v3c-research.md`.

## Goals (v3d)

`/goal <text>` hands Vexis a multi-step objective. As of v0.11 it
runs **in the background by default** (`goals.default_mode`): filed
as a kanban task, chat stays free, progress via a `[BACKGROUND
GOALS]` block injected into chat turns (just ask "how's my goal
going?") + `/goal status` + the dashboard. `/goal --fg <text>` runs
the old in-chat loop instead: an aux judge
(`subsystem_tier("goal_judge")`) decides done each turn, else a
continuation enqueues via the per-chat FIFO (`/goal
resume`/`pause`/`clear`; `/cancel` → paused). Budget 20
(`goals.max_turns`); disable `goals.enabled: false`; foreground
noise `goals.notify_policy`. Projection: `core/goal_background.py`.

**Pointers:** `docs/goals.md` · `.plans/goal-command-research.md`.

## Kanban (v3e)

Multi-task work queue at `~/.vexis/kanban.db`. `/kanban add
"<title>"` (Telegram) or the dashboard quick-add files a task;
the dispatcher claims ready tasks (parents done) and spawns one
worker per task via `brain.spawn_aux`. Bounded by
`max_concurrent_workers` (default 2 — respects brain rate limit).

Six columns: triage → todo → ready → in_progress → blocked →
done. Parent-child links block promotion until parents reach
done. Per-task circuit breaker auto-blocks after `failure_limit`
consecutive failures (default 3).

Lanes (vexis's lightweight replacement for upstream profiles):
each task carries a `lane` name. A lane = `(system_prompt,
skills, tier_override)`. Same brain, different hat. Defaults:
`research` / `implementation` / `review` / `ops` / `triage`.
Override per-lane under `kanban.lanes:` in `~/.vexis/config.yaml`.

Telegram + dashboard are co-equal subscribers to one event bus
(`task_events`). `/goal` is parallel, not nested — the dashboard
renders active goals in a read-only goal-pad sidebar.

**Pointers:** `docs/kanban.md` (commands, board layout,
notification policy) · `.plans/kanban-research.md` (design lock).

## Brain abstraction (Phase C)

Vexis runs on top of an agent CLI selected at startup by
`brain.kind` in `~/.vexis/config.yaml`. Three implementations
satisfy the `core.brain.Brain` ABC: `claude-code` (default,
sessions in `~/.claude/projects/<encoded-cwd>/`), `opencode`
(opt-in, sessions in `~/.local/share/opencode/opencode.db`),
and `null` (test fake). Brain switching and per-subsystem
assignment are first-class UX surfaces; YAML-edit-and-restart
still supported but no longer required.

**Pointers:** `docs/brains.md` · `docs/migration.md` ·
`docs/model-ux.md` · `docs/dogfood-checklist.md` · `docs/memory-isolation.md` (per-subagent memory scopes; `brain.subprocess_memory_max`).

## Conversation compression

Before every brain turn the handler calls
`brain.compress_if_needed(session_id)`. When the transcript
crosses either threshold — token estimate ≥ 80% of context
window OR turn count > 40 — the brain spawns an aux
summariser (`subsystem_tier("compressor")`, default `small`),
gets back a structured summary, and atomically rewrites the
on-disk transcript: system metadata preserved, the older
turns folded into one synthetic user-turn whose body starts
with `SUMMARY_PREFIX`, the last 10 turns kept byte-for-byte.

`SUMMARY_PREFIX` is distinct from every recursion-guard
prefix on purpose: a compressed foreground transcript still
passes the curator's content-prefix filter (the right answer
— we WANT the curator to be able to review long sessions for
lessons). Iterative compression detects the prior summary by
its prefix and folds it into the new one. Claude-code lands
the JSONL rewrite; opencode logs the trigger but defers the
SQL rewrite to a follow-up.

**Pointers:** `docs/compression.md`.

## File-mutation verifier footer

Each brain turn snapshots the workspace before and after; the
diff is prepended to the next user message as
`[turn-N verifier] Files changed last turn: ...` so the model
self-corrects against silent write failures. The goal judge
consumes the same diff via `brain.peek_files_changed` and
weighs the brain's response against ground truth rather than
the brain's own claims.

Snapshot work runs via `asyncio.to_thread` (~1.5ms on the
vexis-agent repo, under 200ms on a 10k-file workspace).
Disable via `brain.file_mutation_footer: false` (default
`true`); reads disk per turn so toggling takes effect
immediately. The `[turn-N verifier]` marker is deliberately
NOT in the recursion-guard prefix set — verifier-footered
turns remain visible to the learning curator.

**Pointers:** `docs/file-mutation-verifier.md`.

## Add-on system

Vexis features that aren't part of core ship as add-ons under
`vexis_agent/addons/<name>/`. Each add-on is a folder with
`addon.yaml` + `__init__.py` defining `register(ctx)`. Bundled:
`browser` (default-on; ships the `vexis-browser` MCP server) and
`codemux`. Future tools / watcher sources / dashboards plug in alike.

**Pointers:** `docs/addons.md` (full author guide) ·
`vexis_agent/addons/codemux/docs/codemux-watcher.md` (codemux
specifics).

## Capability prompt blocks

The system-prompt "Capabilities" section is no longer a monolith.
Each core capability owns its how-to as a `*_capability.py` next to
its tool and self-registers via `register_capability_block(name,
order=, provider=)`. `core/capabilities.assemble_capability_docs()`
(called by BOTH brain prompt builders) sorts blocks by `order` and
joins them. `CAPABILITIES.md` shrank to the stable core (identity +
the add-on model) and is block 0. Change a tool, change its block —
same PR, no monolith drift.

**Byte-identity is load-bearing.** `assemble_capability_docs()` must
equal `tests/data/capabilities_golden.md`. Intentional prose edits
mean regenerating that golden in the same PR — don't drift it
silently. Add a capability: drop a `*_capability.py`, register a
fresh `order`, list it in `_BUILTIN_CAPABILITY_MODULES`, update the
golden. Never edit `CAPABILITIES.md` for a new tool.

**Pointers:** `docs/capabilities.md` (author guide, ownership map,
codemux-in-core rationale).
