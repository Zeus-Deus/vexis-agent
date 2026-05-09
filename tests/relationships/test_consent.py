"""Day 2: ConsentToken mint + verify + person_slug match + fact_ids
match. Plus the R1 unit test ``test_no_token_no_write``."""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.core.memory import MemoryStore
from vexis_agent.core.relationships.consent import (
    ConsentError,
    PendingTokens,
    derive_fact_ids,
    mint,
    verify_for_promotion,
)


def _mint_sample(facts: list[str]):
    return mint(
        session_uuid="sess-1",
        turn_index=1,
        classifier_verdict="ADD",
        person_slug="sarah",
        facts=facts,
    )


def test_mint_happy_path():
    t = _mint_sample(["likes mystery novels", "allergic to peanuts"])
    assert t.session_uuid == "sess-1"
    assert t.turn_index == 1
    assert t.person_slug == "sarah"
    assert t.classifier_verdict == "ADD"
    assert len(t.fact_ids) == 2


def test_mint_rejects_invalid_inputs():
    with pytest.raises(ConsentError):
        mint(
            session_uuid="",
            turn_index=1,
            classifier_verdict="ADD",
            person_slug="x",
            facts=["a"],
        )
    with pytest.raises(ConsentError):
        mint(
            session_uuid="s",
            turn_index=-1,
            classifier_verdict="ADD",
            person_slug="x",
            facts=["a"],
        )
    with pytest.raises(ConsentError):
        mint(
            session_uuid="s",
            turn_index=1,
            classifier_verdict="MAYBE",  # not in allowed set
            person_slug="x",
            facts=["a"],
        )
    with pytest.raises(ConsentError):
        mint(
            session_uuid="s",
            turn_index=1,
            classifier_verdict="ADD",
            person_slug="",
            facts=["a"],
        )
    with pytest.raises(ConsentError):
        mint(
            session_uuid="s",
            turn_index=1,
            classifier_verdict="ADD",
            person_slug="x",
            facts=[],
        )


def test_verify_for_promotion_happy_path():
    t = _mint_sample(["likes mystery novels", "allergic to peanuts"])
    # Same facts, full set: passes.
    verify_for_promotion(
        t, person_slug="sarah",
        facts=["likes mystery novels", "allergic to peanuts"],
    )
    # Subset of covered facts: passes (token covers MORE, requested LESS).
    verify_for_promotion(
        t, person_slug="sarah",
        facts=["likes mystery novels"],
    )


def test_verify_for_promotion_no_token_raises():
    with pytest.raises(ConsentError, match="no consent token"):
        verify_for_promotion(None, person_slug="sarah", facts=["x"])


def test_verify_for_promotion_person_slug_mismatch():
    t = _mint_sample(["x"])
    with pytest.raises(ConsentError, match="person_slug mismatch"):
        verify_for_promotion(
            t, person_slug="marco", facts=["x"],
        )


def test_verify_for_promotion_fact_ids_mismatch():
    """Tampered shadow file: someone added a fact the original
    consent didn't cover. verify_for_promotion fails fast."""
    t = _mint_sample(["likes mystery novels"])
    with pytest.raises(ConsentError, match="does not cover"):
        verify_for_promotion(
            t, person_slug="sarah",
            facts=["likes mystery novels", "is HIV positive"],
        )


def test_pending_tokens_registry_lifecycle():
    reg = PendingTokens()
    assert len(reg) == 0
    t1 = _mint_sample(["a"])
    reg.add(t1)
    assert len(reg) == 1
    got = reg.get(session_uuid="sess-1", turn_index=1, person_slug="sarah")
    assert got is t1
    consumed = reg.consume(session_uuid="sess-1", turn_index=1, person_slug="sarah")
    assert consumed is t1
    assert reg.get(session_uuid="sess-1", turn_index=1, person_slug="sarah") is None


def test_derive_fact_ids_is_deterministic():
    a = derive_fact_ids(["x", "y", "z"])
    b = derive_fact_ids(["x", "y", "z"])
    assert a == b
    # Whitespace is normalized.
    c = derive_fact_ids(["  x  ", "y", "z"])
    assert a == c


def test_token_repr_does_not_leak_facts():
    t = _mint_sample(["secret diagnosis", "another secret"])
    s = repr(t)
    assert "secret" not in s
    assert "n_facts=2" in s


# --------------------------------------------------------------------
# R1: MemoryStore.add(target="relationships") refuses without a token.
# --------------------------------------------------------------------


def test_no_token_no_write(tmp_path: Path):
    """The literal R1 test from the Day 2 prompt: a direct call to
    MemoryStore.add(target="relationships", ...) must raise.

    Rationale: relationships writes need an explicit ConsentToken,
    which MemoryStore's bullet-text content API cannot carry. The
    PermissionError at the entry point is the signal that the wrong
    write surface was used; legitimate writes go through
    RelationshipsStore via RelationshipsCurator.
    """
    store = MemoryStore(tmp_path)
    with pytest.raises(PermissionError, match="ConsentToken"):
        store.add(target="relationships", content="anything")
