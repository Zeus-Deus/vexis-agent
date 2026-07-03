"""Browser subsystem tests for the scrapling/Camoufox stack.

Pin counts (update when adding cases): 1 DSL-format case group, the
error/stale payload shapes, the dashboard-state contract, the config
surface (incl. the navigation-vs-action timeout split), the Cloudflare
solver-gating group (issue #45: pre-check skips the solver on unchallenged
pages, fail-safes, and the noise-filter), the wedged-session
force-recycle group (issue #55: ``errors.is_timeout``, the
consecutive-nav-timeout streak + bounded recycle on ``SessionManager``, the
``BrowserTools`` navigate/back/click streak wiring, and manual
``BrowserTools.recycle``), and the batched-read/parallel-tabs/cheaper-wait
group (issue #57: the ``wait_until`` goto mapping + ``wait_stable`` skip,
navigate/click ``then_read`` batching incl. the stale-index short-circuit,
the ``SessionManager`` named-tab registry — create/reuse/unknown/cap/bad-name
and clearing on recycle/stop/idle-teardown, real per-tab concurrency vs.
same-tab/main serialization, the named-tab streak wiring, the dashboard
``open_tabs`` + current-page isolation, the generation-scoped streak — a
stale-generation timeout/success is dropped, the concurrent-tab burst repro,
and ``_run_action`` threading the captured generation — and the failed-open
tab discard: a failed create-path navigate leaves no phantom tab and frees the
slot, while a reused-tab or bonus-``then_read`` failure keeps the tab). The
pure-logic tests run anywhere.
The real-browser end-to-end tests (``test_e2e_*``) launch Camoufox and are
gated behind ``VEXIS_BROWSER_E2E=1`` — they need the browser binary
(``camoufox fetch``) and a host that lets a Firefox subprocess spawn, so
they're opt-in rather than a default CI step.

The e2e cases drive ``file://`` pages and local threaded/TCP listeners (no
external network): the core flow (navigate → snapshot → click → type →
press → scroll → screenshot → back), the read/JS-click recovery path, the
shadow-DOM + cursor:pointer snapshot reach, cookie persistence across
restart, the #45 gate (solver skipped on an unchallenged page), the #55
wedge (three real navigation timeouts force-recycle the session; manual
recycle), and the #57 levers (navigate+read single round-trip, three tabs
opened concurrently against a slow local server proving overlap + no state
bleed, and the cheap ``wait_until="domcontentloaded"`` nav) — all against
the real engine. Run them on a real machine with:

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


# --- issue #57: batched read, parallel tabs, cheaper nav wait -------
# All fakes below are pure asyncio — no browser launch, no network. A
# pre-set ``mgr._session`` keeps ``_ensure_session_locked`` from importing
# scrapling (the import lives inside the ``_session is None`` branch), so a
# named-tab acquire works against fake objects.


async def _noop_render(page):
    return {
        "snapshot": "",
        "element_count": 0,
        "url": page.url,
        "title": "T",
    }


async def _noop_captcha(page, target, result):
    return result


def _patch_render_captcha(monkeypatch):
    monkeypatch.setattr("vexis_agent.tools.browser.snapshot.render", _noop_render)
    monkeypatch.setattr(
        "vexis_agent.tools.browser.tools.apply_captcha", _noop_captcha
    )


class _ReadLocator:
    """Fake Playwright locator: ``count`` 0 when its selector is 'missing',
    else 1; ``inner_text`` returns the page body; ``click``/``evaluate`` are
    no-ops."""

    def __init__(self, text: str, missing: bool) -> None:
        self._text = text
        self._missing = missing
        self.first = self

    async def count(self) -> int:
        return 0 if self._missing else 1

    async def inner_text(self) -> str:
        return self._text

    async def click(self) -> None:
        return None

    async def evaluate(self, script) -> None:
        return None


class _ReadPage:
    """Fake page for the batch/tab tests. Records ``goto`` calls (url +
    wait_until), serves ``read`` text, tracks ``wait_for_load_state`` calls,
    and can raise a preset exception from ``goto``."""

    def __init__(
        self,
        body: str = "hello world",
        missing_selectors=(),
        goto_exc=None,
    ) -> None:
        self.url = "about:blank"
        self.goto_calls: list = []
        self.load_state_calls: list = []
        self._body = body
        self._missing = set(missing_selectors)
        self.goto_exc = goto_exc
        self._closed = False

    def is_closed(self) -> bool:
        return self._closed

    def set_default_navigation_timeout(self, ms) -> None:
        return None

    def set_default_timeout(self, ms) -> None:
        return None

    async def goto(self, url, wait_until=None) -> None:
        self.goto_calls.append((url, wait_until))
        if self.goto_exc is not None:
            raise self.goto_exc()
        self.url = url

    async def go_back(self, wait_until=None) -> None:
        self.url = "about:blank"

    async def inner_text(self, selector) -> str:
        return self._body

    def locator(self, selector):
        return _ReadLocator(self._body, selector in self._missing)

    async def wait_for_load_state(self, state, timeout=None) -> None:
        self.load_state_calls.append((state, timeout))

    async def close(self) -> None:
        self._closed = True


class _TabContext:
    """Fake Camoufox context: hands out queued pages (else fresh ``_ReadPage``)
    and counts ``new_page`` calls to prove create-vs-reuse."""

    def __init__(self, pages=None) -> None:
        self._queue = list(pages or [])
        self.new_page_calls = 0
        self.created: list = []

    async def new_page(self):
        self.new_page_calls += 1
        page = self._queue.pop(0) if self._queue else _ReadPage()
        self.created.append(page)
        return page


class _FakeTabSession:
    """Fake ``AsyncStealthySession`` exposing just the context + the
    page-stability hook (so ``wait_stable`` is a no-op) + ``close``. No
    ``_solve_cloudflare`` attr, so ``solves_cloudflare`` reads False."""

    def __init__(self, pages=None) -> None:
        self.context = _TabContext(pages)
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1

    async def _wait_for_page_stability(self, page, load_dom, network_idle) -> None:
        return None


# ---- Lever 3: wait_until mapping -----------------------------------


def test_navigate_wait_until_maps_goto_and_skips_wait_stable(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        for mode, expected, stable_expected in (
            ("domcontentloaded", "domcontentloaded", 0),
            ("load", "load", 0),
            (None, "domcontentloaded", 1),  # default == settle
        ):
            mgr = SessionManager()
            page = _ReadPage()
            _install_fake_acquire(monkeypatch, mgr, _FakeCloseSession(), page)
            stable: list = []

            async def _count_wait_stable(p, _acc=stable):
                _acc.append(p)

            monkeypatch.setattr(mgr, "wait_stable", _count_wait_stable)
            bt = BrowserTools(mgr, tmp_path)

            kwargs = {} if mode is None else {"wait_until": mode}
            out = await bt.navigate("http://x/", **kwargs)
            assert out["ok"] is True, out
            assert page.goto_calls == [("http://x/", expected)]
            assert len(stable) == stable_expected

    asyncio.run(go())


def test_navigate_unknown_wait_until_is_error_payload(tmp_path):
    import asyncio

    async def go():
        # Validated before any acquire — a bad value never launches / navigates.
        bt = BrowserTools(SessionManager(), tmp_path)
        out = await bt.navigate("http://x/", wait_until="bogus")
        assert out["ok"] is False
        assert "wait_until" in out["error"]

    asyncio.run(go())


# ---- Lever 1: navigate then_read -----------------------------------


def test_navigate_then_read_success_carries_read(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        mgr = SessionManager()
        page = _ReadPage(body="catalog rows here")
        # Count acquires to prove ONE round-trip (no re-acquire for the
        # bonus read).
        acquires = {"n": 0}

        async def _counting_acquire():
            acquires["n"] += 1
            mgr._session = _FakeCloseSession()
            mgr._page = page
            return mgr._session, page

        monkeypatch.setattr(mgr, "acquire", _counting_acquire)
        bt = BrowserTools(mgr, tmp_path)

        out = await bt.navigate("http://x/", then_read="body")
        assert out["ok"] is True
        assert out["read"] == {
            "ok": True,
            "text": "catalog rows here",
            "selector": "body",
            "chars": len("catalog rows here"),
        }
        # One acquire only — the read ran inside the same op/lock hold.
        assert acquires["n"] == 1

    asyncio.run(go())


def test_navigate_then_read_failure_keeps_nav_ok(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        mgr = SessionManager()
        page = _ReadPage(missing_selectors={"#nope"})
        _install_fake_acquire(monkeypatch, mgr, _FakeCloseSession(), page)
        bt = BrowserTools(mgr, tmp_path)

        out = await bt.navigate("http://x/", then_read="#nope")
        # A failed bonus read must NOT fail the navigation.
        assert out["ok"] is True
        assert out["read"]["ok"] is False
        assert "error" in out["read"]

    asyncio.run(go())


def test_navigate_failure_has_no_read_key(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        mgr = SessionManager()
        page = _ReadPage(goto_exc=RuntimeError("boom"))
        _install_fake_acquire(monkeypatch, mgr, _FakeCloseSession(), page)
        bt = BrowserTools(mgr, tmp_path)

        out = await bt.navigate("http://x/", then_read="body")
        assert out["ok"] is False
        assert "read" not in out

    asyncio.run(go())


# ---- Lever 1: click then_read (incl. stale-index short-circuit) ----


def test_click_then_read_success(tmp_path, monkeypatch):
    import asyncio

    async def go():
        mgr = SessionManager()
        page = _ReadPage(body="page after click")
        _install_fake_acquire(monkeypatch, mgr, _FakeCloseSession(), page)
        bt = BrowserTools(mgr, tmp_path)

        out = await bt.click(3, then_read="body")
        assert out["ok"] is True
        assert out["read"] == {
            "ok": True,
            "text": "page after click",
            "selector": "body",
            "chars": len("page after click"),
        }
        # The bounded settle wait ran once before the read (nav-triggering
        # clicks read the new document).
        assert page.load_state_calls and page.load_state_calls[0][0] == (
            "domcontentloaded"
        )

    asyncio.run(go())


def test_click_stale_index_skips_then_read(tmp_path, monkeypatch):
    import asyncio

    async def go():
        mgr = SessionManager()
        # The index selector is 'missing' -> count 0 -> soft stale hint, and
        # NO read is attempted for a vanished index.
        page = _ReadPage(missing_selectors={'[data-vexis-idx="5"]'})
        _install_fake_acquire(monkeypatch, mgr, _FakeCloseSession(), page)
        bt = BrowserTools(mgr, tmp_path)

        out = await bt.click(5, then_read="body")
        assert out.get("snapshot_stale") is True
        assert "read" not in out
        assert page.load_state_calls == []

    asyncio.run(go())


# ---- Lever 2: named-tab registry -----------------------------------


def test_tab_create_on_navigate_then_reuse(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        mgr = SessionManager()
        mgr._session = _FakeTabSession()
        bt = BrowserTools(mgr, tmp_path)
        try:
            r1 = await bt.navigate("http://a/1", tab="a")
            assert r1["ok"] is True
            assert list(mgr._tabs) == ["a"]
            assert mgr._session.context.new_page_calls == 1
            # A second navigate on the same tab reuses the page (no new_page).
            r2 = await bt.navigate("http://a/2", tab="a")
            assert r2["ok"] is True
            assert mgr._session.context.new_page_calls == 1
        finally:
            await mgr.stop()

    asyncio.run(go())


def test_unknown_tab_read_errors_with_hint(tmp_path):
    import asyncio

    async def go():
        mgr = SessionManager()
        mgr._session = _FakeTabSession()  # running, but registry empty
        bt = BrowserTools(mgr, tmp_path)
        try:
            out = await bt.read(tab="ghost")
            assert out["ok"] is False
            assert "no tab named 'ghost'" in out["error"]
            assert "open tabs" in out["hint"]
        finally:
            await mgr.stop()

    asyncio.run(go())


def test_tab_close_removes_from_registry(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        mgr = SessionManager()
        mgr._session = _FakeTabSession()
        bt = BrowserTools(mgr, tmp_path)
        try:
            await bt.navigate("http://a/1", tab="a")
            assert "a" in mgr._tabs
            out = await bt.tab_close("a")
            assert out == {"ok": True, "closed": "a"}
            assert "a" not in mgr._tabs
            assert "a" not in mgr._tab_locks
            # Closing it again is now a clean not-found.
            miss = await bt.tab_close("a")
            assert miss["ok"] is False
        finally:
            await mgr.stop()

    asyncio.run(go())


def test_tab_cap_enforced(tmp_path, monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    _patch_render_captcha(monkeypatch)
    monkeypatch.setattr(session_mod, "max_tabs", lambda: 1)

    async def go():
        mgr = SessionManager()
        mgr._session = _FakeTabSession()
        bt = BrowserTools(mgr, tmp_path)
        try:
            assert (await bt.navigate("http://a/", tab="a"))["ok"] is True
            over = await bt.navigate("http://b/", tab="b")
            assert over["ok"] is False
            assert "tab limit" in over["error"].lower()
            assert "close" in over["hint"].lower()
            # The rejected tab was not registered.
            assert list(mgr._tabs) == ["a"]
        finally:
            await mgr.stop()

    asyncio.run(go())


def test_bad_tab_name_is_bad_request(tmp_path):
    import asyncio

    async def go():
        mgr = SessionManager()
        mgr._session = _FakeTabSession()
        bt = BrowserTools(mgr, tmp_path)
        try:
            out = await bt.read(tab="bad name!")
            assert out["ok"] is False
            assert out["kind"] == "BadRequest"
            close = await bt.tab_close("bad name!")
            assert close["ok"] is False
            assert close["kind"] == "BadRequest"
        finally:
            await mgr.stop()

    asyncio.run(go())


def test_tab_registry_cleared_on_recycle_stop_idle(tmp_path, monkeypatch):
    import asyncio
    import time

    _patch_render_captcha(monkeypatch)

    async def go():
        # recycle
        mgr = SessionManager()
        mgr._session = _FakeTabSession()
        bt = BrowserTools(mgr, tmp_path)
        await bt.navigate("http://a/", tab="a")
        await mgr.recycle(reason="test")
        assert mgr._tabs == {} and mgr._tab_locks == {}
        assert mgr.is_running() is False
        await mgr.stop()  # cancel sweeper

        # stop (closes named pages, clears registry)
        mgr2 = SessionManager()
        mgr2._session = _FakeTabSession()
        bt2 = BrowserTools(mgr2, tmp_path)
        await bt2.navigate("http://a/", tab="a")
        page = mgr2._tabs["a"]
        await mgr2.stop()
        assert mgr2._tabs == {} and mgr2._tab_locks == {}
        assert page.is_closed() is True

        # idle sweep teardown
        mgr3 = SessionManager()
        fake = _FakeCloseSession()
        mgr3._session = fake
        idle_page = _ReadPage()
        mgr3._tabs = {"a": idle_page}
        mgr3._tab_locks = {"a": asyncio.Lock()}
        mgr3._last_activity = time.monotonic() - 10_000
        assert await mgr3._sweep_once() is True
        assert mgr3._session is None
        assert mgr3._tabs == {} and mgr3._tab_locks == {}
        assert fake.close_calls == 1

    asyncio.run(go())


# ---- Lever 2: real concurrency -------------------------------------
# Overlap is proved with an asyncio gate: each fake goto increments a shared
# counter and blocks on a shared Event. Two ops that truly run at once both
# enter before either is released; serialized ops can't.


class _Shared:
    def __init__(self, need: int) -> None:
        self.entered = 0
        self.need = need
        self.enough = None  # set lazily to an Event on the running loop
        self.proceed = None


class _GatePage:
    def __init__(self, shared: _Shared) -> None:
        self.url = "about:blank"
        self._shared = shared
        self._closed = False

    def is_closed(self) -> bool:
        return self._closed

    def set_default_navigation_timeout(self, ms) -> None:
        return None

    def set_default_timeout(self, ms) -> None:
        return None

    async def goto(self, url, wait_until=None) -> None:
        self.url = url
        self._shared.entered += 1
        if self._shared.entered >= self._shared.need:
            self._shared.enough.set()
        await self._shared.proceed.wait()

    async def inner_text(self, selector) -> str:
        return "text"

    def locator(self, selector):
        return _ReadLocator("text", False)

    async def wait_for_load_state(self, state, timeout=None) -> None:
        return None

    async def close(self) -> None:
        self._closed = True


class _GateContext:
    def __init__(self, shared: _Shared) -> None:
        self._shared = shared
        self.pages: list = []

    async def new_page(self):
        page = _GatePage(self._shared)
        self.pages.append(page)
        return page


class _GateSession:
    def __init__(self, shared: _Shared) -> None:
        self.context = _GateContext(shared)
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1

    async def _wait_for_page_stability(self, page, load_dom, network_idle) -> None:
        return None


def test_different_tabs_navigate_concurrently(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        shared = _Shared(need=2)
        shared.enough = asyncio.Event()
        shared.proceed = asyncio.Event()
        mgr = SessionManager()
        mgr._session = _GateSession(shared)
        bt = BrowserTools(mgr, tmp_path)
        try:
            task = asyncio.gather(
                bt.navigate("http://a/", tab="a"),
                bt.navigate("http://b/", tab="b"),
            )
            # Both gotos reach the gate before either is released -> overlap.
            # If they serialized this would hang (only one enters) and time out.
            await asyncio.wait_for(shared.enough.wait(), timeout=3)
            assert shared.entered == 2
            shared.proceed.set()
            results = await asyncio.wait_for(task, timeout=3)
            assert all(r["ok"] for r in results)
        finally:
            shared.proceed.set()
            await mgr.stop()

    asyncio.run(go())


def test_same_tab_ops_serialize(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        shared = _Shared(need=2)
        shared.enough = asyncio.Event()
        shared.proceed = asyncio.Event()
        mgr = SessionManager()
        mgr._session = _GateSession(shared)
        bt = BrowserTools(mgr, tmp_path)
        try:
            task = asyncio.gather(
                bt.navigate("http://a/1", tab="a"),
                bt.navigate("http://a/2", tab="a"),
            )
            await asyncio.sleep(0.1)
            # Second op is blocked on the SAME tab lock — only one entered.
            assert shared.entered == 1
            shared.proceed.set()
            results = await asyncio.wait_for(task, timeout=3)
            assert all(r["ok"] for r in results)
            assert shared.entered == 2
            # Reused the one page (create-on-first, reuse-after).
            assert mgr._session.context.pages and len(
                mgr._session.context.pages
            ) == 1
        finally:
            shared.proceed.set()
            await mgr.stop()

    asyncio.run(go())


def test_main_page_ops_serialize(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        shared = _Shared(need=2)
        shared.enough = asyncio.Event()
        shared.proceed = asyncio.Event()
        mgr = SessionManager()
        mgr._session = _GateSession(shared)
        mgr._page = _GatePage(shared)  # pre-set main page; acquire reuses it
        bt = BrowserTools(mgr, tmp_path)
        try:
            task = asyncio.gather(
                bt.navigate("http://m/1"),
                bt.navigate("http://m/2"),
            )
            await asyncio.sleep(0.1)
            # Main-page ops still serialize under action_lock.
            assert shared.entered == 1
            shared.proceed.set()
            results = await asyncio.wait_for(task, timeout=3)
            assert all(r["ok"] for r in results)
            assert shared.entered == 2
        finally:
            shared.proceed.set()
            await mgr.stop()

    asyncio.run(go())


# ---- Lever 2: named-tab streak wiring (#55 x #57) ------------------


def test_named_tab_timeout_feeds_streak_success_clears(tmp_path, monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    _patch_render_captcha(monkeypatch)
    monkeypatch.setattr(
        session_mod, "navigation_timeout_recycle_threshold", lambda: 5
    )

    async def go():
        page = _ReadPage(goto_exc=_PlaywrightTimeoutError)
        mgr = SessionManager()
        mgr._session = _FakeTabSession(pages=[page])
        # Pre-register the tab so the failing navigates REUSE it. A failed
        # create-path open is now discarded (a failed open leaves no tab —
        # see the phantom-tab tests), so the streak wiring is exercised on an
        # already-open tab, which a re-navigation failure must keep.
        mgr._tabs["a"] = page
        mgr._tab_locks["a"] = asyncio.Lock()
        bt = BrowserTools(mgr, tmp_path)
        try:
            assert (await bt.navigate("http://a/1", tab="a"))["ok"] is False
            assert mgr._nav_timeout_streak == 1
            assert (await bt.navigate("http://a/2", tab="a"))["ok"] is False
            assert mgr._nav_timeout_streak == 2
            # A success on the tab clears the streak (success on ANY page).
            page.goto_exc = None
            r = await bt.navigate("http://a/3", tab="a")
            assert r["ok"] is True
            assert mgr._nav_timeout_streak == 0
            # Named-tab navigation is recorded with the tab key.
            recents = bt.state_for_dashboard()["recent_navigations"]
            assert recents[0].get("tab") == "a"
        finally:
            await mgr.stop()

    asyncio.run(go())


def test_named_tab_streak_trips_recycle_and_clears_registry(tmp_path, monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    _patch_render_captcha(monkeypatch)
    monkeypatch.setattr(
        session_mod, "navigation_timeout_recycle_threshold", lambda: 3
    )

    async def go():
        page = _ReadPage(goto_exc=_PlaywrightTimeoutError)
        session = _FakeTabSession(pages=[page])
        mgr = SessionManager()
        mgr._session = session
        # Pre-register the tab so the failing navigates REUSE it rather than
        # being discarded as failed create-path opens (see the phantom-tab
        # tests); the streak wiring here is about an already-open tab.
        mgr._tabs["a"] = page
        mgr._tab_locks["a"] = asyncio.Lock()
        bt = BrowserTools(mgr, tmp_path)
        try:
            assert (await bt.navigate("http://a/1", tab="a"))["ok"] is False
            assert (await bt.navigate("http://a/2", tab="a"))["ok"] is False
            r3 = await bt.navigate("http://a/3", tab="a")
            assert r3["ok"] is False
            # Threshold tripped: force-recycle hint + session torn down + the
            # tab registry emptied.
            assert r3["hint"] == errors.FORCE_RECYCLE_HINT
            assert mgr.is_running() is False
            assert session.close_calls == 1
            assert mgr._tabs == {} and mgr._tab_locks == {}
        finally:
            await mgr.stop()

    asyncio.run(go())


# ---- Dashboard state: open_tabs + current-page isolation ----------


def test_dashboard_open_tabs_empty_when_idle(tmp_path):
    mgr = SessionManager()
    bt = BrowserTools(mgr, tmp_path)
    state = bt.state_for_dashboard()
    assert state["open_tabs"] == []


def test_dashboard_open_tabs_and_main_url_not_clobbered(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        mgr = SessionManager()
        mgr._session = _FakeTabSession()
        mgr._page = _ReadPage(body="main")  # pre-set main page; acquire reuses
        bt = BrowserTools(mgr, tmp_path)
        try:
            await bt.navigate("http://main/")
            s1 = bt.state_for_dashboard()
            assert s1["current_url"] == "http://main/"
            assert s1["open_tabs"] == []

            # A named-tab navigate must NOT clobber the main page's current
            # url/title, but it does add an open tab + a tab-tagged history row.
            await bt.navigate("http://tab-a/", tab="a")
            s2 = bt.state_for_dashboard()
            assert s2["current_url"] == "http://main/"  # unchanged
            assert s2["open_tabs"] == [{"name": "a", "url": "http://tab-a/"}]
            recents = s2["recent_navigations"]
            assert recents[0].get("tab") == "a"  # newest first, the tab nav
            assert "tab" not in recents[1]  # the main-page nav
        finally:
            await mgr.stop()

    asyncio.run(go())


# --- generation-scoped nav-timeout streak (#55 x #57) --------------
# Per-tab locks let K navigations time out concurrently on a wedged engine.
# The first to cross the recycle threshold resets the streak and nulls the
# session, then yields at sess.close(); without generation scoping the
# still-in-flight timeouts increment the FRESH session's streak (a phantom
# streak that force-recycles it after one legitimate timeout). Ops capture
# SessionManager.generation at acquire time and pass it to
# record_navigation_{timeout,success}, which drop a recording whose
# generation no longer matches the live session.


def test_record_timeout_stale_generation_is_dropped(monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    monkeypatch.setattr(
        session_mod, "navigation_timeout_recycle_threshold", lambda: 3
    )

    async def go():
        mgr = SessionManager()
        mgr._session = _FakeCloseSession()
        stale = mgr.generation
        # A teardown bumps the generation; the captured token is now stale.
        await mgr.recycle(reason="test")
        assert mgr.generation != stale
        # A timeout tagged with the dead generation neither counts nor recycles.
        assert await mgr.record_navigation_timeout(stale) is False
        assert mgr._nav_timeout_streak == 0
        # The live generation counts as usual (and a None token is
        # unconditional — the direct-call contract existing tests rely on).
        mgr._session = _FakeCloseSession()
        assert await mgr.record_navigation_timeout(mgr.generation) is False
        assert mgr._nav_timeout_streak == 1
        assert await mgr.record_navigation_timeout(None) is False
        assert mgr._nav_timeout_streak == 2

    asyncio.run(go())


def test_generation_scoped_streak_survives_concurrent_timeout_burst(monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    monkeypatch.setattr(
        session_mod, "navigation_timeout_recycle_threshold", lambda: 3
    )

    class _YieldingCloseSession:
        """close() yields (asyncio.sleep(0)) so a recycle triggered mid-burst
        suspends AFTER it has reset the streak and bumped the generation — the
        exact window the still-in-flight timeouts race into."""

        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            await asyncio.sleep(0)

    async def go():
        mgr = SessionManager()
        session = _YieldingCloseSession()
        mgr._session = session
        mgr._page = object()
        gen = mgr.generation  # captured while the session is live, as an op does

        # Five navigations time out concurrently on the wedged engine. The 3rd
        # crosses the threshold and recycles; while its bounded close() is
        # suspended, the last two in-flight timeouts run. They carry the
        # now-stale generation, so the gate drops them: exactly one recycle
        # fires and the fresh session's streak stays 0. Remove the generation
        # gate (count unconditionally) and those two would land on the fresh
        # generation, leaving a phantom streak of 2 — the streak assertion
        # below then fails. That is why this test pins the fix, by construction.
        results = await asyncio.gather(
            *(mgr.record_navigation_timeout(gen) for _ in range(5))
        )

        assert sum(1 for r in results if r) == 1
        assert session.close_calls == 1
        assert mgr._session is None
        assert mgr._nav_timeout_streak == 0

    asyncio.run(go())


def test_record_success_stale_generation_does_not_clear_live_streak(monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    monkeypatch.setattr(
        session_mod, "navigation_timeout_recycle_threshold", lambda: 5
    )

    async def go():
        mgr = SessionManager()
        mgr._session = _FakeCloseSession()
        stale = mgr.generation
        await mgr.recycle(reason="test")  # bumps the generation
        # A fresh session builds a legitimate streak.
        mgr._session = _FakeCloseSession()
        live = mgr.generation
        assert await mgr.record_navigation_timeout(live) is False
        assert await mgr.record_navigation_timeout(live) is False
        assert mgr._nav_timeout_streak == 2
        # A late success from the DEAD generation must not wipe it.
        mgr.record_navigation_success(stale)
        assert mgr._nav_timeout_streak == 2
        # A success from the LIVE generation clears it.
        mgr.record_navigation_success(live)
        assert mgr._nav_timeout_streak == 0

    asyncio.run(go())


def test_run_action_threads_captured_generation(tmp_path, monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    monkeypatch.setattr(
        session_mod, "navigation_timeout_recycle_threshold", lambda: 3
    )
    _patch_render_captcha(monkeypatch)

    captured: dict[str, list] = {"timeout": [], "success": []}

    class _CapturingManager(SessionManager):
        async def record_navigation_timeout(self, generation=None):
            captured["timeout"].append(generation)
            return await super().record_navigation_timeout(generation)

        def record_navigation_success(self, generation=None):
            captured["success"].append(generation)
            return super().record_navigation_success(generation)

    async def go():
        mgr = _CapturingManager()
        page = _ReadPage()
        _install_fake_acquire(monkeypatch, mgr, _FakeCloseSession(), page)
        bt = BrowserTools(mgr, tmp_path)
        gen = mgr.generation

        # A navigation timeout threads the acquire-time generation through.
        page.goto_exc = _PlaywrightTimeoutError
        assert (await bt.navigate("http://x/1"))["ok"] is False
        assert captured["timeout"] == [gen]

        # So does a navigation success.
        page.goto_exc = None
        assert (await bt.navigate("http://x/2"))["ok"] is True
        assert captured["success"] == [gen]

    asyncio.run(go())


# --- failed tab open leaves no phantom (#57) -----------------------
# acquire_tab registers the page BEFORE the goto runs, so a failed create-path
# navigate would otherwise strand a phantom about:blank tab that browser_tabs
# lists and that burns a max_tabs slot. A FAILED open must leave nothing
# behind; a REUSED tab whose re-navigation fails must stay (the caller had it).


def test_failed_tab_open_leaves_no_phantom_and_frees_slot(tmp_path, monkeypatch):
    import asyncio

    from vexis_agent.tools.browser import session as session_mod

    _patch_render_captcha(monkeypatch)
    monkeypatch.setattr(session_mod, "max_tabs", lambda: 1)

    async def go():
        failing = _ReadPage(goto_exc=RuntimeError("nav boom"))
        good = _ReadPage(body="second")
        mgr = SessionManager()
        mgr._session = _FakeTabSession(pages=[failing, good])
        bt = BrowserTools(mgr, tmp_path)
        try:
            # A failed create-path navigate returns the error payload...
            bad = await bt.navigate("http://a/", tab="a")
            assert bad["ok"] is False
            # ...and leaves NO tab behind: registry + listing empty, and the
            # just-created page was closed.
            assert list(mgr._tabs) == []
            assert mgr.list_open_tabs() == []
            assert failing.is_closed() is True
            # The slot is freed — a follow-up create succeeds even at cap 1.
            ok = await bt.navigate("http://b/", tab="b")
            assert ok["ok"] is True
            assert list(mgr._tabs) == ["b"]
        finally:
            await mgr.stop()

    asyncio.run(go())


def test_reused_tab_failed_renavigation_keeps_tab(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        page = _ReadPage(body="first")
        mgr = SessionManager()
        mgr._session = _FakeTabSession(pages=[page])
        bt = BrowserTools(mgr, tmp_path)
        try:
            # First navigate creates the tab and succeeds.
            assert (await bt.navigate("http://a/1", tab="a"))["ok"] is True
            assert list(mgr._tabs) == ["a"]
            # A re-navigation on the EXISTING tab that fails must NOT discard
            # it — the caller had it before and may retry.
            page.goto_exc = RuntimeError("boom")
            assert (await bt.navigate("http://a/2", tab="a"))["ok"] is False
            assert list(mgr._tabs) == ["a"]
            assert page.is_closed() is False
        finally:
            await mgr.stop()

    asyncio.run(go())


def test_failed_then_read_on_created_tab_keeps_tab(tmp_path, monkeypatch):
    import asyncio

    _patch_render_captcha(monkeypatch)

    async def go():
        page = _ReadPage(body="ok", missing_selectors={"#nope"})
        mgr = SessionManager()
        mgr._session = _FakeTabSession(pages=[page])
        bt = BrowserTools(mgr, tmp_path)
        try:
            # The navigate SUCCEEDS; only the bonus read fails. The create
            # succeeded, so the tab stays.
            out = await bt.navigate("http://a/", tab="a", then_read="#nope")
            assert out["ok"] is True
            assert out["read"]["ok"] is False
            assert list(mgr._tabs) == ["a"]
            assert page.is_closed() is False
        finally:
            await mgr.stop()

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


# --- issue #57 real-browser end-to-end (opt-in) ---------------------


@pytest.mark.skipif(not E2E, reason="set VEXIS_BROWSER_E2E=1 to run real browser e2e")
def test_e2e_navigate_then_read_single_roundtrip(tmp_path):
    # A navigate with then_read returns the nav fields AND the page text in
    # ONE call — the batched-read lever against the real engine.
    import asyncio

    page_html = tmp_path / "catalog.html"
    page_html.write_text(
        "<html><body>"
        "<h1>Catalog</h1>"
        "<div id='results'>"
        "<div class='row'>11427512300 Oliefilterelement</div>"
        "<div class='row'>34116858652 Remblok set voor</div>"
        "</div></body></html>"
    )
    url = page_html.as_uri()

    async def go():
        mgr = SessionManager()
        bt = BrowserTools(mgr, tmp_path)
        try:
            out = await bt.navigate(url, then_read="body")
            assert out["ok"] is True, out
            assert out["url"], out
            # The batched read rode along in the same call.
            assert out["read"]["ok"] is True, out
            assert "11427512300" in out["read"]["text"]
            assert "34116858652" in out["read"]["text"]
            assert out["read"]["selector"] == "body"
            assert out["read"]["chars"] == len(out["read"]["text"])

            # Scoped batched read on a click, too.
            scoped = await bt.navigate(url, then_read="#results")
            assert scoped["read"]["ok"] is True
            assert "Oliefilterelement" in scoped["read"]["text"]

            # A bad selector fails only the bonus read, never the navigation.
            miss = await bt.navigate(url, then_read="#nope")
            assert miss["ok"] is True
            assert miss["read"]["ok"] is False
        finally:
            await mgr.stop()

    asyncio.run(go())


@pytest.mark.skipif(not E2E, reason="set VEXIS_BROWSER_E2E=1 to run real browser e2e")
def test_e2e_wait_until_domcontentloaded(tmp_path):
    # The cheap nav wait: navigate with wait_until="domcontentloaded" still
    # returns ok with readable text (it just skips the networkidle settle).
    import asyncio

    page_html = tmp_path / "data.html"
    page_html.write_text(
        "<html><body><h1>Data page</h1><p id='p'>row-alpha row-beta</p></body></html>"
    )
    url = page_html.as_uri()

    async def go():
        mgr = SessionManager()
        bt = BrowserTools(mgr, tmp_path)
        try:
            nav = await bt.navigate(url, wait_until="domcontentloaded")
            assert nav["ok"] is True, nav
            read = await bt.read("#p")
            assert read["ok"] is True
            assert "row-alpha" in read["text"]
        finally:
            await mgr.stop()

    asyncio.run(go())


def _start_slow_http_server(delay_seconds: float):
    """Bind a local threaded HTTP server whose handler sleeps ``delay_seconds``
    before replying, serving a per-path body so each tab gets distinct content.

    Returns ``(server, port)``; the caller shuts it down in a ``finally``. No
    external network — 127.0.0.1 only."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib callback name
            time.sleep(delay_seconds)
            body = (
                f"<html><body><h1>path {self.path}</h1>"
                f"<p>content-for{self.path}</p></body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence the default stderr logging
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


@pytest.mark.skipif(not E2E, reason="set VEXIS_BROWSER_E2E=1 to run real browser e2e")
def test_e2e_parallel_tabs_no_state_bleed(tmp_path):
    # Three tabs opened CONCURRENTLY (asyncio.gather) against a local server
    # whose handler sleeps ~1.5s. Asserts: (a) each tab's read returns its own
    # page's distinct content, (b) total wall time is well under 3x the
    # per-page delay (overlap proof), (c) browser_tabs lists them, (d) closing
    # one leaves the others + the main page working.
    import asyncio
    import time

    delay = 1.5
    server, port = _start_slow_http_server(delay)
    base = f"http://127.0.0.1:{port}"

    async def go():
        mgr = SessionManager()
        bt = BrowserTools(mgr, tmp_path)
        try:
            started = time.monotonic()
            results = await asyncio.gather(
                bt.navigate(f"{base}/a", tab="a", wait_until="domcontentloaded"),
                bt.navigate(f"{base}/b", tab="b", wait_until="domcontentloaded"),
                bt.navigate(f"{base}/c", tab="c", wait_until="domcontentloaded"),
            )
            elapsed = time.monotonic() - started
            for r in results:
                assert r["ok"] is True, r
            # (b) overlap: three ~1.5s pages loaded concurrently finish well
            # under the 4.5s a serial run would take (generous margin).
            assert elapsed < 3 * delay, elapsed

            # (a) each tab holds its own page's distinct content.
            ra = await bt.read(tab="a")
            rb = await bt.read(tab="b")
            rc = await bt.read(tab="c")
            assert "content-for/a" in ra["text"]
            assert "content-for/b" in rb["text"]
            assert "content-for/c" in rc["text"]

            # (c) browser_tabs lists all three.
            listing = await bt.tabs()
            assert listing["ok"] is True
            assert {t["name"] for t in listing["tabs"]} == {"a", "b", "c"}

            # (d) closing one leaves the others + the main page working.
            assert (await bt.tab_close("b"))["ok"] is True
            gone = await bt.read(tab="b")
            assert gone["ok"] is False
            assert (await bt.read(tab="a"))["ok"] is True
            main = await bt.navigate(f"{base}/main", wait_until="domcontentloaded")
            assert main["ok"] is True, main
        finally:
            await mgr.stop()
            server.shutdown()
            server.server_close()

    asyncio.run(go())
