---
name: self-extension
description: How to give yourself a capability you don't have yet — the decision tree for skill vs MCP server vs in-process add-on, the hot-vs-restart cost of each, and the guardrails that keep a self-extension from breaking the daemon. Load this when the user asks you to add a tool, command, procedure, or feature to yourself, or when you catch yourself wishing you had a capability mid-task and need to know the cheapest safe way to get it.
---

# Extending yourself

You are a transport in front of an agent CLI, wired to MCP tools, skills,
and in-process add-ons. When you need a capability you don't have, the
question is never "can I add it?" but "which seam, and what does it cost
to make it live?" Pick the cheapest seam that fits. In order of
preference: **skill → MCP server → in-process add-on.**

## Decision tree

Start from what kind of thing you actually need:

| You need… | Add… | Goes live… |
|---|---|---|
| A new callable TOOL (browser, scraper, API client, anything you invoke) | an MCP server (point at one, or wire a new one into `~/.vexis/mcp-servers.yaml`) | **next turn** — no restart |
| A repeatable PROCEDURE / how-to you'll want again | a SKILL (a `SKILL.md` under `<workspace>/skills/`) | **next session** — no restart |
| A Telegram command, a dashboard tab, a watcher source, or daemon-resident state | an in-process ADD-ON (`vexis_agent/addons/<name>/` or `~/.vexis/addons/<slug>/`) | **after a RESTART** — ask the user or use `/restart` |

If two seams could work, take the cheaper one. A skill that documents how
to drive an existing MCP tool beats writing a new add-on. A new MCP tool
beats a new add-on if you don't actually need a command/tab/watcher.

## Hot-vs-restart matrix

Know when your change takes effect before you make it — promising the user
something "now" that needs a restart is the common failure here.

- **MCP server** (new or changed tool) → **next turn.** The tool list is
  rebuilt per turn; a freshly-wired MCP shows up on your very next call.
- **Skill** (markdown procedure) → **next session.** Skills are read into
  the prompt at session start, not mid-conversation.
- **In-process add-on** (command / tab / watcher source / daemon state) →
  **restart.** Add-ons load once at daemon startup. There is no hot-reload;
  the user must restart the daemon (`/restart`) for a new or changed add-on
  to take effect.
- **System-prompt / SOUL.md / MEMORY.md edits** → **next session, never
  mid-turn.** The per-session system prompt is cached per session UUID;
  editing it changes nothing until a fresh session spawns. Don't expect a
  mid-conversation edit to change how you behave this turn.

## How to add each

### A skill

Write a `SKILL.md` with `name:` + `description:` frontmatter under
`<workspace>/skills/<skill-name>/`. The `description` is what future-you
matches against to decide whether to load it — make it a precise "load
this when…" trigger, not a title. Keep the body prescriptive: the steps,
the pitfalls, the do-NOT list. It's live next session.

### An MCP server

Add the server to `~/.vexis/mcp-servers.yaml` (or ask the user to). Vexis
serialises it into both brains' MCP config (`.mcp.json` for claude-code,
merged into `opencode.json` for opencode) — you don't hand-edit those. Its
tools are callable next turn.

### An in-process add-on

A folder with `addon.yaml` (manifest) + `__init__.py` defining
`register(ctx)`. Inside `register`, use the `PluginContext` hooks —
`register_telegram_command`, `register_dispatch_handler`,
`register_watcher_source`, `register_system_prompt_block`,
`register_capability_block`, `register_skill`, `register_dashboard_page`,
`register_background_task`. **Never patch core to integrate an add-on** —
core stays add-on-agnostic; the add-on owns its wiring through `ctx`.
Enable with `vexis-addons enable <slug>`, then restart. The codemux add-on
(`vexis_agent/addons/codemux/`) is the canonical worked example.

## Guardrails

- **Never touch the recursion-guard prefixes or the curator
  content-prefix filter.** Those prefixes (`CURATOR_REVIEW_PROMPT_PREFIX`,
  `GOAL_JUDGE_PROMPT_PREFIX`, `KANBAN_WORKER_PREFIX`, …) are what stop aux
  subsystems from reviewing each other's transcripts and looping forever.
  Changing them silently breaks the learning curator, goals, and kanban.
- **Respect aux tool allowlists.** Every aux spawn declares the narrow set
  of tools it needs. Don't try to widen an aux's surface from inside a
  transcript — a poisoned transcript must not be able to argue an aux into
  a tool it wasn't given.
- **Verify before you swap.** When replacing a working tool with a new one,
  add the new one ALONGSIDE, test it against reality, then cut over. Never
  delete the thing that works in the same step you introduce the
  replacement.
- **Prefer the cheapest seam that fits:** skill > MCP > in-process add-on.
  A restart is the most disruptive change you can make — earn it.
- **Don't break the per-session prompt cache.** Register add-on capability
  blocks and prompt blocks at load time (before any session spawns), so the
  cached per-session system prompt stays stable. Don't mutate the prompt
  mid-conversation.
