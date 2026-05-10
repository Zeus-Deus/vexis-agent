"""CLI client for ``vexis-browse``.

Same shape as ``tools.background_cli``: connect to the daemon's
control socket, send a single JSON line, print the JSON response.
The daemon's ``main.py`` dispatch routes ``browser_*`` ops to a
shared ``BrowserTools`` instance.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

# Slightly above the daemon-side action timeout (default 120s) so a
# slow page can finish before the client gives up first.
DEFAULT_TIMEOUT_SECONDS = 150.0
RECV_BUFSIZE = 65536


def _socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "vexis-agent" / "vexis-agent.sock"


def _send(op: str, args: dict, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    path = _socket_path()
    if not path.exists():
        print(
            f"vexis-browse: daemon socket not found at {path} — is vexis-agent running?",
            file=sys.stderr,
        )
        sys.exit(1)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        try:
            sock.connect(str(path))
        except OSError as exc:
            print(f"vexis-browse: cannot connect: {exc}", file=sys.stderr)
            sys.exit(1)
        try:
            sock.sendall((json.dumps({"op": op, "args": args}) + "\n").encode())
            sock.shutdown(socket.SHUT_WR)
        except OSError as exc:
            print(f"vexis-browse: send failed: {exc}", file=sys.stderr)
            sys.exit(1)
        chunks: list[bytes] = []
        try:
            while True:
                chunk = sock.recv(RECV_BUFSIZE)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout:
            print("vexis-browse: timed out waiting for daemon", file=sys.stderr)
            sys.exit(1)
    finally:
        sock.close()
    raw = b"".join(chunks).decode().strip()
    if not raw:
        print("vexis-browse: empty response from daemon", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"vexis-browse: invalid JSON from daemon: {raw!r}", file=sys.stderr)
        sys.exit(1)


def _print_and_exit(resp: dict) -> int:
    # Control socket wraps as {"ok": true, "result": ...} when the
    # dispatcher returns a non-dict; for browser ops the dispatcher
    # forwards the dict directly so {"ok": ...} is at the top level.
    if "result" in resp and isinstance(resp.get("result"), dict):
        payload = resp["result"]
    else:
        payload = resp
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


def _cmd_navigate(url: str) -> int:
    return _print_and_exit(_send("browser_navigate", {"url": url}))


def _cmd_snapshot(full: bool) -> int:
    return _print_and_exit(_send("browser_snapshot", {"full": full}))


def _cmd_click(index: int) -> int:
    return _print_and_exit(_send("browser_click", {"index": index}))


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
    # config default (``[browser].screenshot_include_base64``) when
    # the key is absent.
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
        help="Reserved (no-op today; browser-use serializes one DSL form).",
    )

    p_click = sub.add_parser("click", help="Click element by index.")
    p_click.add_argument("index", type=int)

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
        return _cmd_click(args.index)
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
