"""Day 2: parser + serializer round-trip + RelationshipsStore ops."""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.core.relationships.consent import ConsentError, mint
from vexis_agent.core.relationships.store import (
    Fact,
    Person,
    RelationshipsStore,
    parse_relationships_file,
    relationships_live_path,
    relationships_shadow_path,
    serialize_relationships_file,
)


def _mint_for(person: Person):
    """Helper: mint a token covering the given person's facts."""
    return mint(
        session_uuid=person.source_session or "sess-stub",
        turn_index=person.source_turn_index or 1,
        classifier_verdict="ADD",
        person_slug=person.slug,
        facts=[f.text for f in person.facts],
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "ws"


def _sample_person(slug: str = "sarah", staged: bool = False) -> Person:
    return Person(
        slug=slug,
        display_name="Sarah",
        relationship="friend",
        qualifier=("friend" if slug != "sarah" else None),
        last_confirmed="2026-05-04",
        source_session="abcdef12-1234-5678-90ab-cdef01234567",
        facts=(
            Fact(
                text="likes mystery novels",
                confirmed_date="2026-05-04",
                source_session_short="abcdef12",
                staged=staged,
            ),
            Fact(
                text="allergic to peanuts",
                confirmed_date="2026-05-04",
                source_session_short="abcdef12",
                staged=staged,
            ),
        ),
        pending=staged,
        staged_at=("2026-05-04T12:00:00+00:00" if staged else None),
        source_turn_index=(1 if staged else None),
    )


def test_serialize_then_parse_round_trip_live():
    p = _sample_person()
    text = serialize_relationships_file([p], kind="live")
    parsed = parse_relationships_file(text)
    assert len(parsed) == 1
    assert parsed[0].slug == "sarah"
    assert parsed[0].display_name == "Sarah"
    assert parsed[0].relationship == "friend"
    assert parsed[0].pending is False
    assert len(parsed[0].facts) == 2
    assert parsed[0].facts[0].text == "likes mystery novels"
    assert parsed[0].facts[0].staged is False


def test_serialize_then_parse_round_trip_shadow():
    p = _sample_person(staged=True)
    text = serialize_relationships_file([p], kind="shadow")
    parsed = parse_relationships_file(text)
    assert len(parsed) == 1
    assert parsed[0].pending is True
    assert parsed[0].staged_at == "2026-05-04T12:00:00+00:00"
    assert parsed[0].source_turn_index == 1
    assert all(f.staged for f in parsed[0].facts)


def test_two_people_same_first_name_different_slugs(workspace: Path):
    sarah_friend = Person(
        slug="sarah",
        display_name="Sarah",
        relationship="friend",
        qualifier="friend",
        last_confirmed="2026-05-04",
        source_session="s1",
        facts=(Fact("loves jazz", "2026-05-04", "s1aaaaaa"),),
    )
    sarah_coworker = Person(
        slug="sarah-coworker",
        display_name="Sarah",
        relationship="coworker",
        qualifier="coworker",
        last_confirmed="2026-05-04",
        source_session="s2",
        facts=(Fact("uses vim", "2026-05-04", "s2bbbbbb"),),
    )
    text = serialize_relationships_file(
        [sarah_friend, sarah_coworker], kind="live"
    )
    parsed = parse_relationships_file(text)
    slugs = [p.slug for p in parsed]
    assert slugs == ["sarah", "sarah-coworker"]


def test_store_stage_writes_to_shadow(workspace: Path):
    store = RelationshipsStore(workspace)
    p = _sample_person(staged=True)
    res = store.stage(p, token=_mint_for(p))
    assert res.ok
    assert relationships_shadow_path(workspace).exists()
    assert not relationships_live_path(workspace).exists()
    shadow_people = store.list_shadow()
    assert len(shadow_people) == 1
    assert shadow_people[0].slug == "sarah"
    assert shadow_people[0].pending is True


def test_store_promote_moves_shadow_to_live_and_flips_pins(workspace: Path):
    store = RelationshipsStore(workspace)
    p = _sample_person(staged=True)
    tok = _mint_for(p)
    store.stage(p, token=tok)
    res = store.promote("sarah", token=tok)
    assert res.ok
    # Shadow now empty for sarah, live has it.
    assert store.get_shadow("sarah") is None
    live = store.get_live("sarah")
    assert live is not None
    assert live.pending is False
    assert all(f.staged is False for f in live.facts)


def test_store_promote_merges_into_existing_live_person(workspace: Path):
    store = RelationshipsStore(workspace)
    # Pre-existing live entry.
    pre = Person(
        slug="sarah",
        display_name="Sarah",
        relationship="friend",
        qualifier="friend",
        last_confirmed="2026-04-01",
        source_session="s-old",
        facts=(Fact("met at conference", "2026-04-01", "s-oldaaa"),),
    )
    from vexis_agent.core.relationships.store import _write_people
    _write_people(
        relationships_live_path(workspace), [pre], kind="live"
    )
    # Now stage + promote a new shadow entry with two new facts.
    p = _sample_person(staged=True)
    tok = _mint_for(p)
    store.stage(p, token=tok)
    res = store.promote("sarah", token=tok)
    assert res.ok
    live = store.get_live("sarah")
    assert live is not None
    # 1 pre-existing + 2 promoted.
    assert len(live.facts) == 3


def test_store_drop_shadow(workspace: Path):
    store = RelationshipsStore(workspace)
    p = _sample_person(staged=True)
    store.stage(p, token=_mint_for(p))
    res = store.drop_shadow("sarah", reason="test")
    assert res.ok
    assert store.get_shadow("sarah") is None


def test_store_drop_missing_shadow_returns_not_ok(workspace: Path):
    store = RelationshipsStore(workspace)
    res = store.drop_shadow("nobody", reason="test")
    assert not res.ok


def test_parser_skips_malformed_yaml_block():
    text = """# RELATIONSHIPS.md

## Bad
```yaml
not: valid: yaml: at: all
```
- [confirmed 2026-05-04 sess:bad12345] orphan fact

## Good
```yaml
slug: good
display_name: Good
relationship: friend
qualifier: null
last_confirmed: 2026-05-04
source_session: g123
```
- [confirmed 2026-05-04 sess:g1234567] real fact
"""
    parsed = parse_relationships_file(text)
    # Bad section was skipped; only Good survives.
    assert len(parsed) == 1
    assert parsed[0].slug == "good"


def test_parser_handles_empty_input():
    assert parse_relationships_file("") == []


# --------------------------------------------------------------------
# Defense-in-depth: store re-verifies tokens (Ask 3 from Day 2 review).
# --------------------------------------------------------------------


def test_store_write_requires_token(workspace: Path):
    """RelationshipsStore.stage / .promote refuse to write without
    a verified ConsentToken, even if the curator caller forgets to
    verify upstream. Mirrors the R1 pattern from
    MemoryStore.add(target='relationships', ...).

    Asserts the signature won't accept a positional-only call AND
    that ConsentError (PermissionError subclass) is raised on
    missing / mismatched tokens at the store level."""
    store = RelationshipsStore(workspace)
    p = _sample_person(staged=True)

    # Missing token kwarg — TypeError from the kw-only signature.
    with pytest.raises(TypeError):
        store.stage(p)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        store.promote("sarah")  # type: ignore[call-arg]

    # token=None — ConsentError from verify_for_promotion.
    with pytest.raises(ConsentError, match="no consent token"):
        store.stage(p, token=None)


def test_store_promote_verifies_token_against_shadow_facts(
    workspace: Path,
):
    """Token issued for one set of facts cannot promote a tampered
    shadow entry that contains a fact the token doesn't cover."""
    store = RelationshipsStore(workspace)
    # Stage with the legitimate token.
    p = _sample_person(staged=True)
    legitimate = _mint_for(p)
    store.stage(p, token=legitimate)
    # Now manually tamper the shadow file to add an extra fact.
    tampered = Person(
        slug="sarah",
        display_name="Sarah",
        relationship="friend",
        qualifier=None,
        last_confirmed="2026-05-04",
        source_session=p.source_session,
        facts=p.facts + (
            Fact(
                text="extra hostile fact",
                confirmed_date="2026-05-04",
                source_session_short="abcdef12",
                staged=True,
            ),
        ),
        pending=True,
        staged_at=p.staged_at,
        source_turn_index=p.source_turn_index,
    )
    from vexis_agent.core.relationships.store import _write_people
    _write_people(
        relationships_shadow_path(workspace), [tampered], kind="shadow",
    )
    with pytest.raises(ConsentError, match="does not cover"):
        store.promote("sarah", token=legitimate)


def test_store_stage_rejects_wrong_person_slug(workspace: Path):
    """A token minted for slug A cannot stage a person at slug B."""
    store = RelationshipsStore(workspace)
    p = _sample_person(staged=True)  # slug=sarah
    wrong_token = mint(
        session_uuid=p.source_session,
        turn_index=p.source_turn_index or 1,
        classifier_verdict="ADD",
        person_slug="marco",  # mismatch
        facts=[f.text for f in p.facts],
    )
    with pytest.raises(ConsentError, match="person_slug mismatch"):
        store.stage(p, token=wrong_token)


def test_update_shadow_flag_does_not_require_token(workspace: Path):
    """The coherence_block flag is a curator-internal diagnostic;
    setting it is not a write of new content. Token check does
    not apply (would otherwise stop the missing-transcript guard
    in RelationshipsCurator from working at all)."""
    store = RelationshipsStore(workspace)
    p = _sample_person(staged=True)
    store.stage(p, token=_mint_for(p))
    # No token kwarg — should succeed.
    res = store.update_shadow_flag("sarah", coherence_block="missing_transcript")
    assert res.ok
    refetched = store.get_shadow("sarah")
    assert refetched is not None
    assert refetched.coherence_block == "missing_transcript"


def test_coherence_block_field_round_trips(workspace: Path):
    """Parser + serializer preserve the coherence_block YAML field."""
    p = Person(
        slug="anna",
        display_name="Anna",
        relationship="friend",
        qualifier="friend",
        last_confirmed="2026-05-04",
        source_session="telegram-chat-99",
        facts=(Fact("likes hiking", "2026-05-04", "telegram", staged=True),),
        pending=True,
        staged_at="2026-05-04T12:00:00Z",
        source_turn_index=4,
        coherence_block="missing_transcript",
    )
    text = serialize_relationships_file([p], kind="shadow")
    assert "coherence_block: missing_transcript" in text
    parsed = parse_relationships_file(text)
    assert len(parsed) == 1
    assert parsed[0].coherence_block == "missing_transcript"


def test_supersede_provenance_round_trip():
    """Day 3 reserved field — make sure parser tolerates it."""
    f = Fact(
        text="moved to Berlin",
        confirmed_date="2026-05-04",
        source_session_short="abcdef12",
        superseded_by_date="2026-06-01",
        superseded_by_session="newsess1",
    )
    rendered = f.render()
    assert "[superseded 2026-06-01 by sess:newsess1]" in rendered
    assert "[confirmed 2026-05-04 sess:abcdef12]" in rendered
