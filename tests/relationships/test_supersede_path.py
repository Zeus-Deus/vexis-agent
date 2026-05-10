"""v3b Day 3b: synchronous SUPERSEDE flow.

Covers:

- SUPERSEDE on existing slug: archive gains a ``## SUPERSEDED <date>``
  block with the OLD facts annotated ``[superseded ...]``; live
  entry has the new facts as ``[confirmed ...]`` pins under the
  same H2 + YAML.
- SUPERSEDE on missing slug: friendly no-op.
- SUPERSEDE blocked by sensitive scanner: no live mutation, no
  token consumed.
- SUPERSEDE blocked by coherence INCOHERENT: no live mutation.
- SUPERSEDE token has ``action="supersede"``; ADD/DELETE call
  sites refuse it; SUPERSEDE call sites refuse ADD/DELETE tokens.
- Atomic-rename ordering: archive rename precedes live rewrite.
- Counter wiring: supersede_executed / _missing / _blocked_*.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from vexis_agent.core.coherence_judge import CoherenceVerdict
from vexis_agent.core.relationships.consent import (
    ConsentError,
    mint,
    verify_for_promotion,
)
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
from vexis_agent.core.transcripts import claude_session_jsonl_dir


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
            Fact(
                text="prefers async standups",
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


def _stage_source_jsonl(
    workspace: Path, session_uuid: str, user_text: str
) -> None:
    pdir = claude_session_jsonl_dir(workspace)
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{session_uuid}.jsonl"
    line = json.dumps({
        "type": "user",
        "uuid": "u-1",
        "timestamp": "2026-05-04T12:00:00Z",
        "message": {"role": "user", "content": user_text},
    })
    path.write_text(line + "\n", encoding="utf-8")


# ---------------------------------------------------------------- consent


def test_supersede_token_carries_action_supersede():
    t = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="SUPERSEDE",
        person_slug="sarah",
        facts=["loves classical now"],
        action="supersede",
    )
    assert t.action == "supersede"
    assert t.fact_ids != ()


def test_supersede_token_requires_facts():
    with pytest.raises(ConsentError, match="requires at least one fact"):
        mint(
            session_uuid="s",
            turn_index=1,
            classifier_verdict="SUPERSEDE",
            person_slug="sarah",
            facts=[],
            action="supersede",
        )


def test_supersede_call_site_rejects_add_token():
    add_t = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="ADD",
        person_slug="sarah",
        facts=["x"],
    )
    with pytest.raises(ConsentError, match="action mismatch"):
        verify_for_promotion(
            add_t, person_slug="sarah", facts=["x"],
            expected_action="supersede",
        )


def test_supersede_call_site_rejects_delete_token():
    del_t = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="DELETE",
        person_slug="sarah",
        facts=[],
        action="delete",
    )
    with pytest.raises(ConsentError, match="action mismatch"):
        verify_for_promotion(
            del_t, person_slug="sarah", facts=["x"],
            expected_action="supersede",
        )


def test_add_call_site_rejects_supersede_token():
    sup_t = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="SUPERSEDE",
        person_slug="sarah",
        facts=["x"],
        action="supersede",
    )
    with pytest.raises(ConsentError, match="action mismatch"):
        verify_for_promotion(sup_t, person_slug="sarah", facts=["x"])


# ---------------------------------------------------------------- store


def test_store_supersede_live_archives_then_rewrites(workspace: Path):
    _seed_live_with_sarah(workspace)
    store = RelationshipsStore(workspace)
    token = mint(
        session_uuid="new-sess-uuid-1234567890",
        turn_index=2,
        classifier_verdict="SUPERSEDE",
        person_slug="sarah",
        facts=["loves classical now", "moved to Berlin"],
        action="supersede",
    )
    res = store.supersede_live(
        "sarah",
        token=token,
        new_facts=["loves classical now", "moved to Berlin"],
        new_session_uuid="new-sess-uuid-1234567890",
        new_session_short="new-sess",
        superseded_date="2026-05-04",
    )
    assert res.ok is True
    # Live file: same H2, new facts pinned with [confirmed].
    live = store.list_live()
    assert len(live) == 1
    assert live[0].slug == "sarah"
    assert live[0].last_confirmed == "2026-05-04"
    assert live[0].source_session == "new-sess-uuid-1234567890"
    fact_texts = [f.text for f in live[0].facts]
    assert "loves classical now" in fact_texts
    assert "moved to Berlin" in fact_texts
    # Old facts gone from live.
    assert "likes jazz" not in fact_texts

    # Archive: SUPERSEDED block with the OLD facts annotated.
    archive_body = relationships_archive_path(workspace).read_text(encoding="utf-8")
    assert "## SUPERSEDED 2026-05-04" in archive_body
    assert "likes jazz" in archive_body
    assert "[superseded 2026-05-04 by sess:new-sess]" in archive_body


def test_store_supersede_live_no_match_returns_not_ok(workspace: Path):
    store = RelationshipsStore(workspace)
    token = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="SUPERSEDE",
        person_slug="missing",
        facts=["x"],
        action="supersede",
    )
    res = store.supersede_live(
        "missing",
        token=token,
        new_facts=["x"],
        new_session_uuid="s",
        new_session_short="s",
        superseded_date="2026-05-04",
    )
    assert res.ok is False
    assert not relationships_archive_path(workspace).exists()


def test_store_supersede_uses_archive_first_atomic_rename(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    _seed_live_with_sarah(workspace)
    store = RelationshipsStore(workspace)
    token = mint(
        session_uuid="s",
        turn_index=1,
        classifier_verdict="SUPERSEDE",
        person_slug="sarah",
        facts=["new fact"],
        action="supersede",
    )
    real_replace = Path.replace
    renames: list[tuple[str, str]] = []

    def tracked_replace(self: Path, target: Path) -> Path:
        renames.append((self.name, Path(target).name))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", tracked_replace)
    res = store.supersede_live(
        "sarah",
        token=token,
        new_facts=["new fact"],
        new_session_uuid="s",
        new_session_short="s",
        superseded_date="2026-05-04",
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


# ---------------------------------------------------------------- curator


def test_curator_supersede_executes_synchronously(workspace: Path):
    _seed_live_with_sarah(workspace)
    _stage_source_jsonl(
        workspace, "sess-sup", "actually Sarah loves classical now",
    )
    classifier_verdict = TriggerVerdict(
        verdict="SUPERSEDE",
        person_name="Sarah",
        facts=("loves classical now",),
        confidence=0.95,
        matched_pattern_id="SUP-1",
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
        coherence_judge=lambda *a, **kw: CoherenceVerdict.coherent(),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "actually Sarah loves classical now",
            session_uuid="sess-sup",
            turn_index=1,
        )
    )
    assert res.superseded is True
    assert res.matched is True
    assert res.verdict == "SUPERSEDE"
    assert res.person_slug == "sarah"
    assert res.reply_text is not None
    assert "Updated Sarah" in res.reply_text
    assert "archived for restore" in res.reply_text.lower()
    # Live: new facts replace old.
    live = curator.store.list_live()
    fact_texts = [f.text for f in live[0].facts]
    assert "loves classical now" in fact_texts
    assert "likes jazz" not in fact_texts
    # Archive has SUPERSEDED block.
    archive_body = relationships_archive_path(workspace).read_text(encoding="utf-8")
    assert "## SUPERSEDED" in archive_body
    # Token consumed.
    assert len(curator.tokens) == 0
    assert curator.counters["supersede_executed"] == 1


def test_curator_supersede_missing_returns_friendly_no_op(workspace: Path):
    classifier_verdict = TriggerVerdict(
        verdict="SUPERSEDE",
        person_name="Sarah",
        facts=("loves classical now",),
        confidence=0.95,
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "update what you know about Sarah",
            session_uuid="sess-sup",
            turn_index=1,
        )
    )
    assert res.superseded is False
    assert res.matched is False
    assert res.reply_text == "I don't have anything on Sarah to update."
    assert curator.counters["supersede_missing"] == 1
    assert curator.counters["supersede_executed"] == 0
    assert not relationships_archive_path(workspace).exists()


def test_curator_supersede_blocks_on_sensitive_pattern(workspace: Path):
    _seed_live_with_sarah(workspace)
    classifier_verdict = TriggerVerdict(
        verdict="SUPERSEDE",
        person_name="Sarah",
        facts=("is on prescription antidepressants for her depression",),
        confidence=0.95,
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
        coherence_judge=lambda *a, **kw: CoherenceVerdict.coherent(),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "update what you know about Sarah - she's on antidepressants now",
            session_uuid="sess-sup",
            turn_index=1,
        )
    )
    assert res.superseded is False
    assert res.matched is True
    assert res.blocked_by == "sensitive-pattern"
    assert "can't store" in res.reply_text
    # Live unchanged (still has the original facts).
    live = curator.store.list_live()
    fact_texts = [f.text for f in live[0].facts]
    assert "likes jazz" in fact_texts
    assert curator.counters["supersede_blocked_sensitive"] == 1


def test_curator_supersede_blocks_on_coherence_incoherent(workspace: Path):
    _seed_live_with_sarah(workspace)
    _stage_source_jsonl(
        workspace, "sess-sup", "actually Sarah loves classical now",
    )
    classifier_verdict = TriggerVerdict(
        verdict="SUPERSEDE",
        person_name="Sarah",
        facts=("loves classical now",),
        confidence=0.95,
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
        coherence_judge=lambda *a, **kw: CoherenceVerdict.incoherent(
            reason="ungrounded", explanation="...",
        ),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "actually Sarah loves classical now",
            session_uuid="sess-sup",
            turn_index=1,
        )
    )
    assert res.superseded is False
    assert res.blocked_by == "coherence"
    assert "doesn't match" in res.reply_text
    # Live unchanged.
    live = curator.store.list_live()
    assert any(f.text == "likes jazz" for f in live[0].facts)
    assert curator.counters["supersede_blocked_coherence"] == 1


def test_curator_supersede_skips_judge_when_source_unloadable(workspace: Path):
    """Coherence judge is advisory-skipped (not blocking) when the
    source turn isn't loadable. SUPERSEDE proceeds."""
    _seed_live_with_sarah(workspace)
    classifier_verdict = TriggerVerdict(
        verdict="SUPERSEDE",
        person_name="Sarah",
        facts=("loves classical now",),
        confidence=0.95,
    )
    judge = MagicMock()
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
        coherence_judge=judge,
    )
    res = asyncio.run(
        curator.process_user_turn(
            "actually Sarah loves classical now",
            session_uuid="sess-no-jsonl",
            turn_index=1,
        )
    )
    assert res.superseded is True
    # Judge never invoked because source turn unloadable.
    assert judge.call_count == 0
