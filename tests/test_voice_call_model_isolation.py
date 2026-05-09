"""Isolation tests — voice-call model override must NOT leak into
other surfaces.

The user's contract: setting ``voice.call_mode.model`` (and
``reasoning_level``) should ONLY affect ``/api/v1/chat/voice``. Any
other entrypoint — ``/api/v1/chat/send`` (the text-chat tab + browser
JS), the Telegram transport — must keep passing ``model=None`` and
``reasoning_level=None`` to ``Brain.respond``.

These tests wire the full stack:
``WebChatTransport → MessageHandler → BrainNull`` (the canonical
test fake which records every call). The route layer above is a
real FastAPI ``TestClient`` so multipart form fields, JSON bodies,
and auth middleware all behave as in production.

If a future refactor accidentally pipes the override into
``/chat/send`` or any other foreground turn, these tests fail loud.
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from core.brain.null import BrainNull
from core.handler import MessageHandler
from core.notify import Notifier
from core.sessions import SessionStore
from core.web_server import DashboardConfig, WebDashboard
from transports.web import WebChatTransport


_TOKEN = "test-token-voice-isolation-cafe"
_ALLOWED_USER_ID = 12345


# ──────────────────────────────────────────────────────────────────
# Stub STT — returns a canned transcript without spawning ffmpeg or
# voxtype. Replaces the route-internal ``stt_provider()`` import so
# /chat/voice short-circuits straight to the chat send path.
# ──────────────────────────────────────────────────────────────────


class _StubSTT:
    name = "stub"

    def __init__(self, transcript: str = "transcribed user audio"):
        self.transcript = transcript

    async def transcribe(self, audio_path: Path) -> str:
        return self.transcript


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def brain() -> BrainNull:
    """Recorder fake. Pre-loaded with responses so multiple turns
    can run without exhausting the canned-reply queue."""
    return BrainNull(responses=[f"reply-{i}" for i in range(20)])


@pytest.fixture
def handler(brain: BrainNull) -> MessageHandler:
    """Real MessageHandler — same code that runs in production. The
    ``allowed_user_id`` is hard-coded so tests can assert the
    transport stamps the right user_id on each call. Workspace
    omitted (None) since these tests don't exercise pinning."""
    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = Path("/dev/null")  # type: ignore[attr-defined]
    sessions._active = "test"  # type: ignore[attr-defined]
    sessions._sessions = {  # type: ignore[attr-defined]
        "test": {"uuid": "00000000-0000-0000-0000-000000000000",
                 "initialized": True,
                 "created_at": "2026-05-08T00:00:00+00:00"},
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
def client(
    chat: WebChatTransport, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Real WebDashboard wired with the live transport + handler so
    /chat/send and /chat/voice round-trip end-to-end. STT is stubbed
    so /chat/voice doesn't try to shell out to ffmpeg."""
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

    # Stub voice + chat-handler context so /chat/voice doesn't 503.
    cfg = tmp_path / "config-voice-on.yaml"
    cfg.write_text(
        "voice:\n  enabled: true\n  stt:\n    provider: stub\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("core.yaml_config._config_path", lambda: cfg)
    # Replace the route's stt_provider lookup with our canned stub.
    monkeypatch.setattr(
        "core.web_server.stt_provider", lambda: _StubSTT(),
    )

    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    return TestClient(dashboard._app)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN}"}


# ──────────────────────────────────────────────────────────────────
# /chat/voice — overrides flow through to brain.respond
# ──────────────────────────────────────────────────────────────────


def test_voice_with_model_override_passes_to_brain(
    client: TestClient, brain: BrainNull,
) -> None:
    r = client.post(
        "/api/v1/chat/voice",
        headers=_auth(),
        files={"audio": ("v.wav", b"fake-audio", "audio/wav")},
        data={"model": "claude-haiku-4-5"},
    )
    assert r.status_code == 200
    # BrainNull tuple shape: (message, chat_id, model, reasoning_level)
    calls = brain.calls()
    assert len(calls) == 1
    _msg, _chat_id, model, reasoning = calls[0]
    assert model == "claude-haiku-4-5"
    assert reasoning is None  # no reasoning_level field in the request


def test_voice_with_model_and_reasoning_passes_both(
    client: TestClient, brain: BrainNull,
) -> None:
    r = client.post(
        "/api/v1/chat/voice",
        headers=_auth(),
        files={"audio": ("v.wav", b"fake-audio", "audio/wav")},
        data={"model": "claude-opus-4-7", "reasoning_level": "high"},
    )
    assert r.status_code == 200
    calls = brain.calls()
    assert len(calls) == 1
    _msg, _chat_id, model, reasoning = calls[0]
    assert model == "claude-opus-4-7"
    assert reasoning == "high"


def test_voice_without_overrides_passes_none(
    client: TestClient, brain: BrainNull,
) -> None:
    r = client.post(
        "/api/v1/chat/voice",
        headers=_auth(),
        files={"audio": ("v.wav", b"fake-audio", "audio/wav")},
    )
    assert r.status_code == 200
    _msg, _chat_id, model, reasoning = brain.calls()[0]
    assert model is None
    assert reasoning is None


def test_voice_empty_string_overrides_treated_as_none(
    client: TestClient, brain: BrainNull,
) -> None:
    """Empty-string fields = "use brain default" sentinel. The
    server must coerce them to None before calling the brain so the
    --model/--effort flags don't get appended with empty values."""
    r = client.post(
        "/api/v1/chat/voice",
        headers=_auth(),
        files={"audio": ("v.wav", b"fake-audio", "audio/wav")},
        data={"model": "", "reasoning_level": "   "},
    )
    assert r.status_code == 200
    _msg, _chat_id, model, reasoning = brain.calls()[0]
    assert model is None
    assert reasoning is None


# ──────────────────────────────────────────────────────────────────
# /chat/send — MUST NOT forward overrides regardless of what the
# voice settings say. This is the user-explicit invariant.
# ──────────────────────────────────────────────────────────────────


def test_chat_send_never_forwards_override(
    client: TestClient, brain: BrainNull, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with voice.call_mode.model SET in config, a regular
    /chat/send call must always pass model=None and reasoning=None
    to the brain. Text chat keeps using the brain's account default;
    only the voice route applies the override."""
    # Set the override in config — and assert it has zero effect
    # on the text-send path.
    cfg = tmp_path / "config-voice-with-override.yaml"
    cfg.write_text(
        "voice:\n"
        "  enabled: true\n"
        "  call_mode:\n"
        "    model: claude-haiku-4-5\n"
        "    reasoning_level: high\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("core.yaml_config._config_path", lambda: cfg)

    r = client.post(
        "/api/v1/chat/send",
        headers=_auth(),
        json={"text": "hello from text chat"},
    )
    assert r.status_code == 200
    calls = brain.calls()
    assert len(calls) == 1
    _msg, _chat_id, model, reasoning = calls[0]
    # The override is set in config but /chat/send never reads it —
    # the route is stateless w.r.t. voice config.
    assert model is None, (
        "/chat/send leaked voice.call_mode.model into a text send"
    )
    assert reasoning is None, (
        "/chat/send leaked voice.call_mode.reasoning_level into a text send"
    )


def test_sequential_voice_then_send_no_leak(
    client: TestClient, brain: BrainNull,
) -> None:
    """Voice call with override, then a text send — the second call
    must NOT inherit the override. Caught by the same assertion as
    above plus a second-call recorder."""
    # Voice with override.
    r1 = client.post(
        "/api/v1/chat/voice",
        headers=_auth(),
        files={"audio": ("v.wav", b"audio", "audio/wav")},
        data={"model": "claude-opus-4-7", "reasoning_level": "max"},
    )
    assert r1.status_code == 200
    # Text send right after.
    r2 = client.post(
        "/api/v1/chat/send",
        headers=_auth(),
        json={"text": "follow-up text"},
    )
    assert r2.status_code == 200

    calls = brain.calls()
    assert len(calls) == 2
    # First call had the override.
    _, _, m1, r1m = calls[0]
    assert m1 == "claude-opus-4-7"
    assert r1m == "max"
    # Second call must be clean.
    _, _, m2, r2m = calls[1]
    assert m2 is None
    assert r2m is None


def test_sequential_voice_with_then_without(
    client: TestClient, brain: BrainNull,
) -> None:
    """Two voice calls: first with override, second without. Second
    must record None for both knobs — no caller-side state leak."""
    r1 = client.post(
        "/api/v1/chat/voice",
        headers=_auth(),
        files={"audio": ("v.wav", b"audio1", "audio/wav")},
        data={"model": "claude-haiku-4-5", "reasoning_level": "low"},
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/api/v1/chat/voice",
        headers=_auth(),
        files={"audio": ("v.wav", b"audio2", "audio/wav")},
        # No data field — represents the "Default" UI selection.
    )
    assert r2.status_code == 200

    calls = brain.calls()
    assert len(calls) == 2
    assert (calls[0][2], calls[0][3]) == ("claude-haiku-4-5", "low")
    assert (calls[1][2], calls[1][3]) == (None, None)


# ──────────────────────────────────────────────────────────────────
# Bare-alias filter — the picker must not surface short aliases
# ──────────────────────────────────────────────────────────────────


def test_alias_filter_strips_bare_short_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The picker drops claude-code aliases that don't carry the
    canonical ``claude-`` prefix. Mirrors the live discovery shape
    where Anthropic returns ``haiku``/``sonnet``/``opus`` alongside
    the dated full IDs."""
    # Mock discovery to return a mix of full IDs + bare aliases.
    monkeypatch.setattr(
        "core.model_discovery.discover_claude_code_models",
        lambda: {
            "claude-haiku-4-5", "claude-opus-4-7", "claude-sonnet-4-6",
            "haiku", "opus", "sonnet",
        },
    )
    monkeypatch.setattr(
        "core.model_discovery.discover_claude_code_capabilities",
        lambda: {},
    )
    out = WebDashboard._voice_call_mode_available_models_static("claude-code")
    ids = sorted(m["id"] for m in out)
    # All "claude-" prefixed entries survive; bare aliases gone.
    assert ids == ["claude-haiku-4-5", "claude-opus-4-7", "claude-sonnet-4-6"]
    assert "haiku" not in ids
    assert "opus" not in ids
    assert "sonnet" not in ids
