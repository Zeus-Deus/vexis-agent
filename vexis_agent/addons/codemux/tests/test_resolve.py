"""Dialect-aware workspace → terminal-handle resolution for the
Codemux source plugin.

The watcher supports BOTH codemux MCP dialects with autodetect on
first call:

  * **Desktop** ``codemux mcp`` — the Tauri app's in-process MCP.
    Workspaces own panes; ``workspace_info`` (no args) returns the
    *active* workspace's surfaces with a ``session_id`` per pane.
    Resolution: confirm active, extract focused-pane session_id.

  * **Remote** ``codemux-remote mcp`` — the headless daemon.
    Workspaces and terminals are independent entities;
    ``workspace_info({"id": ...})`` returns the workspace's
    worktree path, ``terminal_list`` enumerates daemon-owned PTYs.
    Resolution: match by cwd; refuse on zero / multiple matches.

Tests use an in-process fake :class:`CodemuxMcpClient` keyed on
``app_status`` shape — the same discriminator
``_classify_dialect`` uses in production. Live MCP smoke is
covered separately by the e2e probe in the porting PR.
"""

from __future__ import annotations

import asyncio

import pytest

from vexis_agent.core.watcher.sources import SourceUnavailable
from vexis_agent.addons.codemux.source import (
    DIALECT_DESKTOP,
    DIALECT_REMOTE,
    CodemuxSource,
    WorkspaceResolution,
    _classify_dialect,
    detect_dialect,
    resolve_workspace_to_session,
)


# ---------------------------------------------------------------------------
# Dialect classifier (pure function — no MCP roundtrips)
# ---------------------------------------------------------------------------

def test_classify_dialect_remote_via_host_id_and_mode():
    """The remote daemon's app_status reply is the canonical shape
    captured against codemux-remote 0.7.1."""
    status = {
        "host_id": "ai-node",
        "mode": "headless",
        "started_at": "2026-05-27T22:50:51Z",
        "terminal_count": 0,
        "version": "0.7.1",
        "workspace_count": 0,
    }
    assert _classify_dialect(status) == DIALECT_REMOTE


def test_classify_dialect_desktop_via_active_workspace_id():
    """The desktop MCP's app_status reply names the active workspace
    and focused pane — concepts the headless daemon doesn't have."""
    status = {
        "active_workspace_id": "workspace-1066",
        "app_version": "0.7.1",
        "focused_pane_id": "pane-1069",
        "protocol_version": 1,
        "socket_path": "/run/user/1000/codemux.sock",
        "workspaces": [],
    }
    assert _classify_dialect(status) == DIALECT_DESKTOP


def test_classify_dialect_unknown_shape_defaults_to_remote():
    """Pragmatic default: today's mcp-servers.yaml template points
    at codemux-remote. A misroute against remote produces a clear
    not_found error; a misroute against desktop silently returns
    the wrong workspace's data."""
    assert _classify_dialect({}) == DIALECT_REMOTE
    assert _classify_dialect("not a dict") == DIALECT_REMOTE
    assert _classify_dialect(None) == DIALECT_REMOTE


# ---------------------------------------------------------------------------
# Fake MCP infrastructure
# ---------------------------------------------------------------------------

class _DesktopMcp:
    """Stand-in for CodemuxMcpClient speaking the DESKTOP dialect.

    Configure with ``active`` (the active workspace id reported by
    app_status) and ``info`` (the workspace_info reply, or an
    Exception to raise). ``app_status`` is reused as both the
    initial dialect probe and the resolver's active-workspace check.
    """

    def __init__(self, *, active: str, info) -> None:
        self._active = active
        self._info = info
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool: str, args: dict):
        self.calls.append((tool, args))
        if tool == "app_status":
            return {
                "active_workspace_id": self._active,
                "app_version": "0.7.1",
                "focused_pane_id": "pane-7",
                "protocol_version": 1,
                "socket_path": "/tmp/codemux.sock",
                "workspaces": [],
            }
        if tool == "workspace_info":
            if isinstance(self._info, Exception):
                raise self._info
            return self._info
        raise AssertionError(f"unexpected desktop call {tool!r}")


class _RemoteMcp:
    """Stand-in for CodemuxMcpClient speaking the REMOTE dialect."""

    def __init__(self, *, info, terminals) -> None:
        self._info = info
        self._terminals = terminals
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool: str, args: dict):
        self.calls.append((tool, args))
        if tool == "app_status":
            return {
                "host_id": "ai-node",
                "mode": "headless",
                "started_at": "2026-05-27T22:50:51Z",
                "terminal_count": 0,
                "version": "0.7.1",
                "workspace_count": 0,
            }
        if tool == "workspace_info":
            if isinstance(self._info, Exception):
                raise self._info
            return self._info
        if tool == "terminal_list":
            if isinstance(self._terminals, Exception):
                raise self._terminals
            return {"terminals": self._terminals}
        raise AssertionError(f"unexpected remote call {tool!r}")


def _remote_workspace(*, ws_id: str = "ws-42",
                      path: str = "/home/u/proj",
                      name: str = "proj") -> dict:
    return {
        "workspace": {
            "id": ws_id,
            "name": name,
            "path": path,
            "branch": "main",
            "project_root": None,
            "created_at": "2026-05-01T00:00:00Z",
            "updated_at": "2026-05-01T00:00:00Z",
            "owner_id": None,
            "origin_host_id": "host-1",
            "notes": None,
        }
    }


_DESKTOP_OK_INFO = {
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


# ---------------------------------------------------------------------------
# detect_dialect — autodetect helper
# ---------------------------------------------------------------------------

def test_detect_dialect_desktop():
    client = _DesktopMcp(active="ws", info=_DESKTOP_OK_INFO)
    assert asyncio.run(detect_dialect(client)) == DIALECT_DESKTOP


def test_detect_dialect_remote():
    client = _RemoteMcp(info=_remote_workspace(), terminals=[])
    assert asyncio.run(detect_dialect(client)) == DIALECT_REMOTE


def test_detect_dialect_surfaces_mcp_failure_as_source_unavailable():
    from vexis_agent.addons.codemux.mcp_client import CodemuxMcpError

    class _Broken:
        async def call(self, tool: str, args: dict):
            raise CodemuxMcpError("synthetic crash")

    with pytest.raises(SourceUnavailable) as exc:
        asyncio.run(detect_dialect(_Broken()))
    assert "synthetic crash" in str(exc.value)


# ---------------------------------------------------------------------------
# Desktop dialect — resolver behaviour
# ---------------------------------------------------------------------------

def test_desktop_happy_path_extracts_session_id_and_metadata():
    client = _DesktopMcp(active="workspace-42", info=_DESKTOP_OK_INFO)
    res = asyncio.run(resolve_workspace_to_session(client, "workspace-42"))
    assert isinstance(res, WorkspaceResolution)
    assert res.session_id == "session-99"
    assert res.workspace_id == "workspace-42"
    assert res.repo_path == "/home/u/proj"
    assert res.title == "proj"
    # Calls in the right order: dispatch probe → desktop active check
    # → workspace_info. Two app_status calls is the cost of unifying
    # the top-level entrypoint; ~1ms overhead, fine.
    tools = [t for t, _ in client.calls]
    assert tools == ["app_status", "app_status", "workspace_info"]


def test_desktop_workspace_not_active_raises_with_actionable_message():
    """Pin: error message names the active workspace AND tells the
    user how to fix it. The CLI's user-facing copy chains off this
    text, so wording drift would silently degrade the UX."""
    client = _DesktopMcp(active="workspace-OTHER", info=None)
    with pytest.raises(SourceUnavailable) as exc:
        asyncio.run(resolve_workspace_to_session(client, "workspace-42"))
    msg = str(exc.value).lower()
    assert "not the active codemux workspace" in msg
    assert "workspace-other" in msg
    assert "open the workspace" in msg
    # Did NOT call workspace_info — saved a round trip.
    assert [t for t, _ in client.calls] == ["app_status", "app_status"]


def test_desktop_active_but_no_terminal_session_raises():
    info_no_terminal = {**_DESKTOP_OK_INFO, "surfaces": []}
    client = _DesktopMcp(active="workspace-42", info=info_no_terminal)
    with pytest.raises(SourceUnavailable) as exc:
        asyncio.run(resolve_workspace_to_session(client, "workspace-42"))
    assert "no terminal session" in str(exc.value).lower()


def test_desktop_workspace_info_returns_non_dict_raises():
    client = _DesktopMcp(active="workspace-42", info="not a dict")  # type: ignore[arg-type]
    with pytest.raises(SourceUnavailable):
        asyncio.run(resolve_workspace_to_session(client, "workspace-42"))


# ---------------------------------------------------------------------------
# Remote dialect — resolver behaviour
# ---------------------------------------------------------------------------

def test_remote_happy_path_extracts_terminal_id_and_metadata():
    client = _RemoteMcp(
        info=_remote_workspace(),
        terminals=[
            {"id": "term-7", "cwd": "/home/u/proj", "command": "/bin/bash"},
        ],
    )
    res = asyncio.run(resolve_workspace_to_session(client, "ws-42"))
    assert isinstance(res, WorkspaceResolution)
    # ``session_id`` is the historic field name; it carries a terminal_id.
    assert res.session_id == "term-7"
    assert res.workspace_id == "ws-42"
    assert res.repo_path == "/home/u/proj"
    assert res.title == "proj"
    tools = [t for t, _ in client.calls]
    # Dispatch probe → workspace_info({id}) → terminal_list.
    assert tools == ["app_status", "workspace_info", "terminal_list"]
    # The workspace lookup carries the user-facing id.
    info_call = next(args for tool, args in client.calls if tool == "workspace_info")
    assert info_call == {"id": "ws-42"}


def test_remote_workspace_not_found_raises_without_listing_terminals():
    """A daemon ``not_found`` propagates as ``CodemuxMcpError`` whose
    text contains "workspace not found" — we collapse it to
    SourceUnavailable and short-circuit before terminal_list."""
    from vexis_agent.addons.codemux.mcp_client import CodemuxMcpError

    client = _RemoteMcp(
        info=CodemuxMcpError("workspace_info failed: workspace not found: ws-42"),
        terminals=AssertionError("should not be called"),
    )
    with pytest.raises(SourceUnavailable) as exc:
        asyncio.run(resolve_workspace_to_session(client, "ws-42"))
    msg = str(exc.value).lower()
    assert "workspace_info" in msg
    assert "not found" in msg
    # Dispatch probe + workspace_info only.
    assert [t for t, _ in client.calls] == ["app_status", "workspace_info"]


def test_remote_workspace_without_path_raises():
    info = _remote_workspace()
    info["workspace"]["path"] = ""
    client = _RemoteMcp(info=info, terminals=[])
    with pytest.raises(SourceUnavailable) as exc:
        asyncio.run(resolve_workspace_to_session(client, "ws-42"))
    assert "no recorded worktree path" in str(exc.value).lower()


def test_remote_no_terminal_in_workspace_cwd_raises_with_hint():
    """Pin: refusal copy must name the path AND point at the escape
    hatch (`--session-id`). The CLI's user-facing copy chains off
    this text, so wording drift would silently degrade the UX."""
    client = _RemoteMcp(
        info=_remote_workspace(),
        terminals=[
            {"id": "term-9", "cwd": "/somewhere/else", "command": "/bin/bash"},
        ],
    )
    with pytest.raises(SourceUnavailable) as exc:
        asyncio.run(resolve_workspace_to_session(client, "ws-42"))
    msg = str(exc.value)
    assert "/home/u/proj" in msg
    assert "--session-id" in msg


def test_remote_multiple_terminals_in_workspace_cwd_raises_ambiguous():
    client = _RemoteMcp(
        info=_remote_workspace(),
        terminals=[
            {"id": "term-a", "cwd": "/home/u/proj", "command": "/bin/bash"},
            {"id": "term-b", "cwd": "/home/u/proj", "command": "/bin/zsh"},
        ],
    )
    with pytest.raises(SourceUnavailable) as exc:
        asyncio.run(resolve_workspace_to_session(client, "ws-42"))
    msg = str(exc.value)
    # Both terminal ids surfaced so the user can pick one.
    assert "term-a" in msg and "term-b" in msg
    assert "--session-id" in msg


def test_remote_top_level_workspace_shape_is_accepted_for_back_compat():
    """The desktop dialect returns the workspace fields at the top
    level. Accept it so a misrouted client gets a clear ``no
    terminals`` error rather than a KeyError on ``path``."""
    info_top_level = _remote_workspace()["workspace"]  # no wrapper
    client = _RemoteMcp(
        info=info_top_level,
        terminals=[
            {"id": "term-7", "cwd": "/home/u/proj", "command": "/bin/bash"},
        ],
    )
    res = asyncio.run(resolve_workspace_to_session(client, "ws-42"))
    assert res.session_id == "term-7"


# ---------------------------------------------------------------------------
# CodemuxSource — dialect-aware read / is_alive args
# ---------------------------------------------------------------------------

class _ArgCapturingDesktop(_DesktopMcp):
    def __init__(self) -> None:
        super().__init__(active="ws", info=_DESKTOP_OK_INFO)
        self.read_args: list[dict] = []

    async def call(self, tool: str, args: dict):
        if tool == "terminal_read":
            self.read_args.append(args)
            return {"data": "ok"}
        return await super().call(tool, args)


class _ArgCapturingRemote(_RemoteMcp):
    def __init__(self) -> None:
        super().__init__(info=_remote_workspace(), terminals=[])
        self.read_args: list[dict] = []

    async def call(self, tool: str, args: dict):
        if tool == "terminal_read":
            self.read_args.append(args)
            return {"data": "ok", "byte_count": 2}
        return await super().call(tool, args)


def test_source_uses_desktop_arg_shape_on_desktop_dialect():
    client = _ArgCapturingDesktop()
    src = CodemuxSource(client)
    asyncio.run(src.read_recent_output("session-99"))
    assert src.dialect == DIALECT_DESKTOP
    # Desktop dialect uses ``session_id`` + ``lines``.
    assert client.read_args == [{"session_id": "session-99", "lines": 200}]


def test_source_uses_remote_arg_shape_on_remote_dialect():
    client = _ArgCapturingRemote()
    src = CodemuxSource(client)
    asyncio.run(src.read_recent_output("term-7"))
    assert src.dialect == DIALECT_REMOTE
    # Remote dialect uses ``terminal_id`` + ``max_bytes``.
    assert client.read_args == [{"terminal_id": "term-7", "max_bytes": 16384}]


def test_source_caches_dialect_after_first_probe():
    """Pin: app_status is probed once, even across many reads. A
    repeated probe per poll would burn an MCP roundtrip × every
    watched workspace × every poll tick — a real DoS."""
    client = _ArgCapturingRemote()
    src = CodemuxSource(client)
    asyncio.run(src.read_recent_output("a"))
    asyncio.run(src.read_recent_output("b"))
    asyncio.run(src.read_recent_output("c"))
    status_calls = [t for t, _ in client.calls if t == "app_status"]
    assert len(status_calls) == 1


def test_source_is_alive_uses_minimal_payload_per_dialect():
    """Cheapest probe: desktop ``lines=1``, remote ``max_bytes=1``."""
    desktop = _ArgCapturingDesktop()
    src = CodemuxSource(desktop)
    asyncio.run(src.is_alive("session-99"))
    assert desktop.read_args == [{"session_id": "session-99", "lines": 1}]

    remote = _ArgCapturingRemote()
    src = CodemuxSource(remote)
    asyncio.run(src.is_alive("term-7"))
    assert remote.read_args == [{"terminal_id": "term-7", "max_bytes": 1}]


# ---------------------------------------------------------------------------
# is_alive — dead-marker matching
# ---------------------------------------------------------------------------

def test_is_alive_recognises_remote_not_found_error_shape():
    """Pin: the remote dialect signals a closed/missing terminal as
    ``CodemuxMcpError`` whose text contains ``"kind": "not_found"``
    (the JSON-encoded structured error code). ``is_alive`` MUST
    return False for that shape — otherwise a poll loop will never
    mark a dead PTY dead and the user gets stale watchers."""
    from vexis_agent.addons.codemux.mcp_client import CodemuxMcpError

    class _Dead(_RemoteMcp):
        def __init__(self) -> None:
            super().__init__(info=_remote_workspace(), terminals=[])

        async def call(self, tool: str, args: dict):
            if tool == "terminal_read":
                raise CodemuxMcpError(
                    'terminal_read failed: {\n  "kind": "not_found",\n'
                    '  "message": "abc-123"\n}'
                )
            return await super().call(tool, args)

    src = CodemuxSource(_Dead())  # type: ignore[arg-type]
    assert asyncio.run(src.is_alive("abc-123")) is False


def test_is_alive_recognises_desktop_session_not_found():
    """Desktop dialect raises a prose error containing "session not
    found" / "no active terminal session". is_alive must read that
    too."""
    from vexis_agent.addons.codemux.mcp_client import CodemuxMcpError

    class _Dead(_DesktopMcp):
        def __init__(self) -> None:
            super().__init__(active="ws", info=_DESKTOP_OK_INFO)

        async def call(self, tool: str, args: dict):
            if tool == "terminal_read":
                raise CodemuxMcpError(
                    "terminal_read failed: terminal session 'session-99' not found"
                )
            return await super().call(tool, args)

    src = CodemuxSource(_Dead())  # type: ignore[arg-type]
    assert asyncio.run(src.is_alive("session-99")) is False


def test_is_alive_keeps_alive_when_mcp_is_unavailable():
    """Conservative: if the whole MCP is unreachable, every watched
    agent would otherwise look dead. Keep them alive so a flapping
    daemon doesn't mass-reap watchers."""
    from vexis_agent.addons.codemux.mcp_client import CodemuxMcpUnavailable

    class _Down:
        async def call(self, tool: str, args: dict):
            raise CodemuxMcpUnavailable("codemux not on PATH")

    src = CodemuxSource(_Down())  # type: ignore[arg-type]
    assert asyncio.run(src.is_alive("abc-123")) is True


# ---------------------------------------------------------------------------
# Resolver failure modes that apply to BOTH dialects
# ---------------------------------------------------------------------------

def test_mcp_call_failure_at_dispatch_probe_surfaces_as_source_unavailable():
    from vexis_agent.addons.codemux.mcp_client import CodemuxMcpError

    class _Broken:
        async def call(self, tool: str, args: dict):
            raise CodemuxMcpError("synthetic crash")

    with pytest.raises(SourceUnavailable) as exc:
        asyncio.run(resolve_workspace_to_session(_Broken(), "ws-42"))
    assert "synthetic crash" in str(exc.value)
