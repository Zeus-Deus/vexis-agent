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
    error_payload,
    is_timeout,
    normalize_exception,
    stale_index_payload,
)
from vexis_agent.tools.browser.profile import action_timeout_seconds, screenshots_dir
from vexis_agent.tools.browser.session import SessionManager
from vexis_agent.tools.browser.snapshot import INDEX_ATTR

log = logging.getLogger(__name__)

_RECENT_NAVIGATIONS_MAX = 10


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
        """
        running = self._manager.is_running()
        return {
            "current_url": self._current_url if running else None,
            "current_title": self._current_title if running else None,
            "recent_navigations": list(reversed(self._recent_navigations)),
        }

    async def navigate(self, url: str) -> dict[str, Any]:
        if not isinstance(url, str) or not url.strip():
            return error_payload("missing or empty 'url'")
        target = url.strip()

        async def op(page: Any) -> dict[str, Any]:
            await page.goto(target, wait_until="domcontentloaded")
            await self._manager.wait_stable(page)
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
            return result

        result = await self._run_action("navigate", op, navigation=True)
        if result.get("ok"):
            self._update_current_page(result)
            self._record_navigation(result.get("url") or target)
        return result

    async def snapshot(self, full: bool = False) -> dict[str, Any]:
        # ``full`` is accepted for forward-compat with the v1 spec but the
        # serializer already emits one DSL form regardless. Kept for API
        # stability.
        del full
        result = await self._run_action(
            "snapshot", lambda page: snapshot_mod.render(page)
        )
        if result.get("ok"):
            self._update_current_page(result)
        return result

    async def click(self, index: int, js: bool = False) -> dict[str, Any]:
        if not isinstance(index, int):
            return error_payload("'index' must be an integer")
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
        return await self._run_indexed_action("click", index, op)

    async def read(self, selector: str | None = None) -> dict[str, Any]:
        """Return the rendered text of ``selector`` (default ``body``).

        The snapshot DSL only serializes interactive/visible elements, so a
        page that renders its payload as plain ``<div>``/``<tr>`` text comes
        back nearly empty and the brain wrongly falls back to a screenshot.
        ``read`` is the fast, lossless escape hatch: ``page.inner_text`` of
        the body (or a CSS selector) returns the same text a vanilla driver
        would, in a few ms, no vision round-trip.
        """
        if selector is not None and not isinstance(selector, str):
            return error_payload("'selector' must be a string")
        sel = (selector or "").strip() or "body"

        async def op(page: Any) -> dict[str, Any]:
            if sel != "body":
                if await page.locator(sel).count() == 0:
                    raise ValueError(f"no element matches selector {sel!r}")
                text = await page.locator(sel).first.inner_text()
            else:
                text = await page.inner_text("body")
            text = text or ""
            return {
                "text": text,
                "selector": sel,
                "chars": len(text),
                "url": page.url or "",
            }

        result = await self._run_action("read", op)
        if result.get("ok"):
            self._update_current_page(result)
        return result

    async def type(
        self, index: int, text: str, clear: bool = True
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

        return await self._run_indexed_action("type", index, op)

    async def press(self, key: str) -> dict[str, Any]:
        if not isinstance(key, str) or not key.strip():
            return error_payload("missing or empty 'key'")
        chord = key.strip()
        return await self._run_action(
            "press", lambda page: page.keyboard.press(chord)
        )

    async def back(self) -> dict[str, Any]:
        async def op(page: Any) -> dict[str, Any]:
            await page.go_back(wait_until="domcontentloaded")
            await self._manager.wait_stable(page)
            return {"url": page.url or ""}

        result = await self._run_action("back", op, navigation=True)
        if result.get("ok"):
            self._update_current_page(result)
            url = result.get("url")
            if url:
                self._record_navigation(url)
        return result

    async def scroll(self, direction: str, pages: float = 1.0) -> dict[str, Any]:
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

        return await self._run_action("scroll", op)

    async def screenshot(
        self,
        full_page: bool = False,
        include_base64: bool | None = None,
    ) -> dict[str, Any]:
        """Save a PNG to ``<workspace>/browser/screenshots/<ts>.png``.

        ``image_base64`` is OPT-IN: omitted by default to keep the JSON
        line under the brain's stream buffer. Pass ``include_base64=True``
        (CLI: ``--include-base64``) when the consumer needs the bytes
        inline. Default tracks
        ``yaml_config.browser_screenshot_include_base64()``.
        """
        try:
            _session, page = await self._manager.acquire()
        except Exception as exc:
            log.exception("[browser] session start failed for screenshot")
            return normalize_exception(exc, action="browser_screenshot")
        async with self._manager.action_lock:
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

    async def _run_action(
        self,
        name: str,
        op: Callable[[Any], Awaitable[Any]],
        *,
        navigation: bool = False,
    ) -> dict[str, Any]:
        """Acquire the page under the action lock, run ``op(page)``, merge
        any dict it returns into the success payload.

        ``navigation=True`` (goto / back only) feeds the consecutive-timeout
        streak that force-recycles a wedged engine (issue #55): a nav success
        clears the streak, a nav timeout counts, and the recycle-that-fired
        stamps ``FORCE_RECYCLE_HINT`` on the failure payload. A slow
        click/type timing out on an overlay is normal, not a wedge signature,
        so only navigations opt in.
        """
        try:
            _session, page = await self._manager.acquire()
        except Exception as exc:
            log.exception("[browser] session start failed for %s", name)
            return normalize_exception(exc, action=f"browser_{name}")
        async with self._manager.action_lock:
            try:
                result = await asyncio.wait_for(
                    op(page), timeout=action_timeout_seconds()
                )
            except Exception as exc:
                log.warning("[browser] %s raised: %s", name, exc)
                payload = normalize_exception(exc, action=f"browser_{name}")
                # A navigation timeout is the wedged-engine signature: count
                # it, and if this one tripped the recycle threshold, tell the
                # brain the session was force-recycled and it can just retry.
                if navigation and is_timeout(exc):
                    recycled = await self._manager.record_navigation_timeout()
                    if recycled:
                        payload["hint"] = FORCE_RECYCLE_HINT
                return payload
            finally:
                self._manager.mark_activity()
        if navigation:
            self._manager.record_navigation_success()
        extra = result if isinstance(result, dict) else {}
        return {"ok": True, **extra}

    async def _run_indexed_action(
        self,
        name: str,
        index: int,
        op: Callable[[Any], Awaitable[Any]],
    ) -> dict[str, Any]:
        """Resolve ``index`` to the element the last snapshot stamped, then
        run ``op(locator)``. A vanished index is a soft stale-index hint,
        not an error."""
        try:
            _session, page = await self._manager.acquire()
        except Exception as exc:
            log.exception("[browser] session start failed for %s", name)
            return normalize_exception(exc, action=f"browser_{name}")
        selector = f'[{INDEX_ATTR}="{index}"]'
        async with self._manager.action_lock:
            try:
                locator = page.locator(selector)
                if await locator.count() == 0:
                    return stale_index_payload()
                await asyncio.wait_for(
                    op(locator.first), timeout=action_timeout_seconds()
                )
            except Exception as exc:
                log.warning("[browser] %s raised: %s", name, exc)
                return normalize_exception(exc, action=f"browser_{name}")
            finally:
                self._manager.mark_activity()
        return {"ok": True}

    def _update_current_page(self, result: dict[str, Any]) -> None:
        url = result.get("url")
        if isinstance(url, str) and url:
            self._current_url = url
        title = result.get("title")
        if isinstance(title, str):
            self._current_title = title or None

    def _record_navigation(self, url: str) -> None:
        if not url:
            return
        self._recent_navigations.append(
            {
                "url": url,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
