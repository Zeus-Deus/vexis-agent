# Codemux orchestration watcher

Tracks long-running terminal-attached agents (Codemux workspaces
today; raw PTYs and tmux later) and pings Telegram when the inner
agent goes idle. Closes the UX gap between `vexis-bg` (which notifies
on subprocess exit) and Codemux-delegated work (where the inner agent
runs in a PTY that never exits).

## Quick start

```bash
# Inside a Vexis-managed Telegram session, the brain runs:
vexis-watch register \
  --name my-build \
  --workspace workspace-1039 \
  --agent-kind claude-code \
  --idle-after 30s \
  --goal "wire codemux watcher into vexis-agent"

# Walk away. ~30s after the inner agent stops emitting bytes,
# you get one Telegram ping:
#
#   Workspace `my-build` (agent: claude-code) went idle after 19m.
#   Goal: wire codemux watcher into vexis-agent
#   Last line: ✓ all tests pass
#   Reply `tail my-build` to peek, `peek my-build` to summarize,
#   `mute my-build` to silence.
```

## Conditional activation

The whole feature is gated on the `codemux` MCP being wired into
`~/.vexis/mcp-servers.yaml`. With it absent:

- `WatcherController` is not instantiated; the polling loop never runs.
- `/codemux` slash command is not registered with the Bot API.
- The watcher header line is not added to the system prompt.
- `vexis-watch` commands return cleanly with the "Codemux MCP not
  configured" message and exit 0 — the daemon is healthy, the feature
  is just off.

This is the zero-cost contract for users who don't use Codemux.

## Architecture

Three layers:

1. **Watcher core** (`vexis_agent/core/watcher/`):
   - `registry.py` — `WatchedAgent` dataclass, JSON persistence at
     `$XDG_STATE_HOME/vexis-agent/watcher-registry.json`.
   - `poller.py` — async polling loop with content-hash diff,
     idle threshold, and oscillation debounce (60s default).
   - `sources/base.py` — `Source` ABC and the in-process registry
     plug-ins register themselves into.
   - `sources/codemux.py` — Codemux source plugin; calls
     `terminal_read`, `workspace_list` via MCP.
   - `mcp_client.py` — long-lived stdio JSON-RPC client for the
     `codemux mcp` subprocess.
   - `__init__.py` — `WatcherController` facade + `codemux_mcp_configured()`
     gate + header-block helper.

2. **System-prompt header** (`vexis_agent/core/brain/claude_code.py`):
   `ClaudeCodeBrain.__init__` accepts `extra_prompt_blocks`, which the
   daemon wires to `WatcherController.header_block()`. The block is
   exactly one line — `Active Codemux work: N workspaces — run
   'vexis-watch status' for details.` — scales flat regardless of how
   many agents are watched. Frozen per session UUID, so mid-session
   registry changes don't perturb the prefix cache; `/clear` rotates
   the UUID and re-resolves.

3. **Telegram surface** (`vexis_agent/transports/telegram.py`):
   - `/codemux` command — registered only when watcher is on.
   - Inline-reply commands (`tail <name>`, `peek <name>`,
     `mute <name>`, `unmute <name>`, `unwatch <name>`) intercepted
     in `_maybe_handle_watch_reply` BEFORE the brain dispatch.
     Matched only when the second token is a known agent name, so
     they don't eat real sentences.

4. **Control-socket dispatch ops** (`vexis_agent/main.py`):
   `watch_register`, `watch_unregister`, `watch_list`, `watch_status`,
   `watch_mute`, `watch_tail`. All return `{ok: false, kind:
   "CodemuxNotConfigured"}` when the watcher isn't wired.

## Adding a new source plugin

A new transport (raw PTY, tmux pane, SSH session) needs nothing
beyond a new file in `vexis_agent/core/watcher/sources/`:

```python
from vexis_agent.core.watcher.sources import (
    Source, SourceDescription, register_source,
)

class TmuxSource(Source):
    source_type = "tmux"
    async def read_recent_output(self, identifier): ...
    async def is_alive(self, identifier): ...
    async def describe(self, identifier): ...

register_source(TmuxSource())
```

Then add an import to `WatcherController.__init__` (or have the user
import the module). The registry, poller, notification rail, CLI, and
slash command all work unchanged.

## Notification contract

- **One ping per idle transition.** When `last_output_at` ages out
  past `idle_after_seconds`, the status flips `running → idle` and
  the notifier fires once.
- **Oscillation debounce.** If `idle → running → idle` happens
  within the oscillation window (default 60s), the second idle
  transition flips the status but DOES NOT re-notify. A long-quiet
  agent that resumes briefly won't spam you on every short break.
- **Death is silent.** `running → dead` (source plugin reports the
  session is gone, or `read_recent_output` raises
  `SourceUnavailable`) does NOT fire a notification. The user
  killed the workspace deliberately — pinging them about it is noise.
- **Mute is per-agent.** `vexis-watch mute <name>` (or Telegram
  inline `mute <name>`) sets a sticky flag; the loop still tracks
  state changes but the notify path is short-circuited until
  `vexis-watch unmute <name>` re-arms it.

## Visibility on the generic status surfaces

Registration is not just the idle ping — it is what makes a
delegation *visible*. Watched agents are rendered by
`core/watcher/views.py` and composed into:

- Telegram `/tasks` and `/status` (the `👁 Watched workspaces:` block),
- the dashboard status page (`watched_agents` in `/api/v1/status`).

See `docs/active-work-visibility.md` for the full contract. The
historical bug this closes: the brain delegated to a workspace,
truthfully said "still working", and `/tasks` replied "No background
tasks running." because only the vexis-bg registry was consulted.

## Inline reply commands

Telegram-side commands recognised when their second token matches a
registered agent name (intercepted in `_on_text` before the brain
dispatch):

| Inline reply           | Effect                                              |
|------------------------|-----------------------------------------------------|
| `tail <name>`          | Last 20 lines of the workspace's terminal scrollback. |
| `peek <name>`          | Synthesises a brain turn: "Summarize what `<name>` is doing right now." |
| `mute <name>`          | Silence future idle pings for this agent.           |
| `unmute <name>`        | Re-arm idle pings.                                  |
| `unwatch <name>`       | Deregister from the registry.                       |

Real user sentences that happen to start with `tail`, `peek`, etc.
fall through to the brain because the second token isn't a known
agent name — the registry membership check is the gate.

## `vexis-watch` CLI reference

```
vexis-watch register --name <h> --workspace <id> --agent-kind <kind>
                     [--source codemux] [--idle-after 30s]
                     [--goal "<one-liner>"] [--repo <path>]
vexis-watch list                      # all watched agents as JSON
vexis-watch status [--name <h>]       # one or all
vexis-watch unregister <h>
vexis-watch mute <h>                  # stop notifying
vexis-watch unmute <h>                # re-arm
vexis-watch tail <h> [--lines 20]
```

All commands emit one line of JSON to stdout (vexis-bg style). The
chat_id used at `register` time comes from `$VEXIS_CHAT_ID` (the
foreground brain sets it; export it manually from a shell).

### Exit codes (load-bearing contract)

| Exit | Meaning                                                       |
|------|---------------------------------------------------------------|
| `0`  | Operation succeeded. Result JSON on stdout.                   |
| `0`  | Codemux MCP not configured — daemon is healthy, feature off. Stderr has the message; stdout empty. **By design**, so a skill can call `vexis-watch register` unconditionally without a pre-check and get a no-op instead of a crash. |
| `1`  | Daemon error or operation failure (unknown agent, duplicate name, malformed args, source unavailable, daemon unreachable). Stderr has the message. |

Callers that genuinely need to branch on "ok vs MCP-off" can either
inspect stdout (empty → MCP off) or call `vexis-watch list` first
(`[]` → wired but empty registry; CodemuxNotConfigured message →
MCP off). The convention favours the common case: skills shouldn't
have to special-case the off path.

## Tests

- `tests/test_watcher.py` — registry persistence, poller idle
  transitions, debounce, mute, death, header-block one-line
  guarantee, pluggable-source contract.
- `tests/test_watcher_prompt_injection.py` — header appears,
  zero-cost when provider is None, per-session-UUID cache freezes.
- `tests/test_watcher_dispatch.py` — control-socket ops, watcher-
  absent gating returns `CodemuxNotConfigured`, register/list/tail
  round trip.

## Out of scope (v1)

- Multi-user fan-out — Vexis is single-user.
- Inner-agent tool-use / thinking-trace parsing — still pure PTY
  bytes.
- A dedicated dashboard tab — `/codemux` and `vexis-watch status`
  cover the use case for v1; revisit if usage warrants.
- Skill-file changes — the codemux-delegation / codemux-
  orchestration skills are managed separately by Vexis. The only
  daemon-side contract is that `vexis-watch register` is callable
  from inside a skill.
