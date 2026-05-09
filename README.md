# vexis-agent

Standalone Python daemon that bridges Telegram to an agent CLI,
letting you control an Omarchy (Hyprland/Wayland) desktop from your
phone. It is a transport layer in front of an agent CLI (claude-code
by default; opencode optional), not a new agent — Telegram in, MCP
tools out, agent CLI in the middle.

## Install

1. **Install a brain.** Pick one (you can switch later):

   - **claude-code** (recommended default) —
     <https://docs.anthropic.com/claude/claude-code>
   - **opencode** (alternative; supports 30+ providers including
     Anthropic OAuth, ChatGPT Plus, GitHub Copilot, plus API keys) —
     ```bash
     curl -fsSL https://opencode.ai/install | bash
     ```

   Authenticate the brain you installed:
   - claude-code: `claude /login` (Pro/Max subscription) or set
     `ANTHROPIC_API_KEY` in your env.
   - opencode: `opencode providers login` (interactive provider
     picker; the legacy `opencode auth login` alias still works).

2. **Clone vexis**:
   ```bash
   git clone https://github.com/Zeus-Deus/vexis-agent.git
   cd vexis-agent
   ```

3. **Set up a Python venv**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```
   (All dependencies live inside this env. Never install to global
   Python.)

4. **Configure secrets**:
   ```bash
   cp .env.example .env
   # then edit .env and fill in TELEGRAM_BOT_TOKEN,
   # TELEGRAM_ALLOWED_USER_ID, and ANTHROPIC_API_KEY (or other
   # provider keys if you're on opencode).
   ```

5. **Run the install script**:
   ```bash
   ./scripts/install.sh
   ```
   This:
   - Symlinks `AGENTS.md → CLAUDE.md` so both brains see the same
     project instructions.
   - Verifies your chosen brain is on PATH (prints an install hint
     if not).
   - Writes the brain's MCP config: `<workspace>/.mcp.json` for
     claude-code, `<workspace>/opencode.json` (with the `vexis-`
     namespace prefix preserving any non-prefixed entries you've
     added by hand) for opencode.

   Idempotent — safe to re-run after switching brains or updating
   vexis. Pass `--dry-run` to preview without touching the
   filesystem.

## Choosing your brain

The default is `claude-code`. To use opencode instead, set in
`~/.vexis/config.yaml`:

```yaml
brain:
  kind: opencode
```

Restart vexis after editing. See [`docs/brains.md`](docs/brains.md)
for the per-brain reference (auth modes, session storage, MCP
config, tool naming) and [`docs/migration.md`](docs/migration.md)
for the full opt-in / opt-out flow.

Before declaring opencode ready for daily use on a fresh install,
walk through the [dogfood checklist](docs/dogfood-checklist.md) —
12 manual flows covering cold-boot, multi-turn, tools, MCP,
`/cancel`, `/goal`, `/schedule`, daemon restart, bad auth, bad
install, plus two non-negotiable race-condition checks.

## Running

The daemon expects to run under conda. From the repo root:

```bash
conda activate vexis-agent_env
python main.py
```

For systemd / autostart setups, see the dispatch scripts in
`scripts/` (`vexis-bg`, `vexis-stream`, `vexis-dashboard`, etc.).

## Project structure

- `core/` — main loop, brain abstraction, learning curator, goals,
  schedules, sessions, config.
- `core/brain/` — `Brain` ABC + concrete implementations
  (`claude_code.py`, `opencode.py`, `null.py` for tests).
- `transports/` — messaging adapters. Default: `telegram.py`.
- `tools/` — MCP servers (desktop-control, voxtype, livestream,
  etc.).
- `scripts/` — dispatch helpers + `install.sh` / `install.py`.
- `tests/` — pytest suite, parametrised over all three brain
  implementations for the cross-brain contract.
- `docs/` — user-facing reference (this README, brain-by-brain
  guide, migration, dogfood checklist).

## Status

Phase C of the brain abstraction landing — opencode is opt-in and
functionally complete pending the Day 8 dogfood-gated flag posture
decision. claude-code remains the default and is unchanged from
pre-Phase-C behaviour.
