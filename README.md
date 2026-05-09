# vexis-agent

Telegram-bridged agent for Linux desktops. Sends Telegram (or chat
UI) messages through an agent CLI (claude-code by default; opencode
optional) and pipes the brain's tool calls out to your machine —
screenshots, window control, voice notes, browser automation. Vexis
is the transport layer; the brain is whatever CLI you point it at.

Single-user by design. Hyprland/Wayland-targeted. Tailscale-friendly.

## Install

### One-liner (curl-bash)

Linux only (Arch, Debian/Ubuntu, Fedora, openSUSE supported out of
the box). Auto-runs the setup wizard at the end so you finish
configured.

```bash
curl -fsSL https://raw.githubusercontent.com/Zeus-Deus/vexis-agent/main/install.sh | bash
```

The installer:

- Refuses to run as root (single-user by design).
- Installs `pipx` if missing, via your distro's package manager.
- `pipx install --force git+https://github.com/Zeus-Deus/vexis-agent.git`.
- Surfaces missing soft dependencies (brain CLI, Hyprland/Wayland
  tools, Tailscale) with distro-specific install hints.
- Auto-runs `vexis-agent setup` unless `--skip-setup` is passed.

Flags (when piping into bash, pass them after `bash -s --`):

- `--dry-run` — print what would happen, don't install.
- `--skip-setup` — install only; don't launch the wizard.

Env knobs:

- `VEXIS_CHANNEL=stable|dev` — `main` (default) vs `develop` branch.
- `VEXIS_REPO=git+...` — override the source URL (forks, mirrors).
- `NO_COLOR=1` — disable ANSI escapes.

### Manual install

If you'd rather skip the auto-setup wizard:

```bash
pipx install git+https://github.com/Zeus-Deus/vexis-agent.git
vexis-agent setup
```

`vexis-agent setup` walks six sections: writes `~/.vexis/config.yaml`
and `~/.vexis/.env` (mode 0600) from shipped templates, prompts for
your Telegram bot token + numeric user ID, verifies the configured
brain CLI is on PATH, sets up `~/vexis-workspace/` (with the
`AGENTS.md → CLAUDE.md` symlink for opencode users), wires any MCP
servers it finds (e.g. `omarchy-kb`), checks Tailscale, and offers
to install the systemd user unit.

### Brain prerequisites

Pick one (you can switch later via `~/.vexis/config.yaml`):

- **claude-code** (default) — <https://docs.anthropic.com/claude/claude-code>
- **opencode** — 30+ providers including Anthropic OAuth,
  ChatGPT Plus, GitHub Copilot, plus API keys:
  ```bash
  curl -fsSL https://opencode.ai/install | bash
  ```

Authenticate:

- claude-code: `claude /login` (Pro/Max subscription) or set
  `ANTHROPIC_API_KEY` in your shell.
- opencode: `opencode providers login`.

Tip: run `vexis-agent doctor` after install for a 10-check readiness
pass that surfaces every prerequisite (Python, config, secrets,
brain CLI, workspace, dispatch wrappers, Tailscale, systemctl,
linger, service unit).

### Operating system + tooling

Vexis is **Hyprland/Wayland-targeted**. The daemon hard-requires
these binaries on PATH at startup; install them via your distro:

| Tool | Arch | Debian/Ubuntu | Fedora |
|------|------|---------------|--------|
| `hyprctl` | ships with Hyprland | ships with Hyprland | ships with Hyprland |
| `wtype` | `pacman -S wtype` | `apt install wtype` | `dnf install wtype` |
| `ydotool` | `pacman -S ydotool` | `apt install ydotool` | `dnf install ydotool` |
| `grim` | `pacman -S grim` | `apt install grim` | `dnf install grim` |
| `ffmpeg` | `pacman -S ffmpeg` | `apt install ffmpeg` | `dnf install ffmpeg` |
| `jq` | `pacman -S jq` | `apt install jq` | `dnf install jq` |
| `voxtype` | (separate install) | (separate install) | (separate install) |

`tailscale` is optional but strongly recommended — without it, the
dashboard is localhost-only and the live-stream tools have no
remote tunnel.

## Operating

```bash
vexis-agent run                       # foreground daemon
vexis-agent service install           # write the systemd user unit
systemctl --user enable --now vexis-agent.service
vexis-agent service status            # systemctl --user status …
vexis-agent service logs --follow     # journalctl -u … -f
vexis-agent service restart           # after edits to ~/.vexis/config.yaml
vexis-agent doctor                    # 10-check readiness pass

vexis-agent backup --out backup.zip   # pack ~/.vexis + ~/vexis-workspace
vexis-agent backup-restore backup.zip # restore on the destination
vexis-agent update                    # pipx-aware self-upgrade
```

`vexis-agent update` is bullet-proof against bad-luck disconnects:
ignores SIGHUP for the duration, mirrors output to
`~/.vexis/logs/update.log`, and writes a pre-update zip of `~/.vexis/`
to `~/.vexis/backups/pre-update-<utc>.zip` before any install work
runs. It never touches `~/.vexis/` or `~/vexis-workspace/`
(decision D7: code dir ≠ data dir) and never auto-restarts the
service — prints a hint instead.

If an update breaks something, restore the pre-update snapshot:

```bash
vexis-agent backup-restore ~/.vexis/backups/pre-update-<utc>.zip --overwrite
```

## Migrating to a new machine

Vexis ships first-class backup/restore for personal state — no
manual rsync required.

```bash
# On the source machine:
vexis-agent backup --out vexis-backup.zip
scp vexis-backup.zip you@new-server:

# On the new machine:
curl -fsSL https://…/install.sh | bash    # auto-runs setup wizard
vexis-agent backup-restore vexis-backup.zip
vexis-agent service install
systemctl --user enable --now vexis-agent.service
```

The backup zip contains everything personal: `~/.vexis/config.yaml`,
`~/.vexis/.env`, curator state, learning state, goals, dashboard
token, plus the gittable workspace markdown (CLAUDE.md, SOUL.md,
MEMORY.md, USER.md, RELATIONSHIPS.md, memories/, skills/). It
**excludes** regenerable junk: bytecode caches, browser profiles
(too large; cached chromium), node_modules, `.git` history, runtime
PID files, and SQLite WAL sidecars (which can produce torn restores).

Secrets (`.env`, dashboard token) restore at mode 0600.

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

Or with conda (the maintainer's workflow):

```bash
conda create -n vexis-agent_env python=3.11
conda activate vexis-agent_env
pip install -e '.[dev]'
./scripts/dev-setup.sh   # AGENTS.md repo symlink + git pre-commit hook
```

Run the test suite:

```bash
pytest
```

`scripts/dev-setup.sh` is the dev-side counterpart to `vexis-agent
setup` — it wires the repo's `AGENTS.md → CLAUDE.md` symlink (so
opencode finds the same instruction file claude-code reads from
`CLAUDE.md`) and installs the dashboard-rebuild git pre-commit hook.
Idempotent.

## Project structure

- `vexis_agent/` — installable package; the wheel pipx ships.
  - `cli.py` — Typer entry, source of `vexis-agent` console script.
  - `main.py` — daemon entry.
  - `core/` — main loop, brain ABC + adapters, learning curator,
    goals, schedules, sessions, config.
  - `transports/` — messaging adapters (default `telegram.py`).
  - `tools/` — desktop-control, voxtype, livestream, browser, …
  - `daemon/` — systemd unit rendering, update mechanics, backup,
    doctor.
  - `data/` — shipped runtime resources (CAPABILITIES.md, setup
    templates, workspace CLAUDE.md template).
- `web/` — dashboard frontend (Vite/React; built with `npm run build`).
- `scripts/` — dev helpers (`dev-setup.sh`, `dev_setup.py`,
  `eval_learning.py`, `bench_curator_tick.py`, …) plus the bash
  dispatch wrappers (`vexis-bg`, `vexis-stream`, …) which double as
  console scripts when the wheel is installed.
- `tests/` — pytest suite, parametrised over all three brain
  implementations for the cross-brain contract. ~2000 tests.
- `docs/` — user-facing reference.

## Status

Packaging effort landed on the `packaging-effort` branch. Everything
above (curl-bash install with auto-setup, hermes-style wizard,
systemd lifecycle, doctor with 10 checks, backup/restore, robust
update with snapshots + log mirror, MCP auto-detection) is
implemented and dogfooded on the maintainer's dev box. The repo is
single-user by design; no plans for PyPI / AUR / Docker until the
curl-bash flow is battle-tested in production.
