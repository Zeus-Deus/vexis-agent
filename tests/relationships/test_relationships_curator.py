"""Day 2: RelationshipsCurator end-to-end flows.

Covers:
- Turn-level ADD: detector + token mint + shadow write + reply.
- Multi-fact under one token (research doc §3.4 cardinality).
- Tick-level promote: token-check + coherence + sensitive scan + promote.
- Coherence INCOHERENT BLOCKS promotion (research doc C-J3 fixture).
- Sensitive-pattern hit BLOCKS promotion.
- Missing token BLOCKS this tick (entry stays in shadow for restart-recovery).
- Restart recovery: classifier verdict matches → re-mint;
  classifier verdict mismatches → drop with REPORT.md row.
- DELETE wired in 3a (covered in test_delete_path.py).
- SUPERSEDE raises NotImplementedError per 3a scope.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.coherence_judge import CoherenceVerdict
from core.relationships.curator import RelationshipsCurator
from core.relationships.store import (
    Fact,
    Person,
    RelationshipsStore,
    _write_people,
    relationships_live_path,
    relationships_shadow_path,
)
from core.relationships.triggers import TriggerVerdict
from core.transcripts import claude_session_jsonl_dir


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
    """Build a one-shot stub classifier that returns the given
    verdict regardless of input."""
    async def _call(text, **kwargs):
        return verdict
    return _call


def _stub_judge(verdict: CoherenceVerdict):
    """Build a coherence-judge stub that returns the given verdict.
    Phase B: the judge accepts ``brain`` as the 4th positional, so
    the stub signature must accept it (test stubs ignore the brain
    reference and return a pre-decided verdict)."""
    def _judge(workspace, lesson, messages, brain=None, **kwargs):
        return verdict
    return _judge


def _stage_source_jsonl(workspace: Path, session_uuid: str, user_text: str) -> None:
    """Write a synthetic JSONL with one user message at the
    expected (session_uuid).jsonl path so restart-recovery can
    load the source turn."""
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


# --------------------------------------------------------------------
# Turn-level ADD path
# --------------------------------------------------------------------


def test_turn_level_add_mints_token_and_stages(workspace: Path):
    classifier_verdict = TriggerVerdict(
        verdict="ADD",
        person_name="Sarah",
        qualifier="girlfriend",
        facts=("likes mystery novels",),
        confidence=0.95,
        matched_pattern_id="ADD-1",
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "remember that my girlfriend Sarah likes mystery novels",
            session_uuid="sess-abc",
            turn_index=1,
        )
    )
    assert res.staged is True
    assert res.verdict == "ADD"
    assert res.person_slug == "sarah"
    assert res.fact_count == 1
    assert res.reply_text and "Sarah" in res.reply_text

    # Token registered.
    assert len(curator.tokens) == 1
    assert curator.tokens.get(
        session_uuid="sess-abc", turn_index=1, person_slug="sarah"
    ) is not None

    # Shadow file written with pending=True.
    shadow_people = curator.store.list_shadow()
    assert len(shadow_people) == 1
    assert shadow_people[0].pending is True
    assert shadow_people[0].slug == "sarah"
    assert len(shadow_people[0].facts) == 1


def test_turn_level_multi_fact_under_one_token(workspace: Path):
    """Research doc §3.4 cardinality: 'remember Sarah likes mystery
    novels and is allergic to peanuts' = 1 token, 2 facts."""
    classifier_verdict = TriggerVerdict(
        verdict="ADD",
        person_name="Sarah",
        qualifier="girlfriend",
        facts=("likes mystery novels", "allergic to peanuts"),
        confidence=0.95,
        matched_pattern_id="ADD-1",
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "remember that Sarah likes mystery novels and is allergic to peanuts",
            session_uuid="sess-mf",
            turn_index=3,
        )
    )
    assert res.fact_count == 2
    # ONE token in the registry, NOT two.
    assert len(curator.tokens) == 1
    token = curator.tokens.get(
        session_uuid="sess-mf", turn_index=3, person_slug="sarah"
    )
    assert token is not None
    assert len(token.fact_ids) == 2
    # Shadow has one Person with two facts.
    shadow = curator.store.list_shadow()
    assert len(shadow) == 1
    assert len(shadow[0].facts) == 2


def test_turn_level_no_trigger_returns_no_op(workspace: Path):
    classifier_verdict = TriggerVerdict(verdict="NONE")
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "what's the weather like today",
            session_uuid="sess-x",
            turn_index=1,
        )
    )
    assert res.staged is False
    assert res.reply_text is None
    assert len(curator.tokens) == 0
    assert curator.store.list_shadow() == []


def test_turn_level_supersede_zero_facts_returns_no_op(workspace: Path):
    """3b: SUPERSEDE with no extracted facts is a degenerate
    classifier output. Curator returns staged=False, reply None,
    no token. Real SUPERSEDE flows live in test_supersede_path.py.
    """
    classifier_verdict = TriggerVerdict(
        verdict="SUPERSEDE",
        person_name="Sarah",
        confidence=0.95,
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
    )
    res = asyncio.run(
        curator.process_user_turn(
            "update what you know about Sarah",
            session_uuid="sess-s",
            turn_index=1,
        )
    )
    assert res.staged is False
    assert res.reply_text is None
    assert res.verdict == "SUPERSEDE"
    assert len(curator.tokens) == 0


# --------------------------------------------------------------------
# Tick-level promote
# --------------------------------------------------------------------


def test_tick_promote_happy_path(workspace: Path):
    """ADD trigger → shadow → tick-promote (coherence COHERENT,
    no sensitive hit) → live. Stages a source JSONL so the
    Day-2-completion missing-transcript guard does not pre-empt
    the judge call."""
    _stage_source_jsonl(workspace, "sess-promote", "remember Sarah likes jazz")
    classifier_verdict = TriggerVerdict(
        verdict="ADD", person_name="Sarah", qualifier="friend",
        facts=("likes jazz",), confidence=0.95, matched_pattern_id="ADD-1",
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
        coherence_judge=_stub_judge(CoherenceVerdict.coherent()),
    )
    asyncio.run(
        curator.process_user_turn(
            "remember Sarah likes jazz",
            session_uuid="sess-promote", turn_index=1,
        )
    )
    results = curator.tick_promote_pending()
    assert len(results) == 1
    assert results[0].promoted is True

    # Shadow now empty, live has it.
    assert curator.store.list_shadow() == []
    live = curator.store.list_live()
    assert len(live) == 1
    assert live[0].slug == "sarah"
    assert live[0].pending is False
    assert live[0].facts[0].staged is False
    # Token consumed.
    assert len(curator.tokens) == 0


def test_tick_promote_blocks_on_incoherent_judge(workspace: Path):
    """Research doc C-J3: source-turn says 'I have no siblings',
    consent says 'remember my sister Sarah'. INCOHERENT BLOCKS
    promotion (entry stays in shadow)."""
    classifier_verdict = TriggerVerdict(
        verdict="ADD", person_name="Sarah", qualifier="sister",
        facts=("hates jazz",), confidence=0.95, matched_pattern_id="ADD-1",
    )
    incoherent = CoherenceVerdict.incoherent(
        reason="contradicts-window",
        explanation="user said 'I have no siblings' two turns earlier",
    )
    _stage_source_jsonl(
        workspace, "sess-cj3", "remember my sister Sarah hates jazz"
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
        coherence_judge=_stub_judge(incoherent),
    )
    asyncio.run(
        curator.process_user_turn(
            "remember my sister Sarah hates jazz",
            session_uuid="sess-cj3", turn_index=1,
        )
    )
    results = curator.tick_promote_pending()
    assert len(results) == 1
    assert results[0].promoted is False
    assert results[0].blocked_by == "coherence"
    # Shadow STILL has the entry (blocked, not dropped).
    assert len(curator.store.list_shadow()) == 1
    # Live empty.
    assert curator.store.list_live() == []
    # REPORT.md drop event was recorded.
    drops = curator.drain_drop_events()
    assert any(d.reason == "coherence-incoherent" for d in drops)


def test_tick_promote_blocks_on_sensitive_hit(workspace: Path):
    """C-X1 analog: shadow entry contains a fact that the medical
    pattern catches at promote-time. BLOCKS promotion."""
    _stage_source_jsonl(
        workspace, "sess-med",
        "remember that my coworker Marco is on prescription antidepressants",
    )
    classifier_verdict = TriggerVerdict(
        verdict="ADD", person_name="Marco", qualifier="coworker",
        facts=("is on prescription antidepressants",),
        confidence=0.95, matched_pattern_id="ADD-1",
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
        coherence_judge=_stub_judge(CoherenceVerdict.coherent()),
    )
    asyncio.run(
        curator.process_user_turn(
            "remember that my coworker Marco is on prescription antidepressants",
            session_uuid="sess-med", turn_index=1,
        )
    )
    results = curator.tick_promote_pending()
    assert len(results) == 1
    assert results[0].promoted is False
    assert results[0].blocked_by == "sensitive"
    # Live empty.
    assert curator.store.list_live() == []


def test_tick_promote_blocks_on_missing_token(workspace: Path):
    """If the in-memory token is gone (daemon restart, no recovery
    yet), promote BLOCKS this tick — but the shadow entry stays so
    restart-recovery can re-mint on the next startup."""
    curator = RelationshipsCurator(
        workspace=workspace,
        coherence_judge=_stub_judge(CoherenceVerdict.coherent()),
    )
    # Manually stage a person without minting a token (simulates
    # the shadow file surviving a daemon restart).
    person = Person(
        slug="ghost",
        display_name="Ghost",
        relationship="friend",
        qualifier=None,
        last_confirmed="2026-05-04",
        source_session="sess-ghost",
        facts=(Fact("orphaned fact", "2026-05-04", "sessghos", staged=True),),
        pending=True,
        staged_at="2026-05-04T00:00:00Z",
        source_turn_index=1,
    )
    # Simulate "shadow file survived daemon restart, in-memory
    # tokens lost" by writing the file at the storage layer
    # directly (the curator-facing store.stage now requires a
    # token, which is exactly what we're saying isn't here).
    _write_people(
        relationships_shadow_path(workspace), [person], kind="shadow",
    )
    results = curator.tick_promote_pending()
    assert len(results) == 1
    assert results[0].promoted is False
    assert results[0].blocked_by == "missing-token"
    # Shadow still has the entry.
    assert len(curator.store.list_shadow()) == 1


# --------------------------------------------------------------------
# Ask 2 (Day 2 completion): missing-transcript guard. Synthetic
# Telegram session_uuid → no JSONL on disk → curator MUST block
# promotion deterministically rather than spawn the judge against
# an empty transcript window.
# --------------------------------------------------------------------


def test_tick_promote_blocks_on_missing_transcript_for_telegram_synthetic_uuid(
    workspace: Path,
):
    """Walks the real Telegram-triggered ADD code path:

    1. Trigger detector fires, mints token, writes pending shadow
       entry with source_session = 'telegram-chat-99' (no JSONL on
       disk for that synthetic id).
    2. Tick fires, RelationshipsCurator picks up the pending entry.
    3. Token-presence check passes (we just minted it).
    4. The curator pre-empts the judge call because source_msg is
       None (no JSONL). Sets coherence_block = 'missing_transcript'
       on the shadow entry, records a drop event, returns
       PromoteResult(blocked_by='missing-transcript').

    Asserts the entry stays in shadow with the flag, judge is NEVER
    invoked (would otherwise spawn a real claude -p subprocess in
    degraded mode), and a REPORT.md row is recorded."""
    judge_calls = 0

    def _spy_judge(workspace, lesson, messages, **kwargs):
        nonlocal judge_calls
        judge_calls += 1
        return CoherenceVerdict.coherent()  # would be vacuous here

    classifier_verdict = TriggerVerdict(
        verdict="ADD", person_name="Sarah", qualifier="coworker",
        facts=("likes mystery novels",), confidence=0.95,
        matched_pattern_id="ADD-1",
    )
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(classifier_verdict),
        coherence_judge=_spy_judge,
    )
    # Drive the turn-level path with a synthetic Telegram session_uuid.
    asyncio.run(
        curator.process_user_turn(
            "remember my coworker Sarah likes mystery novels",
            session_uuid="telegram-chat-99",  # no JSONL on disk
            turn_index=1,
        )
    )
    # Tick fires.
    results = curator.tick_promote_pending()
    assert len(results) == 1
    assert results[0].promoted is False
    assert results[0].blocked_by == "missing-transcript"
    # Judge was NEVER invoked.
    assert judge_calls == 0
    # Shadow still holds the entry.
    shadow = curator.store.list_shadow()
    assert len(shadow) == 1
    assert shadow[0].slug == "sarah"
    # The shadow entry now carries the coherence_block flag.
    assert shadow[0].coherence_block == "missing_transcript"
    # Live empty.
    assert curator.store.list_live() == []
    # REPORT.md drop event recorded.
    drops = curator.drain_drop_events()
    assert any(d.reason == "coherence-missing-transcript" for d in drops)
    # Token still in registry — entry is blocked, not dropped, so
    # a future tick (e.g. once the JSONL becomes loadable via the
    # Day 3 brain-session-UUID handoff) can retry.
    assert len(curator.tokens) == 1


# --------------------------------------------------------------------
# Restart recovery
# --------------------------------------------------------------------


def test_restart_recovery_re_mints_on_verdict_match(workspace: Path):
    """Shadow has a pending entry, daemon "restart" — the in-memory
    PendingTokens registry is empty. recover_after_restart re-runs
    the classifier against the stored source turn; verdict matches
    → token re-minted; subsequent tick promotes successfully."""
    # Pre-stage source JSONL so the recovery can load the turn.
    _stage_source_jsonl(
        workspace, "sess-recover",
        "remember Sarah likes jazz",
    )
    # Pre-write a shadow file directly (simulates surviving a restart).
    pre_curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(
            TriggerVerdict(
                verdict="ADD", person_name="Sarah", qualifier="friend",
                facts=("likes jazz",), confidence=0.95,
                matched_pattern_id="ADD-1",
            )
        ),
    )
    asyncio.run(
        pre_curator.process_user_turn(
            "remember Sarah likes jazz",
            session_uuid="sess-recover", turn_index=1,
        )
    )
    assert len(pre_curator.tokens) == 1
    assert len(pre_curator.store.list_shadow()) == 1

    # Now build a NEW curator (fresh in-memory tokens — simulates
    # daemon restart). Same classifier stub returns the same verdict
    # against the same source turn.
    new_curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(
            TriggerVerdict(
                verdict="ADD", person_name="Sarah", qualifier="friend",
                facts=("likes jazz",), confidence=0.95,
                matched_pattern_id="ADD-1",
            )
        ),
        coherence_judge=_stub_judge(CoherenceVerdict.coherent()),
    )
    assert len(new_curator.tokens) == 0  # registry empty post-restart
    recovered = asyncio.run(new_curator.recover_after_restart())
    assert len(recovered) == 1
    assert recovered[0].re_minted is True
    assert len(new_curator.tokens) == 1

    # Subsequent tick promotes the entry.
    promote_results = new_curator.tick_promote_pending()
    assert len(promote_results) == 1
    assert promote_results[0].promoted is True


def test_restart_recovery_drops_on_verdict_mismatch(workspace: Path):
    """Classifier returns a different verdict on the same source
    turn (e.g. classifier improved between releases) — drop the
    pending entry, surface in REPORT.md."""
    _stage_source_jsonl(
        workspace, "sess-mismatch",
        "remember Sarah likes jazz",
    )
    pre_curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(
            TriggerVerdict(
                verdict="ADD", person_name="Sarah", qualifier="friend",
                facts=("likes jazz",), confidence=0.95,
                matched_pattern_id="ADD-1",
            )
        ),
    )
    asyncio.run(
        pre_curator.process_user_turn(
            "remember Sarah likes jazz",
            session_uuid="sess-mismatch", turn_index=1,
        )
    )
    assert len(pre_curator.store.list_shadow()) == 1

    # New curator — classifier stub returns NONE this time
    # (verdict mismatch).
    new_curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(TriggerVerdict(verdict="NONE")),
    )
    recovered = asyncio.run(new_curator.recover_after_restart())
    assert len(recovered) == 1
    assert recovered[0].re_minted is False
    assert recovered[0].dropped_reason == "verdict-mismatch"
    # Shadow now empty (entry dropped).
    assert new_curator.store.list_shadow() == []
    # Drop event recorded for REPORT.md.
    drops = new_curator.drain_drop_events()
    assert any(d.reason == "recovery-verdict-mismatch" for d in drops)


def test_restart_recovery_drops_on_missing_source(workspace: Path):
    """Pending entry references a session UUID whose JSONL is
    gone (workspace deleted, archive purge) — drop, surface."""
    # Stage a shadow person that points at a non-existent session.
    curator = RelationshipsCurator(workspace=workspace)
    person = Person(
        slug="orphan",
        display_name="Orphan",
        relationship="friend",
        qualifier=None,
        last_confirmed="2026-05-04",
        source_session="sess-gone",
        facts=(Fact("ghost fact", "2026-05-04", "sessgone", staged=True),),
        pending=True,
        staged_at="2026-05-04T00:00:00Z",
        source_turn_index=1,
    )
    # Simulate "shadow file survived daemon restart, in-memory
    # tokens lost" by writing the file at the storage layer
    # directly (the curator-facing store.stage now requires a
    # token, which is exactly what we're saying isn't here).
    _write_people(
        relationships_shadow_path(workspace), [person], kind="shadow",
    )
    recovered = asyncio.run(curator.recover_after_restart())
    assert len(recovered) == 1
    assert recovered[0].re_minted is False
    assert recovered[0].dropped_reason == "source-missing"
    assert curator.store.list_shadow() == []
    drops = curator.drain_drop_events()
    assert any(d.reason == "recovery-source-missing" for d in drops)


def test_restart_recovery_is_one_shot(workspace: Path):
    """recover_after_restart is idempotent — calling it twice
    returns an empty list the second time even if there's
    pending work."""
    _stage_source_jsonl(workspace, "sess-once", "remember Sarah likes jazz")
    curator = RelationshipsCurator(
        workspace=workspace,
        classifier_call=_stub_classifier(
            TriggerVerdict(
                verdict="ADD", person_name="Sarah", qualifier="friend",
                facts=("likes jazz",), confidence=0.95,
                matched_pattern_id="ADD-1",
            )
        ),
    )
    person = Person(
        slug="sarah", display_name="Sarah", relationship="friend",
        qualifier="friend", last_confirmed="2026-05-04",
        source_session="sess-once",
        facts=(Fact("likes jazz", "2026-05-04", "sessonce", staged=True),),
        pending=True, staged_at="2026-05-04T00:00:00Z", source_turn_index=1,
    )
    # Simulate "shadow file survived daemon restart, in-memory
    # tokens lost" by writing the file at the storage layer
    # directly (the curator-facing store.stage now requires a
    # token, which is exactly what we're saying isn't here).
    _write_people(
        relationships_shadow_path(workspace), [person], kind="shadow",
    )
    first = asyncio.run(curator.recover_after_restart())
    second = asyncio.run(curator.recover_after_restart())
    assert len(first) == 1
    assert second == []
