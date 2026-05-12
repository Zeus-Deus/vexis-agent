"""Bundled-skills discovery + read-only enforcement.

Pinned behaviours:

  * ``bundled_skills_root()`` resolves to the in-package directory
    when no env override is set.
  * ``$VEXIS_BUNDLED_SKILLS`` overrides the resolved path (deb/AUR
    relocation hook + test fixture mechanism).
  * ``discover_skills_with_bundled`` merges workspace + bundled,
    workspace wins on name collision, bundled metas carry
    ``source="bundled"``.
  * ``view_skill`` resolves bundled skills (read), telemetry written
    to the workspace's .usage.json regardless of source.
  * Write operations (edit / patch / delete / archive) on a name
    that exists ONLY in bundled return an error rather than
    silently no-oping or stomping.
  * The bundled tree shipped with vexis includes the kanban
    orchestrator + worker skills (acceptance gate so a future
    rename doesn't silently lose them).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vexis_agent.core.skills import (
    SkillMeta,
    bundled_skills_root,
    build_skills_index_block,
    create_skill,
    delete_skill,
    discover_skills,
    discover_skills_with_bundled,
    edit_skill,
    parse_skill_md,
    view_skill,
)


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace_skills(tmp_path: Path) -> Path:
    root = tmp_path / "workspace_skills"
    root.mkdir()
    return root


def _make_skill(root: Path, name: str, *, description: str = "test skill", body: str = "body") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.fixture
def bundled_override(tmp_path: Path, monkeypatch):
    """Point ``$VEXIS_BUNDLED_SKILLS`` at a tmp dir for tests that
    want to control bundled contents independently of the shipped
    package data."""
    bundled = tmp_path / "bundled_override"
    bundled.mkdir()
    monkeypatch.setenv("VEXIS_BUNDLED_SKILLS", str(bundled))
    return bundled


# ──────────────────────────────────────────────────────────────────
# bundled_skills_root resolver
# ──────────────────────────────────────────────────────────────────


def test_bundled_root_resolves_to_in_package_default(monkeypatch):
    """Without the env override, the resolver finds the package's
    own ``_bundled_skills/`` directory. This is the production path."""
    monkeypatch.delenv("VEXIS_BUNDLED_SKILLS", raising=False)
    root = bundled_skills_root()
    assert root is not None, "expected a bundled root in the shipped package"
    assert root.name == "_bundled_skills"
    # Sanity: contains at least one SKILL.md
    skill_files = list(root.rglob("SKILL.md"))
    assert len(skill_files) > 0


def test_bundled_root_env_override(bundled_override: Path):
    root = bundled_skills_root()
    assert root == bundled_override


def test_bundled_root_env_pointing_at_missing_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("VEXIS_BUNDLED_SKILLS", str(tmp_path / "does-not-exist"))
    assert bundled_skills_root() is None


# ──────────────────────────────────────────────────────────────────
# Multi-root discovery
# ──────────────────────────────────────────────────────────────────


def test_discover_with_bundled_merges_both_roots(
    workspace_skills: Path, bundled_override: Path,
):
    _make_skill(workspace_skills, "from-workspace", description="ws")
    _make_skill(bundled_override, "from-bundled", description="b")
    metas = discover_skills_with_bundled(workspace_skills)
    by_name = {m.name: m for m in metas}
    assert by_name["from-workspace"].source == "workspace"
    assert by_name["from-bundled"].source == "bundled"


def test_workspace_wins_on_name_collision(
    workspace_skills: Path, bundled_override: Path,
):
    """User intentionally overrides a bundled skill by creating their
    own with the same name. Workspace copy must win in the rendered
    index."""
    _make_skill(
        workspace_skills, "kanban-orchestrator",
        description="user override",
    )
    _make_skill(
        bundled_override, "kanban-orchestrator",
        description="bundled default",
    )
    metas = discover_skills_with_bundled(workspace_skills)
    matching = [m for m in metas if m.name == "kanban-orchestrator"]
    assert len(matching) == 1
    assert matching[0].source == "workspace"
    assert matching[0].description == "user override"


def test_no_bundled_root_falls_back_to_workspace_only(
    workspace_skills: Path, monkeypatch, tmp_path,
):
    monkeypatch.setenv("VEXIS_BUNDLED_SKILLS", str(tmp_path / "missing"))
    _make_skill(workspace_skills, "ws-only")
    metas = discover_skills_with_bundled(workspace_skills)
    assert {m.name for m in metas} == {"ws-only"}


def test_discover_skills_single_root_unchanged(workspace_skills: Path):
    """Back-compat: the original single-root discover_skills still
    works and tags everything as workspace-source."""
    _make_skill(workspace_skills, "x")
    metas = discover_skills(workspace_skills)
    assert len(metas) == 1
    assert metas[0].source == "workspace"


# ──────────────────────────────────────────────────────────────────
# Index render
# ──────────────────────────────────────────────────────────────────


def test_index_block_marks_bundled_with_label(
    workspace_skills: Path, bundled_override: Path,
):
    _make_skill(workspace_skills, "ws", description="ws desc")
    _make_skill(bundled_override, "bun", description="bun desc")
    block = build_skills_index_block(workspace_skills)
    assert "<available_skills>" in block
    assert "- ws: ws desc" in block
    # Bundled rows carry a [bundled] tag so the brain can tell them
    # apart in the index — drives the override-by-creating-with-same-
    # name UX.
    assert "- bun [bundled]: bun desc" in block


def test_index_block_empty_when_neither_root_has_skills(
    workspace_skills: Path, monkeypatch, tmp_path,
):
    """If both roots are empty the block is empty (no preamble noise)."""
    monkeypatch.setenv("VEXIS_BUNDLED_SKILLS", str(tmp_path / "empty"))
    block = build_skills_index_block(workspace_skills)
    assert block == ""


# ──────────────────────────────────────────────────────────────────
# parse_skill_md source tagging
# ──────────────────────────────────────────────────────────────────


def test_parse_skill_md_tags_source():
    content = "---\nname: x\ndescription: y\n---\nbody"
    meta_default = parse_skill_md(content)
    assert meta_default is not None
    assert meta_default.source == "workspace"

    meta_bundled = parse_skill_md(content, source="bundled")
    assert meta_bundled is not None
    assert meta_bundled.source == "bundled"


# ──────────────────────────────────────────────────────────────────
# view_skill resolves bundled
# ──────────────────────────────────────────────────────────────────


def test_view_skill_reads_bundled(
    workspace_skills: Path, bundled_override: Path,
):
    _make_skill(
        bundled_override, "bundled-only",
        body="canonical bundled body content",
    )
    res = view_skill(workspace_skills, "bundled-only")
    assert res.ok, res.message
    assert "canonical bundled body content" in res.extra["content"]


def test_view_skill_workspace_wins_over_bundled(
    workspace_skills: Path, bundled_override: Path,
):
    _make_skill(
        workspace_skills, "shared",
        body="WORKSPACE_BODY_MARKER",
    )
    _make_skill(
        bundled_override, "shared",
        body="BUNDLED_BODY_MARKER",
    )
    res = view_skill(workspace_skills, "shared")
    assert res.ok
    assert "WORKSPACE_BODY_MARKER" in res.extra["content"]
    assert "BUNDLED_BODY_MARKER" not in res.extra["content"]


# ──────────────────────────────────────────────────────────────────
# Write ops refuse on bundled
# ──────────────────────────────────────────────────────────────────


def test_edit_refuses_bundled(
    workspace_skills: Path, bundled_override: Path,
):
    _make_skill(
        bundled_override, "bundled-only",
        body="original bundled body",
    )
    new_content = "---\nname: bundled-only\ndescription: hijacked\n---\nbad\n"
    res = edit_skill(workspace_skills, "bundled-only", new_content)
    assert not res.ok
    # Either the misleading "no skill named" or the new bundled-readonly
    # message is acceptable for v1 — the critical pin is that the bundled
    # file on disk is unchanged.
    bundled_md = bundled_override / "bundled-only" / "SKILL.md"
    assert "original bundled body" in bundled_md.read_text(encoding="utf-8")


def test_delete_refuses_bundled(
    workspace_skills: Path, bundled_override: Path,
):
    _make_skill(bundled_override, "bundled-only")
    res = delete_skill(workspace_skills, "bundled-only")
    assert not res.ok
    # Verify bundled directory is intact.
    assert (bundled_override / "bundled-only" / "SKILL.md").exists()


def test_create_with_same_name_overrides_bundled(
    workspace_skills: Path, bundled_override: Path,
):
    """Create with same name as a bundled skill should SUCCEED — that's
    the override mechanism. Workspace copy then shadows bundled in the
    index."""
    _make_skill(bundled_override, "shared")
    content = (
        "---\nname: shared\ndescription: my override\n---\noverride body\n"
    )
    res = create_skill(workspace_skills, "shared", content)
    assert res.ok, res.message
    metas = discover_skills_with_bundled(workspace_skills)
    matching = [m for m in metas if m.name == "shared"]
    assert len(matching) == 1
    assert matching[0].source == "workspace"


# ──────────────────────────────────────────────────────────────────
# Acceptance: kanban skills ship with vexis
# ──────────────────────────────────────────────────────────────────


def test_kanban_skills_ship_in_bundled_root(monkeypatch):
    """Acceptance gate: vexis ships with the kanban orchestrator + worker
    skills out of the box. A future rename or refactor will trip this
    test before it lands.
    """
    monkeypatch.delenv("VEXIS_BUNDLED_SKILLS", raising=False)
    root = bundled_skills_root()
    assert root is not None
    names = {p.parent.name for p in root.rglob("SKILL.md")}
    assert "kanban-orchestrator" in names, names
    assert "kanban-worker" in names, names


def test_kanban_skills_render_in_index_block(monkeypatch, workspace_skills):
    """Bundled kanban skills appear in the brain's index even on an
    empty workspace — the user gets them on first install."""
    monkeypatch.delenv("VEXIS_BUNDLED_SKILLS", raising=False)
    block = build_skills_index_block(workspace_skills)
    assert "kanban-orchestrator [bundled]" in block
    assert "kanban-worker [bundled]" in block
