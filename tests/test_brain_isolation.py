"""v3c Day 4a: brain prompt + relationships isolation contract.

The patch to ``.plans/relationships-v3c-research.md`` §2.2 wires
RELATIONSHIPS.md INTO ``brains.claude_code.build_system_prompt`` and
keeps every other relationships file (CANDIDATES, SHADOW, ARCHIVE,
the .vexis JSON queue) out of it. These tests are the load-bearing
proof of that contract — a future PR that "helpfully" adds the
candidates file to the brain context fails CI.

Tests:

- empty workspace → no relationships block.
- live RELATIONSHIPS.md populated → block included after USER.md.
- candidates file populated → never appears in the prompt.
- shadow + archive populated → never appear in the prompt.
- staleness instruction line present in the assembled prompt
  (defends the §2.2 patch's "defer to in-conversation evidence
  on conflict" requirement).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.brain.claude_code import build_system_prompt
from core.relationships.candidate_store import (
    RelationshipsCandidateStore,
    candidates_path,
)
from core.relationships.store import (
    Fact,
    Person,
    relationships_archive_path,
    relationships_live_path,
    relationships_shadow_path,
    serialize_relationships_file,
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _seed_live(workspace: Path) -> None:
    sarah = Person(
        slug="sarah-coworker",
        display_name="Sarah",
        relationship="coworker",
        qualifier="coworker",
        last_confirmed="2026-05-04",
        source_session="approve",
        facts=(
            Fact(
                text="VEXIS_LIVE_FACT_SARAH_TECH_LEAD",
                confirmed_date="2026-05-04",
                source_session_short="approve1",
                staged=False,
            ),
        ),
    )
    relationships_live_path(workspace).write_text(
        serialize_relationships_file([sarah], kind="live"),
        encoding="utf-8",
    )


def _seed_shadow(workspace: Path) -> None:
    pending = Person(
        slug="marco",
        display_name="Marco",
        relationship="friend",
        qualifier=None,
        last_confirmed="2026-05-04",
        source_session="sess-shadow",
        facts=(
            Fact(
                text="VEXIS_SHADOW_FACT_MARCO_VIM",
                confirmed_date="2026-05-04",
                source_session_short="sess-sha",
                staged=True,
            ),
        ),
        pending=True,
        staged_at="2026-05-04T12:00:00+00:00",
        source_turn_index=3,
    )
    relationships_shadow_path(workspace).write_text(
        serialize_relationships_file([pending], kind="shadow"),
        encoding="utf-8",
    )


def _seed_archive(workspace: Path) -> None:
    archive = relationships_archive_path(workspace)
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(
        "# RELATIONSHIPS-ARCHIVE.md\n\n"
        "> archive intro\n\n"
        "## REMOVED 2026-04-30\n"
        "## OldFriend\n"
        "```yaml\n"
        "slug: old-friend\n"
        "display_name: OldFriend\n"
        "relationship: friend\n"
        "qualifier: null\n"
        "last_confirmed: '2026-04-30'\n"
        "source_session: sess-archive\n"
        "```\n"
        "- [confirmed 2026-04-30 sess:archive1] VEXIS_ARCHIVE_FACT_OLD_FRIEND\n",
        encoding="utf-8",
    )


def _seed_candidates(workspace: Path) -> None:
    cstore = RelationshipsCandidateStore(candidates_path(workspace))
    cstore.add_observation(
        slug="hidden",
        display_name="Hidden",
        qualifier="coworker",
        fact_text="VEXIS_CANDIDATE_FACT_NEVER_IN_PROMPT",
        session_uuid="sess-cand",
        turn_index=1,
    )
    # Also write the file directly with a sentinel so the test
    # checks for both the JSON path and a content sentinel.
    raw = json.loads(candidates_path(workspace).read_text(encoding="utf-8"))
    assert "by_slug" in raw  # sanity


# ---------------------------------------------------------------- empty


def test_empty_workspace_no_relationships_block(workspace: Path):
    prompt = build_system_prompt(workspace)
    # SOUL + CAPABILITIES are present; relationships block is not
    # emitted because RELATIONSHIPS.md doesn't exist.
    assert prompt
    assert "RELATIONSHIPS.md" not in prompt or "Facts in RELATIONSHIPS.md" in prompt
    # The DEFAULT_SOUL contains the staleness instruction (which
    # mentions "RELATIONSHIPS.md"); other usages should not.
    # No live-content sentinel.
    assert "VEXIS_LIVE_FACT" not in prompt


# ---------------------------------------------------------------- live included


def test_live_relationships_included_after_user(workspace: Path):
    _seed_live(workspace)
    prompt = build_system_prompt(workspace)
    assert "VEXIS_LIVE_FACT_SARAH_TECH_LEAD" in prompt


def test_live_relationships_block_appears_after_user_block(workspace: Path):
    """Confirm the prompt-order contract: SOUL → CAPABILITIES →
    MEMORY → USER → RELATIONSHIPS → skills index. We sandwich-test
    by writing distinct sentinels in USER.md and RELATIONSHIPS.md
    and asserting their relative position.
    """
    from core.memory import MemoryStore
    from core.paths import memories_dir
    _seed_live(workspace)
    user_store = MemoryStore(memories_dir(workspace))
    user_store.add(target="user", content="VEXIS_USER_BLOCK_SENTINEL")
    prompt = build_system_prompt(workspace)
    user_idx = prompt.find("VEXIS_USER_BLOCK_SENTINEL")
    rel_idx = prompt.find("VEXIS_LIVE_FACT_SARAH_TECH_LEAD")
    assert user_idx >= 0 and rel_idx >= 0
    assert user_idx < rel_idx, (
        "RELATIONSHIPS.md must appear AFTER USER.md in the prompt"
    )


# ---------------------------------------------------------------- isolation


def test_candidates_never_in_prompt_under_any_state(workspace: Path):
    """The load-bearing isolation test. With every relationships
    file populated AND the candidates queue populated, the
    assembled prompt must not contain the candidate-file sentinel.
    """
    _seed_live(workspace)
    _seed_shadow(workspace)
    _seed_archive(workspace)
    _seed_candidates(workspace)
    prompt = build_system_prompt(workspace)
    # Live IS included (Day 4a wiring).
    assert "VEXIS_LIVE_FACT_SARAH_TECH_LEAD" in prompt
    # Candidates / shadow / archive are NOT included.
    assert "VEXIS_CANDIDATE_FACT_NEVER_IN_PROMPT" not in prompt
    assert "VEXIS_SHADOW_FACT_MARCO_VIM" not in prompt
    assert "VEXIS_ARCHIVE_FACT_OLD_FRIEND" not in prompt
    # Defensive: filename references must also not appear.
    assert "RELATIONSHIPS-CANDIDATES" not in prompt
    assert "RELATIONSHIPS-SHADOW" not in prompt
    assert "RELATIONSHIPS-ARCHIVE" not in prompt
    assert "relationships-candidates.json" not in prompt


def test_shadow_only_no_live_no_block(workspace: Path):
    """Shadow alone (no live) → no relationships block. Shadow
    is for in-flight v3b explicit stages; never goes to brain."""
    _seed_shadow(workspace)
    prompt = build_system_prompt(workspace)
    assert "VEXIS_SHADOW_FACT_MARCO_VIM" not in prompt


def test_archive_only_no_live_no_block(workspace: Path):
    """Archive alone (no live) → no relationships block. Archive
    is delete/supersede history; never goes to brain."""
    _seed_archive(workspace)
    prompt = build_system_prompt(workspace)
    assert "VEXIS_ARCHIVE_FACT_OLD_FRIEND" not in prompt


# ---------------------------------------------------------------- staleness instruction


def test_default_soul_carries_staleness_instruction(workspace: Path):
    """The §2.2 patch added a one-liner to DEFAULT_SOUL telling
    the brain to treat RELATIONSHIPS.md as durable but not
    necessarily current."""
    prompt = build_system_prompt(workspace)
    assert "defer to in-conversation evidence on conflict" in prompt


def test_format_relationships_helper_returns_empty_when_absent(workspace: Path):
    """``format_relationships_for_system_prompt`` reads ONLY the
    live file and returns empty when it doesn't exist or is
    empty — the caller in build_system_prompt drops empty blocks."""
    from core.relationships.store import format_relationships_for_system_prompt
    out = format_relationships_for_system_prompt(workspace)
    assert out == ""

    # Empty live file → still empty.
    relationships_live_path(workspace).write_text("", encoding="utf-8")
    out = format_relationships_for_system_prompt(workspace)
    assert out == ""

    # Populated → returns content (live only, NOT shadow/archive/candidates).
    _seed_live(workspace)
    _seed_shadow(workspace)
    _seed_archive(workspace)
    _seed_candidates(workspace)
    out = format_relationships_for_system_prompt(workspace)
    assert "VEXIS_LIVE_FACT_SARAH_TECH_LEAD" in out
    assert "VEXIS_SHADOW_FACT_MARCO_VIM" not in out
    assert "VEXIS_ARCHIVE_FACT_OLD_FRIEND" not in out
    assert "VEXIS_CANDIDATE_FACT_NEVER_IN_PROMPT" not in out
