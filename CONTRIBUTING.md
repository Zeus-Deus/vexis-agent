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
- In-process plugin loader. vexis treats **MCP servers as the
  extension mechanism** — see the next section.

## Adding tools (MCP servers)

`~/.vexis/mcp-servers.yaml` is the **universal MCP config** — one
source of truth, regardless of which brain (claude-code / opencode)
you're running. Declare each server once:

```yaml
servers:
  - name: my-tool
    command: my-tool
    args: ["--mcp"]
    env:
      MY_TOOL_API_KEY: "..."
```

Re-run `vexis-agent setup` (or restart the daemon). The wizard
detects each server whose binary is on PATH and writes BOTH
per-brain native files: `<workspace>/.mcp.json` (claude-code's
``mcpServers`` shape) and `<workspace>/opencode.json` (opencode's
``mcp`` block with the `vexis-` namespace prefix). Switching
brains later (edit ``brain.kind``, restart) is zero-friction —
the new brain's native config is already there.

Full schema + commented examples: `vexis_agent/data/mcp-servers.example.yaml`.

## Adding skills

Skills are markdown procedure documents the brain reads on every
session. Two ways to add one:

```bash
# via the helper — validates frontmatter, sets up the directory
vexis-skill create my-skill --content-file ~/my-skill.md

# or directly — the brain auto-discovers everything under
# ~/vexis-workspace/skills/<name>/SKILL.md on next session
mkdir -p ~/vexis-workspace/skills/my-skill
$EDITOR ~/vexis-workspace/skills/my-skill/SKILL.md
```

Other commands: `vexis-skill list / view / edit / patch / archive
/ restore / write-file / remove-file`. The curator can also promote
skills from your past sessions automatically — see CLAUDE.md's
"Learning curator" section.

## Extending at the source level

For deeper extensions, the layered architecture supports it:

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
