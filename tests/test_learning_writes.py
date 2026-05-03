"""Day 2 tests for core/learning_writes.py.

Coverage:
  - shadow_skills_root: created on demand, lives under skills/.shadow/.
  - stage_skill_patch: happy path, pinned refusal, missing live skill,
    patch_old not found, multi-match refusal, frontmatter-name change
    rejection.
  - stage_support_file: happy path, pinned refusal, invalid subdir,
    absolute / parent-traversal path rejection, missing live skill.
  - stage_new_skill: happy path, name validation, body validation,
    frontmatter name-mismatch, live-tree collision, archive collision,
    staging-tree collision.
  - list_staged_skills: empty, S3-only, S1+S2 combo, mixed.
  - flip_shadow_to_live: S3 creates new live skill + initializes
    telemetry, S1/S2 overlay onto existing skill + bumps patch
    telemetry, only_skill scoping, pinned refusal at flip time,
    re-running flip on cleared staging is a no-op.
  - successive S1 patches accumulate against the staged version,
    not the live version (correctness for in-flight chains).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import learning_writes as lw
from core.paths import skills_dir
from core.skills import (
    ARCHIVE_DIR_NAME,
    PinStore,
    UsageStore,
    create_skill,
)


# --------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A fresh workspace with an empty skills tree."""
    ws = tmp_path / "vexis-workspace"
    (ws / "skills").mkdir(parents=True)
    return ws


def _create_live_skill(
    workspace: Path,
    name: str,
    *,
    body_section: str = "## Section A\nlive content here\n",
    description: str = "Test skill.",
    category: str | None = None,
) -> Path:
    """Helper: create a live skill via the public API and return its dir."""
    body = (
        f"---\nname: {name}\ndescription: {description}\n---\n\n"
        f"{body_section}"
    )
    result = create_skill(skills_dir(workspace), name, body, category)
    assert result.ok, f"setup: create_skill failed — {result.message}"
    return Path(result.extra["path"]).parent


# --------------------------------------------------------------------
# shadow_skills_root
# --------------------------------------------------------------------


def test_shadow_skills_root_created_on_demand(workspace):
    root = lw.shadow_skills_root(workspace)
    assert root.exists() and root.is_dir()
    assert root.name == ".shadow"
    assert root.parent == skills_dir(workspace)


def test_shadow_skills_root_invisible_to_discover_skills(workspace):
    """The whole point of the .shadow/ prefix: discover_skills must
    not see staged skills (they're not real skills yet)."""
    from core.skills import discover_skills
    _create_live_skill(workspace, "real-skill")
    # Stage a fake skill in .shadow/
    body = "---\nname: ghost\ndescription: D.\norigin: learning-curator\n---\n\nB\n"
    lw.stage_new_skill(workspace, "ghost", body)
    metas = discover_skills(skills_dir(workspace))
    names = {m.name for m in metas}
    assert "real-skill" in names
    assert "ghost" not in names  # invisible because it's under .shadow/


# --------------------------------------------------------------------
# stage_skill_patch (S1)
# --------------------------------------------------------------------


def test_stage_skill_patch_happy_path(workspace):
    _create_live_skill(workspace, "test-skill")
    result = lw.stage_skill_patch(
        workspace, "test-skill", "live content here", "patched content here"
    )
    assert result.ok, result.message
    assert result.staged_path is not None
    assert result.staged_path.exists()
    body = result.staged_path.read_text(encoding="utf-8")
    assert "patched content here" in body
    assert "live content here" not in body
    # Live skill is unchanged:
    live = (skills_dir(workspace) / "test-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "live content here" in live
    # A diff sidecar is also written:
    diff_path = result.staged_path.with_name("SKILL.md.diff")
    assert diff_path.exists()
    assert "patched content here" in diff_path.read_text(encoding="utf-8")


def test_stage_skill_patch_refuses_pinned(workspace):
    _create_live_skill(workspace, "pinned-skill")
    PinStore(skills_dir(workspace)).pin("pinned-skill")
    result = lw.stage_skill_patch(
        workspace, "pinned-skill", "live content here", "patched content here"
    )
    assert result.ok is False
    assert "pinned" in result.message.lower()


def test_stage_skill_patch_refuses_missing_live_skill(workspace):
    result = lw.stage_skill_patch(
        workspace, "ghost-skill", "anything", "anything else"
    )
    assert result.ok is False
    assert "no live skill" in result.message.lower() or "doesn't exist" in result.message.lower()


def test_stage_skill_patch_refuses_patch_old_not_found(workspace):
    _create_live_skill(workspace, "test-skill")
    result = lw.stage_skill_patch(
        workspace, "test-skill",
        "this string does not appear anywhere",
        "replacement",
    )
    assert result.ok is False
    assert "not found" in result.message.lower()


def test_stage_skill_patch_refuses_multi_match_to_force_uniqueness(workspace):
    body = (
        "## Section A\n"
        "duplicate line\n"
        "## Section B\n"
        "duplicate line\n"
    )
    _create_live_skill(workspace, "ambiguous", body_section=body)
    result = lw.stage_skill_patch(
        workspace, "ambiguous", "duplicate line", "unique replacement"
    )
    assert result.ok is False
    assert "uniquely" in result.message.lower() or "match" in result.message.lower()


def test_stage_skill_patch_refuses_frontmatter_name_change(workspace):
    _create_live_skill(workspace, "test-skill")
    # Patch the name field in frontmatter — should be refused because
    # the post-patch parsed name no longer matches.
    result = lw.stage_skill_patch(
        workspace, "test-skill",
        "name: test-skill", "name: renamed-skill",
    )
    assert result.ok is False


def test_stage_skill_patch_successive_patches_accumulate(workspace):
    """Two patches against the same skill should chain — the second
    patch reads the staged body (not live), so it can patch text the
    first patch introduced."""
    _create_live_skill(workspace, "chain-skill")
    r1 = lw.stage_skill_patch(
        workspace, "chain-skill", "live content here", "first-patch content\nINSERT HERE\n"
    )
    assert r1.ok, r1.message
    r2 = lw.stage_skill_patch(
        workspace, "chain-skill", "INSERT HERE", "second-patch addition"
    )
    assert r2.ok, r2.message
    body = r2.staged_path.read_text(encoding="utf-8")
    assert "first-patch content" in body
    assert "second-patch addition" in body
    assert "INSERT HERE" not in body


# --------------------------------------------------------------------
# stage_support_file (S2)
# --------------------------------------------------------------------


def test_stage_support_file_happy_path(workspace):
    _create_live_skill(workspace, "umbrella-skill")
    result = lw.stage_support_file(
        workspace, "umbrella-skill",
        "references/notes.md", "Notes content here\n",
    )
    assert result.ok, result.message
    assert result.staged_path.exists()
    assert result.staged_path.read_text(encoding="utf-8") == "Notes content here\n"
    # Live skill's references/ is untouched:
    live_refs = skills_dir(workspace) / "umbrella-skill" / "references"
    assert not live_refs.exists()


def test_stage_support_file_refuses_pinned(workspace):
    _create_live_skill(workspace, "pinned-umbrella")
    PinStore(skills_dir(workspace)).pin("pinned-umbrella")
    result = lw.stage_support_file(
        workspace, "pinned-umbrella",
        "references/notes.md", "content",
    )
    assert result.ok is False
    assert "pinned" in result.message.lower()


def test_stage_support_file_refuses_missing_live_skill(workspace):
    result = lw.stage_support_file(
        workspace, "ghost-skill",
        "references/notes.md", "content",
    )
    assert result.ok is False
    assert "no live skill" in result.message.lower() or "S2 requires" in result.message


def test_stage_support_file_refuses_invalid_subdir(workspace):
    _create_live_skill(workspace, "test-skill")
    result = lw.stage_support_file(
        workspace, "test-skill",
        "secrets/leak.md", "evil content",
    )
    assert result.ok is False


def test_stage_support_file_refuses_absolute_path(workspace):
    _create_live_skill(workspace, "test-skill")
    result = lw.stage_support_file(
        workspace, "test-skill",
        "/etc/passwd", "content",
    )
    assert result.ok is False


def test_stage_support_file_refuses_parent_traversal(workspace):
    _create_live_skill(workspace, "test-skill")
    result = lw.stage_support_file(
        workspace, "test-skill",
        "references/../../../escape.md", "content",
    )
    assert result.ok is False


# --------------------------------------------------------------------
# stage_new_skill (S3)
# --------------------------------------------------------------------


def _new_skill_body(name: str, *, with_origin: bool = True) -> str:
    origin = "\norigin: learning-curator" if with_origin else ""
    return (
        f"---\nname: {name}\ndescription: Test new skill.{origin}\n---\n\n"
        f"# Body\n\nNew skill content.\n"
    )


def test_stage_new_skill_happy_path(workspace):
    result = lw.stage_new_skill(workspace, "fresh-skill", _new_skill_body("fresh-skill"))
    assert result.ok, result.message
    assert result.staged_path.exists()
    assert "origin: learning-curator" in result.staged_path.read_text(encoding="utf-8")


def test_stage_new_skill_with_category(workspace):
    body = _new_skill_body("categorized")
    result = lw.stage_new_skill(workspace, "categorized", body, category="my-cat")
    assert result.ok, result.message
    expected = lw.shadow_skills_root(workspace) / "my-cat" / "categorized" / "SKILL.md"
    assert result.staged_path == expected


def test_stage_new_skill_refuses_invalid_name(workspace):
    result = lw.stage_new_skill(workspace, "Invalid Name!", _new_skill_body("Invalid Name!"))
    assert result.ok is False


def test_stage_new_skill_refuses_invalid_body(workspace):
    result = lw.stage_new_skill(workspace, "fresh-skill", "no frontmatter at all")
    assert result.ok is False


def test_stage_new_skill_refuses_frontmatter_name_mismatch(workspace):
    body = _new_skill_body("different-name")
    result = lw.stage_new_skill(workspace, "fresh-skill", body)
    assert result.ok is False


def test_stage_new_skill_refuses_live_collision(workspace):
    _create_live_skill(workspace, "existing")
    result = lw.stage_new_skill(workspace, "existing", _new_skill_body("existing"))
    assert result.ok is False
    assert "live skill" in result.message.lower() or "already exists" in result.message.lower()


def test_stage_new_skill_refuses_archive_collision(workspace):
    """A skill in the archive blocks S3 with the same name (mirrors
    create_skill's behavior in core/skills.py)."""
    archive_dir = skills_dir(workspace) / ARCHIVE_DIR_NAME / "archived-skill"
    archive_dir.mkdir(parents=True)
    (archive_dir / "SKILL.md").write_text(
        "---\nname: archived-skill\ndescription: D.\n---\n\nB\n",
        encoding="utf-8",
    )
    result = lw.stage_new_skill(
        workspace, "archived-skill", _new_skill_body("archived-skill")
    )
    assert result.ok is False
    assert "archive" in result.message.lower()


def test_stage_new_skill_refuses_staging_collision(workspace):
    """A second S3 with the same name must fail rather than silently
    overwrite — the user resolves manually."""
    body = _new_skill_body("twin")
    r1 = lw.stage_new_skill(workspace, "twin", body)
    assert r1.ok
    r2 = lw.stage_new_skill(workspace, "twin", body)
    assert r2.ok is False
    assert "staged skill" in r2.message.lower() or "already" in r2.message.lower()


# --------------------------------------------------------------------
# list_staged_skills
# --------------------------------------------------------------------


def test_list_staged_skills_empty_tree(workspace):
    assert lw.list_staged_skills(workspace) == []


def test_list_staged_skills_picks_up_s3(workspace):
    lw.stage_new_skill(workspace, "fresh", _new_skill_body("fresh"))
    staged = lw.list_staged_skills(workspace)
    assert len(staged) == 1
    assert staged[0].name == "fresh"
    assert staged[0].live_dir is None  # S3 — no live counterpart yet
    assert staged[0].has_skill_md is True
    assert staged[0].support_files == ()


def test_list_staged_skills_picks_up_s2_only(workspace):
    """An S2-only stage (support file added under existing skill,
    SKILL.md not patched) must still appear — list_staged_skills
    can't rely on staged SKILL.md presence as the discovery signal."""
    _create_live_skill(workspace, "umbrella")
    lw.stage_support_file(workspace, "umbrella", "references/notes.md", "x")
    staged = lw.list_staged_skills(workspace)
    assert len(staged) == 1
    assert staged[0].name == "umbrella"
    assert staged[0].live_dir is not None  # S2 references existing live skill
    assert staged[0].has_skill_md is False
    assert len(staged[0].support_files) == 1


def test_list_staged_skills_combines_s1_and_s2_into_one_entry(workspace):
    _create_live_skill(workspace, "umbrella")
    lw.stage_skill_patch(workspace, "umbrella", "live content here", "patched")
    lw.stage_support_file(workspace, "umbrella", "references/notes.md", "x")
    staged = lw.list_staged_skills(workspace)
    assert len(staged) == 1
    assert staged[0].name == "umbrella"
    assert staged[0].has_skill_md is True
    assert len(staged[0].support_files) == 1


def test_list_staged_skills_sorted_by_name(workspace):
    lw.stage_new_skill(workspace, "zebra", _new_skill_body("zebra"))
    lw.stage_new_skill(workspace, "alpha", _new_skill_body("alpha"))
    lw.stage_new_skill(workspace, "mango", _new_skill_body("mango"))
    staged = lw.list_staged_skills(workspace)
    assert [s.name for s in staged] == ["alpha", "mango", "zebra"]


# --------------------------------------------------------------------
# flip_shadow_to_live
# --------------------------------------------------------------------


def test_flip_shadow_no_op_on_empty_staging(workspace):
    assert lw.flip_shadow_to_live(workspace) == []


def test_flip_shadow_promotes_s3_into_live_tree(workspace):
    lw.stage_new_skill(workspace, "fresh", _new_skill_body("fresh"))
    results = lw.flip_shadow_to_live(workspace)
    assert len(results) == 1
    assert results[0].ok
    assert results[0].is_new_skill
    assert "SKILL.md" in results[0].files_copied
    # Live tree picked it up:
    live = skills_dir(workspace) / "fresh" / "SKILL.md"
    assert live.exists()
    assert "origin: learning-curator" in live.read_text(encoding="utf-8")
    # Staging cleared:
    assert lw.list_staged_skills(workspace) == []
    # Telemetry was initialized for the new skill:
    usage = UsageStore(skills_dir(workspace)).load()
    assert "fresh" in usage


def test_flip_shadow_promotes_s1_patch_into_live(workspace):
    _create_live_skill(workspace, "patched")
    lw.stage_skill_patch(workspace, "patched", "live content here", "patched content")
    pre_usage = UsageStore(skills_dir(workspace)).record("patched")
    pre_patch_count = pre_usage.get("patch_count", 0)
    results = lw.flip_shadow_to_live(workspace)
    assert results[0].ok
    assert results[0].is_new_skill is False
    live_body = (skills_dir(workspace) / "patched" / "SKILL.md").read_text(encoding="utf-8")
    assert "patched content" in live_body
    assert "live content here" not in live_body
    # Telemetry got bumped:
    post_usage = UsageStore(skills_dir(workspace)).record("patched")
    assert post_usage.get("patch_count", 0) == pre_patch_count + 1


def test_flip_shadow_promotes_s2_support_file_into_live(workspace):
    _create_live_skill(workspace, "umbrella")
    lw.stage_support_file(workspace, "umbrella", "references/notes.md", "Notes\n")
    results = lw.flip_shadow_to_live(workspace)
    assert results[0].ok
    target = skills_dir(workspace) / "umbrella" / "references" / "notes.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "Notes\n"


def test_flip_shadow_promotes_combined_s1_and_s2_atomically(workspace):
    """S1 + S2 against the same skill is one flip — both files copy
    or neither does (per-skill atomicity, not per-file)."""
    _create_live_skill(workspace, "combo")
    lw.stage_skill_patch(workspace, "combo", "live content here", "patched body")
    lw.stage_support_file(workspace, "combo", "references/n.md", "ref content")
    results = lw.flip_shadow_to_live(workspace)
    assert results[0].ok
    body = (skills_dir(workspace) / "combo" / "SKILL.md").read_text(encoding="utf-8")
    assert "patched body" in body
    ref = (skills_dir(workspace) / "combo" / "references" / "n.md").read_text(encoding="utf-8")
    assert ref == "ref content"


def test_flip_shadow_only_skill_targets_one(workspace):
    lw.stage_new_skill(workspace, "alpha", _new_skill_body("alpha"))
    lw.stage_new_skill(workspace, "beta", _new_skill_body("beta"))
    results = lw.flip_shadow_to_live(workspace, only_skill="alpha")
    assert len(results) == 1
    assert results[0].skill_name == "alpha"
    # alpha is live:
    assert (skills_dir(workspace) / "alpha" / "SKILL.md").exists()
    # beta is still staged:
    assert (lw.shadow_skills_root(workspace) / "beta" / "SKILL.md").exists()
    assert not (skills_dir(workspace) / "beta").exists()


def test_flip_shadow_only_skill_unknown_returns_explicit_error(workspace):
    results = lw.flip_shadow_to_live(workspace, only_skill="nonexistent")
    assert len(results) == 1
    assert results[0].ok is False
    assert "no staged skill" in results[0].message.lower()


def test_flip_shadow_refuses_pinned_skill(workspace):
    """If a user pins a skill while its patch is staged, the flip
    refuses rather than silently overwriting a pinned skill."""
    _create_live_skill(workspace, "later-pinned")
    lw.stage_skill_patch(workspace, "later-pinned", "live content here", "patched")
    PinStore(skills_dir(workspace)).pin("later-pinned")
    results = lw.flip_shadow_to_live(workspace)
    assert len(results) == 1
    assert results[0].ok is False
    assert "pinned" in results[0].message.lower()
    # Live skill is still the original:
    body = (skills_dir(workspace) / "later-pinned" / "SKILL.md").read_text(encoding="utf-8")
    assert "live content here" in body


def test_flip_shadow_idempotent_after_clean_flip(workspace):
    """Re-running flip after a clean flip is a no-op."""
    lw.stage_new_skill(workspace, "fresh", _new_skill_body("fresh"))
    lw.flip_shadow_to_live(workspace)
    second = lw.flip_shadow_to_live(workspace)
    assert second == []


def test_flip_shadow_keeps_categories_clean(workspace):
    """After flipping a categorized S3, the empty category dir under
    .shadow/ is pruned so list_staged_skills returns []."""
    lw.stage_new_skill(workspace, "cat-skill", _new_skill_body("cat-skill"), category="bucket")
    lw.flip_shadow_to_live(workspace)
    assert lw.list_staged_skills(workspace) == []
    # The category dir under .shadow/ should also be gone:
    assert not (lw.shadow_skills_root(workspace) / "bucket").exists()
    # But the live category dir exists:
    assert (skills_dir(workspace) / "bucket" / "cat-skill" / "SKILL.md").exists()
