"""System-prompt header injection — generic add-on header pipeline.

Phase B moved the codemux-specific "Active Codemux work: N" line
out of WatcherController and into the codemux add-on's
``register_system_prompt_block`` registration. The brain's prompt
builder reads from ``addon_runtime.header_blocks()`` (via the
``extra_prompt_blocks`` callback main.py wires up) — no
watcher-specific code in core.

What's pinned here:

  * The generic provider pipeline (Brain.extra_prompt_blocks is
    a list-of-strings callable) honours add-on-supplied blocks.
  * The codemux add-on's header provider returns the same
    "Active Codemux work: N workspaces" string the hardcoded
    path used to emit, with the same one-line guarantee.
  * Mid-session registry changes still don't perturb the cached
    system prompt for the session that already saw it (cache
    contract preserved from the old code path).

The watcher source registration uses a fake source so we don't
need a real CodemuxMcpClient — the header provider only reads
the registry, not the source.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

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
    )


def _make_brain(
    workspace: Path,
    tmp_path: Path,
    controller: WatcherController | None,
) -> ClaudeCodeBrain:
    """Build a brain whose ``extra_prompt_blocks`` runs the codemux
    add-on's header provider against the supplied controller. ``None``
    simulates the no-controller / no-addon path."""
    sessions = SessionStore(state_path=tmp_path / "session.json")
    extras = None
    if controller is not None:
        from vexis_agent.addons.codemux.header import (
            build_codemux_header_provider,
        )
        ctx = MagicMock()
        ctx.get_service.return_value = controller
        provider = build_codemux_header_provider(ctx)

        def _extras() -> list[str]:
            block = provider()
            return [block] if block else []
        extras = _extras
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
    asyncio.run(controller.register_agent(
        name="ws-a", source_type="fake", identifier="x",
        agent_kind="claude-code", chat_id=1,
    ))
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
    """The no-codemux-add-on fast path: no provider wired, the
    header line is absent. CAPABILITIES.md explains the feature in
    prose regardless — the watcher gate guards STATE INJECTION, not
    the model's awareness that the tool exists.
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
