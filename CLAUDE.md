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
- `core/brain/` — agent CLI adapters (claude-code, opencode, null).
- `transports/` — messaging adapters. Default: `telegram.py`.
- `tools/` — MCP servers (desktop-control, tailnet-serve, voxtype, omarchy-kb).
- `core/` — main loop, auth, config, learning curator, goals, schedules, sessions.

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

## Reference repos (clone to /tmp when needed)
- `NousResearch/hermes-agent` — peek at gateway, skills, memory patterns. Never bulk-copy.

## Build order
1. Telegram ↔ agent CLI bridge.
2. User-ID auth check.
3. Voice (voxtype whisper model).
4. tailnet-serve + omarchy-kb tools.
5. Screenshot tool (read-only).
6. Input tool + safety scaffolding.

## Invariants

Cross-feature contracts. Read these before touching any feature
section — violating them is breaking the codebase.

- **Content-prefix is the canonical recursion-guard filter.**
  `list_eligible_sessions` skips JSONLs whose first user message
  starts with `CURATOR_REVIEW_PROMPT_PREFIX`. Env vars set on
  aux spawns (`VEXIS_CURATOR=1`, `VEXIS_GOAL_JUDGE=1`, etc.)
  are forensic markers for audit logs only; no curator path
  reads them for filtering.
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
  the model validator emits (rule 4 / rule 6) is the same
  string the dashboard refusal toast renders AND the same
  string `BrainModelNotFoundError.suggested_fix` carries when
  the spawn-site backstop fires. Single source of truth at
  `core.model_validator` template constants. Drift = test
  failure.
- **Comment-preservation backup is on-disk-state-triggered.**
  `backup_if_commented` runs when the current
  `~/.vexis/config.yaml` has YAML comments — not when an
  in-memory "backed up this session?" flag says first edit.
  The flag pattern destroys comments after daemon restart;
  the on-disk trigger is self-managing.

## Model selection

Two-step resolution: subsystems pick an abstract size tier
(`tiny` / `small` / `medium` / `large`) via
`subsystem_tier(<name>)`; the active brain translates tier →
native model id via `models.tiers.<brain-kind>.<tier>` config
or `DEFAULT_TIER_MAP_<BRAIN>`. Foreground turn uses the brain's
account default — no `--model` flag.

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
(five surfaces + verdict shapes) ·
`.plans/coherence-curator-research.md`.

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

Subcommands: `/goal status` · `/goal pause` (soft; in-flight
turn finishes; queue drained) · `/goal resume` (resets
`turns_used`) · `/goal clear`. `/cancel` while active flips to
paused with `paused_reason="user-cancelled"`. Budget defaults
to 20 turns (`goals.max_turns`); disable entirely via
`goals.enabled: false`.

**Pointers:** `docs/goals.md` (grammar, soft-pause semantics,
prompt-cache invariant, eval-gate cost ceiling) ·
`.plans/goal-command-research.md`.

## Brain abstraction (Phase C)

Vexis runs on top of an agent CLI selected at startup by
`brain.kind` in `~/.vexis/config.yaml`. Three implementations
ship; all satisfy the `core.brain.Brain` ABC so the rest of
vexis doesn't care which is running.

- **`claude-code` (default)** — sessions in
  `~/.claude/projects/<encoded-cwd>/`.
- **`opencode` (opt-in)** — sessions in
  `~/.local/share/opencode/opencode.db`.
- **`null`** — `BrainNull`, the test fake.

Brain switching and per-subsystem assignment are first-class
UX surfaces (post Day 5 of model-management research); the
YAML-edit-and-restart workflow remains supported but is no
longer required.

**Pointers:** `docs/brains.md` (per-brain reference) ·
`docs/migration.md` · `docs/model-ux.md` (slash + dashboard) ·
`docs/dogfood-checklist.md` (12 manual flows).
