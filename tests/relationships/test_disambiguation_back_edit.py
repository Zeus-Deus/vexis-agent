"""v3b Day 3b: disambiguation back-edit on slug collision.

Covers:

- ADD with utterance qualifier + existing bare-slug WITH a YAML
  qualifier: live file gains the new qualified entry, existing
  bare-slug renamed to its qualified form, archive gains a
  ``## DISAMBIGUATED`` block with a ``[disambiguated ...]``
  provenance line.
- ADD with utterance qualifier + existing bare-slug WITHOUT a YAML
  qualifier: emits AMBIGUOUS reply, no back-edit attempted.
- ADD with utterance qualifier matching the existing bare-slug's
  qualifier: re-ADD on the bare slug, no rename.
- ADD with utterance qualifier + qualified-slug already exists:
  re-ADD on the qualified slug, no back-edit.
- Atomic-rename ordering for the back-edit: archive write
  precedes live rewrite.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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


def _stub_classifier(verdict: TriggerVerdict):
    async def _call(text, **kwargs):
        return verdict
    return _call


def _seed_bare_sarah_with_yaml_qualifier(workspace: Path) -> None:
    """A pre-3b RELATIONSHIPS.md shape: slug=sarah, qualifier in
    YAML. The back-edit case."""
    sarah = Person(
        slug="sarah",
        display_name="Sarah",
        relationship="friend",
        qualifier="friend",
        last_confirmed="2026-04-01",
        source_session="aaa11111",
        facts=(
            Fact(
                text="met in college",
                confirmed_date="2026-04-01",
                source_session_short="aaa11111",
                staged=False,
            ),
        ),
    )
    relationships_live_path(workspace).write_text(
        serialize_relationships_file([sarah], kind="live"),
        encoding="utf-8",
    )


def _seed_bare_sarah_no_qualifier(workspace: Path) -> None:
    """Pre-3b RELATIONSHIPS.md with no qualifier — the AMBIGUOUS-
    on-back-edit case."""
    sarah = Person(
        slug="sarah",
        display_name="Sarah",
        relationship="(unspecified)",
        qualifier=None,
        last_confirmed="2026-04-01",
        source_session="aaa11111",
        facts=(
            Fact(
                text="met somewhere",
                confirmed_date="2026-04-01",
                source_session_short="aaa11111",
                staged=False,
            ),
        ),
    )
    relationships_live_path(workspace).write_text(
        serialize_relationships_file([sarah], kind="live"),
        encoding="utf-8",
    )


# ---------------------------------------------------------------- back-edit


def test_back_edit_renames_existing_and_stages_new(workspace: Path):
    _seed_bare_sarah_with_yaml_qualifier(workspace)
    classifier_verdict = TriggerVerdict(
        verdict="ADD",
        person_name="Sarah",
        qualifier="coworker",
        facts=("uses Vim",),
        confidence=0.95,
        matched_pattern_id="ADD-1",
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "remember my coworker Sarah uses Vim",
            session_uuid="sess-back-edit",
            turn_index=2,
            chat_id=42,
        )
    )
    assert res.staged is True
    assert res.person_slug == "sarah-coworker"

    # Live: bare slug renamed to sarah-friend; new sarah-coworker staged in shadow.
    live_slugs = [p.slug for p in curator.store.list_live()]
    assert live_slugs == ["sarah-friend"]
    shadow_slugs = [p.slug for p in curator.store.list_shadow()]
    assert "sarah-coworker" in shadow_slugs

    # Archive: DISAMBIGUATED block present.
    archive_body = relationships_archive_path(workspace).read_text(encoding="utf-8")
    assert "## DISAMBIGUATED" in archive_body
    assert '[disambiguated' in archive_body
    assert '"sarah"' in archive_body
    assert '"sarah-friend"' in archive_body

    # Counter incremented.
    assert curator.counters["disambiguation_back_edit"] == 1


def test_back_edit_emits_ambiguous_when_existing_has_no_qualifier(
    workspace: Path,
):
    _seed_bare_sarah_no_qualifier(workspace)
    classifier_verdict = TriggerVerdict(
        verdict="ADD",
        person_name="Sarah",
        qualifier="coworker",
        facts=("uses Vim",),
        confidence=0.95,
        matched_pattern_id="ADD-1",
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "remember my coworker Sarah uses Vim",
            session_uuid="sess-amb",
            turn_index=2,
            chat_id=42,
        )
    )
    assert res.ambiguous is True
    assert res.reply_text and "Which Sarah" in res.reply_text

    # Live unchanged: no rename happened.
    live_slugs = [p.slug for p in curator.store.list_live()]
    assert live_slugs == ["sarah"]
    # No shadow entry (AMBIGUOUS path doesn't stage).
    assert curator.store.list_shadow() == []
    # No archive write (AMBIGUOUS doesn't archive).
    assert not relationships_archive_path(workspace).exists()
    # Counters: ambiguous_emitted=1, back_edit=0.
    assert curator.counters["ambiguous_emitted"] == 1
    assert curator.counters["disambiguation_back_edit"] == 0


def test_back_edit_skipped_when_qualifier_matches_existing(workspace: Path):
    """User says "remember my friend Sarah likes jazz" and existing
    bare-slug is already qualifier=friend → re-ADD on bare, no
    rename, no archive write."""
    _seed_bare_sarah_with_yaml_qualifier(workspace)  # qualifier=friend
    classifier_verdict = TriggerVerdict(
        verdict="ADD",
        person_name="Sarah",
        qualifier="friend",
        facts=("likes jazz",),
        confidence=0.95,
        matched_pattern_id="ADD-1",
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "remember my friend Sarah likes jazz",
            session_uuid="sess-rs",
            turn_index=2,
            chat_id=42,
        )
    )
    # Stage on the bare slug (re-ADD).
    assert res.staged is True
    assert res.person_slug == "sarah"
    # No back-edit, no archive.
    assert curator.counters["disambiguation_back_edit"] == 0
    assert not relationships_archive_path(workspace).exists()


def test_back_edit_skipped_when_qualified_slug_already_exists(workspace: Path):
    """sarah-coworker already lives. New "remember my coworker Sarah
    ..." turn re-ADDs on it; no back-edit on the never-existed bare slug."""
    sarah_coworker = Person(
        slug="sarah-coworker",
        display_name="Sarah",
        relationship="coworker",
        qualifier="coworker",
        last_confirmed="2026-04-15",
        source_session="bbb22222",
        facts=(),
    )
    relationships_live_path(workspace).write_text(
        serialize_relationships_file([sarah_coworker], kind="live"),
        encoding="utf-8",
    )
    classifier_verdict = TriggerVerdict(
        verdict="ADD",
        person_name="Sarah",
        qualifier="coworker",
        facts=("uses Vim",),
        confidence=0.95,
        matched_pattern_id="ADD-1",
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "remember my coworker Sarah uses Vim",
            session_uuid="sess-re",
            turn_index=2,
            chat_id=42,
        )
    )
    assert res.staged is True
    assert res.person_slug == "sarah-coworker"
    assert curator.counters["disambiguation_back_edit"] == 0


# ---------------------------------------------------------------- store-level


def test_store_rename_live_slug_archive_first_atomic_rename(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    _seed_bare_sarah_with_yaml_qualifier(workspace)
    store = RelationshipsStore(workspace)

    real_replace = Path.replace
    renames: list[tuple[str, str]] = []

    def tracked_replace(self: Path, target: Path) -> Path:
        renames.append((self.name, Path(target).name))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", tracked_replace)
    res = store.rename_live_slug(
        old_slug="sarah",
        new_slug="sarah-friend",
        new_qualifier="friend",
        disambiguated_date="2026-05-04",
    )
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
    assert renames.index(archive_renames[0]) < renames.index(live_renames[0])


def test_store_rename_live_slug_refuses_collision(workspace: Path):
    """rename_live_slug refuses if the new slug already exists in live."""
    sarah_friend = Person(
        slug="sarah-friend",
        display_name="Sarah",
        relationship="friend",
        qualifier="friend",
        last_confirmed="2026-04-01",
        source_session="aaa11111",
        facts=(),
    )
    sarah_coworker = Person(
        slug="sarah-coworker",
        display_name="Sarah",
        relationship="coworker",
        qualifier="coworker",
        last_confirmed="2026-04-15",
        source_session="bbb22222",
        facts=(),
    )
    relationships_live_path(workspace).write_text(
        serialize_relationships_file(
            [sarah_friend, sarah_coworker], kind="live",
        ),
        encoding="utf-8",
    )
    store = RelationshipsStore(workspace)
    res = store.rename_live_slug(
        old_slug="sarah-friend",
        new_slug="sarah-coworker",
        new_qualifier="coworker",
        disambiguated_date="2026-05-04",
    )
    assert res.ok is False
    assert "already exists" in res.message
