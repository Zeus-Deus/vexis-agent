"""Subprocess wrapper around `claude -p` with persistent session id."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
from collections.abc import Iterator
from pathlib import Path

from core.safety import DESTRUCTIVE_PATTERNS
from core.sessions import SessionStore

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

DISALLOWED_TOOLS: list[str] = []  # All tools enabled in Step 6

DEFAULT_SOUL = (
    "You are Vexis, the user's personal agent. Be concise, truth-seeking, "
    "and genuinely useful. Never invent information; admit uncertainty. "
    "Address the user as 'sir' occasionally where it fits."
)

# Phrases that suggest Vexis is asking permission rather than reporting
# execution. Heuristic for dogfooding signal only — the model decides what
# to run; this just classifies the textual reply.
_ASKING_RE = re.compile(
    r"\b(should|shall|may|do you want|want me|would you like|"
    r"okay to|ok to|confirm|before I|about to|going to|"
    r"plan(ning)? to|ready to|may I|is it ok|is that ok)\b",
    re.IGNORECASE,
)
# Sentence terminator: ., !, ? followed by whitespace/EOS, or a newline.
# Bounding the asking-language scan to a single sentence prevents a `?` in
# one sentence from misclassifying a destructive mention in the next.
_SENTENCE_END = re.compile(r"[.!?](?:\s+|$)|\n+")


def _sentence_around(text: str, start: int, end: int) -> str:
    left = 0
    for m in _SENTENCE_END.finditer(text, 0, start):
        left = m.end()
    m = _SENTENCE_END.search(text, end)
    right = m.start() + 1 if m else len(text)
    return text[left:right]


def _read_markdown(path: Path) -> str | None:
    """Read a UTF-8 markdown file. Missing file is fine (returns None);
    unreadable / non-UTF-8 file logs a warning and also returns None."""
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("Could not read %s: %s", path, exc)
        return None
    except UnicodeDecodeError as exc:
        log.warning("%s is not valid UTF-8: %s", path, exc)
        return None
    return content or None


def audit_destructive_mentions(response: str) -> Iterator[tuple[str, bool]]:
    """Yield (reason, asked_first) for each destructive pattern hit in response.

    asked_first is True when the enclosing sentence contains a question mark
    or asking-language, suggesting Vexis sought confirmation rather than
    reporting after the fact. False = appears to have run it.
    """
    for pattern, reason in DESTRUCTIVE_PATTERNS:
        for match in pattern.finditer(response):
            sentence = _sentence_around(response, *match.span())
            asked = "?" in sentence or bool(_ASKING_RE.search(sentence))
            yield reason, asked


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

    def _read_soul(self) -> str | None:
        return _read_markdown(self._workspace / "SOUL.md")

    def _read_capabilities(self) -> str | None:
        return _read_markdown(_PROJECT_ROOT / "CAPABILITIES.md")

    async def respond(self, message: str) -> str:
        session_id = self._session.get()
        # First call pins the UUID with --session-id; subsequent calls resume it.
        if self._session.is_initialized():
            session_flag = ["--resume", session_id]
        else:
            session_flag = ["--session-id", session_id]

        soul = self._read_soul() or DEFAULT_SOUL
        capabilities = self._read_capabilities()
        system_prompt = f"{soul}\n\n{capabilities}" if capabilities else soul

        argv = [
            "claude",
            "-p",
            message,
            *session_flag,
            "--append-system-prompt",
            system_prompt,
        ]
        if DISALLOWED_TOOLS:
            argv += ["--disallowedTools", *DISALLOWED_TOOLS]
        # bypassPermissions: required when running headless (-p) with tools
        # enabled. Otherwise Claude Code would try to prompt interactively
        # for each tool use and the call would hang. Step 6.5 will add a
        # PreToolUse hook that consults core.safety for hard enforcement.
        argv += ["--permission-mode", "bypassPermissions"]
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
        response = stdout.decode(errors="replace").strip()
        for reason, asked in audit_destructive_mentions(response):
            if asked:
                log.info("Vexis confirmed before destructive: %s", reason)
            else:
                log.info("Vexis ran without confirm: %s", reason)
        return response

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
