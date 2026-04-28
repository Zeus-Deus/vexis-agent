# vexis-agent

Standalone Python daemon that bridges Telegram to `claude -p`, letting you control an Omarchy (Hyprland/Wayland) desktop from your phone. It is a transport layer in front of Claude Code, not a new agent — Telegram in, MCP tools out, Claude Code in the middle.

## Setup

1. Copy `.env.example` to `.env` and fill in real values for `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, and `ANTHROPIC_API_KEY`.
2. Activate the Miniconda env: `conda activate vexis-agent_env`. All dependencies should be installed inside that env, never globally.

## Status

Early development.
