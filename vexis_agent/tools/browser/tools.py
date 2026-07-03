"""``BrowserTools`` — the browser actions the brain drives via Bash.

Each method is the in-process implementation behind one ``browser_*`` op
the control socket dispatches. The CLI client (``tools.browser_cli``) is
the thin shell that gets these results back to the brain via JSON over a
Unix socket.

The engine underneath is scrapling's Camoufox ``StealthySession`` — we
hold one persistent page (see ``session.SessionManager``) and drive it
with the Playwright API directly. The public surface and JSON shapes are
unchanged from the browser-use era:

- success: ``{"ok": True, ...}``
- failure: ``{"ok": False, "error": "...", "hint": "..."}``
- soft hint: ``{"ok": True, "snapshot_stale": True, "suggestion": "..."}``
  (on click/type/press when the indexed element has vanished)

Per-action wall-clock timeout comes from
``profile.action_timeout_seconds()`` (default 120s, configurable in
``~/.vexis/config.yaml`` ``[browser]``).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from vexis_agent.core import yaml_config
from vexis_agent.tools.browser import snapshot as snapshot_mod
from vexis_agent.tools.browser.captcha import apply_captcha
from vexis_agent.tools.browser.errors import (
    FORCE_RECYCLE_HINT,
    TAB_NAME_RE,
    InvalidTabNameError,
    TabError,
    TabLimitError,
    TabNotFoundError,
    error_payload,
    invalid_tab_name_payload,
    is_timeout,
    normalize_exception,
    stale_index_payload,
    tab_limit_payload,
    tab_not_found_payload,
)
from vexis_agent.tools.browser.profile import (
    action_timeout_seconds,
    navigation_timeout_seconds,
    screenshots_dir,
)
from vexis_agent.tools.browser.session import SessionManager
from vexis_agent.tools.browser.snapshot import INDEX_ATTR

log = logging.getLogger(__name__)

_RECENT_NAVIGATIONS_MAX = 10

#: Accepted values for ``navigate``'s ``wait_until`` (issue #57). ``settle``
#: (default/absent) keeps today's byte-for-byte path — goto(domcontentloaded)
#: + the bounded ``wait_stable`` settle + the Cloudflare gate. The two cheap
#: modes skip ``wait_stable``: ``domcontentloaded`` returns as soon as the DOM
#: parses (the fast path for catalog/data reads), ``load`` waits for the load
#: event. The CF gate + captcha layer stay on for every mode.
_WAIT_UNTIL_MODES = ("domcontentloaded", "load", "settle")


class BrowserTools:
    """Daemon-side implementation of the browser_* control-socket ops."""

    def __init__(self, manager: SessionManager, workspace: Path) -> None:
        self._manager = manager
        self._workspace = workspace
        # Cached page metadata, refreshed on every action that returns a
        # snapshot. The dashboard reads these without touching the live
        # session — so an inspection request can't race with a click in
        # flight.
        self._current_url: str | None = None
        self._current_title: str | None = None
        # Recent URL ring buffer. Append on successful navigate/back;
        # newest entry first when serialized. The deque enforces the cap.
        self._recent_navigations: deque[dict[str, str]] = deque(
            maxlen=_RECENT_NAVIGATIONS_MAX
        )

    @property
    def manager(self) -> SessionManager:
        """Expose the underlying SessionManager (used by the dashboard)."""
        return self._manager

    def state_for_dashboard(self) -> dict[str, Any]:
        """Cached page metadata for ``WebDashboard``. Pure read, no I/O.

        Once the session is gone (idle sweep, manual recycle), the cached
        URL/title describe a page that no longer exists — so they're
        suppressed even though the values still sit in this instance.
        Recent navigations stay; they're history, not live state.

        ``open_tabs`` (issue #57) is the live named-tab list, sourced from a
        sync best-effort ``SessionManager`` helper (no awaits, never raises);
        empty when the session isn't running. ``current_url``/``current_title``
        track the MAIN page only — named-tab activity never clobbers them.
        """
        running = self._manager.is_running()
        return {
            "current_url": self._current_url if running else None,
            "current_title": self._current_title if running else None,
            "recent_navigations": list(reversed(self._recent_navigations)),
            "open_tabs": self._manager.list_open_tabs() if running else [],
        }

    async def navigate(
        self,
        url: str,
        wait_until: str | None = None,
        then_read: str | None = None,
        tab: str | None = None,
    ) -> dict[str, Any]:
        """Navigate ``url`` (issue #57 adds three optional levers).

        ``wait_until`` — ``"settle"`` (default/absent) keeps today's path
        (goto + bounded ``wait_stable`` + Cloudflare gate); ``"domcontentloaded"``
        / ``"load"`` skip ``wait_stable`` for a cheap nav on catalog/data
        pages. ``then_read`` — a CSS selector (``"body"`` = whole body) read
        in the SAME lock hold as the navigation, so a navigate+read is one
        round-trip; a failed bonus read never fails the navigation. ``tab`` —
        a named parallel tab (created here) instead of the main page.
        """
        if not isinstance(url, str) or not url.strip():
            return error_payload("missing or empty 'url'")
        target = url.strip()
        mode = "settle" if wait_until is None else wait_until
        if mode not in _WAIT_UNTIL_MODES:
            return error_payload(
                "'wait_until' must be one of: domcontentloaded, load, settle"
            )
        if then_read is not None and not isinstance(then_read, str):
            return error_payload("'then_read' must be a string")

        async def op(page: Any) -> dict[str, Any]:
            if mode == "settle":
                # goto() returns at DOMContentLoaded; wait_stable adds the
                # bounded load/networkidle settle the interactive loop wants.
                await page.goto(target, wait_until="domcontentloaded")
                await self._manager.wait_stable(page)
            else:
                # Cheap path: return as soon as the DOM parses (or the load
                # event fires) — no networkidle settle. The CF gate below
                # still runs (it's wait-free on unchallenged pages since #45).
                await page.goto(target, wait_until=mode)
            if self._manager.solves_cloudflare:
                try:
                    await self._manager.solve_cloudflare(page)
                except Exception as exc:  # solver is best-effort
                    log.warning("[browser] cloudflare solve note: %s", exc)
            result = await snapshot_mod.render(page)
            # Pluggable third-party captcha layer (CapSolver / 2Captcha) for
            # the families scrapling's Cloudflare pass doesn't cover. Detects
            # the captcha and either solves it (when configured) or attaches a
            # hint pointing at the dashboard. Best-effort: a solver fault must
            # not fail a navigation that otherwise loaded.
            try:
                result = await apply_captcha(page, target, result)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("[browser] captcha layer note: %s", exc)
            if then_read is not None:
                result["read"] = await self._safe_read(page, then_read)
            return result

        result = await self._run_action(
            "navigate",
            op,
            navigation=True,
            tab=tab,
            create_tab=tab is not None,
        )
        if result.get("ok"):
            # Named-tab activity must not clobber the dashboard's "current
            # page" — only the main page updates current_url/title.
            if tab is None:
                self._update_current_page(result)
            self._record_navigation(result.get("url") or target, tab=tab)
        return result

    async def snapshot(
        self, full: bool = False, tab: str | None = None
    ) -> dict[str, Any]:
        # ``full`` is accepted for forward-compat with the v1 spec but the
        # serializer already emits one DSL form regardless. Kept for API
        # stability. ``tab`` targets a named tab (issue #57).
        del full
        result = await self._run_action(
            "snapshot", lambda page: snapshot_mod.render(page), tab=tab
        )
        if result.get("ok") and tab is None:
            self._update_current_page(result)
        return result

    async def click(
        self,
        index: int,
        js: bool = False,
        then_read: str | None = None,
        tab: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(index, int):
            return error_payload("'index' must be an integer")
        if then_read is not None and not isinstance(then_read, str):
            return error_payload("'then_read' must be a string")
        # js=True fires the element's own click() handler from JS, bypassing
        # Playwright's actionability checks. A normal click is hit-tested
        # against the topmost element at the target point, so a full-screen
        # consent/cookie overlay swallows it and the click times out; the JS
        # path goes straight to the element. Use it when a normal click hangs
        # on an overlay-covered page.
        if js:
            op = lambda loc: loc.evaluate("el => el.click()")
        else:
            op = lambda loc: loc.click()
        return await self._run_indexed_action(
            "click", index, op, then_read=then_read, tab=tab
        )

    @staticmethod
    async def _read_text(page: Any, selector: str | None) -> dict[str, Any]:
        """Extract the rendered text of ``selector`` (default ``body``).

        The single text-extraction primitive shared by ``read``, the
        navigate ``then_read`` batch, and the click ``then_read`` batch, so
        the "whole body vs. CSS selector, missing selector is an error"
        semantics can't drift between them. Returns
        ``{text, selector, chars}``; raises ``ValueError`` when a non-body
        selector matches nothing (a fast, clean failure, not a 120s hang).
        """
        sel = (selector or "").strip() or "body"
        if sel != "body":
            if await page.locator(sel).count() == 0:
                raise ValueError(f"no element matches selector {sel!r}")
            text = await page.locator(sel).first.inner_text()
        else:
            text = await page.inner_text("body")
        text = text or ""
        return {"text": text, "selector": sel, "chars": len(text)}

    async def _safe_read(self, page: Any, selector: str) -> dict[str, Any]:
        """Best-effort bonus read for a navigate/click batch (issue #57).

        A failed read (bad selector, detached node) must NEVER fail the
        parent action that already succeeded, so this swallows the exception
        and reports it inside the sub-payload instead: ``{ok: True, text,
        selector, chars}`` on success, ``{ok: False, error}`` on failure.
        """
        try:
            data = await self._read_text(page, selector)
            return {"ok": True, **data}
        except Exception as exc:
            msg = str(exc).splitlines()[0].strip() or type(exc).__name__
            return {"ok": False, "error": msg}

    async def read(
        self, selector: str | None = None, tab: str | None = None
    ) -> dict[str, Any]:
        """Return the rendered text of ``selector`` (default ``body``).

        The snapshot DSL only serializes interactive/visible elements, so a
        page that renders its payload as plain ``<div>``/``<tr>`` text comes
        back nearly empty and the brain wrongly falls back to a screenshot.
        ``read`` is the fast, lossless escape hatch: ``page.inner_text`` of
        the body (or a CSS selector) returns the same text a vanilla driver
        would, in a few ms, no vision round-trip. ``tab`` reads a named tab
        (issue #57).
        """
        if selector is not None and not isinstance(selector, str):
            return error_payload("'selector' must be a string")

        async def op(page: Any) -> dict[str, Any]:
            data = await self._read_text(page, selector)
            return {**data, "url": page.url or ""}

        result = await self._run_action("read", op, tab=tab)
        if result.get("ok") and tab is None:
            self._update_current_page(result)
        return result

    async def type(
        self,
        index: int,
        text: str,
        clear: bool = True,
        tab: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(index, int):
            return error_payload("'index' must be an integer")
        if not isinstance(text, str):
            return error_payload("'text' must be a string")

        async def op(loc: Any) -> None:
            if clear:
                await loc.fill(text)
            else:
                await loc.click()
                await loc.press_sequentially(text)

        return await self._run_indexed_action("type", index, op, tab=tab)

    async def press(self, key: str, tab: str | None = None) -> dict[str, Any]:
        if not isinstance(key, str) or not key.strip():
            return error_payload("missing or empty 'key'")
        chord = key.strip()
        return await self._run_action(
            "press", lambda page: page.keyboard.press(chord), tab=tab
        )

    async def back(self, tab: str | None = None) -> dict[str, Any]:
        async def op(page: Any) -> dict[str, Any]:
            await page.go_back(wait_until="domcontentloaded")
            await self._manager.wait_stable(page)
            return {"url": page.url or ""}

        result = await self._run_action("back", op, navigation=True, tab=tab)
        if result.get("ok"):
            if tab is None:
                self._update_current_page(result)
            url = result.get("url")
            if url:
                self._record_navigation(url, tab=tab)
        return result

    async def scroll(
        self, direction: str, pages: float = 1.0, tab: str | None = None
    ) -> dict[str, Any]:
        if direction not in ("up", "down"):
            return error_payload("'direction' must be 'up' or 'down'")
        try:
            pages_f = float(pages)
        except (TypeError, ValueError):
            return error_payload("'pages' must be a number")
        if pages_f <= 0:
            return error_payload("'pages' must be > 0")

        async def op(page: Any) -> dict[str, Any]:
            # One "page" == one viewport height. Negative dy scrolls up.
            height = await page.evaluate("() => window.innerHeight") or 720
            dy = int(height * pages_f) * (1 if direction == "down" else -1)
            await page.mouse.wheel(0, dy)
            return {}

        return await self._run_action("scroll", op, tab=tab)

    async def screenshot(
        self,
        full_page: bool = False,
        include_base64: bool | None = None,
        tab: str | None = None,
    ) -> dict[str, Any]:
        """Save a PNG to ``<workspace>/browser/screenshots/<ts>.png``.

        ``image_base64`` is OPT-IN: omitted by default to keep the JSON
        line under the brain's stream buffer. Pass ``include_base64=True``
        (CLI: ``--include-base64``) when the consumer needs the bytes
        inline. Default tracks
        ``yaml_config.browser_screenshot_include_base64()``. ``tab``
        captures a named tab (issue #57).

        Hand-rolled (not via ``_run_action``) because it returns raw bytes,
        not a JSON dict — but it resolves its page + lock through the same
        ``_acquire_for`` seam so a named tab captures correctly and its own
        lock serializes concurrent shots of that tab.
        """
        try:
            # Screenshot never creates a tab or navigates, so the generation +
            # created_tab from the seam are unused here.
            page, lock, _generation, _created_tab = await self._acquire_for(
                tab, create_tab=False
            )
        except TabError as exc:
            return self._tab_error_payload(exc)
        except Exception as exc:
            log.exception("[browser] session start failed for screenshot")
            return normalize_exception(exc, action="browser_screenshot")
        async with lock:
            try:
                raw = await asyncio.wait_for(
                    page.screenshot(full_page=bool(full_page), type="png"),
                    timeout=action_timeout_seconds(),
                )
            except Exception as exc:
                log.warning("[browser] screenshot raised: %s", exc)
                return normalize_exception(exc, action="browser_screenshot")
            finally:
                self._manager.mark_activity()
        raw = bytes(raw)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = screenshots_dir(self._workspace)
        path = out_dir / f"{ts}.png"
        try:
            path.write_bytes(raw)
        except OSError as exc:
            return error_payload(f"could not write screenshot: {exc}")
        if include_base64 is None:
            include_base64 = yaml_config.browser_screenshot_include_base64()
        payload: dict[str, Any] = {
            "ok": True,
            "path": str(path),
            "size_bytes": len(raw),
            "mime_type": "image/png",
        }
        if include_base64:
            payload["image_base64"] = base64.b64encode(raw).decode("ascii")
        return payload

    async def recycle(self) -> dict[str, Any]:
        """Force-recycle the persistent session — works even when wedged.

        Deliberately takes NO action_lock and does NOT ``acquire()``: a
        wedged engine (issue #55) is exactly when the agent reaches for this,
        and a queued action behind the lock must not gate the recovery. It
        also must NOT lazy-start a session — recycling into a fresh launch
        would be a surprising side effect. ``{ok, was_running}``; login state
        survives on disk so the next action restarts clean.
        """
        was_running = self._manager.is_running()
        await self._manager.recycle(reason="manual recycle requested")
        return {"ok": True, "was_running": was_running}

    async def tabs(self) -> dict[str, Any]:
        """List the open named tabs (issue #57). The main page is unnamed and
        not listed. Pure read — never lazy-starts a session, so on a cold
        session it returns ``{"ok": True, "tabs": []}``."""
        return {"ok": True, "tabs": self._manager.list_open_tabs()}

    async def tab_close(self, tab: str) -> dict[str, Any]:
        """Close a named tab (issue #57). Unknown tab → error payload."""
        if not isinstance(tab, str) or not TAB_NAME_RE.match(tab):
            return invalid_tab_name_payload(tab)
        try:
            closed = await self._manager.close_tab(tab)
        except TabNotFoundError as exc:
            return tab_not_found_payload(exc.name, exc.open_tabs)
        return {"ok": True, "closed": closed}

    async def _acquire_for(
        self, tab: str | None, *, create_tab: bool
    ) -> tuple[Any, asyncio.Lock, int, str | None]:
        """Resolve ``(page, lock, generation, created_tab)`` for the main page
        or a named tab.

        The one seam every page-taking op routes through so the plumbing is
        uniform: ``tab is None`` → the persistent main page under
        ``action_lock``; a name → the named tab under its own lock
        (``create_tab=True`` opens it — navigate only). Raises the
        :class:`TabError` subtypes (bad name / not found / over cap) for the
        caller to map via :meth:`_tab_error_payload`; a session-start failure
        propagates as a generic exception the caller normalizes.

        ``generation`` is the ``SessionManager`` generation captured the
        instant after the acquire (issue #57): the caller hands it to
        ``record_navigation_{timeout,success}`` so a streak update from a
        since-recycled session is discarded. ``created_tab`` is the tab name
        iff THIS acquire opened it (create path, tab was absent), else None —
        the caller discards it if the ensuing op fails, so a failed open leaves
        no phantom tab behind. The tiny window between the acquire and the
        generation read is benign: a recycle there kills the page, whose op
        then fails with a non-timeout "browser closed" error that never feeds
        the streak.
        """
        if tab is None:
            _session, page = await self._manager.acquire()
            return page, self._manager.action_lock, self._manager.generation, None
        if not isinstance(tab, str) or not TAB_NAME_RE.match(tab):
            raise InvalidTabNameError(tab if isinstance(tab, str) else str(tab))
        _session, page, created = await self._manager.acquire_tab(
            tab, create=create_tab
        )
        created_tab = tab if created else None
        return page, self._manager.tab_lock(tab), self._manager.generation, created_tab

    @staticmethod
    def _tab_error_payload(exc: TabError) -> dict[str, Any]:
        """Map a :class:`TabError` to its JSON error shape."""
        if isinstance(exc, InvalidTabNameError):
            return invalid_tab_name_payload(exc.name)
        if isinstance(exc, TabLimitError):
            return tab_limit_payload(exc.name, exc.limit, exc.open_tabs)
        if isinstance(exc, TabNotFoundError):
            return tab_not_found_payload(exc.name, exc.open_tabs)
        # Defensive: an unmapped TabError subtype still returns a clean error
        # rather than escaping the tools layer.
        return error_payload(str(exc))

    async def _run_action(
        self,
        name: str,
        op: Callable[[Any], Awaitable[Any]],
        *,
        navigation: bool = False,
        tab: str | None = None,
        create_tab: bool = False,
    ) -> dict[str, Any]:
        """Acquire the page under its lock, run ``op(page)``, merge any dict
        it returns into the success payload.

        ``tab`` (issue #57) picks the page + lock via ``_acquire_for``: the
        main page under ``action_lock`` (``tab is None``) or a named tab under
        its own lock. Ops on different tabs run concurrently; same-tab ops
        serialize.

        ``navigation=True`` (goto / back only) feeds the consecutive-timeout
        streak that force-recycles a wedged engine (issue #55): a nav success
        (on ANY tab) clears the streak, a nav timeout counts, and the
        recycle-that-fired stamps ``FORCE_RECYCLE_HINT`` on the failure
        payload — tabs must not mask a wedged engine. A slow click/type timing
        out on an overlay is normal, not a wedge signature, so only
        navigations opt in. ``record_navigation_timeout`` is called while we
        hold a page lock (main or tab); the recycle it may trigger takes
        ``_start_lock`` — safe per the lock-order note in ``session.recycle``.
        """
        try:
            page, lock, generation, created_tab = await self._acquire_for(
                tab, create_tab=create_tab
            )
        except TabError as exc:
            return self._tab_error_payload(exc)
        except Exception as exc:
            log.exception("[browser] session start failed for %s", name)
            return normalize_exception(exc, action=f"browser_{name}")
        async with lock:
            try:
                result = await asyncio.wait_for(
                    op(page), timeout=action_timeout_seconds()
                )
            except Exception as exc:
                log.warning("[browser] %s raised: %s", name, exc)
                payload = normalize_exception(exc, action=f"browser_{name}")
                # A navigation timeout is the wedged-engine signature: count
                # it (scoped to the generation we acquired against, so a late
                # timeout from a recycled session can't poison the fresh one),
                # and if this one tripped the recycle threshold, tell the brain
                # the session was force-recycled and it can just retry.
                if navigation and is_timeout(exc):
                    recycled = await self._manager.record_navigation_timeout(
                        generation
                    )
                    if recycled:
                        payload["hint"] = FORCE_RECYCLE_HINT
                # A tab THIS op opened whose first navigation failed leaves no
                # phantom behind: discard it (registry + best-effort close)
                # AFTER the streak recording. A reused tab, or a failed op on a
                # pre-existing tab, is untouched. We still hold the tab lock;
                # discard_tab takes _start_lock — the sanctioned order.
                if created_tab is not None:
                    await self._manager.discard_tab(created_tab)
                return payload
            finally:
                self._manager.mark_activity()
        if navigation:
            self._manager.record_navigation_success(generation)
        extra = result if isinstance(result, dict) else {}
        return {"ok": True, **extra}

    async def _run_indexed_action(
        self,
        name: str,
        index: int,
        op: Callable[[Any], Awaitable[Any]],
        *,
        then_read: str | None = None,
        tab: str | None = None,
    ) -> dict[str, Any]:
        """Resolve ``index`` to the element the last snapshot stamped, then
        run ``op(locator)``. A vanished index is a soft stale-index hint,
        not an error (and no ``then_read`` is attempted for it).

        ``tab`` (issue #57) picks the page + lock via ``_acquire_for``.
        ``then_read`` (click only) runs a bounded best-effort read in the SAME
        lock hold after the action — the batch that halves a click→read
        round-trip. The read needs the PAGE, so we thread it through here
        rather than only the locator.
        """
        try:
            # No navigation and no tab creation here (create_tab=False), so the
            # generation + created_tab the seam returns are unused.
            page, lock, _generation, _created_tab = await self._acquire_for(
                tab, create_tab=False
            )
        except TabError as exc:
            return self._tab_error_payload(exc)
        except Exception as exc:
            log.exception("[browser] session start failed for %s", name)
            return normalize_exception(exc, action=f"browser_{name}")
        selector = f'[{INDEX_ATTR}="{index}"]'
        read_payload: dict[str, Any] | None = None
        async with lock:
            try:
                locator = page.locator(selector)
                if await locator.count() == 0:
                    return stale_index_payload()
                await asyncio.wait_for(
                    op(locator.first), timeout=action_timeout_seconds()
                )
                if then_read is not None:
                    # A nav-triggering click swaps the document; wait briefly
                    # for the new one to reach domcontentloaded so we read it,
                    # not the old page. This returns immediately when no
                    # navigation happened (the page is already loaded), so a
                    # same-page click still reads at once; the bounded wait
                    # (the short navigation budget) only caps a stalled nav,
                    # and its timeout is swallowed so the read still runs.
                    try:
                        await page.wait_for_load_state(
                            "domcontentloaded",
                            timeout=navigation_timeout_seconds() * 1000,
                        )
                    except Exception:
                        pass
                    read_payload = await self._safe_read(page, then_read)
            except Exception as exc:
                log.warning("[browser] %s raised: %s", name, exc)
                return normalize_exception(exc, action=f"browser_{name}")
            finally:
                self._manager.mark_activity()
        result: dict[str, Any] = {"ok": True}
        if read_payload is not None:
            result["read"] = read_payload
        return result

    def _update_current_page(self, result: dict[str, Any]) -> None:
        url = result.get("url")
        if isinstance(url, str) and url:
            self._current_url = url
        title = result.get("title")
        if isinstance(title, str):
            self._current_title = title or None

    def _record_navigation(self, url: str, tab: str | None = None) -> None:
        if not url:
            return
        entry: dict[str, str] = {
            "url": url,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        # Main-page entries stay exactly {url, at}; a named-tab navigation
        # carries the extra "tab" key so the dashboard can distinguish them
        # without conflating them with the main page's current-page state.
        if tab is not None:
            entry["tab"] = tab
        self._recent_navigations.append(entry)
