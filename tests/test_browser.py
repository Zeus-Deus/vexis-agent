"""Browser subsystem tests for the scrapling/Camoufox stack.

Pin counts (update when adding cases): 1 DSL-format case group, the
error/stale payload shapes, the dashboard-state contract, and the config
surface. The pure-logic tests run anywhere. The real-browser end-to-end
test (``test_e2e_*``) launches Camoufox and is gated behind
``VEXIS_BROWSER_E2E=1`` — it needs the browser binary (``camoufox fetch``)
and a host that lets a Firefox subprocess spawn, so it's opt-in rather
than a default CI step.

The e2e drives a ``file://`` page, exercising navigate → snapshot →
click → type → press → scroll → screenshot → back end-to-end against the
real engine without any network. Run it on a real machine with:

    VEXIS_BROWSER_E2E=1 pytest tests/test_browser.py -k e2e -s
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vexis_agent.tools.browser import errors
from vexis_agent.tools.browser import snapshot as snapshot_mod
from vexis_agent.tools.browser.session import SessionManager
from vexis_agent.tools.browser.tools import BrowserTools


# --- pure logic: DSL formatting -------------------------------------

def test_format_rows_emits_indexed_dsl():
    rows = [
        {"idx": 0, "tag": "input",
         "attrs": {"type": "text", "placeholder": "Enter name"}, "text": ""},
        {"idx": 1, "tag": "button",
         "attrs": {"aria-label": "Submit form"}, "text": "Submit"},
        {"idx": 2, "tag": "a", "attrs": {"href": "/help"}, "text": "Help"},
    ]
    text = snapshot_mod._format_rows(rows)
    lines = text.splitlines()
    assert lines[0] == '[0]<input type="text" placeholder="Enter name" />'
    assert lines[1] == '[1]<button aria-label="Submit form">Submit</button>'
    assert lines[2] == '[2]<a href="/help">Help</a>'
    # index marker the click/type contract relies on
    for i, line in enumerate(lines):
        assert line.startswith(f"[{i}]<")


def test_format_rows_drops_value_attr_into_text_slot_only():
    # 'value' is surfaced as text, never duplicated as an attribute.
    rows = [{"idx": 0, "tag": "input",
             "attrs": {"type": "text", "value": "secret"}, "text": "secret"}]
    text = snapshot_mod._format_rows(rows)
    assert 'value=' not in text
    assert text == '[0]<input type="text">secret</input>'


def test_index_attr_is_the_snapshot_click_contract():
    # snapshot stamps this; tools.py builds [data-vexis-idx="N"] from it.
    assert snapshot_mod.INDEX_ATTR == "data-vexis-idx"
    from vexis_agent.tools.browser.tools import INDEX_ATTR as tools_attr
    assert tools_attr == snapshot_mod.INDEX_ATTR


# --- pure logic: error / stale payloads -----------------------------

def test_error_payload_shape():
    p = errors.error_payload("boom", "try again")
    assert p == {"ok": False, "error": "boom", "hint": "try again"}
    assert errors.error_payload("boom") == {"ok": False, "error": "boom"}


def test_stale_index_payload_is_soft_success():
    p = errors.stale_index_payload()
    assert p["ok"] is True
    assert p["snapshot_stale"] is True
    assert "snapshot" in p["suggestion"].lower()


def test_normalize_exception_keeps_one_line():
    exc = RuntimeError("Page.goto: NS_ERROR\nCall log:\n  - navigating")
    p = errors.normalize_exception(exc, action="browser_navigate")
    assert p["ok"] is False
    assert "\n" not in p["error"]
    assert p["error"].startswith("browser_navigate failed:")


def test_normalize_exception_timeout_has_hint():
    import asyncio
    p = errors.normalize_exception(asyncio.TimeoutError(), action="browser_click")
    assert p["ok"] is False
    assert "timed out" in p["error"]
    assert p["hint"]


# --- dashboard state contract (no browser launch) -------------------

def test_session_state_for_dashboard_not_started():
    mgr = SessionManager()
    state = mgr.state_for_dashboard()
    assert state["state"] == "not_started"
    assert state["started_at"] is None
    assert state["last_activity_at"] is None
    assert "headless" in state
    # CDP attach is gone — no attach key leaks into the payload.
    assert "attached_to_cdp" not in state
    assert "attach_mode" not in state


def test_tools_state_for_dashboard_suppressed_when_idle(tmp_path):
    mgr = SessionManager()
    bt = BrowserTools(mgr, tmp_path)
    bt._current_url = "https://example.com"
    bt._current_title = "Example"
    # not running -> live page metadata is suppressed, history stays
    state = bt.state_for_dashboard()
    assert state["current_url"] is None
    assert state["current_title"] is None
    assert state["recent_navigations"] == []


# --- config surface --------------------------------------------------

def test_session_kwargs_targets_camoufox_persistent_profile():
    from vexis_agent.tools.browser import profile
    kw = profile.session_kwargs()
    assert kw["user_data_dir"].endswith("/browser-profiles/default")
    assert isinstance(kw["headless"], bool)
    assert "solve_cloudflare" in kw
    assert kw["block_webrtc"] is True


# --- real-browser end-to-end (opt-in) -------------------------------

E2E = os.environ.get("VEXIS_BROWSER_E2E") == "1"


@pytest.mark.skipif(not E2E, reason="set VEXIS_BROWSER_E2E=1 to run real browser e2e")
def test_e2e_navigate_snapshot_click_type_screenshot(tmp_path):
    import asyncio
    import re

    page_html = tmp_path / "page.html"
    page_html.write_text(
        "<html><body><h1 id='h'>Start</h1>"
        "<button onclick=\"document.getElementById('h').innerText='clicked'\">Go</button>"
        "<input id='t' type='text' placeholder='name'>"
        "<a href='page2.html'>next</a></body></html>"
    )
    (tmp_path / "page2.html").write_text("<html><body><h1>Page Two</h1></body></html>")
    url = page_html.as_uri()

    async def go():
        mgr = SessionManager()
        bt = BrowserTools(mgr, tmp_path)
        try:
            nav = await bt.navigate(url)
            assert nav["ok"] is True, nav
            assert nav["element_count"] >= 2
            assert "<button" in nav["snapshot"]

            snap = await bt.snapshot()
            assert snap["ok"] is True
            m = re.search(r"\[(\d+)\]<button", snap["snapshot"])
            assert m, snap["snapshot"]
            assert (await bt.click(int(m.group(1))))["ok"] is True

            m2 = re.search(r"\[(\d+)\]<input", snap["snapshot"])
            assert m2, snap["snapshot"]
            assert (await bt.type(int(m2.group(1)), "hello@example.com"))["ok"] is True

            assert (await bt.press("Tab"))["ok"] is True
            assert (await bt.scroll("down", 1.0))["ok"] is True

            shot = await bt.screenshot()
            assert shot["ok"] is True
            assert Path(shot["path"]).is_file() and shot["size_bytes"] > 0

            # an out-of-range index is a soft stale-index hint, not an error
            stale = await bt.click(9999)
            assert stale.get("snapshot_stale") is True
        finally:
            await mgr.stop()

    asyncio.run(go())


@pytest.mark.skipif(not E2E, reason="set VEXIS_BROWSER_E2E=1 to run real browser e2e")
def test_e2e_cookie_persists_across_restart(tmp_path, monkeypatch):
    # Point the profile dir at a temp location and prove a cookie set in
    # one session survives a full session teardown + restart.
    import asyncio
    from vexis_agent.tools.browser import profile as profile_mod
    prof = tmp_path / "prof"
    monkeypatch.setattr(profile_mod, "profile_dir", lambda: prof)

    async def seed():
        mgr = SessionManager()
        bt = BrowserTools(mgr, tmp_path)
        await bt.navigate("data:text/html,<b>seed</b>")
        session, _page = await mgr.acquire()
        await session.context.add_cookies([{
            "name": "vexis_login", "value": "sess_abc123",
            "domain": "example.com", "path": "/", "expires": 9999999999,
        }])
        await mgr.stop()

    async def reread():
        mgr2 = SessionManager()
        session2, _ = await mgr2.acquire()
        cookies = await session2.context.cookies()
        await mgr2.stop()
        return cookies

    asyncio.run(seed())
    cookies = asyncio.run(reread())
    found = [c for c in cookies if c["name"] == "vexis_login"]
    assert found and found[0]["value"] == "sess_abc123"
