"""Control-socket dispatch handlers for the nine ``browser_*`` ops.

These moved verbatim out of ``main._build_dispatch`` (the hardcoded
``if op == "browser_*"`` branches) when the browser became an add-on.
Each handler takes the control-socket ``args`` dict and returns the
JSON-able result dict that ``vexis-browse`` prints. Argument validation
(int/str coercion, bad-request shapes) is preserved exactly so the
``vexis-browse`` CLI contract is unchanged.

``build_browser_handlers`` binds them all to one ``BrowserTools``
instance and returns ``{op_name: handler}``, which ``register(ctx)``
feeds to ``ctx.register_dispatch_handler`` one by one. The
add-on-dispatch-first check in ``main._build_dispatch`` means these win
over any (now-removed) core branch.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from vexis_agent.tools.browser import BrowserTools

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def build_browser_handlers(browser: BrowserTools) -> dict[str, Handler]:
    """Return ``{op_name: handler}`` for the nine browser_* ops."""

    async def browser_navigate(args: dict[str, Any]) -> dict[str, Any]:
        url = args.get("url", "")
        return await browser.navigate(url if isinstance(url, str) else "")

    async def browser_snapshot(args: dict[str, Any]) -> dict[str, Any]:
        return await browser.snapshot(bool(args.get("full", False)))

    async def browser_click(args: dict[str, Any]) -> dict[str, Any]:
        try:
            index = int(args.get("index"))
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "'index' must be an integer",
                "kind": "BadRequest",
            }
        return await browser.click(index, bool(args.get("js", False)))

    async def browser_read(args: dict[str, Any]) -> dict[str, Any]:
        sel = args.get("selector")
        if sel is not None and not isinstance(sel, str):
            return {
                "ok": False,
                "error": "'selector' must be a string",
                "kind": "BadRequest",
            }
        return await browser.read(sel)

    async def browser_type(args: dict[str, Any]) -> dict[str, Any]:
        try:
            index = int(args.get("index"))
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "'index' must be an integer",
                "kind": "BadRequest",
            }
        text = args.get("text", "")
        if not isinstance(text, str):
            return {
                "ok": False,
                "error": "'text' must be a string",
                "kind": "BadRequest",
            }
        clear = bool(args.get("clear", True))
        return await browser.type(index, text, clear)

    async def browser_press(args: dict[str, Any]) -> dict[str, Any]:
        key = args.get("key", "")
        return await browser.press(key if isinstance(key, str) else "")

    async def browser_back(args: dict[str, Any]) -> dict[str, Any]:
        return await browser.back()

    async def browser_scroll(args: dict[str, Any]) -> dict[str, Any]:
        direction = args.get("direction", "")
        if not isinstance(direction, str):
            direction = ""
        try:
            pages = float(args.get("pages", 1.0))
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "'pages' must be a number",
                "kind": "BadRequest",
            }
        return await browser.scroll(direction, pages)

    async def browser_screenshot(args: dict[str, Any]) -> dict[str, Any]:
        include_b64_raw = args.get("include_base64")
        include_b64 = (
            bool(include_b64_raw) if include_b64_raw is not None else None
        )
        return await browser.screenshot(
            bool(args.get("full_page", False)),
            include_base64=include_b64,
        )

    return {
        "browser_navigate": browser_navigate,
        "browser_snapshot": browser_snapshot,
        "browser_click": browser_click,
        "browser_read": browser_read,
        "browser_type": browser_type,
        "browser_press": browser_press,
        "browser_back": browser_back,
        "browser_scroll": browser_scroll,
        "browser_screenshot": browser_screenshot,
    }
