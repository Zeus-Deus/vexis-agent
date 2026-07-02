"""Tests for per-conversation web sessions (issue #48).

Covers the general core seam and its web-transport mapping:

  1. Mapping helpers — determinism, distinct cids → distinct
     names/chat ids, sanitisation (spaces/slashes/unicode/200-char cap),
     derived name always valid per ``_NAME_RE`` and ≤ 64 chars, chat id
     strictly inside the reserved band and never == ``WEB_CHAT_ID``.
  2. ``SessionStore.ensure`` — creates once, idempotent, never flips
     active, persists to disk.
  3. ``SessionView`` — get/rotate/mark_initialized/set write through to
     the named session and never touch the active one.
  4. Transport ``send`` WITH conversation_id — brain records the derived
     chat_id + the threaded view (uuid == that named session's uuid),
     active session untouched, two conversations isolated.
  5. Transport ``send`` WITHOUT conversation_id — legacy path pinned
     (chat_id == -1, recorded session is None).
  6. ``clear`` — a conversation clear rotates only that conversation's
     uuid; legacy clear still rotates active.
  7. ``cancel`` — routes the derived chat id (or WEB_CHAT_ID) to
     RunningTasks.
  8. ``stream`` WITH conversation_id — chunk+done, session threading
     recorded.
  9. Busy — injected ``TaskAlreadyRunning`` → ``handle`` returns the busy
     toast, ``stream`` yields ``("error", {"code": "busy"})``,
     ``TurnOutcome.kind == "busy"`` and ``is_brain_failure`` is False.
 10. Notifier isolation — a note buffered for conversation A is not
     consumed by a turn on B, is consumed (and injected) by a turn on A.
 11. Routes (TestClient) — send/stream/clear/cancel accept + validate
     ``conversation_id`` (400 on empty / non-string / >200 chars), send
     echoes it, ``/chat/history`` returns [] for unknown cid, 400 on
     invalid, messages for a seeded one; legacy no-conversation requests
     stay byte-identical.
 12. main.py web-only wiring — ``background_tasks.set_notify(notifier.send)``
     buffers a completion note the next chat turn can consume.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core.brain.null import BrainNull
from vexis_agent.core.handler import TurnOutcome, MessageHandler, _BUSY
from vexis_agent.core.notify import Notifier
from vexis_agent.core.running_tasks import TaskAlreadyRunning
from vexis_agent.core.sessions import SessionStore, SessionView, _NAME_RE
from vexis_agent.core.transcripts import claude_session_jsonl_dir
from vexis_agent.core.web_server import DashboardConfig, WebDashboard
from vexis_agent.transports.web import (
    WEB_CHAT_ID,
    WebChatTransport,
    conversation_chat_id,
    conversation_session_name,
    validate_conversation_id,
)


_TOKEN = "test-token-conv-cafef00d"
_ALLOWED_USER_ID = 12345

_CHAT_ID_BAND_LOW = -(2 ** 52 + 2 ** 48)
_CHAT_ID_BAND_HIGH = -(2 ** 52)


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    """A REAL store (per-name ops matter now) with one auto-created
    active session on a temp path."""
    return SessionStore(tmp_path / "session.json")


def _make_handler(
    store: SessionStore,
    *,
    responses: list[str] | None = None,
    notifier: Notifier | None = None,
    transcript_workspace: Path | None = None,
) -> tuple[BrainNull, MessageHandler]:
    brain = BrainNull(
        responses=responses if responses is not None else [],
        transcript_workspace=transcript_workspace,
    )
    handler = MessageHandler(
        brain=brain,
        sessions=store,
        allowed_user_id=_ALLOWED_USER_ID,
        notifier=notifier,
    )
    return brain, handler


def _uuid_for(store: SessionStore, name: str) -> str:
    return next(s.uuid for s in store.list() if s.name == name)


def _build_client(tmp_path: Path, chat: object | None) -> TestClient:
    """A dashboard wired with just the attributes the chat routes read,
    plus a fake RunningTasks for the cancel route."""

    class _FakeRunning:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def cancel(self, chat_id: int, grace_seconds: float = 2.0) -> bool:
            self.calls.append(chat_id)
            return True

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
        "_sessions", "_background_tasks", "_curator", "_browser",
        "_addon_runtime", "_started_at", "_tailscale_url", "_tailscale_dns",
        "_server", "_serve_task", "_profile_size_cache", "_running_brain_kind",
    ):
        setattr(dashboard, k, None)
    dashboard._running_tasks = _FakeRunning()  # type: ignore[attr-defined]
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    client = TestClient(dashboard._app)
    # Stash the fake so the cancel route test can assert which chat id
    # the Stop button routed to.
    client._fake_running = dashboard._running_tasks  # type: ignore[attr-defined]
    return client


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN}"}


def _parse_sse(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[len("data: "):]))
            except json.JSONDecodeError:
                pass
    return out


# ──────────────────────────────────────────────────────────────────
# 1. Mapping helpers
# ──────────────────────────────────────────────────────────────────


def test_mapping_is_deterministic() -> None:
    for cid in ("tab-1", "hello world", "  spaced  ", "日本語/x"):
        assert conversation_session_name(cid) == conversation_session_name(cid)
        assert conversation_chat_id(cid) == conversation_chat_id(cid)


def test_mapping_distinct_cids_distinct_outputs() -> None:
    a, b = "conversation-a", "conversation-b"
    assert conversation_session_name(a) != conversation_session_name(b)
    assert conversation_chat_id(a) != conversation_chat_id(b)


def test_mapping_same_slug_distinct_via_hash() -> None:
    """Two ids that sanitise to the same slug still map to distinct
    sessions — the sha256 suffix breaks the tie."""
    n1 = conversation_session_name("a/b")
    n2 = conversation_session_name("a-b")
    assert n1.startswith("web-a-b-")
    assert n2.startswith("web-a-b-")
    assert n1 != n2


@pytest.mark.parametrize(
    "cid",
    [
        "tab-1",
        "hello world",
        "a/b/c",
        "../../etc/passwd",
        "日本語のタブ",
        "!!!",  # only punctuation → empty slug, name is web-<hash>
        "x" * 200,  # at the length cap
        "-leading-and-trailing-",
        "under_score-and-dash",
    ],
)
def test_derived_name_always_valid(cid: str) -> None:
    name = conversation_session_name(cid)
    assert _NAME_RE.match(name), name
    assert len(name) <= 64
    assert name.startswith("web-")


def test_punctuation_only_id_falls_back_to_hash_name() -> None:
    name = conversation_session_name("!!!")
    # Slug collapses to empty, so the name is just prefix + 8-hex hash.
    assert name == f"web-{name.split('-')[-1]}"
    assert len(name) == len("web-") + 8


@pytest.mark.parametrize(
    "cid", ["tab-1", "hello world", "日本語", "x" * 200, "!!!", "a/b"],
)
def test_chat_id_inside_band_and_never_web_chat_id(cid: str) -> None:
    candidate = conversation_chat_id(cid)
    assert _CHAT_ID_BAND_LOW <= candidate <= _CHAT_ID_BAND_HIGH
    assert candidate != WEB_CHAT_ID
    # Well clear of the Telegram group-id band (~-10**12 .. -10**13).
    assert candidate < -(10 ** 13)


def test_validate_conversation_id_rules() -> None:
    assert validate_conversation_id("  keep-me  ") == "keep-me"
    with pytest.raises(ValueError):
        validate_conversation_id("")
    with pytest.raises(ValueError):
        validate_conversation_id("   ")
    with pytest.raises(ValueError):
        validate_conversation_id(123)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        validate_conversation_id("x" * 201)


# ──────────────────────────────────────────────────────────────────
# 2. SessionStore.ensure
# ──────────────────────────────────────────────────────────────────


def test_ensure_creates_once_idempotent_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    store = SessionStore(path)
    active_before = store.active_name()
    count_before = len(store.list())

    name = "web-conv-abc12345"
    assert store.ensure(name) == name
    uuid_first = _uuid_for(store, name)
    # Idempotent: second call is a no-op and does NOT mint a new uuid.
    assert store.ensure(name) == name
    assert _uuid_for(store, name) == uuid_first
    assert len(store.list()) == count_before + 1
    # Never flips the active pointer.
    assert store.active_name() == active_before

    # Persisted to disk: a fresh store reads the session back.
    reloaded = SessionStore(path)
    assert any(s.name == name for s in reloaded.list())
    assert reloaded.active_name() == active_before


def test_ensure_rejects_invalid_name(store: SessionStore) -> None:
    with pytest.raises(ValueError):
        store.ensure("bad name with spaces")


# ──────────────────────────────────────────────────────────────────
# 3. SessionView
# ──────────────────────────────────────────────────────────────────


def test_session_view_writes_through_and_leaves_active_alone(
    store: SessionStore,
) -> None:
    active_name = store.active_name()
    active_uuid = store.get()

    name = store.ensure("web-view-deadbeef")
    view = SessionView(store, name)

    assert view.name == name
    assert view.get() == _uuid_for(store, name)
    assert view.is_initialized() is False

    view.mark_initialized()
    assert view.is_initialized() is True
    # Active session's initialized flag is independent.

    rotated = view.rotate()
    assert rotated == _uuid_for(store, name)
    assert view.get() == rotated
    assert view.is_initialized() is False

    view.set("pinned-token")
    assert view.get() == "pinned-token"
    assert _uuid_for(store, name) == "pinned-token"

    # The active session was never touched by any view op.
    assert store.active_name() == active_name
    assert store.get() == active_uuid


def test_session_view_deleted_session_raises(store: SessionStore) -> None:
    name = store.ensure("web-doomed-cafef00d")
    view = SessionView(store, name)
    store.switch(store.active_name())  # ensure active != doomed
    store.delete(name)
    with pytest.raises(ValueError):
        view.get()


# ──────────────────────────────────────────────────────────────────
# 4/5. Transport send with / without conversation_id
# ──────────────────────────────────────────────────────────────────


def test_send_with_conversation_threads_view_and_chat_id(
    store: SessionStore,
) -> None:
    brain, handler = _make_handler(store, responses=["reply-a"])
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)

    active_name = store.active_name()
    active_uuid = store.get()

    reply = asyncio.run(chat.send("hi there", conversation_id="conv-A"))
    assert reply == "reply-a"

    name_a = conversation_session_name("conv-A")
    # Brain saw the derived chat_id and the threaded view.
    msg, chat_id, model, reasoning = brain.calls()[-1]
    assert msg == "hi there"
    assert chat_id == conversation_chat_id("conv-A")
    threaded = brain.respond_sessions()[-1]
    assert isinstance(threaded, SessionView)
    assert threaded.name == name_a
    assert threaded.get() == _uuid_for(store, name_a)

    # Global active session untouched.
    assert store.active_name() == active_name
    assert store.get() == active_uuid


def test_two_conversations_are_isolated(store: SessionStore) -> None:
    brain, handler = _make_handler(store, responses=["ra", "rb"])
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)

    asyncio.run(chat.send("to A", conversation_id="A"))
    asyncio.run(chat.send("to B", conversation_id="B"))

    name_a = conversation_session_name("A")
    name_b = conversation_session_name("B")
    assert name_a != name_b
    assert _uuid_for(store, name_a) != _uuid_for(store, name_b)
    # Distinct chat ids recorded.
    chat_ids = [c[1] for c in brain.calls()]
    assert chat_ids == [conversation_chat_id("A"), conversation_chat_id("B")]


def test_send_without_conversation_is_legacy_path(store: SessionStore) -> None:
    brain, handler = _make_handler(store, responses=["legacy"])
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)

    reply = asyncio.run(chat.send("hi"))
    assert reply == "legacy"
    msg, chat_id, _, _ = brain.calls()[-1]
    assert chat_id == WEB_CHAT_ID == -1
    # No session threaded on the legacy path.
    assert brain.respond_sessions()[-1] is None


# ──────────────────────────────────────────────────────────────────
# 6. clear
# ──────────────────────────────────────────────────────────────────


def test_clear_conversation_rotates_only_that_conversation(
    store: SessionStore,
) -> None:
    brain, handler = _make_handler(store, responses=["seed"])
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)

    asyncio.run(chat.send("seed A", conversation_id="A"))
    name_a = conversation_session_name("A")
    uuid_before = _uuid_for(store, name_a)
    active_uuid_before = store.get()

    reply = asyncio.run(chat.clear(conversation_id="A"))
    assert reply == "Conversation cleared."
    assert _uuid_for(store, name_a) != uuid_before
    # Active session unchanged by the conversation clear.
    assert store.get() == active_uuid_before


def test_clear_conversation_never_used_is_harmless(store: SessionStore) -> None:
    _, handler = _make_handler(store)
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    active_uuid_before = store.get()

    reply = asyncio.run(chat.clear(conversation_id="never-sent"))
    assert reply == "Conversation cleared."
    # The session came into existence (ensure) and got a fresh rotate.
    name = conversation_session_name("never-sent")
    assert any(s.name == name for s in store.list())
    assert store.get() == active_uuid_before  # active untouched


def test_legacy_clear_rotates_active(store: SessionStore) -> None:
    _, handler = _make_handler(store)
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    active_uuid_before = store.get()

    reply = asyncio.run(chat.clear())
    assert reply == "Conversation cleared."
    assert store.get() != active_uuid_before


# ──────────────────────────────────────────────────────────────────
# 7. cancel
# ──────────────────────────────────────────────────────────────────


def test_cancel_routes_derived_chat_id(store: SessionStore) -> None:
    _, handler = _make_handler(store)
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)

    class _Rec:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def cancel(self, chat_id: int) -> bool:
            self.calls.append(chat_id)
            return True

    rec = _Rec()
    assert asyncio.run(chat.cancel(rec, conversation_id="A")) is True
    assert rec.calls == [conversation_chat_id("A")]

    rec2 = _Rec()
    asyncio.run(chat.cancel(rec2))
    assert rec2.calls == [WEB_CHAT_ID]


# ──────────────────────────────────────────────────────────────────
# 8. stream with conversation_id
# ──────────────────────────────────────────────────────────────────


def test_stream_with_conversation_threads_session(store: SessionStore) -> None:
    brain, handler = _make_handler(store, responses=["streamed"])
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)

    async def run() -> list:
        out: list = []
        async for evt in chat.stream("hello", conversation_id="A"):
            out.append(evt)
        return out

    events = asyncio.run(run())
    assert events == [("chunk", "streamed"), ("done", "streamed")]

    name_a = conversation_session_name("A")
    msg, chat_id, _, _ = brain.calls()[-1]
    assert chat_id == conversation_chat_id("A")
    threaded = brain.respond_sessions()[-1]
    assert isinstance(threaded, SessionView)
    assert threaded.name == name_a


def test_default_astream_legacy_brain_without_session_kwarg(
    store: SessionStore,
) -> None:
    """A third-party brain whose ``respond`` never grew the ``session``
    kwarg must keep working through the ABC's default ``astream``
    fallback on the legacy (``session=None``) path — the streaming
    twin of the handler's conditional kwarg omission. Regression pin:
    an unconditional ``session=session`` forward in the default
    ``astream`` raised ``TypeError`` here."""

    class LegacyBrain(BrainNull):
        async def respond(  # type: ignore[override]
            self, message, chat_id, *, model=None, reasoning_level=None,
        ):
            return "legacy-ok"

    brain = LegacyBrain(responses=[])
    handler = MessageHandler(
        brain=brain, sessions=store, allowed_user_id=_ALLOWED_USER_ID,
        notifier=None,
    )

    async def run() -> list:
        out: list = []
        async for evt in handler.stream(
            _ALLOWED_USER_ID, WEB_CHAT_ID, "hi",
        ):
            out.append(evt)
        return out

    events = asyncio.run(run())
    assert events == [("chunk", "legacy-ok"), ("done", "legacy-ok")]


# ──────────────────────────────────────────────────────────────────
# 9. Busy
# ──────────────────────────────────────────────────────────────────


def test_handle_busy_returns_toast_and_outcome(store: SessionStore) -> None:
    brain, handler = _make_handler(store)
    brain.next_raises(TaskAlreadyRunning("slot taken"))
    outcome = TurnOutcome()

    reply = asyncio.run(
        handler.handle(_ALLOWED_USER_ID, WEB_CHAT_ID, "hi", outcome=outcome)
    )
    assert reply == _BUSY
    assert outcome.kind == "busy"
    assert outcome.is_brain_failure is False
    assert outcome.succeeded is False


def test_stream_busy_yields_error_code(store: SessionStore) -> None:
    brain, handler = _make_handler(store)
    brain.next_raises(TaskAlreadyRunning("slot taken"))
    outcome = TurnOutcome()

    async def run() -> list:
        out: list = []
        async for evt in handler.stream(
            _ALLOWED_USER_ID, WEB_CHAT_ID, "hi", outcome=outcome,
        ):
            out.append(evt)
        return out

    events = asyncio.run(run())
    assert events == [("error", {"code": "busy", "message": _BUSY})]
    assert outcome.kind == "busy"
    assert outcome.is_brain_failure is False


# ──────────────────────────────────────────────────────────────────
# 10. Notifier isolation
# ──────────────────────────────────────────────────────────────────


def test_notifier_context_isolated_per_conversation(store: SessionStore) -> None:
    notifier = Notifier()  # no telegram app; buffer half only
    brain, handler = _make_handler(store, responses=["rb", "ra", "ra2"], notifier=notifier)
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)

    # Buffer a note against conversation A's chat_id.
    asyncio.run(notifier.append_context(conversation_chat_id("A"), "note for A"))

    # A turn on conversation B must NOT consume A's note.
    asyncio.run(chat.send("hi B", conversation_id="B"))
    msg_b = brain.calls()[-1][0]
    assert "SYSTEM CONTEXT" not in msg_b
    assert "note for A" not in msg_b

    # A turn on conversation A injects AND consumes the note.
    asyncio.run(chat.send("hi A", conversation_id="A"))
    msg_a = brain.calls()[-1][0]
    assert "[SYSTEM CONTEXT" in msg_a
    assert "note for A" in msg_a

    # Consumed: a second A turn no longer sees it.
    asyncio.run(chat.send("hi A again", conversation_id="A"))
    msg_a2 = brain.calls()[-1][0]
    assert "note for A" not in msg_a2


# ──────────────────────────────────────────────────────────────────
# 11. Routes (TestClient)
# ──────────────────────────────────────────────────────────────────


def test_route_send_echoes_conversation_id(
    tmp_path: Path, store: SessionStore,
) -> None:
    _, handler = _make_handler(store, responses=["hello from brain"])
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    client = _build_client(tmp_path, chat)

    r = client.post(
        "/api/v1/chat/send",
        headers=_auth(),
        json={"text": "hi", "conversation_id": "tab-7"},
    )
    assert r.status_code == 200
    assert r.json() == {"reply": "hello from brain", "conversation_id": "tab-7"}


def test_route_send_legacy_has_no_conversation_key(
    tmp_path: Path, store: SessionStore,
) -> None:
    _, handler = _make_handler(store, responses=["legacy reply"])
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    client = _build_client(tmp_path, chat)

    r = client.post("/api/v1/chat/send", headers=_auth(), json={"text": "hi"})
    assert r.status_code == 200
    assert r.json() == {"reply": "legacy reply"}


@pytest.mark.parametrize("bad", ["", "   ", 123, "x" * 201])
def test_route_send_rejects_bad_conversation_id(
    tmp_path: Path, store: SessionStore, bad: object,
) -> None:
    _, handler = _make_handler(store, responses=["unused"])
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    client = _build_client(tmp_path, chat)

    r = client.post(
        "/api/v1/chat/send",
        headers=_auth(),
        json={"text": "hi", "conversation_id": bad},
    )
    assert r.status_code == 400


def test_route_stream_with_conversation(
    tmp_path: Path, store: SessionStore,
) -> None:
    brain, handler = _make_handler(store, responses=["streamed reply"])
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    client = _build_client(tmp_path, chat)

    r = client.post(
        "/api/v1/chat/stream",
        headers=_auth(),
        json={"text": "hi", "conversation_id": "tab-9"},
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events == [
        {"type": "chunk", "text": "streamed reply"},
        {"type": "done", "reply": "streamed reply"},
    ]
    assert brain.calls()[-1][1] == conversation_chat_id("tab-9")


def test_route_clear_with_conversation(
    tmp_path: Path, store: SessionStore,
) -> None:
    _, handler = _make_handler(store)
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    client = _build_client(tmp_path, chat)
    active_uuid_before = store.get()

    r = client.post(
        "/api/v1/chat/clear",
        headers=_auth(),
        json={"conversation_id": "tab-clear"},
    )
    assert r.status_code == 200
    assert r.json() == {"reply": "Conversation cleared."}
    # The conversation session now exists; active untouched.
    assert any(
        s.name == conversation_session_name("tab-clear") for s in store.list()
    )
    assert store.get() == active_uuid_before


def test_route_clear_no_body_still_works(
    tmp_path: Path, store: SessionStore,
) -> None:
    _, handler = _make_handler(store)
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    client = _build_client(tmp_path, chat)
    r = client.post("/api/v1/chat/clear", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"reply": "Conversation cleared."}


def test_route_cancel_with_conversation(
    tmp_path: Path, store: SessionStore,
) -> None:
    _, handler = _make_handler(store)
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    client = _build_client(tmp_path, chat)

    r = client.post(
        "/api/v1/chat/cancel",
        headers=_auth(),
        json={"conversation_id": "tab-cancel"},
    )
    assert r.status_code == 200
    assert r.json() == {"cancelled": True}
    # The Stop button routed to the conversation's derived chat id, not
    # the shared WEB_CHAT_ID.
    assert client._fake_running.calls == [conversation_chat_id("tab-cancel")]


def test_route_cancel_invalid_conversation_id(
    tmp_path: Path, store: SessionStore,
) -> None:
    _, handler = _make_handler(store)
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    client = _build_client(tmp_path, chat)
    r = client.post(
        "/api/v1/chat/cancel", headers=_auth(), json={"conversation_id": ""},
    )
    assert r.status_code == 400


def test_route_history_unknown_conversation_returns_empty(
    tmp_path: Path, store: SessionStore,
) -> None:
    _, handler = _make_handler(store)
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    client = _build_client(tmp_path, chat)

    r = client.get(
        "/api/v1/chat/history",
        headers=_auth(),
        params={"conversation_id": "never-used"},
    )
    assert r.status_code == 200
    assert r.json() == {"messages": []}
    # A history read must NOT create the session as a side effect.
    assert not any(
        s.name == conversation_session_name("never-used") for s in store.list()
    )


def test_route_history_invalid_conversation_id(
    tmp_path: Path, store: SessionStore,
) -> None:
    _, handler = _make_handler(store)
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    client = _build_client(tmp_path, chat)
    r = client.get(
        "/api/v1/chat/history", headers=_auth(), params={"conversation_id": ""},
    )
    assert r.status_code == 400


def test_route_history_returns_seeded_messages(
    tmp_path: Path,
) -> None:
    """Seed a claude-code-style JSONL for a conversation's session uuid
    and read it back through the /chat/history route."""
    workspace = tmp_path
    store = SessionStore(tmp_path / "session.json")
    brain = BrainNull(responses=[], transcript_workspace=workspace)
    handler = MessageHandler(
        brain=brain, sessions=store,
        allowed_user_id=_ALLOWED_USER_ID, notifier=None,
    )
    chat = WebChatTransport(handler=handler, allowed_user_id=_ALLOWED_USER_ID)
    client = _build_client(tmp_path, chat)

    cid = "seeded-conv"
    name = conversation_session_name(cid)
    store.ensure(name)
    uuid = _uuid_for(store, name)

    jsonl_dir = claude_session_jsonl_dir(workspace)
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    (jsonl_dir / f"{uuid}.jsonl").write_text(
        "\n".join(
            [
                json.dumps({
                    "type": "user", "uuid": "u-0",
                    "timestamp": "2026-05-04T12:00:00Z",
                    "message": {"role": "user", "content": "what is 2+2"},
                }),
                json.dumps({
                    "type": "assistant", "uuid": "a-1",
                    "timestamp": "2026-05-04T12:00:01Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "4"}],
                    },
                }),
            ]
        ) + "\n",
        encoding="utf-8",
    )

    r = client.get(
        "/api/v1/chat/history",
        headers=_auth(),
        params={"conversation_id": cid},
    )
    assert r.status_code == 200
    messages = r.json()["messages"]
    assert [m["content"] for m in messages] == ["what is 2+2", "4"]
    assert [m["role"] for m in messages] == ["user", "assistant"]


# ──────────────────────────────────────────────────────────────────
# 12. main.py web-only notifier wiring (focused; no _run mega-mock)
# ──────────────────────────────────────────────────────────────────


def test_web_only_notify_wiring_buffers_completion(tmp_path: Path) -> None:
    """The exact composition main._run performs when Telegram is
    disabled: ``background_tasks.set_notify(notifier.send)``. Verify a
    background-task completion then reaches the notifier's context buffer
    (the half a web-only deploy needs) even with no Telegram app bound."""
    from vexis_agent.core.background_tasks import BackgroundTasks

    notifier = Notifier()  # no telegram app bound
    background_tasks = BackgroundTasks(
        workspace=tmp_path,
        system_prompt_provider=lambda: "sys prompt",
        state_file=tmp_path / "bg-state.json",
        log_dir=tmp_path / "bg-logs",
    )
    background_tasks.set_notify(notifier.send)

    asyncio.run(background_tasks._maybe_notify(WEB_CHAT_ID, "task 'foo' done"))
    notes = asyncio.run(notifier.consume_context(WEB_CHAT_ID))
    assert [n.text for n in notes] == ["task 'foo' done"]
