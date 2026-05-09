"""CLI client for ``vexis-bg``.

Talks to the daemon's control socket
(``$XDG_RUNTIME_DIR/vexis-agent/vexis-agent.sock``) over a
single-message-per-connection JSON protocol. Vexis (the foreground
brain) is the primary caller; the user can also invoke this directly
from a shell when debugging.

For ``spawn``, the chat_id is taken from the ``VEXIS_CHAT_ID`` env var
the foreground brain sets on its own subprocess. That's how a tool
spawned by ``claude -p`` knows which Telegram conversation to ping
when the background work finishes.
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
            f"vexis-bg: daemon socket not found at {path} — is vexis-agent running?",
            file=sys.stderr,
        )
        sys.exit(1)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        try:
            sock.connect(str(path))
        except OSError as exc:
            print(f"vexis-bg: cannot connect: {exc}", file=sys.stderr)
            sys.exit(1)
        try:
            sock.sendall((json.dumps({"op": op, "args": args}) + "\n").encode())
            sock.shutdown(socket.SHUT_WR)
        except OSError as exc:
            print(f"vexis-bg: send failed: {exc}", file=sys.stderr)
            sys.exit(1)
        chunks: list[bytes] = []
        try:
            while True:
                chunk = sock.recv(RECV_BUFSIZE)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout:
            print("vexis-bg: timed out waiting for daemon", file=sys.stderr)
            sys.exit(1)
    finally:
        sock.close()
    raw = b"".join(chunks).decode().strip()
    if not raw:
        print("vexis-bg: empty response from daemon", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"vexis-bg: invalid JSON from daemon: {raw!r}", file=sys.stderr)
        sys.exit(1)


def _resolve_chat_id() -> int:
    raw = os.environ.get("VEXIS_CHAT_ID")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    print(
        "vexis-bg: VEXIS_CHAT_ID is not set. The foreground brain sets it "
        "automatically; if you're running this from a shell, export it to "
        "your Telegram chat id first.",
        file=sys.stderr,
    )
    sys.exit(1)


def _print_result_or_exit(resp: dict) -> int:
    if not resp.get("ok"):
        err = resp.get("error", "unknown error")
        print(f"vexis-bg: {err}", file=sys.stderr)
        return 1
    result = resp.get("result")
    print(json.dumps(result))
    return 0


def _cmd_spawn(name: str, prompt: str) -> int:
    chat_id = _resolve_chat_id()
    return _print_result_or_exit(
        _send("bg_spawn", {"chat_id": chat_id, "name": name, "prompt": prompt})
    )


def _cmd_status(name: str | None) -> int:
    args: dict = {} if name is None else {"name": name}
    return _print_result_or_exit(_send("bg_status", args))


def _cmd_cancel(name: str) -> int:
    return _print_result_or_exit(_send("bg_cancel", {"name": name}))


def _cmd_tail(name: str, lines: int) -> int:
    resp = _send("bg_tail", {"name": name, "lines": lines})
    if not resp.get("ok"):
        print(f"vexis-bg: {resp.get('error', 'tail failed')}", file=sys.stderr)
        return 1
    text = (resp.get("result") or {}).get("text", "")
    if text:
        print(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vexis-bg",
        description="Spawn, inspect, and cancel background Vexis tasks.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("spawn", help="Spawn a new background task.")
    sp.add_argument("name", help="kebab-case task name (3-30 chars).")
    sp.add_argument("prompt", help="Prompt to feed claude -p.")

    st = sub.add_parser("status", help="Show one or all task summaries.")
    st.add_argument("--name", default=None, help="Limit output to this task.")

    cn = sub.add_parser("cancel", help="Cancel a running task.")
    cn.add_argument("name")

    tl = sub.add_parser("tail", help="Print the tail of a task's log.")
    tl.add_argument("name")
    tl.add_argument("--lines", type=int, default=50, help="How many lines to print.")

    args = parser.parse_args()
    if args.cmd == "spawn":
        return _cmd_spawn(args.name, args.prompt)
    if args.cmd == "status":
        return _cmd_status(args.name)
    if args.cmd == "cancel":
        return _cmd_cancel(args.name)
    if args.cmd == "tail":
        return _cmd_tail(args.name, args.lines)
    return 2


if __name__ == "__main__":
    sys.exit(main())
