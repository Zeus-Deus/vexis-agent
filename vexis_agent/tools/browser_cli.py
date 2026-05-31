"""CLI client for ``vexis-browse``.

A thin shell over the daemon control socket: connect, send one JSON
line, print the JSON response. The daemon's dispatch routes
``browser_*`` ops to the browser add-on's ``BrowserTools`` instance
(the one persistent Camoufox session per daemon).

The socket round-trip lives in ``tools.browser._client`` — shared with
the ``vexis-browser-mcp`` MCP server so the CLI and the MCP front-end
can't drift. This module owns only the argparse surface and exit-code
mapping.
"""

from __future__ import annotations

import argparse
import json
import sys

from vexis_agent.tools.browser._client import (
    BrowserSocketError,
    send as _socket_send,
    unwrap_response,
)


def _send(op: str, args: dict) -> dict:
    """Send a request; on any socket failure print to stderr and exit 1.

    The CLI's contract is "speak JSON on success, die loudly on
    transport failure" — so a missing daemon is a hard exit, unlike the
    MCP server which keeps running and returns the error as a result."""
    try:
        return _socket_send(op, args)
    except BrowserSocketError as exc:
        print(f"vexis-browse: {exc}", file=sys.stderr)
        sys.exit(1)


def _print_and_exit(resp: dict) -> int:
    payload = unwrap_response(resp)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


def _cmd_navigate(url: str) -> int:
    return _print_and_exit(_send("browser_navigate", {"url": url}))


def _cmd_snapshot(full: bool) -> int:
    return _print_and_exit(_send("browser_snapshot", {"full": full}))


def _cmd_click(index: int, js: bool) -> int:
    return _print_and_exit(_send("browser_click", {"index": index, "js": js}))


def _cmd_read(selector: str | None) -> int:
    args: dict = {}
    if selector:
        args["selector"] = selector
    return _print_and_exit(_send("browser_read", args))


def _cmd_type(index: int, text: str, clear: bool) -> int:
    return _print_and_exit(
        _send("browser_type", {"index": index, "text": text, "clear": clear})
    )


def _cmd_press(key: str) -> int:
    return _print_and_exit(_send("browser_press", {"key": key}))


def _cmd_back() -> int:
    return _print_and_exit(_send("browser_back", {}))


def _cmd_scroll(direction: str, pages: float) -> int:
    return _print_and_exit(
        _send("browser_scroll", {"direction": direction, "pages": pages})
    )


def _cmd_screenshot(full_page: bool, include_base64: bool) -> int:
    args: dict = {"full_page": full_page}
    # Only forward when explicitly set; daemon falls back to its
    # config default (``addons.browser.screenshot_include_base64``)
    # when the key is absent.
    if include_base64:
        args["include_base64"] = True
    return _print_and_exit(_send("browser_screenshot", args))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vexis-browse",
        description="Drive the Vexis browser via the daemon's singleton session.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_nav = sub.add_parser("navigate", help="Navigate to a URL.")
    p_nav.add_argument("url")

    p_snap = sub.add_parser(
        "snapshot", help="Return the accessibility-tree DSL for the current page."
    )
    p_snap.add_argument(
        "--full",
        action="store_true",
        help="Reserved (no-op today; the snapshot serializes one DSL form).",
    )

    p_click = sub.add_parser("click", help="Click element by index.")
    p_click.add_argument("index", type=int)
    p_click.add_argument(
        "--js",
        action="store_true",
        help=(
            "Fire the element's click() from JS, bypassing actionability "
            "checks. Use when a normal click hangs on a cookie/consent "
            "overlay that intercepts the hit-test."
        ),
    )

    p_read = sub.add_parser(
        "read",
        help=(
            "Return the rendered text of the page (or a CSS selector). Fast, "
            "lossless alternative to a screenshot for div/table-heavy result "
            "pages the snapshot DSL leaves empty."
        ),
    )
    p_read.add_argument(
        "selector",
        nargs="?",
        default=None,
        help="Optional CSS selector; defaults to the whole <body>.",
    )

    p_type = sub.add_parser("type", help="Type text into element by index.")
    p_type.add_argument("index", type=int)
    p_type.add_argument("text")
    p_type.add_argument(
        "--no-clear",
        dest="clear",
        action="store_false",
        help="Append to the field instead of clearing it first (default: clear).",
    )

    p_press = sub.add_parser(
        "press", help="Send a key chord, e.g. 'Enter' or 'Control+L'."
    )
    p_press.add_argument("key")

    sub.add_parser("back", help="Navigate back in browser history.")

    p_scroll = sub.add_parser(
        "scroll", help="Scroll the page up or down by N pages (default 1)."
    )
    p_scroll.add_argument("direction", choices=("up", "down"))
    p_scroll.add_argument(
        "--pages",
        type=float,
        default=1.0,
        help="0.5=half page, 1=full page, 10=jump to top/bottom (default: 1).",
    )

    p_screenshot = sub.add_parser(
        "screenshot",
        help=(
            "Save a PNG screenshot to ~/vexis-workspace/browser/screenshots/"
            " and return its path."
        ),
    )
    p_screenshot.add_argument(
        "--full-page",
        action="store_true",
        help="Capture the entire scrollable page (default: viewport only).",
    )
    p_screenshot.add_argument(
        "--include-base64",
        action="store_true",
        help=(
            "Also include the PNG bytes as base64 in the JSON response. "
            "Off by default — most consumers read the file via the path."
        ),
    )

    args = parser.parse_args()
    if args.cmd == "navigate":
        return _cmd_navigate(args.url)
    if args.cmd == "snapshot":
        return _cmd_snapshot(args.full)
    if args.cmd == "click":
        return _cmd_click(args.index, args.js)
    if args.cmd == "read":
        return _cmd_read(args.selector)
    if args.cmd == "type":
        return _cmd_type(args.index, args.text, args.clear)
    if args.cmd == "press":
        return _cmd_press(args.key)
    if args.cmd == "back":
        return _cmd_back()
    if args.cmd == "scroll":
        return _cmd_scroll(args.direction, args.pages)
    if args.cmd == "screenshot":
        return _cmd_screenshot(args.full_page, args.include_base64)
    return 2


if __name__ == "__main__":
    sys.exit(main())
