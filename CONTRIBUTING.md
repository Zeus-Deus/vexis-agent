# Contributing to vexis-agent

Thanks for poking at this. vexis-agent is a single-user project, so
the contribution surface is small. Here's what's helpful and what
to expect.

## What's in scope

- **Bug reports**, especially for the install / setup / update flow
  on a fresh box.
- **Soft-dep portability** — the daemon hard-requires Hyprland +
  some Wayland tools. Patches that demote those to per-feature
  checks (so vexis runs on niri, sway, X11, or container hosts) are
  welcome.
- **New brain adapters** under `vexis_agent/core/brain/`. Match the
  contract in `core/brain/base.py`; cross-brain tests in
  `tests/test_brain_contract.py` are parametrised over every
  registered brain.
- **MCP server entries**. Drop a new detector into
  `vexis_agent.setup_wizard._MCP_DETECTORS` and the wizard will
  pick it up.

## What's not in scope

- Multi-user / multi-tenant features. `vexis-agent` is single-user
  by design (CLAUDE.md invariant).
- Removing the Telegram dependency. The transport adapter is
  pluggable (`vexis_agent/transports/`), but Telegram is the
  reference; new transports land alongside, not in place of.
- PyPI / AUR / Homebrew / Nix packaging. Distribution is curl-bash
  → pipx; alternatives are deferred until that flow is
  battle-tested.

## Setup

```bash
git clone https://github.com/Zeus-Deus/vexis-agent.git
cd vexis-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
./scripts/dev-setup.sh   # AGENTS.md repo symlink + dashboard pre-commit hook
pytest                   # ~2000 tests, ~40s
```

If you're on conda (the maintainer's setup):

```bash
conda create -n vexis-agent_env python=3.11
conda activate vexis-agent_env
pip install -e '.[dev]'
./scripts/dev-setup.sh
pytest
```

## Pull-request checklist

- [ ] `pytest` passes locally.
- [ ] `vexis-agent doctor` still runs clean on your dev box.
- [ ] If you added a CLI command or changed a path resolver, the
      existing wizard / install.sh / doctor flows still pass their
      tests.
- [ ] Commit messages: imperative present tense. No AI-attribution
      trailers (`Co-Authored-By:`, etc.). No first-person
      ("I added", "Claude added"). No session-context references
      ("audited during ...").
- [ ] If the change touches `CLAUDE.md`, the file stays under the
      220-line tripwire (`tests/test_claude_md_invariants.py`).

## Branching

- Work on `feature/<short-name>` branches off `main`.
- Maintainer keeps `main` always-working — no `develop` branch.
- Releases happen via the `vexis-agent-release` claude-code skill;
  it tags `vN.N.N` annotated tags. Curl-bash users land on the
  latest tag, so until your change is in a tag, end-users won't see
  it.

## Where to ask

GitHub issues for bugs and feature requests. The maintainer is
single-user so response time is best-effort.
