"""workspace_id → session_id resolution for the Codemux source plugin.

Codemux's MCP exposes pane/session metadata for the *currently active*
workspace only (``workspace_info`` / ``pane_list`` take no arg). So
the watcher's register-time auto-resolution must:

  * Confirm the target workspace IS active via ``app_status``.
  * Pull the focused-pane session id via ``workspace_info``.
  * Refuse with a typed error when the workspace isn't active —
    instead of silently calling ``workspace_open`` (which would yank
    the user's UI focus).

Tests use an in-process fake :class:`CodemuxMcpClient`. We only
exercise the resolver's interaction shape — the live MCP path is
covered by the live e2e script.
"""

from __future__ import annotations

import asyncio

import pytest

from vexis_agent.core.watcher.sources import SourceUnavailable
from vexis_agent.core.watcher.sources.codemux import (
    WorkspaceResolution,
    resolve_workspace_to_session,
)


class _FakeMcp:
    """Stand-in for CodemuxMcpClient with a programmable call() result."""

    def __init__(self, *, active: str, info: dict | None) -> None:
        self._active = active
        self._info = info
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool: str, args: dict):
        self.calls.append((tool, args))
        if tool == "app_status":
            return {"active_workspace_id": self._active}
        if tool == "workspace_info":
            if self._info is None:
                raise RuntimeError("workspace_info not configured")
            return self._info
        raise AssertionError(f"unexpected call {tool!r}")


_OK_INFO = {
    "workspace_id": "workspace-42",
    "cwd": "/home/u/proj",
    "title": "proj",
    "surfaces": [
        {
            "active_pane_id": "pane-7",
            "root": {
                "kind": "terminal",
                "pane_id": "pane-7",
                "session_id": "session-99",
                "title": "Terminal 1",
            },
        },
    ],
}


def test_happy_path_extracts_session_id_and_metadata():
    client = _FakeMcp(active="workspace-42", info=_OK_INFO)
    res = asyncio.run(resolve_workspace_to_session(client, "workspace-42"))
    assert isinstance(res, WorkspaceResolution)
    assert res.session_id == "session-99"
    assert res.workspace_id == "workspace-42"
    assert res.repo_path == "/home/u/proj"
    assert res.title == "proj"
    # Calls in the right order: status first (cheap), info only if active.
    assert [t for t, _ in client.calls] == ["app_status", "workspace_info"]


def test_workspace_not_active_raises_with_actionable_message():
    """Pin: error message names the active workspace AND tells the
    user how to fix it. The CLI's user-facing copy chains off this
    text, so wording drift would silently degrade the UX."""
    client = _FakeMcp(active="workspace-OTHER", info=None)
    with pytest.raises(SourceUnavailable) as exc:
        asyncio.run(resolve_workspace_to_session(client, "workspace-42"))
    msg = str(exc.value).lower()
    assert "not the active codemux workspace" in msg
    assert "workspace-other" in msg
    assert "open the workspace" in msg
    # Did NOT call workspace_info — saved a round trip.
    assert [t for t, _ in client.calls] == ["app_status"]


def test_active_but_no_terminal_session_raises():
    info_no_terminal = {**_OK_INFO, "surfaces": []}
    client = _FakeMcp(active="workspace-42", info=info_no_terminal)
    with pytest.raises(SourceUnavailable) as exc:
        asyncio.run(resolve_workspace_to_session(client, "workspace-42"))
    assert "no terminal session" in str(exc.value).lower()


def test_workspace_info_returns_non_dict_raises():
    client = _FakeMcp(active="workspace-42", info="not a dict")  # type: ignore[arg-type]
    with pytest.raises(SourceUnavailable):
        asyncio.run(resolve_workspace_to_session(client, "workspace-42"))


def test_mcp_call_failure_surfaces_as_source_unavailable():
    from vexis_agent.core.watcher.mcp_client import CodemuxMcpError

    class _Broken:
        async def call(self, tool: str, args: dict):
            raise CodemuxMcpError("synthetic crash")

    with pytest.raises(SourceUnavailable) as exc:
        asyncio.run(resolve_workspace_to_session(_Broken(), "workspace-42"))
    assert "synthetic crash" in str(exc.value)
