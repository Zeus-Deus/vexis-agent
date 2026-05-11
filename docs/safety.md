# Safety hook (Step 6.5)

Hard enforcement layer for destructive Bash commands. Sits in front
of every `Bash` tool call the brain makes (foreground turns AND aux
spawns like curator / goal-judge) and denies the call if it matches
the regex tripwire in `core.safety.check_command()`.

Two-layer model:

* **Soft (Step 1):** the default workspace `CLAUDE.md` template tells
  the brain *"Confirm destructive actions before running them."*
  Asking nicely. Free; works most of the time.
* **Hard (Step 6.5, this doc):** Claude Code `PreToolUse` hook that
  consults the same regex set and emits a `deny` verdict regardless
  of whether the model asked first. Belt + suspenders.

## Wiring

Three pieces:

| Module | Role |
| --- | --- |
| `core.safety` | Pure regex tripwire. Step 6 — was already built before Step 6.5 landed. |
| `core.safety_hook` | Pure payload-→-verdict logic for the hook entry point. |
| `core.safety_install` | Atomic + idempotent writer for `<workspace>/.claude/settings.json`. |
| `cli safety-hook` subcommand | The actual process Claude Code spawns for each Bash invocation. |

At daemon startup `BrainClaudeCode.__init__` calls
`ensure_workspace_safety_hook(workspace)`, which:

1. Reads existing `<workspace>/.claude/settings.json` (or `{}`).
2. Finds or creates `hooks.PreToolUse[<matcher=Bash>].hooks[]`.
3. Looks for an entry whose `command` field contains the sentinel
   `vexis_agent.cli safety-hook` — if present, updates it in place;
   otherwise appends a new entry.
4. Atomic write only if the resulting JSON differs from disk.

User-owned keys in `settings.json` are preserved verbatim. User
hooks for other tools, other PreToolUse matcher groups, and
sibling entries inside the Bash group are all left alone.

## Wire protocol

Claude Code spawns the hook with the PreToolUse payload on stdin:

```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {"command": "rm -rf /tmp/x", "description": "..."}
}
```

For destructive commands the hook writes a deny verdict to stdout:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Vexis safety hook blocked: recursive/forced rm"
  },
  "systemMessage": "Vexis safety hook blocked: recursive/forced rm"
}
```

For benign commands the hook writes nothing and exits 0 — Claude
Code falls through to its normal permission flow (which, under
`--permission-mode bypassPermissions`, is "allow").

## Fail-open philosophy

Every error path in the hook subprocess exits 0 with no stdout:

* Invalid JSON on stdin → log to stderr, allow.
* `payload_verdict()` raises → log, allow.
* stdin oversize (>256 KiB) → log, allow.
* Hook installer can't write `settings.json` → log, daemon continues
  without the hook (degraded safety beats broken startup).

The reasoning: a broken hook must never block a working brain. The
tripwire is advisory protection on top of the model's own
discretion — losing it temporarily is acceptable; losing the daemon
entirely is not.

## What's covered today

Patterns the tripwire denies — see `core/safety.py` for the
authoritative regex set:

* `rm -rf` (recursive AND force, any flag order)
* `dd if=` / `dd of=` (raw block device IO)
* `curl … | bash` / `wget … | sh` (pipe-to-shell installs)
* `mkfs.*` (filesystem creation)
* `chmod -R 777`
* `git push -f` / `git push --force`
* `git reset --hard`
* Redirects to `/dev/sd*`, `/dev/nvme*`, `/dev/hd*`, `/dev/mmcblk*`
* `sudo` (any invocation)

What it does NOT catch (by design):

* Non-Bash tools (Edit, Write, etc.). The regex tripwire only
  understands shell strings; gating file-mutating tools needs a
  different signal and is out of scope.
* Bash in disguise — e.g. `bash -c "$(printf 'rm -rf /')"`. The
  regex matches the literal `rm -rf` substring inside the quoted
  payload, so this specific shape IS caught, but a sufficiently
  obfuscated invocation (base64-decode-then-eval, multi-stage
  variable indirection) will slip through. This is a tripwire,
  not a sandbox.
* `RM -RF` (uppercase). Documented carve-out in `test_safety.py` —
  `\brm` is case-sensitive and uppercase `RM` is typically a
  no-op binary on Linux. Not worth the false-positive risk.

## Brain coverage

| Brain | Hard enforcement (Step 6.5) | Soft enforcement (CLAUDE.md) |
| --- | --- | --- |
| `claude-code` (default) | **Yes** — PreToolUse hook | Yes |
| `opencode` (opt-in) | **No — follow-up** (see below) | Yes |
| `null` (test fake) | N/A — no real tool calls | N/A |

OpenCode's hook surface is structurally different from Claude
Code's: plugins are TypeScript modules running in Bun, not
subprocesses reading stdin. The right surface is the
`permission.ask` plugin hook, which can override the
permission verdict to `"deny"` for a given tool call. The plan:

1. Ship a small TS plugin under `vexis_agent/data/` (analogous to
   how `CAPABILITIES.md` is shipped as workspace content).
2. Have `safety_install` (or a sibling installer) write the plugin
   path into `<workspace>/opencode.json`'s `plugin: [...]` array
   alongside the existing `agent.*.permission` merger.
3. The plugin either reimplements the regex set in TypeScript
   (faster, no Python dep at hook time) OR shells out to
   `python -m vexis_agent.cli safety-hook` (single source of
   truth at the cost of a Python startup per call).

Until that lands, **opencode foreground turns rely on soft
prompting only**. The auxiliary spawn path (judges, extractors)
already denies shell entirely via the existing
`permission.shell = "deny"` ruleset Vexis writes in
`_OPENCODE_CONFIG_CONTENT`, so the gap is foreground-only.

## Future work (not in Step 6.5)

* **OpenCode plugin** — see the table above.
* **Override channel.** The user has no way to say "yes really, run
  this destructive command" from Telegram today. The brain has to
  relay the deny and tell the user to run it from their own shell.
  A `/force` prefix or a per-turn env-var unlock is a future
  enhancement; deliberately deferred to keep Step 6.5 small.
* **Counter telemetry.** Each deny logs a single
  `safety_install: …` line on first write but no per-fire log. If
  we ever want a real "blocked vs ran cold" ratio, the hook should
  also emit a structured log line per deny. Cheap to add later;
  the CLI subcommand already has the verdict in hand.
* **Non-Bash gating.** If we ever want to deny Edit/Write inside
  specific paths (e.g. block writes to `/etc/`), it would be a
  parallel matcher group with its own payload_verdict variant.
