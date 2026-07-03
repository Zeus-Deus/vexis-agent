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

The tool surface mirrors the twelve ``browser_*`` control-socket ops one
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
async def browser_navigate(
    url: str,
    wait_until: Optional[str] = None,
    then_read: Optional[str] = None,
    tab: Optional[str] = None,
) -> dict[str, Any]:
    """Navigate the persistent browser to a URL.

    Returns ``{ok, url, title, snapshot, element_count}``. The inline
    ``snapshot`` is the same accessibility-tree DSL ``browser_snapshot``
    returns, so there's usually no need to snapshot right after
    navigating. Cloudflare challenges are auto-solved on navigate.

    Three optional levers (issue #57):

    - ``wait_until`` — ``"settle"`` (default) waits for load + networkidle;
      pass ``"domcontentloaded"`` (or ``"load"``) to skip that settle for a
      much cheaper navigation on catalog/data pages you'll just read.
    - ``then_read`` — a CSS selector (``"body"`` = whole body) read in the
      SAME call after the page loads, so a navigate→read is ONE round-trip.
      The result gains ``read: {ok, text, selector, chars}``; a failed
      bonus read never fails the navigation.
    - ``tab`` — a named parallel tab (created here). To fan out over K
      pages, fire several ``browser_navigate`` calls with DISTINCT ``tab``
      names IN PARALLEL (in one batch of tool calls), then ``browser_read(tab=...)``
      each — the tabs load concurrently instead of one serial round-trip
      apiece. Omit ``tab`` for the single shared main page.
    """
    args: dict[str, Any] = {"url": url}
    if wait_until is not None:
        args["wait_until"] = wait_until
    if then_read is not None:
        args["then_read"] = then_read
    if tab is not None:
        args["tab"] = tab
    return await _call("browser_navigate", args)


@mcp.tool()
async def browser_snapshot(
    full: bool = False, tab: Optional[str] = None
) -> dict[str, Any]:
    """Return the accessibility-tree DSL of the current page.

    One line per interactive element: ``[index]<tag attr="val">text</tag>``.
    The integer ``index`` is what you pass to ``browser_click`` /
    ``browser_type``. Each snapshot re-numbers the page from scratch —
    always act on indices from your most recent snapshot. ``full`` is
    reserved (no-op today). ``tab`` targets a named parallel tab.
    """
    args: dict[str, Any] = {"full": full}
    if tab is not None:
        args["tab"] = tab
    return await _call("browser_snapshot", args)


@mcp.tool()
async def browser_click(
    index: int,
    js: bool = False,
    then_read: Optional[str] = None,
    tab: Optional[str] = None,
) -> dict[str, Any]:
    """Click the element with the given snapshot ``index``.

    Set ``js=true`` to fire the element's own ``click()`` from
    JavaScript, bypassing actionability checks — use it when a normal
    click hangs on a full-screen cookie/consent overlay that intercepts
    the hit-test. A vanished index returns a soft ``snapshot_stale``
    hint, not an error: re-snapshot and retry.

    ``then_read`` (issue #57) — a CSS selector (``"body"`` = whole body)
    read in the SAME call after the click, so a click that navigates and
    the read of the new page are ONE round-trip; the result gains
    ``read: {ok, text, selector, chars}`` (a failed read never fails the
    click). ``tab`` targets a named parallel tab.
    """
    args: dict[str, Any] = {"index": index, "js": js}
    if then_read is not None:
        args["then_read"] = then_read
    if tab is not None:
        args["tab"] = tab
    return await _call("browser_click", args)


@mcp.tool()
async def browser_read(
    selector: Optional[str] = None, tab: Optional[str] = None
) -> dict[str, Any]:
    """Return the rendered text of the page (or a CSS ``selector``).

    Fast, lossless alternative to a screenshot for div/table-heavy
    pages the snapshot DSL leaves nearly empty. Defaults to the whole
    ``<body>``. Returns ``{ok, text, selector, chars, url}``. ``tab``
    reads a named parallel tab (issue #57).
    """
    args: dict[str, Any] = {}
    if selector:
        args["selector"] = selector
    if tab is not None:
        args["tab"] = tab
    return await _call("browser_read", args)


@mcp.tool()
async def browser_type(
    index: int, text: str, clear: bool = True, tab: Optional[str] = None
) -> dict[str, Any]:
    """Type ``text`` into the element with the given snapshot ``index``.

    Clears the field first by default; pass ``clear=false`` to append.
    A vanished index returns a soft ``snapshot_stale`` hint. ``tab``
    targets a named parallel tab (issue #57).
    """
    args: dict[str, Any] = {"index": index, "text": text, "clear": clear}
    if tab is not None:
        args["tab"] = tab
    return await _call("browser_type", args)


@mcp.tool()
async def browser_press(key: str, tab: Optional[str] = None) -> dict[str, Any]:
    """Send a key chord to the page, e.g. ``Enter``, ``Tab``, ``Control+L``.

    ``tab`` targets a named parallel tab (issue #57)."""
    args: dict[str, Any] = {"key": key}
    if tab is not None:
        args["tab"] = tab
    return await _call("browser_press", args)


@mcp.tool()
async def browser_back(tab: Optional[str] = None) -> dict[str, Any]:
    """Navigate back in the browser's history. Returns ``{ok, url}``.

    ``tab`` targets a named parallel tab (issue #57)."""
    args: dict[str, Any] = {}
    if tab is not None:
        args["tab"] = tab
    return await _call("browser_back", args)


@mcp.tool()
async def browser_scroll(
    direction: str, pages: float = 1.0, tab: Optional[str] = None
) -> dict[str, Any]:
    """Scroll the page ``up`` or ``down`` by ``pages`` viewport heights.

    ``pages=0.5`` is half a page; ``pages=10`` effectively jumps to the
    top/bottom. ``direction`` must be ``"up"`` or ``"down"``. ``tab``
    targets a named parallel tab (issue #57).
    """
    args: dict[str, Any] = {"direction": direction, "pages": pages}
    if tab is not None:
        args["tab"] = tab
    return await _call("browser_scroll", args)


@mcp.tool()
async def browser_screenshot(
    full_page: bool = False,
    include_base64: bool = False,
    tab: Optional[str] = None,
) -> dict[str, Any]:
    """Save a PNG of the current page and return its path.

    Returns ``{ok, path, size_bytes, mime_type}``. Include the ``path``
    verbatim in your reply to the user — the Telegram transport detects
    ``<workspace>/browser/screenshots/<ts>.png`` and sends the file as a
    photo. ``full_page=true`` captures the whole scrollable page.
    ``include_base64=true`` also returns the bytes inline (off by
    default; the path is the canonical handoff and base64 can overflow
    the brain's stream buffer). ``tab`` captures a named parallel tab
    (issue #57).
    """
    args: dict[str, Any] = {"full_page": full_page}
    # Only forward when explicitly requested; an omitted key lets the
    # daemon apply its config default
    # (``addons.browser.screenshot_include_base64``) — same contract as
    # the ``vexis-browse`` CLI.
    if include_base64:
        args["include_base64"] = True
    if tab is not None:
        args["tab"] = tab
    return await _call("browser_screenshot", args)


@mcp.tool()
async def browser_recycle() -> dict[str, Any]:
    """Force-recycle the persistent browser session.

    Reach for this when navigations repeatedly time out or the session
    otherwise seems wedged (a stealth engine can lock up while the host
    itself is fine). Tears the current session down; your next browser
    action lazily restarts a fresh one. Your login state, cookies, and
    local storage live on disk and survive the recycle — you stay logged
    in. Returns ``{ok, was_running}``. Note: after three consecutive
    navigation timeouts the session already recycles itself and the error
    hint says so, so usually you can just retry; call this when you want to
    force it sooner.
    """
    return await _call("browser_recycle", {})


@mcp.tool()
async def browser_tabs() -> dict[str, Any]:
    """List the open named parallel tabs (issue #57).

    Returns ``{ok, tabs: [{name, url}]}``. The main page is unnamed and is
    NOT listed here. Named tabs are the ones you opened with
    ``browser_navigate(tab=...)``; use this to see what's open before
    reading or closing one.
    """
    return await _call("browser_tabs", {})


@mcp.tool()
async def browser_tab_close(tab: str) -> dict[str, Any]:
    """Close a named parallel tab (issue #57). Returns ``{ok, closed}``.

    Frees a slot against the ``max_tabs`` cap. An unknown tab name returns
    a clean error with the list of open tabs. The main page has no name and
    can't be closed this way.
    """
    return await _call("browser_tab_close", {"tab": tab})


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
