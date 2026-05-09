"""Day 3 tests for core/user_candidates.py.

Coverage:
  - load/save round-trip, missing-file is empty, corrupt-file is empty.
  - add_occurrence: new claim, existing claim, multi-session counting.
  - distinct_session_uuids vs distinct_session_uuids_within: window
    expiry semantics.
  - eligible_for_promotion: insufficient sessions, threshold met, not
    yet promoted vs already promoted, window expiry.
  - mark_promoted: idempotent, missing claim returns None.
  - expire_stale: removes unpromoted-and-old, retains promoted-or-recent.
  - list_pending vs list_promoted vs list_all.
  - schema validation: corrupt occurrence dropped, top-level non-dict
    treated as empty, malformed timestamps drop the entry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vexis_agent.core.user_candidates import (
    DEFAULT_PROMOTION_THRESHOLD,
    DEFAULT_WINDOW,
    UserCandidate,
    UserCandidateOccurrence,
    UserCandidateStore,
)


# --------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "user_candidates.json"


def _utc(year=2026, month=5, day=3, hour=12, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------
# Round-trip + corrupt-file handling
# --------------------------------------------------------------------


def test_load_missing_file_returns_empty(store_path):
    store = UserCandidateStore(store_path)
    assert store.load() == {}


def test_save_and_reload_round_trip(store_path):
    store = UserCandidateStore(store_path)
    store.add_occurrence("c1", "sess-1", "ev1", now=_utc(hour=10))
    reloaded = UserCandidateStore(store_path).load()
    assert "c1" in reloaded
    assert reloaded["c1"].claim == "c1"
    assert len(reloaded["c1"].occurrences) == 1
    assert reloaded["c1"].occurrences[0].session_uuid == "sess-1"


def test_load_corrupt_json_returns_empty(store_path):
    store_path.write_text("{not json", encoding="utf-8")
    assert UserCandidateStore(store_path).load() == {}


def test_load_top_level_array_returns_empty(store_path):
    """Schema requires {by_claim: {}} at top level. An array shape
    means somebody hand-edited the file wrong; treat as empty rather
    than crashing the curator."""
    store_path.write_text("[]", encoding="utf-8")
    assert UserCandidateStore(store_path).load() == {}


def test_load_drops_malformed_occurrences(store_path):
    """One malformed occurrence in a candidate must not take down
    the whole candidate — drop just the bad entry, keep the rest."""
    import json
    payload = {
        "by_claim": {
            "c1": {
                "first_seen": "2026-05-03T10:00:00Z",
                "last_seen": "2026-05-03T11:00:00Z",
                "occurrences": [
                    {"session_uuid": "ok", "evidence": "e",
                     "seen_at": "2026-05-03T10:00:00Z"},
                    {"session_uuid": "bad", "evidence": "e"},  # missing seen_at
                    "not even a dict",
                    {"session_uuid": 123, "evidence": "e",
                     "seen_at": "2026-05-03T10:30:00Z"},  # wrong type
                ],
            }
        }
    }
    store_path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = UserCandidateStore(store_path).load()
    assert "c1" in loaded
    assert len(loaded["c1"].occurrences) == 1
    assert loaded["c1"].occurrences[0].session_uuid == "ok"


# --------------------------------------------------------------------
# add_occurrence + distinct counting
# --------------------------------------------------------------------


def test_add_occurrence_creates_new_claim(store_path):
    store = UserCandidateStore(store_path)
    c = store.add_occurrence("c1", "sess-1", "ev1", now=_utc(hour=10))
    assert c.claim == "c1"
    assert len(c.occurrences) == 1
    assert c.distinct_session_uuids() == {"sess-1"}


def test_add_occurrence_appends_to_existing(store_path):
    store = UserCandidateStore(store_path)
    store.add_occurrence("c1", "sess-1", "ev1", now=_utc(hour=10))
    c = store.add_occurrence("c1", "sess-2", "ev2", now=_utc(hour=11))
    assert len(c.occurrences) == 2
    assert c.distinct_session_uuids() == {"sess-1", "sess-2"}


def test_same_session_repeats_count_as_one_distinct(store_path):
    store = UserCandidateStore(store_path)
    store.add_occurrence("c1", "sess-1", "ev1", now=_utc(hour=10))
    store.add_occurrence("c1", "sess-1", "ev2", now=_utc(hour=11))
    c = store.add_occurrence("c1", "sess-1", "ev3", now=_utc(hour=12))
    assert len(c.occurrences) == 3  # audit log retains all
    assert len(c.distinct_session_uuids()) == 1  # threshold counts unique


def test_distinct_session_uuids_within_excludes_old(store_path):
    """Old observations age out. A 90-day-old observation plus one
    today is one distinct session in a 30-day window."""
    store = UserCandidateStore(store_path)
    store.add_occurrence("c1", "ancient", "ev", now=_utc(day=1) - timedelta(days=90))
    store.add_occurrence("c1", "recent", "ev", now=_utc(day=1))
    c = store.get("c1")
    distinct_30d = c.distinct_session_uuids_within(timedelta(days=30), now=_utc(day=1))
    assert distinct_30d == {"recent"}


# --------------------------------------------------------------------
# Promotion eligibility
# --------------------------------------------------------------------


def test_eligible_false_for_unknown_claim(store_path):
    assert UserCandidateStore(store_path).eligible_for_promotion("nope") is False


def test_eligible_false_below_threshold(store_path):
    store = UserCandidateStore(store_path)
    store.add_occurrence("c1", "sess-1", "ev", now=_utc(hour=10))
    assert store.eligible_for_promotion("c1", now=_utc(hour=11)) is False


def test_eligible_true_at_threshold(store_path):
    store = UserCandidateStore(store_path)
    store.add_occurrence("c1", "sess-1", "ev", now=_utc(hour=10))
    store.add_occurrence("c1", "sess-2", "ev", now=_utc(hour=11))
    assert store.eligible_for_promotion("c1", now=_utc(hour=12)) is True


def test_eligible_false_after_promotion(store_path):
    store = UserCandidateStore(store_path)
    store.add_occurrence("c1", "sess-1", "ev", now=_utc(hour=10))
    store.add_occurrence("c1", "sess-2", "ev", now=_utc(hour=11))
    store.mark_promoted("c1", now=_utc(hour=12))
    assert store.eligible_for_promotion("c1", now=_utc(hour=13)) is False


def test_eligible_false_when_distinct_sessions_outside_window(store_path):
    """Two distinct sessions but BOTH older than the window — the
    eligibility check uses ``seen_at`` not ``first_seen``, so this
    must not promote."""
    store = UserCandidateStore(store_path)
    far_past = _utc(day=1) - timedelta(days=60)
    store.add_occurrence("c1", "sess-1", "ev", now=far_past)
    store.add_occurrence("c1", "sess-2", "ev", now=far_past + timedelta(hours=1))
    assert store.eligible_for_promotion(
        "c1", window=timedelta(days=30), now=_utc(day=1)
    ) is False


def test_eligible_with_one_old_one_recent(store_path):
    """One observation way old + one recent = only one in-window
    session = NOT eligible."""
    store = UserCandidateStore(store_path)
    store.add_occurrence("c1", "old", "ev", now=_utc(day=1) - timedelta(days=90))
    store.add_occurrence("c1", "recent", "ev", now=_utc(day=1))
    assert store.eligible_for_promotion(
        "c1", window=timedelta(days=30), now=_utc(day=1)
    ) is False


# --------------------------------------------------------------------
# mark_promoted
# --------------------------------------------------------------------


def test_mark_promoted_sets_fields(store_path):
    store = UserCandidateStore(store_path)
    store.add_occurrence("c1", "sess-1", "ev", now=_utc(hour=10))
    when = _utc(hour=12)
    c = store.mark_promoted("c1", now=when)
    assert c is not None
    assert c.promoted_to_user_md is True
    assert c.promoted_at == when


def test_mark_promoted_missing_claim_returns_none(store_path):
    assert UserCandidateStore(store_path).mark_promoted("never-added") is None


# --------------------------------------------------------------------
# expire_stale
# --------------------------------------------------------------------


def test_expire_stale_removes_old_unpromoted(store_path):
    store = UserCandidateStore(store_path)
    store.add_occurrence("old", "sess-1", "ev", now=_utc(day=1) - timedelta(days=60))
    store.add_occurrence("recent", "sess-2", "ev", now=_utc(day=1))
    removed = store.expire_stale(now=_utc(day=1), window=timedelta(days=30))
    assert removed == 1
    remaining = {c.claim for c in store.list_all()}
    assert "recent" in remaining
    assert "old" not in remaining


def test_expire_stale_retains_promoted_even_when_old(store_path):
    """Promoted claims stay forever for audit. Don't expire them."""
    store = UserCandidateStore(store_path)
    store.add_occurrence("promoted", "sess-1", "ev", now=_utc(day=1) - timedelta(days=90))
    store.add_occurrence("promoted", "sess-2", "ev", now=_utc(day=1) - timedelta(days=90))
    store.mark_promoted("promoted", now=_utc(day=1) - timedelta(days=90))
    removed = store.expire_stale(now=_utc(day=1), window=timedelta(days=30))
    assert removed == 0
    assert store.get("promoted") is not None


def test_expire_stale_no_op_when_nothing_old(store_path):
    store = UserCandidateStore(store_path)
    store.add_occurrence("recent", "sess-1", "ev", now=_utc(day=1))
    assert store.expire_stale(now=_utc(day=1)) == 0


# --------------------------------------------------------------------
# list_pending vs list_promoted vs list_all
# --------------------------------------------------------------------


def test_list_pending_excludes_promoted(store_path):
    store = UserCandidateStore(store_path)
    store.add_occurrence("a", "sess-1", "ev", now=_utc(hour=10))
    store.add_occurrence("b", "sess-1", "ev", now=_utc(hour=11))
    store.add_occurrence("b", "sess-2", "ev", now=_utc(hour=12))
    store.mark_promoted("b", now=_utc(hour=13))
    pending = store.list_pending()
    promoted = store.list_promoted()
    assert [c.claim for c in pending] == ["a"]
    assert [c.claim for c in promoted] == ["b"]
    assert {c.claim for c in store.list_all()} == {"a", "b"}


# --------------------------------------------------------------------
# Defaults are sane
# --------------------------------------------------------------------


def test_default_promotion_threshold_is_two():
    """Spec value from §3.4 of the v2 research doc."""
    assert DEFAULT_PROMOTION_THRESHOLD == 2


def test_default_window_is_thirty_days():
    """Spec value from §3.4 of the v2 research doc."""
    assert DEFAULT_WINDOW == timedelta(days=30)


# --------------------------------------------------------------------
# Day 3.5: hard cap on occurrences per claim
# --------------------------------------------------------------------


def test_max_occurrences_per_claim_constant():
    """The cap is set in the module so tests pin to it; production
    callers can't override (intentional — a buggy expire_stale
    shouldn't be papered over by a generous cap)."""
    from vexis_agent.core.user_candidates import MAX_OCCURRENCES_PER_CLAIM
    assert MAX_OCCURRENCES_PER_CLAIM == 20


def test_add_occurrence_caps_at_max(store_path):
    """Occurrences past the cap drop the OLDEST entries FIFO so the
    queue can't grow unbounded even if expire_stale never runs."""
    from vexis_agent.core.user_candidates import MAX_OCCURRENCES_PER_CLAIM
    store = UserCandidateStore(store_path)
    # Seed cap+5 occurrences with deterministic increasing timestamps.
    base = _utc(year=2026, month=5, day=3, hour=10)
    for i in range(MAX_OCCURRENCES_PER_CLAIM + 5):
        store.add_occurrence(
            "c1", f"sess-{i}", f"ev-{i}",
            now=base + timedelta(minutes=i),
        )
    c = store.get("c1")
    assert c is not None
    # Cap holds:
    assert len(c.occurrences) == MAX_OCCURRENCES_PER_CLAIM
    # Oldest dropped, newest kept:
    seen_uuids = {o.session_uuid for o in c.occurrences}
    assert "sess-0" not in seen_uuids
    assert "sess-1" not in seen_uuids
    assert "sess-2" not in seen_uuids
    assert "sess-3" not in seen_uuids
    assert "sess-4" not in seen_uuids
    assert f"sess-{MAX_OCCURRENCES_PER_CLAIM + 4}" in seen_uuids
    # And the last_seen advanced to the most recent:
    assert c.last_seen == base + timedelta(minutes=MAX_OCCURRENCES_PER_CLAIM + 4)


def test_cap_does_not_break_eligibility_for_in_window_sessions(store_path):
    """A claim that's been emitted >cap times across many distinct
    sessions still hits the threshold — the cap doesn't drop ALL
    past sessions, just the oldest ones."""
    from vexis_agent.core.user_candidates import MAX_OCCURRENCES_PER_CLAIM
    store = UserCandidateStore(store_path)
    base = _utc(year=2026, month=5, day=3, hour=10)
    for i in range(MAX_OCCURRENCES_PER_CLAIM + 5):
        store.add_occurrence(
            "c1", f"sess-{i}", "ev",
            now=base + timedelta(minutes=i),
        )
    # The newest occurrences span >= 2 distinct sessions, so the
    # claim should still be promotion-eligible:
    assert store.eligible_for_promotion(
        "c1", now=base + timedelta(minutes=MAX_OCCURRENCES_PER_CLAIM + 5)
    ) is True
