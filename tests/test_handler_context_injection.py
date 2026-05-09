"""Tests for context injection in core/handler.py::MessageHandler.

The handler should consume any pending notifier context for a chat and
prepend it to the user's message in a [SYSTEM CONTEXT] / [USER MESSAGE]
envelope before asking the brain to respond. Empty buffer = passthrough.

Tests follow the codebase convention of sync test functions calling
asyncio.run() rather than pytest-asyncio.
"""

from __future__ import annotations

import asyncio
from typing import Any

from vexis_agent.core.handler import MessageHandler
from vexis_agent.core.notify import Notifier


class FakeBrain:
    def __init__(self) -> None:
        self.last_message: str | None = None
        self.calls: int = 0

    async def respond(
        self,
        message: str,
        chat_id: int,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
    ) -> str:
        self.last_message = message
        self.last_model = model
        self.last_reasoning_level = reasoning_level
        self.calls += 1
        return "ok"


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(
        self, *, chat_id: int, text: str, parse_mode: Any = None, **_kwargs: Any
    ) -> None:
        self.sent.append((chat_id, text))


class FakeApp:
    def __init__(self) -> None:
        self.bot = FakeBot()


_USER = 99
_CHAT = 100


def _build_handler(
    brain: FakeBrain, notifier: Notifier | None = None
) -> MessageHandler:
    return MessageHandler(
        brain=brain,
        sessions=object(),  # handle() doesn't touch sessions
        allowed_user_id=_USER,
        notifier=notifier,
    )


def test_passthrough_when_no_pending_notes():
    notifier = Notifier()
    notifier.bind_app(FakeApp())
    brain = FakeBrain()
    handler = _build_handler(brain, notifier)

    async def scenario() -> None:
        await handler.handle(_USER, _CHAT, "hello")

    asyncio.run(scenario())
    assert brain.last_message == "hello"


def test_pending_note_is_prepended_as_system_context():
    notifier = Notifier()
    notifier.bind_app(FakeApp())
    brain = FakeBrain()
    handler = _build_handler(brain, notifier)

    async def scenario() -> None:
        await notifier.send(_CHAT, "✅ Background task `repo-tour` finished.")
        await handler.handle(_USER, _CHAT, "yea")

    asyncio.run(scenario())
    msg = brain.last_message or ""
    assert "[SYSTEM CONTEXT" in msg
    assert "repo-tour" in msg
    assert "[USER MESSAGE]" in msg
    assert msg.endswith("yea")
    # The system context block precedes the user message.
    assert msg.index("[SYSTEM CONTEXT") < msg.index("[USER MESSAGE]")


def test_consume_clears_buffer_after_first_call():
    notifier = Notifier()
    notifier.bind_app(FakeApp())
    brain = FakeBrain()
    handler = _build_handler(brain, notifier)

    async def scenario() -> tuple[str, str]:
        await notifier.send(_CHAT, "alert!")
        await handler.handle(_USER, _CHAT, "first")
        msg_first = brain.last_message or ""
        await handler.handle(_USER, _CHAT, "second")
        msg_second = brain.last_message or ""
        return msg_first, msg_second

    first, second = asyncio.run(scenario())
    assert "alert!" in first
    assert "alert!" not in second
    assert second == "second"


def test_multiple_notes_render_in_chronological_order():
    notifier = Notifier()
    notifier.bind_app(FakeApp())
    brain = FakeBrain()
    handler = _build_handler(brain, notifier)

    async def scenario() -> None:
        await notifier.send(_CHAT, "first event")
        await asyncio.sleep(0.01)
        await notifier.send(_CHAT, "second event")
        await handler.handle(_USER, _CHAT, "what now")

    asyncio.run(scenario())
    msg = brain.last_message or ""
    first_idx = msg.find("first event")
    second_idx = msg.find("second event")
    assert first_idx > 0
    assert second_idx > first_idx


def test_other_chats_buffer_does_not_leak_into_this_call():
    notifier = Notifier()
    notifier.bind_app(FakeApp())
    brain = FakeBrain()
    handler = _build_handler(brain, notifier)

    async def scenario() -> None:
        await notifier.send(7777, "for someone else")
        await handler.handle(_USER, _CHAT, "hi")

    asyncio.run(scenario())
    msg = brain.last_message or ""
    assert "for someone else" not in msg
    assert msg == "hi"


def test_handler_without_notifier_is_unchanged():
    brain = FakeBrain()
    handler = _build_handler(brain, notifier=None)

    async def scenario() -> None:
        await handler.handle(_USER, _CHAT, "plain")

    asyncio.run(scenario())
    assert brain.last_message == "plain"


def test_disallowed_user_skips_brain_and_does_not_consume():
    notifier = Notifier()
    notifier.bind_app(FakeApp())
    brain = FakeBrain()
    handler = _build_handler(brain, notifier)

    async def scenario() -> int:
        await notifier.send(_CHAT, "queued event")
        # Wrong user id — handler should reject and never reach the brain.
        await handler.handle(user_id=12345, chat_id=_CHAT, text="hi")
        # Buffer should still be intact for the legitimate user.
        await handler.handle(_USER, _CHAT, "real reply")
        return brain.calls

    calls = asyncio.run(scenario())
    assert calls == 1
    msg = brain.last_message or ""
    assert "queued event" in msg
