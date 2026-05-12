"""Skill install + uninstall + provenance + read-only enforcement.

Pinned behaviours:

  * Local-path install reads the file, validates SKILL.md, drops
    into ``installed/<name>/`` with a ``.provenance.json`` sidecar.
  * URL install (mocked) follows the same path.
  * Refuses re-install on existing installed skill unless --overwrite.
  * Refuses install when a non-installed workspace skill of the same
    name already exists.
  * Refuses bytes > MAX_FETCH_BYTES.
  * Discovery tags installed skills as ``source="installed"``;
    unrelated workspace skills stay ``source="workspace"``.
  * Build-skills-index-block renders ``[installed]`` tag.
  * Write-op refusal: edit / delete on an installed skill returns
    the read-only error and does NOT touch the file.
  * Uninstall removes the dir + file. Refuses on missing or on a
    workspace-authored skill (no provenance marker).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from vexis_agent.core.skill_install import (
    INSTALLED_DIR_NAME,
    PROVENANCE_FILENAME,
    Provenance,
    install_skill,
    is_installed_skill_dir,
    load_provenance,
    uninstall_skill,
)
from vexis_agent.core.skills import (
    build_skills_index_block,
    delete_skill,
    discover_skills_with_bundled,
    edit_skill,
)


SAMPLE_SKILL = """\
---
name: my-installed-skill
description: A test skill installed from an external source for the test suite.
---

# My Installed Skill

This is the body of the installed skill.
"""


@pytest.fixture
def workspace_skills(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    return root


@pytest.fixture
def local_skill_md(tmp_path: Path) -> Path:
    src = tmp_path / "fixture-skill.md"
    src.write_text(SAMPLE_SKILL, encoding="utf-8")
    return src


@pytest.fixture(autouse=True)
def _bundled_off(monkeypatch, tmp_path):
    """Point the bundled root at an empty dir so the always-shipped
    kanban-orchestrator + kanban-worker don't leak into unrelated
    install assertions."""
    monkeypatch.setenv("VEXIS_BUNDLED_SKILLS", str(tmp_path / "no-bundled"))
    yield


# ──────────────────────────────────────────────────────────────────
# Local-path install
# ──────────────────────────────────────────────────────────────────


def test_install_from_local_file(
    workspace_skills: Path, local_skill_md: Path,
):
    result = install_skill(workspace_skills, str(local_skill_md))
    assert result.ok, result.message
    assert result.name == "my-installed-skill"

    target = workspace_skills / INSTALLED_DIR_NAME / "my-installed-skill"
    assert (target / "SKILL.md").is_file()
    assert (target / PROVENANCE_FILENAME).is_file()


def test_install_creates_provenance_record(
    workspace_skills: Path, local_skill_md: Path,
):
    result = install_skill(workspace_skills, str(local_skill_md))
    assert result.provenance is not None
    assert result.provenance.source_kind == "file"
    assert result.provenance.sha256
    assert result.provenance.bytes_fetched > 0
    assert result.provenance.installed_at  # ISO timestamp


def test_provenance_round_trip_through_json(
    workspace_skills: Path, local_skill_md: Path,
):
    install_skill(workspace_skills, str(local_skill_md))
    target = workspace_skills / INSTALLED_DIR_NAME / "my-installed-skill"
    raw = json.loads((target / PROVENANCE_FILENAME).read_text())
    parsed = Provenance.from_dict(raw)
    assert parsed.source_kind == "file"
    assert parsed.sha256


def test_install_refuses_duplicate_without_overwrite(
    workspace_skills: Path, local_skill_md: Path,
):
    install_skill(workspace_skills, str(local_skill_md))
    second = install_skill(workspace_skills, str(local_skill_md))
    assert not second.ok
    assert "already installed" in second.message


def test_install_overwrite_replaces(
    workspace_skills: Path, local_skill_md: Path, tmp_path: Path,
):
    install_skill(workspace_skills, str(local_skill_md))
    # Modify the source so the re-install differs.
    new_text = SAMPLE_SKILL + "\nadditional content\n"
    new_path = tmp_path / "v2.md"
    new_path.write_text(new_text, encoding="utf-8")
    second = install_skill(
        workspace_skills, str(new_path), overwrite=True,
    )
    assert second.ok, second.message
    target = workspace_skills / INSTALLED_DIR_NAME / "my-installed-skill"
    assert "additional content" in (target / "SKILL.md").read_text()


def test_install_refuses_missing_file(workspace_skills: Path):
    result = install_skill(workspace_skills, "/does/not/exist.md")
    assert not result.ok
    assert "fetch failed" in result.message.lower()


def test_install_refuses_non_utf8(workspace_skills: Path, tmp_path: Path):
    bad = tmp_path / "binary.md"
    bad.write_bytes(b"\xff\xfe\xfd not utf-8")
    result = install_skill(workspace_skills, str(bad))
    assert not result.ok
    assert "utf-8" in result.message


def test_install_refuses_invalid_skill_md(
    workspace_skills: Path, tmp_path: Path,
):
    bad = tmp_path / "no-frontmatter.md"
    bad.write_text("just markdown without frontmatter\n", encoding="utf-8")
    result = install_skill(workspace_skills, str(bad))
    assert not result.ok
    assert "invalid SKILL.md" in result.message


def test_install_refuses_collision_with_workspace_skill(
    workspace_skills: Path, local_skill_md: Path,
):
    """Pre-create a workspace-authored skill with the same name.
    Install must refuse so it doesn't shadow the user's work."""
    ws_skill_dir = workspace_skills / "my-installed-skill"
    ws_skill_dir.mkdir(parents=True)
    (ws_skill_dir / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")
    result = install_skill(workspace_skills, str(local_skill_md))
    assert not result.ok
    assert "workspace-authored" in result.message


# ──────────────────────────────────────────────────────────────────
# URL install (mocked urllib)
# ──────────────────────────────────────────────────────────────────


def test_install_from_url_mocked(workspace_skills: Path):
    """End-to-end install from an https URL with urllib mocked.
    Confirms the full path: classify → fetch → validate → write
    → provenance record carries source_kind='https'."""

    class FakeResponse:
        headers = {"content-length": str(len(SAMPLE_SKILL.encode("utf-8")))}

        def read(self, n: int) -> bytes:
            return SAMPLE_SKILL.encode("utf-8")[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch(
        "vexis_agent.core.skill_install.urllib.request.urlopen",
        return_value=FakeResponse(),
    ):
        result = install_skill(
            workspace_skills,
            "https://example.test/skills/my-installed-skill/SKILL.md",
        )
    assert result.ok, result.message
    assert result.provenance is not None
    assert result.provenance.source_kind == "https"
    assert result.provenance.source.startswith("https://")


def test_install_refuses_oversize_response(workspace_skills: Path):
    """Response > MAX_FETCH_BYTES is refused before the body is
    written to disk."""

    big = b"a" * (256 * 1024 + 1)  # one byte over

    class FakeResponse:
        headers = {"content-length": str(len(big))}

        def read(self, n: int) -> bytes:
            return big[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch(
        "vexis_agent.core.skill_install.urllib.request.urlopen",
        return_value=FakeResponse(),
    ):
        result = install_skill(
            workspace_skills, "https://example.test/big",
        )
    assert not result.ok
    assert "refusing fetch" in result.message.lower() or "too" in result.message.lower()


# ──────────────────────────────────────────────────────────────────
# Discovery + index render
# ──────────────────────────────────────────────────────────────────


def test_discovery_tags_installed_source(
    workspace_skills: Path, local_skill_md: Path,
):
    install_skill(workspace_skills, str(local_skill_md))
    metas = discover_skills_with_bundled(workspace_skills)
    matching = [m for m in metas if m.name == "my-installed-skill"]
    assert len(matching) == 1
    assert matching[0].source == "installed"


def test_workspace_skills_unaffected_by_install(
    workspace_skills: Path, local_skill_md: Path,
):
    """A workspace-authored skill in a different name keeps source="workspace"."""
    ws_dir = workspace_skills / "my-other-skill"
    ws_dir.mkdir(parents=True)
    (ws_dir / "SKILL.md").write_text(
        "---\nname: my-other-skill\ndescription: x\n---\nbody\n",
        encoding="utf-8",
    )
    install_skill(workspace_skills, str(local_skill_md))
    metas = discover_skills_with_bundled(workspace_skills)
    by_name = {m.name: m for m in metas}
    assert by_name["my-other-skill"].source == "workspace"
    assert by_name["my-installed-skill"].source == "installed"


def test_index_block_marks_installed_with_label(
    workspace_skills: Path, local_skill_md: Path,
):
    install_skill(workspace_skills, str(local_skill_md))
    block = build_skills_index_block(workspace_skills)
    assert "[installed]" in block
    assert "my-installed-skill [installed]" in block


# ──────────────────────────────────────────────────────────────────
# Read-only enforcement
# ──────────────────────────────────────────────────────────────────


def test_edit_refused_on_installed_skill(
    workspace_skills: Path, local_skill_md: Path,
):
    install_skill(workspace_skills, str(local_skill_md))
    new_content = (
        "---\nname: my-installed-skill\ndescription: hijacked\n---\nbad\n"
    )
    res = edit_skill(workspace_skills, "my-installed-skill", new_content)
    assert not res.ok
    # On-disk content unchanged.
    target = workspace_skills / INSTALLED_DIR_NAME / "my-installed-skill" / "SKILL.md"
    assert "hijacked" not in target.read_text()


def test_delete_refused_on_installed_skill(
    workspace_skills: Path, local_skill_md: Path,
):
    install_skill(workspace_skills, str(local_skill_md))
    res = delete_skill(workspace_skills, "my-installed-skill")
    assert not res.ok
    target = workspace_skills / INSTALLED_DIR_NAME / "my-installed-skill"
    assert (target / "SKILL.md").exists()


# ──────────────────────────────────────────────────────────────────
# Uninstall
# ──────────────────────────────────────────────────────────────────


def test_uninstall_removes_dir(
    workspace_skills: Path, local_skill_md: Path,
):
    install_skill(workspace_skills, str(local_skill_md))
    res = uninstall_skill(workspace_skills, "my-installed-skill")
    assert res.ok, res.message
    target = workspace_skills / INSTALLED_DIR_NAME / "my-installed-skill"
    assert not target.exists()


def test_uninstall_missing_skill(workspace_skills: Path):
    res = uninstall_skill(workspace_skills, "ghost")
    assert not res.ok
    assert "no installed skill" in res.message


def test_uninstall_refuses_workspace_skill(workspace_skills: Path):
    """A skill that lives in installed/<name>/ but has NO provenance
    marker is suspicious — refuse to remove it via uninstall (the
    user can `delete` it explicitly if they really want to)."""
    fake = workspace_skills / INSTALLED_DIR_NAME / "phantom"
    fake.mkdir(parents=True)
    (fake / "SKILL.md").write_text(
        "---\nname: phantom\ndescription: x\n---\nbody\n",
        encoding="utf-8",
    )
    # NO .provenance.json written
    res = uninstall_skill(workspace_skills, "phantom")
    assert not res.ok
    assert "no .provenance.json" in res.message or "provenance" in res.message
    assert (fake / "SKILL.md").exists()


# ──────────────────────────────────────────────────────────────────
# Provenance helpers
# ──────────────────────────────────────────────────────────────────


def test_is_installed_skill_dir(workspace_skills, local_skill_md):
    install_skill(workspace_skills, str(local_skill_md))
    target = workspace_skills / INSTALLED_DIR_NAME / "my-installed-skill"
    assert is_installed_skill_dir(target)
    # A workspace-authored skill returns False.
    other = workspace_skills / "other"
    other.mkdir()
    (other / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")
    assert not is_installed_skill_dir(other)


def test_load_provenance_returns_none_on_missing(tmp_path):
    assert load_provenance(tmp_path) is None
