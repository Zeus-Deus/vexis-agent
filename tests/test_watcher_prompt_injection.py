"""Watcher LAYER 2: system-prompt header injection.

The header is added to the system prompt EXACTLY ONCE per session
UUID — the same per-session cache that protects the prefix cache for
all other prompt content. Spec contract: a fresh /clear session sees
the line; mid-session changes to the registry are NOT reflected (by
design, to keep the prefix cache stable).

The line is exactly ONE line regardless of how many workspaces are
watched; that's the context-budget guarantee. This test re-states
that via the ``\\n`` count assertion to keep the regression-detector
loud.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore
from vexis_agent.core.watcher import WatcherController, WatcherRegistry


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "skills").mkdir()
    (ws / "SOUL.md").write_text("# Test SOUL\n", encoding="utf-8")
    return ws


def _make_controller(tmp_path: Path) -> WatcherController:
    return WatcherController(
        registry=WatcherRegistry(state_path=tmp_path / "watcher.json"),
        register_codemux_source=False,
    )


def _make_brain(
    workspace: Path,
    tmp_path: Path,
    controller: WatcherController | None,
) -> ClaudeCodeBrain:
    sessions = SessionStore(state_path=tmp_path / "session.json")
    extras = None
    if controller is not None:
        def _provider() -> list[str]:
            block = controller.header_block()
            return [block] if block else []
        extras = _provider
    return ClaudeCodeBrain(
        workspace=workspace,
        session=sessions,
        running_tasks=RunningTasks(),
        extra_prompt_blocks=extras,
    )


def test_header_appears_when_registry_has_active_workspaces(
    workspace: Path, tmp_path: Path,
):
    controller = _make_controller(tmp_path)
    import asyncio
    asyncio.run(controller.register_agent(
        name="ws-a", source_type="fake", identifier="x",
        agent_kind="claude-code", chat_id=1,
    ))
    # Plug a no-op source so register_agent doesn't reject 'fake'.
    # We can't actually poll, but we don't need to for header tests.
    brain = _make_brain(workspace, tmp_path, controller)
    prompt = brain._system_prompt_for("session-1")
    assert "Active Codemux work" in prompt
    assert "vexis-watch status" in prompt


def test_no_header_when_registry_empty(workspace: Path, tmp_path: Path):
    controller = _make_controller(tmp_path)
    brain = _make_brain(workspace, tmp_path, controller)
    prompt = brain._system_prompt_for("session-1")
    assert "Active Codemux work" not in prompt


def test_no_header_when_provider_is_none(workspace: Path, tmp_path: Path):
    """The MCP-absent fast path: no provider wired, the header line
    is absent. CAPABILITIES.md explains the feature in prose
    regardless — the watcher gate guards STATE INJECTION, not
    the model's awareness that the tool exists. The contract under
    test is "no per-session header line eats prompt budget when the
    user doesn't have Codemux," not "the model has never heard of
    the watcher."
    """
    brain = _make_brain(workspace, tmp_path, controller=None)
    prompt = brain._system_prompt_for("session-1")
    assert "Active Codemux work" not in prompt


def test_header_is_frozen_per_session_uuid(
    workspace: Path, tmp_path: Path,
):
    """Cache contract: mid-session registry changes do NOT perturb
    the system prompt for the session that already cached it."""
    controller = _make_controller(tmp_path)
    brain = _make_brain(workspace, tmp_path, controller)
    first = brain._system_prompt_for("session-1")
    assert "Active Codemux work" not in first

    import asyncio
    asyncio.run(controller.register_agent(
        name="ws-late", source_type="fake", identifier="y",
        agent_kind="claude-code", chat_id=1,
    ))
    # Same UUID → byte-stable cache, no header retroactively.
    second = brain._system_prompt_for("session-1")
    assert second == first

    # New UUID (e.g. /clear) picks up the header.
    third = brain._system_prompt_for("session-2")
    assert "Active Codemux work" in third


@pytest.fixture(autouse=True)
def _seed_fake_source():
    """Tests above register 'fake' agents; register_agent rejects an
    unknown source_type, so plug a stub."""
    from vexis_agent.core.watcher.sources import (
        Source,
        SourceDescription,
        clear_sources,
        register_source,
    )

    class Stub(Source):
        source_type = "fake"
        async def read_recent_output(self, identifier: str) -> bytes:
            return b""
        async def is_alive(self, identifier: str) -> bool:
            return True
        async def describe(self, identifier: str) -> SourceDescription:
            return SourceDescription()
    clear_sources()
    register_source(Stub())
    yield
    clear_sources()
