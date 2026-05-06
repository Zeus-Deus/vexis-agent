# Migrating from claude-code to opencode (or back)

For existing vexis users on the claude-code brain — what changes,
what doesn't, and how to opt in.

## Do nothing to stay on claude-code

The default `brain.kind` is `claude-code`. Phase C of the brain
abstraction shipped opencode as opt-in; the claude-code path is
byte-equivalent to pre-Phase-C behaviour. If you didn't change
anything, nothing changed.

## Opting into opencode

1. **Install opencode**:
   ```bash
   curl -fsSL https://opencode.ai/install | bash
   ```

2. **Authenticate with a provider**:
   ```bash
   opencode providers login
   ```
   Pick from the interactive list. If you have a Claude Pro/Max
   subscription, select "Anthropic" — opencode uses the same OAuth
   flow `claude /login` does. Other common picks: ChatGPT Plus
   (OpenAI), GitHub Copilot, or any of 30+ API-key providers. See
   `docs/brains.md` for the full list.

3. **Edit `~/.vexis/config.yaml`** — set `brain.kind` AND
   migrate any legacy raw-string model keys to the abstract-tier
   schema (see "Switching to opencode: minimal config" below for
   why):
   ```yaml
   brain:
     kind: opencode
   ```

4. **Re-run the install script** to write opencode's MCP config:
   ```bash
   ./scripts/install.sh
   ```
   Idempotent — re-running the script doesn't churn existing files.
   This step writes `<workspace>/opencode.json` with vexis's MCP
   servers under the `vexis-` namespace prefix and creates the
   `<workspace>/AGENTS.md` symlink to `<workspace>/CLAUDE.md`.

5. **Restart vexis**:
   ```bash
   # however you run vexis — systemctl, screen/tmux session, etc.
   ```

## Switching to opencode: minimal config

If your `~/.vexis/config.yaml` carries legacy raw-string model
keys (the pre-Phase-B style, e.g. `models.learning_review:
sonnet`), flipping `brain.kind: opencode` without also migrating
those keys will fail at first aux spawn.

**Why it fails.** Vexis's tier resolver passes legacy raw-string
keys (anything not in `{tiny, small, medium, large}`) through
unchanged to the brain — for back-compat with claude-code which
accepts `claude --model sonnet` directly. opencode's `--model`
flag requires `provider/model` shape (`anthropic/claude-sonnet-3-7`,
`openai/gpt-4o`, etc.). A bare `sonnet` produces:

```
Error: Model not found: sonnet/.
```

**Minimal config that works on opencode.** Replace each legacy
raw-string subsystem key with the abstract-tier equivalent under
the new `models.subsystems` block:

```yaml
brain:
  kind: opencode

models:
  brain: default                  # foreground brain — leave as default
                                  # (display-only on the dashboard;
                                  # the foreground turn never passes
                                  # --model to the brain)

  # NEW abstract-tier schema. Each subsystem picks a size tier;
  # `models.tiers.opencode.<tier>` (or the built-in
  # DEFAULT_TIER_MAP_OPENCODE) translates to the right
  # provider/model id at spawn time.
  subsystems:
    learning_review: small        # was "sonnet"
    learning_triage: tiny         # was "haiku"
    coherence_judge: small        # was "sonnet"
    relationships_extractor: medium
    relationships_classifier: tiny
    goal_judge: large
    curator: small
```

The defaults this maps to (built-in `DEFAULT_TIER_MAP_OPENCODE`):

| Tier   | Resolves to                          |
|--------|--------------------------------------|
| tiny   | `anthropic/claude-haiku-3-5`         |
| small  | `anthropic/claude-haiku-3-5`         |
| medium | `anthropic/claude-sonnet-3-7`        |
| large  | `anthropic/claude-sonnet-4`          |

**Override per tier** if you want different models (e.g. use GPT
for the goal judge, Claude for everything else):

```yaml
models:
  tiers:
    opencode:
      large: openai/gpt-4o            # goal_judge → GPT-4o
      medium: anthropic/claude-sonnet-4
```

**Both brains in one config**. You can keep both legacy keys AND
the new schema side-by-side. claude-code reads the legacy
raw-string passthrough; opencode reads the
`models.subsystems.*` block (which takes precedence). One config,
two brains, switchable via `brain.kind` alone:

```yaml
brain:
  kind: claude-code   # flip to opencode without other edits

models:
  # Legacy keys — claude-code reads these (raw-string passthrough).
  learning_review: sonnet
  learning_triage: haiku
  coherence_judge: sonnet

  # New schema — opencode reads these (resolves via tier map).
  # Path 1 (subsystems block) wins over Path 2 (legacy keys) for
  # any subsystem that appears in both.
  subsystems:
    learning_review: small
    learning_triage: tiny
    coherence_judge: small
    relationships_extractor: medium
    relationships_classifier: tiny
    goal_judge: large
    curator: small
```

**One known dead knob.** `models.migration_classifier` is declared
but no live spawn caller reads it (it surfaces only on the
dashboard's models display). Setting it has no runtime effect on
either brain. Safe to leave or remove.

---

## What persists across the switch

Brain-agnostic vexis artefacts:

- `<workspace>/memories/MEMORY.md` and `USER.md`
- `<workspace>/RELATIONSHIPS.md`
- `<workspace>/skills/**/SKILL.md` (opencode auto-discovers these
  natively; same content, same index)
- `~/.vexis/goals.json` (per-session standing goals)
- `~/.vexis/schedules.json` (recurring fire-and-forget jobs)
- `~/.vexis/learning/reviewed.json`,
  `~/.vexis/learning/spawned.json`,
  `~/.vexis/learning/user_candidates.json`

These are stored under vexis-owned paths, not the brain's session
storage, so they survive a brain switch unchanged.

## What does NOT persist

- **Conversation sessions.** Each brain has its own session storage
  (claude-code: JSONL files under `~/.claude/projects/`; opencode:
  rows in `~/.local/share/opencode/opencode.db`). Sessions don't
  migrate. After the switch your next message starts a fresh
  session — you'll see a "Started new session" line in the daemon
  log. Memory and skills carry the relevant context forward, so the
  brain still knows who you are and what you're working on; it
  just won't remember yesterday's `/goal` thread word-for-word.
- **In-flight goals and schedules.** Goal state is per-session;
  switching brains starts a new session, so any goal that was
  in-progress on claude-code lands as `expired/old session`. Re-run
  `/goal <text>` after the switch to resume the same task on the
  new brain. Schedules persist as they're keyed by chat_id, not
  session — those keep firing.
- **The brain's session resume token.** Vexis stores the brain's
  current session id in `~/.vexis/sessions.json`, and `set()` /
  `rotate()` are brain-agnostic — but the token VALUE is opaque to
  vexis (claude-code: UUID; opencode: `ses_<base32>`). When you
  switch, the stored token is invalid for the new brain and
  rotation kicks in on first message.

## Switching back to claude-code

Same flow in reverse. Install claude-code if you uninstalled it,
authenticate (`claude /login` or `ANTHROPIC_API_KEY` env), edit
`~/.vexis/config.yaml` with `brain.kind: claude-code`, re-run
`./scripts/install.sh`, restart vexis.

The persistence story is symmetric — memory/skills/goals/schedules
keep working; only the conversation session resets.

## Verifying the switch worked

Send a message on the new brain. Vexis logs the active brain at
startup:

```
Brain: OpenCodeBrain (brain.kind=opencode)
```

(or the claude-code equivalent). The `/status` command shows the
session token; for opencode it'll start with `ses_`.

To verify MCP tools are reachable on opencode, ask:

> "what's the omarchy keybind for fullscreen?"

If omarchy-kb is configured and reachable, the brain replies with
content from the omarchy-kb knowledge base. If it isn't, double-
check the MCP config write happened by inspecting
`<workspace>/opencode.json` — there should be entries prefixed
`vexis-` under the `mcp:` block.

## When things go wrong

- **"BrainAuthRequired" on first message** — the auth step didn't
  take. Re-run `opencode providers login` and verify with
  `opencode providers list` (the auth file is at
  `~/.local/share/opencode/auth.json`).
- **"BrainNotInstalled" at startup** — the binary isn't on PATH.
  `which opencode` should print a path; if not, the
  `curl ... | bash` install step didn't add the install bin
  directory to PATH (typically `~/.local/bin`).
- **MCP tools missing** — re-run `./scripts/install.sh`. The
  install script's idempotence contract means it's always safe to
  re-run.

## Reference

For the full per-brain design — auth modes, session storage shape,
MCP config strategy, tool naming, etc. — see [`docs/brains.md`](brains.md).

For the manual verification flow before declaring opencode ready
for daily use, see [`docs/dogfood-checklist.md`](dogfood-checklist.md).
