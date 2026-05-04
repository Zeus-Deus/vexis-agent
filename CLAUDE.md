# Vexis-Agent

Standalone Python daemon. Telegram bot + `claude -p` bridge for controlling an Omarchy (Hyprland/Wayland) desktop from a phone.

This is a transport layer in front of Claude Code, not a new agent. Telegram in, MCP tools out, Claude Code in the middle.

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
haiku-default) runs at every learning-curator tick over each
session that passed triage. Extracted facts get tiered eligibility
per `core/relationships/candidate_store.py`:

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
**zero** sensitive-content leaks. Below threshold, flip
`models.relationships_extractor: sonnet` in
`~/.vexis/config.yaml` and re-run.

Full design: `.plans/relationships-v3c-research.md`.
