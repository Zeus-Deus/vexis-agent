"""Tests for the dashboard ``/api/v1/chat/*`` endpoints.

Mirrors the construction trick in ``test_dashboard_goals_endpoints.py``:
bypass the daemon wiring with ``WebDashboard.__new__``, populate only
the attributes the route handlers touch, and exercise the FastAPI app
through ``TestClient``.

The chat transport is stubbed with a fake that records every call —
the real :class:`transports.web.WebChatTransport` is a thin shim over
:class:`core.handler.MessageHandler` and the unit tests for that live
elsewhere. What we want to verify here:

  * auth gating (no token → 401, bad token → 401)
  * 503 when ``chat`` was constructed as ``None``
  * input validation (empty text, oversized text, missing names)
  * the route forwards the right method/args to the transport
  * the JSON wire format matches the README/UI contract
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.web_server import DashboardConfig, WebDashboard


_TOKEN = "test-token-chat-cafef00d"


# ──────────────────────────────────────────────────────────────────
# Stubs
# ──────────────────────────────────────────────────────────────────


class _StubChat:
    """Records every call. Returns canned replies so the route layer
    can be exercised without dragging the brain or session store
    through the test surface."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.send_reply: str | None = "stubbed reply"
        self.session_reply: str | None = "stubbed session reply"
        self.sessions: list | None = []  # list of WebSessionInfo-shaped objects

    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))

    async def send(self, text: str) -> str | None:
        self._record("send", text)
        return self.send_reply

    async def clear(self) -> str | None:
        self._record("clear")
        return self.session_reply

    async def new_session(self, name: str | None = None) -> str | None:
        self._record("new_session", name)
        return self.session_reply

    async def switch_session(self, name: str) -> str | None:
        self._record("switch_session", name)
        return self.session_reply

    async def rename_session(self, old: str, new: str) -> str | None:
        self._record("rename_session", old, new)
        return self.session_reply

    async def delete_session(self, name: str) -> str | None:
        self._record("delete_session", name)
        return self.session_reply

    def list_sessions(self):
        self._record("list_sessions")
        return self.sessions


class _FakeSessionInfo:
    """Duck-typed WebSessionInfo. The route only reads three fields."""

    def __init__(self, name: str, is_active: bool, created_at: str) -> None:
        self.name = name
        self.is_active = is_active
        self.created_at = created_at


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


def _build_dashboard(
    tmp_path: Path, *, chat: object | None
) -> WebDashboard:
    """Construct a WebDashboard wired with just the attributes
    ``_build_app`` reads. The chat transport is the variable of
    interest; everything else is set to None or a sentinel."""
    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = tmp_path  # type: ignore[attr-defined]
    dashboard._token = _TOKEN  # type: ignore[attr-defined]
    dashboard._learning = None  # type: ignore[attr-defined]
    dashboard._chat = chat  # type: ignore[attr-defined]
    dashboard._relationships_mutation_window_seconds = 600  # type: ignore[attr-defined]
    dashboard._relationships_mutation_limit = 100  # type: ignore[attr-defined]
    dashboard._relationships_mutation_log = defaultdict(deque)  # type: ignore[attr-defined]
    dashboard._config = DashboardConfig(  # type: ignore[attr-defined]
        host="127.0.0.1",
        port=0,
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
    return dashboard


@pytest.fixture
def stub_chat() -> _StubChat:
    return _StubChat()


@pytest.fixture
def client_with_chat(tmp_path: Path, stub_chat: _StubChat) -> TestClient:
    return TestClient(_build_dashboard(tmp_path, chat=stub_chat)._app)


@pytest.fixture
def client_no_chat(tmp_path: Path) -> TestClient:
    return TestClient(_build_dashboard(tmp_path, chat=None)._app)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN}"}


# ──────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────


def test_send_requires_auth(client_with_chat: TestClient) -> None:
    r = client_with_chat.post("/api/v1/chat/send", json={"text": "hi"})
    assert r.status_code == 401


def test_send_rejects_bad_token(client_with_chat: TestClient) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/send",
        json={"text": "hi"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401


def test_sessions_requires_auth(client_with_chat: TestClient) -> None:
    r = client_with_chat.get("/api/v1/chat/sessions")
    assert r.status_code == 401


# ──────────────────────────────────────────────────────────────────
# 503 path: chat transport not initialised
# ──────────────────────────────────────────────────────────────────


def test_send_503_when_chat_none(client_no_chat: TestClient) -> None:
    r = client_no_chat.post(
        "/api/v1/chat/send", json={"text": "hi"}, headers=_auth()
    )
    assert r.status_code == 503


def test_sessions_503_when_chat_none(client_no_chat: TestClient) -> None:
    r = client_no_chat.get("/api/v1/chat/sessions", headers=_auth())
    assert r.status_code == 503


# ──────────────────────────────────────────────────────────────────
# /chat/send
# ──────────────────────────────────────────────────────────────────


def test_send_round_trip(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    stub_chat.send_reply = "from the brain"
    r = client_with_chat.post(
        "/api/v1/chat/send", json={"text": "hello"}, headers=_auth()
    )
    assert r.status_code == 200
    assert r.json() == {"reply": "from the brain"}
    assert stub_chat.calls == [("send", ("hello",), {})]


def test_send_rejects_empty_text(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/send", json={"text": "   "}, headers=_auth()
    )
    assert r.status_code == 400
    assert stub_chat.calls == []


def test_send_rejects_missing_text(client_with_chat: TestClient) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/send", json={}, headers=_auth()
    )
    assert r.status_code == 400


def test_send_rejects_oversized_text(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    # Server cap is 32 KiB; 33 KiB triggers the 413.
    r = client_with_chat.post(
        "/api/v1/chat/send",
        json={"text": "x" * (33 * 1024)},
        headers=_auth(),
    )
    assert r.status_code == 413
    assert stub_chat.calls == []


def test_send_401_when_handler_returns_none(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    # ``None`` is the handler's "user_id rejected" signal. Behind the
    # token gate this should be unreachable, but the route forwards
    # it as 401 to keep the contract explicit.
    stub_chat.send_reply = None
    r = client_with_chat.post(
        "/api/v1/chat/send", json={"text": "hi"}, headers=_auth()
    )
    assert r.status_code == 401


# ──────────────────────────────────────────────────────────────────
# /chat/sessions
# ──────────────────────────────────────────────────────────────────


def test_sessions_list_empty(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    stub_chat.sessions = []
    r = client_with_chat.get("/api/v1/chat/sessions", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_sessions_list_round_trip(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    stub_chat.sessions = [
        _FakeSessionInfo("work", True, "2026-05-08T10:00:00+00:00"),
        _FakeSessionInfo("side", False, "2026-05-07T14:30:00+00:00"),
    ]
    r = client_with_chat.get("/api/v1/chat/sessions", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "sessions": [
            {
                "name": "work",
                "is_active": True,
                "created_at": "2026-05-08T10:00:00+00:00",
            },
            {
                "name": "side",
                "is_active": False,
                "created_at": "2026-05-07T14:30:00+00:00",
            },
        ]
    }


# ──────────────────────────────────────────────────────────────────
# Session CRUD
# ──────────────────────────────────────────────────────────────────


def test_new_session_with_name(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/sessions/new",
        json={"name": "demo"},
        headers=_auth(),
    )
    assert r.status_code == 200
    assert stub_chat.calls == [("new_session", ("demo",), {})]


def test_new_session_auto_name(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/sessions/new", json={}, headers=_auth()
    )
    assert r.status_code == 200
    # Empty body forwards None so the handler auto-generates.
    assert stub_chat.calls == [("new_session", (None,), {})]


def test_switch_session(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/sessions/switch",
        json={"name": "demo"},
        headers=_auth(),
    )
    assert r.status_code == 200
    assert stub_chat.calls == [("switch_session", ("demo",), {})]


def test_switch_session_requires_name(client_with_chat: TestClient) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/sessions/switch", json={}, headers=_auth()
    )
    assert r.status_code == 400


def test_rename_session(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/sessions/rename",
        json={"old": "a", "new": "b"},
        headers=_auth(),
    )
    assert r.status_code == 200
    assert stub_chat.calls == [("rename_session", ("a", "b"), {})]


def test_rename_session_requires_both(client_with_chat: TestClient) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/sessions/rename",
        json={"old": "a"},
        headers=_auth(),
    )
    assert r.status_code == 400


def test_delete_session(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/sessions/delete",
        json={"name": "demo"},
        headers=_auth(),
    )
    assert r.status_code == 200
    assert stub_chat.calls == [("delete_session", ("demo",), {})]


def test_clear(
    client_with_chat: TestClient, stub_chat: _StubChat
) -> None:
    r = client_with_chat.post("/api/v1/chat/clear", headers=_auth())
    assert r.status_code == 200
    assert stub_chat.calls == [("clear", (), {})]


# ──────────────────────────────────────────────────────────────────
# Voice endpoints — /chat/voice (STT) and /chat/tts
#
# We don't exercise the actual STT/TTS providers here (those have
# their own tests in test_voice_providers.py). These tests verify
# the route layer:
#   * auth gating
#   * config-driven 503 (voice disabled / null providers)
#   * input validation (size cap, empty body)
#   * the /info probe shape
# Provider-level errors are simulated by leaving voice disabled —
# the null providers raise STTUnavailable/TTSUnavailable which the
# route translates to 503.
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def voice_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the voice subsystem at an empty config (= voice disabled)."""
    cfg = tmp_path / "config-voice-disabled.yaml"
    monkeypatch.setattr("core.yaml_config._config_path", lambda: cfg)


@pytest.fixture
def voice_enabled_voxtype(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Voice on with voxtype STT and null TTS — used to test the
    /info shape when STT is wired but TTS isn't."""
    cfg = tmp_path / "config-voice-voxtype.yaml"
    cfg.write_text(
        "voice:\n"
        "  enabled: true\n"
        "  stt:\n    provider: voxtype\n"
        "  tts:\n    provider: null\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("core.yaml_config._config_path", lambda: cfg)


def test_voice_info_disabled(
    client_with_chat: TestClient, voice_disabled: None
) -> None:
    r = client_with_chat.get("/api/v1/chat/voice/info", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["stt"]["available"] is False
    assert body["tts"]["available"] is False
    assert body["stt"]["provider"] == "null"
    assert body["tts"]["provider"] == "null"


def test_voice_info_enabled_with_voxtype(
    client_with_chat: TestClient, voice_enabled_voxtype: None
) -> None:
    r = client_with_chat.get("/api/v1/chat/voice/info", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["stt"]["provider"] == "voxtype"
    assert body["stt"]["available"] is True
    assert body["tts"]["provider"] == "null"
    assert body["tts"]["available"] is False


def test_voice_info_requires_auth(client_with_chat: TestClient) -> None:
    r = client_with_chat.get("/api/v1/chat/voice/info")
    assert r.status_code == 401


def test_voice_post_503_when_disabled(
    client_with_chat: TestClient, voice_disabled: None
) -> None:
    # Audio upload to /chat/voice → null STT raises STTUnavailable
    # → route returns 503.
    r = client_with_chat.post(
        "/api/v1/chat/voice",
        headers=_auth(),
        files={"audio": ("test.ogg", b"fake-ogg-bytes", "audio/ogg")},
    )
    assert r.status_code == 503


def test_voice_post_requires_auth(client_with_chat: TestClient) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/voice",
        files={"audio": ("test.ogg", b"x", "audio/ogg")},
    )
    assert r.status_code == 401


def test_voice_post_rejects_empty_upload(
    client_with_chat: TestClient, voice_disabled: None
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/voice",
        headers=_auth(),
        files={"audio": ("empty.ogg", b"", "audio/ogg")},
    )
    assert r.status_code == 400


def test_voice_post_503_when_chat_none(
    client_no_chat: TestClient, voice_disabled: None
) -> None:
    # Chat transport itself missing → 503 before any voice work.
    r = client_no_chat.post(
        "/api/v1/chat/voice",
        headers=_auth(),
        files={"audio": ("test.ogg", b"x", "audio/ogg")},
    )
    assert r.status_code == 503


def test_tts_post_503_when_disabled(
    client_with_chat: TestClient, voice_disabled: None
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/tts", json={"text": "hello"}, headers=_auth()
    )
    assert r.status_code == 503


def test_tts_post_requires_auth(client_with_chat: TestClient) -> None:
    r = client_with_chat.post("/api/v1/chat/tts", json={"text": "hi"})
    assert r.status_code == 401


def test_tts_post_rejects_empty_text(
    client_with_chat: TestClient, voice_disabled: None
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/tts", json={"text": "  "}, headers=_auth()
    )
    assert r.status_code == 400


def test_tts_post_rejects_oversized_text(
    client_with_chat: TestClient, voice_disabled: None
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/tts",
        json={"text": "x" * (33 * 1024)},
        headers=_auth(),
    )
    # Either 400 (size validator) or 413 (TTS-specific cap) is fine —
    # both correctly reject. Our chat send route returns 413; the TTS
    # route reuses _validated_text first which is also a 413.
    assert r.status_code in (400, 413)


# ──────────────────────────────────────────────────────────────────
# Attachments — /chat/attach upload + /chat/send with attachments
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def attachments_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty config = attachments enabled with defaults."""
    cfg = tmp_path / "config-attach-default.yaml"
    monkeypatch.setattr("core.yaml_config._config_path", lambda: cfg)


@pytest.fixture
def attachments_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config-attach-disabled.yaml"
    cfg.write_text(
        "chat:\n  attachments:\n    enabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("core.yaml_config._config_path", lambda: cfg)


def test_attach_requires_auth(client_with_chat: TestClient) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/attach",
        files={"file": ("hi.png", b"fake-png", "image/png")},
    )
    assert r.status_code == 401


def test_attach_503_when_disabled(
    client_with_chat: TestClient, attachments_disabled: None
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/attach",
        headers=_auth(),
        files={"file": ("hi.png", b"x", "image/png")},
    )
    assert r.status_code == 503


def test_attach_rejects_disallowed_mime(
    client_with_chat: TestClient,
    stub_chat: _StubChat,
    attachments_default: None,
) -> None:
    stub_chat.sessions = [
        _FakeSessionInfo("work", True, "2026-05-08T10:00:00+00:00"),
    ]
    r = client_with_chat.post(
        "/api/v1/chat/attach",
        headers=_auth(),
        files={"file": ("evil.exe", b"MZ...", "application/x-executable")},
    )
    assert r.status_code == 415


def test_attach_rejects_empty_file(
    client_with_chat: TestClient,
    stub_chat: _StubChat,
    attachments_default: None,
) -> None:
    stub_chat.sessions = [
        _FakeSessionInfo("work", True, "2026-05-08T10:00:00+00:00"),
    ]
    r = client_with_chat.post(
        "/api/v1/chat/attach",
        headers=_auth(),
        files={"file": ("hi.png", b"", "image/png")},
    )
    assert r.status_code == 400


def test_attach_writes_to_workspace_uploads(
    client_with_chat: TestClient,
    stub_chat: _StubChat,
    tmp_path: Path,
    attachments_default: None,
) -> None:
    stub_chat.sessions = [
        _FakeSessionInfo("work", True, "2026-05-08T10:00:00+00:00"),
    ]
    payload = b"\x89PNG\r\n\x1a\nfake-png-bytes-for-test"
    r = client_with_chat.post(
        "/api/v1/chat/attach",
        headers=_auth(),
        files={"file": ("hello.png", payload, "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "hello.png"
    assert body["size"] == len(payload)
    assert body["mime"] == "image/png"
    # Path lives under the workspace's uploads/<session>/<ts>-<name>.
    saved = Path(body["path"])
    assert saved.parent.parent == tmp_path / "uploads"
    assert saved.parent.name == "work"
    assert saved.read_bytes() == payload


def test_attach_sanitizes_filename(
    client_with_chat: TestClient,
    stub_chat: _StubChat,
    attachments_default: None,
) -> None:
    stub_chat.sessions = [
        _FakeSessionInfo("work", True, "2026-05-08T10:00:00+00:00"),
    ]
    # Filename has path separators and non-ASCII — server must strip
    # path components and replace unsafe chars.
    r = client_with_chat.post(
        "/api/v1/chat/attach",
        headers=_auth(),
        files={"file": ("../etc/passwd.txt", b"root:x:0:0", "text/plain")},
    )
    assert r.status_code == 200
    body = r.json()
    # Path component stripped, name retained but cleaned.
    assert "/" not in body["name"]
    assert body["name"] == "passwd.txt"
    saved = Path(body["path"])
    assert saved.is_file()
    # And the saved file isn't anywhere near /etc.
    assert "etc" not in saved.parts[1:-1]


def test_send_with_attachments_prepends_hint(
    client_with_chat: TestClient,
    stub_chat: _StubChat,
    attachments_default: None,
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/send",
        headers=_auth(),
        json={
            "text": "What's in this image?",
            "attachments": [
                {
                    "path": "/tmp/uploads/work/20260508T120000Z-cat.png",
                    "name": "cat.png",
                    "mime": "image/png",
                },
            ],
        },
    )
    assert r.status_code == 200
    # The handler stub recorded the formatted message — check the
    # hint block is present and the user text is preserved.
    assert len(stub_chat.calls) == 1
    name, args, _ = stub_chat.calls[0]
    assert name == "send"
    msg = args[0]
    assert "[ATTACHMENTS" in msg
    assert "cat.png" in msg
    assert "/tmp/uploads/work/20260508T120000Z-cat.png" in msg
    assert "What's in this image?" in msg


def test_send_with_invalid_attachments_rejected(
    client_with_chat: TestClient, attachments_default: None
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/send",
        headers=_auth(),
        json={"text": "hi", "attachments": "not-a-list"},
    )
    assert r.status_code == 400


def test_send_with_empty_attachments_skips_hint(
    client_with_chat: TestClient,
    stub_chat: _StubChat,
    attachments_default: None,
) -> None:
    r = client_with_chat.post(
        "/api/v1/chat/send",
        headers=_auth(),
        json={"text": "hello", "attachments": []},
    )
    assert r.status_code == 200
    name, args, _ = stub_chat.calls[0]
    # No hint when the list was empty — message goes through bare.
    assert args[0] == "hello"
