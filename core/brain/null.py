"""BrainNull — canned-response fake for unit tests.

No subprocess, no API costs, no binary required. Returns pre-configured
responses in order; raises pre-configured exceptions when a test wants
to verify error handling. The default unit-test brain — every transport
test, goal/schedule test, dashboard test uses this; the smoke suite
(real ``claude -p`` / real ``opencode``) is opt-in via
``@pytest.mark.brain_smoke{,_opencode}``.

Usage:

    brain = BrainNull(responses=["hello", "goodbye"])
    assert await brain.respond("hi", chat_id=1) == "hello"
    assert await brain.respond("bye", chat_id=1) == "goodbye"

    # Inject an exception for the next call:
    brain.next_raises(SessionLost("test"))
    with pytest.raises(SessionLost):
        await brain.respond("oops", chat_id=1)

    # Inspect what the transport handed to the brain:
    assert brain.calls() == [("hi", 1), ("bye", 1), ("oops", 1)]

Design citation: ``.plans/brain-abstraction-research.md`` §4 ("BrainNull
— the testing fake").
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import count
from pathlib import Path
from typing import Any

from core.brain.base import (
    AuxResult,
    Brain,
    BrainError,
    BrainHealth,
    McpServerSpec,
)


class BrainNull(Brain):
    """Canned-response brain. See module docstring."""

    def __init__(
        self,
        responses: list[str] | None = None,
        aux_results: list[AuxResult] | None = None,
        system_prompt: str = "[null brain] system prompt",
    ) -> None:
        # Queue of responses; ``respond()`` consumes from the head.
        # Once exhausted, returns "" (the default empty reply).
        self._responses: list[str] = list(responses or [])
        self._aux_results: list[AuxResult] = list(aux_results or [])
        self._system_prompt = system_prompt
        # Pending exception for the next ``respond()`` call. ``None``
        # means no injection — proceed normally.
        self._pending_exc: BrainError | None = None
        self._pending_aux_exc: BrainError | None = None
        # Call recorder for test assertions.
        self._respond_calls: list[tuple[str, int]] = []
        self._aux_calls: list[tuple[str, str | None]] = []
        # Recorded MCP-config writes so tests can assert what the
        # caller passed without inspecting filesystem state.
        self._mcp_writes: list[list[McpServerSpec]] = []
        # Session-token counter — rotates produce monotonic ids.
        self._session_counter = count(1)
        self._session_token: str | None = f"null-session-{next(self._session_counter)}"

    # ─── injection / inspection helpers (test-facing API) ────────

    def next_raises(self, exc: BrainError) -> None:
        """Inject ``exc`` so the next ``respond()`` call raises it."""
        self._pending_exc = exc

    def next_aux_raises(self, exc: BrainError) -> None:
        """Inject ``exc`` so the next ``spawn_aux()`` call raises it."""
        self._pending_aux_exc = exc

    def calls(self) -> list[tuple[str, int]]:
        """Return ``(message, chat_id)`` pairs ``respond()`` was called
        with, in order. Lets tests assert what the transport handed to
        the brain without inspecting subprocess state."""
        return list(self._respond_calls)

    def aux_calls(self) -> list[tuple[str, str | None]]:
        """Return ``(prompt, model_tier)`` pairs ``spawn_aux()`` was
        called with, in order."""
        return list(self._aux_calls)

    def mcp_writes(self) -> list[list[McpServerSpec]]:
        """Return the list of server-spec lists ``write_mcp_config()``
        was called with, in order. Empty list means the writer was
        never called."""
        return list(self._mcp_writes)

    # ─── Brain ABC implementations ───────────────────────────────

    async def respond(self, message: str, chat_id: int) -> str:
        self._respond_calls.append((message, chat_id))
        if self._pending_exc is not None:
            exc = self._pending_exc
            self._pending_exc = None
            raise exc
        if not self._responses:
            return ""
        return self._responses.pop(0)

    def build_system_prompt(self) -> str:
        return self._system_prompt

    async def spawn_aux(
        self,
        prompt: str,
        *,
        model_tier: str | None = None,
        timeout_seconds: float = 60.0,
        env_overrides: dict[str, str] | None = None,
        allow_tools: bool = False,
        cwd: Path | None = None,
    ) -> AuxResult:
        self._aux_calls.append((prompt, model_tier))
        if self._pending_aux_exc is not None:
            exc = self._pending_aux_exc
            self._pending_aux_exc = None
            raise exc
        if not self._aux_results:
            return AuxResult(stdout="", stderr="", returncode=0)
        return self._aux_results.pop(0)

    def session_token(self) -> str | None:
        return self._session_token

    def rotate_session(self) -> str:
        self._session_token = f"null-session-{next(self._session_counter)}"
        return self._session_token

    def iter_session_metas(self) -> Iterator[Any]:
        return iter(())

    def iter_messages(self, session_id: str) -> Iterator[Any]:
        return iter(())

    def is_brain_owned_session(self, session_id: str) -> bool:
        return False

    def write_mcp_config(self, servers: list[McpServerSpec]) -> Path:
        # Record the call for test assertions; return a placeholder
        # path that doesn't exist on disk (tests that need a real
        # path should mock this method directly).
        self._mcp_writes.append(list(servers))
        return Path("/dev/null/null-brain-mcp-config")

    def instruction_file_name(self) -> str:
        return "AGENTS.md"

    def instruction_search_paths(self, workspace: Path) -> list[Path]:
        return []

    async def healthcheck(self) -> BrainHealth:
        return BrainHealth(ok=True, error=None, hints=[])

    async def kill_in_flight(self) -> None:
        # No subprocess to kill in the null brain.
        return None
