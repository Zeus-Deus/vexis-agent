"""v3b Day 3a: synchronous DELETE flow.

Covers:

- DELETE on existing slug: archive gains the block under a
  ``## REMOVED <date>`` header, live loses it.
- DELETE on missing slug: returns ``matched=False``, no file changes,
  reply text matches.
- DELETE token has ``action="delete"``; ADD call site refuses it.
- ADD token has ``action="add"``; DELETE call site refuses it.
- DELETE never invokes the coherence judge (no claim to ground).
- DELETE never invokes the sensitive-pattern scanner (no new content).
- Atomic rename: live and archive use ``.tmp`` + atomic ``replace``.
- Archive header is initialised on the first deletion.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.coherence_judge import CoherenceVerdict
from core.relationships.consent import (
    ConsentError,
    mint,
    verify_for_promotion,
)
from core.relationships.curator import RelationshipsCurator
from core.relationships.store import (
    Fact,
    Person,
    RelationshipsStore,
    relationships_archive_path,
    relationships_live_path,
    serialize_relationships_file,
)
from core.relationships.triggers import TriggerVerdict


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


def _seed_live_with_sarah(workspace: Path) -> None:
    """Write a RELATIONSHIPS.md with one Person (sarah) so DELETE has
    something to archive."""
    sarah = Person(
        slug="sarah",
        display_name="Sarah",
        relationship="coworker",
        qualifier=None,
        last_confirmed="2026-04-30",
        source_session="abc12345",
        facts=(
            Fact(
                text="likes jazz",
                confirmed_date="2026-04-30",
                source_session_short="abc12345",
                staged=False,
            ),
        ),
    )
    relationships_live_path(workspace).write_text(
        serialize_relationships_file([sarah], kind="live"),
        encoding="utf-8",
    )


# ---------------------------------------------------------------- consent


def test_add_token_carries_action_add():
    t = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="ADD",
        person_slug="sarah",
        facts=["likes jazz"],
    )
    assert t.action == "add"
    assert t.fact_ids != ()


def test_delete_token_carries_action_delete_and_empty_fact_ids():
    t = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="DELETE",
        person_slug="sarah",
        facts=[],
        action="delete",
    )
    assert t.action == "delete"
    assert t.fact_ids == ()


def test_add_call_site_rejects_delete_token():
    delete_t = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="DELETE",
        person_slug="sarah",
        facts=[],
        action="delete",
    )
    with pytest.raises(ConsentError, match="action mismatch"):
        verify_for_promotion(
            delete_t, person_slug="sarah", facts=["likes jazz"],
        )


def test_delete_call_site_rejects_add_token():
    add_t = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="ADD",
        person_slug="sarah",
        facts=["likes jazz"],
    )
    with pytest.raises(ConsentError, match="action mismatch"):
        verify_for_promotion(
            add_t, person_slug="sarah", facts=[], expected_action="delete",
        )


def test_delete_token_repr_does_not_leak_facts():
    t = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="DELETE",
        person_slug="sarah",
        facts=[],
        action="delete",
    )
    s = repr(t)
    assert "delete" in s
    assert "n_facts=0" in s


# ---------------------------------------------------------------- store


def test_store_delete_live_archives_then_removes(workspace: Path):
    _seed_live_with_sarah(workspace)
    store = RelationshipsStore(workspace)
    token = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="DELETE",
        person_slug="sarah",
        facts=[],
        action="delete",
    )
    res = store.delete_live(
        "sarah", token=token, removed_date="2026-05-04",
    )
    assert res.ok is True
    # Live file no longer mentions sarah.
    live = store.list_live()
    assert live == []
    # Archive file now contains a REMOVED block with the original H2.
    archive_path = relationships_archive_path(workspace)
    assert archive_path.exists()
    body = archive_path.read_text(encoding="utf-8")
    assert "# RELATIONSHIPS-ARCHIVE.md" in body
    assert "## REMOVED 2026-05-04" in body
    assert "## Sarah" in body
    assert "likes jazz" in body


def test_store_delete_live_no_match_returns_not_ok(workspace: Path):
    store = RelationshipsStore(workspace)
    token = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="DELETE",
        person_slug="not-there",
        facts=[],
        action="delete",
    )
    res = store.delete_live(
        "not-there", token=token, removed_date="2026-05-04",
    )
    assert res.ok is False
    # Archive file is NOT created on a no-op delete.
    assert not relationships_archive_path(workspace).exists()


def test_store_delete_live_rejects_add_token(workspace: Path):
    _seed_live_with_sarah(workspace)
    store = RelationshipsStore(workspace)
    add_token = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="ADD",
        person_slug="sarah",
        facts=["x"],
        action="add",
    )
    with pytest.raises(ConsentError, match="action mismatch"):
        store.delete_live(
            "sarah", token=add_token, removed_date="2026-05-04",
        )


def test_store_delete_live_uses_atomic_rename(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    """Both live + archive writes must go via .tmp + replace.

    We patch ``Path.replace`` to record every rename operation; any
    direct ``Path.write_text`` to the live or archive path bypassing
    the tmp would cause this assertion to fail.
    """
    _seed_live_with_sarah(workspace)
    store = RelationshipsStore(workspace)
    token = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="DELETE",
        person_slug="sarah",
        facts=[],
        action="delete",
    )

    real_replace = Path.replace
    renames: list[tuple[str, str]] = []

    def tracked_replace(self: Path, target: Path) -> Path:
        renames.append((self.name, Path(target).name))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", tracked_replace)

    res = store.delete_live(
        "sarah", token=token, removed_date="2026-05-04",
    )
    assert res.ok is True
    # We expect exactly two renames: archive .tmp → archive, live .tmp → live.
    archive_renames = [
        (a, b) for a, b in renames
        if "RELATIONSHIPS-ARCHIVE.md" in b
    ]
    live_renames = [
        (a, b) for a, b in renames
        if b == "RELATIONSHIPS.md"
    ]
    assert len(archive_renames) == 1, renames
    assert len(live_renames) == 1, renames
    assert archive_renames[0][0].endswith(".tmp")
    assert live_renames[0][0].endswith(".tmp")
    # Archive write happens BEFORE live rewrite — that's the
    # crash-recovery contract (slug recoverable from archive even
    # if the live rewrite never lands).
    assert renames.index(archive_renames[0]) < renames.index(live_renames[0])


# ---------------------------------------------------------------- curator


def test_curator_delete_executes_synchronously(workspace: Path):
    _seed_live_with_sarah(workspace)
    classifier_verdict = TriggerVerdict(
        verdict="DELETE",
        person_name="Sarah",
        confidence=0.95,
        matched_pattern_id="DEL-1",
    )
    judge = MagicMock()
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
        coherence_judge=judge,
    )
    res = asyncio.run(
        curator.process_user_turn(
            "forget Sarah",
            session_uuid="sess-d",
            turn_index=1,
        )
    )
    assert res.deleted is True
    assert res.matched is True
    assert res.verdict == "DELETE"
    assert res.person_slug == "sarah"
    assert res.reply_text is not None
    assert "Forgot what I had on Sarah" in res.reply_text
    assert "Archived for restore" in res.reply_text

    # Live + archive states reflect the delete.
    assert curator.store.list_live() == []
    archive_body = relationships_archive_path(workspace).read_text(encoding="utf-8")
    assert "## REMOVED" in archive_body
    assert "Sarah" in archive_body

    # No tokens left in the registry — DELETE consumed its token.
    assert len(curator.tokens) == 0

    # Counters: delete_executed bumped, delete_missing not.
    assert curator.counters["delete_executed"] == 1
    assert curator.counters["delete_missing"] == 0

    # DELETE never fires the coherence judge.
    assert judge.call_count == 0


def test_curator_delete_missing_returns_friendly_no_op(workspace: Path):
    # No live entry seeded.
    classifier_verdict = TriggerVerdict(
        verdict="DELETE",
        person_name="Sarah",
        confidence=0.95,
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "forget Sarah",
            session_uuid="sess-d",
            turn_index=1,
        )
    )
    assert res.deleted is False
    assert res.matched is False
    assert res.verdict == "DELETE"
    assert res.reply_text == "I don't have anything on Sarah to forget."

    # No archive file (no-op).
    assert not relationships_archive_path(workspace).exists()
    # Counters: delete_missing bumped, no token minted.
    assert curator.counters["delete_executed"] == 0
    assert curator.counters["delete_missing"] == 1
    assert len(curator.tokens) == 0


def test_curator_delete_does_not_invoke_sensitive_scanner(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    """DELETE removes information; it can't introduce new sensitive
    content. The scanner from learning_review must not be called."""
    _seed_live_with_sarah(workspace)

    # Replace the scanner with a counter so any call would be recorded.
    from core.relationships import curator as curator_module
    sentinel_calls: list[tuple] = []

    def fake_scan(*args, **kwargs):
        sentinel_calls.append(args)
        return None

    monkeypatch.setattr(
        curator_module, "_scan_lesson_for_sensitive_content", fake_scan,
    )

    classifier_verdict = TriggerVerdict(
        verdict="DELETE", person_name="Sarah", confidence=0.95,
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    asyncio.run(
        curator.process_user_turn(
            "forget Sarah",
            session_uuid="sess-d",
            turn_index=1,
        )
    )
    assert sentinel_calls == []


def test_curator_delete_token_is_consumed(workspace: Path):
    """Successful DELETE leaves no residual token in the registry —
    the token was minted, used, and consumed in one synchronous call."""
    _seed_live_with_sarah(workspace)
    classifier_verdict = TriggerVerdict(
        verdict="DELETE", person_name="Sarah", confidence=0.95,
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    asyncio.run(
        curator.process_user_turn(
            "forget Sarah",
            session_uuid="sess-d",
            turn_index=42,
        )
    )
    assert curator.tokens.get(
        session_uuid="sess-d", turn_index=42, person_slug="sarah",
    ) is None
