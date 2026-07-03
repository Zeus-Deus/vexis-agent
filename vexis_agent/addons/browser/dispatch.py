"""Control-socket dispatch handlers for the twelve ``browser_*`` ops.

These moved verbatim out of ``main._build_dispatch`` (the hardcoded
``if op == "browser_*"`` branches) when the browser became an add-on.
Each handler takes the control-socket ``args`` dict and returns the
JSON-able result dict that ``vexis-browse`` prints. Argument validation
(int/str coercion, bad-request shapes) is preserved exactly so the
``vexis-browse`` CLI contract is unchanged.

Issue #57 added the batched/tab levers — ``wait_until`` + ``then_read``
on navigate, ``then_read`` on click, ``tab`` on every page-taking op, and
the ``browser_tabs`` / ``browser_tab_close`` ops. New args are forwarded
only when present (an omitted key lets ``BrowserTools`` default, parity
with the CLI/MCP front-ends) and type-checked to a ``BadRequest`` shape,
matching the existing int/str coercion.

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


def _bad_request(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "kind": "BadRequest"}


def _collect_str_kwargs(
    args: dict[str, Any], keys: tuple[str, ...]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Pull optional string kwargs, forwarding only the ones present.

    Returns ``(kwargs, None)`` on success or ``(None, bad_request)`` when a
    present key is the wrong type — the forward-only-when-set discipline the
    CLI and MCP front-ends also follow, so an omitted key lets the daemon
    default rather than pinning it.
    """
    kwargs: dict[str, Any] = {}
    for key in keys:
        value = args.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            return None, _bad_request(f"'{key}' must be a string")
        kwargs[key] = value
    return kwargs, None


def build_browser_handlers(browser: BrowserTools) -> dict[str, Handler]:
    """Return ``{op_name: handler}`` for the twelve browser_* ops."""

    async def browser_navigate(args: dict[str, Any]) -> dict[str, Any]:
        url = args.get("url", "")
        if not isinstance(url, str):
            url = ""
        kwargs, bad = _collect_str_kwargs(
            args, ("wait_until", "then_read", "tab")
        )
        if bad is not None:
            return bad
        return await browser.navigate(url, **kwargs)

    async def browser_snapshot(args: dict[str, Any]) -> dict[str, Any]:
        kwargs, bad = _collect_str_kwargs(args, ("tab",))
        if bad is not None:
            return bad
        return await browser.snapshot(bool(args.get("full", False)), **kwargs)

    async def browser_click(args: dict[str, Any]) -> dict[str, Any]:
        try:
            index = int(args.get("index"))
        except (TypeError, ValueError):
            return _bad_request("'index' must be an integer")
        kwargs, bad = _collect_str_kwargs(args, ("then_read", "tab"))
        if bad is not None:
            return bad
        return await browser.click(
            index, bool(args.get("js", False)), **kwargs
        )

    async def browser_read(args: dict[str, Any]) -> dict[str, Any]:
        sel = args.get("selector")
        if sel is not None and not isinstance(sel, str):
            return _bad_request("'selector' must be a string")
        kwargs, bad = _collect_str_kwargs(args, ("tab",))
        if bad is not None:
            return bad
        return await browser.read(sel, **kwargs)

    async def browser_type(args: dict[str, Any]) -> dict[str, Any]:
        try:
            index = int(args.get("index"))
        except (TypeError, ValueError):
            return _bad_request("'index' must be an integer")
        text = args.get("text", "")
        if not isinstance(text, str):
            return _bad_request("'text' must be a string")
        kwargs, bad = _collect_str_kwargs(args, ("tab",))
        if bad is not None:
            return bad
        clear = bool(args.get("clear", True))
        return await browser.type(index, text, clear, **kwargs)

    async def browser_press(args: dict[str, Any]) -> dict[str, Any]:
        key = args.get("key", "")
        kwargs, bad = _collect_str_kwargs(args, ("tab",))
        if bad is not None:
            return bad
        return await browser.press(key if isinstance(key, str) else "", **kwargs)

    async def browser_back(args: dict[str, Any]) -> dict[str, Any]:
        kwargs, bad = _collect_str_kwargs(args, ("tab",))
        if bad is not None:
            return bad
        return await browser.back(**kwargs)

    async def browser_scroll(args: dict[str, Any]) -> dict[str, Any]:
        direction = args.get("direction", "")
        if not isinstance(direction, str):
            direction = ""
        try:
            pages = float(args.get("pages", 1.0))
        except (TypeError, ValueError):
            return _bad_request("'pages' must be a number")
        kwargs, bad = _collect_str_kwargs(args, ("tab",))
        if bad is not None:
            return bad
        return await browser.scroll(direction, pages, **kwargs)

    async def browser_screenshot(args: dict[str, Any]) -> dict[str, Any]:
        include_b64_raw = args.get("include_base64")
        include_b64 = (
            bool(include_b64_raw) if include_b64_raw is not None else None
        )
        kwargs, bad = _collect_str_kwargs(args, ("tab",))
        if bad is not None:
            return bad
        return await browser.screenshot(
            bool(args.get("full_page", False)),
            include_base64=include_b64,
            **kwargs,
        )

    async def browser_recycle(args: dict[str, Any]) -> dict[str, Any]:
        # No args: force-recycle the wedged/live session (issue #55). The
        # BrowserTools method deliberately takes no action_lock and never
        # lazy-starts, so it works even when a navigation is wedged.
        del args
        return await browser.recycle()

    async def browser_tabs(args: dict[str, Any]) -> dict[str, Any]:
        # No args: list the open named tabs (issue #57). Pure read; never
        # lazy-starts a session.
        del args
        return await browser.tabs()

    async def browser_tab_close(args: dict[str, Any]) -> dict[str, Any]:
        tab = args.get("tab")
        if not isinstance(tab, str):
            return _bad_request("'tab' must be a string")
        return await browser.tab_close(tab)

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
        "browser_recycle": browser_recycle,
        "browser_tabs": browser_tabs,
        "browser_tab_close": browser_tab_close,
    }
