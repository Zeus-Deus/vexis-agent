# Vexis-Agent

Standalone Python daemon. Telegram bot + `claude -p` bridge for controlling an Omarchy (Hyprland/Wayland) desktop from a phone.

This is a transport layer in front of an agent CLI (claude-code by default; opencode optional), not a new agent. Telegram in, MCP tools out, agent CLI in the middle.

## Repo layout
- `brains/` — AI provider adapters. Default: `claude_code.py`.
- `transports/` — messaging adapters. Default: `telegram.py`.
- `tools/` — MCP servers (desktop-control, tailnet-serve, voxtype, omarchy-kb).
- `core/` — main loop, auth, config.

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

## Model selection
Internal subsystems (learning curator review, coherence judge,
migration classifier) call `claude -p` with `--model sonnet` by
default so they don't compete for plan tokens with the user-facing
brain. The brain uses the account default. Configure under
`models:` in `~/.vexis/config.yaml`; the literal value `default`
means "no `--model` flag — let `claude -p` pick". See
`core/yaml_config.py:model_*()` and `resolve_model_flag()`.

## Reference repos (clone to /tmp when needed)
- `NousResearch/hermes-agent` — peek at gateway, skills, memory patterns. Never bulk-copy.

## Build order
1. Telegram ↔ `claude -p` bridge.
2. User-ID auth check.
3. Voice (voxtype whisper model).
4. tailnet-serve + omarchy-kb tools.
5. Screenshot tool (read-only).
6. Input tool + safety scaffolding.

## Learning curator

A background daemon reviews finished sessions and routes any lesson
found to the right durable store by class:

- **PROCEDURAL** (workflow / how-to rules) → a skill under
  `<workspace>/skills/` (patch existing, add support file, or
  create new umbrella).
- **IDENTITY** (durable preferences about how the user wants Vexis
  to behave) → `USER.md`, but only after the same claim has appeared
  in ≥2 distinct sessions within 30 days (queue at
  `~/.vexis/learning/user_candidates.json`).
- **SITUATIONAL** (environment / setup facts) → `MEMORY.md`, with
  exact-evidence dedup against existing entries.
- **VOLATILE** (one-shot or temporary) → dropped.

Pinned skills are read-only to the curator. Full design and routing
prompts: `.plans/learning-curator-v2-research.md`.

### Recursion guard

Each `claude -p` review fork writes a NEW session JSONL into the
same projects directory the curator scans for eligibility. Four
mechanisms keep the curator from reviewing its own reviews:

1. **Persistent spawned-UUIDs registry** at
   `~/.vexis/learning/spawned.json`. Every review fork's session UUID
   is appended before the spawn returns; `list_eligible_sessions`
   unions this with the in-memory set so a daemon restart doesn't
   drop the exclusion list.
2. **Content-prefix filter**: `list_eligible_sessions` opens each
   candidate JSONL and skips any whose first user message starts
   with `CURATOR_REVIEW_PROMPT_PREFIX`. Catches legacy backlog and
   anything the persistent registry missed (eval workspaces, restored
   backups, store corruption). The unit test
   `test_curator_prompt_invariant` asserts that the rendered prompt
   actually starts with the constant, so future prompt edits surface
   a test failure rather than a silent filter regression.
3. **Max-attempts cap** at `MAX_REVIEW_FAILURES=3`. After three
   consecutive failures the curator pins the session's
   `last_message_at_review_time` so the eligibility gate filters it
   until the user adds new content (which advances the JSONL's
   `last_message_timestamp` past the pinned snapshot, reopening
   eligibility). Bounds runaway retry loops on transcripts that the
   verifier consistently rejects.
4. **Single-instance PID lock** at `~/.vexis/daemon.pid` (acquired in
   `main.acquire_daemon_lock` before any work). Two concurrent
   daemons can't fan out into each other's spawns; the second
   startup exits 2 with a clear pointer to the live PID.

Cleanup: `scripts/clean_curator_jsonls.py` (dry-run by default,
`--apply` to act) moves curator-owned JSONLs out of a workspace's
projects directory and into a timestamped archive under
`~/.vexis/learning/curator-jsonl-archive/`.

Historical note: v2 shipped with only the in-memory `_spawned_uuids`
set, which didn't survive daemon restart. The May 2026 audit found
2,165 of 2,207 JSONLs in the workspace projects directory were
curator-owned reviews of past curator reviews. The recursion-fix
commit added the persistent registry, the content filter, the
max-attempts cap, and the PID lock; the cleanup script moved the
legacy fanout out of the workspace. Plan:
`.plans/learning-curator-recursion-fix.md`.

## Coherence curator (v3a)

Third curator. Runs inline inside the learning curator's tick: for
every verified lesson, a `claude -p` "judge" call decides whether
the lesson body is properly grounded in the cited evidence string.
Verdicts: COHERENT (silent), NEAR_MISS_REVIEW (soft annotation),
INCOHERENT (hard `Coherence: FLAGGED (<reason>)` annotation in the
shadow file). Advisory-only — never blocks a write.

Surfaces:
- inline `Coherence:` line in MEMORY-SHADOW.md / USER-SHADOW.md /
  staged SKILL.md entries
- `## Coherence flags` section in per-tick REPORT.md (omitted when
  empty)
- `Coherence flags (last N tick reports):` row in `/learning audit`
- `summary.coherence = {flagged, near_miss, by_reason}` in `run.json`
- `/learning coherence-audit [--shadow-only]` to re-judge already-
  promoted entries on demand (degraded mode — no transcript)

Full design and prompts: `.plans/coherence-curator-research.md`.

## Relationships (v3c)

Vexis silently extracts third-party facts from your conversations
and queues them for your approval. Approved facts land in
`<workspace>/RELATIONSHIPS.md`, which the brain reads on its
next session spawn. The brain never sees the candidate queue.

**Default flow.** A relationships extractor (`claude -p`,
sonnet-default since v3c Day 5) runs at every learning-curator
tick over each session that passed triage. The model started as
haiku in 4a but the Day 5 release-gate eval surfaced reliability
gaps haiku couldn't close (83% positive against the 85%
threshold, even after fixture and prompt fixes); sonnet hit
100% on the same corpus. Override to haiku via
`models.relationships_extractor: haiku` in `~/.vexis/config.yaml`
if cost matters more than reliability for a workspace.
Extracted facts get tiered eligibility per
`core/relationships/candidate_store.py`:

- **Strong qualifier cues** (mom, dad, partner, sibling, child,
  etc.): eligible after 1 session.
- **Soft + weak cues**: eligible after ≥2 distinct sessions in
  30 days.

Approve via dashboard (Learning tab → Relationships panel) for
per-fact granularity, or via Telegram:

- `/learning relationships-pending` — list pending candidates.
- `/learning relationships-approve <slug>` — whole-person.
- `/learning relationships-reject <slug>` — tombstone.
- `/learning relationships-digest` — formatted summary.

**Brain-cache hint.** Approval takes effect on the brain's *next*
session — the running session has a cached system prompt. Vexis
appends a `/clear` reminder after each approve. Suppressible via
`relationships.approval_hint_enabled: false` in
`~/.vexis/config.yaml`.

**Explicit-consent fast lane (legacy).** v3b's "remember
that..." path is shipped but runtime-disabled by default. Enable
with `relationships.explicit_consent_enabled: true` if you want
phrasings like "remember that Sarah likes mystery novels" to save
immediately without going through the candidate queue.

**Eval gate.** The haiku-default extractor has an integration
eval at `tests/relationships/test_extractor_eval.py`. Run
deliberately (real `claude -p` calls):

```
pytest tests/relationships/ -m eval
```

Thresholds: ≥85% positive accuracy, ≥95% negative accuracy,
**zero** sensitive-content leaks. v3c shipped on sonnet at
100% positive on Day 5; if you've overridden to haiku and the
eval drops, flip back to sonnet via
`models.relationships_extractor: sonnet`.

Full design: `.plans/relationships-v3c-research.md`.
End-user one-pager: `docs/relationships.md`.

## Goals (v3d)

`/goal <text>` kicks off a multi-step task that Vexis works on
across turns until the goal is reached, paused, or the turn budget
runs out. After every brain turn an auxiliary `claude -p` judge
(sonnet-default; override via `models.goal_judge` in
`~/.vexis/config.yaml`) decides whether the goal is satisfied; if
not and the budget remains, Vexis enqueues a continuation prompt
through the same per-chat FIFO that real user messages use.

**Default flow.**

1. `/goal <text>` — sets the standing goal, kicks off turn 1.
2. Brain replies. The post-turn hook in `transports/telegram.py`
   reads the assistant's final text, calls `judge_goal`, and
   either marks the goal done or enqueues a continuation tagged
   `origin="goal_continuation"`.
3. Loop terminates when the judge says done, the user pauses /
   clears, the turn budget exhausts, or `/cancel` fires.

**Subcommands.**

- `/goal` (alias `/goal status`) — show current state.
- `/goal pause` — soft pause: the in-flight brain turn finishes,
  the loop stops auto-continuing afterwards. Drops queued
  continuations from the FIFO; never kills the brain proc.
- `/goal resume` — resets `turns_used` to 0 and re-enables the
  loop. Next brain turn restarts the judge cycle.
- `/goal clear` — drops the goal. Record retained on disk for
  audit; the chat treats it as no-active-goal.

**Auto-pause on `/cancel`.** A `/cancel` while a goal is active
flips status to paused with `paused_reason="user-cancelled"` and
drops queued continuations. This is the §4 trade-off in
`.plans/goal-command-research.md`: a surprise continuation hours
after a /cancel (when the user thought they were done) is a worse
failure mode than typing `/goal resume` to keep going.

**Soft pause.** `/goal pause` writes paused state and drops
pending continuations from the queue. It does NOT call
`running_tasks.cancel` and does NOT touch any brain subprocess —
the in-flight turn keeps running and lands its reply normally. The
loop just won't auto-continue afterwards. Test pin:
`tests/test_goal_command.py::test_pause_does_not_cancel_running_brain_proc`.

**Default budget.** 20 turns. Override via `goals.max_turns: N` in
`~/.vexis/config.yaml`. On exhaustion the manager auto-pauses with
`paused_reason="turn budget exhausted (N/M)"` — the user can `/goal
resume` to extend with another full budget (resume zeros the count).

**Persistence.** Per-session goal state lives at `~/.vexis/goals.json`,
keyed by Claude session UUID. Sidecar `.lock` + `fcntl.flock` +
atomic temp-rename writes (same idiom as `core/learning_curator.py:SpawnedStore`).
Survives daemon restart; the next user message wakes the loop.
**No auto-fire on boot** — restart safety is "next user message
resumes", not "boot resumes".

**Curator-recursion guard.** Every judge call spawns a `claude -p`
that writes its own session JSONL into the workspace projects
directory. Without a filter the curator would later review each
judge JSONL for lessons. Filter is the content-prefix check at
`core/transcripts.py:_is_curator_owned`, recognising
`GOAL_JUDGE_PROMPT_PREFIX = "You are a strict judge evaluating whether
an autonomous agent"`. The `VEXIS_GOAL_JUDGE=1` env var is set on
the spawn for audit / forensics — same pattern as
`COHERENCE_JUDGE_ENV_VAR`, but note (per the Day 1 audit) that the
env-var-as-filter mechanism is a phantom: no curator code path
reads either env var for filtering. The content-prefix is the
only real filter.

**Prompt-cache invariant.** Continuation prompts are plain
user-role messages of shape
``[Continuing toward your standing goal]\nGoal: <text>\n\n...``.
No system-prompt mutation, no toolset swap. Anthropic prompt
caching stays intact across continuations. Pinned by:

- `tests/test_goal_manager.py::test_continuation_prompt_starts_with_verbatim_prefix`
- `tests/test_goal_manager.py::test_continuation_prompt_no_system_prompt_leak`

**Eval gate.** Six-fixture release-gate eval at
`tests/test_goal_eval.py`. Run when the prompt or judge model
changes:

```
pytest -m eval tests/test_goal_eval.py -v -s
```

Threshold: 100% accuracy on cases (a) clear-done, (b)
clear-continue, (c) unachievable→done, (e) empty→continue, (f)
error→continue. Case (d) ambiguous→continue is advisory — the
verdict is logged for human review without a hard assertion.
Approximate cost: 4 real sonnet judge calls + 2 deterministic
fixtures ≈ $0.05 ceiling.

**Disabled flag.** `goals.enabled` defaults to `True` (v3d Day 4
release flip). Set `goals: {enabled: false}` in
`~/.vexis/config.yaml` to silence the slash command and the
post-turn hook without a code change.

Full design: `.plans/goal-command-research.md`.
End-user one-pager: `docs/goals.md`.

## Brain abstraction (Phase C)

Vexis runs on top of an agent CLI selected at startup by
`brain.kind` in `~/.vexis/config.yaml`. Two implementations
ship today; both satisfy the `core.brain.Brain` ABC so the rest
of vexis (transports, learning curator, goals, schedules,
dashboard, install script) doesn't care which one is running.

- **`claude-code` (default)** — `ClaudeCodeBrain` against the
  `claude` CLI binary. Pre-Phase-C behaviour, byte-equivalent.
  Sessions live in `~/.claude/projects/<encoded-cwd>/`.
- **`opencode` (opt-in)** — `OpenCodeBrain` against the
  `opencode` CLI binary. Sessions live in
  `~/.local/share/opencode/opencode.db`. Opt-in; flipping
  requires the legacy-keys → tier-schema config migration
  documented in `docs/migration.md`.
- **`null`** — `BrainNull`, the test fake. Useful for a vexis
  daemon running without a real model (dashboard-only smoke).

**Default flow.**

1. `main.py` reads `brain.kind` once at startup; logs which
   brain was instantiated.
2. The transport hands user messages to `brain.respond` for
   foreground turns.
3. Aux subsystems (curator, judges, extractors) spawn through
   `brain.spawn_aux(prompt, model_tier=...)` — never directly
   shell out. Tier resolution: subsystem picks an abstract size
   (`tiny` / `small` / `medium` / `large`); brain translates
   per `models.tiers.<brain-kind>.<tier>` config or the built-in
   `DEFAULT_TIER_MAP_<BRAIN>` constants.
4. Curator reads transcripts via `brain.iter_session_metas` +
   `brain.iter_messages` + `brain.is_brain_owned_session`. The
   recursion guard works on either brain's session storage.

**Key files.**

- `core/brain/base.py` — `Brain` ABC + `BrainEvent` variants +
  exception hierarchy (`BrainError`, `BrainTimeoutError`,
  `BrainCancelled`, `SessionLost`, `BrainNotInstalled`,
  `BrainAuthRequired`).
- `core/brain/claude_code.py` — claude-code implementation.
- `core/brain/opencode.py` — opencode implementation, including
  the SQLite reader, `OPENCODE_CONFIG_CONTENT` builder, and
  namespace-prefix MCP merge.
- `core/brain/null.py` — test fake; canned responses + recorded
  call shapes. Default brain in the unit-test suite.
- `core/yaml_config.py` — `brain_kind()`, `model_for_tier()`,
  `subsystem_tier()`, plus the `DEFAULT_TIER_MAP_*` constants.
- `scripts/install.py` — installer; symlinks AGENTS.md ↔
  CLAUDE.md, writes per-brain MCP config (`.mcp.json` for
  claude-code, `opencode.json` with `vexis-` namespace prefix
  for opencode), verifies the binary is on PATH.

**Per-brain test runs.**

```
pytest                              # default suite (BrainNull)
pytest -m brain_smoke               # real claude-code binary
pytest -m brain_smoke_opencode      # real opencode binary
```

**Cross-brain contract.** `tests/test_brain_contract.py`
parametrises 23 inspection-only assertions over all three
implementations (`null` / `claude_code` / `opencode`); 67
total cases. `tests/test_aux_spawn_routing.py` pins each aux
subsystem's tier choice. `tests/test_system_prompt_snapshots.py`
pins structural invariants (no tool-name leaks, opencode
omits the `<available_skills>` block claude-code emits, SOUL
renders before CAPABILITIES).

**Decision posture.** `brain.kind: claude-code` is the default
and stays the default. Opencode is opt-in. Switching brains is
high-friction today (YAML edit + restart + minimal config
migration); the next research after this rollout closes is
**`/model` UX** — slash-commands, dashboard picker, runtime
config edits — which makes the switch productive enough to
dogfood opencode end-to-end.

End-user docs: `docs/brains.md` (per-brain reference),
`docs/migration.md` (opt-in / opt-out flow + the legacy-keys
migration recipe), `docs/dogfood-checklist.md` (12 manual
flows that gate "ready for daily use" on a fresh install).
