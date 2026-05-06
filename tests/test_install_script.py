"""Tests for ``scripts/install.py``.

Covers the planning + apply paths against tmp workspaces. The
shell wrapper ``scripts/install.sh`` is a thin conda-activate
delegator — its behaviour is implicit in the Python tests.

What's verified

- **Plan describe**: dry-run output covers brain.kind, binary
  presence, workspace, symlink classifications, and the canonical
  MCP server list parsed from ``.mcp.json``.
- **Symlink creation**: a fresh workspace gets ``AGENTS.md``
  symlinked to ``CLAUDE.md`` in both repo and workspace.
- **Idempotence**: re-running on a workspace that already has the
  symlinks produces no churn (apply() is a no-op for the symlink
  half + a byte-identical MCP config write).
- **Refuse to overwrite a real AGENTS.md**: the install script
  must NOT clobber a hand-maintained AGENTS.md file.
- **MCP config shape per brain**: claude-code writes
  ``.mcp.json``; opencode writes ``opencode.json`` with the
  ``vexis-`` namespace prefix.
- **Brain not on PATH** is a fatal planning error (exit 1).

Design citation: ``.plans/brain-abstraction-research.md`` §5 Day 6.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Importing the script's machinery directly (it adds itself to
# sys.path) lets us exercise the planning logic without a
# subprocess shell hop.
from scripts.install import (
    SymlinkAction,
    brain_binary_for_kind,
    brain_install_hint,
    build_plan,
    main,
    read_canonical_mcp_servers,
)


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_yaml_config(monkeypatch, tmp_path):
    """Tier resolution + brain.kind read from
    ``~/.vexis/config.yaml`` — keep tests insulated from the
    user's real config so ``resolve_brain_kind()`` returns the
    documented default."""
    from core import yaml_config
    cfg_dir = tmp_path / "vexis-config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        yaml_config, "_config_path", lambda: cfg_dir / "config.yaml"
    )


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A tmp directory that mimics the vexis-agent repo layout —
    just enough to drive the install script: ``CLAUDE.md`` +
    ``.mcp.json``."""
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(
        "# Vexis-Agent\n\nfake repo claude file\n", encoding="utf-8",
    )
    (repo / ".mcp.json").write_text(
        json.dumps({
            "mcpServers": {
                "codemux": {
                    "command": "/usr/bin/codemux",
                    "args": ["mcp"],
                    "env": {"CODEMUX_WORKSPACE_ID": "test"},
                }
            }
        }),
        encoding="utf-8",
    )
    return repo


@pytest.fixture
def fake_workspace(tmp_path: Path) -> Path:
    return tmp_path / "ws-install"


# ──────────────────────────────────────────────────────────────────
# read_canonical_mcp_servers
# ──────────────────────────────────────────────────────────────────


def test_read_canonical_mcp_servers_parses_repo_mcp_json(fake_repo: Path):
    servers = read_canonical_mcp_servers(fake_repo)
    assert len(servers) == 1
    assert servers[0].name == "codemux"
    assert servers[0].command == "/usr/bin/codemux"
    assert servers[0].args == ["mcp"]
    assert servers[0].env == {"CODEMUX_WORKSPACE_ID": "test"}


def test_read_canonical_mcp_servers_missing_file_returns_empty(tmp_path: Path):
    """A repo without ``.mcp.json`` yields the empty list — the
    install script then writes an empty ``mcpServers: {}`` config
    rather than crashing."""
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    assert read_canonical_mcp_servers(repo) == []


def test_read_canonical_mcp_servers_corrupt_json_returns_empty(tmp_path: Path):
    """Corrupt ``.mcp.json`` is logged-and-treated as empty.
    Better to land an empty config the user can fix than to
    refuse the whole install."""
    repo = tmp_path / "corrupt-repo"
    repo.mkdir()
    (repo / ".mcp.json").write_text("not valid json", encoding="utf-8")
    assert read_canonical_mcp_servers(repo) == []


# ──────────────────────────────────────────────────────────────────
# brain helpers
# ──────────────────────────────────────────────────────────────────


def test_brain_binary_for_kind():
    assert brain_binary_for_kind("claude-code") == "claude"
    assert brain_binary_for_kind("opencode") == "opencode"
    assert brain_binary_for_kind("null") is None
    assert brain_binary_for_kind("future-brain") is None


def test_brain_install_hint_known_kinds():
    assert "Claude Code" in brain_install_hint("claude-code")
    assert "opencode.ai" in brain_install_hint("opencode")
    # Unknown kind: hint is still informative, not a crash.
    assert "future-brain" in brain_install_hint("future-brain")


# ──────────────────────────────────────────────────────────────────
# SymlinkAction
# ──────────────────────────────────────────────────────────────────


def test_symlink_action_create(tmp_path: Path):
    target = tmp_path / "target.md"
    target.write_text("hi")
    link = tmp_path / "link.md"
    action = SymlinkAction(link, target)
    assert action.state == "create"
    action.apply()
    assert link.is_symlink()
    assert link.resolve() == target.resolve()


def test_symlink_action_already_correct(tmp_path: Path):
    target = tmp_path / "target.md"
    target.write_text("hi")
    link = tmp_path / "link.md"
    link.symlink_to("target.md")
    action = SymlinkAction(link, target)
    assert action.state == "already_correct"
    action.apply()  # no-op
    assert link.is_symlink()


def test_symlink_action_refuses_real_file(tmp_path: Path):
    """A hand-maintained AGENTS.md is sacred. Install must NOT
    overwrite it."""
    target = tmp_path / "target.md"
    target.write_text("repo content")
    link = tmp_path / "AGENTS.md"
    link.write_text("user-maintained AGENTS.md")
    action = SymlinkAction(link, target)
    assert action.state == "refuse_real_file"
    action.apply()  # no-op
    assert link.is_file() and not link.is_symlink()
    assert link.read_text() == "user-maintained AGENTS.md"


def test_symlink_action_replaces_wrong_symlink(tmp_path: Path):
    """If AGENTS.md is a symlink to the wrong file (e.g. user
    pointed it at a different doc), the install replaces it. We
    only own the symlink case — a stale one is ours to fix."""
    target = tmp_path / "target.md"
    target.write_text("right target")
    other = tmp_path / "other.md"
    other.write_text("wrong target")
    link = tmp_path / "link.md"
    link.symlink_to("other.md")
    action = SymlinkAction(link, target)
    assert action.state == "replace_wrong_symlink"
    action.apply()
    assert link.resolve() == target.resolve()


# ──────────────────────────────────────────────────────────────────
# build_plan + describe
# ──────────────────────────────────────────────────────────────────


def test_build_plan_describe_covers_essentials(
    fake_repo: Path, fake_workspace: Path
):
    plan = build_plan(
        repo=fake_repo,
        workspace=fake_workspace,
        brain_kind="claude-code",
    )
    out = "\n".join(plan.describe())
    assert "brain.kind = claude-code" in out
    assert str(fake_repo) in out
    assert str(fake_workspace) in out
    assert "codemux" in out


def test_build_plan_workspace_symlink_targets_workspace_claude(
    fake_repo: Path, fake_workspace: Path
):
    """The workspace symlink target is ``<workspace>/CLAUDE.md``,
    not the repo's CLAUDE.md — apply() copies the repo file into
    the workspace first so each install dir is self-contained."""
    plan = build_plan(
        repo=fake_repo,
        workspace=fake_workspace,
        brain_kind="claude-code",
    )
    assert plan.workspace_symlink is not None
    assert plan.workspace_symlink.target == fake_workspace / "CLAUDE.md"


def test_build_plan_no_repo_claude_skips_repo_symlink(
    tmp_path: Path, fake_workspace: Path
):
    """Repo without CLAUDE.md (degenerate) skips the repo symlink
    plan rather than crashing. Workspace symlink still plans
    (apply() will copy from the repo or leave the workspace
    untouched)."""
    repo = tmp_path / "no-claude-repo"
    repo.mkdir()
    plan = build_plan(
        repo=repo,
        workspace=fake_workspace,
        brain_kind="claude-code",
    )
    assert plan.repo_symlink is None
    assert plan.workspace_symlink is not None


# ──────────────────────────────────────────────────────────────────
# fatal_errors — brain binary on PATH
# ──────────────────────────────────────────────────────────────────


def test_fatal_errors_when_brain_binary_missing(
    fake_repo: Path, fake_workspace: Path, monkeypatch
):
    """brain.kind=opencode but the binary isn't on PATH → fatal."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    plan = build_plan(
        repo=fake_repo,
        workspace=fake_workspace,
        brain_kind="opencode",
    )
    errs = plan.fatal_errors()
    assert errs
    assert "opencode" in errs[0]
    assert "opencode.ai" in errs[0]  # install hint included


def test_no_fatal_errors_when_binary_present(
    fake_repo: Path, fake_workspace: Path, monkeypatch
):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    plan = build_plan(
        repo=fake_repo,
        workspace=fake_workspace,
        brain_kind="claude-code",
    )
    assert plan.fatal_errors() == []


# ──────────────────────────────────────────────────────────────────
# apply — claude-code
# ──────────────────────────────────────────────────────────────────


def test_apply_writes_claude_code_mcp_config(
    fake_repo: Path, fake_workspace: Path, monkeypatch
):
    """Real apply() against tmp workspace: symlinks created, MCP
    config written in claude-code's ``.mcp.json`` shape."""
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    plan = build_plan(
        repo=fake_repo,
        workspace=fake_workspace,
        brain_kind="claude-code",
    )
    written = plan.apply()
    # Repo symlink in place.
    assert (fake_repo / "AGENTS.md").is_symlink()
    assert (fake_repo / "AGENTS.md").resolve() == (fake_repo / "CLAUDE.md").resolve()
    # Workspace exists, has CLAUDE.md (copied), AGENTS.md symlink.
    assert fake_workspace.is_dir()
    assert (fake_workspace / "CLAUDE.md").is_file()
    assert (fake_workspace / "AGENTS.md").is_symlink()
    # MCP config written in claude-code shape.
    assert written == fake_workspace / ".mcp.json"
    assert written.is_file()
    data = json.loads(written.read_text(encoding="utf-8"))
    assert "mcpServers" in data
    assert "codemux" in data["mcpServers"]
    # Idempotence: a second apply produces a byte-identical config.
    contents1 = written.read_bytes()
    plan2 = build_plan(
        repo=fake_repo,
        workspace=fake_workspace,
        brain_kind="claude-code",
    )
    plan2.apply()
    contents2 = (fake_workspace / ".mcp.json").read_bytes()
    assert contents1 == contents2


# ──────────────────────────────────────────────────────────────────
# apply — opencode (namespace-prefix MCP merge)
# ──────────────────────────────────────────────────────────────────


def test_apply_writes_opencode_mcp_config_with_namespace(
    fake_repo: Path, fake_workspace: Path, monkeypatch
):
    """opencode brain writes ``<workspace>/opencode.json`` with
    each entry prefixed ``vexis-``. User-owned entries (any key
    not starting with the prefix) are preserved by the writer's
    namespace-merge strategy — pinned in
    ``test_brain_opencode_scaffold.py``; here we only verify the
    install script invokes the right writer."""
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    plan = build_plan(
        repo=fake_repo,
        workspace=fake_workspace,
        brain_kind="opencode",
    )
    written = plan.apply()
    assert written == fake_workspace / "opencode.json"
    data = json.loads(written.read_text(encoding="utf-8"))
    assert "mcp" in data
    # vexis-prefixed entry present (the writer adds the prefix).
    assert any(k.startswith("vexis-") for k in data["mcp"].keys())


# ──────────────────────────────────────────────────────────────────
# main() CLI entry
# ──────────────────────────────────────────────────────────────────


def test_main_dry_run_returns_zero_when_plan_clean(
    fake_repo: Path, fake_workspace: Path, monkeypatch
):
    """``--dry-run`` with a clean plan exits 0 and doesn't touch
    the filesystem."""
    monkeypatch.setattr("scripts.install.repo_root", lambda: fake_repo)
    monkeypatch.setattr(
        "scripts.install.resolve_brain_kind", lambda: "claude-code",
    )
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    rc = main([
        "--dry-run",
        "--workspace", str(fake_workspace),
        "--quiet",
    ])
    assert rc == 0
    # Filesystem untouched: no symlinks, no MCP config.
    assert not (fake_repo / "AGENTS.md").exists()
    assert not (fake_workspace / "AGENTS.md").exists()
    assert not (fake_workspace / ".mcp.json").exists()


def test_main_dry_run_returns_one_when_brain_missing(
    fake_repo: Path, fake_workspace: Path, monkeypatch
):
    """``--dry-run`` with a fatal planning error (brain binary
    missing) exits 1 — script callers can use this to gate
    install steps in a Makefile / CI."""
    monkeypatch.setattr("scripts.install.repo_root", lambda: fake_repo)
    monkeypatch.setattr(
        "scripts.install.resolve_brain_kind", lambda: "opencode",
    )
    monkeypatch.setattr("shutil.which", lambda name: None)
    rc = main([
        "--dry-run",
        "--workspace", str(fake_workspace),
        "--quiet",
    ])
    assert rc == 1


def test_main_apply_invocation(
    fake_repo: Path, fake_workspace: Path, monkeypatch
):
    """Without ``--dry-run`` main() actually applies — verify the
    end state matches the dry-run plan."""
    monkeypatch.setattr("scripts.install.repo_root", lambda: fake_repo)
    monkeypatch.setattr(
        "scripts.install.resolve_brain_kind", lambda: "claude-code",
    )
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    rc = main([
        "--workspace", str(fake_workspace),
        "--quiet",
    ])
    assert rc == 0
    assert (fake_workspace / ".mcp.json").is_file()
    assert (fake_workspace / "AGENTS.md").is_symlink()
