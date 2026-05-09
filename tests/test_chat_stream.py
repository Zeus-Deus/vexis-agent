"""Tests for the SSE streaming chat path.

Covers:
  - Brain.astream default fallback (yields buffered respond once)
  - BrainNull native streaming (when responses are pre-loaded)
  - MessageHandler.stream sentinel-tagged generator (chunk/done/error)
  - WebChatTransport.stream forwards
  - POST /api/v1/chat/stream emits well-formed SSE frames
  - Auth gating
  - Model + reasoning override flow through to the brain
  - /chat/send (non-streaming) NEVER invokes astream — protects the
    Telegram path from accidentally streaming
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core.brain.null import BrainNull
from vexis_agent.core.handler import MessageHandler
from vexis_agent.core.sessions import SessionStore
from vexis_agent.core.web_server import DashboardConfig, WebDashboard
from vexis_agent.transports.web import WebChatTransport, _truncate_preview


_TOKEN = "test-token-stream-cafef00d"
_ALLOWED_USER_ID = 12345


# ──────────────────────────────────────────────────────────────────
# Fixtures (mirror the isolation test pattern)
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def brain() -> BrainNull:
    """Pre-loaded with one canned reply so a single ``respond`` /
    ``astream`` turn succeeds. Tests that need more refresh the
    queue per turn."""
    return BrainNull(responses=["streamed reply text"])


@pytest.fixture
def handler(brain: BrainNull, tmp_path: Path) -> MessageHandler:
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
    return MessageHandler(
        brain=brain,
        sessions=sessions,
        allowed_user_id=_ALLOWED_USER_ID,
        notifier=None,
    )


@pytest.fixture
def chat(handler: MessageHandler) -> WebChatTransport:
    return WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)


@pytest.fixture
def client(chat: WebChatTransport, tmp_path: Path) -> TestClient:
    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = tmp_path  # type: ignore[attr-defined]
    dashboard._token = _TOKEN  # type: ignore[attr-defined]
    dashboard._learning = None  # type: ignore[attr-defined]
    dashboard._chat = chat  # type: ignore[attr-defined]
    dashboard._relationships_mutation_window_seconds = 600  # type: ignore[attr-defined]
    dashboard._relationships_mutation_limit = 100  # type: ignore[attr-defined]
    dashboard._relationships_mutation_log = defaultdict(deque)  # type: ignore[attr-defined]
    dashboard._config = DashboardConfig(  # type: ignore[attr-defined]
        host="127.0.0.1", port=0,
        web_dist=tmp_path / "no-frontend",
        manage_tailscale=False,
    )
    dashboard._sessions = None  # type: ignore[attr-defined]
    dashboard._running_tasks = None  # type: ignore[attr-defined]
    dashboard._background_tasks = None  # type: ignore[attr-defined]
    dashboard._curator = None  # type: ignore[attr-defined]
    dashboard._browser = None  # type: ignore[attr-defined]
    dashboard._started_at = None  # type: ignore[attr-defined]
    dashboard._tailscale_url = None  # type: ignore[attr-defined]
    dashboard._tailscale_dns = None  # type: ignore[attr-defined]
    dashboard._server = None  # type: ignore[attr-defined]
    dashboard._serve_task = None  # type: ignore[attr-defined]
    dashboard._profile_size_cache = None  # type: ignore[attr-defined]
    dashboard._running_brain_kind = None  # type: ignore[attr-defined]
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    return TestClient(dashboard._app)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN}"}


def _parse_sse(text: str) -> list[dict]:
    """Pull the JSON payload out of every ``data: `` frame."""
    out: list[dict] = []
    for frame in text.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data: "):
                try:
                    out.append(json.loads(line[len("data: "):]))
                except json.JSONDecodeError:
                    pass
    return out


# ──────────────────────────────────────────────────────────────────
# Brain ABC: default astream falls back to respond()
# ──────────────────────────────────────────────────────────────────


def test_brain_null_astream_falls_back_to_respond(brain: BrainNull) -> None:
    """BrainNull doesn't override astream — the ABC's default
    implementation should fire respond and yield once. Verifies the
    fallback path that opencode + null both rely on."""
    chunks: list[str] = []

    async def run() -> None:
        async for chunk in brain.astream("hello", chat_id=1):
            chunks.append(chunk)

    asyncio.run(run())
    assert chunks == ["streamed reply text"]
    # Same call recorder as respond — confirms astream went through
    # the same code path (respond → recorded).
    assert brain.calls() == [("hello", 1, None, None)]


def test_brain_astream_forwards_overrides(brain: BrainNull) -> None:
    """model + reasoning_level override flow through the default
    astream → respond bridge. Same per-turn isolation contract."""

    async def run() -> None:
        async for _ in brain.astream(
            "hi", chat_id=1,
            model="claude-haiku-4-5", reasoning_level="high",
        ):
            pass

    asyncio.run(run())
    assert brain.calls() == [
        ("hi", 1, "claude-haiku-4-5", "high"),
    ]


# ──────────────────────────────────────────────────────────────────
# MessageHandler.stream — sentinel-tagged generator
# ──────────────────────────────────────────────────────────────────


def test_handler_stream_emits_chunks_then_done(
    handler: MessageHandler, brain: BrainNull,
) -> None:
    """Stream contract: zero-or-more ('chunk', text) events,
    followed by exactly one ('done', full_reply). BrainNull's
    default fallback yields once, so we get one chunk + one done."""

    async def run() -> list[tuple[str, str | None]]:
        out: list = []
        async for evt in handler.stream(_ALLOWED_USER_ID, 1, "hi"):
            out.append(evt)
        return out

    events = asyncio.run(run())
    assert events == [
        ("chunk", "streamed reply text"),
        ("done", "streamed reply text"),
    ]


def test_handler_stream_done_carries_full_concatenated_reply(
    tmp_path: Path,
) -> None:
    """When the brain yields multiple chunks, ``done`` payload is
    the concatenation. UI uses the canonical ``done`` text rather
    than concatenating client-side to avoid stream-parse drift."""

    class ChunkyBrain(BrainNull):
        async def astream(
            self, message: str, chat_id: int, *,
            model=None, reasoning_level=None,
        ) -> AsyncIterator[str]:
            for piece in ["hel", "lo ", "world"]:
                yield piece

    brain = ChunkyBrain(responses=[])
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
    handler = MessageHandler(
        brain=brain, sessions=sessions,
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )

    async def run() -> list:
        out: list = []
        async for evt in handler.stream(_ALLOWED_USER_ID, 1, "ignored"):
            out.append(evt)
        return out

    events = asyncio.run(run())
    chunks = [e[1] for e in events if e[0] == "chunk"]
    dones = [e[1] for e in events if e[0] == "done"]
    assert chunks == ["hel", "lo ", "world"]
    assert dones == ["hello world"]


def test_handler_stream_forwards_tool_events(tmp_path: Path) -> None:
    """Brain.astream yields a discriminated union — str (text delta)
    or dict (tool-use event). MessageHandler.stream must forward
    dict events as ``("tool", payload)`` so the SSE route can emit
    them, without mixing them into the final text reply.

    Pin: tool dicts must NOT contribute to the ``done`` payload
    (would corrupt the assistant's transcript copy)."""

    class ToolyBrain(BrainNull):
        async def astream(
            self, message: str, chat_id: int, *,
            model=None, reasoning_level=None,
        ) -> AsyncIterator[str | dict]:
            yield "Looking… "
            yield {"type": "tool", "name": "Read", "target": "src/foo.py"}
            yield "found it. "
            yield {"type": "tool", "name": "Bash", "target": "git status"}
            yield "Done."

    brain = ToolyBrain(responses=[])
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
    handler = MessageHandler(
        brain=brain, sessions=sessions,
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )

    async def run() -> list:
        out: list = []
        async for evt in handler.stream(_ALLOWED_USER_ID, 1, "x"):
            out.append(evt)
        return out

    events = asyncio.run(run())
    # Order matters: text + tool events interleave as the brain
    # emits them, then a single ``done`` at the end.
    assert events == [
        ("chunk", "Looking… "),
        ("tool", {"type": "tool", "name": "Read", "target": "src/foo.py"}),
        ("chunk", "found it. "),
        ("tool", {"type": "tool", "name": "Bash", "target": "git status"}),
        ("chunk", "Done."),
        ("done", "Looking… found it. Done."),
    ]


def test_stream_route_emits_tool_frames(
    tmp_path: Path, brain: BrainNull,
) -> None:
    """SSE wire format: tool events become ``data: {"type":"tool",...}``
    frames, distinct from chunk/done/error. Pin the wire shape so
    the frontend's parser doesn't need to be rewritten when a new
    tool is added on the brain side."""

    class ToolyBrain(BrainNull):
        async def astream(
            self, message: str, chat_id: int, *,
            model=None, reasoning_level=None,
        ) -> AsyncIterator[str | dict]:
            yield {"type": "tool", "name": "Read", "target": "/etc/hostname"}
            yield "hello"

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
    handler = MessageHandler(
        brain=ToolyBrain(responses=[]), sessions=sessions,
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )
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
        "_browser", "_started_at", "_tailscale_url", "_tailscale_dns",
        "_server", "_serve_task", "_profile_size_cache",
        "_running_brain_kind",
    ):
        setattr(dashboard, k, None)
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    cl = TestClient(dashboard._app)

    r = cl.post(
        "/api/v1/chat/stream",
        headers=_auth(),
        json={"text": "hi"},
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events == [
        {"type": "tool", "name": "Read", "target": "/etc/hostname"},
        {"type": "chunk", "text": "hello"},
        {"type": "done", "reply": "hello"},
    ]


def test_handler_stream_emits_dict_error_with_code(tmp_path: Path) -> None:
    """Phase C: error events carry a discriminator code so the
    chat UI can pick a per-error recovery affordance. Pin the
    BrainTimeoutError → ``brain_timeout`` mapping; same shape
    expected for BrainError / SessionLost / generic Exception."""
    from vexis_agent.core.brain.base import BrainTimeoutError

    class TimeyBrain(BrainNull):
        async def astream(
            self, message: str, chat_id: int, *,
            model=None, reasoning_level=None,
        ) -> AsyncIterator[str]:
            yield "starting…"
            raise BrainTimeoutError("simulated timeout")

    brain = TimeyBrain(responses=[])
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
    handler = MessageHandler(
        brain=brain, sessions=sessions,
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )

    async def run() -> list:
        out: list = []
        async for evt in handler.stream(_ALLOWED_USER_ID, 1, "hi"):
            out.append(evt)
        return out

    events = asyncio.run(run())
    # First a chunk, then a single error with structured payload.
    assert events[0] == ("chunk", "starting…")
    assert len(events) == 2
    kind, payload = events[1]
    assert kind == "error"
    assert isinstance(payload, dict)
    assert payload["code"] == "brain_timeout"
    assert "ceiling" in payload["message"]  # the user-facing string


def test_stream_route_error_frame_includes_code(tmp_path: Path) -> None:
    """End-to-end: SSE route serializes the dict error payload as
    ``data: {"type":"error","code":"...","message":"..."}``."""
    from vexis_agent.core.brain.base import BrainError

    class CrashBrain(BrainNull):
        async def astream(
            self, message: str, chat_id: int, *,
            model=None, reasoning_level=None,
        ) -> AsyncIterator[str]:
            raise BrainError("crashed for the test")

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
    handler = MessageHandler(
        brain=CrashBrain(responses=[]), sessions=sessions,
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )
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
        "_browser", "_started_at", "_tailscale_url", "_tailscale_dns",
        "_server", "_serve_task", "_profile_size_cache",
        "_running_brain_kind",
    ):
        setattr(dashboard, k, None)
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    cl = TestClient(dashboard._app)

    r = cl.post("/api/v1/chat/stream", headers=_auth(), json={"text": "hi"})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    err = next(e for e in events if e.get("type") == "error")
    assert err["code"] == "brain_error"
    assert "Something broke" in err["message"]


def test_handler_stream_rejects_disallowed_user(
    handler: MessageHandler, brain: BrainNull,
) -> None:
    """Wrong user_id → single ``("error", None)`` event, brain
    NEVER consulted. Defensive against auth drift."""

    async def run() -> list:
        out: list = []
        async for evt in handler.stream(99999, 1, "x"):
            out.append(evt)
        return out

    events = asyncio.run(run())
    assert events == [("error", None)]
    # Brain recorder is empty — astream never invoked.
    assert brain.calls() == []


# ──────────────────────────────────────────────────────────────────
# /api/v1/chat/stream route — SSE format
# ──────────────────────────────────────────────────────────────────


def test_stream_route_emits_sse_frames(
    client: TestClient, brain: BrainNull,
) -> None:
    r = client.post(
        "/api/v1/chat/stream",
        headers=_auth(),
        json={"text": "hello"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    # One chunk (default fallback yields once), then one done.
    assert events == [
        {"type": "chunk", "text": "streamed reply text"},
        {"type": "done", "reply": "streamed reply text"},
    ]


def test_stream_route_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/api/v1/chat/stream",
        json={"text": "hi"},
    )
    assert r.status_code == 401


def test_stream_route_rejects_empty_text(client: TestClient) -> None:
    r = client.post(
        "/api/v1/chat/stream",
        headers=_auth(),
        json={"text": "  "},
    )
    assert r.status_code == 400


def test_stream_route_503_when_chat_disabled(
    tmp_path: Path,
) -> None:
    """Same 503 posture as the rest of /chat/* — chat=None at
    construction time → service not initialised."""
    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = tmp_path  # type: ignore[attr-defined]
    dashboard._token = _TOKEN  # type: ignore[attr-defined]
    dashboard._learning = None  # type: ignore[attr-defined]
    dashboard._chat = None  # type: ignore[attr-defined]
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
        "_browser", "_started_at", "_tailscale_url", "_tailscale_dns",
        "_server", "_serve_task", "_profile_size_cache",
        "_running_brain_kind",
    ):
        setattr(dashboard, k, None)
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    cl = TestClient(dashboard._app)
    r = cl.post(
        "/api/v1/chat/stream",
        headers=_auth(),
        json={"text": "hi"},
    )
    assert r.status_code == 503


def test_stream_forwards_model_override(
    client: TestClient, brain: BrainNull,
) -> None:
    """Voice-call-mode override semantics work for streaming too —
    ``model`` and ``reasoning_level`` on the body get forwarded all
    the way to the brain. Same isolation invariant the buffered
    /chat/voice route already pins."""
    r = client.post(
        "/api/v1/chat/stream",
        headers=_auth(),
        json={
            "text": "hi",
            "model": "claude-opus-4-7",
            "reasoning_level": "high",
        },
    )
    assert r.status_code == 200
    # BrainNull recorder has the override.
    assert brain.calls() == [
        ("hi", -1, "claude-opus-4-7", "high"),
    ]


def test_stream_attachments_prepend_hint(
    client: TestClient, brain: BrainNull,
) -> None:
    """Attachments on the streaming body get the same hint-block
    prepend as the buffered /chat/send path. Brain sees the same
    prompt regardless of streaming or buffered."""
    r = client.post(
        "/api/v1/chat/stream",
        headers=_auth(),
        json={
            "text": "what's in this image?",
            "attachments": [
                {
                    "path": "/tmp/uploads/work/cat.png",
                    "name": "cat.png",
                    "mime": "image/png",
                },
            ],
        },
    )
    assert r.status_code == 200
    assert len(brain.calls()) == 1
    msg = brain.calls()[0][0]
    assert "[ATTACHMENTS" in msg
    assert "cat.png" in msg
    assert "what's in this image?" in msg


# ──────────────────────────────────────────────────────────────────
# Isolation: streaming does NOT leak into /chat/send (regression)
# ──────────────────────────────────────────────────────────────────


def test_chat_send_still_uses_buffered_respond(
    client: TestClient, brain: BrainNull,
) -> None:
    """/chat/send must continue to work unchanged — same
    BrainNull.respond call shape, no streaming invocation. Pins
    the contract that adding streaming didn't accidentally re-route
    the buffered path."""
    r = client.post(
        "/api/v1/chat/send",
        headers=_auth(),
        json={"text": "hello"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"reply": "streamed reply text"}


# ──────────────────────────────────────────────────────────────────
# /api/v1/chat/cancel — Stop button + session-switch cleanup
# ──────────────────────────────────────────────────────────────────


def test_cancel_route_requires_auth(client: TestClient) -> None:
    """Same auth gate as the rest of /chat/*. Without a token the
    cancel route can't be used to discover whether a turn is in
    flight (info-leak)."""
    r = client.post("/api/v1/chat/cancel")
    assert r.status_code == 401


def test_cancel_route_returns_false_when_running_tasks_unwired(
    client: TestClient,
) -> None:
    """Test fixture sets ``_running_tasks=None`` — the cancel route
    should treat this as 'nothing to cancel' rather than 500. Lets
    the front-end fire-and-forget regardless of construction state.
    """
    r = client.post("/api/v1/chat/cancel", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"cancelled": False}


def test_cancel_route_invokes_running_tasks_cancel(
    chat: WebChatTransport, tmp_path: Path,
) -> None:
    """When ``_running_tasks`` is wired, the cancel route should
    route through ``RunningTasks.cancel(WEB_CHAT_ID=-1)``. Pin the
    chat_id so a future refactor can't silently start cancelling
    Telegram chats from the web Stop button."""

    class FakeRunning:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def cancel(self, chat_id: int, grace_seconds: float = 2.0) -> bool:
            self.calls.append(chat_id)
            # Pretend a turn was running so we can assert on the
            # response body (cancelled=true).
            return True

    fake = FakeRunning()
    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = tmp_path  # type: ignore[attr-defined]
    dashboard._token = _TOKEN  # type: ignore[attr-defined]
    dashboard._learning = None  # type: ignore[attr-defined]
    dashboard._chat = chat  # type: ignore[attr-defined]
    dashboard._relationships_mutation_window_seconds = 600  # type: ignore[attr-defined]
    dashboard._relationships_mutation_limit = 100  # type: ignore[attr-defined]
    dashboard._relationships_mutation_log = defaultdict(deque)  # type: ignore[attr-defined]
    dashboard._config = DashboardConfig(  # type: ignore[attr-defined]
        host="127.0.0.1", port=0,
        web_dist=tmp_path / "no-frontend",
        manage_tailscale=False,
    )
    for k in (
        "_sessions", "_background_tasks", "_curator",
        "_browser", "_started_at", "_tailscale_url", "_tailscale_dns",
        "_server", "_serve_task", "_profile_size_cache",
        "_running_brain_kind",
    ):
        setattr(dashboard, k, None)
    dashboard._running_tasks = fake  # type: ignore[attr-defined]
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    cl = TestClient(dashboard._app)

    r = cl.post("/api/v1/chat/cancel", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"cancelled": True}
    # WEB_CHAT_ID == -1 — same chat-id namespace the streaming path
    # uses. Drift here would mean cancel hits the wrong subprocess.
    assert fake.calls == [-1]


def test_cancel_route_503_when_chat_disabled(tmp_path: Path) -> None:
    """Mirrors the rest of /chat/* — chat=None → 503 not 500."""
    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = tmp_path  # type: ignore[attr-defined]
    dashboard._token = _TOKEN  # type: ignore[attr-defined]
    dashboard._learning = None  # type: ignore[attr-defined]
    dashboard._chat = None  # type: ignore[attr-defined]
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
        "_browser", "_started_at", "_tailscale_url", "_tailscale_dns",
        "_server", "_serve_task", "_profile_size_cache",
        "_running_brain_kind",
    ):
        setattr(dashboard, k, None)
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    cl = TestClient(dashboard._app)
    r = cl.post("/api/v1/chat/cancel", headers=_auth())
    assert r.status_code == 503


def test_web_transport_cancel_routes_to_running_tasks_with_web_chat_id(
    chat: WebChatTransport,
) -> None:
    """Direct unit test on the transport. The route test above is a
    full-stack assertion; this one pins the transport contract on
    its own so a route-rewrite can't silently change which chat_id
    gets cancelled."""

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def cancel(self, chat_id: int) -> bool:
            self.calls.append(chat_id)
            return False

    recorder = Recorder()
    result = asyncio.run(chat.cancel(recorder))
    assert result is False
    assert recorder.calls == [-1]


# ──────────────────────────────────────────────────────────────────
# Sidebar previews (Phase B): WebChatTransport.list_sessions
# attaches a snippet of each session's first user message.
# ──────────────────────────────────────────────────────────────────


def test_truncate_preview_short_text_unchanged() -> None:
    """Short user messages survive intact — the helper is a no-op
    when the text is already under the cap. Pin so we don't add
    a forced ellipsis later."""
    assert _truncate_preview("hello world") == "hello world"


def test_truncate_preview_long_text_capped_with_ellipsis() -> None:
    """Long messages get truncated to the cap with a trailing ellipsis.
    Pin the exact truncated length so a future cap change is visible
    in this test rather than silently rolling out."""
    text = "a" * 200
    out = _truncate_preview(text)
    assert len(out) <= 80
    assert out.endswith("…")


def test_truncate_preview_collapses_whitespace() -> None:
    """Multi-line / extra-spaces input gets normalized to one line.
    A multi-line preview would visually break the sidebar's
    line-clamp layout and confuse the rendered length."""
    text = "first line\n\n\nsecond   line\twith\ttabs"
    out = _truncate_preview(text)
    assert "\n" not in out
    assert "  " not in out  # no run of multiple spaces
    assert "first line" in out
    assert "second line" in out


class _PreviewBrain(BrainNull):
    """Minimal stand-in: returns a fake transcript with one user
    message via ``iter_messages``. Lets the transport's preview
    logic exercise without hitting disk."""

    def __init__(self, first_user_text: str = "test prompt") -> None:
        super().__init__(responses=[])
        self._first_user_text = first_user_text
        self.iter_calls: list[str] = []

    def iter_messages(self, session_id: str):  # type: ignore[override]
        self.iter_calls.append(session_id)
        # Yield a system message first to verify the transport
        # walks past non-user messages to find a user turn.

        class _M:
            def __init__(self, role: str, text: str) -> None:
                self.role = role
                self.text = text

        yield _M("assistant", "hi sir, how can I help?")
        yield _M("user", self._first_user_text)
        yield _M("assistant", "...")


def test_list_sessions_attaches_preview_from_first_user_message(
    tmp_path: Path,
) -> None:
    """End-to-end: list_sessions calls into the brain's transcript
    reader, finds the first user-role message, attaches a truncated
    preview to each WebSessionInfo. Sidebar sees the snippet."""
    brain = _PreviewBrain(first_user_text="explain monads in haskell")
    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = tmp_path / "sessions.json"  # type: ignore[attr-defined]
    sessions._active = "work"  # type: ignore[attr-defined]
    sessions._sessions = {  # type: ignore[attr-defined]
        "work": {
            "uuid": "11111111-2222-3333-4444-555555555555",
            "initialized": True,
            "created_at": "2026-05-08T10:00:00+00:00",
        },
    }
    handler = MessageHandler(
        brain=brain, sessions=sessions,
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)

    infos = chat.list_sessions()
    assert infos is not None
    assert len(infos) == 1
    assert infos[0].preview == "explain monads in haskell"


def test_list_sessions_preview_cache_avoids_rereading(tmp_path: Path) -> None:
    """Cache invariant: a second list_sessions call must NOT
    re-read the brain's transcript. First-user-message is append-
    only on the brain side (claude writes once at session init),
    so the cache stays valid forever."""
    brain = _PreviewBrain()
    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = tmp_path / "sessions.json"  # type: ignore[attr-defined]
    sessions._active = "work"  # type: ignore[attr-defined]
    sessions._sessions = {  # type: ignore[attr-defined]
        "work": {
            "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "initialized": True,
            "created_at": "2026-05-08T10:00:00+00:00",
        },
    }
    handler = MessageHandler(
        brain=brain, sessions=sessions,
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)

    chat.list_sessions()
    chat.list_sessions()
    chat.list_sessions()
    # Brain.iter_messages called exactly once for that uuid.
    assert brain.iter_calls.count(
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ) == 1


def test_list_sessions_preview_none_when_brain_raises(tmp_path: Path) -> None:
    """Robustness: a brain that fails on iter_messages (missing
    transcript, locked DB) must not crash the sidebar list call.
    Preview becomes None; the row still renders with name + date."""

    class _FaultyBrain(BrainNull):
        def iter_messages(self, session_id: str):  # type: ignore[override]
            raise RuntimeError("transcript unreadable")

    brain = _FaultyBrain(responses=[])
    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = tmp_path / "sessions.json"  # type: ignore[attr-defined]
    sessions._active = "work"  # type: ignore[attr-defined]
    sessions._sessions = {  # type: ignore[attr-defined]
        "work": {
            "uuid": "ffffffff-1111-2222-3333-444444444444",
            "initialized": True,
            "created_at": "2026-05-08T10:00:00+00:00",
        },
    }
    handler = MessageHandler(
        brain=brain, sessions=sessions,
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    infos = chat.list_sessions()
    assert infos is not None
    assert infos[0].preview is None


def test_list_sessions_preview_skips_non_user_messages(tmp_path: Path) -> None:
    """Defensive: transcripts may lead with a system or assistant
    message (curator/judge sessions, or anything weird). The
    transport must walk past those to find the first ACTUAL user
    turn — using the first message regardless of role would show
    bogus previews like `hi sir, how can I help?` from the
    assistant side."""
    # The _PreviewBrain fixture above leads with an assistant
    # message; verify the user message is what's surfaced.
    brain = _PreviewBrain(first_user_text="this is the user turn")
    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = tmp_path / "sessions.json"  # type: ignore[attr-defined]
    sessions._active = "work"  # type: ignore[attr-defined]
    sessions._sessions = {  # type: ignore[attr-defined]
        "work": {
            "uuid": "11111111-1111-1111-1111-111111111111",
            "initialized": True,
            "created_at": "2026-05-08T10:00:00+00:00",
        },
    }
    handler = MessageHandler(
        brain=brain, sessions=sessions,
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    infos = chat.list_sessions()
    assert infos is not None
    assert infos[0].preview == "this is the user turn"
