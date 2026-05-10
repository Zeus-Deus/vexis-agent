"""Tests for the skills subsystem: discovery, telemetry, pinning,
manage actions, archive/restore, curator-blocked actions."""

from __future__ import annotations

from pathlib import Path


from vexis_agent.core.skills import (
    ARCHIVE_DIR_NAME,
    PinStore,
    STATE_ACTIVE,
    STATE_ARCHIVED,
    USAGE_JSON_NAME,
    UsageStore,
    archive_skill,
    archived_skill_names,
    build_skills_index_block,
    create_skill,
    delete_skill,
    discover_skills,
    edit_skill,
    list_active_reports,
    parse_skill_md,
    patch_skill,
    remove_supporting_file,
    restore_skill,
    validate_skill_md,
    validate_skill_name,
    view_skill,
    write_supporting_file,
)


SAMPLE_SKILL_MD = """\
---
name: alpha
description: Alpha skill for tests
---

# Body

Some markdown content.
"""


def _write_skill(root: Path, name: str, *, category: str | None = None,
                 description: str | None = None) -> Path:
    """Helper: create a skill on disk directly (bypasses create_skill so
    we can simulate pre-existing state)."""
    desc = description or f"{name} description"
    body = f"# {name} body\n\nContent.\n"
    content = f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}"
    parent = (root / category) if category else root
    parent.mkdir(parents=True, exist_ok=True)
    skill_dir = parent / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


# --------------------------------------------------------------------
# Frontmatter parsing
# --------------------------------------------------------------------


def test_parse_skill_md_returns_meta():
    meta = parse_skill_md(SAMPLE_SKILL_MD)
    assert meta is not None
    assert meta.name == "alpha"
    assert meta.description == "Alpha skill for tests"
    assert "Body" in meta.body


def test_parse_skill_md_rejects_missing_name():
    bad = SAMPLE_SKILL_MD.replace("name: alpha\n", "")
    assert parse_skill_md(bad) is None


def test_parse_skill_md_rejects_no_frontmatter():
    assert parse_skill_md("just a body") is None


def test_validate_skill_md_human_errors():
    assert "frontmatter" in (validate_skill_md("body only") or "")
    assert "name" in (validate_skill_md("---\ndescription: x\n---\n\nbody\n") or "")
    assert "description" in (validate_skill_md("---\nname: x\n---\n\nbody\n") or "")


def test_validate_skill_name():
    assert validate_skill_name("good-name") is None
    assert validate_skill_name("Bad-Name") is not None
    assert validate_skill_name("9-leading-digit") is not None
    assert validate_skill_name("") is not None


# --------------------------------------------------------------------
# Discovery + index
# --------------------------------------------------------------------


def test_discover_skips_archive_dir(tmp_path: Path):
    _write_skill(tmp_path, "active-one")
    archive = tmp_path / ARCHIVE_DIR_NAME / "old-one"
    archive.mkdir(parents=True)
    (archive / "SKILL.md").write_text(
        "---\nname: old-one\ndescription: archived\n---\n\nbody\n",
        encoding="utf-8",
    )
    metas = discover_skills(tmp_path)
    names = [m.name for m in metas]
    assert "active-one" in names
    assert "old-one" not in names


def test_discover_silently_skips_malformed(tmp_path: Path, caplog):
    skill_dir = tmp_path / "broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("not yaml", encoding="utf-8")
    _write_skill(tmp_path, "good")
    metas = discover_skills(tmp_path)
    assert [m.name for m in metas] == ["good"]


def test_index_block_lists_active_skills(tmp_path: Path):
    _write_skill(tmp_path, "first")
    _write_skill(tmp_path, "second")
    block = build_skills_index_block(tmp_path)
    assert "## Skills (mandatory)" in block
    assert "<available_skills>" in block
    assert "- first:" in block
    assert "- second:" in block


def test_index_empty_when_no_skills(tmp_path: Path):
    assert build_skills_index_block(tmp_path) == ""


# --------------------------------------------------------------------
# Manage actions
# --------------------------------------------------------------------


def test_create_skill_writes_file_and_inits_telemetry(tmp_path: Path):
    op = create_skill(tmp_path, "alpha", SAMPLE_SKILL_MD)
    assert op.ok
    assert (tmp_path / "alpha" / "SKILL.md").is_file()
    usage = UsageStore(tmp_path).load()
    assert "alpha" in usage
    assert usage["alpha"]["state"] == STATE_ACTIVE


def test_create_refuses_name_collision(tmp_path: Path):
    create_skill(tmp_path, "alpha", SAMPLE_SKILL_MD)
    op = create_skill(tmp_path, "alpha", SAMPLE_SKILL_MD)
    assert not op.ok
    assert "already exists" in op.message


def test_create_refuses_when_archived(tmp_path: Path):
    archive_root = tmp_path / ARCHIVE_DIR_NAME
    archive_root.mkdir()
    (archive_root / "alpha").mkdir()
    (archive_root / "alpha" / "SKILL.md").write_text(
        SAMPLE_SKILL_MD, encoding="utf-8"
    )
    op = create_skill(tmp_path, "alpha", SAMPLE_SKILL_MD)
    assert not op.ok
    assert "archive" in op.message


def test_view_bumps_telemetry(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    op = view_skill(tmp_path, "alpha")
    assert op.ok
    rec = UsageStore(tmp_path).record("alpha")
    assert rec["view_count"] == 1
    # Per the design contract: view == use.
    assert rec["use_count"] == 1
    assert rec["last_viewed_at"] is not None
    assert rec["last_used_at"] is not None


def test_view_supporting_file_whitelist(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    (tmp_path / "alpha" / "references").mkdir()
    (tmp_path / "alpha" / "references" / "doc.md").write_text("hello", encoding="utf-8")

    ok = view_skill(tmp_path, "alpha", "references/doc.md")
    assert ok.ok
    assert ok.extra is not None
    assert ok.extra["content"] == "hello"

    # Outside whitelist
    bad = view_skill(tmp_path, "alpha", "secrets/leak.txt")
    assert not bad.ok


def test_patch_unique_match(tmp_path: Path):
    _write_skill(tmp_path, "alpha", description="first")
    op = patch_skill(tmp_path, "alpha", "first", "second")
    assert op.ok
    content = (tmp_path / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert "description: second" in content


def test_patch_rejects_pinned(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    PinStore(tmp_path).pin("alpha")
    op = patch_skill(tmp_path, "alpha", "Content.", "Other.")
    assert not op.ok
    assert "pinned" in op.message


def test_patch_multi_match_requires_replace_all(tmp_path: Path):
    skill_dir = _write_skill(tmp_path, "alpha")
    p = skill_dir / "SKILL.md"
    p.write_text(
        "---\nname: alpha\ndescription: d\n---\n\nfoo\n\nfoo\n", encoding="utf-8"
    )
    out = patch_skill(tmp_path, "alpha", "foo", "bar")
    assert not out.ok
    assert "matched 2 times" in out.message
    out2 = patch_skill(tmp_path, "alpha", "foo", "bar", replace_all=True)
    assert out2.ok


def test_edit_full_rewrite(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    new = (
        "---\nname: alpha\ndescription: rewritten\n---\n\n# New body\n\nx\n"
    )
    op = edit_skill(tmp_path, "alpha", new)
    assert op.ok
    fresh = parse_skill_md((tmp_path / "alpha" / "SKILL.md").read_text(encoding="utf-8"))
    assert fresh is not None
    assert fresh.description == "rewritten"


def test_edit_rejects_name_change(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    new = (
        "---\nname: bravo\ndescription: same\n---\n\nbody\n"
    )
    op = edit_skill(tmp_path, "alpha", new)
    assert not op.ok


def test_delete_removes_skill_and_telemetry(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    UsageStore(tmp_path).initialize("alpha")
    op = delete_skill(tmp_path, "alpha")
    assert op.ok
    assert not (tmp_path / "alpha").exists()
    assert "alpha" not in UsageStore(tmp_path).load()


def test_delete_refuses_pinned(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    PinStore(tmp_path).pin("alpha")
    op = delete_skill(tmp_path, "alpha")
    assert not op.ok
    assert (tmp_path / "alpha").exists()


def test_write_supporting_file_whitelist(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    ok = write_supporting_file(
        tmp_path, "alpha", "references/notes.md", "# hi\n"
    )
    assert ok.ok
    assert (tmp_path / "alpha" / "references" / "notes.md").is_file()

    bad = write_supporting_file(
        tmp_path, "alpha", "secrets/key.txt", "secret"
    )
    assert not bad.ok


def test_remove_supporting_file(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    write_supporting_file(tmp_path, "alpha", "templates/x.txt", "y")
    op = remove_supporting_file(tmp_path, "alpha", "templates/x.txt")
    assert op.ok
    assert not (tmp_path / "alpha" / "templates" / "x.txt").exists()


# --------------------------------------------------------------------
# Pinning
# --------------------------------------------------------------------


def test_pin_unpin_round_trip(tmp_path: Path):
    pins = PinStore(tmp_path)
    assert not pins.is_pinned("alpha")
    assert pins.pin("alpha") is True
    assert pins.is_pinned("alpha")
    assert pins.pin("alpha") is False  # idempotent
    assert pins.unpin("alpha") is True
    assert pins.unpin("alpha") is False


# --------------------------------------------------------------------
# Archive / restore
# --------------------------------------------------------------------


def test_archive_moves_dir(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    op = archive_skill(tmp_path, "alpha")
    assert op.ok
    assert not (tmp_path / "alpha").exists()
    assert (tmp_path / ARCHIVE_DIR_NAME / "alpha").is_dir()
    rec = UsageStore(tmp_path).record("alpha")
    assert rec["state"] == STATE_ARCHIVED
    assert rec["archived_at"] is not None


def test_archive_collision_appends_timestamp(tmp_path: Path):
    archive_root = tmp_path / ARCHIVE_DIR_NAME
    archive_root.mkdir()
    (archive_root / "alpha").mkdir()
    (archive_root / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: old\n---\n\nbody\n", encoding="utf-8"
    )
    _write_skill(tmp_path, "alpha")
    op = archive_skill(tmp_path, "alpha")
    assert op.ok
    entries = sorted(p.name for p in archive_root.iterdir())
    assert "alpha" in entries
    # The new archive entry has a timestamp suffix.
    assert any(e.startswith("alpha-") for e in entries)


def test_restore_round_trip(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    archive_skill(tmp_path, "alpha")
    archived = archived_skill_names(tmp_path)
    assert "alpha" in archived
    op = restore_skill(tmp_path, "alpha")
    assert op.ok
    assert (tmp_path / "alpha").exists()
    rec = UsageStore(tmp_path).record("alpha")
    assert rec["state"] == STATE_ACTIVE
    assert rec["archived_at"] is None


def test_restore_refuses_when_active_exists(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    archive_skill(tmp_path, "alpha")
    # Create a new skill at the same name in the active tree
    _write_skill(tmp_path, "alpha")
    op = restore_skill(tmp_path, "alpha")
    assert not op.ok


# --------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------


def test_list_active_reports_includes_pinning_and_state(tmp_path: Path):
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    PinStore(tmp_path).pin("beta")
    UsageStore(tmp_path).set_state("alpha", "stale")

    reports = list_active_reports(tmp_path)
    by_name = {r.name: r for r in reports}
    assert by_name["alpha"].state == "stale"
    assert by_name["beta"].pinned is True
    assert by_name["alpha"].pinned is False


def test_usage_json_does_not_lock(tmp_path: Path):
    """Telemetry is statistically-important, not correctness-critical;
    no lockfile should be created next to .usage.json."""
    UsageStore(tmp_path).initialize("alpha")
    assert (tmp_path / USAGE_JSON_NAME).is_file()
    # No sidecar lock
    assert not list(tmp_path.glob(f"{USAGE_JSON_NAME}.lock"))
