# Capabilities

Operational reference for tools you can invoke. SOUL.md tells you who
you are; this file tells you what you can do.

## Adding new abilities — use add-ons, not core edits

**If the user asks you to add a new feature, slash command, watcher
source, background task, dashboard tab, or skill, build it as an
add-on. Do NOT edit `vexis_agent/core/` or `vexis_agent/main.py`
or `vexis_agent/transports/telegram.py`.**

An add-on is one folder with two files:

    ~/.vexis/addons/<slug>/
    ├── addon.yaml           # manifest: name, version, requires, provides
    └── __init__.py          # def register(ctx): ...

Inside `register(ctx)`, the `PluginContext` exposes 8 hooks:

  - `ctx.register_telegram_command(name, handler, menu_description=...)`
  - `ctx.register_dispatch_handler(op_name, handler)`  — control-socket ops
  - `ctx.register_background_task(name, factory)`      — long-lived coro
  - `ctx.register_watcher_source(source_type, source)` — pollable agent
  - `ctx.register_system_prompt_block(name, provider)` — header line
  - `ctx.register_mcp_server_default(spec)`            — setup wizard hint
  - `ctx.register_skill(skill_file)`                   — auto-installed
  - `ctx.register_dashboard_page(manifest)`            — web UI tab

Cross-cutting access: `ctx.get_service("watcher")` to talk to the
watcher controller (other services attach over time).

After creating the folder: `vexis-addons enable <slug>` then restart
vexis-agent. The codemux integration (`/codemux` slash command,
watcher source, background poller, header block) is the canonical
example — see `vexis_agent/addons/codemux/` for a complete worked
add-on. Full API + lifecycle docs in `docs/addons.md`.

When in doubt: ask first whether the user wants a bundled add-on
(ships in the vexis-agent wheel; future users get it) or a user-
local one (`~/.vexis/addons/`; just for this machine). Both use the
same shape; only the location differs.
