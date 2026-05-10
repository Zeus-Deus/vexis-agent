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
- Bespoke Python plugin loader (hermes-style). vexis treats
  **MCP servers as the canonical extension mechanism** — see the
  next section.

## Extending vexis: the MCP-server-as-plugin model

Want vexis to do something it doesn't do today? Don't fork
`vexis_agent/`. Build (or install) an **MCP server** instead.

Concretely:

1. Pick a tool that does the thing you want — `peekaboo` for macOS
   desktop control, `playwright-mcp` for browser automation,
   `omarchy-kb` for Omarchy/Hyprland system docs, your own
   purpose-built MCP server, …
2. Install the server's binary the way its docs say (brew / npm /
   cargo / pip / your distro's package manager). Anything that
   ends up on PATH or as a runnable command works.
3. Declare it in `~/.vexis/mcp-servers.yaml` (or
   `$VEXIS_HOME/mcp-servers.yaml`):

   ```yaml
   servers:
     - name: peekaboo
       binary: npx                       # presence check
       command: npx
       args: ["-y", "@steipete/peekaboo"]
       env:
         PEEKABOO_AI_PROVIDERS: anthropic/claude-opus-4
   ```

4. Re-run `vexis-agent setup` (or restart the daemon). The wizard
   detects the server, writes the matching entry into the
   workspace MCP config (`<workspace>/.mcp.json` for claude-code,
   `<workspace>/opencode.json` with the `vexis-` prefix for
   opencode), and the brain auto-discovers the new tools on next
   spawn.

A copy-pasteable starter lives at `vexis_agent/data/
mcp-servers.example.yaml`. The design rationale is in
`.plans/plugin-architecture-research.md`: vexis is single-user, the
MCP protocol is already the lingua franca, and bolting a hermes-style
in-process Python plugin loader on top would be 3000 lines for
problems vexis doesn't have.

If you DO want to extend vexis at the source level — new transport,
new brain adapter, new built-in tool — the layered architecture
already supports that:

- `vexis_agent/transports/` — drop a new module, register in
  `transports/__init__.py`. Telegram is the reference.
- `vexis_agent/core/brain/` — implement the `Brain` ABC.
- `vexis_agent/tools/` — add a `*_cli.py` with a `main()` entry,
  wire it as a console script in `pyproject.toml`'s `[project.scripts]`.

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
