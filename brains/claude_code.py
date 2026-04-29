"""Subprocess wrapper around `claude -p` with persistent session id."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from core.session import SessionStore

log = logging.getLogger(__name__)

DISALLOWED_TOOLS = [
    "Bash",
    "Edit",
    "Write",
    "Read",
    "Grep",
    "Glob",
    "WebFetch",
    "Task",
]


class BrainError(RuntimeError):
    pass


class BrainTimeoutError(BrainError):
    pass


class SessionLost(BrainError):
    """Raised when --resume fails because Claude Code can't find the session.
    The session has been rotated; the user's message was not processed."""


class ClaudeCodeBrain:
    def __init__(
        self, workspace: Path, session: SessionStore, timeout_seconds: int
    ) -> None:
        self._workspace = workspace
        self._session = session
        self._timeout = timeout_seconds

    async def respond(self, message: str) -> str:
        session_id = self._session.get()
        # First call pins the UUID with --session-id; subsequent calls resume it.
        if self._session.is_initialized():
            session_flag = ["--resume", session_id]
        else:
            session_flag = ["--session-id", session_id]

        argv = [
            "claude",
            "-p",
            message,
            *session_flag,
            "--disallowedTools",
            *DISALLOWED_TOOLS,
        ]
        log.debug(
            "Spawning claude -p (%s=%s, cwd=%s)",
            session_flag[0],
            session_id,
            self._workspace,
        )

        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self._workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except asyncio.TimeoutError as exc:
            await self._kill_group(proc)
            raise BrainTimeoutError(
                f"claude -p timed out after {self._timeout}s"
            ) from exc

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            # Claude Code's wording has varied across versions; substring
            # match is more robust than pinning an exact string.
            if self._session.is_initialized() and "No conversation found" in err:
                old_uuid = self._session.get()
                new_uuid = self._session.rotate()
                log.warning(
                    "Claude Code lost session %s; rotated to %s",
                    old_uuid,
                    new_uuid,
                )
                raise SessionLost(
                    "Claude Code session was lost. Rotated to new session."
                )
            raise BrainError(
                f"claude -p exited {proc.returncode}: {err or '(no stderr)'}"
            )

        # Mark only after a successful exit so a failed first call doesn't
        # leave us thinking the UUID is live.
        if not self._session.is_initialized():
            self._session.mark_initialized()
        return stdout.decode(errors="replace").strip()

    @staticmethod
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
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                log.error("claude -p (pid=%s) ignored SIGKILL", proc.pid)
