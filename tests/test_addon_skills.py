"""Tests for ``core.addon_skills.install_addon_skills``.

Pinned behaviour:

  * First install copies every registered skill into
    ``<workspace>/skills/addons/<name>/`` with a provenance sidecar.
  * Re-running on identical content is a no-op (hash match).
  * Re-running on changed content overwrites atomically.
  * Per-skill failures are logged but don't abort sibling installs.
  * ``uninstall_addon_skills`` removes everything for one add-on.
"""

from __future__ import annotations

import json
from pathlib import Path

from vexis_agent.core.addon_skills import (
    install_addon_skills,
    uninstall_addon_skills,
)
from vexis_agent.core.addons import AddonRuntime, make_context, AddonConfig


def _drop_skill(addon_dir: Path, name: str, body: str = "# skill") -> Path:
    """Create a fake skill file inside ``addon_dir``."""
    skill = addon_dir / "skills" / name
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(body, encoding="utf-8")
    return skill


def _make_runtime_with_skill(
    tmp_path: Path, addon_name: str = "myaddon",
    skill_name: str = "hello.md", body: str = "# hello",
) -> AddonRuntime:
    runtime = AddonRuntime()
    addon_dir = tmp_path / "src" / addon_name
    addon_dir.mkdir(parents=True)
    skill = _drop_skill(addon_dir, skill_name, body)
    ctx = make_context(
        runtime,
        addon_name=addon_name,
        addon_dir=addon_dir,
        config=AddonConfig(),
    )
    ctx.register_skill(skill)
    return runtime


def test_install_copies_skill(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = _make_runtime_with_skill(tmp_path)
    n = install_addon_skills(workspace, runtime)
    assert n == 1
    dest = workspace / "skills" / "addons" / "myaddon" / "hello.md"
    assert dest.is_file()
    assert dest.read_text() == "# hello"


def test_install_writes_provenance_sidecar(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = _make_runtime_with_skill(tmp_path, body="# rich content")
    install_addon_skills(workspace, runtime)
    sidecar = (
        workspace / "skills" / "addons" / "myaddon"
        / "hello.md.provenance.json"
    )
    assert sidecar.is_file()
    data = json.loads(sidecar.read_text())
    assert data["kind"] == "addon"
    assert data["addon_name"] == "myaddon"
    assert data["sha256"]
    assert data["installed_at"]


def test_install_is_idempotent_on_identical_content(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = _make_runtime_with_skill(tmp_path)
    first = install_addon_skills(workspace, runtime)
    second = install_addon_skills(workspace, runtime)
    assert first == 1
    assert second == 0  # no writes needed


def test_install_overwrites_changed_content(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = _make_runtime_with_skill(tmp_path, body="# v1")
    install_addon_skills(workspace, runtime)

    # Mutate the source file (simulating an addon update).
    src = tmp_path / "src" / "myaddon" / "skills" / "hello.md"
    src.write_text("# v2", encoding="utf-8")
    n = install_addon_skills(workspace, runtime)
    assert n == 1  # rewrote
    dest = workspace / "skills" / "addons" / "myaddon" / "hello.md"
    assert dest.read_text() == "# v2"


def test_install_handles_multiple_addons(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = AddonRuntime()
    for name in ("alpha", "beta"):
        addon_dir = tmp_path / "src" / name
        addon_dir.mkdir(parents=True)
        skill = _drop_skill(addon_dir, f"{name}.md", f"# {name}")
        ctx = make_context(
            runtime, addon_name=name, addon_dir=addon_dir,
            config=AddonConfig(),
        )
        ctx.register_skill(skill)

    install_addon_skills(workspace, runtime)
    assert (workspace / "skills" / "addons" / "alpha" / "alpha.md").is_file()
    assert (workspace / "skills" / "addons" / "beta" / "beta.md").is_file()


def test_install_target_subdir(tmp_path: Path):
    """A non-``.`` ``target_subdir`` lands the file under that subfolder."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = AddonRuntime()
    addon_dir = tmp_path / "src" / "myaddon"
    addon_dir.mkdir(parents=True)
    skill = _drop_skill(addon_dir, "hello.md")
    ctx = make_context(
        runtime, addon_name="myaddon", addon_dir=addon_dir,
        config=AddonConfig(),
    )
    ctx.register_skill(skill, target_subdir="subfolder")
    install_addon_skills(workspace, runtime)
    assert (
        workspace / "skills" / "addons" / "myaddon" / "subfolder" / "hello.md"
    ).is_file()


def test_uninstall_removes_addon_skills(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = _make_runtime_with_skill(tmp_path)
    install_addon_skills(workspace, runtime)
    addon_dir = workspace / "skills" / "addons" / "myaddon"
    assert addon_dir.is_dir()
    n = uninstall_addon_skills(workspace, "myaddon")
    assert n >= 1
    assert not addon_dir.exists()


def test_uninstall_missing_addon_returns_zero(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    n = uninstall_addon_skills(workspace, "nope")
    assert n == 0
