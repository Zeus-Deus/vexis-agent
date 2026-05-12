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
- **Aux subsystems route through `brain.spawn_aux`.** Never
  shell out directly. Tier choice is subsystem-owned (caller
  passes `model_tier="small"`); tier→native translation is
  brain-owned (each brain reads `models.tiers.<kind>.<tier>`
  or its `DEFAULT_TIER_MAP_<KIND>` constant).
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

## Model selection

Two-step resolution: subsystems pick an abstract size tier
(`tiny` / `small` / `medium` / `large`) via
`subsystem_tier(<name>)`; the active brain translates tier →
native model id via `models.tiers.<brain-kind>.<tier>` config
or `DEFAULT_TIER_MAP_<BRAIN>`. Foreground turn uses the brain's
account default (no `--model` flag) — unless the caller passes a
per-turn override (voice call mode does, sourced from
`voice.call_mode.model`; Telegram/text-chat always pass `None`).

Override per-subsystem under `models.subsystems.<name>` in
`~/.vexis/config.yaml`. Legacy raw-string keys (e.g.
`models.coherence_judge: sonnet`) still work on claude-code
via back-compat; they break on opencode (which requires
`provider/model` shape). Sentinel `default` means
"no `--model` flag — let the brain pick".

**Pointers:** `docs/model-ux.md` (resolution table, slash,
dashboard, hot-reload-vs-restart matrix) · `docs/migration.md`
(legacy-keys-on-opencode trap recipe).

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

`/goal <text>` kicks off a multi-step task Vexis works on
across turns until done, paused, or the budget runs out. After
each brain turn an auxiliary judge
(`subsystem_tier("goal_judge")`, default `large`) decides
whether the goal is satisfied; if not and the budget remains,
Vexis enqueues a continuation through the same per-chat FIFO
real user messages use.

Subcommands: `/goal status|pause|resume|clear`. `/cancel` while
active flips to paused with `paused_reason="user-cancelled"`.
Budget defaults to 20 turns (`goals.max_turns`); disable via
`goals.enabled: false`.

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
`docs/model-ux.md` · `docs/dogfood-checklist.md`.
