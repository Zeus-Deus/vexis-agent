"""Verify the system-prompt cache is keyed by session UUID and stays
byte-stable across turns of one session, but rebuilds when the UUID
rotates. This is the prefix-cache defense; if it regresses,
Anthropic's prompt cache silently stops hitting and tokens cost ~2x.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brains.claude_code import ClaudeCodeBrain
from core.memory import MemoryStore
from core.paths import memories_dir
from core.running_tasks import RunningTasks
from core.sessions import SessionStore


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "skills").mkdir()
    (ws / "SOUL.md").write_text("# Test SOUL\n\nshort.\n", encoding="utf-8")
    return ws


@pytest.fixture
def brain(workspace: Path, tmp_path: Path) -> ClaudeCodeBrain:
    sessions = SessionStore(state_path=tmp_path / "session.json")
    return ClaudeCodeBrain(
        workspace=workspace, session=sessions, running_tasks=RunningTasks()
    )


def test_snapshot_byte_stable_across_calls_same_uuid(
    brain: ClaudeCodeBrain, workspace: Path
):
    uuid = "abc-123"
    first = brain._system_prompt_for(uuid)
    second = brain._system_prompt_for(uuid)
    assert first == second
    assert first is second  # cached object identity


def test_snapshot_freezes_against_disk_writes(
    brain: ClaudeCodeBrain, workspace: Path
):
    """Writing to MEMORY.md mid-session must NOT change the cached
    snapshot. The brain reuses the same string for every turn of one
    session, so prefix caching keeps hitting."""
    uuid = "abc-123"
    initial = brain._system_prompt_for(uuid)
    assert "MEMORY (your personal notes)" not in initial  # no entries yet

    # Mutate memory mid-session
    store = MemoryStore(memories_dir(workspace))
    store.add("memory", "ZX9-test-marker-frozen-snapshot")

    # Same UUID — must return the cached prompt unchanged
    after_write = brain._system_prompt_for(uuid)
    assert after_write == initial
    assert "ZX9-test-marker" not in after_write


def test_snapshot_rebuilds_when_uuid_rotates(
    brain: ClaudeCodeBrain, workspace: Path
):
    """When the user runs /clear or /new, the session UUID rotates
    and the brain rebuilds the snapshot from disk. New memory writes
    show up in the next session's prompt."""
    store = MemoryStore(memories_dir(workspace))
    first = brain._system_prompt_for("session-1")
    assert "ZX9-test-marker" not in first

    store.add("memory", "ZX9-test-marker-frozen-snapshot")
    rotated = brain._system_prompt_for("session-2")
    assert "ZX9-test-marker" in rotated
    assert rotated != first


def test_snapshot_cache_evicts_oldest_after_max(brain: ClaudeCodeBrain):
    from brains.claude_code import _SYSTEM_PROMPT_CACHE_MAX

    # Fill the cache one over capacity. The oldest entry should be
    # evicted; the newest one must be present.
    for i in range(_SYSTEM_PROMPT_CACHE_MAX + 1):
        brain._system_prompt_for(f"sess-{i:03d}")
    assert "sess-000" not in brain._system_prompt_cache
    assert f"sess-{_SYSTEM_PROMPT_CACHE_MAX:03d}" in brain._system_prompt_cache
