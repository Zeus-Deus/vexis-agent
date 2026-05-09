"""Tests for ``MessageHandler.handle_history`` — name→uuid lookup,
brain.iter_messages integration, message-shape conversion, limit
slicing, auth gating.

The brain layer is well-tested at the integration level
(test_brain_contract.py exercises ``BrainNull.iter_messages``;
the live brains have their own smoke tests). Here we focus on the
handler-level glue: does it look up the right uuid by name, drain
the iterator, slice by limit, and convert TranscriptMessage to the
{role, content, ts} wire format the dashboard route expects?
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest

from core.brain.null import BrainNull
from core.handler import MessageHandler
from core.sessions import SessionStore
from core.transcripts import TranscriptMessage


_ALLOWED_USER_ID = 12345


def _msg(role: str, text: str, *, year: int = 2026, month: int = 5, day: int = 9) -> TranscriptMessage:
    """Build a TranscriptMessage with sensible defaults. Tests only
    care about role, text, and timestamp ordering."""
    return TranscriptMessage(
        role=role,
        text=text,
        timestamp=datetime(year, month, day, tzinfo=timezone.utc),
        uuid=f"msg-{text[:6]}",
        tool_calls=(),
        raw={},
    )


@pytest.fixture
def session_store(tmp_path: Path) -> SessionStore:
    """Real SessionStore with one named session 'work'. UUID is
    fixed so tests can assert the brain receives it."""
    store = SessionStore(tmp_path / "sessions.json")
    # Replace the auto-created session with one we can pin.
    store._sessions = {  # type: ignore[attr-defined]
        "work": {
            "uuid": "work-uuid-fixture",
            "initialized": True,
            "created_at": "2026-05-09T00:00:00+00:00",
        },
        "side": {
            "uuid": "side-uuid-fixture",
            "initialized": True,
            "created_at": "2026-05-08T00:00:00+00:00",
        },
    }
    store._active = "work"  # type: ignore[attr-defined]
    return store


@pytest.fixture
def brain() -> BrainNull:
    return BrainNull()


@pytest.fixture
def handler(brain: BrainNull, session_store: SessionStore) -> MessageHandler:
    return MessageHandler(
        brain=brain,
        sessions=session_store,
        allowed_user_id=_ALLOWED_USER_ID,
        notifier=None,
    )


def test_history_returns_role_content_ts(
    handler: MessageHandler, brain: BrainNull,
) -> None:
    """Happy path: brain yields three messages, handler converts
    each to {role, content, ts} with ts in unix milliseconds."""
    messages = [
        _msg("user", "hi", day=1),
        _msg("assistant", "hello", day=2),
        _msg("user", "again", day=3),
    ]

    def fake_iter(uuid: str) -> Iterator[TranscriptMessage]:
        assert uuid == "work-uuid-fixture"
        yield from messages

    with patch.object(brain, "iter_messages", side_effect=fake_iter):
        result = handler.handle_history(_ALLOWED_USER_ID, "work")

    assert result is not None
    assert len(result) == 3
    assert result[0] == {
        "role": "user",
        "content": "hi",
        "ts": int(messages[0].timestamp.timestamp() * 1000),
    }
    assert result[1]["role"] == "assistant"
    assert result[1]["content"] == "hello"
    assert result[2]["content"] == "again"


def test_history_limit_takes_latest(
    handler: MessageHandler, brain: BrainNull,
) -> None:
    """Limit applies to the LATEST N messages, not the first N.
    Mirrors a chat UI loading 'recent history'."""
    messages = [_msg("user", f"msg-{i}", day=i) for i in range(1, 11)]  # 10 messages

    with patch.object(brain, "iter_messages", return_value=iter(messages)):
        result = handler.handle_history(_ALLOWED_USER_ID, "work", limit=3)

    assert result is not None
    contents = [m["content"] for m in result]
    assert contents == ["msg-8", "msg-9", "msg-10"]


def test_history_empty_session_returns_empty_list(
    handler: MessageHandler, brain: BrainNull,
) -> None:
    """Brand-new session that's never been written to → empty
    iterator → empty list. NOT None — None is reserved for auth
    rejection."""
    with patch.object(brain, "iter_messages", return_value=iter([])):
        result = handler.handle_history(_ALLOWED_USER_ID, "work")

    assert result == []


def test_history_unknown_session_returns_empty_list(
    handler: MessageHandler, brain: BrainNull,
) -> None:
    """Unknown name → empty list (not None). Lets the route return
    200 with empty messages so the UI treats unknown the same as
    'pristine new session'.

    Brain.iter_messages is NOT called when the name lookup misses
    — patched here to confirm by raising on call."""
    with patch.object(
        brain, "iter_messages",
        side_effect=AssertionError("brain should not be consulted for unknown name"),
    ):
        result = handler.handle_history(
            _ALLOWED_USER_ID, "never-existed",
        )

    assert result == []


def test_history_skips_messages_with_empty_text(
    handler: MessageHandler, brain: BrainNull,
) -> None:
    """Tool-call-only assistant turns have empty .text — they'd
    render as blank bubbles. Filtered out so the chat UI shows
    only meaningful turns."""
    messages = [
        _msg("user", "what's 2+2"),
        # Tool-call-only turn, empty text.
        TranscriptMessage(
            role="assistant", text="", timestamp=datetime(2026, 5, 9, 1, tzinfo=timezone.utc),
            uuid="tool-only", tool_calls=({"name": "calc"},), raw={},
        ),
        _msg("assistant", "4", day=10),
    ]

    with patch.object(brain, "iter_messages", return_value=iter(messages)):
        result = handler.handle_history(_ALLOWED_USER_ID, "work")

    assert result is not None
    contents = [m["content"] for m in result]
    assert contents == ["what's 2+2", "4"]


def test_history_rejects_disallowed_user(
    handler: MessageHandler, brain: BrainNull,
) -> None:
    """Wrong user_id → None (route translates to 401). Must NOT
    consult the brain — defensive against auth drift."""
    with patch.object(
        brain, "iter_messages",
        side_effect=AssertionError("brain consulted on rejected user"),
    ):
        result = handler.handle_history(99999, "work")

    assert result is None


def test_history_with_zero_or_negative_limit_returns_empty(
    handler: MessageHandler, brain: BrainNull,
) -> None:
    """limit <= 0 short-circuits to empty without touching the
    brain. Defensive against a route layer that forgot to clamp."""
    with patch.object(
        brain, "iter_messages",
        side_effect=AssertionError("brain consulted with non-positive limit"),
    ):
        assert handler.handle_history(_ALLOWED_USER_ID, "work", limit=0) == []
        assert handler.handle_history(_ALLOWED_USER_ID, "work", limit=-5) == []


def test_history_resolves_uuid_from_session_name(
    handler: MessageHandler, brain: BrainNull,
) -> None:
    """The handler uses SessionStore.list() to map name → uuid.
    Confirm the right uuid is what gets passed to iter_messages."""
    captured_uuids: list[str] = []

    def capture_uuid(uuid: str) -> Iterator[TranscriptMessage]:
        captured_uuids.append(uuid)
        return iter([])

    with patch.object(brain, "iter_messages", side_effect=capture_uuid):
        handler.handle_history(_ALLOWED_USER_ID, "work")
        handler.handle_history(_ALLOWED_USER_ID, "side")

    assert captured_uuids == ["work-uuid-fixture", "side-uuid-fixture"]
