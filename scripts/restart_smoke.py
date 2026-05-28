#!/usr/bin/env python3
"""End-to-end smoke for the /restart self-restart primitive.

Exercises the genuinely risky parts of the in-place daemon restart using
the REAL ``acquire_daemon_lock`` and a REAL ``ControlSocket``:

  * the daemon PID lock survives an ``os.execv`` (CLOEXEC drops the old
    flock; the fresh image re-acquires it; same PID passes the
    stale-vs-alive guard rather than tripping "already running"),
  * the control socket re-binds after a graceful stop + re-exec,
  * the re-exec preserves the PID (so systemd MainPID tracking holds).

It does NOT launch the full daemon (that needs Telegram secrets). The
chat-resume guarantee is a property of on-disk sessions, which a normal
restart already exercises; what this proves is that the new restart
plumbing comes back up cleanly.

Run (locally or in Docker):

    SMOKE_STATE_DIR=$(mktemp -d) python scripts/restart_smoke.py

Exit 0 + "SMOKE OK" on success; non-zero on any assertion failure.
Generation 0 sets up + re-execs; generation 1 verifies + exits.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from vexis_agent.core.control_socket import ControlSocket
from vexis_agent.main import acquire_daemon_lock


async def _dispatch(op: str, args: dict) -> dict:
    # Trivial echo dispatcher — enough to prove the socket actually serves.
    return {"ok": True, "op": op, "args": args}


async def _roundtrip(sock_path: Path) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    writer.write((json.dumps({"op": "ping", "args": {}}) + "\n").encode())
    await writer.drain()
    line = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return json.loads(line.decode())


async def main() -> int:
    state_dir = Path(os.environ["SMOKE_STATE_DIR"])
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_path = state_dir / "daemon.pid"
    sock_path = state_dir / "control.sock"

    gen = int(os.environ.get("SMOKE_GEN", "0"))
    expected_pid = int(os.environ.get("SMOKE_PID", "0"))

    # Real lock acquisition. On gen 1 the pid file already names our PID
    # (execv preserved it), so this must NOT raise DaemonAlreadyRunning.
    acquire_daemon_lock(pid_path)

    cs = ControlSocket(sock_path, _dispatch)
    await cs.start()  # real unix-socket bind; must succeed after re-exec

    resp = await _roundtrip(sock_path)
    assert resp.get("ok") is True, f"socket roundtrip failed: {resp}"

    print(
        f"[gen {gen}] pid={os.getpid()} lock+socket OK (roundtrip {resp['op']})",
        flush=True,
    )

    if gen == 0:
        # Mirror the daemon teardown: stop the socket before re-exec so
        # the fresh image re-binds cleanly.
        await cs.stop()
        os.environ["SMOKE_GEN"] = "1"
        os.environ["SMOKE_PID"] = str(os.getpid())
        sys.stdout.flush()
        sys.stderr.flush()
        # Same execv shape as main._exec_restart, but targeting THIS
        # script so we don't boot the real (secrets-requiring) daemon.
        os.execv(sys.executable, [sys.executable, __file__])
        return 0  # unreachable

    # Generation 1: the restarted image.
    assert os.getpid() == expected_pid, (
        f"PID changed across execv: {os.getpid()} != {expected_pid}"
    )
    pid_on_disk = int(pid_path.read_text().strip())
    assert pid_on_disk == os.getpid(), (
        f"pid file names {pid_on_disk}, expected {os.getpid()}"
    )
    await cs.stop()
    print(
        "SMOKE OK: re-exec preserved PID, re-acquired lock, re-bound socket, "
        "served a request in both generations.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
