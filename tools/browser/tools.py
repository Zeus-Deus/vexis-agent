"""``BrowserTools`` — the six Phase 2 browser actions.

Each method is the in-process implementation behind one
``browser_*`` op the control socket dispatches. The CLI client
(``tools.browser_cli``) is the thin shell that gets these results
back to the brain via JSON over a Unix socket.

Every method returns a JSON-able dict:

- success: ``{"ok": True, ...}``
- failure: ``{"ok": False, "error": "...", "hint": "..."}``
- soft hint: ``{"ok": True, "snapshot_stale": True, "suggestion": "..."}``
  (only on click/type/press/back when browser-use signals a stale index)

Per-action wall-clock timeout comes from
``profile.action_timeout_seconds()`` (default 120s, configurable in
``~/.vexis/config.yaml`` ``[browser]``).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from browser_use import Tools

from tools.browser import snapshot as snapshot_mod
from tools.browser.errors import (
    error_payload,
    is_stale_index_hint,
    normalize_exception,
    stale_index_payload,
)
from core import yaml_config

from tools.browser.profile import action_timeout_seconds, screenshots_dir
from tools.browser.session import SessionManager

log = logging.getLogger(__name__)


class BrowserTools:
    """Daemon-side implementation of the browser_* control-socket ops."""

    def __init__(self, manager: SessionManager, workspace: Path) -> None:
        self._manager = manager
        self._tools = Tools()
        self._workspace = workspace

    async def navigate(self, url: str) -> dict[str, Any]:
        if not isinstance(url, str) or not url.strip():
            return error_payload("missing or empty 'url'")
        return await self._run_action(
            "navigate",
            lambda session: self._tools.navigate(
                url=url.strip(), browser_session=session
            ),
            include_snapshot=True,
        )

    async def snapshot(self, full: bool = False) -> dict[str, Any]:
        # ``full`` is accepted for forward-compat with the v1 spec but
        # browser-use's ``get_state_as_text`` already produces the same
        # serialized DSL regardless — there's no separate full mode in
        # the underlying library. Kept for API stability.
        del full
        try:
            session = await self._manager.get()
        except Exception as exc:
            log.exception("[browser] session start failed")
            return normalize_exception(exc, action="browser_snapshot")
        try:
            snap = await asyncio.wait_for(
                snapshot_mod.render(session), timeout=action_timeout_seconds()
            )
        except Exception as exc:
            log.exception("[browser] snapshot failed")
            return normalize_exception(exc, action="browser_snapshot")
        self._manager.mark_activity()
        return {"ok": True, **snap}

    async def click(self, index: int) -> dict[str, Any]:
        if not isinstance(index, int):
            return error_payload("'index' must be an integer")
        return await self._run_action(
            "click",
            lambda session: self._tools.click(index=index, browser_session=session),
        )

    async def type(
        self, index: int, text: str, clear: bool = True
    ) -> dict[str, Any]:
        if not isinstance(index, int):
            return error_payload("'index' must be an integer")
        if not isinstance(text, str):
            return error_payload("'text' must be a string")
        return await self._run_action(
            "type",
            lambda session: self._tools.input(
                index=index,
                text=text,
                clear_existing=clear,
                browser_session=session,
            ),
        )

    async def press(self, key: str) -> dict[str, Any]:
        if not isinstance(key, str) or not key.strip():
            return error_payload("missing or empty 'key'")
        return await self._run_action(
            "press",
            lambda session: self._tools.send_keys(
                keys=key.strip(), browser_session=session
            ),
        )

    async def back(self) -> dict[str, Any]:
        return await self._run_action(
            "back",
            lambda session: self._tools.go_back(browser_session=session),
            include_url=True,
        )

    async def scroll(self, direction: str, pages: float = 1.0) -> dict[str, Any]:
        if direction not in ("up", "down"):
            return error_payload("'direction' must be 'up' or 'down'")
        try:
            pages_f = float(pages)
        except (TypeError, ValueError):
            return error_payload("'pages' must be a number")
        if pages_f <= 0:
            return error_payload("'pages' must be > 0")
        down = direction == "down"
        return await self._run_action(
            "scroll",
            lambda session: self._tools.scroll(
                down=down, pages=pages_f, browser_session=session
            ),
        )

    async def screenshot(
        self,
        full_page: bool = False,
        include_base64: bool | None = None,
    ) -> dict[str, Any]:
        """Save a PNG to ``<workspace>/browser/screenshots/<ts>.png``.

        ``image_base64`` is OPT-IN: omitted by default to keep the JSON
        line under the brain's stream buffer. A 4 MB base64 string in a
        single stream-json line crashes the asyncio StreamReader unless
        it's been bumped — and even then, the brain doesn't use it
        (the path + Read tool is the canonical image flow). Pass
        ``include_base64=True`` (CLI: ``--include-base64``) when the
        consumer actually needs the bytes inline. Default tracks
        ``yaml_config.browser_screenshot_include_base64()``.
        """
        try:
            session = await self._manager.get()
        except Exception as exc:
            log.exception("[browser] session start failed for screenshot")
            return normalize_exception(exc, action="browser_screenshot")
        async with self._manager.action_lock:
            try:
                data = await asyncio.wait_for(
                    session.take_screenshot(full_page=bool(full_page), format="png"),
                    timeout=action_timeout_seconds(),
                )
            except Exception as exc:
                log.warning("[browser] screenshot raised: %s", exc)
                return normalize_exception(exc, action="browser_screenshot")
            finally:
                self._manager.mark_activity()
        # browser-use returns either raw bytes or a base64-encoded
        # string depending on internal codepaths; tolerate both.
        if isinstance(data, str):
            try:
                raw = base64.b64decode(data)
            except (ValueError, TypeError):
                return error_payload("screenshot returned a non-base64 string")
            b64: str | None = data
        else:
            raw = bytes(data)
            b64 = None
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
            if b64 is None:
                b64 = base64.b64encode(raw).decode("ascii")
            payload["image_base64"] = b64
        return payload

    async def _run_action(
        self,
        name: str,
        op,
        *,
        include_snapshot: bool = False,
        include_url: bool = False,
    ) -> dict[str, Any]:
        try:
            session = await self._manager.get()
        except Exception as exc:
            log.exception("[browser] session start failed for %s", name)
            return normalize_exception(exc, action=f"browser_{name}")
        async with self._manager.action_lock:
            try:
                result = await asyncio.wait_for(
                    op(session), timeout=action_timeout_seconds()
                )
            except Exception as exc:
                log.warning("[browser] %s raised: %s", name, exc)
                return normalize_exception(exc, action=f"browser_{name}")
            finally:
                self._manager.mark_activity()
        # ``Tools`` actions return ActionResult; some return None for
        # method-style invocations that just dispatch an event. Treat
        # None as success.
        extra: dict[str, Any] = {}
        if result is not None:
            error = getattr(result, "error", None)
            if error:
                return error_payload(f"browser_{name} failed: {error}")
            extracted = getattr(result, "extracted_content", None)
            if is_stale_index_hint(extracted):
                return stale_index_payload()
        if include_snapshot:
            try:
                snap = await asyncio.wait_for(
                    snapshot_mod.render(session),
                    timeout=action_timeout_seconds(),
                )
                extra.update(snap)
            except Exception as exc:
                log.warning("[browser] post-%s snapshot failed: %s", name, exc)
        if include_url and "url" not in extra:
            try:
                extra["url"] = await session.get_current_page_url() or ""
            except Exception:
                extra["url"] = ""
        return {"ok": True, **extra}
