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

from transports.telegram import (
    _INCOMING_PHOTO_DIR,
    TelegramTransport,
    _build_incoming_photo_path,
    _cleanup_incoming_images,
    _format_incoming_image_message,
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


def _make_transport(handler: _FakeHandler, allowed_user_id: int) -> TelegramTransport:
    """Build a TelegramTransport without going through PTB's Application.

    `_on_photo` only reads `_handler` and `_allowed_user_id`, so we
    bypass __init__ and stub the two attributes it needs.
    """
    t = TelegramTransport.__new__(TelegramTransport)
    t._handler = handler  # type: ignore[attr-defined]
    t._allowed_user_id = allowed_user_id  # type: ignore[attr-defined]
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
