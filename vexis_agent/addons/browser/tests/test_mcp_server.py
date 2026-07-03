"""Unit contract for the ``vexis-browser`` MCP server.

The MCP server is a thin stdio adapter over the daemon control socket:
each tool forwards one ``browser_*`` op with the same argument shape the
``vexis-browse`` CLI / dispatch layer use, so the two front-ends stay
behaviourally identical. These tests pin that without a live daemon or a
real Camoufox session — the socket round-trip
(``tools.browser._client.send``) is monkeypatched to capture the
``(op, args)`` it would send.

Pins:
  * all ten browser tools are registered on the FastMCP server;
  * each tool forwards the correct op + argument dict (coercion parity
    with the CLI: screenshot only forwards ``include_base64`` when set,
    read omits an empty selector, etc.);
  * a daemon-down transport error becomes a clean ``{"ok": false}`` tool
    result, never an exception (the per-turn server must not crash);
  * the response unwrapper handles both control-socket framings.
"""

from __future__ import annotations

import asyncio

import pytest

from vexis_agent.tools.browser import mcp_server
from vexis_agent.tools.browser._client import BrowserSocketError, unwrap_response


# ──────────────────────────────────────────────────────────────────
# Tool registration
# ──────────────────────────────────────────────────────────────────


def test_all_ten_tools_registered():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_read",
        "browser_type",
        "browser_press",
        "browser_back",
        "browser_scroll",
        "browser_screenshot",
        "browser_recycle",
    }


def test_server_name_matches_addon_spec():
    """The FastMCP server name must equal the McpServerSpec.name the
    browser add-on registers, or claude-code/opencode would namespace
    the tools under a different prefix than the config declares."""
    from vexis_agent.addons import browser as browser_addon  # noqa: F401
    from vexis_agent.tools.browser.mcp_server import SERVER_NAME

    assert mcp_server.mcp.name == SERVER_NAME == "vexis-browser"


# ──────────────────────────────────────────────────────────────────
# Forwarding parity — each tool sends the right op + args
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def captured(monkeypatch):
    """Capture the (op, args) each tool would send; return a canned ok."""
    calls: list[tuple[str, dict]] = []

    def fake_send(op, args, *, timeout=None):
        calls.append((op, dict(args)))
        return {"ok": True, "echo": op}

    # Patch the symbol the server module imported, not the origin.
    monkeypatch.setattr(mcp_server, "_socket_send", fake_send)
    return calls


def _run(coro):
    return asyncio.run(coro)


def test_navigate_forwards_url(captured):
    out = _run(mcp_server.browser_navigate("https://example.com"))
    assert out["ok"] is True
    assert captured == [("browser_navigate", {"url": "https://example.com"})]


def test_snapshot_forwards_full_flag(captured):
    _run(mcp_server.browser_snapshot(True))
    assert captured == [("browser_snapshot", {"full": True})]


def test_click_forwards_index_and_js(captured):
    _run(mcp_server.browser_click(7, js=True))
    assert captured == [("browser_click", {"index": 7, "js": True})]


def test_read_omits_empty_selector(captured):
    _run(mcp_server.browser_read())
    _run(mcp_server.browser_read("div.result"))
    assert captured[0] == ("browser_read", {})
    assert captured[1] == ("browser_read", {"selector": "div.result"})


def test_type_forwards_clear_default_true(captured):
    _run(mcp_server.browser_type(3, "hello"))
    assert captured == [
        ("browser_type", {"index": 3, "text": "hello", "clear": True})
    ]


def test_press_and_back(captured):
    _run(mcp_server.browser_press("Enter"))
    _run(mcp_server.browser_back())
    assert captured[0] == ("browser_press", {"key": "Enter"})
    assert captured[1] == ("browser_back", {})


def test_scroll_forwards_direction_and_pages(captured):
    _run(mcp_server.browser_scroll("down", 2.0))
    assert captured == [("browser_scroll", {"direction": "down", "pages": 2.0})]


def test_screenshot_only_forwards_include_base64_when_set(captured):
    """Matches the CLI contract: an omitted key lets the daemon apply
    its config default; ``include_base64=False`` must NOT force it off."""
    _run(mcp_server.browser_screenshot())
    _run(mcp_server.browser_screenshot(full_page=True, include_base64=True))
    assert captured[0] == ("browser_screenshot", {"full_page": False})
    assert captured[1] == (
        "browser_screenshot",
        {"full_page": True, "include_base64": True},
    )


def test_recycle_forwards_empty_args(captured):
    # browser_recycle (issue #55) takes no args and forwards {} to the op.
    out = _run(mcp_server.browser_recycle())
    assert out["ok"] is True
    assert captured == [("browser_recycle", {})]


# ──────────────────────────────────────────────────────────────────
# Resilience — daemon down / bad reply
# ──────────────────────────────────────────────────────────────────


def test_transport_error_becomes_ok_false(monkeypatch):
    """A missing daemon must surface as a tool result, not an exception —
    the per-turn MCP server must not die mid-conversation."""

    def boom(op, args, *, timeout=None):
        raise BrowserSocketError("daemon socket not found")

    monkeypatch.setattr(mcp_server, "_socket_send", boom)
    out = _run(mcp_server.browser_navigate("https://example.com"))
    assert out["ok"] is False
    assert "daemon socket not found" in out["error"]
    assert "hint" in out


def test_unwrap_handles_both_framings():
    # Browser handlers return their own {"ok": ...}; socket forwards it.
    assert unwrap_response({"ok": True, "url": "x"}) == {"ok": True, "url": "x"}
    # A generic op the socket wrapped as {"ok": true, "result": {...}}.
    assert unwrap_response({"ok": True, "result": {"ok": True, "a": 1}}) == {
        "ok": True,
        "a": 1,
    }
