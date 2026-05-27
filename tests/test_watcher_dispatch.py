"""Control-socket dispatch ops for the watcher.

Direct unit tests against the dispatch coroutine ``_build_dispatch``
returns. We don't bind a real socket — the JSON envelope is the same
shape ``ControlSocket._dispatch_safely`` would write, and the
``vexis-watch`` CLI reads it the same way ``vexis-bg`` does (already
covered by ``test_control_socket`` for the framing layer).

The conditional-activation contract is the most important pin: when
``watcher=None`` is passed to ``_build_dispatch`` (MCP absent), every
``watch_*`` op MUST return ``ok=False`` with ``kind="CodemuxNotConfigured"``
so the CLI prints the user-facing message and exits 0.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vexis_agent.core.watcher import (
    UNAVAILABLE_MESSAGE,
    WatcherController,
    WatcherRegistry,
)
from vexis_agent.core.watcher.sources import (
    Source,
    SourceDescription,
    clear_sources,
    register_source,
)
from vexis_agent.main import _build_dispatch


class _StubSource(Source):
    source_type = "fake"

    async def read_recent_output(self, identifier: str) -> bytes:
        return f"line1\nline2 for {identifier}\nline3\n".encode()

    async def is_alive(self, identifier: str) -> bool:
        return True

    async def describe(self, identifier: str) -> SourceDescription:
        return SourceDescription()


@pytest.fixture(autouse=True)
def _stub_source():
    clear_sources()
    register_source(_StubSource())
    yield
    clear_sources()


class _NoBg:
    """Background tasks stand-in. The watcher ops don't touch it."""


class _NoBrowser:
    pass


def _controller(tmp_path: Path) -> WatcherController:
    return WatcherController(
        registry=WatcherRegistry(state_path=tmp_path / "wr.json"),
        register_codemux_source=False,
    )


def _dispatcher(tmp_path: Path, *, with_watcher: bool):
    watcher = _controller(tmp_path) if with_watcher else None
    return _build_dispatch(_NoBg(), _NoBrowser(), watcher), watcher


# ---------- conditional activation ------------------------------------------


def test_watch_list_without_watcher_returns_codemux_not_configured(tmp_path):
    dispatch, _ = _dispatcher(tmp_path, with_watcher=False)
    resp = asyncio.run(dispatch("watch_list", {}))
    assert resp == {
        "ok": False,
        "error": UNAVAILABLE_MESSAGE,
        "kind": "CodemuxNotConfigured",
    }


def test_every_watch_op_gated_by_watcher_presence(tmp_path):
    dispatch, _ = _dispatcher(tmp_path, with_watcher=False)
    ops = [
        "watch_register",
        "watch_unregister",
        "watch_list",
        "watch_status",
        "watch_mute",
        "watch_tail",
    ]
    for op in ops:
        resp = asyncio.run(dispatch(op, {"name": "x"}))
        assert resp.get("kind") == "CodemuxNotConfigured", (
            f"{op} leaked through with {resp!r}"
        )


# ---------- happy paths with watcher attached --------------------------------


def test_register_then_list_then_unregister(tmp_path):
    dispatch, watcher = _dispatcher(tmp_path, with_watcher=True)

    reg = asyncio.run(dispatch("watch_register", {
        "name": "ws-a",
        "source": "fake",
        "identifier": "workspace-7",
        "agent_kind": "claude-code",
        "chat_id": 99,
        "idle_after_seconds": 5,
    }))
    assert reg["ok"] is True
    assert reg["result"]["name"] == "ws-a"

    lst = asyncio.run(dispatch("watch_list", {}))
    assert lst["ok"] is True
    assert len(lst["result"]) == 1
    assert lst["result"][0]["name"] == "ws-a"

    st = asyncio.run(dispatch("watch_status", {"name": "ws-a"}))
    assert st["ok"] is True
    assert st["result"]["chat_id"] == 99

    un = asyncio.run(dispatch("watch_unregister", {"name": "ws-a"}))
    assert un["ok"] is True

    lst2 = asyncio.run(dispatch("watch_list", {}))
    assert lst2["result"] == []


def test_register_rejects_duplicate(tmp_path):
    dispatch, _ = _dispatcher(tmp_path, with_watcher=True)
    args = {
        "name": "dup",
        "source": "fake",
        "identifier": "x",
        "agent_kind": "claude-code",
        "chat_id": 1,
    }
    first = asyncio.run(dispatch("watch_register", args))
    assert first["ok"] is True
    second = asyncio.run(dispatch("watch_register", args))
    assert second["ok"] is False
    assert second["kind"] == "DuplicateName"


def test_tail_returns_lines(tmp_path):
    dispatch, _ = _dispatcher(tmp_path, with_watcher=True)
    asyncio.run(dispatch("watch_register", {
        "name": "ws-tail",
        "source": "fake",
        "identifier": "abc",
        "agent_kind": "claude-code",
        "chat_id": 1,
    }))
    resp = asyncio.run(dispatch("watch_tail", {"name": "ws-tail", "lines": 2}))
    assert resp["ok"] is True
    assert "line2 for abc" in resp["result"]["text"]


def test_mute_round_trips(tmp_path):
    dispatch, watcher = _dispatcher(tmp_path, with_watcher=True)
    asyncio.run(dispatch("watch_register", {
        "name": "mute-me",
        "source": "fake",
        "identifier": "x",
        "agent_kind": "claude-code",
        "chat_id": 1,
    }))
    resp = asyncio.run(dispatch("watch_mute", {"name": "mute-me", "muted": True}))
    assert resp["ok"] is True
    assert resp["result"]["muted"] is True

    resp = asyncio.run(dispatch("watch_mute", {"name": "mute-me", "muted": False}))
    assert resp["ok"] is True
    assert resp["result"]["muted"] is False


def test_unregister_unknown_name_is_structured_error(tmp_path):
    dispatch, _ = _dispatcher(tmp_path, with_watcher=True)
    resp = asyncio.run(dispatch("watch_unregister", {"name": "ghost"}))
    assert resp["ok"] is False
    assert resp["kind"] == "UnknownName"


# ---------- CLI exit-code contract ------------------------------------------


def test_cli_print_result_returns_zero_on_codemux_not_configured(tmp_path):
    """Pin: the CLI returns 0 (not 1) when the daemon says the MCP is off.

    This is the skill-friendly path the docstring documents — a skill
    calling ``vexis-watch register`` shouldn't crash on a host that
    doesn't have Codemux. If this regresses to exit 1, scripts that
    branch on success-vs-real-error will start treating "feature
    unavailable" as a hard failure."""
    from vexis_agent.tools.watch_cli import _print_result_or_exit

    code = _print_result_or_exit({
        "ok": False,
        "error": "Codemux MCP not configured.",
        "kind": "CodemuxNotConfigured",
    })
    assert code == 0


def test_cli_print_result_returns_one_on_real_error(tmp_path):
    from vexis_agent.tools.watch_cli import _print_result_or_exit
    code = _print_result_or_exit({
        "ok": False,
        "error": "no watched agent named 'ghost'",
        "kind": "UnknownName",
    })
    assert code == 1
