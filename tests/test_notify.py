"""Tests for core/notify.py.

The notifier owns two halves: send to Telegram and buffer the same
event as a context note for the next brain turn. We stub the PTB app
with a tiny FakeApp/FakeBot that records ``send_message`` calls so the
tests can assert both halves fire and buffer correctly.

Tests follow the codebase convention of sync test functions calling
asyncio.run() rather than pytest-asyncio.
"""

from __future__ import annotations

import asyncio
from typing import Any

from vexis_agent.core.notify import Notifier


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Any]] = []

    async def send_message(
        self, *, chat_id: int, text: str, parse_mode: Any = None, **_kwargs: Any
    ) -> None:
        self.sent.append((chat_id, text, parse_mode))


class FakeApp:
    def __init__(self) -> None:
        self.bot = FakeBot()


def test_send_buffers_and_delivers():
    notifier = Notifier()
    app = FakeApp()
    notifier.bind_app(app)

    async def scenario() -> list:
        await notifier.send(42, "hello")
        return await notifier.consume_context(42)

    notes = asyncio.run(scenario())
    assert len(notes) == 1
    assert notes[0].text == "hello"
    assert app.bot.sent == [(42, "hello", "Markdown")]


def test_consume_clears_buffer():
    notifier = Notifier()
    notifier.bind_app(FakeApp())

    async def scenario() -> tuple[list, list]:
        await notifier.append_context(1, "a")
        await notifier.append_context(1, "b")
        first = await notifier.consume_context(1)
        second = await notifier.consume_context(1)
        return first, second

    first, second = asyncio.run(scenario())
    assert [n.text for n in first] == ["a", "b"]
    assert second == []


def test_buffer_is_per_chat():
    notifier = Notifier()
    notifier.bind_app(FakeApp())

    async def scenario() -> tuple[list, list]:
        await notifier.append_context(1, "for-1")
        await notifier.append_context(2, "for-2")
        return (
            await notifier.consume_context(1),
            await notifier.consume_context(2),
        )

    one, two = asyncio.run(scenario())
    assert [n.text for n in one] == ["for-1"]
    assert [n.text for n in two] == ["for-2"]


def test_notes_preserve_chronological_order():
    notifier = Notifier()
    notifier.bind_app(FakeApp())

    async def scenario() -> list:
        await notifier.send(7, "first")
        # Force a small gap so the timestamps are distinct.
        await asyncio.sleep(0.01)
        await notifier.send(7, "second")
        return await notifier.consume_context(7)

    notes = asyncio.run(scenario())
    assert [n.text for n in notes] == ["first", "second"]
    assert notes[0].timestamp <= notes[1].timestamp


def test_send_buffers_even_when_app_unbound():
    """Buffering is independent of Telegram delivery — the brain still
    deserves to know about an event the user *would have* seen."""
    notifier = Notifier()  # never bound

    async def scenario() -> list:
        await notifier.send(99, "alert!")
        return await notifier.consume_context(99)

    notes = asyncio.run(scenario())
    assert [n.text for n in notes] == ["alert!"]


def test_markdown_failure_falls_back_to_plain():
    notifier = Notifier()
    app = FakeApp()
    notifier.bind_app(app)
    fail_first = {"count": 0}

    async def flaky_send_message(
        *, chat_id: int, text: str, parse_mode: Any = None, **_kwargs: Any
    ) -> None:
        fail_first["count"] += 1
        if parse_mode == "Markdown":
            raise RuntimeError("bad markdown")
        app.bot.sent.append((chat_id, text, parse_mode))

    app.bot.send_message = flaky_send_message  # type: ignore[assignment]

    async def scenario() -> list:
        await notifier.send(5, "stray ` backtick")
        return await notifier.consume_context(5)

    notes = asyncio.run(scenario())
    # Buffered exactly once even though delivery had to retry.
    assert [n.text for n in notes] == ["stray ` backtick"]
    # And the plain-text fallback delivered the message.
    assert app.bot.sent == [(5, "stray ` backtick", None)]
