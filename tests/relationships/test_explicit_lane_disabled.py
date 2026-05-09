"""v3c Day 4a: explicit-consent fast lane is runtime-disabled by default.

Per the v3c research-doc patch (§6 — `e2c6155`):

- ``relationships.explicit_consent_enabled`` is a new YAML config
  flag, default ``false``.
- When false, ``transports/telegram.py:_run_relationships_hook``
  short-circuits at function entry — zero per-message cost. The
  legacy v3a/v3b explicit path never runs.
- When true, the v3a/v3b explicit path runs as designed (this is
  v3b's existing test surface, gated on the flag in fixtures).

This test set covers the default-OFF behavior. v3b tests that
exercise the explicit path opt the flag on via a per-test
monkeypatch (see ``tests/relationships/test_telegram_handoff.py``
for examples).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from vexis_agent.core.handler import MessageHandler
from vexis_agent.core.running_tasks import RunningTasks


class _FakeBrain:
    async def respond(self, message: str, chat_id: int) -> str:
        return "brain-reply"


class _FakeSessionStore:
    def __init__(self, uuid: str = "real-session"):
        self._uuid = uuid

    def get(self) -> str:
        return self._uuid


class _FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []

    async def send_chat_action(self, *_a, **_k) -> None:
        return None

    async def send_message(
        self, *, chat_id: int, text: str, parse_mode: Any = None, **_kw: Any,
    ) -> None:
        self.sent_messages.append((chat_id, text))


class _RecordingRelationships:
    """Records every call so the test can assert it was NEVER
    invoked when the flag is off."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.counter_increments: list[str] = []

    async def process_user_turn(
        self,
        text: str,
        *,
        session_uuid: str,
        turn_index: int,
        chat_id: int | None = None,
    ):
        self.calls.append((session_uuid, turn_index, chat_id))
        from vexis_agent.core.relationships.curator import TurnLevelResult
        return TurnLevelResult(staged=False, reply_text=None)

    def increment_counter(self, name: str, by: int = 1) -> None:
        for _ in range(by):
            self.counter_increments.append(name)


class _FakeLearningController:
    def __init__(self, relationships) -> None:
        self.relationships_curator = relationships


def _build_transport(
    handler: MessageHandler, relationships
):
    from vexis_agent.transports.telegram import TelegramTransport
    t = TelegramTransport.__new__(TelegramTransport)
    t._handler = handler  # type: ignore[attr-defined]
    t._allowed_user_id = 99  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    t._learning_curator = _FakeLearningController(relationships)  # type: ignore[attr-defined]
    return t


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _build_handler(workspace: Path) -> MessageHandler:
    return MessageHandler(
        brain=_FakeBrain(),  # type: ignore[arg-type]
        sessions=_FakeSessionStore(),  # type: ignore[arg-type]
        allowed_user_id=99,
        notifier=None,
        workspace=workspace,
    )


# ---------------------------------------------------------------- default off


def test_flag_default_is_false():
    from vexis_agent.core.yaml_config import relationships_explicit_consent_enabled
    assert relationships_explicit_consent_enabled() is False


def test_explicit_lane_disabled_short_circuits_hook(workspace: Path):
    """With the flag off (default), inbound user message containing
    an explicit-consent phrasing → hook short-circuits → no
    detector call, no shadow write, message proceeds to brain."""
    handler = _build_handler(workspace)

    received_text: list[str] = []

    async def patched_handle(user_id: int, chat_id: int, text: str):
        received_text.append(text)
        return "brain-reply"

    handler.handle = patched_handle  # type: ignore[assignment]
    relationships = _RecordingRelationships()
    transport = _build_transport(handler, relationships)
    bot = _FakeBot()

    asyncio.run(
        transport._dispatch_to_brain(
            bot, 42, 99, "remember Sarah likes mystery novels"
        )
    )

    # Relationships never invoked.
    assert relationships.calls == []
    # No staged-ack, no relationships-side bot message — only the
    # brain reply.
    sent = [text for cid, text in bot.sent_messages]
    assert sent == ["brain-reply"]
    # Brain saw the user's verbatim text (no detector intercepted).
    assert received_text == ["remember Sarah likes mystery novels"]


def test_explicit_lane_enabled_invokes_relationships(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
):
    """With the flag on, the legacy v3b explicit path engages —
    relationships is called, cursor is claimed."""
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.relationships_explicit_consent_enabled",
        lambda: True,
    )
    handler = _build_handler(workspace)

    async def patched_handle(user_id: int, chat_id: int, text: str):
        return "brain-reply"

    handler.handle = patched_handle  # type: ignore[assignment]
    relationships = _RecordingRelationships()
    transport = _build_transport(handler, relationships)
    bot = _FakeBot()

    asyncio.run(
        transport._dispatch_to_brain(
            bot, 42, 99, "remember Sarah likes mystery novels"
        )
    )

    # Hook fired — exactly one call (one drain iteration).
    assert len(relationships.calls) == 1
    session_uuid, turn_index, chat_id = relationships.calls[0]
    assert session_uuid == "real-session"
    assert turn_index == 1
    assert chat_id == 42


def test_yaml_flag_explicit_value_overrides_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Direct YAML round-trip: writing
    ``relationships.explicit_consent_enabled: true`` to the
    config yields True from the helper."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    cfg_path = cfg_dir / "config.yaml"
    cfg_path.write_text(
        "relationships:\n  explicit_consent_enabled: true\n",
        encoding="utf-8",
    )
    # Point yaml_config's reader at this fixture file.
    monkeypatch.setattr("vexis_agent.core.yaml_config._config_path", lambda: cfg_path)
    # Bypass any caching by re-importing.
    from vexis_agent.core import yaml_config as yc
    # _read_raw caches by mtime; reset cache via private attribute
    # if it exists, otherwise just call.
    raw = yc._read_raw()
    assert isinstance(raw, dict)
    assert yc.relationships_explicit_consent_enabled() is True
