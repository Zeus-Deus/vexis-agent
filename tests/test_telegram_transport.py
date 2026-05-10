"""Tests for transports/telegram.py — inbound photo support and cleanup.

Photo updates from Telegram should land on disk as
/tmp/vexis-incoming-<uuid>.png and be routed to the brain as a synthetic
text message. A periodic cleanup sweeps files older than 1 hour.

Tests follow the codebase convention of sync test functions calling
asyncio.run() rather than pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from vexis_agent.core import paths, status as status_module
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.status import StatusSnapshot
from vexis_agent.transports.telegram import (
    _INCOMING_PHOTO_DIR,
    _PICKING_UP_PREFIX,
    _STATUS_IDLE,
    TelegramTransport,
    _build_incoming_photo_path,
    _cleanup_incoming_images,
    _format_incoming_image_message,
    _format_status_duration,
    _format_status_reply,
    _make_pickup_preview,
)


# --- pure helpers ----------------------------------------------------------


def test_format_message_with_caption():
    path = Path("/tmp/vexis-incoming-abc.png")
    out = _format_incoming_image_message(path, "what does this say?")
    assert out == "[user sent image: /tmp/vexis-incoming-abc.png] what does this say?"


def test_format_message_without_caption():
    path = Path("/tmp/vexis-incoming-abc.png")
    assert _format_incoming_image_message(path, None) == (
        "[user sent image: /tmp/vexis-incoming-abc.png]"
    )


def test_format_message_with_blank_caption_drops_caption():
    path = Path("/tmp/vexis-incoming-abc.png")
    assert _format_incoming_image_message(path, "   \n") == (
        "[user sent image: /tmp/vexis-incoming-abc.png]"
    )


def test_build_incoming_photo_path_shape():
    p = _build_incoming_photo_path()
    assert p.parent == _INCOMING_PHOTO_DIR
    assert p.name.startswith("vexis-incoming-")
    assert p.suffix == ".png"


def test_build_incoming_photo_path_is_unique_per_call():
    a = _build_incoming_photo_path()
    b = _build_incoming_photo_path()
    assert a != b


# --- cleanup --------------------------------------------------------------


def test_cleanup_removes_old_keeps_new(tmp_path):
    old_file = tmp_path / "vexis-incoming-old.png"
    new_file = tmp_path / "vexis-incoming-new.png"
    unrelated = tmp_path / "unrelated.png"
    for f in (old_file, new_file, unrelated):
        f.write_bytes(b"x")
    now = datetime.now(timezone.utc)
    two_hours_ago = (now - timedelta(hours=2)).timestamp()
    os.utime(old_file, (two_hours_ago, two_hours_ago))

    removed = _cleanup_incoming_images(now, directory=tmp_path)

    assert removed == 1
    assert not old_file.exists()
    assert new_file.exists()
    assert unrelated.exists()


def test_cleanup_at_threshold_keeps_file(tmp_path):
    f = tmp_path / "vexis-incoming-edge.png"
    f.write_bytes(b"x")
    now = datetime.now(timezone.utc)
    exactly_max_age = (now - timedelta(hours=1)).timestamp()
    os.utime(f, (exactly_max_age, exactly_max_age))

    removed = _cleanup_incoming_images(now, directory=tmp_path)

    assert removed == 0
    assert f.exists()


def test_cleanup_handles_empty_directory(tmp_path):
    assert _cleanup_incoming_images(datetime.now(timezone.utc), directory=tmp_path) == 0


# --- _on_photo end-to-end --------------------------------------------------


class _FakeFile:
    def __init__(self) -> None:
        self.saved_to: Path | None = None

    async def download_to_drive(self, custom_path: Any) -> None:
        path = Path(custom_path)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.saved_to = path


class _FakePhoto:
    def __init__(self) -> None:
        self.file = _FakeFile()

    async def get_file(self) -> _FakeFile:
        return self.file


class _FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []
        self.typing_calls = 0

    async def send_chat_action(self, _chat_id: int, _action: Any) -> None:
        self.typing_calls += 1

    async def send_message(
        self, *, chat_id: int, text: str, parse_mode: Any = None, **_kw: Any
    ) -> None:
        self.sent_messages.append((chat_id, text))


class _FakeMessage:
    def __init__(
        self, photo: tuple, caption: str | None, chat_id: int, bot: _FakeBot
    ) -> None:
        self.photo = photo
        self.caption = caption
        self.chat_id = chat_id
        self._bot = bot

    def get_bot(self) -> _FakeBot:
        return self._bot


class _FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _FakeUpdate:
    def __init__(self, message: _FakeMessage, user: _FakeUser) -> None:
        self.message = message
        self.effective_user = user


class _FakeHandler:
    def __init__(self, reply: str | None = None) -> None:
        self.reply = reply
        self.last_user_id: int | None = None
        self.last_chat_id: int | None = None
        self.last_text: str | None = None

    async def handle(self, user_id: int, chat_id: int, text: str) -> str | None:
        self.last_user_id = user_id
        self.last_chat_id = chat_id
        self.last_text = text
        return self.reply

    # v3b Day 3a accessors. _learning_curator is None on these
    # legacy fixtures so the relationships hook short-circuits
    # before reaching them, but the transport defends against an
    # AttributeError by delegating only when both are wired.
    def current_session_uuid(self) -> str:
        return "test-session"

    def next_user_turn_index(self, _session_uuid: str) -> int:
        return 1

    async def claim_next_turn_index(self, _session_uuid: str) -> int | None:
        return 1


def _make_transport(handler: _FakeHandler, allowed_user_id: int) -> TelegramTransport:
    """Build a TelegramTransport without going through PTB's Application.

    The handlers we exercise here read ``_handler``, ``_allowed_user_id``
    and ``_running_tasks``; we bypass __init__ and stub those.
    """
    t = TelegramTransport.__new__(TelegramTransport)
    t._handler = handler  # type: ignore[attr-defined]
    t._allowed_user_id = allowed_user_id  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    # v3b Day 3a: optional collaborators the drain references.
    # Defaulted to None so the relationships hook short-circuits
    # and these legacy tests stay focused on drain/pickup behavior.
    t._learning_curator = None  # type: ignore[attr-defined]
    return t


_USER = 99
_CHAT = 42


def test_on_photo_with_caption_routes_synthetic_text_to_brain():
    handler = _FakeHandler(reply=None)
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _FakeBot()
    photo = _FakePhoto()
    msg = _FakeMessage(
        photo=(photo,), caption="what does this say?", chat_id=_CHAT, bot=bot
    )
    update = _FakeUpdate(msg, _FakeUser(_USER))

    try:
        asyncio.run(transport._on_photo(update, None))

        saved = photo.file.saved_to
        assert saved is not None
        assert saved.parent == Path("/tmp")
        assert saved.name.startswith("vexis-incoming-")
        assert saved.suffix == ".png"
        assert saved.exists()
        assert handler.last_user_id == _USER
        assert handler.last_chat_id == _CHAT
        assert handler.last_text == f"[user sent image: {saved}] what does this say?"
    finally:
        if photo.file.saved_to is not None:
            photo.file.saved_to.unlink(missing_ok=True)


def test_on_photo_without_caption_uses_bare_prefix():
    handler = _FakeHandler(reply=None)
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _FakeBot()
    photo = _FakePhoto()
    msg = _FakeMessage(photo=(photo,), caption=None, chat_id=_CHAT, bot=bot)
    update = _FakeUpdate(msg, _FakeUser(_USER))

    try:
        asyncio.run(transport._on_photo(update, None))

        saved = photo.file.saved_to
        assert saved is not None
        assert handler.last_text == f"[user sent image: {saved}]"
    finally:
        if photo.file.saved_to is not None:
            photo.file.saved_to.unlink(missing_ok=True)


def test_on_photo_picks_largest_variant():
    """PTB delivers PhotoSize tuple smallest→largest; we use the last."""
    handler = _FakeHandler(reply=None)
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _FakeBot()
    small = _FakePhoto()
    medium = _FakePhoto()
    large = _FakePhoto()
    msg = _FakeMessage(
        photo=(small, medium, large), caption=None, chat_id=_CHAT, bot=bot
    )
    update = _FakeUpdate(msg, _FakeUser(_USER))

    try:
        asyncio.run(transport._on_photo(update, None))

        assert small.file.saved_to is None
        assert medium.file.saved_to is None
        assert large.file.saved_to is not None
    finally:
        for p in (small, medium, large):
            if p.file.saved_to is not None:
                p.file.saved_to.unlink(missing_ok=True)


def test_on_photo_rejects_disallowed_user():
    handler = _FakeHandler(reply=None)
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _FakeBot()
    photo = _FakePhoto()
    msg = _FakeMessage(photo=(photo,), caption="hi", chat_id=_CHAT, bot=bot)
    update = _FakeUpdate(msg, _FakeUser(user_id=12345))

    asyncio.run(transport._on_photo(update, None))

    assert photo.file.saved_to is None
    assert handler.last_text is None


# --- pickup preview formatting ---------------------------------------------


def test_pickup_preview_strips_voice_tag():
    out = _make_pickup_preview("[transcribed voice memo] what time is it")
    assert out == "🎙️ what time is it"


def test_pickup_preview_strips_image_prefix_with_caption():
    out = _make_pickup_preview(
        "[user sent image: /tmp/vexis-incoming-abc.png] what does this say?"
    )
    assert out == "📷 what does this say?"


def test_pickup_preview_strips_image_prefix_without_caption():
    out = _make_pickup_preview("[user sent image: /tmp/vexis-incoming-abc.png]")
    assert out == "📷"


def test_pickup_preview_truncates_long_text():
    long_text = "a" * 200
    out = _make_pickup_preview(long_text, max_len=40)
    assert out == ("a" * 40) + "…"


def test_pickup_preview_collapses_newlines():
    out = _make_pickup_preview("first line\nsecond line")
    assert "\n" not in out
    assert out == "first line second line"


def test_pickup_preview_short_text_no_ellipsis():
    out = _make_pickup_preview("hi")
    assert out == "hi"


# --- drain + queue + Picking up ack ----------------------------------------


class _SerialisingHandler:
    """Brain stub that lets the test step the drain forward one turn at
    a time. Each `handle()` waits on a per-call gate the test releases."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[str] = []
        self.in_call = asyncio.Event()
        self.release_call = asyncio.Event()

    async def handle(self, user_id: int, chat_id: int, text: str) -> str | None:
        self.calls.append(text)
        self.in_call.set()
        await self.release_call.wait()
        # Reset for the next turn.
        self.in_call.clear()
        self.release_call.clear()
        if not self._replies:
            return None
        return self._replies.pop(0)


def _bot_messages(bot: _FakeBot, chat_id: int) -> list[str]:
    return [text for cid, text in bot.sent_messages if cid == chat_id]


def test_drain_runs_followups_with_pickup_ack():
    """Two messages, second arrives while first is mid-flight: it should
    enqueue silently, then run after the first turn finishes — with a
    'Picking up:' ack preceding the brain reply for the queued one."""

    handler = _SerialisingHandler(replies=["reply-A", "reply-B"])
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _FakeBot()

    async def scenario() -> None:
        # Submit msg A — claims the chat and starts the drain.
        drain_task = asyncio.create_task(
            transport._dispatch_to_brain(bot, _CHAT, _USER, "msg A")
        )
        # Wait until the brain is mid-call on A.
        await asyncio.wait_for(handler.in_call.wait(), timeout=1.0)
        # Submit msg B — drain is busy, so this should enqueue silently.
        await transport._dispatch_to_brain(bot, _CHAT, _USER, "msg B")
        # No 'Picking up:' message yet, no reply for B yet.
        assert all(
            not text.startswith(_PICKING_UP_PREFIX)
            for text in _bot_messages(bot, _CHAT)
        )
        # Let A finish.
        handler.release_call.set()
        # Wait for B to enter the brain.
        await asyncio.wait_for(handler.in_call.wait(), timeout=1.0)
        # Reply A should have been sent. 'Picking up:' should now precede B.
        sent = _bot_messages(bot, _CHAT)
        assert "reply-A" in sent
        pickup = next(
            (text for text in sent if text.startswith(_PICKING_UP_PREFIX)), None
        )
        assert pickup is not None
        assert "msg B" in pickup
        # Pickup ack must come AFTER reply-A.
        assert sent.index("reply-A") < sent.index(pickup)
        # Let B finish.
        handler.release_call.set()
        await asyncio.wait_for(drain_task, timeout=1.0)

    asyncio.run(scenario())

    sent = _bot_messages(bot, _CHAT)
    assert "reply-A" in sent
    assert "reply-B" in sent
    assert handler.calls == ["msg A", "msg B"]
    # Drain released ownership at the end.
    assert not transport._running_tasks.is_running(_CHAT)


def test_cancel_clears_queue_and_stops_drain():
    """During a queued state, /cancel should drop the pending follow-ups
    and the drain loop should exit cleanly without invoking the brain
    on the cancelled messages."""

    # Brain returns None on cancellation (matches handler's BrainCancelled
    # path which is caught and converted to None).
    handler = _SerialisingHandler(replies=[None])  # type: ignore[list-item]
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _FakeBot()

    async def scenario() -> None:
        drain_task = asyncio.create_task(
            transport._dispatch_to_brain(bot, _CHAT, _USER, "msg A")
        )
        await asyncio.wait_for(handler.in_call.wait(), timeout=1.0)
        # Stack two follow-ups.
        await transport._dispatch_to_brain(bot, _CHAT, _USER, "msg B")
        await transport._dispatch_to_brain(bot, _CHAT, _USER, "msg C")
        assert transport._running_tasks.queue_depth(_CHAT) == 2
        # Cancel — should clear the queue and flag drain exit.
        cancelled = await transport._running_tasks.cancel(_CHAT)
        assert cancelled is True
        # Release the (now-cancelled) brain call so drain can unwind.
        handler.release_call.set()
        await asyncio.wait_for(drain_task, timeout=1.0)

    asyncio.run(scenario())

    # Only msg A entered the brain — B and C were dropped by /cancel.
    assert handler.calls == ["msg A"]
    assert not transport._running_tasks.is_running(_CHAT)
    assert transport._running_tasks.queue_depth(_CHAT) == 0


def test_pickup_preview_empty_text_falls_back():
    """Empty input would otherwise produce 'Picking up: ' (trailing
    space). The fallback keeps the ack sensible if it ever fires for
    a degenerate input."""
    assert _make_pickup_preview("") == "(empty)"
    assert _make_pickup_preview("   \n\n   ") == "(empty)"


class _RaisingHandler:
    """Brain stub whose first call raises, second call returns a reply.

    Verifies the drain's per-turn try/except keeps the loop alive when
    handler.handle leaks an exception (e.g., an unexpected error from
    _inject_context that escapes handler's own brain catch)."""

    def __init__(self, *, second_reply: str = "ok") -> None:
        self.calls: list[str] = []
        self._second_reply = second_reply

    async def handle(self, user_id: int, chat_id: int, text: str) -> str | None:
        self.calls.append(text)
        if len(self.calls) == 1:
            raise RuntimeError("handler exploded")
        return self._second_reply


def test_drain_survives_handler_exception_and_releases_ownership():
    """Scenario 1: an unexpected exception in handler.handle must NOT
    leak drain ownership. The drain logs, surfaces an error reply, and
    keeps processing the rest of the queue. The chat is left in a
    clean state at the end."""

    handler = _RaisingHandler()
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _FakeBot()

    async def scenario() -> None:
        # Pre-queue a follow-up so the drain has work after the broken turn.
        # The simplest way is to submit two messages back-to-back where
        # the second arrives while the first is still being claimed —
        # but here we just enqueue directly via the running_tasks API
        # because handler doesn't await.
        # Easier: claim, drain processes msg1 (raises), pops msg2 (returns).
        await transport._dispatch_to_brain(bot, _CHAT, _USER, "msg1")

    asyncio.run(scenario())

    # Both turns ran; first raised, second succeeded. User got the
    # error reply and the second turn's reply.
    assert handler.calls == ["msg1"]
    sent = [text for cid, text in bot.sent_messages if cid == _CHAT]
    assert any("Something broke" in t for t in sent)
    # Drain ownership released.
    assert not transport._running_tasks.is_running(_CHAT)


def test_drain_survives_handler_exception_with_followup():
    """Same as above but with a queued follow-up to verify the drain
    keeps draining even after a turn explodes."""

    handler = _RaisingHandler(second_reply="recovered")
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _FakeBot()

    async def scenario() -> None:
        # First dispatch claims and runs msg1 (raises).
        # Pre-stack msg2 by directly enqueuing — simulates a follow-up
        # that arrived while msg1 was being processed (and would have
        # been silently lost if the drain bailed on the exception).
        await transport._running_tasks.claim(_CHAT)
        await transport._running_tasks.enqueue(_CHAT, _USER, "msg2")
        # Manually drive the drain since claim was already taken.
        await transport._drain_chat(bot, _CHAT, _USER, "msg1")

    asyncio.run(scenario())

    assert handler.calls == ["msg1", "msg2"]
    sent = [text for cid, text in bot.sent_messages if cid == _CHAT]
    assert any("Something broke" in t for t in sent)
    assert "recovered" in sent
    assert not transport._running_tasks.is_running(_CHAT)


def test_post_cancel_message_is_processed_not_lost():
    """Scenario 4: /cancel fires while a turn is in flight; a new
    message arrives during cancel cleanup. It must NOT be silently
    lost — the drain takes over after release and processes it."""

    handler = _SerialisingHandler(replies=[None, "reply-D"])  # type: ignore[list-item]
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _FakeBot()

    async def scenario() -> None:
        drain_task = asyncio.create_task(
            transport._dispatch_to_brain(bot, _CHAT, _USER, "msg A")
        )
        await asyncio.wait_for(handler.in_call.wait(), timeout=1.0)
        # Cancel while A is mid-flight.
        cancelled = await transport._running_tasks.cancel(_CHAT)
        assert cancelled is True
        # Post-cancel message arrives during cleanup window — same
        # path the user's bot would take. claim() returns False
        # because drain_active is still True; this lands in the queue.
        await transport._dispatch_to_brain(bot, _CHAT, _USER, "msg D")
        assert transport._running_tasks.queue_depth(_CHAT) == 1
        # Release the cancelled brain call so drain can unwind.
        handler.release_call.set()
        # Drain pops_or_release → cancelled → release WITHOUT clearing
        # the queue. take_over_if_pending picks up msg D. Brain runs D.
        await asyncio.wait_for(handler.in_call.wait(), timeout=1.0)
        handler.release_call.set()
        await asyncio.wait_for(drain_task, timeout=1.0)

    asyncio.run(scenario())

    # msg D was processed; reply was sent.
    sent = [text for cid, text in bot.sent_messages if cid == _CHAT]
    assert "reply-D" in sent
    assert handler.calls == ["msg A", "msg D"]
    assert not transport._running_tasks.is_running(_CHAT)


# --- /status reply formatting ----------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_format_status_idle_with_no_history():
    out = _format_status_reply(
        snapshot=None, queue_depth=0, last_idle_at=None, now=_now()
    )
    assert out == _STATUS_IDLE


def test_format_status_idle_with_last_idle_timestamp():
    last = _now() - timedelta(minutes=23)
    out = _format_status_reply(
        snapshot=None, queue_depth=0, last_idle_at=last, now=_now()
    )
    assert out == "Nothing running, sir. Idle for 23m."


def test_format_status_working_with_tool_activity():
    snap = StatusSnapshot(
        chat_id=1,
        started_at=_now() - timedelta(minutes=4, seconds=12),
        last_event_at=_now() - timedelta(seconds=2),
        tool_count=7,
        last_tool="Edit",
        last_target="core/running_tasks.py",
    )
    out = _format_status_reply(
        snapshot=snap, queue_depth=0, last_idle_at=None, now=_now()
    )
    assert out == (
        "Working for 4m 12s.\n"
        "Last action: edited `core/running_tasks.py`\n"
        "Tools used: 7"
    )


def test_format_status_working_with_queue_depth():
    snap = StatusSnapshot(
        chat_id=1,
        started_at=_now() - timedelta(minutes=2),
        last_event_at=_now(),
        tool_count=3,
        last_tool="Bash",
        last_target="git status",
    )
    out = _format_status_reply(
        snapshot=snap, queue_depth=2, last_idle_at=None, now=_now()
    )
    assert "Queued follow-ups: 2" in out
    assert "ran `git status`" in out


def test_format_status_working_no_tool_activity_yet():
    snap = StatusSnapshot(
        chat_id=1,
        started_at=_now() - timedelta(seconds=8),
        last_event_at=_now() - timedelta(seconds=8),
        tool_count=0,
        last_tool=None,
        last_target=None,
    )
    out = _format_status_reply(
        snapshot=snap, queue_depth=0, last_idle_at=None, now=_now()
    )
    assert out == "Working for 8s. No tool activity yet."


def test_format_status_unknown_tool_falls_back_to_used_form():
    snap = StatusSnapshot(
        chat_id=1,
        started_at=_now() - timedelta(seconds=30),
        last_event_at=_now(),
        tool_count=1,
        last_tool="Mystery",
        last_target="something",
    )
    out = _format_status_reply(
        snapshot=snap, queue_depth=0, last_idle_at=None, now=_now()
    )
    assert "Last action: used Mystery on `something`" in out


def test_format_status_tool_with_no_target():
    snap = StatusSnapshot(
        chat_id=1,
        started_at=_now() - timedelta(seconds=30),
        last_event_at=_now(),
        tool_count=1,
        last_tool="TodoWrite",
        last_target=None,
    )
    out = _format_status_reply(
        snapshot=snap, queue_depth=0, last_idle_at=None, now=_now()
    )
    assert "Last action: TodoWrite" in out


def test_format_status_duration_unit_boundaries():
    assert _format_status_duration(timedelta(seconds=8)) == "8s"
    assert _format_status_duration(timedelta(seconds=60)) == "1m"
    assert _format_status_duration(timedelta(minutes=4, seconds=12)) == "4m 12s"
    assert _format_status_duration(timedelta(hours=1)) == "1h"
    assert _format_status_duration(timedelta(hours=1, minutes=4)) == "1h 4m"
    assert _format_status_duration(timedelta(seconds=-5)) == "0s"


# --- /status handler end-to-end --------------------------------------------


@pytest.fixture
def patch_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(status_module, "runtime_dir", lambda: tmp_path)
    return tmp_path


class _StatusBot:
    def __init__(self) -> None:
        self.replies: list[str] = []


class _StatusMessage:
    def __init__(self, chat_id: int, bot: _StatusBot) -> None:
        self.chat_id = chat_id
        self._bot = bot

    def get_bot(self) -> _StatusBot:
        return self._bot

    async def reply_text(self, text: str, **_kw: Any) -> None:
        self._bot.replies.append(text)


class _StatusUpdate:
    def __init__(self, chat_id: int, user_id: int, bot: _StatusBot) -> None:
        self.message = _StatusMessage(chat_id, bot)
        self.effective_user = _FakeUser(user_id)


def test_on_status_when_idle_and_never_busy(patch_runtime_dir):
    handler = _FakeHandler(reply=None)
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _StatusBot()
    update = _StatusUpdate(_CHAT, _USER, bot)

    asyncio.run(transport._on_status(update, None))

    assert bot.replies == [_STATUS_IDLE]


def test_on_status_reflects_last_idle_after_drain_release(patch_runtime_dir):
    handler = _FakeHandler(reply=None)
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _StatusBot()
    update = _StatusUpdate(_CHAT, _USER, bot)

    async def scenario() -> None:
        # Run a quick claim-then-release cycle so last_idle_at is set.
        await transport._running_tasks.claim(_CHAT)
        await transport._running_tasks.pop_or_release(_CHAT)
        await transport._on_status(update, None)

    asyncio.run(scenario())
    assert len(bot.replies) == 1
    assert bot.replies[0].startswith("Nothing running, sir. Idle for")


def test_on_status_while_brain_running_shows_tool_activity(patch_runtime_dir):
    handler = _FakeHandler(reply=None)
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _StatusBot()
    update = _StatusUpdate(_CHAT, _USER, bot)

    sf = status_module.StatusFile(_CHAT)
    sf.start()
    sf.record_tool("Edit", "core/foo.py")
    sf.record_tool("Edit", "core/bar.py")

    asyncio.run(transport._on_status(update, None))

    assert len(bot.replies) == 1
    text = bot.replies[0]
    assert text.startswith("Working for")
    assert "Last action: edited `core/bar.py`" in text
    assert "Tools used: 2" in text


def test_on_status_reflects_queue_depth(patch_runtime_dir):
    handler = _FakeHandler(reply=None)
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _StatusBot()
    update = _StatusUpdate(_CHAT, _USER, bot)

    sf = status_module.StatusFile(_CHAT)
    sf.start()
    sf.record_tool("Bash", "git status")

    async def scenario() -> None:
        await transport._running_tasks.claim(_CHAT)
        await transport._running_tasks.enqueue(_CHAT, _USER, "follow-up A")
        await transport._running_tasks.enqueue(_CHAT, _USER, "follow-up B")
        await transport._on_status(update, None)

    asyncio.run(scenario())
    assert "Queued follow-ups: 2" in bot.replies[0]


def test_on_status_does_not_interfere_with_running_drain(patch_runtime_dir):
    """Calling /status while a drain is mid-flight must not block the
    drain or affect its outcome. We run a serialising drain, fire
    /status once between turn 1's start and turn 1's reply send, then
    let the drain finish — both replies should land normally."""

    drain_handler = _SerialisingHandler(replies=["reply-A"])
    transport = _make_transport(drain_handler, allowed_user_id=_USER)
    drain_bot = _FakeBot()
    status_bot = _StatusBot()
    status_update = _StatusUpdate(_CHAT, _USER, status_bot)

    async def scenario() -> None:
        drain_task = asyncio.create_task(
            transport._dispatch_to_brain(drain_bot, _CHAT, _USER, "msg A")
        )
        # Wait until the drain handler is mid-call.
        await asyncio.wait_for(drain_handler.in_call.wait(), timeout=1.0)
        # /status is read-only — it must complete without blocking.
        await asyncio.wait_for(transport._on_status(status_update, None), timeout=1.0)
        # Release the brain to let the drain finish.
        drain_handler.release_call.set()
        await asyncio.wait_for(drain_task, timeout=1.0)

    asyncio.run(scenario())

    # /status produced exactly one reply, drain's reply was sent
    # normally, and the chat is idle at the end.
    assert len(status_bot.replies) == 1
    drain_replies = [text for cid, text in drain_bot.sent_messages if cid == _CHAT]
    assert "reply-A" in drain_replies
    assert not transport._running_tasks.is_running(_CHAT)


def test_on_status_rejects_disallowed_user(patch_runtime_dir):
    handler = _FakeHandler(reply=None)
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _StatusBot()
    update = _StatusUpdate(_CHAT, 12345, bot)

    asyncio.run(transport._on_status(update, None))

    assert bot.replies == []


def test_dispatch_rejects_disallowed_user_silently():
    handler = _FakeHandler(reply=None)
    transport = _make_transport(handler, allowed_user_id=_USER)
    bot = _FakeBot()

    async def scenario() -> None:
        await transport._dispatch_to_brain(bot, _CHAT, 12345, "intruder")

    asyncio.run(scenario())
    assert handler.last_text is None
    assert _bot_messages(bot, _CHAT) == []
    assert not transport._running_tasks.is_running(_CHAT)
