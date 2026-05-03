"""Day 2: parser + serializer round-trip + RelationshipsStore ops."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.relationships.store import (
    Fact,
    Person,
    RelationshipsStore,
    parse_relationships_file,
    relationships_live_path,
    relationships_shadow_path,
    serialize_relationships_file,
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
    res = store.stage(p)
    assert res.ok
    assert relationships_shadow_path(workspace).exists()
    assert not relationships_live_path(workspace).exists()
    shadow_people = store.list_shadow()
    assert len(shadow_people) == 1
    assert shadow_people[0].slug == "sarah"
    assert shadow_people[0].pending is True


def test_store_promote_moves_shadow_to_live_and_flips_pins(workspace: Path):
    store = RelationshipsStore(workspace)
    store.stage(_sample_person(staged=True))
    res = store.promote("sarah")
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
    from core.relationships.store import _write_people
    _write_people(
        relationships_live_path(workspace), [pre], kind="live"
    )
    # Now stage + promote a new shadow entry with two new facts.
    store.stage(_sample_person(staged=True))
    res = store.promote("sarah")
    assert res.ok
    live = store.get_live("sarah")
    assert live is not None
    # 1 pre-existing + 2 promoted.
    assert len(live.facts) == 3


def test_store_drop_shadow(workspace: Path):
    store = RelationshipsStore(workspace)
    store.stage(_sample_person(staged=True))
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
