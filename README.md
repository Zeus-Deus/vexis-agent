# vexis-agent

Standalone Python daemon that bridges Telegram to an agent CLI,
letting you control an Omarchy (Hyprland/Wayland) desktop from your
phone. It is a transport layer in front of an agent CLI (claude-code
by default; opencode optional) — Telegram in, MCP tools out, agent
CLI in the middle.

## Install

### One-liner (curl-bash)

Linux only (Arch, Debian/Ubuntu, Fedora, openSUSE supported out of
the box).

```bash
curl -fsSL https://raw.githubusercontent.com/Zeus-Deus/vexis-agent/main/install.sh | bash
vexis-agent setup
systemctl --user enable --now vexis-agent.service
```

The installer:

- Refuses to run as root (vexis-agent is single-user by design).
- Installs `pipx` if missing, using your distro's package manager.
- Runs `pipx install --force git+https://github.com/Zeus-Deus/vexis-agent.git`.
- Prints the next-step hints; does NOT auto-run `vexis-agent setup`.

Pass `--dry-run` (when piped: `bash -s -- --dry-run`) to preview
without changes. Set `VEXIS_CHANNEL=dev` to install from the
`develop` branch instead of `main`.

### Manual install

If you'd rather inspect the source first, or don't trust the
curl-bash flow:

```bash
pipx install git+https://github.com/Zeus-Deus/vexis-agent.git
vexis-agent setup
```

`vexis-agent setup` is interactive: it writes `~/.vexis/config.yaml`
(from a shipped template) and `~/.vexis/.env` (mode 0600), prompts
for your Telegram bot token and numeric user ID, and offers to
install the systemd user unit.

### Brain prerequisites

Pick one (you can switch later via `~/.vexis/config.yaml`):

- **claude-code** (default) — <https://docs.anthropic.com/claude/claude-code>
- **opencode** (alternative; 30+ providers including Anthropic OAuth,
  ChatGPT Plus, GitHub Copilot, plus API keys) —
  ```bash
  curl -fsSL https://opencode.ai/install | bash
  ```

Authenticate:

- claude-code: `claude /login` (Pro/Max subscription) or set
  `ANTHROPIC_API_KEY` in your shell.
- opencode: `opencode providers login`.

Run `vexis-agent doctor` to confirm the brain CLI is on PATH and
all secrets are reachable.

## Operating

```bash
vexis-agent run                       # foreground daemon
vexis-agent service install           # write the systemd user unit
systemctl --user enable --now vexis-agent.service
vexis-agent service status            # systemctl --user status …
vexis-agent service logs --follow     # journalctl -u … -f
vexis-agent service restart           # after edits to ~/.vexis/config.yaml
vexis-agent update                    # pipx upgrade (or git pull for editable installs)
vexis-agent doctor                    # diagnose install + config
```

`update` never touches `~/.vexis/` or `~/vexis-workspace/` — your
state is preserved across upgrades. After a successful update it
prints a hint pointing at `vexis-agent service restart`; it never
restarts the daemon for you.

## Migrating a running deployment

To move vexis-agent from your dev box to a home server (or any
fresh machine):

1. Run the curl-bash installer on the destination.
2. `vexis-agent setup` — interactive prompts.
3. `scp` your gittable agent state from the source machine:
   `SOUL.md`, `MEMORY.md`, `USER.md`, `RELATIONSHIPS.md`, and
   `skills/` go into `~/vexis-workspace/`.
4. `vexis-agent service install`, then enable + start.
5. From then on, `vexis-agent update` whenever the dev machine
   pushes new features.

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

## Development setup

For contributors (and the maintainer's own machine). Conda is one
option; venv / uv / poetry all work — pick whatever you like.

```bash
git clone https://github.com/Zeus-Deus/vexis-agent.git
cd vexis-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Or with conda (if that's already your workflow):

```bash
conda create -n vexis-agent_env python=3.11
conda activate vexis-agent_env
pip install -e '.[dev]'
```

Run the test suite:

```bash
pytest
```

The legacy entry-point `python main.py` no longer exists post-Phase-2;
use `vexis-agent run` (or `python -m vexis_agent.cli run`) instead.
The dispatch wrappers in `scripts/` (`vexis-bg`, `vexis-stream`, …)
still work for ad-hoc tooling.

## Project structure

- `vexis_agent/` — installable package; the wheel that pipx ships.
  - `cli.py` — Typer entry, source of `vexis-agent` console script.
  - `main.py` — daemon entry.
  - `core/` — main loop, brain ABC + adapters, learning curator,
    goals, schedules, sessions, config.
  - `transports/` — messaging adapters. Default: `telegram.py`.
  - `tools/` — MCP servers (desktop-control, voxtype, livestream, …).
  - `daemon/` — systemd unit rendering, update detection, doctor.
  - `data/` — shipped templates the setup wizard installs.
- `web/` — dashboard frontend (Vite/React; built with `npm run build`).
- `scripts/` — dev helpers + dispatch wrappers (`vexis-bg`,
  `vexis-dispatch`, …).
- `tests/` — pytest suite, parametrised over all three brain
  implementations for the cross-brain contract.
- `docs/` — user-facing reference.

## Status

Packaging effort landed on the `packaging-effort` branch. Everything
above (curl-bash install, Typer CLI, systemd lifecycle, doctor,
update) is implemented and dogfooded on the maintainer's dev box.
The repo is single-user by design; no plans for PyPI / AUR / Docker
until the curl-bash flow is battle-tested in production.
