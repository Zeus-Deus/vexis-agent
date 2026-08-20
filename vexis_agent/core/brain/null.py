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

from vexis_agent.core.brain.base import (
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
        transcript_workspace: Path | None = None,
    ) -> None:
        # Queue of responses; ``respond()`` consumes from the head.
        # Once exhausted, returns "" (the default empty reply).
        self._responses: list[str] = list(responses or [])
        self._aux_results: list[AuxResult] = list(aux_results or [])
        self._system_prompt = system_prompt
        # When set, the transcript-readback methods front a real
        # claude-code-style session store at this workspace (delegating
        # to ``core.transcripts``) instead of yielding empty. Lets a
        # test seed JSONL session files AND keep BrainNull's
        # deterministic aux-spawn behaviour — the combination the
        # learning curator's "real review" integration tests need.
        # Default ``None`` keeps the zero-dependency empty behaviour
        # every other test relies on.
        self._transcript_workspace: Path | None = transcript_workspace
        # Pending exception for the next ``respond()`` call. ``None``
        # means no injection — proceed normally.
        self._pending_exc: BrainError | None = None
        self._pending_aux_exc: BrainError | None = None
        # Call recorder for test assertions.
        # Tuple shape: (message, chat_id, model, reasoning_level).
        # The last two are per-turn overrides and are None for the
        # typical case where the caller didn't pass them — tests that
        # don't care about overrides can ignore them.
        self._respond_calls: list[
            tuple[str, int, str | None, str | None]
        ] = []
        # Issue #48: parallel record of the per-turn ``session`` handle
        # each ``respond()`` (and, via the ABC's default ``astream`` →
        # ``respond`` bridge, each streamed turn) was threaded with.
        # Kept OUT of the 4-tuple above on purpose — many tests assert
        # that exact tuple shape, so the session goes in a side list.
        # ``None`` means the legacy active-session path; a ``SessionView``
        # (or any ``SessionLike``) means a specific named session.
        self._respond_sessions: list[Any] = []
        # Lower attachment plumbing: parallel record of the per-turn
        # ``attachments`` list each ``respond()`` call (and, via the
        # ABC's default ``astream`` fallback, each streamed turn) was
        # threaded with. ``None`` means the caller omitted the kwarg.
        self._respond_attachments: list[list[Path] | None] = []
        # Aux call records: full kwarg snapshot so tests can assert on
        # ``env_overrides``, ``allow_tools``, ``timeout_seconds``, etc.
        # ``aux_calls()`` returns a list of (prompt, tier) tuples for
        # the simple-shape assertions; ``aux_call_records()`` returns
        # the full dict list for tests that need every parameter.
        self._aux_records: list[dict[str, Any]] = []
        # Recorded MCP-config writes so tests can assert what the
        # caller passed without inspecting filesystem state.
        self._mcp_writes: list[list[McpServerSpec]] = []
        # Session-token counter — rotates produce monotonic ids.
        self._session_counter = count(1)
        self._session_token: str | None = f"null-session-{next(self._session_counter)}"
        # Issue #9: tests that exercise the file-mutation verifier
        # footer pre-inject a per-chat list via ``set_files_changed``;
        # ``consume_files_changed`` drains it the same way the real
        # brains drain their disk-derived buffer. Default empty so
        # the existing suite stays oblivious to the feature.
        self._files_changed_by_chat: dict[int, list[str]] = {}
        # Issue #11: record of ``compress_if_needed`` calls so tests
        # that exercise the handler's pre-turn compression hook can
        # assert the call happened without spinning up a real brain.
        # Each entry is the ``session_id`` argument the handler
        # passed. ``_compress_returns`` is a queue of return values
        # — tests pre-load to drive specific code paths (e.g.
        # "second turn returns True, first returns False").
        self._compress_calls: list[str] = []
        self._compress_returns: list[bool] = []

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
        called with, in order. Convenience for the common
        "did the caller use the right tier?" assertion shape."""
        return [(r["prompt"], r["model_tier"]) for r in self._aux_records]

    def respond_sessions(self) -> list[Any]:
        """Return the per-turn ``session`` handle each ``respond()`` call
        (including streamed turns routed through the ABC ``astream``
        fallback) was threaded with, in order. ``None`` entries are the
        legacy active-session path; non-``None`` entries are the
        ``SessionView`` a conversation was mapped onto (issue #48). Lets
        tests assert the transport threaded the right named session
        without spinning up a real brain."""
        return list(self._respond_sessions)

    def attachments_calls(self) -> list[list[Path] | None]:
        """Return the per-turn ``attachments`` list each ``respond()``
        call was threaded with, in order. ``None`` entries mean the
        caller omitted the kwarg (the common case)."""
        return list(self._respond_attachments)

    def aux_call_records(self) -> list[dict[str, Any]]:
        """Return the full kwarg snapshot for every ``spawn_aux()``
        call. Each dict has keys: ``prompt``, ``model_tier``,
        ``timeout_seconds``, ``env_overrides``, ``allow_tools``,
        ``allowed_tools``, ``cwd``, ``subsystem``, ``reasoning_level``,
        ``context_window``. Tests that need to assert on env-override
        merging, the per-call defense-in-depth tool allowlist (Issue
        #10), or per-call timeout use this."""
        return [dict(r) for r in self._aux_records]

    def mcp_writes(self) -> list[list[McpServerSpec]]:
        """Return the list of server-spec lists ``write_mcp_config()``
        was called with, in order. Empty list means the writer was
        never called."""
        return list(self._mcp_writes)

    # ─── Brain ABC implementations ───────────────────────────────

    async def respond(
        self,
        message: str,
        chat_id: int,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
        session: Any = None,
        attachments: list[Path] | None = None,
    ) -> str:
        # ``model`` and ``reasoning_level`` accepted for ABC parity;
        # recorded so tests can assert that overrides are forwarded
        # correctly through the handler/transport stack. Tuple shape
        # is (message, chat_id, model, reasoning_level) — UNCHANGED
        # (many tests pin it exactly). The per-turn ``session`` handle
        # (issue #48) is recorded separately in ``_respond_sessions``.
        self._respond_calls.append(
            (message, chat_id, model, reasoning_level),
        )
        self._respond_sessions.append(session)
        self._respond_attachments.append(attachments)
        if self._pending_exc is not None:
            exc = self._pending_exc
            self._pending_exc = None
            raise exc
        if not self._responses:
            # Fail loud: silent ``return ""`` would let tests pass for
            # the wrong reason when downstream assertions happen to
            # tolerate empty replies. Tests should pre-load enough
            # responses for the call volume they expect.
            raise AssertionError(
                f"BrainNull.respond exhausted at call #{len(self._respond_calls)} "
                f"(message={message!r}, chat_id={chat_id}); "
                f"pre-load more responses or use next_raises() to inject"
            )
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
        allowed_tools: list[str] | None = None,
        cwd: Path | None = None,
        subsystem: str | None = None,
        reasoning_level: str | None = None,
        context_window: int | None = None,
    ) -> AuxResult:
        # Issue #10 — record ``allowed_tools`` on the call record so
        # tests can assert each caller's defense-in-depth allowlist
        # without spawning a real subprocess. ``None`` stays ``None``
        # (back-compat: caller didn't pass it); an explicit list (even
        # ``[]``) is preserved as-is so the test surface can tell the
        # text-only-explicit case apart from the back-compat case.
        self._aux_records.append({
            "prompt": prompt,
            "model_tier": model_tier,
            "timeout_seconds": timeout_seconds,
            "env_overrides": dict(env_overrides) if env_overrides else None,
            "allow_tools": allow_tools,
            "allowed_tools": (
                list(allowed_tools) if allowed_tools is not None else None
            ),
            "cwd": cwd,
            "subsystem": subsystem,
            "reasoning_level": reasoning_level,
            "context_window": context_window,
        })
        if self._pending_aux_exc is not None:
            exc = self._pending_aux_exc
            self._pending_aux_exc = None
            raise exc
        if not self._aux_results:
            # Fail loud — see the matching docstring on respond()
            # exhaustion for rationale.
            raise AssertionError(
                f"BrainNull.spawn_aux exhausted at call #{len(self._aux_records)} "
                f"(prompt={prompt[:80]!r}, model_tier={model_tier!r}); "
                f"pre-load more aux_results or use next_aux_raises() to inject"
            )
        return self._aux_results.pop(0)

    def session_token(self) -> str | None:
        return self._session_token

    def rotate_session(self) -> str:
        self._session_token = f"null-session-{next(self._session_counter)}"
        return self._session_token

    def iter_session_metas(self) -> Iterator[Any]:
        if self._transcript_workspace is None:
            return iter(())
        from vexis_agent.core.transcripts import (
            iter_session_metas as _iter_session_metas,
        )
        return _iter_session_metas(self._transcript_workspace)

    def iter_messages(self, session_id: str) -> Iterator[Any]:
        if self._transcript_workspace is None:
            return iter(())
        from vexis_agent.core.transcripts import (
            iter_messages as _iter_messages,
            iter_session_metas as _iter_session_metas,
        )
        for meta in _iter_session_metas(self._transcript_workspace):
            if meta.session_uuid == session_id and meta.jsonl_path is not None:
                return _iter_messages(meta.jsonl_path)
        return iter(())

    def is_brain_owned_session(self, session_id: str) -> bool:
        if self._transcript_workspace is None:
            return False
        from vexis_agent.core.transcripts import (
            _is_curator_owned,
            iter_session_metas as _iter_session_metas,
        )
        for meta in _iter_session_metas(self._transcript_workspace):
            if meta.session_uuid == session_id and meta.jsonl_path is not None:
                return _is_curator_owned(meta.jsonl_path)
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

    # ─── Issue #11 — compress_if_needed test surface ─────────────

    def queue_compress_returns(self, *values: bool) -> None:
        """Test hook: pre-load the return values for the next N
        :meth:`compress_if_needed` calls. Unspecified calls fall back
        to the ABC default (``False``)."""
        self._compress_returns.extend(values)

    def compress_calls(self) -> list[str]:
        """Test hook: return the list of session_ids
        :meth:`compress_if_needed` was called with, in order."""
        return list(self._compress_calls)

    async def compress_if_needed(self, session_id: str) -> bool:
        """No-op by default (per ABC contract — null brain is inert).

        Tests can drive specific behaviour by pre-loading
        :meth:`queue_compress_returns`. Every call is recorded
        regardless so the test can assert the handler made the
        call."""
        self._compress_calls.append(session_id)
        if self._compress_returns:
            return self._compress_returns.pop(0)
        return False

    # ─── Issue #9 — file-mutation verifier footer test surface ───

    def set_files_changed(self, chat_id: int, files: list[str]) -> None:
        """Test-only: stage a file-mutation list that the next
        :meth:`consume_files_changed` call drains. Mirrors how
        ``ClaudeCodeBrain`` populates its buffer from a real
        snapshot diff; tests can drive the verifier footer code
        path without spinning up a real brain subprocess."""
        self._files_changed_by_chat[chat_id] = list(files)

    def consume_files_changed(self, chat_id: int) -> list[str]:
        return self._files_changed_by_chat.pop(chat_id, [])

    def peek_files_changed(self, chat_id: int) -> list[str]:
        return list(self._files_changed_by_chat.get(chat_id, []))
