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
    navigation_timeout_recycle_threshold,
    navigation_timeout_seconds,
    session_kwargs,
)

if TYPE_CHECKING:  # import only for type hints — see acquire() for why
    from scrapling.fetchers import AsyncStealthySession

log = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 30.0
# Hard ceiling on the ``session.close()`` in a force-recycle (issue #55). A
# wedged engine is exactly the case the recycle exists for, so its close must
# not be allowed to hang the recycle indefinitely — past this we log and
# abandon the reference (the OS reaps the leaked subprocess). Distinct from
# the graceful shutdown path (``stop()``), whose close stays unbounded.
_FORCE_CLOSE_TIMEOUT_SECONDS = 30.0


class _CloudflareNoiseFilter(logging.Filter):
    """Suppresses scrapling's spurious ``No Cloudflare challenge found.`` ERROR.

    scrapling logs that line at ERROR from ``_cloudflare_solver`` whenever the
    solver runs on a page with no challenge (issue #45) — a normal outcome it
    misclassifies as an error. ``SessionManager.solve_cloudflare`` now gates
    the solver behind a challenge pre-check, so in the normal flow the solver
    never runs on an unchallenged page; this filter closes the residual gaps —
    a challenge that clears between the pre-check and the solver's own
    re-detection, plus any future non-gated solver call — so the line can never
    spam the journal and bury real errors. Every other scrapling record passes
    untouched.
    """

    _NEEDLE = "No Cloudflare challenge found."

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # A malformed record elsewhere must not take down logging.
            return True
        # Drop (return False), don't merely downgrade the level: scrapling's
        # own handler emits at NOTSET, so a level-downgraded record would still
        # print. Dropping is what actually silences it.
        return self._NEEDLE not in message


def _silence_cloudflare_noise() -> None:
    """Attach :class:`_CloudflareNoiseFilter` to the ``scrapling`` logger.

    Logger-level, not handler-level: scrapling emits the line directly on the
    ``scrapling`` logger (not a descendant), so one filter there drops it
    before any handler and before it propagates to root — unlike the "Typed"
    redaction, which must go on handlers because those records come from
    descendant loggers. Idempotent: skips if the filter is already attached, so
    it is safe on every session start and self-heals if scrapling reconfigures
    its logger.
    """
    logger = logging.getLogger("scrapling")
    if not any(isinstance(f, _CloudflareNoiseFilter) for f in logger.filters):
        logger.addFilter(_CloudflareNoiseFilter())


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
        # Consecutive navigation-timeout counter (issue #55). A wedged engine
        # returns Page.goto timeouts back-to-back; once the streak hits the
        # configured threshold we force-recycle. Reset by any navigation
        # success, by a recycle, and by stop().
        self._nav_timeout_streak: int = 0
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
                # Drop scrapling's spurious "No Cloudflare challenge found."
                # ERROR (issue #45) — a normal outcome it logs as an error.
                _silence_cloudflare_noise()
                self._session = session
                self._page = None
                self._started_at_wall = datetime.now(timezone.utc)
            if self._page is None or self._page.is_closed():
                page = await self._session.context.new_page()
                # scrapling's own _get_page sets these; we bypass it by
                # taking the context directly, so apply them here. Two
                # distinct budgets on purpose:
                #   * navigation timeout (default 30s) bounds goto/back and
                #     the load/networkidle waits — a navigation must fail
                #     fast, not creep toward the action ceiling.
                #   * default timeout (action timeout, default 120s) bounds
                #     interactions (click/type/fill); a deliberate, slow
                #     action gets the generous budget the navigation can't.
                page.set_default_navigation_timeout(
                    navigation_timeout_seconds() * 1000
                )
                page.set_default_timeout(action_timeout_seconds() * 1000)
                self._page = page
            self._last_activity = time.monotonic()
            self._last_activity_at_wall = datetime.now(timezone.utc)
            if self._sweeper is None or self._sweeper.done():
                self._sweeper = asyncio.create_task(self._sweep_loop())
            return self._session, self._page

    async def wait_stable(self, page: Any) -> None:
        """Wait for load / DOMContentLoaded / network-idle on ``page``.

        Delegates to scrapling's own page-stability helper so we settle the
        page the way its ``fetch()`` does — but BOUNDED and BEST-EFFORT.
        The caller's ``goto`` already awaited DOMContentLoaded, so the page
        is usable; the trailing ``load`` + ``networkidle`` waits are a
        nicety. A chat/feed page with long-lived sockets never reaches
        networkidle, and scrapling's helper lets that wait run to the page
        default timeout, so without a cap a single navigation would block
        for the full action timeout. We cap it at the navigation budget and
        swallow the timeout: settle if we can, proceed regardless.
        """
        if self._session is None:
            return
        budget = navigation_timeout_seconds()
        try:
            await asyncio.wait_for(
                self._session._wait_for_page_stability(page, True, True),
                timeout=budget,
            )
        except Exception as exc:  # asyncio.TimeoutError + Playwright timeouts
            # CancelledError is BaseException, so a real cancellation still
            # propagates; only the stability wait's own failure is absorbed.
            log.debug("[browser] page-stability wait capped at %ss: %s", budget, exc)

    async def solve_cloudflare(self, page: Any) -> None:
        """Run scrapling's Cloudflare solver against ``page`` — but only when a
        challenge is actually present (issue #45).

        scrapling's ``_cloudflare_solver`` unconditionally waits up to 5s for
        network-idle and then treats "no challenge" as an ``ERROR`` — so on a
        browsing-heavy daemon the common case (a page with no challenge) pays
        ~5s and emits a spurious ``ERROR: No Cloudflare challenge found.`` on
        nearly every navigation. We gate it: the caller already ran
        ``wait_stable`` (load + domcontentloaded + network-idle) before us, so
        the page is settled and any challenge is already in its content. We run
        scrapling's *own* ``_detect_cloudflare`` classifier on that content —
        the same one the solver uses, so our skip-decision agrees with what the
        solver would find — and invoke the full solver only when it detects a
        challenge. No challenge → return immediately, paying neither the wait
        nor the ERROR. A genuinely challenged page still gets the full solve.

        Wrapped here so the scrapling-private calls live in one place.
        """
        if self._session is None:
            return
        if not await self._has_cloudflare_challenge(page):
            return
        await self._session._cloudflare_solver(page)
        await self.wait_stable(page)

    async def _has_cloudflare_challenge(self, page: Any) -> bool:
        """Wait-free pre-check: does ``page`` currently show a CF challenge?

        Reuses scrapling's own ``_detect_cloudflare`` string classifier so our
        verdict matches what the solver would decide — but against the
        already-settled page content, skipping the solver's 5s network-idle
        wait. Fail-safe: if the scrapling-private classifier is absent (version
        drift) or reading the content raises, return ``True`` so the full
        solver still runs. When unsure, solving a real challenge beats saving
        the wait.
        """
        session = self._session
        if session is None:
            return False
        detect = getattr(session, "_detect_cloudflare", None)
        if detect is None:
            return True
        try:
            content = await page.content()
        except Exception:
            return True
        try:
            return detect(content or "") is not None
        except Exception:
            return True

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
            self._nav_timeout_streak = 0
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

    async def recycle(self, *, reason: str) -> bool:
        """Force-drop the live session so the next action lazy-starts a fresh
        one. Returns True iff a live session was actually torn down.

        Distinct from ``stop()``: this is the *recover* path (idle-sweep
        analogue, wedged-engine / manual recycle), NOT shutdown. It leaves
        the sweeper running — with ``_session`` None its loop simply skips —
        so the daemon keeps its inactivity discipline after a recycle. Login
        state lives in ``user_data_dir`` on disk, so a recycle is cheap:
        cookies and storage survive.

        The teardown is two-phase on purpose. Under ``_start_lock`` we swap
        the session/page references out to None and reset the timeout streak
        — a quick, non-blocking critical section. The actual
        ``session.close()`` runs OUTSIDE the lock and BOUNDED by
        ``_FORCE_CLOSE_TIMEOUT_SECONDS``: a wedged engine (the exact case this
        exists for, issue #55) must never be able to hang the recycle on its
        own close. On timeout/exception we log and abandon the reference.

        Lock order: callers (``record_navigation_timeout``) hold
        ``_action_lock`` when they call this; we take ``_start_lock``. Safe
        only because nothing ever holds ``_start_lock`` while waiting on
        ``_action_lock`` — keep it that way.
        """
        async with self._start_lock:
            sess = self._session
            self._session = None
            self._page = None
            self._started_at_wall = None
            self._last_activity_at_wall = None
            self._nav_timeout_streak = 0
        if sess is None:
            return False
        try:
            await asyncio.wait_for(
                sess.close(), timeout=_FORCE_CLOSE_TIMEOUT_SECONDS
            )
        except Exception:
            # asyncio.TimeoutError included: a wedged close is expected here,
            # so log-and-abandon rather than propagate — the OS reaps the
            # leaked subprocess; the fresh session starts clean regardless.
            log.exception(
                "[browser] force-close during recycle failed or timed out; "
                "abandoning old session (reason=%s)",
                reason,
            )
        log.info("[browser] session recycled (reason=%s)", reason)
        return True

    def record_navigation_success(self) -> None:
        """A navigation succeeded — clear the consecutive-timeout streak."""
        self._nav_timeout_streak = 0

    async def record_navigation_timeout(self) -> bool:
        """Count one navigation timeout; force-recycle once the streak trips
        the configured threshold (issue #55). Returns True iff it recycled.

        Threshold is re-read per call so a config edit hot-reloads at the
        next timeout (same discipline as the sweeper's per-tick read).
        ``threshold <= 0`` disables the feature: we don't even count.

        Called while the caller holds ``_action_lock`` — see ``recycle`` for
        the lock-order note.
        """
        threshold = navigation_timeout_recycle_threshold()
        if threshold <= 0:
            return False
        self._nav_timeout_streak += 1
        if self._nav_timeout_streak < threshold:
            return False
        log.warning(
            "[browser] %d consecutive navigation timeouts (threshold %d) — "
            "force-recycling wedged session",
            self._nav_timeout_streak,
            threshold,
        )
        await self.recycle(reason="consecutive navigation timeouts")
        return True

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
                    # Reset the streak like every other teardown path (issue
                    # #55): it counts consecutive timeouts on the *currently-
                    # live* session, and this one is gone. Leaving it set would
                    # leak a stale count into the next fresh session and could
                    # force-recycle it after a single legitimate timeout.
                    self._nav_timeout_streak = 0
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
