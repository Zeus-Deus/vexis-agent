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
