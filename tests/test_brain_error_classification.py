# ruff: noqa: F811
# (pytest fixture parameters intentionally re-bind names imported
# from test_brain_cancel; ruff misreads this as unused-redefinition.)
"""Brain error classification + inline retry — regression suite for
the May 2026 schedule-fire crash.

Background: a scheduled fire of ``skill-sync`` hit an Anthropic 500
at exactly the moment the brain spawned. ``claude -p`` exited 1 with
*empty stderr* and the API error text only in stdout's stream-json as
an ``assistant`` text block. The pre-fix brain wrapper raised
``BrainError("claude -p exited 1: (no stderr)")`` and the handler
surfaced "Something broke. Logs have details." The user had to ssh
into the JSONL to find out why.

This suite pins the post-fix behaviour:

  1. ``_classify_brain_failure`` maps known wordings to the right
     subclass (``BrainTransientError`` / ``BrainPermanentError`` /
     base ``BrainError``).
  2. ``ClaudeCodeBrain.respond`` and ``ClaudeCodeBrain.astream``
     raise the classified subclass with the actual upstream message.
  3. Inline retry on ``BrainTransientError``: the first attempt
     fails, the second succeeds, no exception propagates and no
     duplicate output is yielded downstream.
  4. Streaming retry is suppressed once *anything* has been yielded
     to the caller — a transient mid-stream propagates instead of
     double-rendering.
  5. ``BrainPermanentError`` is NOT retried.
  6. ``handler._shorten_brain_error`` trims the prefix and caps the
     length so the Telegram toast stays under message-length nag.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from vexis_agent.core.brain import claude_code as brain_module
from vexis_agent.core.brain.claude_code import (
    BrainError,
    BrainPermanentError,
    BrainTransientError,
    ClaudeCodeBrain,
    _classify_brain_failure,
)
from vexis_agent.core import handler as handler_module
from vexis_agent.core.running_tasks import RunningTasks

# Reuse the FakeProc / FakeSession / patch_killpg fixtures from the
# brain_cancel suite — same machinery, same semantics. The
# noqa marker tells ruff these are pytest fixtures (used implicitly
# by ``def test_xxx(..., patch_killpg)`` signatures), not dead imports.
from tests.test_brain_cancel import (  # noqa: F401
    FakeProc,
    FakeSession,
    _stream_json_result,
    patch_killpg,  # pytest fixture used by tests below — see top noqa
)


# ─── _classify_brain_failure: pattern matrix ─────────────────────


@pytest.mark.parametrize(
    "stderr_text,assistant_text,expected_cls",
    [
        # The exact wording from 15 May 2026 production crash. This
        # was the assistant-text body; stderr was empty.
        (
            "",
            "API Error: 500 Internal server error. This is a "
            "server-side issue, usually temporary — try again in a "
            "moment. If it persists, check status.claude.com.",
            BrainTransientError,
        ),
        # Other 5xx codes claude wraps the same way.
        ("", "API Error: 502 Bad Gateway", BrainTransientError),
        ("", "API Error: 503 Service Unavailable", BrainTransientError),
        ("", "API Error: 504 Gateway Timeout", BrainTransientError),
        # Rate limit: matches twice (the regex catches both the
        # "API Error: 429" form and the "rate limit" wording).
        ("", "API Error: 429 Too Many Requests", BrainTransientError),
        ("", "rate_limit_error: please slow down", BrainTransientError),
        # Anthropic SDK-style "overloaded_error" code.
        (
            "",
            '{"type":"error","error":{"type":"overloaded_error"}}',
            BrainTransientError,
        ),
        # Network-style errors land in stderr more often than stdout.
        ("connection reset by peer", "", BrainTransientError),
        ("request timed out after 30s", "", BrainTransientError),
        # 4xx — permanent. The user has to do something.
        (
            "",
            "API Error: 401 Unauthorized — invalid_api_key",
            BrainPermanentError,
        ),
        ("", "API Error: 403 Forbidden", BrainPermanentError),
        ("", "API Error: 400 invalid_request_error", BrainPermanentError),
        # Model-not-found wording — claude-code prints this verbatim.
        (
            "",
            "There's an issue with the selected model: not-a-real-model",
            BrainPermanentError,
        ),
        # Insufficient credit — different products use different wording.
        ("", "insufficient credit on account", BrainPermanentError),
        ("", "insufficient quota for this billing period", BrainPermanentError),
        # Unknown shape → base BrainError. Caller treats as
        # non-retryable, surfaces verbatim.
        ("segfault in tokenizer", "", BrainError),
        ("", "", BrainError),
    ],
)
def test_classify_brain_failure_matrix(stderr_text, assistant_text, expected_cls):
    cls, message = _classify_brain_failure(
        stderr_text=stderr_text,
        assistant_text=assistant_text,
    )
    assert cls is expected_cls, (
        f"stderr={stderr_text!r} assistant={assistant_text!r} "
        f"mapped to {cls.__name__}, expected {expected_cls.__name__}"
    )
    # Sanity: the message contains some piece of the input (when input
    # is non-empty), so the user actually sees what broke.
    if stderr_text or assistant_text:
        joined = (stderr_text + " " + assistant_text).strip()
        # The message is never the literal "(no stderr or assistant text)"
        # when we fed something in.
        assert message != "(no stderr or assistant text)"
        # And one of the input tokens is in the surfaced message.
        for token in joined.split():
            if len(token) >= 5:
                assert token in message
                break


def test_classify_brain_failure_combines_both_sources():
    """When stderr AND assistant text are both populated, both end up
    in the diagnostic so we don't drop context. The 15 May crash had
    stderr empty, but defensively we want to handle the case where
    claude emits both."""
    cls, msg = _classify_brain_failure(
        stderr_text="auxiliary stderr noise",
        assistant_text="API Error: 500 something broke upstream",
    )
    assert cls is BrainTransientError
    assert "API Error: 500" in msg
    assert "auxiliary stderr noise" in msg


def test_classify_brain_failure_empty_inputs():
    cls, msg = _classify_brain_failure(stderr_text="", assistant_text="")
    assert cls is BrainError
    assert "(no stderr or assistant text)" in msg


# ─── _read_stream_events: tuple shape ────────────────────────────


def test_read_stream_events_captures_assistant_text():
    """The helper must surface ``assistant`` text blocks as the second
    tuple element. The May 2026 crash relied on this — claude wrote
    the API error into an assistant text block and we need to read it
    out for classification."""
    stream_lines = (
        json.dumps(
            {"type": "system", "subtype": "init", "session_id": "s1"}
        ).encode()
        + b"\n"
        + json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "API Error: 500 boom"}],
            },
        }).encode()
        + b"\n"
    )

    async def scenario():
        from vexis_agent.core.brain.claude_code import _read_stream_events
        from vexis_agent.core.status import StatusFile
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # Avoid touching real runtime_dir.
            from vexis_agent.core import paths as paths_mod
            from vexis_agent.core import status as status_mod
            orig = paths_mod.runtime_dir
            paths_mod.runtime_dir = lambda: Path(tmp)
            status_mod.runtime_dir = lambda: Path(tmp)
            try:
                sf = StatusFile(chat_id=9999)
                stream = _FakeReader(stream_lines)
                final, assistant = await _read_stream_events(stream, sf)
                return final, assistant
            finally:
                paths_mod.runtime_dir = orig
                status_mod.runtime_dir = orig

    final, assistant = asyncio.run(scenario())
    assert final == ""  # no result event, so no buffered reply
    assert "API Error: 500" in assistant


class _FakeReader:
    """Minimal asyncio.StreamReader stand-in for the helper test."""
    def __init__(self, data: bytes) -> None:
        self._buf = data

    async def readline(self) -> bytes:
        if not self._buf:
            return b""
        nl = self._buf.find(b"\n")
        if nl < 0:
            line, self._buf = self._buf, b""
            return line
        line, self._buf = self._buf[: nl + 1], self._buf[nl + 1:]
        return line


# ─── Integration: respond() raises classified subclass ───────────


def _stream_json_api_error(text: str) -> bytes:
    """Build a stream-json sequence mimicking the 15 May 2026 crash:
    init event + one assistant text block carrying the API error +
    NO result event (claude bailed before emitting it)."""
    return (
        json.dumps(
            {"type": "system", "subtype": "init", "session_id": "s1"}
        ).encode()
        + b"\n"
        + json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        }).encode()
        + b"\n"
    )


def _build_brain(running_tasks: RunningTasks, tmp_path: Path) -> ClaudeCodeBrain:
    return ClaudeCodeBrain(
        workspace=tmp_path,
        session=FakeSession(),
        running_tasks=running_tasks,
    )


def _patch_spawn_sequence(monkeypatch, procs: list[FakeProc]) -> list[int]:
    """Patch ``create_subprocess_exec`` to hand out ``procs`` in order.

    Returns a list ``calls`` whose length grows with each spawn — useful
    for asserting "the brain only spawned once" vs "the brain retried."
    """
    calls: list[int] = []

    async def _fake_spawn(*_argv, **_kwargs) -> FakeProc:
        idx = len(calls)
        calls.append(idx)
        if idx >= len(procs):
            raise RuntimeError(f"spawn #{idx} but only {len(procs)} procs primed")
        return procs[idx]

    monkeypatch.setattr(brain_module.asyncio, "create_subprocess_exec", _fake_spawn)
    return calls


def test_respond_raises_transient_on_api_500(monkeypatch, tmp_path, patch_killpg):
    """The 15 May 2026 production crash: API 500 in assistant text,
    empty stderr, exit 1. Must raise BrainTransientError with the
    actual upstream wording. With retry, BOTH attempts must fail."""
    msg = (
        "API Error: 500 Internal server error. This is a "
        "server-side issue, usually temporary — try again in a moment. "
        "If it persists, check status.claude.com."
    )
    procs = [
        FakeProc(
            pid=501 + i, mode="fail",
            stdout=_stream_json_api_error(msg),
            stderr=b"", returncode=1,
        )
        for i in range(2)
    ]
    for p in procs:
        patch_killpg[p.pid] = p
    calls = _patch_spawn_sequence(monkeypatch, procs)
    # Skip the 3s retry sleep so the test stays sub-second.
    monkeypatch.setattr(brain_module, "_TRANSIENT_RETRY_DELAY_SECONDS", 0)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario():
        with pytest.raises(BrainTransientError) as ei:
            await brain.respond("ping", chat_id=50)
        return ei.value

    err = asyncio.run(scenario())
    assert "API Error: 500" in str(err)
    assert len(calls) == 2, f"expected exactly 2 spawn attempts, got {len(calls)}"
    assert not reg.is_running(50)


def test_respond_inline_retry_recovers(monkeypatch, tmp_path, patch_killpg):
    """First attempt: transient 500. Second attempt: success. The
    caller sees the success — no exception propagates. This is the
    'user never knows' path."""
    bad = FakeProc(
        pid=601, mode="fail",
        stdout=_stream_json_api_error("API Error: 500 transient blip"),
        stderr=b"", returncode=1,
    )
    good = FakeProc(
        pid=602, mode="ok",
        stdout=_stream_json_result("recovered, sir"),
    )
    patch_killpg[bad.pid] = bad
    patch_killpg[good.pid] = good
    calls = _patch_spawn_sequence(monkeypatch, [bad, good])
    monkeypatch.setattr(brain_module, "_TRANSIENT_RETRY_DELAY_SECONDS", 0)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario():
        return await brain.respond("ping", chat_id=51)

    reply = asyncio.run(scenario())
    assert reply == "recovered, sir"
    assert len(calls) == 2
    assert not reg.is_running(51)


def test_respond_permanent_error_is_not_retried(monkeypatch, tmp_path, patch_killpg):
    """A permanent error (401, model-not-found, etc) must NOT consume
    a retry attempt — the cause won't change between attempts and
    retrying just burns latency."""
    procs = [
        FakeProc(
            pid=701, mode="fail",
            stdout=_stream_json_api_error("API Error: 401 Unauthorized"),
            stderr=b"", returncode=1,
        ),
        # If the brain ever spawns this one the test fails — but we
        # prime it anyway so the _fake_spawn sequence doesn't raise.
        FakeProc(pid=702, mode="ok", stdout=_stream_json_result("should not run")),
    ]
    for p in procs:
        patch_killpg[p.pid] = p
    calls = _patch_spawn_sequence(monkeypatch, procs)
    monkeypatch.setattr(brain_module, "_TRANSIENT_RETRY_DELAY_SECONDS", 0)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario():
        with pytest.raises(BrainPermanentError) as ei:
            await brain.respond("ping", chat_id=52)
        return ei.value

    err = asyncio.run(scenario())
    assert "401" in str(err)
    assert len(calls) == 1, "permanent errors must not retry"
    assert not reg.is_running(52)


def test_respond_falls_back_to_brain_error_on_unknown_wording(
    monkeypatch, tmp_path, patch_killpg,
):
    """A non-zero exit with a body that doesn't match any pattern is
    NOT classified as transient — we don't want to retry blindly on
    unknown failures. Surface it as base BrainError."""
    proc = FakeProc(
        pid=801, mode="fail",
        stdout=b"", stderr=b"weird subprocess crash, no API context",
        returncode=2,
    )
    patch_killpg[proc.pid] = proc
    calls = _patch_spawn_sequence(monkeypatch, [proc])
    monkeypatch.setattr(brain_module, "_TRANSIENT_RETRY_DELAY_SECONDS", 0)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario():
        with pytest.raises(BrainError) as ei:
            await brain.respond("ping", chat_id=53)
        # Must be base BrainError, not transient or permanent — the
        # caller's `except BrainTransientError` won't catch it, so
        # the user gets the generic toast instead of a misleading
        # "retry in a moment" message.
        assert type(ei.value) is BrainError
        return ei.value

    err = asyncio.run(scenario())
    assert "weird subprocess crash" in str(err)
    assert len(calls) == 1, "unknown errors must not retry"


# ─── Integration: astream() retry policy ─────────────────────────


def test_astream_retries_on_transient_when_nothing_yielded(
    monkeypatch, tmp_path, patch_killpg,
):
    """astream's retry-only-if-empty rule: first attempt fails with a
    transient before yielding anything; second attempt succeeds.
    Caller sees only the recovered output."""
    bad = FakeProc(
        pid=901, mode="fail",
        stdout=_stream_json_api_error("API Error: 503 service unavailable"),
        stderr=b"", returncode=1,
    )
    # Success path: a content_block_delta carrying "ok" + result event.
    good_stdout = (
        json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "ok"},
            },
        }).encode()
        + b"\n"
        + json.dumps({"type": "result", "result": "ok"}).encode()
        + b"\n"
    )
    good = FakeProc(pid=902, mode="ok", stdout=good_stdout)
    patch_killpg[bad.pid] = bad
    patch_killpg[good.pid] = good
    calls = _patch_spawn_sequence(monkeypatch, [bad, good])
    monkeypatch.setattr(brain_module, "_TRANSIENT_RETRY_DELAY_SECONDS", 0)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario():
        collected: list = []
        async for event in brain.astream("ping", chat_id=60):
            collected.append(event)
        return collected

    out = asyncio.run(scenario())
    assert "ok" in out, f"expected 'ok' in yielded events, got {out!r}"
    # The bad attempt yielded nothing, so the second attempt's "ok"
    # is the only thing the caller saw.
    assert out.count("ok") == 1
    assert len(calls) == 2


def test_astream_does_not_retry_after_partial_yield(
    monkeypatch, tmp_path, patch_killpg,
):
    """If a text delta has ALREADY been streamed and then the brain
    hits a transient, retrying would double-render the prefix. Policy
    is to propagate instead."""
    partial_then_fail_stdout = (
        # Yield "hello " first.
        json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello "},
            },
        }).encode()
        + b"\n"
        # Then the API blows up mid-stream.
        + json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "API Error: 500 blip"}],
            },
        }).encode()
        + b"\n"
    )
    bad = FakeProc(
        pid=1001, mode="fail", stdout=partial_then_fail_stdout,
        stderr=b"", returncode=1,
    )
    # Prime a second proc that should NEVER spawn.
    never = FakeProc(pid=1002, mode="ok", stdout=_stream_json_result("nope"))
    patch_killpg[bad.pid] = bad
    patch_killpg[never.pid] = never
    calls = _patch_spawn_sequence(monkeypatch, [bad, never])
    monkeypatch.setattr(brain_module, "_TRANSIENT_RETRY_DELAY_SECONDS", 0)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario():
        collected: list = []
        with pytest.raises(BrainTransientError):
            async for event in brain.astream("ping", chat_id=61):
                collected.append(event)
        return collected

    out = asyncio.run(scenario())
    # The user saw "hello " before the transient propagated — that's
    # the contract: once we've committed text to the bubble, we don't
    # retry on top of it.
    assert "hello " in out
    assert len(calls) == 1, "must not retry after a partial yield"


# ─── handler._shorten_brain_error ─────────────────────────────────


def test_shorten_brain_error_strips_exit_prefix():
    raw = (
        "claude -p exited 1: API Error: 500 Internal server error. "
        "Try again in a moment."
    )
    out = handler_module._shorten_brain_error(raw)
    assert out.startswith("API Error: 500")
    assert "exited 1" not in out


def test_shorten_brain_error_caps_length():
    raw = "claude -p exited 1: " + ("x" * 1000)
    out = handler_module._shorten_brain_error(raw)
    assert len(out) <= handler_module._BRAIN_ERROR_TAIL_LIMIT
    assert out.endswith("…")


def test_shorten_brain_error_empty():
    assert "no diagnostic" in handler_module._shorten_brain_error("")
    assert "no diagnostic" in handler_module._shorten_brain_error("   ")


def test_shorten_brain_error_passes_through_short_input():
    out = handler_module._shorten_brain_error("plain message")
    assert out == "plain message"


# ─── MessageHandler renders classified error to user ──────────────


def test_handler_stream_emits_brain_transient_code_and_real_message(tmp_path):
    """The handler's SSE stream surfaces transient errors with the
    ``brain_transient`` code AND the actual upstream wording in the
    message. The 15 May regression: the user got "Something broke";
    they should have gotten "Anthropic API hiccup — API Error: 500…"."""
    from collections.abc import AsyncIterator

    from vexis_agent.core.brain.null import BrainNull
    from vexis_agent.core.handler import MessageHandler
    from vexis_agent.core.sessions import SessionStore

    class ApiErrorBrain(BrainNull):
        async def astream(
            self, message, chat_id, *, model=None, reasoning_level=None,
        ) -> AsyncIterator:
            # Python recognises a function as an async generator only
            # if it contains ``yield``. Unreachable yield + raise is
            # the established pattern (see test_chat_stream.py).
            if False:
                yield ""  # type: ignore[unreachable]
            raise BrainTransientError(
                "claude -p exited 1: API Error: 500 Internal server error. "
                "Try again in a moment."
            )

    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = tmp_path / "sessions.json"
    sessions._active = "test"
    sessions._sessions = {
        "test": {
            "uuid": "00000000-0000-0000-0000-000000000000",
            "initialized": True,
            "created_at": "2026-05-09T00:00:00+00:00",
        },
    }
    h = MessageHandler(
        brain=ApiErrorBrain(responses=[]),
        sessions=sessions,
        allowed_user_id=12345,
    )

    async def run():
        out = []
        async for evt in h.stream(12345, 99, "hi"):
            out.append(evt)
        return out

    events = asyncio.run(run())
    err = [e for e in events if e[0] == "error"]
    assert len(err) == 1
    payload = err[0][1]
    assert payload["code"] == "brain_transient"
    # The user sees the actual upstream cause — not "Something broke."
    assert "API Error: 500" in payload["message"]
    # And the prefix tags it as upstream-side so the user knows
    # whether to wait or to dig in.
    assert "Upstream API hiccup" in payload["message"]


def test_handler_stream_emits_brain_permanent_code_for_auth_errors(tmp_path):
    """4xx-style permanent failures get a distinct code so the UI can
    suppress the retry button — retry won't help."""
    from collections.abc import AsyncIterator

    from vexis_agent.core.brain.null import BrainNull
    from vexis_agent.core.handler import MessageHandler
    from vexis_agent.core.sessions import SessionStore

    class AuthBrain(BrainNull):
        async def astream(
            self, message, chat_id, *, model=None, reasoning_level=None,
        ) -> AsyncIterator:
            if False:
                yield ""  # type: ignore[unreachable]
            raise BrainPermanentError(
                "claude -p exited 1: API Error: 401 Unauthorized — "
                "invalid_api_key"
            )

    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = tmp_path / "sessions.json"
    sessions._active = "test"
    sessions._sessions = {
        "test": {
            "uuid": "00000000-0000-0000-0000-000000000000",
            "initialized": True,
            "created_at": "2026-05-09T00:00:00+00:00",
        },
    }
    h = MessageHandler(
        brain=AuthBrain(responses=[]),
        sessions=sessions,
        allowed_user_id=12345,
    )

    async def run():
        out = []
        async for evt in h.stream(12345, 99, "hi"):
            out.append(evt)
        return out

    events = asyncio.run(run())
    err = [e for e in events if e[0] == "error"]
    assert len(err) == 1
    payload = err[0][1]
    assert payload["code"] == "brain_permanent"
    assert "401" in payload["message"]


def test_handler_handle_returns_specific_message_for_transient(tmp_path):
    """Non-streaming path (Telegram) — the return value carries the
    real upstream wording, not the generic ``_BRAIN_ERROR`` toast."""
    from vexis_agent.core.brain.null import BrainNull
    from vexis_agent.core.handler import MessageHandler
    from vexis_agent.core.sessions import SessionStore

    class ApiErrorBrain(BrainNull):
        async def respond(
            self, message, chat_id, *, model=None, reasoning_level=None,
        ) -> str:
            raise BrainTransientError(
                "claude -p exited 1: API Error: 503 Service Unavailable"
            )

    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = tmp_path / "sessions.json"
    sessions._active = "test"
    sessions._sessions = {
        "test": {
            "uuid": "00000000-0000-0000-0000-000000000000",
            "initialized": True,
            "created_at": "2026-05-09T00:00:00+00:00",
        },
    }
    h = MessageHandler(
        brain=ApiErrorBrain(responses=[]),
        sessions=sessions,
        allowed_user_id=12345,
    )

    reply = asyncio.run(h.handle(12345, 99, "hi"))
    assert reply is not None
    assert "API Error: 503" in reply
    assert "Upstream API hiccup" in reply
