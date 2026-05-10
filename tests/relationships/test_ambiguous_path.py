"""v3b Day 3b: AMBIGUOUS verdict + per-chat pending-disambiguation state.

Covers:

- AMBIGUOUS first turn: pending entry persisted to disk, AMBIGUOUS
  reply sent, no token, no shadow.
- AMBIGUOUS second turn resolves: merged classification yields a
  qualified slug, original verdict fires using the pending entry's
  ``session_uuid`` / ``turn_index``.
- AMBIGUOUS still ambiguous on second turn: TTL refreshed, count
  bumped.
- 3rd unresolved AMBIGUOUS: silent drop, pending entry deleted.
- Daemon restart with live pending entry: store loads, entry
  survives. Next disambiguation turn resolves correctly.
- Pending entry TTL expiry: a 5-minute-old entry on next hook fire
  is dropped before classification.
- Unrelated message during pending state: pending entry dropped
  silently, brain handles the message.
- Concurrent pending entries for different chat_ids: independent
  state.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vexis_agent.core.relationships.curator import RelationshipsCurator
from vexis_agent.core.relationships.pending import (
    MAX_AMBIGUITY_REPROMPTS,
    PendingDisambiguationStore,
    PendingEntry,
)
from vexis_agent.core.relationships.store import (
    Fact,
    Person,
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


def _seed_two_sarahs(workspace: Path) -> None:
    """Seed live with two Sarah variants so DELETE/SUPERSEDE on
    the bare slug triggers AMBIGUOUS."""
    sarah_friend = Person(
        slug="sarah-friend",
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
    sarah_coworker = Person(
        slug="sarah-coworker",
        display_name="Sarah",
        relationship="coworker",
        qualifier="coworker",
        last_confirmed="2026-04-15",
        source_session="bbb22222",
        facts=(
            Fact(
                text="tech lead on Vexis",
                confirmed_date="2026-04-15",
                source_session_short="bbb22222",
                staged=False,
            ),
        ),
    )
    relationships_live_path(workspace).write_text(
        serialize_relationships_file(
            [sarah_friend, sarah_coworker], kind="live",
        ),
        encoding="utf-8",
    )


def _make_classifier_sequence(verdicts: list[TriggerVerdict]):
    """Sequenced stub: each call returns the next verdict in the list,
    repeats the last one once exhausted."""
    state = {"i": 0}

    async def _call(text, **kwargs):
        i = state["i"]
        state["i"] = min(i + 1, len(verdicts) - 1)
        return verdicts[i]

    return _call


# ---------------------------------------------------------------- pending store


def test_pending_store_put_get_consume_roundtrip(workspace: Path):
    store = PendingDisambiguationStore(workspace=workspace)
    entry = store.put(
        chat_id=42,
        original_verdict="ADD",
        original_text="remember Sarah hates jazz",
        candidate_slugs=["sarah-friend", "sarah-coworker"],
        session_uuid="sess-1",
        turn_index=5,
    )
    assert entry.chat_id == 42
    assert entry.ambiguity_count == 1

    # Persistence: file landed on disk.
    assert store.path.exists()
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert "42" in on_disk

    # Round-trip via a fresh store instance.
    store2 = PendingDisambiguationStore(workspace=workspace)
    got = store2.get(42)
    assert got is not None
    assert got.original_text == "remember Sarah hates jazz"
    assert got.candidate_slugs == ("sarah-friend", "sarah-coworker")

    # Consume drops the entry.
    consumed = store2.consume(42)
    assert consumed is not None
    assert store2.get(42) is None
    assert json.loads(store2.path.read_text(encoding="utf-8")) == {}


def test_pending_store_get_drops_expired_entries(workspace: Path):
    store = PendingDisambiguationStore(
        workspace=workspace, ttl=timedelta(seconds=1),
    )
    store.put(
        chat_id=42,
        original_verdict="ADD",
        original_text="remember Sarah hates jazz",
        candidate_slugs=["sarah-friend"],
        session_uuid="sess-1",
        turn_index=1,
    )
    # Force expiry by overwriting with an in-the-past expires_at.
    expired = PendingEntry(
        chat_id=42,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        original_verdict="ADD",
        original_text="remember Sarah hates jazz",
        candidate_slugs=("sarah-friend",),
        session_uuid="sess-1",
        turn_index=1,
        ambiguity_count=1,
    )
    store._entries[42] = expired

    got = store.get(42)
    assert got is None
    # Expired entry was removed from the in-memory dict.
    assert 42 not in store._entries


def test_pending_store_load_drops_expired_on_startup(workspace: Path):
    """A daemon restart should not surface a pending entry whose TTL
    already lapsed before the restart."""
    # Hand-write an on-disk entry with an in-the-past expires_at.
    pending_dir = workspace / ".vexis"
    pending_dir.mkdir(parents=True)
    expired_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    pending_dir.joinpath("relationships-pending.json").write_text(
        json.dumps({
            "42": {
                "expires_at": expired_iso,
                "original_verdict": "ADD",
                "original_text": "remember Sarah hates jazz",
                "candidate_slugs": ["sarah-friend"],
                "session_uuid": "sess-1",
                "turn_index": 1,
                "ambiguity_count": 1,
            }
        }),
        encoding="utf-8",
    )
    store = PendingDisambiguationStore(workspace=workspace)
    assert store.get(42) is None


def test_pending_store_concurrent_chat_ids_independent(workspace: Path):
    store = PendingDisambiguationStore(workspace=workspace)
    store.put(
        chat_id=1,
        original_verdict="ADD",
        original_text="remember Sarah likes jazz",
        candidate_slugs=["sarah-friend"],
        session_uuid="sess-1",
        turn_index=1,
    )
    store.put(
        chat_id=2,
        original_verdict="DELETE",
        original_text="forget Marco",
        candidate_slugs=["marco-coworker", "marco-cousin"],
        session_uuid="sess-2",
        turn_index=8,
    )
    e1 = store.get(1)
    e2 = store.get(2)
    assert e1 is not None and e1.original_verdict == "ADD"
    assert e2 is not None and e2.original_verdict == "DELETE"
    # Consuming chat 1 doesn't affect chat 2.
    store.consume(1)
    assert store.get(1) is None
    assert store.get(2) is not None


# ---------------------------------------------------------------- curator AMBIGUOUS


def test_ambiguous_first_turn_writes_pending_and_replies(workspace: Path):
    _seed_two_sarahs(workspace)
    classifier = _make_classifier_sequence([
        TriggerVerdict(
            verdict="DELETE", person_name="Sarah", confidence=0.95,
        ),
    ])
    curator = RelationshipsCurator(
        workspace=workspace, classifier_call=classifier,
    )
    res = asyncio.run(
        curator.process_user_turn(
            "forget Sarah",
            session_uuid="sess-amb",
            turn_index=3,
            chat_id=42,
        )
    )
    assert res.ambiguous is True
    assert res.matched is False
    assert res.verdict == "AMBIGUOUS"
    assert res.reply_text and "Which Sarah" in res.reply_text
    assert "friend" in res.reply_text and "coworker" in res.reply_text

    # No token, no shadow.
    assert len(curator.tokens) == 0
    assert curator.store.list_shadow() == []

    # Pending entry persisted.
    pending = curator._pending_disambig.get(42)
    assert pending is not None
    assert pending.original_verdict == "DELETE"
    assert pending.original_text == "forget Sarah"
    assert pending.session_uuid == "sess-amb"
    assert pending.turn_index == 3
    assert curator.counters["ambiguous_emitted"] == 1


def test_ambiguous_second_turn_resolves_with_qualifier(workspace: Path):
    _seed_two_sarahs(workspace)
    # First turn: classifier returns DELETE/Sarah/no-qualifier (ambiguous).
    # Second turn: classifier (re-run on merged text) returns
    # DELETE/Sarah/qualifier=coworker → resolves to sarah-coworker.
    classifier = _make_classifier_sequence([
        TriggerVerdict(
            verdict="DELETE", person_name="Sarah", confidence=0.95,
        ),
        TriggerVerdict(
            verdict="DELETE",
            person_name="Sarah",
            qualifier="coworker",
            confidence=0.95,
        ),
    ])
    curator = RelationshipsCurator(
        workspace=workspace, classifier_call=classifier,
    )
    # Turn 1: AMBIGUOUS.
    asyncio.run(
        curator.process_user_turn(
            "forget Sarah",
            session_uuid="sess-amb",
            turn_index=3,
            chat_id=42,
        )
    )
    # Turn 2: "the one from work" + merge → sarah-coworker.
    res2 = asyncio.run(
        curator.process_user_turn(
            "the one from work",
            session_uuid="sess-amb",
            turn_index=4,
            chat_id=42,
        )
    )
    assert res2.deleted is True
    assert res2.matched is True
    assert res2.verdict == "DELETE"
    assert res2.person_slug == "sarah-coworker"
    assert "Forgot what I had on Sarah" in res2.reply_text

    # Live: sarah-friend remains; sarah-coworker gone.
    live_slugs = [p.slug for p in curator.store.list_live()]
    assert "sarah-friend" in live_slugs
    assert "sarah-coworker" not in live_slugs

    # Pending entry consumed.
    assert curator._pending_disambig.get(42) is None
    assert curator.counters["ambiguous_resolved"] == 1


def test_ambiguous_three_strikes_drops_silently(workspace: Path):
    _seed_two_sarahs(workspace)
    # All three turns return DELETE/Sarah/no-qualifier → still
    # ambiguous each time.
    ambiguous_verdict = TriggerVerdict(
        verdict="DELETE", person_name="Sarah", confidence=0.95,
    )
    classifier = _make_classifier_sequence([ambiguous_verdict])
    curator = RelationshipsCurator(
        workspace=workspace, classifier_call=classifier,
    )
    # Turn 1: AMBIGUOUS, count=1.
    asyncio.run(curator.process_user_turn(
        "forget Sarah", session_uuid="s", turn_index=1, chat_id=42,
    ))
    # Turn 2: still AMBIGUOUS, count bumped to 2 (still under cap).
    res2 = asyncio.run(curator.process_user_turn(
        "the one I told you about", session_uuid="s", turn_index=2, chat_id=42,
    ))
    assert res2.ambiguous is True
    pending = curator._pending_disambig.get(42)
    assert pending is not None and pending.ambiguity_count == 2

    # Turn 3: still AMBIGUOUS, count bumped to 3 (still under cap;
    # cap is "exceeds" so 3 is allowed, 4 drops).
    res3 = asyncio.run(curator.process_user_turn(
        "you know which one", session_uuid="s", turn_index=3, chat_id=42,
    ))
    assert res3.ambiguous is True
    pending = curator._pending_disambig.get(42)
    assert pending is not None and pending.ambiguity_count == 3

    # Turn 4: bumped to 4 → exceeds MAX_AMBIGUITY_REPROMPTS (3) →
    # silent drop, no reply.
    res4 = asyncio.run(curator.process_user_turn(
        "still you know", session_uuid="s", turn_index=4, chat_id=42,
    ))
    assert res4.reply_text is None
    assert curator._pending_disambig.get(42) is None
    assert curator.counters["ambiguous_dropped_unresolved"] == 1


def test_ambiguous_unrelated_message_drops_pending(workspace: Path):
    _seed_two_sarahs(workspace)
    # Turn 1 returns DELETE/Sarah → ambiguous. Turn 2's merge
    # returns NONE (the user said something unrelated).
    classifier = _make_classifier_sequence([
        TriggerVerdict(
            verdict="DELETE", person_name="Sarah", confidence=0.95,
        ),
        TriggerVerdict(verdict="NONE"),
    ])
    curator = RelationshipsCurator(
        workspace=workspace, classifier_call=classifier,
    )
    asyncio.run(curator.process_user_turn(
        "forget Sarah", session_uuid="s", turn_index=1, chat_id=42,
    ))
    res2 = asyncio.run(curator.process_user_turn(
        "what's the weather", session_uuid="s", turn_index=2, chat_id=42,
    ))
    # Merge returned NONE → unrelated → pending dropped, hook
    # falls through (returns None to caller).
    assert res2.reply_text is None
    assert curator._pending_disambig.get(42) is None
    assert curator.counters["ambiguous_dropped_unrelated"] == 1


def test_ambiguous_pending_entry_survives_curator_recreation(workspace: Path):
    """Daemon-restart simulation: write pending entry, throw away
    the curator instance, build a new one, second turn still
    resolves correctly."""
    _seed_two_sarahs(workspace)
    classifier1 = _make_classifier_sequence([
        TriggerVerdict(
            verdict="DELETE", person_name="Sarah", confidence=0.95,
        ),
    ])
    curator1 = RelationshipsCurator(
        workspace=workspace, classifier_call=classifier1,
    )
    asyncio.run(curator1.process_user_turn(
        "forget Sarah", session_uuid="sess-amb", turn_index=3, chat_id=42,
    ))
    # Daemon restart: brand-new curator instance reading the same
    # workspace.
    classifier2 = _make_classifier_sequence([
        TriggerVerdict(
            verdict="DELETE",
            person_name="Sarah",
            qualifier="friend",
            confidence=0.95,
        ),
    ])
    curator2 = RelationshipsCurator(
        workspace=workspace, classifier_call=classifier2,
    )
    # Confirm pending entry survived.
    assert curator2._pending_disambig.get(42) is not None
    res2 = asyncio.run(curator2.process_user_turn(
        "the one from college",
        session_uuid="sess-amb",
        turn_index=4,
        chat_id=42,
    ))
    assert res2.deleted is True
    assert res2.person_slug == "sarah-friend"


def test_ambiguous_chat_id_none_does_not_persist(workspace: Path):
    """When chat_id is None (test path or non-Telegram caller),
    AMBIGUOUS still returns a reply but writes no pending entry."""
    _seed_two_sarahs(workspace)
    classifier = _make_classifier_sequence([
        TriggerVerdict(
            verdict="DELETE", person_name="Sarah", confidence=0.95,
        ),
    ])
    curator = RelationshipsCurator(
        workspace=workspace, classifier_call=classifier,
    )
    res = asyncio.run(curator.process_user_turn(
        "forget Sarah", session_uuid="s", turn_index=1, chat_id=None,
    ))
    assert res.ambiguous is True
    assert res.reply_text and "Which Sarah" in res.reply_text
    # No pending entries anywhere.
    assert curator._pending_disambig.all() == []
