"""CLI client for ``vexis-watch``.

Mirrors ``vexis-bg``: single-message-per-connection JSON over the
daemon's control socket. The brain spawns this from inside a session
to declare "watch this Codemux workspace for me," and the user can
also invoke it directly from a shell.

For ``register``, the chat_id is taken from the ``VEXIS_CHAT_ID`` env
var the foreground brain sets on its own subprocess — same wiring as
``vexis-bg``. Set it manually when running from a plain shell.

Exit codes — load-bearing contract for callers:

  * ``0`` — operation succeeded. Result JSON on stdout.
  * ``0`` — Codemux MCP not configured (``CodemuxNotConfigured`` from
    the dispatch handler). Stderr carries the explanation; stdout is
    empty. This is intentional and skill-friendly: a skill can call
    ``vexis-watch register …`` unconditionally without a pre-check
    and get a no-op rather than a crash. The daemon is healthy, the
    feature is just off; that's not a runtime error.
  * ``1`` — daemon error or operation failure (unknown agent,
    duplicate name, malformed args, source unavailable, etc.).
    Stderr carries the message. Callers branching on success-vs-
    real-error use this code.
  * ``1`` — daemon unreachable / no socket / send timeout. Distinct
    from "MCP not configured": the daemon itself isn't running.

To distinguish "ok" from "no-op" when you DO need to branch:
inspect the empty-stdout signature, or call ``vexis-watch list``
first — it returns ``[]`` on a wired-but-empty registry and the
CodemuxNotConfigured message on the absent-MCP path.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 10.0
RECV_BUFSIZE = 65536


def _socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "vexis-agent" / "vexis-agent.sock"


def _send(op: str, args: dict, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    path = _socket_path()
    if not path.exists():
        print(
            f"vexis-watch: daemon socket not found at {path} — is vexis-agent running?",
            file=sys.stderr,
        )
        sys.exit(1)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        try:
            sock.connect(str(path))
        except OSError as exc:
            print(f"vexis-watch: cannot connect: {exc}", file=sys.stderr)
            sys.exit(1)
        try:
            sock.sendall((json.dumps({"op": op, "args": args}) + "\n").encode())
            sock.shutdown(socket.SHUT_WR)
        except OSError as exc:
            print(f"vexis-watch: send failed: {exc}", file=sys.stderr)
            sys.exit(1)
        chunks: list[bytes] = []
        try:
            while True:
                chunk = sock.recv(RECV_BUFSIZE)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout:
            print("vexis-watch: timed out waiting for daemon", file=sys.stderr)
            sys.exit(1)
    finally:
        sock.close()
    raw = b"".join(chunks).decode().strip()
    if not raw:
        print("vexis-watch: empty response from daemon", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"vexis-watch: invalid JSON from daemon: {raw!r}", file=sys.stderr)
        sys.exit(1)


def _print_result_or_exit(resp: dict) -> int:
    if resp.get("ok"):
        result = resp.get("result")
        print(json.dumps(result))
        return 0
    # Distinguish "MCP not wired" from real errors. Exit 0 in the
    # former — the daemon is healthy, the feature is just off.
    kind = resp.get("kind") or ""
    err = resp.get("error", "unknown error")
    if kind == "CodemuxNotConfigured":
        print(err, file=sys.stderr)
        return 0
    print(f"vexis-watch: {err}", file=sys.stderr)
    return 1


def _resolve_chat_id() -> int:
    raw = os.environ.get("VEXIS_CHAT_ID")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    print(
        "vexis-watch: VEXIS_CHAT_ID is not set. The foreground brain sets it "
        "automatically; if you're running this from a shell, export it to "
        "your Telegram chat id first.",
        file=sys.stderr,
    )
    sys.exit(1)


def _parse_idle_after(raw: str) -> int:
    """Accept ``30s`` / ``2m`` / ``120`` (bare seconds)."""
    raw = raw.strip().lower()
    if raw.endswith("s"):
        return int(raw[:-1])
    if raw.endswith("m"):
        return int(raw[:-1]) * 60
    return int(raw)


def _cmd_register(args: argparse.Namespace) -> int:
    chat_id = _resolve_chat_id()
    payload: dict = {
        "chat_id": chat_id,
        "name": args.name,
        "source": args.source,
        "agent_kind": args.agent_kind,
    }
    # ``--workspace`` is the user-facing handle (Codemux workspace id);
    # the daemon resolves it to the internal session id at register
    # time. ``--session-id`` is the escape hatch for power users who
    # want to bypass auto-resolution (e.g. registering a pane that
    # belongs to a workspace they don't have focused).
    if args.session_id:
        payload["identifier"] = args.session_id
        if args.workspace:
            payload["workspace_id"] = args.workspace
    elif args.workspace:
        # For non-codemux sources, ``--workspace`` is the source-
        # specific identifier — pass it through as ``identifier``.
        # The daemon's auto-resolve only fires when source == codemux
        # AND identifier wasn't supplied.
        if args.source == "codemux":
            payload["workspace_id"] = args.workspace
        else:
            payload["identifier"] = args.workspace
    else:
        print(
            "vexis-watch: --workspace or --session-id required.",
            file=sys.stderr,
        )
        return 1
    if args.idle_after:
        payload["idle_after_seconds"] = _parse_idle_after(args.idle_after)
    if args.goal:
        payload["goal_hint"] = args.goal
    if args.repo:
        payload["repo_path"] = args.repo
    return _print_result_or_exit(_send("watch_register", payload))


def _cmd_unregister(args: argparse.Namespace) -> int:
    return _print_result_or_exit(_send("watch_unregister", {"name": args.name}))


def _cmd_list(_: argparse.Namespace) -> int:
    return _print_result_or_exit(_send("watch_list", {}))


def _cmd_status(args: argparse.Namespace) -> int:
    payload: dict = {}
    if args.name:
        payload["name"] = args.name
    return _print_result_or_exit(_send("watch_status", payload))


def _cmd_mute(args: argparse.Namespace) -> int:
    return _print_result_or_exit(
        _send("watch_mute", {"name": args.name, "muted": True})
    )


def _cmd_unmute(args: argparse.Namespace) -> int:
    return _print_result_or_exit(
        _send("watch_mute", {"name": args.name, "muted": False})
    )


def _cmd_tail(args: argparse.Namespace) -> int:
    resp = _send("watch_tail", {"name": args.name, "lines": args.lines})
    if not resp.get("ok"):
        kind = resp.get("kind") or ""
        err = resp.get("error", "tail failed")
        if kind == "CodemuxNotConfigured":
            print(err, file=sys.stderr)
            return 0
        print(f"vexis-watch: {err}", file=sys.stderr)
        return 1
    text = (resp.get("result") or {}).get("text", "")
    if text:
        print(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vexis-watch",
        description="Register & inspect long-running terminal-attached agents.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    reg = sub.add_parser("register", help="Watch a workspace / pane / pty.")
    reg.add_argument("--name", required=True, help="kebab-case handle.")
    reg.add_argument(
        "--source", default="codemux",
        help="Source plugin slug (default: codemux).",
    )
    reg.add_argument(
        "--workspace", default=None,
        help=(
            "Codemux workspace id (auto-resolved to a session id at "
            "register time — the workspace must be active in Codemux). "
            "For non-codemux sources, the source-specific identifier."
        ),
    )
    reg.add_argument(
        "--session-id", default=None,
        help=(
            "Codemux terminal session id (bypasses workspace_id auto-"
            "resolution; use when you already know the session id, "
            "e.g. from a prior `codemux json pane_list`)."
        ),
    )
    reg.add_argument(
        "--agent-kind", required=True,
        help="Inner agent CLI (claude-code, opencode, aider, …).",
    )
    reg.add_argument(
        "--idle-after", default=None,
        help="Idle threshold (e.g. '30s', '2m'). Default 30s.",
    )
    reg.add_argument(
        "--goal", default=None, help="One-line description of what's running.",
    )
    reg.add_argument(
        "--repo", default=None, help="Optional repo path hint.",
    )
    reg.set_defaults(func=_cmd_register)

    unreg = sub.add_parser("unregister", help="Stop watching.")
    unreg.add_argument("name")
    unreg.set_defaults(func=_cmd_unregister)

    lst = sub.add_parser("list", help="Dump the registry as JSON.")
    lst.set_defaults(func=_cmd_list)

    st = sub.add_parser("status", help="Status for one or all watched agents.")
    st.add_argument("--name", default=None)
    st.set_defaults(func=_cmd_status)

    mute = sub.add_parser("mute", help="Silence future idle notifications.")
    mute.add_argument("name")
    mute.set_defaults(func=_cmd_mute)

    unmute = sub.add_parser("unmute", help="Re-arm idle notifications.")
    unmute.add_argument("name")
    unmute.set_defaults(func=_cmd_unmute)

    tl = sub.add_parser("tail", help="Print recent terminal output.")
    tl.add_argument("name")
    tl.add_argument("--lines", type=int, default=20)
    tl.set_defaults(func=_cmd_tail)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
