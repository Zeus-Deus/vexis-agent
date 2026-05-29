"""Singleton ``SessionManager`` for the Vexis Camoufox browser session.

Holds at most one live ``AsyncStealthySession`` per daemon process, plus
the single long-lived page we drive across actions. scrapling's own
``fetch()`` opens and closes a page per call — that's right for scrape-
and-go, wrong for an interactive navigate → snapshot → click loop where
element indices must stay valid between calls. So we take the persistent
Camoufox context scrapling launches (``session.context``) and keep one
page open on top of it; the context owns the ``user_data_dir`` profile, so
cookies and storage persist regardless of when we recycle the page.

Lazy start on the first action; an idle sweep recycles the session after
the configured inactivity window so a quiet daemon doesn't keep Firefox
resident. Login state lives in ``user_data_dir`` on disk, so recycling is
cheap — cookies survive.

Concurrency: ``action_lock`` serializes browser actions. The Telegram
message queue already serializes turns of one chat; the lock guards the
rarer case where one turn fires multiple browser tools, which would race
over the shared page.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from vexis_agent.core.logging import redact_sensitive_logs
from vexis_agent.tools.browser.profile import (
    action_timeout_seconds,
    headless,
    inactivity_timeout_seconds,
    session_kwargs,
)

if TYPE_CHECKING:  # import only for type hints — see acquire() for why
    from scrapling.fetchers import AsyncStealthySession

log = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 30.0


class SessionManager:
    """Owns the live Camoufox session + persistent page (or None when idle)."""

    def __init__(self) -> None:
        self._session: AsyncStealthySession | None = None
        self._page: Any | None = None
        self._start_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()
        self._last_activity: float = 0.0
        self._sweeper: asyncio.Task | None = None
        self._stopping = False
        # Wall-clock counterparts of _last_activity (monotonic). The
        # monotonic value drives the inactivity sweep; the wall-clock
        # value powers the dashboard's "X minutes ago" rendering.
        self._started_at_wall: datetime | None = None
        self._last_activity_at_wall: datetime | None = None

    @property
    def action_lock(self) -> asyncio.Lock:
        return self._action_lock

    async def acquire(self) -> tuple[AsyncStealthySession, Any]:
        """Return ``(session, page)``, lazy-starting both if necessary.

        Recreates the page if it was closed underneath us (e.g. a site
        called ``window.close()``); recreates the whole session if it was
        swept while idle.
        """
        # Lazy import: scrapling 0.3.x evaluates camoufox_version() at module
        # import time, which raises FileNotFoundError until `camoufox fetch`
        # has downloaded the browser binary. Importing at module top would
        # then break importing the whole browser package — and transitively
        # the daemon (web_server/main import it) — on a host where the binary
        # isn't fetched yet. Deferring it here keeps the package importable
        # everywhere; a missing binary instead surfaces as a clean error on
        # the first actual navigate (callers wrap acquire() and normalize it).
        from scrapling.fetchers import AsyncStealthySession

        async with self._start_lock:
            if self._session is None:
                kwargs = session_kwargs()
                log.info(
                    "[browser] starting Camoufox session (profile=%s, headless=%s, "
                    "solve_cloudflare=%s)",
                    kwargs.get("user_data_dir"),
                    kwargs.get("headless"),
                    kwargs.get("solve_cloudflare"),
                )
                session = AsyncStealthySession(**kwargs)
                await session.start()
                # scrapling/Playwright log the literal text typed into form
                # fields; re-attach the redaction filter on every start so a
                # password typed during an agent-driven login never lands in
                # the journal, surviving idle-recycle.
                redact_sensitive_logs("scrapling")
                self._session = session
                self._page = None
                self._started_at_wall = datetime.now(timezone.utc)
            if self._page is None or self._page.is_closed():
                page = await self._session.context.new_page()
                # scrapling's own _get_page sets these; we bypass it by
                # taking the context directly, so apply them here. Gives
                # goto/click the full configured budget instead of
                # Playwright's 30s default, which would otherwise fire
                # before the action-timeout wrapper on a slow page.
                timeout_ms = action_timeout_seconds() * 1000
                page.set_default_navigation_timeout(timeout_ms)
                page.set_default_timeout(timeout_ms)
                self._page = page
            self._last_activity = time.monotonic()
            self._last_activity_at_wall = datetime.now(timezone.utc)
            if self._sweeper is None or self._sweeper.done():
                self._sweeper = asyncio.create_task(self._sweep_loop())
            return self._session, self._page

    async def wait_stable(self, page: Any) -> None:
        """Wait for load / DOMContentLoaded / network-idle on ``page``.

        Delegates to scrapling's own page-stability helper so we wait
        exactly the way its ``fetch()`` does.
        """
        if self._session is None:
            return
        await self._session._wait_for_page_stability(page, True, True)

    async def solve_cloudflare(self, page: Any) -> None:
        """Run scrapling's Cloudflare solver against ``page`` (best-effort).

        Same solver ``StealthySession.fetch`` invokes; no-ops when no
        challenge is present. Wrapped here so the scrapling-private call
        lives in one place.
        """
        if self._session is None:
            return
        await self._session._cloudflare_solver(page)
        await self.wait_stable(page)

    @property
    def solves_cloudflare(self) -> bool:
        return bool(getattr(self._session, "_solve_cloudflare", False))

    def mark_activity(self) -> None:
        self._last_activity = time.monotonic()
        self._last_activity_at_wall = datetime.now(timezone.utc)

    def is_running(self) -> bool:
        return self._session is not None

    def state_for_dashboard(self) -> dict:
        """Lifecycle snapshot for ``WebDashboard``. Pure read, no I/O."""
        if self._session is None:
            return {
                "state": "not_started",
                "started_at": None,
                "last_activity_at": None,
                "headless": headless(),
            }
        return {
            "state": "running",
            "started_at": (
                self._started_at_wall.isoformat()
                if self._started_at_wall is not None
                else None
            ),
            "last_activity_at": (
                self._last_activity_at_wall.isoformat()
                if self._last_activity_at_wall is not None
                else None
            ),
            "headless": headless(),
        }

    async def stop(self) -> None:
        """Tear down the live session, if any. Idempotent."""
        async with self._start_lock:
            self._stopping = True
            sess = self._session
            self._session = None
            self._page = None
            self._started_at_wall = None
            self._last_activity_at_wall = None
        sweeper = self._sweeper
        self._sweeper = None
        if sweeper is not None and not sweeper.done():
            sweeper.cancel()
            try:
                await sweeper
            except (asyncio.CancelledError, Exception):
                pass
        if sess is not None:
            try:
                await sess.close()
                log.info("[browser] session closed")
            except Exception:
                log.exception("[browser] error tearing down session")
        # Reset _stopping so a subsequent acquire() re-arms the sweep loop.
        self._stopping = False

    async def _sweep_loop(self) -> None:
        # Read inactivity_timeout each tick so test harnesses (and config
        # reloads) can adjust on the fly. Tick: min(30s, half the timeout)
        # so a short test timeout doesn't sit idle for a full 30s.
        try:
            while not self._stopping:
                timeout = inactivity_timeout_seconds()
                tick = min(_SWEEP_INTERVAL_SECONDS, max(1.0, timeout / 2))
                await asyncio.sleep(tick)
                if self._session is None:
                    continue
                idle = time.monotonic() - self._last_activity
                if idle >= timeout:
                    log.info(
                        "[browser] inactivity %.0fs >= %ds — recycling session",
                        idle,
                        timeout,
                    )
                    sess = self._session
                    self._session = None
                    self._page = None
                    self._started_at_wall = None
                    self._last_activity_at_wall = None
                    try:
                        await sess.close()
                    except Exception:
                        log.exception("[browser] error closing idle session")
        except asyncio.CancelledError:
            return


_GLOBAL_MANAGER: SessionManager | None = None


def get_manager() -> SessionManager:
    """Return the process-global ``SessionManager``, creating it on first use."""
    global _GLOBAL_MANAGER
    if _GLOBAL_MANAGER is None:
        _GLOBAL_MANAGER = SessionManager()
    return _GLOBAL_MANAGER
