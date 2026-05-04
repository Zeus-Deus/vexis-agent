"""v3c Day 4c: /learning relationships-digest slash command.

Renders an on-demand summary of pending candidates per research
doc §5.3. Empty queue → "No pending relationships." Non-empty
queue → ▲-prefixed list + a CTA pointing at approve / dashboard.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.learning_curator import LearningController
from core.relationships.curator import RelationshipsCurator


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _controller(curator: RelationshipsCurator) -> LearningController:
    controller = LearningController.__new__(LearningController)
    controller._relationships_curator = curator  # type: ignore[attr-defined]
    return controller


def _seed(curator, *, slug, display_name, qualifier, fact_text,
          session_uuid):
    curator.candidate_store.add_observation(
        slug=slug,
        display_name=display_name,
        qualifier=qualifier,
        fact_text=fact_text,
        session_uuid=session_uuid,
        turn_index=1,
    )


def test_digest_empty_queue(workspace: Path):
    curator = RelationshipsCurator(workspace=workspace)
    controller = _controller(curator)
    reply = asyncio.run(
        controller.handle_telegram("relationships-digest", [])
    )
    assert reply == "No pending relationships."


def test_digest_renders_three_states(workspace: Path):
    """Exercises the three eligibility states from research doc §5.3:
    eligible / below threshold / drop on next sweep (zero-fact)."""
    curator = RelationshipsCurator(workspace=workspace)
    # Strong cue → eligible after 1 session.
    _seed(curator, slug="mom", display_name="Mom", qualifier="mom",
          fact_text="loves classical", session_uuid="s1")
    # Soft cue, 1 session → below threshold.
    _seed(curator, slug="marco", display_name="Marco", qualifier=None,
          fact_text="uses Vim", session_uuid="s2")
    # Slug with NO active facts (force the zero-fact branch by
    # rejecting the only fact under it; slug stays open).
    _seed(curator, slug="emma", display_name="Emma", qualifier="emma",
          fact_text="single fact", session_uuid="s3")
    from core.relationships.consent import _fact_id
    fid = _fact_id("single fact")
    curator.candidate_store.mark_rejected("emma", fact_ids=[fid])

    controller = _controller(curator)
    reply = asyncio.run(
        controller.handle_telegram("relationships-digest", [])
    )
    assert "Pending relationships (3):" in reply
    assert "▲ mom (mom)" in reply
    assert "Eligible." in reply
    assert "▲ marco (?)" in reply
    assert "Below threshold." in reply
    assert "▲ emma" in reply
    assert "will drop on next sweep" in reply
    # CTA at the end.
    assert "/learning relationships-approve <slug>" in reply
    assert "dashboard" in reply.lower()


def test_digest_singular_session_grammar(workspace: Path):
    curator = RelationshipsCurator(workspace=workspace)
    _seed(curator, slug="mom", display_name="Mom", qualifier="mom",
          fact_text="x", session_uuid="s1")
    controller = _controller(curator)
    reply = asyncio.run(
        controller.handle_telegram("relationships-digest", [])
    )
    assert "1 session," in reply
    assert "1 fact." in reply
    assert "1 sessions" not in reply
    assert "1 facts" not in reply


def test_digest_excludes_approved_and_rejected(workspace: Path):
    curator = RelationshipsCurator(workspace=workspace)
    _seed(curator, slug="approved", display_name="Approved",
          qualifier=None, fact_text="x", session_uuid="s1")
    _seed(curator, slug="rejected", display_name="Rejected",
          qualifier=None, fact_text="y", session_uuid="s2")
    _seed(curator, slug="open", display_name="Open",
          qualifier="mom", fact_text="z", session_uuid="s3")
    curator.candidate_store.mark_approved("approved")
    curator.candidate_store.mark_rejected("rejected")
    controller = _controller(curator)
    reply = asyncio.run(
        controller.handle_telegram("relationships-digest", [])
    )
    # Only "open" survives the filter.
    assert "Pending relationships (1):" in reply
    assert "open" in reply
    assert "approved" not in reply.lower() or "approved" not in reply.split("Pending")[1].lower()
    assert "rejected" not in reply.split("\n", 1)[1].lower()
