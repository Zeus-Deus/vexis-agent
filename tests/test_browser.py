"""Browser subsystem tests for the scrapling/Camoufox stack.

Pin counts (update when adding cases): 1 DSL-format case group, the
error/stale payload shapes, the dashboard-state contract, the config
surface (incl. the navigation-vs-action timeout split), the Cloudflare
solver-gating group (issue #45: pre-check skips the solver on unchallenged
pages, fail-safes, and the noise-filter), and the wedged-session
force-recycle group (issue #55: ``errors.is_timeout``, the
consecutive-nav-timeout streak + bounded recycle on ``SessionManager``, the
``BrowserTools`` navigate/back/click streak wiring, and manual
``BrowserTools.recycle``). The pure-logic tests run anywhere. The
real-browser end-to-end tests (``test_e2e_*``) launch Camoufox and are
gated behind ``VEXIS_BROWSER_E2E=1`` — they need the browser binary
(``camoufox fetch``) and a host that lets a Firefox subprocess spawn, so
they're opt-in rather than a default CI step.

The e2e cases drive ``file://`` pages (and, for the #55 wedge case, a
local never-responding TCP listener — still no external network): the core
flow (navigate → snapshot → click → type → press → scroll → screenshot →
back), the read/JS-click recovery path, the shadow-DOM + cursor:pointer
snapshot reach, cookie persistence across restart, the #45 gate (solver
skipped on an unchallenged page), and the #55 wedge (three real navigation
timeouts force-recycle the session; manual recycle) — all against the real
engine. Run them on a real machine with:

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


def test_navigation_timeout_is_separate_and_shorter_than_action_timeout():
    # The page-settle budget (goto + load + networkidle) is a distinct knob
    # from the per-action ceiling, and deliberately much shorter — so a
    # socket-heavy page that never reaches networkidle can't drag a single
    # navigation out to the full action timeout. See session.wait_stable.
    from vexis_agent.tools.browser import profile
    nav = profile.navigation_timeout_seconds()
    act = profile.action_timeout_seconds()
    assert nav == 30
    assert act == 120
    assert nav < act


# --- Cloudflare solver gating (issue #45) ---------------------------
# The solver must run ONLY when a challenge is actually present, so a
# no-challenge navigation skips scrapling's ~5s network-idle wait and its
# spurious "No Cloudflare challenge found." ERROR. These drive
# SessionManager.solve_cloudflare with a fake scrapling session + page, so
# they assert the gate deterministically without launching a browser.

class _FakeCFPage:
    def __init__(self, content: str, raise_content: bool = False) -> None:
        self._content = content
        self._raise_content = raise_content

    async def content(self) -> str:
        if self._raise_content:
            raise RuntimeError("page.content() boom")
        return self._content


class _FakeCFSession:
    """Stands in for scrapling's AsyncStealthySession — only the two
    private hooks SessionManager.solve_cloudflare touches."""

    def __init__(self, detect_result) -> None:
        self._detect_result = detect_result
        self.solver_calls = 0
        self.stability_calls = 0

    def _detect_cloudflare(self, content: str):
        return self._detect_result

    async def _cloudflare_solver(self, page) -> None:
        self.solver_calls += 1

    async def _wait_for_page_stability(self, page, load_dom, network_idle) -> None:
        self.stability_calls += 1


class _FakeCFSessionNoDetect(_FakeCFSession):
    """A scrapling build where ``_detect_cloudflare`` is absent (version
    drift) — the pre-check must fail safe and still run the solver. The
    ``__getattribute__`` override makes the attribute genuinely missing, so
    ``getattr(session, "_detect_cloudflare", None)`` returns ``None``."""

    def __init__(self) -> None:
        super().__init__(detect_result=None)

    def __getattribute__(self, name):
        if name == "_detect_cloudflare":
            raise AttributeError(name)
        return object.__getattribute__(self, name)


def test_solve_cloudflare_skips_solver_when_no_challenge():
    import asyncio
    mgr = SessionManager()
    fake = _FakeCFSession(detect_result=None)
    mgr._session = fake
    page = _FakeCFPage("<html><body>ordinary page</body></html>")
    asyncio.run(mgr.solve_cloudflare(page))
    # No challenge -> the ~5s solver (and its ERROR log) is never invoked.
    assert fake.solver_calls == 0


def test_solve_cloudflare_runs_solver_when_challenge_present():
    import asyncio
    mgr = SessionManager()
    fake = _FakeCFSession(detect_result="managed")
    mgr._session = fake
    page = _FakeCFPage("<html>cType: 'managed'</html>")
    asyncio.run(mgr.solve_cloudflare(page))
    # Challenge detected -> full solve, then re-settle the page.
    assert fake.solver_calls == 1
    assert fake.stability_calls >= 1


def test_solve_cloudflare_fails_safe_when_detect_missing():
    import asyncio
    mgr = SessionManager()
    fake = _FakeCFSessionNoDetect()
    mgr._session = fake
    page = _FakeCFPage("<html></html>")
    asyncio.run(mgr.solve_cloudflare(page))
    # Can't pre-check -> run the solver rather than risk skipping a real one.
    assert fake.solver_calls == 1


def test_solve_cloudflare_fails_safe_when_content_unreadable():
    import asyncio
    mgr = SessionManager()
    fake = _FakeCFSession(detect_result=None)
    mgr._session = fake
    page = _FakeCFPage("", raise_content=True)
    asyncio.run(mgr.solve_cloudflare(page))
    assert fake.solver_calls == 1


def test_solve_cloudflare_noop_without_session():
    import asyncio
    mgr = SessionManager()
    # No live session -> pure no-op, no exception.
    asyncio.run(mgr.solve_cloudflare(_FakeCFPage("<html></html>")))


def test_cloudflare_noise_filter_drops_no_challenge_error():
    import logging
    from vexis_agent.tools.browser.session import _CloudflareNoiseFilter
    f = _CloudflareNoiseFilter()
    rec = logging.LogRecord(
        "scrapling", logging.ERROR, __file__, 1,
        "No Cloudflare challenge found.", (), None,
    )
    assert f.filter(rec) is False


def test_cloudflare_noise_filter_passes_other_records():
    import logging
    from vexis_agent.tools.browser.session import _CloudflareNoiseFilter
    f = _CloudflareNoiseFilter()
    rec = logging.LogRecord(
        "scrapling", logging.INFO, __file__, 1,
        'The turnstile version discovered is "%s"', ("managed",), None,
    )
    assert f.filter(rec) is True


def test_silence_cloudflare_noise_is_idempotent():
    import logging
    from vexis_agent.tools.browser.session import (
        _CloudflareNoiseFilter,
        _silence_cloudflare_noise,
    )
    logger = logging.getLogger("scrapling")
    logger.filters = [
        f for f in logger.filters if not isinstance(f, _CloudflareNoiseFilter)
    ]
    _silence_cloudflare_noise()
    _silence_cloudflare_noise()
    try:
        count = sum(
            isinstance(f, _CloudflareNoiseFilter) for f in logger.filters
        )
        assert count == 1
    finally:
        logger.filters = [
            f for f in logger.filters
            if not isinstance(f, _CloudflareNoiseFilter)
        ]


# --- wedged-session force-recycle (issue #55) -----------------------
# A wedged Camoufox engine returns Page.goto timeouts back-to-back while
# the host answers fine; the inactivity recycler never fires because a
# failing navigate still marks activity. So N consecutive navigation
# timeouts trip an immediate force-recycle, and the agent can force one
# manually. These drive fake session/page objects — no browser launch.


class _PlaywrightTimeoutError(Exception):
    """Stand-in for Playwright's ``TimeoutError``.

    Matched by class NAME, not inheritance — the real one isn't a builtin
    ``TimeoutError`` subclass either, and ``errors.is_timeout`` must catch
    it without importing playwright. ``__name__`` is reassigned so
    ``type(exc).__name__ == "TimeoutError"`` while the Python-level symbol
    stays distinct from the builtin."""


_PlaywrightTimeoutError.__name__ = "TimeoutError"


class _FakeCloseSession:
    """Minimal fake ``AsyncStealthySession``: counts ``close()`` calls and
    exposes the page-stability hook so ``wait_stable`` is a no-op. No
    ``_solve_cloudflare`` attr, so ``solves_cloudflare`` reads False."""

    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1

    async def _wait_for_page_stability(self, page, load_dom, network_idle) -> None:
        return None


class _FakeNavPage:
    """Fake Playwright page. ``goto``/``go_back`` raise ``goto_exc`` /
    ``go_back_exc`` when set (else succeed); ``locator`` returns a fake
    locator whose ``click`` raises ``click_exc`` when set."""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.goto_exc: type[BaseException] | None = None
        self.go_back_exc: type[BaseException] | None = None
        self.click_exc: type[BaseException] | None = None

    async def goto(self, url, wait_until=None) -> None:
        if self.goto_exc is not None:
            raise self.goto_exc()
        self.url = url

    async def go_back(self, wait_until=None) -> None:
        if self.go_back_exc is not None:
            raise self.go_back_exc()

    def locator(self, selector):
        return _FakeLocator(self.click_exc)


class _FakeLocator:
    def __init__(self, exc: type[BaseException] | None) -> None:
        self._exc = exc
        self.first = self

    async def count(self) -> int:
        return 1

    async def click(self) -> None:
        if self._exc is not None:
            raise self._exc()


def test_is_timeout_true_for_asyncio_and_named_playwright_timeout():
    import asyncio

    assert errors.is_timeout(asyncio.TimeoutError()) is True
    # A class *named* TimeoutError that does NOT inherit the builtin —
    # is_timeout catches it by name so errors.py never imports playwright.
    assert not issubclass(_PlaywrightTimeoutError, TimeoutError)
    assert type(_PlaywrightTimeoutError()).__name__ == "TimeoutError"
    assert errors.is_timeout(_PlaywrightTimeoutError()) is True
    # Anything else is not a timeout.
    assert errors.is_timeout(ValueError("nope")) is False


def test_streak_recycles_at_threshold_and_resets(monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    monkeypatch.setattr(
        session_mod, "navigation_timeout_recycle_threshold", lambda: 3
    )

    async def go():
        mgr = SessionManager()
        fake = _FakeCloseSession()
        mgr._session = fake
        mgr._page = object()

        # Two timeouts: streak builds, no recycle.
        assert await mgr.record_navigation_timeout() is False
        assert await mgr.record_navigation_timeout() is False
        assert mgr._session is fake
        assert mgr.is_running() is True

        # Third crosses the threshold: recycle, session torn down + closed.
        assert await mgr.record_navigation_timeout() is True
        assert mgr._session is None
        assert fake.close_calls == 1
        assert mgr._nav_timeout_streak == 0

        # A success mid-streak clears it.
        fake2 = _FakeCloseSession()
        mgr._session = fake2
        assert await mgr.record_navigation_timeout() is False
        assert await mgr.record_navigation_timeout() is False
        mgr.record_navigation_success()
        assert mgr._nav_timeout_streak == 0
        # Two more do not recycle (streak restarted from zero).
        assert await mgr.record_navigation_timeout() is False
        assert await mgr.record_navigation_timeout() is False
        assert mgr._session is fake2

    asyncio.run(go())


def test_streak_threshold_zero_never_recycles(monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    monkeypatch.setattr(
        session_mod, "navigation_timeout_recycle_threshold", lambda: 0
    )

    async def go():
        mgr = SessionManager()
        fake = _FakeCloseSession()
        mgr._session = fake
        for _ in range(10):
            assert await mgr.record_navigation_timeout() is False
        # Disabled: never recycles, never even counts.
        assert mgr._session is fake
        assert mgr._nav_timeout_streak == 0

    asyncio.run(go())


def test_streak_threshold_reread_per_call(monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    holder = {"t": 5}
    monkeypatch.setattr(
        session_mod,
        "navigation_timeout_recycle_threshold",
        lambda: holder["t"],
    )

    async def go():
        mgr = SessionManager()
        mgr._session = _FakeCloseSession()
        # Threshold 5: two timeouts, no recycle.
        assert await mgr.record_navigation_timeout() is False
        assert await mgr.record_navigation_timeout() is False
        assert mgr.is_running() is True
        # Lower the threshold mid-flight; the next call re-reads it and,
        # with the streak already at 2, crosses the new threshold of 2.
        holder["t"] = 2
        assert await mgr.record_navigation_timeout() is True
        assert mgr.is_running() is False

    asyncio.run(go())


def test_recycle_bounded_close_survives_hanging_close(monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    # A wedged engine's close() must not hang the recycle: shrink the force-
    # close ceiling so the test doesn't wait the real 30s.
    monkeypatch.setattr(session_mod, "_FORCE_CLOSE_TIMEOUT_SECONDS", 0.05)

    class _HangingSession:
        async def close(self) -> None:
            await asyncio.Event().wait()  # never returns

    async def go():
        mgr = SessionManager()
        mgr._session = _HangingSession()
        mgr._page = object()
        # Bounded close times out internally -> recycle still completes.
        assert await mgr.recycle(reason="test") is True
        assert mgr._session is None

    asyncio.run(go())


def test_recycle_with_no_session_returns_false():
    import asyncio

    async def go():
        mgr = SessionManager()
        assert await mgr.recycle(reason="test") is False

    asyncio.run(go())


def _install_fake_acquire(monkeypatch, mgr, session, page):
    async def _acquire():
        mgr._session = session
        mgr._page = page
        return session, page

    monkeypatch.setattr(mgr, "acquire", _acquire)


def test_browsertools_navigate_timeout_streak_wiring(tmp_path, monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    monkeypatch.setattr(
        session_mod, "navigation_timeout_recycle_threshold", lambda: 3
    )

    async def _fake_render(page):
        return {
            "snapshot": "",
            "element_count": 0,
            "url": page.url,
            "title": "T",
        }

    async def _fake_apply_captcha(page, target, result):
        return result

    async def go():
        mgr = SessionManager()
        session = _FakeCloseSession()
        page = _FakeNavPage()
        _install_fake_acquire(monkeypatch, mgr, session, page)
        monkeypatch.setattr(
            "vexis_agent.tools.browser.snapshot.render", _fake_render
        )
        monkeypatch.setattr(
            "vexis_agent.tools.browser.tools.apply_captcha", _fake_apply_captcha
        )
        bt = BrowserTools(mgr, tmp_path)

        page.goto_exc = _PlaywrightTimeoutError
        r1 = await bt.navigate("http://x/1")
        assert r1["ok"] is False
        assert r1.get("hint") != errors.FORCE_RECYCLE_HINT
        assert mgr.is_running() is True

        r2 = await bt.navigate("http://x/2")
        assert r2["ok"] is False
        assert r2.get("hint") != errors.FORCE_RECYCLE_HINT
        assert mgr.is_running() is True

        # Third consecutive nav timeout trips the recycle.
        r3 = await bt.navigate("http://x/3")
        assert r3["ok"] is False
        assert r3["hint"] == errors.FORCE_RECYCLE_HINT
        assert mgr.is_running() is False
        assert session.close_calls == 1

        # A successful navigation clears the streak.
        page.goto_exc = None
        r4 = await bt.navigate("http://x/ok")
        assert r4["ok"] is True
        assert mgr._nav_timeout_streak == 0

        # back() timeouts count toward the streak too.
        page.go_back_exc = _PlaywrightTimeoutError
        b1 = await bt.back()
        assert b1["ok"] is False
        assert mgr._nav_timeout_streak == 1

        # A click timeout does NOT touch the streak (overlay-slow clicks are
        # normal, not a wedge signature).
        page.click_exc = _PlaywrightTimeoutError
        streak_before = mgr._nav_timeout_streak
        c1 = await bt.click(5)
        assert c1["ok"] is False
        assert mgr._nav_timeout_streak == streak_before

    asyncio.run(go())


def test_browsertools_recycle_reports_was_running(tmp_path, monkeypatch):
    import asyncio

    async def go():
        mgr = SessionManager()
        bt = BrowserTools(mgr, tmp_path)

        # Manual recycle must never lazy-start a session.
        async def _boom():
            raise AssertionError("recycle must not acquire/lazy-start")

        monkeypatch.setattr(mgr, "acquire", _boom)

        # Not running -> was_running False, still not running.
        out = await bt.recycle()
        assert out == {"ok": True, "was_running": False}
        assert mgr.is_running() is False

        # Running -> was_running True, torn down + closed.
        fake = _FakeCloseSession()
        mgr._session = fake
        mgr._page = object()
        out2 = await bt.recycle()
        assert out2 == {"ok": True, "was_running": True}
        assert mgr.is_running() is False
        assert fake.close_calls == 1

    asyncio.run(go())


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
def test_e2e_read_recovers_text_and_js_click_beats_overlay(tmp_path):
    # The #29 gap: a results page rendered as plain <div> text plus a
    # full-screen overlay. snapshot sees ~nothing; read() recovers it; a
    # normal click is swallowed by the overlay while a --js click fires.
    import asyncio
    import re

    page_html = tmp_path / "catalog.html"
    page_html.write_text(
        "<html><body>"
        "<div id='results'>"
        "<div class='row'>11427512300 Oliefilterelement</div>"
        "<div class='row'>34116858652 Remblok set voor</div>"
        "</div>"
        "<button id='accept' onclick=\"document.title='ACCEPTED'\">Accept</button>"
        "<div style='position:fixed;top:0;left:0;right:0;bottom:0;"
        "background:rgba(0,0,0,.4);z-index:99999'></div>"
        "</body></html>"
    )
    url = page_html.as_uri()

    async def go():
        mgr = SessionManager()
        bt = BrowserTools(mgr, tmp_path)
        try:
            nav = await bt.navigate(url)
            assert nav["ok"] is True, nav
            # OE numbers live in non-interactive divs -> absent from snapshot
            assert "11427512300" not in nav["snapshot"]

            read = await bt.read()
            assert read["ok"] is True, read
            assert "11427512300" in read["text"]
            assert "34116858652" in read["text"]
            assert read["chars"] == len(read["text"])
            assert read["selector"] == "body"

            # scoped read by selector
            scoped = await bt.read("#results")
            assert scoped["ok"] is True and "Oliefilterelement" in scoped["text"]

            # missing selector -> clean error, not a 120s hang
            miss = await bt.read("#nope")
            assert miss["ok"] is False

            snap = await bt.snapshot()
            m = re.search(r"\[(\d+)\]<button", snap["snapshot"])
            assert m, snap["snapshot"]
            idx = int(m.group(1))

            # normal click is intercepted by the overlay -> times out (error)
            blocked = await bt.click(idx)
            assert blocked["ok"] is False, blocked
            # js click goes straight to the element
            assert (await bt.click(idx, js=True))["ok"] is True
        finally:
            await mgr.stop()

    asyncio.run(go())


@pytest.mark.skipif(not E2E, reason="set VEXIS_BROWSER_E2E=1 to run real browser e2e")
def test_e2e_snapshot_indexes_shadow_dom_and_cursor_pointer(tmp_path):
    # Modern-web reach beyond a flat querySelectorAll: a clickable custom
    # control is a cursor:pointer <div> with no role/onclick, and a real
    # control can live inside an open shadow root. Both must appear in the
    # snapshot, the shadow control must be clickable by index (Playwright
    # pierces open shadow DOM), and a pointer-styled wrapper around a real
    # button must NOT add a second, redundant entry.
    import asyncio
    import re

    page_html = tmp_path / "modern.html"
    page_html.write_text(
        "<html><body>"
        "<div id='custom' style='cursor:pointer'>Custom Control</div>"
        "<div style='cursor:pointer'><button id='real'>Inner Button</button></div>"
        "<my-widget></my-widget>"
        "<script>"
        "class MyWidget extends HTMLElement {"
        "  connectedCallback(){"
        "    const r=this.attachShadow({mode:'open'});"
        "    r.innerHTML=\"<button id='shadow-btn'>Shadow Go</button>\";"
        "  }"
        "}"
        "customElements.define('my-widget', MyWidget);"
        "</script>"
        "</body></html>"
    )
    url = page_html.as_uri()

    async def go():
        mgr = SessionManager()
        bt = BrowserTools(mgr, tmp_path)
        try:
            nav = await bt.navigate(url)
            assert nav["ok"] is True, nav
            snap = (await bt.snapshot())["snapshot"]
            # 1. cursor:pointer custom control surfaces despite no role/onclick
            assert "Custom Control" in snap, snap
            # 2. the inner real button surfaces; the pointer-styled wrapper
            #    around it is suppressed (no separate <div>Inner Button</div>)
            assert re.search(r"\[(\d+)\]<button[^>]*>Inner Button", snap), snap
            assert not re.search(r"\[(\d+)\]<div[^>]*>Inner Button", snap), snap
            # 3. a control inside an OPEN shadow root surfaces...
            m = re.search(r"\[(\d+)\]<button[^>]*>Shadow Go", snap)
            assert m, snap
            # ...and is clickable by index (CSS locator pierces shadow DOM)
            assert (await bt.click(int(m.group(1))))["ok"] is True
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


@pytest.mark.skipif(not E2E, reason="set VEXIS_BROWSER_E2E=1 to run real browser e2e")
def test_e2e_solver_skipped_on_unchallenged_page(tmp_path):
    # Issue #45: with solve_cloudflare on (the default), a navigation to a
    # page with NO challenge must NOT invoke scrapling's ~5s solver. We wrap
    # the real session's private solver with a counter after acquire and
    # assert it stays at zero across a local navigation.
    import asyncio
    from vexis_agent.tools.browser import profile as profile_mod

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(profile_mod, "solve_cloudflare", lambda: True)
    page_html = tmp_path / "plain.html"
    page_html.write_text("<html><body><h1>No challenge here</h1></body></html>")
    url = page_html.as_uri()

    async def go():
        mgr = SessionManager()
        bt = BrowserTools(mgr, tmp_path)
        try:
            session, _page = await mgr.acquire()
            calls = {"n": 0}
            real_solver = session._cloudflare_solver

            async def counting_solver(page):
                calls["n"] += 1
                return await real_solver(page)

            session._cloudflare_solver = counting_solver
            assert mgr.solves_cloudflare is True
            nav = await bt.navigate(url)
            assert nav["ok"] is True, nav
            # The gate detected no challenge and skipped the solver entirely.
            assert calls["n"] == 0
        finally:
            await mgr.stop()
            monkeypatch.undo()

    asyncio.run(go())


def _start_never_responds_listener():
    """Bind a local TCP listener that accepts connections and never replies.

    A navigation to it opens a connection and then hangs waiting for a
    response — reproducing the wedged-engine symptom (a slow/dead peer)
    without any external network. Returns
    ``(server_socket, port, stop_event, thread, accepted_conns)``; the
    caller tears everything down in a ``finally``."""
    import socket
    import threading

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    stop = threading.Event()
    conns: list = []

    def _accept_loop() -> None:
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conns.append(conn)  # hold the socket open; never write a reply

    thread = threading.Thread(target=_accept_loop, daemon=True)
    thread.start()
    return srv, port, stop, thread, conns


@pytest.mark.skipif(not E2E, reason="set VEXIS_BROWSER_E2E=1 to run real browser e2e")
def test_e2e_wedged_navigation_force_recycles(tmp_path, monkeypatch):
    # Issue #55: three consecutive real navigation timeouts must force-recycle
    # the wedged session. A local never-responding TCP listener makes goto
    # hang until the (shortened) navigation timeout fires; the third timeout
    # crosses the threshold, tears the session down, and stamps the recycle
    # hint. A subsequent file:// navigate then succeeds on a fresh session.
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    # Patch in session.py's namespace BEFORE the first acquire so the page's
    # default navigation timeout picks up the shortened budget, and so the
    # streak reader sees the threshold.
    monkeypatch.setattr(session_mod, "navigation_timeout_seconds", lambda: 3)
    monkeypatch.setattr(
        session_mod, "navigation_timeout_recycle_threshold", lambda: 3
    )

    ok_page = tmp_path / "ok.html"
    ok_page.write_text("<html><body><h1>Fresh session OK</h1></body></html>")

    srv, port, stop, thread, conns = _start_never_responds_listener()
    dead_url = f"http://127.0.0.1:{port}/"

    async def go():
        mgr = SessionManager()
        bt = BrowserTools(mgr, tmp_path)
        try:
            r1 = await bt.navigate(dead_url)
            assert r1["ok"] is False, r1
            r2 = await bt.navigate(dead_url)
            assert r2["ok"] is False, r2
            r3 = await bt.navigate(dead_url)
            assert r3["ok"] is False, r3
            assert "recycle" in (r3.get("hint") or "").lower(), r3
            assert mgr.is_running() is False

            good = await bt.navigate(ok_page.as_uri())
            assert good["ok"] is True, good
            assert mgr.is_running() is True
        finally:
            await mgr.stop()

    try:
        asyncio.run(go())
    finally:
        stop.set()
        try:
            srv.close()
        except OSError:
            pass
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass
        thread.join(timeout=2)


@pytest.mark.skipif(not E2E, reason="set VEXIS_BROWSER_E2E=1 to run real browser e2e")
def test_e2e_manual_recycle(tmp_path):
    # Issue #55: the manual recycle path against a live session. Navigate a
    # file:// page, recycle, and confirm the session is torn down and a fresh
    # navigate succeeds afterward.
    import asyncio

    page_html = tmp_path / "page.html"
    page_html.write_text("<html><body><h1>Recycle me</h1></body></html>")
    url = page_html.as_uri()

    async def go():
        mgr = SessionManager()
        bt = BrowserTools(mgr, tmp_path)
        try:
            first = await bt.navigate(url)
            assert first["ok"] is True, first
            assert mgr.is_running() is True

            out = await bt.recycle()
            assert out == {"ok": True, "was_running": True}
            assert mgr.is_running() is False

            again = await bt.navigate(url)
            assert again["ok"] is True, again
            assert mgr.is_running() is True
        finally:
            await mgr.stop()

    asyncio.run(go())
