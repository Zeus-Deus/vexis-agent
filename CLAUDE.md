# Vexis-Agent

Standalone Python daemon. Telegram bot + `claude -p` bridge for controlling an Omarchy (Hyprland/Wayland) desktop from a phone.

This is a transport layer in front of Claude Code, not a new agent. Telegram in, MCP tools out, Claude Code in the middle.

## Repo layout
- `brains/` — AI provider adapters. Default: `claude_code.py`.
- `transports/` — messaging adapters. Default: `telegram.py`.
- `tools/` — MCP servers (desktop-control, tailnet-serve, voxtype, omarchy-kb).
- `core/` — main loop, auth, config.

## Local dev environment
- Miniconda env: `vexis-agent_env`. Activate before any `pip install` or running code.
- Never install to global Python.
- Python 3.11+, async-first, type hints required.

## Secrets
- All sensitive values live in `.env`. Never commit secrets, user IDs, tokens, or personal paths.
- Read user identifiers and tokens from env or `~/.config/vexis-agent/config.toml`. Hardcode nothing user-specific in source.

## Conventions
- Single-user by design. No multi-tenancy.
- Audit before changing. Read the relevant module fully before editing.

## Reference repos (clone to /tmp when needed)
- `NousResearch/hermes-agent` — peek at gateway, skills, memory patterns. Never bulk-copy.

## Build order
1. Telegram ↔ `claude -p` bridge.
2. User-ID auth check.
3. Voice (voxtype whisper model).
4. tailnet-serve + omarchy-kb tools.
5. Screenshot tool (read-only).
6. Input tool + safety scaffolding.
