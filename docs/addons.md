# Add-on system

Vexis-Agent ships a small but complete plug-in system that lets you
add new functionality WITHOUT touching the core. Add-ons can
register Telegram slash commands, control-socket dispatch handlers,
background tasks, watcher source plugins, system-prompt blocks,
MCP server defaults, skills, and dashboard pages — all via a
single `PluginContext` facade.

Add-ons live in folders, are discovered at daemon startup, and
load only when explicitly enabled in `~/.vexis/config.yaml` —
except bundled core features (the **browser**), which are on by
default so an upgrade never silently drops them (see Discovery
roots). A bad add-on cannot kill the daemon — failures are logged
and the loader continues.

## Quick start

Write an add-on:

```
~/.vexis/addons/hello/
├── addon.yaml          # manifest (see schema below)
└── __init__.py         # def register(ctx): ...
```

Enable it:

```bash
vexis-addons enable hello
# restart vexis-agent
```

Inspect what's available:

```bash
vexis-addons list
vexis-addons inspect hello
vexis-addons doctor       # check unmet requirements
```

That's the whole loop. No vexis-agent release required.

## Discovery roots

Add-ons are searched in this order; the first definition of a given
name wins (with a warning logged for shadowed entries):

1. **Bundled** — `vexis_agent/addons/<name>/` shipped in the wheel.
   Owned by the vexis-agent project.
2. **User** — `~/.vexis/addons/<name>/` (or `$VEXIS_HOME/addons/`).
   Your hand-written or `vexis-addons install`-ed add-ons.
3. **Project** — `./.vexis/addons/<name>/`. Opt-in via
   `VEXIS_ENABLE_PROJECT_ADDONS=1`; off by default because
   cwd-based discovery would surprise users running `vexis-agent`
   from random shells.

To override a bundled add-on with your fork: disable the bundled
name (`vexis-addons disable codemux`) and drop your fork under
`~/.vexis/addons/codemux/`.

**Default-on bundled add-ons.** A bundled add-on whose name is in
`DEFAULT_ENABLED_BUNDLED` (`core/addons/loader.py` — currently just
`browser`) loads even when `addons.enabled` doesn't list it. This is
the upgrade-safety carve-out: a capability extracted from core into a
bundled add-on (the browser, which used to be hardcoded) must not
vanish for an existing user whose config has no `addons.enabled`
entry. `addons.disabled` still wins — listing `browser` there turns it
off.

## Manifest schema (`addon.yaml`)

```yaml
name: codemux                          # required; [a-z0-9-]+
version: 1.0.0                         # required
description: "What this add-on does"
author: "Your Name"
kind: standalone                       # standalone | core-extension
                                       # (v1: standalone only)

# Things the add-on depends on. Checked by the loader BEFORE
# import; surfaced via ``vexis-addons doctor``.
requires:
  vexis_agent: ">=0.9.0"               # semver string
  python: ">=3.11"
  mcp_servers:
    - name: codemux
      optional: false                  # if true, addon loads
                                       # without the MCP but degrades
  env: ["MY_API_KEY"]                  # required env var names

# Informational — what the add-on PROMISES to register. Used by
# ``vexis-addons inspect`` and to detect conflicts (two add-ons
# both claiming the same telegram command, etc.).
provides:
  telegram_commands: ["codemux"]
  watcher_sources: ["codemux"]
  background_tasks: ["codemux-watcher-poller"]
  dispatch_handlers: ["watch_register", "watch_list"]
  skills: ["codemux.md"]
  header_blocks: ["codemux-active-work"]
  dashboard_pages: ["codemux"]
  mcp_server_defaults: ["codemux"]

# Optional. The user's ``addons.<name>.*`` slice from
# ~/.vexis/config.yaml is merged with these defaults before
# ``register(ctx)`` runs. Validated against ``type``;
# coercion isn't applied — add-ons that care should validate
# their own ``ctx.config.get(...)``.
config_schema:
  poll_interval_seconds:
    type: float                        # str|int|float|bool|list|dict
    default: 5.0
    description: "How often to poll"
```

## `register(ctx)` — the only entry point

Your add-on's `__init__.py` must define one function:

```python
# ~/.vexis/addons/hello/__init__.py
from vexis_agent.core.addons import PluginContext

def register(ctx: PluginContext) -> None:
    """Wire this addon into vexis-agent. Called once at daemon startup."""

    async def on_hello(update, context):
        await update.message.reply_text(f"Hello from {ctx.addon_name}!")

    ctx.register_telegram_command(
        "hello", on_hello,
        menu_description="Say hi",
    )
```

`register()` must be **synchronous** and side-effect-free beyond
the `ctx.register_*` calls. Anything long-running goes into
`register_background_task()`'s factory, which is invoked later
when the daemon's event loop is running.

## `PluginContext` API

The facade your `register()` sees. Add-ons should import nothing
from `vexis_agent.core` except `PluginContext` itself — touching
internals voids your add-on's forward-compatibility.

### Fields

```python
ctx.addon_name : str        # your slug from the manifest
ctx.addon_dir  : Path       # where your add-on lives on disk
ctx.user_id    : str        # always "default" today; multi-user seam
ctx.config     : AddonConfig  # your slice of config + manifest defaults
ctx.log        : Logger     # pre-namespaced "vexis_agent.addons.<name>"
```

### Registration hooks

| Method | What it wires |
|---|---|
| `register_telegram_command(name, handler, menu_description=None)` | A `/<name>` slash command on the Telegram bot. The handler runs through the standard auth gate — only the configured `telegram_allowed_user_id` reaches it. |
| `register_dispatch_handler(op_name, handler)` | One operation on the daemon's control socket. Used by sibling CLIs (`vexis-watch`, future add-on CLIs) to talk to the running daemon. |
| `register_background_task(name, factory)` | A long-lived coroutine factory. Called once after the event loop is running; runs until cancelled at daemon shutdown. Crashes are logged but never kill the daemon. |
| `register_watcher_source(source_type, source)` | A `Source` subclass for the watcher subsystem — lets your add-on make new agent types pollable via `vexis-watch`. |
| `register_system_prompt_block(name, provider)` | A one-line string injected into the brain's system prompt at every session start. Use for "active state" headers. |
| `register_capability_block(name, provider, *, order)` | A how-to block slotted INTO the system-prompt "Capabilities" section. Lands in the shared core capability registry at the same global `order` space as the built-in blocks (assembled by `assemble_capability_docs()`, both brains). Conflicts on `name` OR `order` raise `AddonConflictError` — pick an `order` clear of the small integers the core blocks use. Stable how-to, unlike `register_system_prompt_block`'s dynamic header. See [docs/capabilities.md](capabilities.md). |
| `register_mcp_server_default(spec)` | Declares an MCP server the brain should have. **Live** (was advisory): at daemon startup `core.addon_mcp.merge_addon_mcp_defaults` folds the spec into the active brain's native MCP config (claude-code `.mcp.json` / opencode `opencode.json`) via `brain.write_mcp_config`, so the next brain spawn gets the tool with no daemon restart. Precedence: a user entry in `$VEXIS_HOME/mcp-servers.yaml` wins on a name collision; an add-on default only fills a gap. Atomic + idempotent; no add-on defaults means nothing is written. See the MCP-defaults note below. |
| `register_skill(skill_file, target_subdir=".")` | Ships a SKILL.md (or any skill file) into each workspace's `skills/` directory at session start. The skill file must live inside `ctx.addon_dir`. |
| `register_dashboard_page(manifest)` | A tab on the web dashboard. Mirrors Hermes-style: `{label, icon, tab, entry, css, api}`. |

### Read-only accessors

```python
ctx.get_brain()         # Brain instance, for ``spawn_aux`` etc.
ctx.get_yaml_config()   # Read-only snapshot of ~/.vexis/config.yaml
```

## Lifecycle

```
daemon startup
    │
    ├─ discover_addons()        # walks roots, parses manifests
    │
    ├─ for each enabled add-on:
    │     ├─ build PluginContext
    │     └─ call register(ctx) # your code runs here
    │
    ├─ ...rest of startup...    # control socket, dashboard, brain,
    │                            # watcher, schedule manager, kanban
    │
    └─ start_all_background_tasks()  # YOUR factories fire now

daemon shutdown
    │
    ├─ stop_all_background_tasks()   # cancelled with 5s timeout
    │
    └─ ...rest of teardown...
```

## Conflict resolution

Every name-keyed registration is unique across all loaded add-ons.
Two add-ons trying to register `/codemux` raise
`AddonConflictError` from the second `ctx.register_telegram_command`
call. Skills and background tasks are list-additive — name
conflicts are fine, they all install / run.

## Failure handling

- Manifest parse errors → log + skip; other add-ons still load.
- Import errors in `__init__.py` → log with traceback + skip.
- Exceptions from `register(ctx)` → log + skip; the add-on
  appears in `vexis-addons list` with `status=loaded-error` so the
  dashboard / CLI can surface the problem.
- Background-task crashes → log; sibling tasks survive.

## Multi-user seam (`ctx.user_id`)

Today `ctx.user_id` is always `"default"`. Add-ons that maintain
per-instance state (e.g. an in-memory registry) MUST key it by
`ctx.user_id` from day one. When multi-user mode lands, the
runtime will mint one `PluginContext` per user — no add-on code
change required.

What multi-user mode WILL NOT change:
- The `register(ctx)` signature.
- The manifest format.
- The CLI surface.

What it WILL change:
- `ctx.user_id` will take values other than `"default"`.
- `~/.vexis/` paths will become per-user (`~/.vexis/users/<id>/`).
- Multiple contexts may exist for the same loaded add-on (one
  per user). Stateless add-ons need zero changes; stateful ones
  just key on `ctx.user_id`.

## CLI reference

```
vexis-addons list                       # all addons, with enable/disable status
vexis-addons enable <name>              # idempotent
vexis-addons disable <name>             # idempotent; wins over enable
vexis-addons inspect <name>             # parsed manifest + provides
vexis-addons doctor                     # check requires for enabled addons
vexis-addons install <local-path>       # rsync into ~/.vexis/addons/
vexis-addons --json <any-of-the-above>  # JSON output for scripts
```

The CLI does NOT touch the running daemon — every command is a
read or write against `~/.vexis/config.yaml` and the on-disk
add-on directories. Restart `vexis-agent` to pick up enable /
disable / install changes.

## MCP server defaults — shipping a tool the brain calls

`register_mcp_server_default(spec)` is how an add-on gives the brain a
new tool. `spec` is a `core.brain.base.McpServerSpec` — a local stdio
server (`command`/`args`) or a remote `url`. At startup
`core.addon_mcp.merge_addon_mcp_defaults` folds every registered
default into the active brain's native MCP config via
`brain.write_mcp_config`, so **both brains** are served (claude-code's
`.mcp.json`, opencode's `opencode.json`). The brain re-reads that file
each turn, so it picks up the server on its **next turn** with no
daemon restart.

Precedence: a same-named entry in the user's
`~/.vexis/mcp-servers.yaml` always wins — an add-on default only fills
a gap. That is exactly what makes a bundled default **swappable**: drop
a same-named server in `mcp-servers.yaml` pointing somewhere else and
the brain uses yours, no code change.

This is how the **browser** ships. The bundled `browser` add-on
registers a `vexis-browser` stdio MCP server (the `vexis-browser-mcp`
console script):

```python
from vexis_agent.core.brain.base import McpServerSpec

def register(ctx):
    ctx.register_mcp_server_default(
        McpServerSpec(name="vexis-browser", command="vexis-browser-mcp"),
    )
```

so the brain gets native `browser_*` MCP tools. Because the
brain↔browser boundary is plain MCP, swapping to a different browser
server (the official Playwright MCP, a cloud browser, a new engine) is
a config-level change in `mcp-servers.yaml` — no daemon edit, no
release. (The `vexis-browse` CLI remains as an equivalent back-compat
front-end; both drive the same persistent session.)

## Bundled add-ons

- **browser** — the stealth Camoufox browser, shipped to the brain as
  the `vexis-browser` MCP server (above). Default-on; owns the
  `browser_*` dispatch ops, the `web-browsing` capability block, and
  the dashboard's browser service. Config under `addons.browser.*`
  (legacy top-level `[browser]` still honoured).
- **codemux** — orchestration watcher for terminal-attached agents.
  Needs the codemux MCP wired in `mcp-servers.yaml`. Owns `/codemux`,
  the `watch_*` ops, the codemux watcher source, and the
  `codemux-orchestration` capability block.

## Limitations (v1)

- **No hot-reload.** Edits to an add-on require a daemon restart.
- **No git URL install.** `vexis-addons install` accepts local
  paths only; clone the repo yourself first.
- **No pip-entry-point discovery.** Add-ons must live in one of
  the three discovery roots; they don't install via pip yet.
- **No sandboxing.** Add-ons run in-process with full daemon
  privileges. Trust your add-ons.
- **Single-user.** The `user_id` seam exists but multi-user mode
  isn't implemented.

All of these are tracked for future revisions of the add-on system;
none of them require changes to existing add-on code.
