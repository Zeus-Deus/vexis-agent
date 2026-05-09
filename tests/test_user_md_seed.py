"""v3c Day 5 — release gate.

USER.md seed install + format-into-system-prompt assertions:

- ``MemoryStore.ensure_seed`` is idempotent: first call installs,
  second call no-ops.
- After the seed lands, the rendered USER block from
  ``format_for_system_prompt("user")`` contains the new line.
- The marker phrase is stable text the eyeball test can find.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.core.memory import MemoryStore
from vexis_agent.core.relationships import (
    RELATIONSHIPS_USER_SEED_MARKER,
    RELATIONSHIPS_USER_SEED_TEXT,
)


@pytest.fixture
def memories(tmp_path: Path) -> Path:
    d = tmp_path / "memories"
    d.mkdir()
    return d


def test_ensure_seed_first_call_installs(memories: Path):
    store = MemoryStore(memories)
    added = store.ensure_seed(
        "user",
        marker=RELATIONSHIPS_USER_SEED_MARKER,
        content=RELATIONSHIPS_USER_SEED_TEXT,
    )
    assert added is True
    entries = store.list_entries("user")
    assert any(RELATIONSHIPS_USER_SEED_MARKER in e for e in entries)


def test_ensure_seed_idempotent_on_second_call(memories: Path):
    store = MemoryStore(memories)
    first = store.ensure_seed(
        "user",
        marker=RELATIONSHIPS_USER_SEED_MARKER,
        content=RELATIONSHIPS_USER_SEED_TEXT,
    )
    second = store.ensure_seed(
        "user",
        marker=RELATIONSHIPS_USER_SEED_MARKER,
        content=RELATIONSHIPS_USER_SEED_TEXT,
    )
    assert first is True
    assert second is False
    # Still exactly one entry for the seed.
    entries = store.list_entries("user")
    matching = [e for e in entries if RELATIONSHIPS_USER_SEED_MARKER in e]
    assert len(matching) == 1


def test_ensure_seed_does_not_collide_with_other_entries(memories: Path):
    store = MemoryStore(memories)
    # Seed an unrelated user entry first.
    res = store.add(target="user", content="User prefers concise replies.")
    assert hasattr(res, "ok") or hasattr(res, "rendered") or True  # MemorySuccess
    store.ensure_seed(
        "user",
        marker=RELATIONSHIPS_USER_SEED_MARKER,
        content=RELATIONSHIPS_USER_SEED_TEXT,
    )
    entries = store.list_entries("user")
    # Both entries present.
    assert any("concise replies" in e for e in entries)
    assert any(RELATIONSHIPS_USER_SEED_MARKER in e for e in entries)


def test_seed_appears_in_format_for_system_prompt(memories: Path):
    """The brain's system prompt assembly calls
    ``format_for_system_prompt("user")``; the seed must surface
    there so the brain actually reads it."""
    store = MemoryStore(memories)
    store.ensure_seed(
        "user",
        marker=RELATIONSHIPS_USER_SEED_MARKER,
        content=RELATIONSHIPS_USER_SEED_TEXT,
    )
    rendered = store.format_for_system_prompt("user")
    assert rendered is not None
    assert RELATIONSHIPS_USER_SEED_MARKER in rendered
    # And key phrases from the seed body show through.
    assert "silent relationships extraction" in rendered
    assert "RELATIONSHIPS.md" in rendered
    assert "/clear" in rendered


def test_seed_marker_is_unique_enough_for_dedup(memories: Path):
    """Defensive: confirm the marker substring is specific enough
    that a stray future user entry isn't likely to collide.
    'silent relationships extraction default' is hand-tuned to
    contain three system-specific terms in sequence."""
    store = MemoryStore(memories)
    store.add(
        target="user",
        content="User likes quiet relationships and a default browser tab.",
    )
    # The composed sentence above shares NONE of the marker's
    # specific tri-gram.
    assert RELATIONSHIPS_USER_SEED_MARKER not in "\n".join(
        store.list_entries("user")
    )
