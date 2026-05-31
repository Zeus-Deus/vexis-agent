"""``vexis-browser-mcp`` — the browser as a standalone MCP server.

This is the modular seam the whole add-on effort was building toward:
the brain reaches the browser through the **Model Context Protocol**,
not a bespoke ``vexis-browse`` Bash CLI. Because the brain⇄browser
boundary is now plain MCP, swapping to a *different* browser (the
official Playwright MCP, a cloud browser like Browserbase, a future
engine) is a config-level change in ``~/.vexis/mcp-servers.yaml`` —
**no daemon edit, no vexis release.** The bundled ``vexis-browser``
server is just the default; any MCP browser server drops into its place.

Architecture — a thin MCP adapter over the daemon's persistent session:

    claude-code / opencode  ──MCP(stdio)──▶  vexis-browser-mcp
                                                   │  control socket
                                                   ▼
                                      vexis-agent daemon  ──▶  Camoufox
                                      (one persistent SessionManager)

The brain (claude-code / opencode) spawns this server fresh per turn
and tears it down at the end of the turn. So this process holds NO
browser state of its own — it forwards each tool call to the daemon's
ONE long-lived ``SessionManager`` over the control socket (shared
round-trip in ``tools.browser._client``). That is deliberate: the
persistent session (login, cookies, current page, the live dashboard
view) must outlive any single turn, so the engine stays in the daemon
and this adapter is stateless. Spawning Camoufox per turn instead would
cold-start the browser every message and lose cross-turn page state.

The tool surface mirrors the nine ``browser_*`` control-socket ops one
for one, with the same argument coercion as the CLI path
(``tools.browser.dispatch``), so the two front-ends are behaviourally
identical. Each tool's docstring is its MCP schema description — the
brain-facing how-to (when to reach for the browser, the snapshot DSL,
the stale-index hint, the screenshot path-handoff convention) lives in
the ``web-browsing`` capability block
(``vexis_agent/addons/browser/capability.py``), kept next to this code.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from vexis_agent.tools.browser._client import (
    BrowserSocketError,
    send as _socket_send,
    unwrap_response,
)

#: The MCP server name. claude-code namespaces tools as
#: ``mcp__vexis-browser__<tool>``; opencode as ``vexis-browser_<tool>``.
#: Must match the ``McpServerSpec.name`` the browser add-on registers in
#: ``vexis_agent/addons/browser/__init__.py`` so the two agree.
SERVER_NAME = "vexis-browser"

mcp = FastMCP(SERVER_NAME)


async def _call(op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Forward one op to the daemon control socket, off the event loop.

    The socket round-trip is blocking; run it in a thread so the MCP
    server's loop stays responsive. A transport failure (daemon down,
    timeout) becomes a clean ``{"ok": false, "error": ...}`` tool
    result instead of crashing the server — the brain reads the error
    and decides what to do, and the next turn's fresh spawn retries."""
    try:
        resp = await asyncio.to_thread(_socket_send, op, args)
    except BrowserSocketError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "hint": (
                "The vexis-agent daemon owns the browser session and "
                "must be running. This usually means the daemon is "
                "down or restarting."
            ),
        }
    return unwrap_response(resp)


@mcp.tool()
async def browser_navigate(url: str) -> dict[str, Any]:
    """Navigate the persistent browser to a URL.

    Returns ``{ok, url, title, snapshot, element_count}``. The inline
    ``snapshot`` is the same accessibility-tree DSL ``browser_snapshot``
    returns, so there's usually no need to snapshot right after
    navigating. Cloudflare challenges are auto-solved on navigate.
    """
    return await _call("browser_navigate", {"url": url})


@mcp.tool()
async def browser_snapshot(full: bool = False) -> dict[str, Any]:
    """Return the accessibility-tree DSL of the current page.

    One line per interactive element: ``[index]<tag attr="val">text</tag>``.
    The integer ``index`` is what you pass to ``browser_click`` /
    ``browser_type``. Each snapshot re-numbers the page from scratch —
    always act on indices from your most recent snapshot. ``full`` is
    reserved (no-op today).
    """
    return await _call("browser_snapshot", {"full": full})


@mcp.tool()
async def browser_click(index: int, js: bool = False) -> dict[str, Any]:
    """Click the element with the given snapshot ``index``.

    Set ``js=true`` to fire the element's own ``click()`` from
    JavaScript, bypassing actionability checks — use it when a normal
    click hangs on a full-screen cookie/consent overlay that intercepts
    the hit-test. A vanished index returns a soft ``snapshot_stale``
    hint, not an error: re-snapshot and retry.
    """
    return await _call("browser_click", {"index": index, "js": js})


@mcp.tool()
async def browser_read(selector: Optional[str] = None) -> dict[str, Any]:
    """Return the rendered text of the page (or a CSS ``selector``).

    Fast, lossless alternative to a screenshot for div/table-heavy
    pages the snapshot DSL leaves nearly empty. Defaults to the whole
    ``<body>``. Returns ``{ok, text, selector, chars, url}``.
    """
    args: dict[str, Any] = {}
    if selector:
        args["selector"] = selector
    return await _call("browser_read", args)


@mcp.tool()
async def browser_type(
    index: int, text: str, clear: bool = True
) -> dict[str, Any]:
    """Type ``text`` into the element with the given snapshot ``index``.

    Clears the field first by default; pass ``clear=false`` to append.
    A vanished index returns a soft ``snapshot_stale`` hint.
    """
    return await _call(
        "browser_type", {"index": index, "text": text, "clear": clear}
    )


@mcp.tool()
async def browser_press(key: str) -> dict[str, Any]:
    """Send a key chord to the page, e.g. ``Enter``, ``Tab``, ``Control+L``."""
    return await _call("browser_press", {"key": key})


@mcp.tool()
async def browser_back() -> dict[str, Any]:
    """Navigate back in the browser's history. Returns ``{ok, url}``."""
    return await _call("browser_back", {})


@mcp.tool()
async def browser_scroll(direction: str, pages: float = 1.0) -> dict[str, Any]:
    """Scroll the page ``up`` or ``down`` by ``pages`` viewport heights.

    ``pages=0.5`` is half a page; ``pages=10`` effectively jumps to the
    top/bottom. ``direction`` must be ``"up"`` or ``"down"``.
    """
    return await _call(
        "browser_scroll", {"direction": direction, "pages": pages}
    )


@mcp.tool()
async def browser_screenshot(
    full_page: bool = False, include_base64: bool = False
) -> dict[str, Any]:
    """Save a PNG of the current page and return its path.

    Returns ``{ok, path, size_bytes, mime_type}``. Include the ``path``
    verbatim in your reply to the user — the Telegram transport detects
    ``<workspace>/browser/screenshots/<ts>.png`` and sends the file as a
    photo. ``full_page=true`` captures the whole scrollable page.
    ``include_base64=true`` also returns the bytes inline (off by
    default; the path is the canonical handoff and base64 can overflow
    the brain's stream buffer).
    """
    args: dict[str, Any] = {"full_page": full_page}
    # Only forward when explicitly requested; an omitted key lets the
    # daemon apply its config default
    # (``addons.browser.screenshot_include_base64``) — same contract as
    # the ``vexis-browse`` CLI.
    if include_base64:
        args["include_base64"] = True
    return await _call("browser_screenshot", args)


def main() -> None:
    """Console-script entry: run the stdio MCP server.

    Spawned by the agent CLI (claude-code / opencode) per turn from the
    ``vexis-browser`` entry in the brain's native MCP config, which the
    browser add-on writes via ``register_mcp_server_default``. Blocks
    serving stdio until the agent closes the pipe at end of turn.
    """
    mcp.run()


if __name__ == "__main__":  # pragma: no cover - manual / agent-spawned
    main()
