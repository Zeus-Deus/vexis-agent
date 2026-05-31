"""Shared control-socket client for the browser tools.

Both the ``vexis-browse`` CLI (``tools.browser_cli``) and the
``vexis-browser-mcp`` MCP server (``tools.browser.mcp_server``) reach
the daemon's persistent Camoufox session the same way: one JSON line
over the daemon's Unix control socket, one JSON line back. This module
is the single implementation of that round-trip so the two front-ends
can't drift.

Protocol (see ``core.control_socket``): send
``{"op": "browser_<verb>", "args": {...}}\\n``; the daemon's dispatch
routes to the browser add-on's handler and writes back the handler's
own ``{"ok": ...}`` dict (browser ops always return an ``ok`` key, so
the socket forwards it verbatim rather than wrapping it in
``{"ok": true, "result": ...}``). ``unwrap_response`` tolerates both
shapes so a future non-browser caller still works.

Why a socket and not the engine directly: the daemon owns ONE
persistent ``SessionManager`` so login/cookies/page state survive
across brain turns and the dashboard can show the live session. The
MCP server and CLI are per-invocation processes; they must talk to
that one long-lived session, not spawn their own.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

#: Slightly above the daemon-side per-action timeout (default 120s) so a
#: slow page finishes before the client gives up first.
DEFAULT_TIMEOUT_SECONDS = 150.0
RECV_BUFSIZE = 65536


class BrowserSocketError(RuntimeError):
    """The control-socket round-trip failed before a JSON reply arrived.

    Carries a human-readable ``message``; callers render it for their
    own surface (the CLI prints to stderr + exits, the MCP server
    returns it as an ``{"ok": false}`` tool result)."""


def socket_path() -> Path:
    """Path to the daemon's control socket.

    Mirrors ``core.control_socket.default_socket_path`` resolution:
    ``$XDG_RUNTIME_DIR/vexis-agent/vexis-agent.sock`` with the
    ``/run/user/<uid>/`` fallback."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "vexis-agent" / "vexis-agent.sock"


def send(
    op: str,
    args: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send one ``{op, args}`` request, return the parsed JSON reply.

    Raises :class:`BrowserSocketError` (never ``SystemExit``) on a
    missing daemon, connection failure, timeout, or unparseable reply —
    so both a CLI (which converts to exit codes) and a long-lived MCP
    server (which must not die on one bad call) can use it.
    """
    path = socket_path()
    if not path.exists():
        raise BrowserSocketError(
            f"daemon socket not found at {path} — is vexis-agent running?"
        )
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        try:
            sock.connect(str(path))
        except OSError as exc:
            raise BrowserSocketError(f"cannot connect: {exc}") from exc
        try:
            sock.sendall((json.dumps({"op": op, "args": args}) + "\n").encode())
            sock.shutdown(socket.SHUT_WR)
        except OSError as exc:
            raise BrowserSocketError(f"send failed: {exc}") from exc
        chunks: list[bytes] = []
        try:
            while True:
                chunk = sock.recv(RECV_BUFSIZE)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout as exc:
            raise BrowserSocketError("timed out waiting for daemon") from exc
    finally:
        sock.close()
    raw = b"".join(chunks).decode().strip()
    if not raw:
        raise BrowserSocketError("empty response from daemon")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrowserSocketError(f"invalid JSON from daemon: {raw!r}") from exc


def unwrap_response(resp: dict[str, Any]) -> dict[str, Any]:
    """Return the browser payload from a control-socket reply.

    Browser handlers return their own ``{"ok": ...}`` dict, which the
    socket forwards at the top level. A generic op the socket wrapped as
    ``{"ok": true, "result": {...}}`` is unwrapped to the inner dict so
    the caller always sees the browser payload shape regardless of
    framing."""
    if "result" in resp and isinstance(resp.get("result"), dict):
        return resp["result"]
    return resp
