# Brains

Vexis runs on top of an agent CLI. Two are supported today; both
implement the same `core.brain.Brain` ABC so the rest of vexis
(transports, learning curator, goals, schedules, dashboard) doesn't
care which one is running. Switch via `brain.kind` in
`~/.vexis/config.yaml`. Default is `claude-code`.

## claude-code (default)

The original brain vexis was built on. Recommended for users who
already have a Claude Pro/Max subscription or an Anthropic API key.

- **Install**: <https://docs.anthropic.com/claude/claude-code>
- **Auth**: Claude Pro/Max subscription via `claude /login`, OR
  Anthropic API key via the `ANTHROPIC_API_KEY` env var.
- **Session storage**: per-session JSONL at
  `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`. The encoded-cwd
  is `s/[/.]/-/g` against the absolute workspace path — e.g.
  `/home/zeus/vexis-workspace` → `-home-zeus-vexis-workspace`.
- **MCP config**: `<workspace>/.mcp.json` (claude-code's
  convention), shape `{"mcpServers": {<name>: {"command", "args",
  "env"}}}`. Vexis writes this once via
  `scripts/install.sh`; the curator never rewrites it.
- **Project instructions**: `<workspace>/CLAUDE.md` (canonical;
  gitted).
- **Tool naming**: PascalCase (`Read`, `Edit`, `Bash`, `TodoWrite`,
  …).

## opencode (opt-in)

Alternative brain that supports a wider set of model providers
(Anthropic, OpenAI, Google, GitHub Copilot, OpenCode Zen,
plus 30+ via API key). Useful if you want to:

- run vexis against a model other than Claude (e.g. GPT, Gemini,
  Grok, a self-hosted Llama),
- consolidate billing under an existing ChatGPT Plus / GitHub
  Copilot / Anthropic OAuth subscription instead of an API key,
- experiment with opencode's native tooling (different tool naming,
  built-in skill discovery).

- **Install**:
  ```bash
  curl -fsSL https://opencode.ai/install | bash
  ```
- **Auth**: `opencode providers login` (interactive prompt to pick
  a provider + auth method). The legacy alias `opencode auth login`
  still works — both invoke the same code path. Common providers:
    - **Anthropic OAuth** (Claude Pro/Max subscription) —
      `opencode providers login` → select "Anthropic" → OAuth flow
    - **ChatGPT Plus** (OpenAI OAuth) — same flow, select "OpenAI"
    - **GitHub Copilot** — same flow, select "GitHub Copilot"
    - **Any of 30+ API-key providers** — same flow, select
      provider, paste API key

  Credentials are stored at `~/.local/share/opencode/auth.json` (verify
  with `opencode providers list`).
- **Session storage**: SQLite at
  `~/.local/share/opencode/opencode.db`. Three tables matter to
  vexis: `session` (per-session metadata, filtered by `directory`
  column for project scope), `message` (conversational turns,
  `data` is JSON role+timestamps), `part` (text/tool/step parts,
  `data` is JSON typed-payload).
- **MCP config**: vexis writes its servers to
  `<workspace>/opencode.json` under the `mcp:` block, namespaced
  with a `vexis-` prefix (e.g. `vexis-codemux`). Vexis only
  touches prefixed entries — any non-prefixed `mcp:` servers you
  add by hand are preserved byte-for-byte across vexis updates.
- **Project instructions**: `<workspace>/AGENTS.md` (canonical for
  opencode), with `<workspace>/CLAUDE.md` as a fallback (unless
  `OPENCODE_DISABLE_CLAUDE_CODE_PROMPT=1` is set; verified at
  `packages/opencode/src/session/instruction.ts:13-17`). The
  install script symlinks `AGENTS.md → CLAUDE.md` so both files
  see the same content.
- **System prompt injection**: vexis spawns each foreground turn
  with `OPENCODE_CONFIG_CONTENT=<json>` in the env, carrying an
  in-memory `agent: { vexis: {...} }` block. Per-spawn isolation,
  no shared file state. Vexis does NOT write to
  `<workspace>/.opencode/agent/`. If you want to override vexis's
  prompt for debugging, write your own file at
  `<workspace>/.opencode/agent/<name>.md` and run
  `opencode run --agent <name>` from a separate shell — that path
  is yours, vexis won't touch it.
- **Skills**: vexis's `<workspace>/skills/<category>/<name>/SKILL.md`
  layout is picked up by opencode's native skill discovery
  (pattern `skills/**/SKILL.md`, verified at
  `packages/opencode/src/skill/index.ts:24`). The system-prompt
  `<available_skills>` block is injected by opencode itself;
  vexis's `build_system_prompt` skips its own block on this brain
  to avoid duplication. Net effect: same skills, same index, no
  duplicate rendering.
- **Tool naming**: lowercase. The canonical IDs vexis sees on the
  event stream are `read`, `edit`, `write`, `glob`, `grep`,
  `todowrite`, `bash` (note: the source directory is `tool/shell/`
  but the exposed `ToolID = "bash"` is preserved for compat with
  existing plugins/permissions; verified at
  `packages/opencode/src/tool/shell/id.ts:14-16`).

## Switching

1. Install the new brain (see install instructions above).
2. Authenticate: claude-code's `claude /login` or opencode's
   `opencode providers login`.
3. Edit `~/.vexis/config.yaml`:
   ```yaml
   brain:
     kind: opencode    # or claude-code
   ```
4. Restart `vexis`.

Sessions are not portable between brains — each brain has its own
session storage and there's no migration path. A switch starts you
with a fresh session. **Memory, USER.md, RELATIONSHIPS.md, skills,
goals, and schedules persist across the switch** — those are
vexis-owned artefacts under `<workspace>/` and `~/.vexis/`,
brain-agnostic by design.

## Models

Vexis uses size tiers (`tiny`/`small`/`medium`/`large`) for its
background subsystems (curator, judges, extractors). Each brain has
its own tier→model mapping in `~/.vexis/config.yaml` under
`models.tiers.<brain-kind>`. Defaults:

- `claude-code`: `tiny`/`small` → `haiku`, `medium`/`large` →
  `sonnet`. Maps to whatever your Claude account's current haiku /
  sonnet aliases resolve to.
- `opencode`: `tiny`/`small` → `anthropic/claude-haiku-3-5`,
  `medium` → `anthropic/claude-sonnet-3-7`, `large` →
  `anthropic/claude-sonnet-4`. Provider-prefixed because opencode
  needs the explicit `provider/model` shape.

Override per tier:

```yaml
models:
  tiers:
    opencode:
      large: openai/gpt-5         # use GPT-5 for the judge
      medium: anthropic/claude-sonnet-4
```

The brain-facing default (the `respond` foreground turn) is the
brain's own native default — vexis doesn't pass `--model` for
foreground turns. Override per provider with the brain's own config
(`opencode tui` → settings, or `claude --model`).

## Before declaring opencode ready for daily use

Run the [dogfood checklist](dogfood-checklist.md) — 12 manual flows
covering cold-boot, multi-turn, tool firing, MCP, `/cancel`,
`/goal`, `/schedule`, daemon restart, bad auth, bad install, and
the two recursion-guard / async-cancel races that are non-
negotiable. Steps 11 and 12 catch failure modes that would corrupt
the curator state silently if missed.

## Known limitations on opencode

> Populated as the dogfood pass surfaces issues. Empty for now —
> a clean dogfood run produces no entries here. Day 8's flag-posture
> decision reads this section.

## Architecture references

- `core/brain/base.py` — `Brain` ABC + `BrainEvent` variants +
  exception hierarchy.
- `core/brain/claude_code.py` — claude-code implementation.
- `core/brain/opencode.py` — opencode implementation, including
  the SQLite reader, `OPENCODE_CONFIG_CONTENT` builder, and
  namespace-prefix MCP merge.
- `core/yaml_config.py` — `brain_kind()`, `model_for_tier()`,
  `subsystem_tier()` — the configuration surface every brain
  reads through.
- `scripts/install.py` — installer; symlinks AGENTS.md ↔
  CLAUDE.md, writes per-brain MCP config, verifies the binary is
  on PATH.

For the full design rationale, see the planning doc lifecycle:
- Phase A (ABC extract) — `core/brain/__init__.py` + `base.py`
- Phase B (aux-spawn routing) — every aux subsystem now spawns
  through `brain.spawn_aux`
- Phase C (opencode) — Days 3–6 land scaffold, session resume,
  SQL transcript reader, stream resilience, install hook

## Phase C close — flag posture

`brain.kind: claude-code` is the default and stays the default.
Opencode is opt-in. Three reasons this is the right posture for
Phase C:

1. **Existing user base is on claude-code.** Flipping the default
   would force every existing install through the
   legacy-keys → tier-schema config migration documented in
   [`docs/migration.md`](migration.md) without warning. That's a
   surprise. Opt-in keeps the migration intentional.
2. **Full opencode dogfood is deferred** until the `/model`
   slash-command UX ships (next research after this rollout
   closes). Switching brains via YAML is too high-friction for
   productive end-to-end dogfood — the 12-flow checklist takes
   an evening of real Telegram use, and clicking through a
   text-editor → daemon-restart loop per flow is dispiriting
   enough that the dogfood gets cut short. `/model` UX makes the
   switch productive enough to dogfood properly.
3. **The goal of this rollout was opt-in support, not a default
   flip.** Phase C enables opencode users to install vexis and
   point it at their existing OpenCode auth + provider mix. That
   ship has sailed; flipping the default for existing users is a
   separate decision that depends on the dogfood pass that's
   blocked on `/model` UX.

Phase D (or whenever the dogfood pass clears) is the right time
to revisit the default.

Verification at Phase C close (Day 8):

- Default test suite: 1441 pass (1438 from Day 7 + 3 new
  claude-code smoke tests gated by `-m brain_smoke`).
- Smoke runs: claude-code happy path / resume / cancel-mid-turn
  all pass against the real `claude` binary; opencode equivalent
  passes against the real `opencode` binary.
- Curator tick benchmark (`scripts/bench_curator_tick.py`):
  ~4.45 ms/tick mean over five 500-sample runs vs. the 4.40 ms
  Day 5 baseline → +1.0% delta, well within the 5% §8 risk #7
  budget. The brain-abstraction layer adds no measurable
  per-tick overhead.
