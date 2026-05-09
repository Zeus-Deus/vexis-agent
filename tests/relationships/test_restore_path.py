"""v3b Day 3b: ``/learning relationships-restore <slug>``.

Covers:

- Restore an archived slug: live gains the entry, archive loses
  the REMOVED block.
- Restore the most-recent block when multiple REMOVED blocks
  exist for the same slug (deleted-restored-deleted history).
- Restore-missing: friendly reply, no file mutations.
- Restore-collision: friendly reply, no file mutations.
- Atomic-rename ordering: archive write precedes live rewrite.
- ``/learning relationships-restore`` slash command wiring.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vexis_agent.core.relationships.consent import mint
from vexis_agent.core.relationships.curator import RelationshipsCurator
from vexis_agent.core.relationships.store import (
    Fact,
    Person,
    RelationshipsStore,
    relationships_archive_path,
    relationships_live_path,
    serialize_relationships_file,
)
from vexis_agent.core.relationships.triggers import TriggerVerdict


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _make_person(slug: str, fact_text: str = "likes jazz") -> Person:
    return Person(
        slug=slug,
        display_name=slug.split("-")[0].capitalize(),
        relationship="friend",
        qualifier=None,
        last_confirmed="2026-04-30",
        source_session="abc12345",
        facts=(
            Fact(
                text=fact_text,
                confirmed_date="2026-04-30",
                source_session_short="abc12345",
                staged=False,
            ),
        ),
    )


def _delete_via_store(workspace: Path, slug: str, *, removed_date: str) -> None:
    """Helper: seed live with one Person, then DELETE it through the
    store so the archive has a real REMOVED block to restore."""
    relationships_live_path(workspace).write_text(
        serialize_relationships_file([_make_person(slug)], kind="live"),
        encoding="utf-8",
    )
    store = RelationshipsStore(workspace)
    token = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="DELETE",
        person_slug=slug,
        facts=[],
        action="delete",
    )
    res = store.delete_live(slug, token=token, removed_date=removed_date)
    assert res.ok


# ---------------------------------------------------------------- store


def test_store_restore_happy_path(workspace: Path):
    _delete_via_store(workspace, "sarah", removed_date="2026-05-01")
    store = RelationshipsStore(workspace)
    # Live empty after the delete; archive has the REMOVED block.
    assert store.list_live() == []
    archive_before = relationships_archive_path(workspace).read_text(encoding="utf-8")
    assert "## REMOVED 2026-05-01" in archive_before
    assert "Sarah" in archive_before

    res = store.restore_from_archive("sarah")
    assert res.ok is True
    # Live regains Sarah.
    live = store.list_live()
    assert len(live) == 1
    assert live[0].slug == "sarah"
    assert any(f.text == "likes jazz" for f in live[0].facts)
    # Archive no longer has the REMOVED block for sarah.
    archive_after = relationships_archive_path(workspace).read_text(encoding="utf-8")
    assert "## REMOVED 2026-05-01" not in archive_after


def test_store_restore_picks_most_recent_block(workspace: Path):
    """Deleted-restored-deleted history: two REMOVED blocks for the
    same slug. Restore picks the most recent."""
    # First delete (older).
    _delete_via_store(workspace, "sarah", removed_date="2026-04-15")
    # Restore so live has Sarah again, with a fact identifying it
    # as the FIRST life.
    relationships_live_path(workspace).write_text(
        serialize_relationships_file(
            [_make_person("sarah", fact_text="loves jazz")], kind="live",
        ),
        encoding="utf-8",
    )
    # Second delete with a different date so we can tell which block
    # the restore reached for. Update the live entry's fact first
    # (so the second REMOVED block has a distinct fact text).
    relationships_live_path(workspace).write_text(
        serialize_relationships_file(
            [_make_person("sarah", fact_text="now likes classical")],
            kind="live",
        ),
        encoding="utf-8",
    )
    store = RelationshipsStore(workspace)
    token2 = mint(
        session_uuid="s2",
        turn_index=1,
        classifier_verdict="DELETE",
        person_slug="sarah",
        facts=[],
        action="delete",
    )
    store.delete_live("sarah", token=token2, removed_date="2026-05-01")
    archive_body = relationships_archive_path(workspace).read_text(encoding="utf-8")
    import re as _re
    real_removed_blocks = _re.findall(
        r"^## REMOVED \d{4}-\d{2}-\d{2}", archive_body, _re.MULTILINE,
    )
    assert len(real_removed_blocks) == 2

    res = store.restore_from_archive("sarah")
    assert res.ok is True
    live = store.list_live()
    # Restored fact should be the SECOND-life fact ("now likes classical").
    assert any(f.text == "now likes classical" for f in live[0].facts)
    # The older REMOVED block (from the first delete) still in archive.
    archive_after = relationships_archive_path(workspace).read_text(encoding="utf-8")
    real_removed_blocks_after = _re.findall(
        r"^## REMOVED \d{4}-\d{2}-\d{2}", archive_after, _re.MULTILINE,
    )
    assert len(real_removed_blocks_after) == 1
    # The remaining block is the older one (2026-04-15), not the
    # one we just restored (2026-05-01).
    assert "## REMOVED 2026-04-15" in archive_after
    assert "## REMOVED 2026-05-01" not in archive_after


def test_store_restore_no_archive_returns_not_ok(workspace: Path):
    store = RelationshipsStore(workspace)
    res = store.restore_from_archive("sarah")
    assert res.ok is False
    assert res.message == "no-archive"


def test_store_restore_no_removed_block_for_slug(workspace: Path):
    _delete_via_store(workspace, "marco", removed_date="2026-05-01")
    store = RelationshipsStore(workspace)
    res = store.restore_from_archive("sarah")
    assert res.ok is False
    assert res.message == "no-removed-block"


def test_store_restore_collision_with_live(workspace: Path):
    """Restore refuses when slug already exists in live."""
    _delete_via_store(workspace, "sarah", removed_date="2026-05-01")
    # Re-seed live with a different sarah so restore would collide.
    relationships_live_path(workspace).write_text(
        serialize_relationships_file([_make_person("sarah")], kind="live"),
        encoding="utf-8",
    )
    store = RelationshipsStore(workspace)
    res = store.restore_from_archive("sarah")
    assert res.ok is False
    assert res.message == "slug-already-live"


def test_store_restore_atomic_rename_archive_first(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    _delete_via_store(workspace, "sarah", removed_date="2026-05-01")
    store = RelationshipsStore(workspace)

    real_replace = Path.replace
    renames: list[tuple[str, str]] = []

    def tracked_replace(self: Path, target: Path) -> Path:
        renames.append((self.name, Path(target).name))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", tracked_replace)
    res = store.restore_from_archive("sarah")
    assert res.ok is True
    archive_renames = [
        (a, b) for a, b in renames
        if "RELATIONSHIPS-ARCHIVE.md" in b
    ]
    live_renames = [
        (a, b) for a, b in renames
        if b == "RELATIONSHIPS.md"
    ]
    assert len(archive_renames) == 1
    assert len(live_renames) == 1
    # Archive rewrite (block removal) precedes live rewrite (block add)
    # — same archive-first ordering as DELETE/SUPERSEDE.
    assert renames.index(archive_renames[0]) < renames.index(live_renames[0])


# ---------------------------------------------------------------- curator


def test_curator_restore_happy_path(workspace: Path):
    _delete_via_store(workspace, "sarah", removed_date="2026-05-01")
    curator = RelationshipsCurator(workspace=workspace)
    res = curator.restore("sarah")
    assert res.matched is True
    assert res.verdict == "RESTORE"
    assert res.reply_text == "Restored Sarah from archive."
    assert curator.counters["restore_executed"] == 1


def test_curator_restore_missing(workspace: Path):
    curator = RelationshipsCurator(workspace=workspace)
    res = curator.restore("sarah")
    assert res.matched is False
    assert res.reply_text == "Nothing to restore for sarah."
    assert curator.counters["restore_missing"] == 1


def test_curator_restore_collision(workspace: Path):
    relationships_live_path(workspace).write_text(
        serialize_relationships_file([_make_person("sarah")], kind="live"),
        encoding="utf-8",
    )
    curator = RelationshipsCurator(workspace=workspace)
    res = curator.restore("sarah")
    assert res.matched is False
    assert "already in your relationships" in res.reply_text
    assert curator.counters["restore_collision"] == 1


# ---------------------------------------------------------------- slash command


def test_relationships_restore_subcommand_dispatch(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """``/learning relationships-restore <slug>`` reaches the
    curator's ``.restore`` and returns its reply text."""
    from vexis_agent.core.learning_curator import LearningController
    from vexis_agent.core.relationships.curator import TurnLevelResult

    # Build a minimal LearningController via __new__ so we don't have
    # to wire the full daemon thread / config plumbing.
    controller = LearningController.__new__(LearningController)
    captured: list[str] = []

    class _StubCurator:
        @property
        def counters(self):
            return {}

        def restore(self, slug):
            captured.append(slug)
            return TurnLevelResult(
                staged=False,
                matched=True,
                reply_text=f"Restored {slug.capitalize()} from archive.",
                verdict="RESTORE",
            )

    controller._relationships_curator = _StubCurator()  # type: ignore[attr-defined]
    reply = asyncio.run(
        controller.handle_telegram(
            "relationships-restore", ["sarah"]
        )
    )
    assert reply == "Restored Sarah from archive."
    assert captured == ["sarah"]


def test_relationships_restore_subcommand_usage_when_no_args(
    workspace: Path,
):
    from vexis_agent.core.learning_curator import LearningController
    controller = LearningController.__new__(LearningController)
    controller._relationships_curator = None  # type: ignore[attr-defined]
    reply = asyncio.run(
        controller.handle_telegram("relationships-restore", [])
    )
    assert "Usage" in reply
    assert "<slug>" in reply
