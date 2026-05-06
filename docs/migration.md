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

3. **Edit `~/.vexis/config.yaml`**:
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
