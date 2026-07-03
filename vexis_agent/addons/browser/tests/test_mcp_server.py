"""Unit contract for the ``vexis-browser`` MCP server.

The MCP server is a thin stdio adapter over the daemon control socket:
each tool forwards one ``browser_*`` op with the same argument shape the
``vexis-browse`` CLI / dispatch layer use, so the two front-ends stay
behaviourally identical. These tests pin that without a live daemon or a
real Camoufox session — the socket round-trip
(``tools.browser._client.send``) is monkeypatched to capture the
``(op, args)`` it would send.

Pins:
  * all twelve browser tools are registered on the FastMCP server (the ten
    original ops plus the issue-#57 ``browser_tabs`` / ``browser_tab_close``);
  * each tool forwards the correct op + argument dict (coercion parity
    with the CLI: screenshot only forwards ``include_base64`` when set,
    read omits an empty selector, the issue-#57 ``wait_until`` / ``then_read``
    / ``tab`` keys are forwarded only when set, etc.);
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


def test_all_twelve_tools_registered():
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
        "browser_tabs",
        "browser_tab_close",
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


def test_navigate_omits_unset_wait_until_then_read_tab(captured):
    # Issue #57: the new keys ride the forward-only-when-set contract — an
    # omitted arg must NOT appear in the payload (lets the daemon default),
    # and a set one is forwarded verbatim.
    _run(mcp_server.browser_navigate("https://x/1"))
    _run(
        mcp_server.browser_navigate(
            "https://x/2",
            wait_until="domcontentloaded",
            then_read="#results",
            tab="a",
        )
    )
    assert captured[0] == ("browser_navigate", {"url": "https://x/1"})
    assert captured[1] == (
        "browser_navigate",
        {
            "url": "https://x/2",
            "wait_until": "domcontentloaded",
            "then_read": "#results",
            "tab": "a",
        },
    )


def test_snapshot_forwards_full_flag(captured):
    _run(mcp_server.browser_snapshot(True))
    assert captured == [("browser_snapshot", {"full": True})]


def test_snapshot_forwards_tab_only_when_set(captured):
    _run(mcp_server.browser_snapshot())
    _run(mcp_server.browser_snapshot(tab="a"))
    assert captured[0] == ("browser_snapshot", {"full": False})
    assert captured[1] == ("browser_snapshot", {"full": False, "tab": "a"})


def test_click_forwards_index_and_js(captured):
    _run(mcp_server.browser_click(7, js=True))
    assert captured == [("browser_click", {"index": 7, "js": True})]


def test_click_forwards_then_read_and_tab_when_set(captured):
    _run(mcp_server.browser_click(3))
    _run(mcp_server.browser_click(4, then_read="body", tab="b"))
    assert captured[0] == ("browser_click", {"index": 3, "js": False})
    assert captured[1] == (
        "browser_click",
        {"index": 4, "js": False, "then_read": "body", "tab": "b"},
    )


def test_read_omits_empty_selector(captured):
    _run(mcp_server.browser_read())
    _run(mcp_server.browser_read("div.result"))
    _run(mcp_server.browser_read(tab="a"))
    assert captured[0] == ("browser_read", {})
    assert captured[1] == ("browser_read", {"selector": "div.result"})
    assert captured[2] == ("browser_read", {"tab": "a"})


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


def test_tabs_forwards_empty_args(captured):
    # browser_tabs (issue #57) takes no args and forwards {} to the op.
    out = _run(mcp_server.browser_tabs())
    assert out["ok"] is True
    assert captured == [("browser_tabs", {})]


def test_tab_close_forwards_tab(captured):
    _run(mcp_server.browser_tab_close("a"))
    assert captured == [("browser_tab_close", {"tab": "a"})]


def test_type_press_back_scroll_screenshot_forward_tab_when_set(captured):
    _run(mcp_server.browser_type(1, "hi", tab="a"))
    _run(mcp_server.browser_press("Enter", tab="a"))
    _run(mcp_server.browser_back(tab="a"))
    _run(mcp_server.browser_scroll("down", 1.0, tab="a"))
    _run(mcp_server.browser_screenshot(tab="a"))
    assert captured[0] == (
        "browser_type",
        {"index": 1, "text": "hi", "clear": True, "tab": "a"},
    )
    assert captured[1] == ("browser_press", {"key": "Enter", "tab": "a"})
    assert captured[2] == ("browser_back", {"tab": "a"})
    assert captured[3] == (
        "browser_scroll",
        {"direction": "down", "pages": 1.0, "tab": "a"},
    )
    assert captured[4] == (
        "browser_screenshot",
        {"full_page": False, "tab": "a"},
    )


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
