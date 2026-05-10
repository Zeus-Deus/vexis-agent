"""Singleton ``SessionManager`` for the Vexis browser-use BrowserSession.

Holds at most one live ``BrowserSession`` per daemon process. Lazy
start on the first action; idle sweep recycles the session after the
configured inactivity window so a quiet daemon doesn't keep Chromium
resident. Login state lives in ``user_data_dir`` so recycling is
cheap — cookies and storage survive on disk regardless.

Concurrency: ``action_lock`` serializes browser actions. browser-use's
own dispatch is async, but multiple concurrent clicks against one
session race over selector_map state. The Telegram message queue
already serializes turns of one chat; the lock here protects against
the (rare) case where Vexis fires multiple browser tools in parallel
inside one turn.

Telemetry: ``ANONYMIZED_TELEMETRY=false`` is set at module import per
§10 of the browser-research doc.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

from browser_use import BrowserSession  # noqa: E402

from vexis_agent.tools.browser.profile import (  # noqa: E402
    build_profile,
    cdp_url,
    headless,
    inactivity_timeout_seconds,
)

log = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 30.0


class SessionManager:
    """Owns the live browser-use session (or holds None when idle)."""

    def __init__(self) -> None:
        self._session: BrowserSession | None = None
        self._start_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()
        self._last_activity: float = 0.0
        self._sweeper: asyncio.Task | None = None
        self._stopping = False
        # True when the live session is attached to an externally-
        # launched Chrome via CDP. In that mode we never kill the
        # process and skip the idle sweep — the user owns lifecycle.
        self._attached_to_cdp = False
        # Wall-clock counterparts of _last_activity (monotonic). The
        # monotonic value drives the inactivity sweep; the wall-clock
        # value powers the dashboard's "X minutes ago" rendering.
        self._started_at_wall: datetime | None = None
        self._last_activity_at_wall: datetime | None = None

    @property
    def action_lock(self) -> asyncio.Lock:
        return self._action_lock

    async def get(self) -> BrowserSession:
        """Return the live session, lazy-starting if necessary."""
        async with self._start_lock:
            if self._session is None:
                profile = build_profile()
                attaching = bool(cdp_url())
                log.info(
                    "[browser] starting session (cdp_url=%s, profile=%s, headless=%s)",
                    cdp_url() or "(none)",
                    profile.user_data_dir or "(cdp-attach)",
                    profile.headless,
                )
                session = BrowserSession(browser_profile=profile)
                await session.start()
                self._session = session
                self._attached_to_cdp = attaching
                self._started_at_wall = datetime.now(timezone.utc)
            self._last_activity = time.monotonic()
            self._last_activity_at_wall = datetime.now(timezone.utc)
            # Don't run the inactivity sweep when attached: the user
            # owns the Chrome process and we shouldn't be poking at
            # its lifecycle from a timer.
            if not self._attached_to_cdp and (
                self._sweeper is None or self._sweeper.done()
            ):
                self._sweeper = asyncio.create_task(self._sweep_loop())
            return self._session

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
                "attached_to_cdp": False,
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
            "attached_to_cdp": self._attached_to_cdp,
            "headless": headless(),
        }

    async def stop(self) -> None:
        """Tear down the live session, if any. Idempotent.

        When attached to an externally-launched Chrome via ``cdp_url``,
        we ``session.stop()`` (disconnect, leave the process alive)
        instead of ``session.kill()`` — the user owns that process.
        """
        async with self._start_lock:
            self._stopping = True
            sess = self._session
            attached = self._attached_to_cdp
            self._session = None
            self._attached_to_cdp = False
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
                if attached:
                    await sess.stop()
                    log.info("[browser] CDP session detached (Chrome left running)")
                else:
                    await sess.kill()
                    log.info("[browser] session killed")
            except Exception:
                log.exception("[browser] error tearing down session")
        # Reset _stopping so a subsequent get() re-arms the sweep loop.
        # Without this, a recycle-then-reopen sequence (e.g. via the
        # dashboard) leaves the new session without an inactivity sweep.
        self._stopping = False

    async def _sweep_loop(self) -> None:
        # Read inactivity_timeout each tick so test harnesses (and
        # later config reloads) can adjust on the fly.
        # Sweep tick: min(30s, half the timeout) so a 30s test
        # timeout doesn't sit idle for a full 30s before the first
        # check. For the production 120s timeout this resolves to 30s.
        try:
            while not self._stopping:
                timeout = inactivity_timeout_seconds()
                tick = min(_SWEEP_INTERVAL_SECONDS, max(1.0, timeout / 2))
                await asyncio.sleep(tick)
                if self._session is None or self._attached_to_cdp:
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
                    try:
                        await sess.kill()
                    except Exception:
                        log.exception("[browser] error killing idle session")
        except asyncio.CancelledError:
            return


_GLOBAL_MANAGER: SessionManager | None = None


def get_manager() -> SessionManager:
    """Return the process-global ``SessionManager``, creating it on first use."""
    global _GLOBAL_MANAGER
    if _GLOBAL_MANAGER is None:
        _GLOBAL_MANAGER = SessionManager()
    return _GLOBAL_MANAGER
