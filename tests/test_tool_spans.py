# ruff: noqa: F811
# (pytest fixture parameters intentionally re-bind names imported from
# test_brain_cancel; ruff misreads this as unused-redefinition.)
"""Stream observability — tool spans + boundary text flush (issue #49).

Pins the two signals the streaming path carries so a slow turn is
diagnosable instead of one opaque block:

  1. Tool-span events on ``ClaudeCodeBrain.astream``: an enriched
     ``{"type":"tool", …, "id", "ts"}`` start and a new
     ``{"type":"tool_end", …, "duration_ms", "status"}`` end, paired by
     block id. Durations are monotonic (``duration_ms >= 0``); ``ts`` is
     epoch ms. A ``tool-span`` INFO log fires on BOTH the streaming
     (``astream``) and buffered (``respond`` / ``_read_stream_events``)
     paths.
  2. Boundary text flush: inter-tool text the model batches (delivered
     as an ``assistant`` text block with no token deltas) is streamed at
     the tool boundary / at end-of-stream via prefix-match dedup — never
     duplicated, and never on the failure path (an API-error block must
     reach the handler only via the raised exception).

Canned stream-json mirrors the captured claude CLI 2.1.x sample:
per-block ``assistant`` events (thinking / text / tool_use each in their
own event), deltas preceding their block's ``assistant`` event, and
``tool_result`` blocks arriving inside ``user`` events. Subprocess is
the FakeProc / monkeypatched ``create_subprocess_exec`` machinery from
test_brain_cancel.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core.brain import claude_code as brain_module
from vexis_agent.core.brain.claude_code import (
    BrainTransientError,
    _ToolSpanTracker,
    _unstreamed_remainder,
)
from vexis_agent.core.brain.null import BrainNull
from vexis_agent.core.handler import MessageHandler
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore
from vexis_agent.core.web_server import DashboardConfig, WebDashboard
from vexis_agent.transports.web import WebChatTransport

# Reuse the subprocess-fake fixtures/helpers from the cancel suite —
# same machinery, same semantics. patch_killpg / patch_runtime_dir are
# pytest fixtures consumed implicitly by the test signatures below.
from tests.test_brain_cancel import (  # noqa: F401
    FakeProc,
    FakeSession,
    _build_brain,
    _patch_spawn,
    patch_killpg,
    patch_runtime_dir,
)


_TOKEN = "test-token-spans-cafef00d"
_ALLOWED_USER_ID = 12345


# ── stream-json line builders (per-block assistant events) ───────────


def _line(obj: dict) -> bytes:
    return json.dumps(obj).encode() + b"\n"


def _sys_init() -> bytes:
    return _line({"type": "system", "subtype": "init", "session_id": "s1"})


def _delta(text: str, parent: str | None = None) -> bytes:
    return _line(
        {
            "type": "stream_event",
            "parent_tool_use_id": parent,
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": text},
            },
        }
    )


def _assistant_text(text: str, parent: str | None = None) -> bytes:
    return _line(
        {
            "type": "assistant",
            "parent_tool_use_id": parent,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        }
    )


def _assistant_tool(
    name: str, tool_id: str, tool_input: dict, parent: str | None = None,
) -> bytes:
    return _line(
        {
            "type": "assistant",
            "parent_tool_use_id": parent,
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": name,
                        "input": tool_input,
                    }
                ],
            },
        }
    )


def _tool_result(
    tool_id: str,
    content: str = "ok",
    is_error: bool | None = None,
    parent: str | None = None,
) -> bytes:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": content,
    }
    if is_error is not None:
        block["is_error"] = is_error
    return _line(
        {
            "type": "user",
            "parent_tool_use_id": parent,
            "message": {"role": "user", "content": [block]},
        }
    )


def _result(text: str, **extra) -> bytes:
    return _line({
        "type": "result",
        "subtype": "success",
        "result": text,
        **extra,
    })


# ── astream drivers ──────────────────────────────────────────────────


def _run_astream(brain: Any, chat_id: int = 1) -> list:
    async def scenario() -> list:
        out: list = []
        async for evt in brain.astream("hi", chat_id=chat_id):
            out.append(evt)
        return out

    return asyncio.run(scenario())


def _texts(events: list) -> list[str]:
    return [e for e in events if isinstance(e, str)]


def _dicts(events: list) -> list[dict]:
    return [e for e in events if isinstance(e, dict)]


def _patch_spawn_sequence(monkeypatch, procs: list[FakeProc]) -> list[int]:
    """Hand out ``procs`` in order to successive spawns (for retry cases)."""
    calls: list[int] = []

    async def _fake_spawn(*_argv, **_kwargs) -> FakeProc:
        idx = len(calls)
        calls.append(idx)
        if idx >= len(procs):
            raise RuntimeError(f"spawn #{idx} but only {len(procs)} procs primed")
        return procs[idx]

    monkeypatch.setattr(
        brain_module.asyncio, "create_subprocess_exec", _fake_spawn
    )
    return calls


# ── unit: prefix-dedup + tracker ─────────────────────────────────────


def test_unstreamed_remainder_batched_block_returns_whole():
    # No deltas streamed → the whole block is the remainder.
    assert _unstreamed_remainder("Checking now.", "") == ("Checking now.", False)


def test_unstreamed_remainder_prefix_returns_suffix():
    assert _unstreamed_remainder("Checking now.", "Checking") == (" now.", False)


def test_unstreamed_remainder_fully_streamed_returns_empty():
    assert _unstreamed_remainder("Checking", "Checking") == ("", False)
    # Segment ran slightly ahead of the reconciled block — still empty.
    assert _unstreamed_remainder("Check", "Checking") == ("", False)


def test_unstreamed_remainder_genuine_mismatch_flags():
    assert _unstreamed_remainder("hello", "world") == ("", True)


def test_tool_span_tracker_unmatched_end_returns_none():
    tracker = _ToolSpanTracker(chat_id=1)
    assert tracker.end("toolu_never_started", is_error=False) is None


def test_tool_span_tracker_pairs_and_reports_status():
    tracker = _ToolSpanTracker(chat_id=1)
    tracker.start("toolu_1", "Read", "sample.txt")
    span = tracker.end("toolu_1", is_error=False)
    assert span is not None
    assert span["type"] == "tool_end"
    assert span["name"] == "Read"
    assert span["target"] == "sample.txt"
    assert span["id"] == "toolu_1"
    assert span["status"] == "completed"
    assert isinstance(span["duration_ms"], int) and span["duration_ms"] >= 0
    assert isinstance(span["ts"], int) and span["ts"] > 0
    # Popped: a second end for the same id no longer matches.
    assert tracker.end("toolu_1", is_error=False) is None


# ── case 1: streamed-deltas turn with one tool ───────────────────────


def test_astream_streamed_deltas_tool_span(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir, caplog,
):
    """Deltas cover both text blocks fully; the tool start carries id +
    int ts, a tool_end follows with duration_ms >= 0 and
    status=completed, text is NOT duplicated, and the concatenation is
    the two blocks. The ``tool-span`` INFO log fires on this
    (streaming) path too."""
    stdout = b"".join(
        [
            _sys_init(),
            _delta("Checking the"),
            _delta(" file now."),
            _assistant_text("Checking the file now."),
            _assistant_tool("Read", "toolu_1", {"file_path": "sample.txt"}),
            _tool_result("toolu_1"),
            _delta("The magic word is aubergine."),
            _assistant_text("The magic word is aubergine."),
            _result("The magic word is aubergine."),
        ]
    )
    proc = FakeProc(pid=910, mode="ok", stdout=stdout)
    patch_killpg[910] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)
    with caplog.at_level(logging.INFO, logger=brain_module.__name__):
        events = _run_astream(brain, chat_id=910)

    dicts = _dicts(events)
    # Issue #56: the terminal ``final`` event trails the tool spans.
    assert [d["type"] for d in dicts] == ["tool", "tool_end", "final"]
    start, end, final = dicts
    assert start["id"] == "toolu_1"
    assert isinstance(start["ts"], int) and start["ts"] > 0
    assert start["name"] == "Read"
    assert start["target"] == "sample.txt"
    assert end["id"] == "toolu_1"
    assert end["status"] == "completed"
    assert isinstance(end["duration_ms"], int) and end["duration_ms"] >= 0
    # The canonical reply is the ``result`` text only.
    assert final == {"type": "final", "text": "The magic word is aubergine."}

    # Text streamed once each — no duplication from the reconciliation.
    assert "".join(_texts(events)) == (
        "Checking the file now.The magic word is aubergine."
    )
    assert "Checking the file now." not in _texts(events)  # never a single chunk

    assert any(
        "tool-span" in r.message and "tool=Read" in r.message
        for r in caplog.records
    )


def test_astream_emits_normalized_claude_usage(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    stdout = b"".join([
        _sys_init(),
        _delta("answer"),
        _assistant_text("answer"),
        _result(
            "answer",
            usage={
                "input_tokens": 100,
                "cache_read_input_tokens": 60,
                "cache_creation_input_tokens": 5,
                "output_tokens": 20,
            },
            total_cost_usd=0.003,
        ),
    ])
    proc = FakeProc(pid=911, mode="ok", stdout=stdout)
    patch_killpg[911] = proc
    _patch_spawn(monkeypatch, proc)
    brain = _build_brain(RunningTasks(), tmp_path)

    usage = next(
        event
        for event in _run_astream(brain, chat_id=911)
        if isinstance(event, dict) and event.get("type") == "usage"
    )
    assert usage == {
        "type": "usage",
        "input_tokens": 100,
        "cache_read_tokens": 60,
        "cache_write_tokens": 5,
        "output_tokens": 20,
        "reasoning_tokens": 0,
        "total_tokens": 185,
        "reported_cost_usd_micros": 3000,
    }


# ── case 2: batched turn (no deltas) → boundary + end flush ──────────


def test_astream_batched_text_flushes_at_boundary_and_end(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """No token deltas at all. The inter-tool text is yielded as a chunk
    BEFORE the tool dict; the final text is yielded at end-of-stream;
    the result-event fallback does not double-yield; concatenation is
    both texts."""
    stdout = b"".join(
        [
            _sys_init(),
            _assistant_text("Checking the file now."),
            _assistant_tool("Read", "toolu_1", {"file_path": "sample.txt"}),
            _tool_result("toolu_1"),
            _assistant_text("The magic word is aubergine."),
            _result("The magic word is aubergine."),
        ]
    )
    proc = FakeProc(pid=920, mode="ok", stdout=stdout)
    patch_killpg[920] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)
    events = _run_astream(brain, chat_id=920)

    # inter-tool text lands before the tool start.
    tool_idx = next(
        i for i, e in enumerate(events)
        if isinstance(e, dict) and e["type"] == "tool"
    )
    pre_tool_text = [e for e in events[:tool_idx] if isinstance(e, str)]
    assert pre_tool_text == ["Checking the file now."]

    assert _texts(events) == [
        "Checking the file now.",
        "The magic word is aubergine.",
    ]
    # Final text appears exactly once — fallback did not double-yield.
    assert _texts(events).count("The magic word is aubergine.") == 1
    assert "".join(_texts(events)) == (
        "Checking the file now.The magic word is aubergine."
    )
    # Issue #56: the stream ends with the canonical ``result`` text.
    assert _dicts(events)[-1] == {
        "type": "final", "text": "The magic word is aubergine.",
    }


# ── case 3: partial streaming → only the suffix is flushed ───────────


def test_astream_partial_deltas_flush_suffix_only(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """Deltas cover a prefix of the text block; only the un-streamed
    suffix is flushed at the tool boundary — the streamed prefix is not
    re-emitted."""
    stdout = b"".join(
        [
            _sys_init(),
            _delta("Checking the"),
            _assistant_text("Checking the file now."),
            _assistant_tool("Read", "toolu_1", {"file_path": "sample.txt"}),
            _tool_result("toolu_1"),
            _result("done"),
        ]
    )
    proc = FakeProc(pid=930, mode="ok", stdout=stdout)
    patch_killpg[930] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)
    events = _run_astream(brain, chat_id=930)

    assert _texts(events) == ["Checking the", " file now."]
    assert "".join(_texts(events)) == "Checking the file now."
    # The whole block was never emitted as one chunk (no duplication).
    assert "Checking the file now." not in _texts(events)
    # Issue #56: canonical ``result`` text trails the tool spans.
    assert _dicts(events)[-1] == {"type": "final", "text": "done"}


# ── case 3b: multi-block assistant event — no re-flush ───────────────


def _assistant_texts(*texts: str) -> bytes:
    """One assistant event carrying SEVERAL text blocks — the batched
    old-CLI shape (the pinned 2.1.x CLI emits one block per event)."""
    return _line(
        {
            "type": "assistant",
            "parent_tool_use_id": None,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": t} for t in texts],
            },
        }
    )


def test_astream_multiblock_event_fully_streamed_not_reflushed(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """Deltas covered BOTH text blocks of one batched assistant event:
    reconciliation must consume the matched prefix per block rather than
    resetting after the first, or the second block looks un-streamed and
    gets duplicated into the reply."""
    stdout = b"".join(
        [
            _sys_init(),
            _delta("Hello "),
            _delta("World"),
            _assistant_texts("Hello ", "World"),
            _result("Hello World"),
        ]
    )
    proc = FakeProc(pid=931, mode="ok", stdout=stdout)
    patch_killpg[931] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)
    events = _run_astream(brain, chat_id=931)

    assert _texts(events) == ["Hello ", "World"]
    assert "".join(_texts(events)) == "Hello World"
    # Issue #56: no tools this turn, so the only dict is the final event.
    assert _dicts(events) == [{"type": "final", "text": "Hello World"}]


# ── case 3c: batched block then streamed block — order preserved ─────


def test_astream_batched_then_streamed_preserves_order(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """A batched text block (no deltas) followed by a token-streamed
    block with NO tool call between them: the buffered remainder must
    flush BEFORE the first new delta, or the reply comes out scrambled
    ('Beta.Alpha. ') in both the live stream and the ``done``
    concatenation."""
    stdout = b"".join(
        [
            _sys_init(),
            _assistant_text("Alpha. "),
            _delta("Beta."),
            _assistant_text("Beta."),
            _result("Alpha. Beta."),
        ]
    )
    proc = FakeProc(pid=932, mode="ok", stdout=stdout)
    patch_killpg[932] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)
    events = _run_astream(brain, chat_id=932)

    assert _texts(events) == ["Alpha. ", "Beta."]
    assert "".join(_texts(events)) == "Alpha. Beta."
    # Issue #56: canonical ``result`` text is the sole trailing dict.
    assert _dicts(events) == [{"type": "final", "text": "Alpha. Beta."}]


def test_astream_multiblock_event_flushes_only_unstreamed_tail(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """Deltas covered block one and a prefix of block two of the same
    batched assistant event: only block two's un-streamed suffix is
    flushed (at end-of-stream — no tool boundary here)."""
    stdout = b"".join(
        [
            _sys_init(),
            _delta("Hello "),
            _delta("Wo"),
            _assistant_texts("Hello ", "World"),
            _result("Hello World"),
        ]
    )
    proc = FakeProc(pid=932, mode="ok", stdout=stdout)
    patch_killpg[932] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)
    events = _run_astream(brain, chat_id=932)

    assert _texts(events) == ["Hello ", "Wo", "rld"]
    assert "".join(_texts(events)) == "Hello World"
    # Issue #56: canonical ``result`` text is the sole trailing dict.
    assert _dicts(events) == [{"type": "final", "text": "Hello World"}]


# ── case 4: failure turn — pending_tail never escapes ────────────────


def test_astream_failure_does_not_flush_error_text(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """The May-2026 shape: an API-error assistant text block, no
    tool_use, exit 1, empty stderr. No text chunk may be yielded — the
    error reaches the caller ONLY via the raised BrainTransientError."""
    err_text = (
        "API Error: 500 Internal server error. This is a server-side "
        "issue, usually temporary — try again in a moment."
    )
    api_error_stdout = b"".join([_sys_init(), _assistant_text(err_text)])
    procs = [
        FakeProc(
            pid=940 + i, mode="fail", stdout=api_error_stdout,
            stderr=b"", returncode=1,
        )
        for i in range(2)  # initial + one retry, both fail
    ]
    for p in procs:
        patch_killpg[p.pid] = p
    _patch_spawn_sequence(monkeypatch, procs)
    monkeypatch.setattr(brain_module, "_TRANSIENT_RETRY_DELAY_SECONDS", 0)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> list:
        collected: list = []
        with pytest.raises(BrainTransientError) as ei:
            async for evt in brain.astream("hi", chat_id=940):
                collected.append(evt)
        assert "API Error: 500" in str(ei.value)
        return collected

    collected = asyncio.run(scenario())
    # Nothing streamed — the error text stayed in the discarded buffer.
    assert _texts(collected) == []
    # Issue #56: no ``final`` event on the failure path — the canonical
    # reply is emitted only on the success path, so a poisoned/error
    # transcript can never surface a canonical reply.
    assert _dicts(collected) == [{"type": "retry", "attempt": 2}]


# ── case 5: is_error → tool_end.status == "error" ────────────────────


def test_astream_tool_result_error_marks_status(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    stdout = b"".join(
        [
            _sys_init(),
            _assistant_tool("Bash", "toolu_1", {"command": "false"}),
            _tool_result("toolu_1", content="boom", is_error=True),
            _result("that failed"),
        ]
    )
    proc = FakeProc(pid=950, mode="ok", stdout=stdout)
    patch_killpg[950] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)
    events = _run_astream(brain, chat_id=950)

    ends = [d for d in _dicts(events) if d["type"] == "tool_end"]
    assert len(ends) == 1
    assert ends[0]["status"] == "error"
    assert ends[0]["name"] == "Bash"
    # Issue #56: even after a failed tool, the turn's ``result`` text is
    # the canonical reply and trails as the final event.
    assert _dicts(events)[-1] == {"type": "final", "text": "that failed"}


# ── case 6: sidechain text is never flushed into the stream ──────────


def test_astream_sidechain_text_never_flushed(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """A subagent (Task) sidechain: its batched assistant text
    (parent_tool_use_id set) must never enter the reply chunk stream,
    while the main thread's own batched text still flushes."""
    stdout = b"".join(
        [
            _sys_init(),
            _assistant_tool("Task", "toolu_task", {"description": "research"}),
            _assistant_text("subagent internal note", parent="toolu_task"),
            _tool_result("toolu_task"),
            _assistant_text("Here is the answer."),
            _result("Here is the answer."),
        ]
    )
    proc = FakeProc(pid=960, mode="ok", stdout=stdout)
    patch_killpg[960] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)
    events = _run_astream(brain, chat_id=960)

    assert "subagent internal note" not in _texts(events)
    assert _texts(events) == ["Here is the answer."]
    # The sidechain Task tool still produced a paired span (observability
    # without leaking its text); issue #56's final event trails them, and
    # its canonical text is the main-thread answer, never the sidechain
    # note.
    assert [d["type"] for d in _dicts(events)] == ["tool", "tool_end", "final"]
    assert _dicts(events)[-1] == {"type": "final", "text": "Here is the answer."}


# ── case 7: buffered path (respond) logs the span line ───────────────


def test_respond_logs_tool_span(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir, caplog,
):
    """The buffered path emits no events but must still log the
    ``tool-span`` INFO line (with tool name + duration) so a slow
    Telegram/aux turn is attributable from logs alone."""
    stdout = b"".join(
        [
            _sys_init(),
            _assistant_tool("Read", "toolu_1", {"file_path": "sample.txt"}),
            _tool_result("toolu_1"),
            _result("done"),
        ]
    )
    proc = FakeProc(pid=970, mode="ok", stdout=stdout)
    patch_killpg[970] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> str:
        return await brain.respond("ping", chat_id=770)

    with caplog.at_level(logging.INFO, logger=brain_module.__name__):
        reply = asyncio.run(scenario())

    assert reply == "done"
    span_lines = [r.message for r in caplog.records if "tool-span" in r.message]
    assert len(span_lines) == 1
    line = span_lines[0]
    assert "tool=Read" in line
    assert "target=sample.txt" in line
    assert "duration_ms=" in line
    assert "status=completed" in line
    assert "chat=770" in line
    # target is free text (Bash targets carry spaces and ``=``) so it
    # must be the LAST field — every fixed-vocabulary key a log parser
    # matches on has to precede it.
    assert line.index("status=") < line.index("target=")
    assert line.index("duration_ms=") < line.index("target=")


# ── SSE + handler wiring (fakes, no subprocess) ──────────────────────


def _make_sessions(tmp_path: Path) -> SessionStore:
    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = tmp_path / "sessions.json"  # type: ignore[attr-defined]
    sessions._active = "test"  # type: ignore[attr-defined]
    sessions._sessions = {  # type: ignore[attr-defined]
        "test": {
            "uuid": "00000000-0000-0000-0000-000000000000",
            "initialized": True,
            "created_at": "2026-05-09T00:00:00+00:00",
        },
    }
    return sessions


def _make_client(handler: MessageHandler, tmp_path: Path) -> TestClient:
    chat_obj = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = tmp_path  # type: ignore[attr-defined]
    dashboard._token = _TOKEN  # type: ignore[attr-defined]
    dashboard._learning = None  # type: ignore[attr-defined]
    dashboard._chat = chat_obj  # type: ignore[attr-defined]
    dashboard._relationships_mutation_window_seconds = 600  # type: ignore[attr-defined]
    dashboard._relationships_mutation_limit = 100  # type: ignore[attr-defined]
    dashboard._relationships_mutation_log = defaultdict(deque)  # type: ignore[attr-defined]
    dashboard._config = DashboardConfig(  # type: ignore[attr-defined]
        host="127.0.0.1", port=0,
        web_dist=tmp_path / "no-frontend",
        manage_tailscale=False,
    )
    for k in (
        "_sessions", "_running_tasks", "_background_tasks", "_curator",
        "_browser", "_addon_runtime", "_started_at", "_tailscale_url",
        "_tailscale_dns", "_server", "_serve_task", "_profile_size_cache",
        "_running_brain_kind",
    ):
        setattr(dashboard, k, None)
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    return TestClient(dashboard._app)


def _parse_sse(text: str) -> list[dict]:
    out: list[dict] = []
    for frame in text.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data: "):
                try:
                    out.append(json.loads(line[len("data: "):]))
                except json.JSONDecodeError:
                    pass
    return out


# ── case 8: SSE route serialises tool + tool_end verbatim ────────────


def test_stream_route_emits_tool_end_frame(tmp_path: Path) -> None:
    """A brain yielding enriched ``tool`` + ``tool_end`` dicts produces
    well-formed ``data: {…}`` frames whose fields survive verbatim — the
    route forwards new dict shapes with no code change."""

    class SpanBrain(BrainNull):
        async def astream(
            self, message: str, chat_id: int, *,
            model=None, reasoning_level=None,
        ) -> AsyncIterator[str | dict]:
            yield {
                "type": "tool", "name": "Read", "target": "/etc/hostname",
                "id": "toolu_9", "ts": 1751000000000,
            }
            yield {
                "type": "tool_end", "name": "Read", "target": "/etc/hostname",
                "id": "toolu_9", "ts": 1751000000142, "duration_ms": 142,
                "status": "completed",
            }
            yield "hello"

    handler = MessageHandler(
        brain=SpanBrain(responses=[]), sessions=_make_sessions(tmp_path),
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )
    client = _make_client(handler, tmp_path)

    r = client.post(
        "/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        json={"text": "hi"},
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events == [
        {
            "type": "tool", "name": "Read", "target": "/etc/hostname",
            "id": "toolu_9", "ts": 1751000000000,
        },
        {
            "type": "tool_end", "name": "Read", "target": "/etc/hostname",
            "id": "toolu_9", "ts": 1751000000142, "duration_ms": 142,
            "status": "completed",
        },
        {"type": "chunk", "text": "hello"},
        {"type": "done", "reply": "hello"},
    ]


# ── case 9: handler keeps dict events out of ``full`` / done ──────────


def test_handler_stream_tool_end_not_in_done(tmp_path: Path) -> None:
    """``tool`` and ``tool_end`` dicts are forwarded as ``("tool", …)``
    and must NOT contribute to the ``("done", …)`` reply payload."""

    class SpanBrain(BrainNull):
        async def astream(
            self, message: str, chat_id: int, *,
            model=None, reasoning_level=None,
        ) -> AsyncIterator[str | dict]:
            yield {"type": "tool", "name": "Read", "target": "f.py", "id": "t1",
                   "ts": 1}
            yield "found it. "
            yield {"type": "tool_end", "name": "Read", "target": "f.py",
                   "id": "t1", "ts": 2, "duration_ms": 1, "status": "completed"}
            yield "Done."

    handler = MessageHandler(
        brain=SpanBrain(responses=[]), sessions=_make_sessions(tmp_path),
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )

    async def run() -> list:
        out: list = []
        async for evt in handler.stream(_ALLOWED_USER_ID, 1, "x"):
            out.append(evt)
        return out

    events = asyncio.run(run())
    assert events == [
        ("tool", {"type": "tool", "name": "Read", "target": "f.py", "id": "t1",
                  "ts": 1}),
        ("chunk", "found it. "),
        ("tool", {"type": "tool_end", "name": "Read", "target": "f.py",
                  "id": "t1", "ts": 2, "duration_ms": 1, "status": "completed"}),
        ("chunk", "Done."),
        ("done", "found it. Done."),
    ]


# ── case 10: issue #56 — scratch note streams but never persists ─────

_SCRATCH = "No Salto price for that OE. Now finalize the answer."
_ANSWER = "The Salto price is not listed for that OE."


@pytest.mark.parametrize("answer_as_deltas", [True, False])
def test_astream_scratch_note_streams_but_excluded_from_final(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir, answer_as_deltas,
):
    """The verified PartPilot leak shape (issue #56): after the last tool
    the model batches a final-segment working note ("… Now finalize the
    answer.") as a buffered block with no deltas, then delivers the real
    answer. The ``result`` event carries the ANSWER ONLY.

    Contract: the scratch note still STREAMS as a live chunk (progress
    observability, unchanged), but the terminal ``final`` event carries
    the answer only — so a consumer that prefers ``final`` persists the
    answer without the note. Parametrised over the two answer-delivery
    shapes seen in the wild: token deltas AND a fully-batched block."""
    lines = [
        _sys_init(),
        _assistant_text("Let me check the catalog."),
        _assistant_tool("Bash", "toolu_1", {"command": "grep Salto db"}),
        _tool_result("toolu_1"),
        # Final-segment scratch note — batched block, no deltas.
        _assistant_text(_SCRATCH),
    ]
    if answer_as_deltas:
        lines += [
            _delta("The Salto price "),
            _delta("is not listed for that OE."),
        ]
    lines.append(_assistant_text(_ANSWER))
    lines.append(_result(_ANSWER))
    proc = FakeProc(pid=980, mode="ok", stdout=b"".join(lines))
    patch_killpg[980] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)
    events = _run_astream(brain, chat_id=980)

    # Live-progress contract: the scratch note reaches the chunk stream
    # (either as its own chunk in the deltas variant, or folded into the
    # end-of-stream batched flush).
    assert any(_SCRATCH in chunk for chunk in _texts(events))
    # Canonical reply excludes the scratch note entirely.
    final = _dicts(events)[-1]
    assert final == {"type": "final", "text": _ANSWER}
    assert _SCRATCH not in final["text"]


def test_handler_stream_final_event_is_canonical_done(tmp_path: Path) -> None:
    """Issue #56 at the handler seam: a brain that streams a mid-turn
    scratch note then the answer and closes with a ``final`` event →
    ``done`` carries the answer ONLY (scratch absent), while the scratch
    still streamed as a ``chunk``. The ``final`` dict is consumed by the
    handler, never forwarded under the ``tool`` tag."""

    class NarratingBrain(BrainNull):
        async def astream(
            self, message: str, chat_id: int, *,
            model=None, reasoning_level=None,
        ) -> AsyncIterator[str | dict]:
            yield _SCRATCH  # live progress — user watches it stream
            yield _ANSWER
            yield {"type": "final", "text": _ANSWER}

    handler = MessageHandler(
        brain=NarratingBrain(responses=[]), sessions=_make_sessions(tmp_path),
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )

    async def run() -> list:
        out: list = []
        async for evt in handler.stream(_ALLOWED_USER_ID, 1, "x"):
            out.append(evt)
        return out

    events = asyncio.run(run())
    # The scratch note streamed as a chunk (progress preserved)…
    assert ("chunk", _SCRATCH) in events
    # …the ``final`` event was consumed, not forwarded as a tool…
    assert all(tag != "tool" for tag, _ in events)
    # …and ``done`` is the canonical answer only.
    done = [payload for tag, payload in events if tag == "done"]
    assert done == [_ANSWER]
    assert _SCRATCH not in done[0]


def test_handler_stream_without_final_falls_back_to_chunks(
    tmp_path: Path,
) -> None:
    """Back-compat (issue #56): a brain that never emits a ``final``
    event (older / third-party brains) → ``done`` is the concatenated
    chunk text, exactly as before the canonical-reply seam."""

    class NoFinalBrain(BrainNull):
        async def astream(
            self, message: str, chat_id: int, *,
            model=None, reasoning_level=None,
        ) -> AsyncIterator[str | dict]:
            for piece in ["hel", "lo ", "world"]:
                yield piece

    handler = MessageHandler(
        brain=NoFinalBrain(responses=[]), sessions=_make_sessions(tmp_path),
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )

    async def run() -> list:
        out: list = []
        async for evt in handler.stream(_ALLOWED_USER_ID, 1, "x"):
            out.append(evt)
        return out

    events = asyncio.run(run())
    done = [payload for tag, payload in events if tag == "done"]
    assert done == ["hello world"]
