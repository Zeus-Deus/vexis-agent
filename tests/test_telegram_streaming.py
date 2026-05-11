"""Tests for the Telegram streaming reply path.

Covers ``TelegramTransport._send_brain_reply_streaming`` and the
``_dispatch_brain_turn`` router that picks streaming vs buffered.

Pin: 13 streaming-side cases (placeholder, edits, throttle, rollover,
done sentinel, error path, screenshot extraction, disabled fallback,
empty response, dispatcher exception). Plus 5 cases on the
``_split_at_streaming_boundary`` helper. Counts drift; this docstring
tracks the truth at write time, not the runtime.

Tests follow the codebase convention of sync test functions calling
``asyncio.run()`` rather than pytest-asyncio, matching the style in
``tests/test_telegram_transport.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.transports.telegram import (
    _STREAMING_PLACEHOLDER,
    _STREAMING_ROLLOVER_THRESHOLD,
    TelegramTransport,
    _split_at_streaming_boundary,
)


# ────────────────────────── boundary helper ──────────────────────────


def test_boundary_returns_input_when_below_threshold():
    head, tail = _split_at_streaming_boundary("hello world", 100)
    assert head == "hello world"
    assert tail == ""


def test_boundary_prefers_paragraph_break():
    text = "first paragraph\n\nsecond paragraph that overflows"
    head, tail = _split_at_streaming_boundary(text, 30)
    assert head == "first paragraph"
    assert tail == "second paragraph that overflows"


def test_boundary_falls_back_to_line_break():
    # No "\n\n" in the window → line break wins.
    text = "line one with stuff\nline two also with stuff"
    head, tail = _split_at_streaming_boundary(text, 25)
    assert head == "line one with stuff"
    assert tail == "line two also with stuff"


def test_boundary_hard_cuts_when_no_break_available():
    text = "a" * 100
    head, tail = _split_at_streaming_boundary(text, 40)
    assert head == "a" * 40
    assert tail == "a" * 60


def test_boundary_keeps_head_at_or_under_threshold():
    text = "abcdefg\n\nabcdefg\n\n" + "a" * 100
    head, tail = _split_at_streaming_boundary(text, 25)
    # 25-char window contains two paragraph breaks at idx 7 and idx
    # 16; rfind picks the rightmost. Head must not exceed threshold.
    assert len(head) <= 25
    assert tail.startswith("a" * 100) or tail == "abcdefg" + "\n\n" + "a" * 100


# ───────────────────────── streaming fakes ───────────────────────────


_USER = 99
_CHAT = 42


class _FakeMessage:
    """Stand-in for ``telegram.Message`` returned by ``send_message``."""

    def __init__(self, message_id: int, chat_id: int, text: str) -> None:
        self.message_id = message_id
        self.chat_id = chat_id
        self.text = text


class _StreamingBot:
    """Records every send/edit/photo for streaming-path assertions.

    Mirrors ``_FakeBot`` from test_telegram_transport.py but adds
    ``edit_message_text`` (which the buffered path never calls) and
    returns a Message-shaped object from ``send_message`` so the
    transport can capture ``message_id`` for subsequent edits.
    """

    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []
        self.edits: list[tuple[int, int, str]] = []
        self.photos: list[tuple[int, Path]] = []
        self.documents: list[tuple[int, Path]] = []
        self.typing_calls = 0
        self._next_msg_id = 1000

    async def send_chat_action(self, _chat_id: int, _action: Any) -> None:
        self.typing_calls += 1

    async def send_message(
        self, *, chat_id: int, text: str, parse_mode: Any = None, **_kw: Any,
    ) -> _FakeMessage:
        self.sent_messages.append((chat_id, text))
        msg_id = self._next_msg_id
        self._next_msg_id += 1
        return _FakeMessage(msg_id, chat_id, text)

    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: Any = None,
        **_kw: Any,
    ) -> None:
        self.edits.append((chat_id, message_id, text))


class _StreamingHandler:
    """Async-iterable stub for ``MessageHandler.stream``.

    Construct with a list of events; ``stream(...)`` replays them.
    Tracks the last ``(user_id, chat_id, text)`` triple it was called
    with so tests can assert the dispatch shape.
    """

    def __init__(self, events: list[tuple[str, Any]]) -> None:
        self._events = events
        self.calls: list[tuple[int, int, str]] = []

    async def stream(self, user_id: int, chat_id: int, text: str):
        self.calls.append((user_id, chat_id, text))
        for ev in self._events:
            yield ev

    # ``handle`` is exercised by the buffered fallback test below.
    async def handle(self, user_id: int, chat_id: int, text: str) -> str | None:
        self.calls.append((user_id, chat_id, text))
        return "buffered reply"


def _make_streaming_transport(
    handler: _StreamingHandler,
    *,
    streaming_enabled: bool = True,
    min_interval: float = 0.0,
) -> TelegramTransport:
    """Build a TelegramTransport bypassing __init__ so we can wire
    just the attributes the streaming path reads.

    ``min_interval=0.0`` disables the edit throttle so tests can
    deterministically assert one edit per chunk without sleeping.
    """
    t = TelegramTransport.__new__(TelegramTransport)
    t._handler = handler  # type: ignore[attr-defined]
    t._allowed_user_id = _USER  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    t._learning_curator = None  # type: ignore[attr-defined]
    t._streaming_enabled = streaming_enabled  # type: ignore[attr-defined]
    t._streaming_min_interval = min_interval  # type: ignore[attr-defined]
    return t


# ───────────────── _send_brain_reply_streaming behaviour ─────────────


def test_streaming_sends_placeholder_first():
    """Even before any chunks arrive, the user sees the placeholder
    so the bot feels responsive while the brain spins up."""
    handler = _StreamingHandler([("done", "")])
    transport = _make_streaming_transport(handler)
    bot = _StreamingBot()

    asyncio.run(
        transport._send_brain_reply_streaming(bot, _CHAT, _USER, "hi")
    )

    # Placeholder is the first send.
    assert bot.sent_messages
    assert bot.sent_messages[0] == (_CHAT, _STREAMING_PLACEHOLDER)


def test_streaming_edits_placeholder_with_first_chunk():
    """First chunk bypasses the throttle so the user sees text
    immediately rather than waiting a full interval."""
    handler = _StreamingHandler(
        [("chunk", "Hello"), ("done", "Hello")],
    )
    transport = _make_streaming_transport(handler, min_interval=10.0)
    bot = _StreamingBot()

    asyncio.run(
        transport._send_brain_reply_streaming(bot, _CHAT, _USER, "hi")
    )

    # Edit fired despite a 10s throttle because last_edit_at == 0.
    assert any(text == "Hello" for _, _, text in bot.edits)


def test_streaming_chunks_accumulate_in_active_message():
    """Chunks concatenate into one bubble until rollover; with
    min_interval=0 every chunk produces an edit."""
    handler = _StreamingHandler(
        [
            ("chunk", "Hello "),
            ("chunk", "world"),
            ("chunk", "!"),
            ("done", "Hello world!"),
        ],
    )
    transport = _make_streaming_transport(handler, min_interval=0.0)
    bot = _StreamingBot()

    final = asyncio.run(
        transport._send_brain_reply_streaming(bot, _CHAT, _USER, "hi")
    )

    edits_text = [text for _, _, text in bot.edits]
    assert "Hello " in edits_text
    assert "Hello world" in edits_text
    assert "Hello world!" in edits_text
    # Same message_id for every edit (no rollover).
    msg_ids = {mid for _, mid, _ in bot.edits}
    assert len(msg_ids) == 1
    assert final == "Hello world!"


def test_streaming_throttle_skips_intermediate_edits():
    """When chunks land faster than min_interval, only the first +
    final edits should fire (the final-flush at done time is the
    safety net that guarantees the user sees the full text)."""
    handler = _StreamingHandler(
        [
            ("chunk", "a"),
            ("chunk", "b"),
            ("chunk", "c"),
            ("chunk", "d"),
            ("done", "abcd"),
        ],
    )
    # 60s throttle — only the first chunk's edit should land
    # mid-stream, then the final flush at done writes the rest.
    transport = _make_streaming_transport(handler, min_interval=60.0)
    bot = _StreamingBot()

    asyncio.run(
        transport._send_brain_reply_streaming(bot, _CHAT, _USER, "hi")
    )

    # First-chunk edit: "a". Final-flush edit: "abcd". Anything in
    # between would mean the throttle leaked.
    edits_text = [text for _, _, text in bot.edits]
    assert "a" in edits_text
    assert "abcd" in edits_text
    # Mid-stream "ab" / "abc" should NOT have fired — they're the
    # tell-tale leak.
    assert "ab" not in edits_text
    assert "abc" not in edits_text


def test_streaming_rollover_starts_new_message():
    """When the active message would exceed the threshold, the
    transport seals it and starts a fresh bubble for the rest."""
    big = "x" * (_STREAMING_ROLLOVER_THRESHOLD + 100)
    handler = _StreamingHandler(
        [("chunk", big), ("done", big)],
    )
    transport = _make_streaming_transport(handler, min_interval=0.0)
    bot = _StreamingBot()

    asyncio.run(
        transport._send_brain_reply_streaming(bot, _CHAT, _USER, "hi")
    )

    # Two send_messages: original placeholder + post-rollover bubble.
    sends = [text for _, text in bot.sent_messages]
    assert sends.count(_STREAMING_PLACEHOLDER) >= 1
    # At least two distinct message_ids appear in edits or sends —
    # rollover means a new send_message call before subsequent edits.
    distinct_ids = {mid for _, mid, _ in bot.edits}
    # The rolled-over bubble starts fresh; subsequent edits target
    # its message_id. With min_interval=0 we should see at least
    # one edit on the new message.
    assert len(bot.sent_messages) >= 2
    # The combined text across all bubbles equals the input.
    # (The exact split depends on the boundary helper; we just
    # check no chars were dropped.)
    rendered = sum(len(text) for _, _, text in bot.edits if text != _STREAMING_PLACEHOLDER)
    rendered += sum(len(text) for _, text in bot.sent_messages if text != _STREAMING_PLACEHOLDER)
    # ``rendered`` counts chars across the latest state of each
    # message; with min_interval=0 the final state per bubble is
    # the full content, so total >= input length.
    assert rendered >= len(big)
    assert distinct_ids, "expected at least one edit on the rolled-over bubble"


def test_streaming_done_sentinel_overrides_local_accumulation():
    """If the handler's ``done`` payload differs from the
    accumulated chunks (e.g. trim, empty-response substitution),
    the done payload wins — it's the canonical reply."""
    handler = _StreamingHandler(
        [
            ("chunk", "raw "),
            ("chunk", "draft"),
            ("done", "FINAL"),
        ],
    )
    transport = _make_streaming_transport(handler, min_interval=0.0)
    bot = _StreamingBot()

    final = asyncio.run(
        transport._send_brain_reply_streaming(bot, _CHAT, _USER, "hi")
    )
    assert final == "FINAL"


def test_streaming_error_event_replaces_bubble_with_error_text():
    """``("error", {"code":..., "message":...})`` swaps the
    placeholder for the user-facing error string and stops."""
    handler = _StreamingHandler(
        [
            ("chunk", "starting "),
            ("error", {"code": "brain_error", "message": "Something broke."}),
        ],
    )
    transport = _make_streaming_transport(handler, min_interval=0.0)
    bot = _StreamingBot()

    final = asyncio.run(
        transport._send_brain_reply_streaming(bot, _CHAT, _USER, "hi")
    )

    assert final == "Something broke."
    # The error message was edited into the bubble.
    edits_text = [text for _, _, text in bot.edits]
    assert "Something broke." in edits_text


def test_streaming_cancelled_error_stays_silent():
    """Empty-message error payload (the cancelled case — /cancel
    handler is the source of truth for the user-visible ack)
    should NOT edit the bubble with empty text (Telegram refuses
    that anyway). Returns empty string."""
    handler = _StreamingHandler(
        [
            ("chunk", "doing "),
            ("error", {"code": "cancelled", "message": ""}),
        ],
    )
    transport = _make_streaming_transport(handler, min_interval=0.0)
    bot = _StreamingBot()

    final = asyncio.run(
        transport._send_brain_reply_streaming(bot, _CHAT, _USER, "hi")
    )

    assert final == ""
    # No edit went through with empty text.
    assert all(text != "" for _, _, text in bot.edits)


def test_streaming_empty_response_renders_marker():
    """No chunks + done with empty payload → user sees the same
    ``(empty response)`` marker the buffered path uses."""
    handler = _StreamingHandler([("done", "")])
    transport = _make_streaming_transport(handler, min_interval=0.0)
    bot = _StreamingBot()

    final = asyncio.run(
        transport._send_brain_reply_streaming(bot, _CHAT, _USER, "hi")
    )

    assert final == "(empty response)"
    edits_text = [text for _, _, text in bot.edits]
    assert "(empty response)" in edits_text


def test_streaming_screenshot_path_is_stripped_and_photo_sent(tmp_path):
    """When the brain references an ephemeral screenshot path in
    its reply, the path token is removed from the visible message
    and the file is sent as a separate photo AFTER the text.

    UX trade-off vs the buffered path (which sends photos BEFORE
    the text): documented in docs/telegram-streaming.md.
    """
    # The transport's screenshot extractor matches the literal path
    # ``/tmp/vexis-screenshot-<digits>.png``. Make a real one so
    # ``path.is_file()`` passes.
    screenshot = Path("/tmp/vexis-screenshot-9999999.png")
    screenshot.write_bytes(b"fake png bytes")
    try:
        body = f"Screenshot saved: {screenshot} — see attached."
        handler = _StreamingHandler(
            [("chunk", body), ("done", body)],
        )
        transport = _make_streaming_transport(handler, min_interval=0.0)
        bot = _StreamingBot()

        asyncio.run(
            transport._send_brain_reply_streaming(bot, _CHAT, _USER, "hi")
        )

        # Cleaned text appears as the final edit; the literal path
        # token does NOT appear in the final-flush edit.
        final_edits = [text for _, _, text in bot.edits]
        # The last edit (cleaned tail) must not contain the path.
        assert final_edits, "expected at least one edit"
        assert str(screenshot) not in final_edits[-1]
        # Photo fired exactly once for the screenshot.
        assert len(bot.photos) == 1
        assert bot.photos[0][0] == _CHAT
    finally:
        # Ephemeral screenshots get unlinked by the transport on
        # success; defend against a test-side leak just in case.
        screenshot.unlink(missing_ok=True)


# Wire the photo capture: _StreamingBot doesn't expose
# send_photo / send_document by default; add them via monkey-patch
# at module scope so the screenshot test above sees the call.


async def _streaming_bot_send_photo(self, *, chat_id: int, photo: Any, **_kw: Any) -> None:
    # `photo` is an open file handle in production; tests pass the
    # same. We record the path via the file's ``.name`` attribute.
    path = Path(getattr(photo, "name", "/tmp/unknown.png"))
    self.photos.append((chat_id, path))


async def _streaming_bot_send_document(
    self, *, chat_id: int, document: Any, filename: str, **_kw: Any,
) -> None:
    self.documents.append((chat_id, Path(filename)))


_StreamingBot.send_photo = _streaming_bot_send_photo  # type: ignore[attr-defined]
_StreamingBot.send_document = _streaming_bot_send_document  # type: ignore[attr-defined]


# ─────────────── _dispatch_brain_turn router behaviour ───────────────


def test_dispatcher_routes_to_buffered_path_when_streaming_disabled():
    """``streaming_enabled = False`` → original ``handler.handle``
    + ``_send_brain_reply`` codepath. No edits fire."""
    handler = _StreamingHandler([])  # stream() never called
    transport = _make_streaming_transport(handler, streaming_enabled=False)
    bot = _StreamingBot()

    final = asyncio.run(
        transport._dispatch_brain_turn(bot, _CHAT, _USER, "hi")
    )

    assert final == "buffered reply"
    # Buffered path used send_message, not edit_message_text.
    assert bot.edits == []
    sends = [text for _, text in bot.sent_messages]
    assert "buffered reply" in sends


def test_dispatcher_routes_to_streaming_path_when_enabled():
    """``streaming_enabled = True`` → ``handler.stream`` is called
    and a placeholder edit pattern fires."""
    handler = _StreamingHandler(
        [("chunk", "streamed"), ("done", "streamed")],
    )
    transport = _make_streaming_transport(handler, streaming_enabled=True)
    bot = _StreamingBot()

    final = asyncio.run(
        transport._dispatch_brain_turn(bot, _CHAT, _USER, "hi")
    )

    assert final == "streamed"
    # Stream path was taken: edits fired against the placeholder.
    assert bot.edits != []
    # Handler.stream was the entry point (recorded in calls).
    assert handler.calls == [(_USER, _CHAT, "hi")]


def test_dispatcher_swallows_streaming_exception_returns_broken_marker():
    """If the streaming reply helper raises, the dispatcher posts
    the standard 'Something broke' ack and returns the same
    string — keeping goal-hook semantics aligned with the
    buffered path's _DRAIN_TURN_BROKE behaviour."""

    class _ExplodingHandler:
        async def stream(self, *_args, **_kwargs):
            # Raise BEFORE yielding anything so we exercise the
            # outer ``try/except`` around _send_brain_reply_streaming.
            raise RuntimeError("kaboom")
            yield  # unreachable but makes this an async generator

    transport = _make_streaming_transport(
        _ExplodingHandler(), streaming_enabled=True,  # type: ignore[arg-type]
    )
    bot = _StreamingBot()

    final = asyncio.run(
        transport._dispatch_brain_turn(bot, _CHAT, _USER, "hi")
    )

    assert final and "broke" in final.lower()
    # The broken-turn ack was sent (after the placeholder).
    sends = [text for _, text in bot.sent_messages]
    assert any("broke" in s.lower() for s in sends)
