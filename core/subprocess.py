"""Shared async subprocess runner.

Process-group isolated, timeout-killable. Returns the raw triple
(returncode, stdout, stderr); callers map non-zero exits to domain
exceptions. Raises asyncio.TimeoutError when the timeout elapses
(after killing the process group).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

log = logging.getLogger(__name__)

_KILL_GRACE_SECONDS = 3


async def run(
    name: str,
    argv: list[str],
    timeout: float,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[int, bytes, bytes]:
    """Run a subprocess with timeout and process-group kill.

    Args:
        name: Logical name for log messages (e.g. "ffmpeg", "ydotool").
        argv: Full argv list (argv[0] is the executable).
        timeout: Wall-clock seconds before SIGTERM/SIGKILL escalation.
        env: Extra env vars merged over os.environ. None = inherit unchanged.
        cwd: Working directory; None = inherit.

    Returns:
        (returncode, stdout_bytes, stderr_bytes).

    Raises:
        asyncio.TimeoutError: timeout exceeded; process group has been killed.
    """
    merged_env = {**os.environ, **env} if env else None
    log.debug("run %s: %s", name, argv)

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=merged_env,
        cwd=str(cwd) if cwd is not None else None,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _kill_group(proc)
        raise

    return proc.returncode or 0, stdout, stderr


async def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
    except asyncio.TimeoutError:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            log.error("subprocess (pid=%s) ignored SIGKILL", proc.pid)
