"""v3c Day 4a: candidate approval flow.

Covers ``RelationshipsCurator.approve_candidate`` /
``reject_candidate`` and ``RelationshipsStore.add_live`` and the
``/learning relationships-pending|approve|reject`` slash commands.

Tests:

- whole-person approve via slash command: token minted, live
  written, candidate cleared from queue.
- approve with sensitive content blocks at promotion-time scan.
- approve with qualifier collision (existing has YAML qualifier):
  v3b back-edit fires, both entries land correctly.
- approve with qualifier collision (existing has NO YAML
  qualifier): missing_existing_qualifier blocked state, slash
  reply tells user to resolve via dashboard.
- reject whole slug: tombstones slug, future facts drop silently.
- approve token wrong action: rejected at verify.
- /learning relationships-pending slash output shape.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vexis_agent.core.relationships.candidate_store import RelationshipsCandidateStore
from vexis_agent.core.relationships.consent import (
    ConsentError,
    mint,
    verify_for_promotion,
)
from vexis_agent.core.relationships.curator import (
    ApproveCandidateResult,
    RelationshipsCurator,
)
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


def _seed_candidate(curator, *, slug, display_name, qualifier, fact_text):
    curator.candidate_store.add_observation(
        slug=slug,
        display_name=display_name,
        qualifier=qualifier,
        fact_text=fact_text,
        session_uuid=f"sess-{slug}",
        turn_index=1,
    )


# ---------------------------------------------------------------- token shape


def test_approve_token_carries_action_approve():
    t = mint(
        session_uuid="approve",
        turn_index=1,
        classifier_verdict="ADD",
        person_slug="sarah",
        facts=["likes jazz"],
        action="approve",
    )
    assert t.action == "approve"


def test_approve_token_requires_facts():
    with pytest.raises(ConsentError, match="requires at least one fact"):
        mint(
            session_uuid="approve",
            turn_index=1,
            classifier_verdict="ADD",
            person_slug="sarah",
            facts=[],
            action="approve",
        )


def test_add_call_site_rejects_approve_token():
    """A token minted with action="approve" cannot satisfy the
    add path's verifier."""
    t = mint(
        session_uuid="approve", turn_index=1, classifier_verdict="ADD",
        person_slug="sarah", facts=["x"], action="approve",
    )
    with pytest.raises(ConsentError, match="action mismatch"):
        verify_for_promotion(t, person_slug="sarah", facts=["x"])  # default "add"


def test_approve_call_site_rejects_add_token():
    t = mint(
        session_uuid="s", turn_index=1, classifier_verdict="ADD",
        person_slug="sarah", facts=["x"], action="add",
    )
    with pytest.raises(ConsentError, match="action mismatch"):
        verify_for_promotion(
            t, person_slug="sarah", facts=["x"],
            expected_action="approve",
        )


# ---------------------------------------------------------------- whole-person approve


def test_approve_writes_live_and_clears_queue(workspace: Path):
    curator = RelationshipsCurator(workspace=workspace)
    _seed_candidate(
        curator, slug="sarah", display_name="Sarah",
        qualifier="coworker", fact_text="tech lead",
    )
    res = curator.approve_candidate("sarah")
    assert res.ok is True
    assert "Approved 1 fact for Sarah" in res.reply_text
    # Live now has Sarah.
    live = curator.store.list_live()
    assert len(live) == 1
    assert any(f.text == "tech lead" for f in live[0].facts)
    # Queue cleared (all facts approved → slug deleted).
    assert curator.candidate_store.get("sarah") is None
    assert curator.counters["candidates_approved"] == 1


def test_approve_with_sensitive_content_blocks(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
):
    curator = RelationshipsCurator(workspace=workspace)
    _seed_candidate(
        curator, slug="sarah", display_name="Sarah",
        qualifier="friend", fact_text="meets a real test fact",
    )
    # Force the sensitive scanner to fire at approve time.
    # `add_live` lazy-imports `_scan_lesson_for_sensitive_content`
    # from `core.learning_review`, so we patch at that module path.
    from vexis_agent.core import learning_review as lr_module

    def fake_scan(text, scope, *, target_file):
        return f"medical:{target_file}"

    monkeypatch.setattr(
        lr_module,
        "_scan_lesson_for_sensitive_content",
        fake_scan,
    )
    res = curator.approve_candidate("sarah")
    assert res.ok is False
    assert res.blocked_by == "sensitive-pattern"
    assert "can't store" in res.reply_text
    # Live unchanged; queue still has the candidate.
    assert curator.store.list_live() == []
    assert curator.candidate_store.get("sarah") is not None
    assert curator.counters["approve_blocked_sensitive"] == 1


def test_approve_with_qualifier_collision_back_edits(workspace: Path):
    """Existing live entry has a YAML qualifier — approve with a
    DIFFERENT qualifier should back-edit the bare slug to its
    qualified form, then add the new entry."""
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
    curator = RelationshipsCurator(workspace=workspace)
    _seed_candidate(
        curator, slug="sarah", display_name="Sarah",
        qualifier="coworker", fact_text="tech lead",
    )
    res = curator.approve_candidate("sarah", qualifier="coworker")
    assert res.ok is True
    live = curator.store.list_live()
    slugs = sorted(p.slug for p in live)
    # bare "sarah" renamed to "sarah-friend"; new "sarah-coworker"
    # added.
    assert slugs == ["sarah-coworker", "sarah-friend"]
    # Disambiguation block in archive.
    archive_body = relationships_archive_path(workspace).read_text(encoding="utf-8")
    assert "## DISAMBIGUATED" in archive_body


def test_approve_with_qualifier_collision_missing_existing_qualifier(workspace: Path):
    """Existing live entry has NO YAML qualifier. Approve returns
    blocked_by="missing_existing_qualifier"; slash reply tells the
    user to resolve via dashboard."""
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
    curator = RelationshipsCurator(workspace=workspace)
    _seed_candidate(
        curator, slug="sarah", display_name="Sarah",
        qualifier="coworker", fact_text="tech lead",
    )
    res = curator.approve_candidate("sarah", qualifier="coworker")
    assert res.ok is False
    assert res.blocked_by == "missing_existing_qualifier"
    assert "Resolve via dashboard" in res.reply_text
    assert curator.counters["approve_blocked_missing_qualifier"] == 1
    # Live unchanged.
    live_slugs = sorted(p.slug for p in curator.store.list_live())
    assert live_slugs == ["sarah"]
    # Candidate still in queue.
    assert curator.candidate_store.get("sarah") is not None


def test_reject_whole_slug_tombstones(workspace: Path):
    curator = RelationshipsCurator(workspace=workspace)
    _seed_candidate(
        curator, slug="marco", display_name="Marco",
        qualifier=None, fact_text="A",
    )
    res = curator.reject_candidate("marco")
    assert res.ok is True
    candidate = curator.candidate_store.get("marco")
    assert candidate.rejected_at is not None
    # Future fact for marco drops silently.
    second = curator.candidate_store.add_observation(
        slug="marco", display_name="Marco", qualifier=None,
        fact_text="B",
        session_uuid="sess2", turn_index=1,
    )
    assert second is None
    assert curator.counters["candidates_rejected"] == 1


def test_approve_missing_slug_returns_friendly(workspace: Path):
    curator = RelationshipsCurator(workspace=workspace)
    res = curator.approve_candidate("nobody")
    assert res.ok is False
    assert res.blocked_by == "not-in-queue"
    assert "No candidate in the queue" in res.reply_text


def test_approve_rejected_slug_refused(workspace: Path):
    curator = RelationshipsCurator(workspace=workspace)
    _seed_candidate(
        curator, slug="marco", display_name="Marco",
        qualifier=None, fact_text="A",
    )
    curator.reject_candidate("marco")
    res = curator.approve_candidate("marco")
    assert res.ok is False
    assert res.blocked_by == "slug-rejected"


# ---------------------------------------------------------------- slash command


def test_slash_pending_lists_eligible_and_below(workspace: Path):
    curator = RelationshipsCurator(workspace=workspace)
    _seed_candidate(
        curator, slug="mom", display_name="Mom",
        qualifier="mom", fact_text="loves classical",
    )
    _seed_candidate(
        curator, slug="marco", display_name="Marco",
        qualifier=None, fact_text="vim",
    )
    text = _render_pending(curator)
    assert "Pending relationships (2):" in text
    assert "mom" in text and "eligible" in text
    assert "marco" in text and "below threshold" in text


def test_slash_dispatch_relationships_pending(workspace: Path):
    """``/learning relationships-pending`` reaches
    LearningController and renders the queue."""
    from vexis_agent.core.learning_curator import LearningController
    controller = LearningController.__new__(LearningController)
    curator = RelationshipsCurator(workspace=workspace)
    _seed_candidate(
        curator, slug="dad", display_name="Dad",
        qualifier="dad", fact_text="loves jazz",
    )
    controller._relationships_curator = curator  # type: ignore[attr-defined]
    reply = asyncio.run(
        controller.handle_telegram("relationships-pending", [])
    )
    assert "Pending relationships (1):" in reply
    assert "dad" in reply


def test_slash_dispatch_relationships_approve(workspace: Path):
    from vexis_agent.core.learning_curator import LearningController
    controller = LearningController.__new__(LearningController)
    curator = RelationshipsCurator(workspace=workspace)
    _seed_candidate(
        curator, slug="mom", display_name="Mom",
        qualifier="mom", fact_text="loves classical",
    )
    controller._relationships_curator = curator  # type: ignore[attr-defined]
    reply = asyncio.run(
        controller.handle_telegram("relationships-approve", ["mom"])
    )
    assert "Approved" in reply
    assert "Mom" in reply


def test_slash_dispatch_relationships_reject(workspace: Path):
    from vexis_agent.core.learning_curator import LearningController
    controller = LearningController.__new__(LearningController)
    curator = RelationshipsCurator(workspace=workspace)
    _seed_candidate(
        curator, slug="marco", display_name="Marco",
        qualifier=None, fact_text="vim",
    )
    controller._relationships_curator = curator  # type: ignore[attr-defined]
    reply = asyncio.run(
        controller.handle_telegram("relationships-reject", ["marco"])
    )
    assert "Rejected" in reply
    assert "Marco" in reply


def _render_pending(curator: RelationshipsCurator) -> str:
    """Inline copy of LearningController._relationships_pending_text
    so we can test rendering without a full controller fixture."""
    views = curator.list_pending_candidates()
    if not views:
        return "No pending relationships."
    lines = [f"Pending relationships ({len(views)}):"]
    for v in views:
        qual = v.qualifier or "?"
        sess = v.session_count
        facts = v.fact_count
        if v.eligible:
            state = "eligible"
        elif facts == 0:
            state = "drop on next sweep"
        else:
            state = "below threshold"
        lines.append(
            f"  {v.slug} ({qual}, {sess} sess, {facts} fact"
            f"{'s' if facts != 1 else ''}) — {state}"
        )
    return "\n".join(lines)
