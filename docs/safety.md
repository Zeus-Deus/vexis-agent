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

| Module | Role | Brain |
| --- | --- | --- |
| `core.safety` | Pure regex tripwire (Step 6). Source of truth for destructive patterns. | both |
| `core.safety_hook` | Pure payload-→-verdict logic for the claude-code CLI subcommand. | claude-code |
| `core.safety_install` | Atomic + idempotent writers for both brain config files. | both |
| `cli safety-hook` subcommand | Subprocess Claude Code spawns for each Bash invocation. | claude-code |
| `data/opencode_safety_plugin.mjs` | ESM plugin loaded by opencode at startup. Hooks `tool.execute.before` on the `bash` tool. | opencode |

### Claude Code path

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

### OpenCode path

At daemon startup `OpenCodeBrain.__init__` calls
`ensure_opencode_safety_plugin(workspace)`, which:

1. Copies `vexis_agent/data/opencode_safety_plugin.mjs` to
   `<workspace>/.vexis-opencode-safety.mjs` (overwrites only if
   the shipped version differs from disk).
2. Merges the relative path `./.vexis-opencode-safety.mjs` into
   `<workspace>/opencode.json`'s `plugin: [...]` array. Existing
   user-owned plugin entries are preserved; ours is matched by
   filename sentinel and updated in place rather than duplicated.
   Both bare-string (`"./x.mjs"`) and tuple-shaped
   (`["./x.mjs", {opts}]`) plugin entries are supported.

The plugin exports `tool.execute.before` and only acts when
`input.tool === "bash"`. For destructive commands it rewrites
`output.args.command` to a benign-but-failing shim
(`printf 'BLOCKED …' >&2; exit 1`) so the bash tool mechanically
runs to completion with a non-zero exit + stderr explanation. The
model receives the failure in its tool_result and learns it was
blocked.

The regex set in the plugin is hand-mirrored from `core/safety.py`.
Drift is caught by `tests/test_safety_opencode_plugin_parity.py`,
which runs both regex sets against an identical fixture list and
asserts byte-for-byte verdict agreement. Add a pattern to
`core/safety.py` without updating the plugin → that test fails.

#### Why `tool.execute.before` and not `permission.ask`

OpenCode's shell tool calls `ctx.ask({permission: "bash", patterns: [...]})`
which would let a `permission.ask` plugin hook override the verdict
to `"deny"`. But Vexis spawns opencode with
`--dangerously-skip-permissions`, and the CLI auto-replies `"once"`
to every permission event at `run.ts:548` — short-circuiting plugins
that depend on the ask flow. `tool.execute.before` fires
unconditionally, giving us the raw args, so it's the surface that
actually works under our spawn flags.

#### Why command rewriting and not throwing

`plugin.trigger` wraps hooks in `Effect.promise` (see
`packages/opencode/src/plugin/index.ts:266`). A thrown promise
rejection becomes an effect die-defect — fatal to the whole turn,
not a graceful tool-call block. Mutating the command keeps the
tool execution succeeding mechanically while delivering a clear
failure the model handles as a tool error.

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
| `opencode` (opt-in) — foreground | **Yes** — `tool.execute.before` plugin | Yes |
| `opencode` (opt-in) — aux spawns | **Yes** — `permission.shell = "deny"` in `_OPENCODE_CONFIG_CONTENT` (already, pre-Step-6.5) | Yes |
| `null` (test fake) | N/A — no real tool calls | N/A |

## Future work (not in Step 6.5)

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
