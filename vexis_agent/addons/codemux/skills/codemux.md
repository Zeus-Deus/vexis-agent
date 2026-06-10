# Codemux orchestration

You can drive Codemux directly through MCP tools. The codemux add-on
also exposes `vexis-watch` for register/list/tail/mute and `/codemux`
in Telegram for status.

## When to reach for what

| You want to… | Use |
|---|---|
| Open / close workspaces, read terminal output, run git ops | `mcp__codemux__*` tools (workspace_open, terminal_read, terminal_write, git_status, git_commit, git_push, browser_*, etc.) |
| Watch a long-running agent in another workspace and get pinged when it idles | `vexis-watch register --workspace <id> --name <label>` (CLI) or ask Vexis to do it |
| See the status of every watched workspace | `/codemux` in Telegram |
| Tail recent output from a specific watched agent | `/codemux` then reply `tail <name>`, or `vexis-watch tail <name>` |
| Mute pings for a flapping agent | `/codemux` then reply `mute <name>`, or `vexis-watch mute <name>` |
| Stop watching | `vexis-watch unregister <name>` |

## Conventions

- **Register agents you care about.** The watcher is opt-in per
  workspace — un-watched workspaces don't ping. Aim for "register
  when I start a long task, unregister when done."
- **Register BEFORE you tell the user it's running.** Registration is
  also visibility: watched workspaces show up in `/tasks`, `/status`,
  and on the dashboard. An unregistered delegation is invisible —
  `/tasks` will tell the user "nothing running" while your delegate
  works. Pass `--goal` so the listing says what it's doing.
- **Use the workspace_id, not the session_id.** `vexis-watch
  register --workspace <id>` resolves the active terminal pane
  automatically. You don't need to chase the session id yourself.
- **Idle threshold defaults to 30s.** Bump per-agent with
  `--idle-after-seconds N` when registering — useful for
  workspaces where the inner agent legitimately pauses a long time.

## Heads-up

- The MCP only exposes the ACTIVE Codemux workspace's terminals.
  If `vexis-watch register --workspace X` fails with
  "WorkspaceNotActive," focus X in Codemux first and retry.
- Idle pings honour a 60s oscillation-debounce window. An agent
  that flickers idle → running → idle won't double-ping.
