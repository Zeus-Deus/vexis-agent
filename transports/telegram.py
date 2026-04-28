"""Telegram transport: PTB Application + chunked sending + typing indicator."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler as PtbMessageHandler,
    filters,
)

from core.auth import is_allowed
from core.handler import MessageHandler
from tools.voxtype import TranscriptionEmpty, TranscriptionError, transcribe_audio

log = logging.getLogger(__name__)

_TYPING_REFRESH_SECONDS = 4
_MAX_CHUNK = 4000
_VOICE_ECHO_PREFIX = "🎙️ "
_TRANSCRIPTION_EMPTY = "⚠️ Couldn't hear anything in that. Try again?"
_TRANSCRIPTION_FAILED = "⚠️ Couldn't transcribe that. Logs have details."


def split_for_telegram(text: str, max_len: int = _MAX_CHUNK) -> list[str]:
    """Split text into Telegram-safe chunks, preferring paragraph/line boundaries."""
    if len(text) <= max_len:
        return [text]
    for sep in ("\n\n", "\n"):
        if sep in text:
            return _greedy_join(text.split(sep), sep, max_len)
    return [text[i : i + max_len] for i in range(0, len(text), max_len)]


def _greedy_join(parts: list[str], sep: str, max_len: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}{sep}{part}" if current else part
        if len(candidate) <= max_len:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(part) > max_len:
            sub = split_for_telegram(part, max_len)
            chunks.extend(sub[:-1])
            current = sub[-1]
        else:
            current = part
    if current:
        chunks.append(current)
    return chunks


class TelegramTransport:
    def __init__(
        self, token: str, handler: MessageHandler, allowed_user_id: int
    ) -> None:
        self._handler = handler
        self._allowed_user_id = allowed_user_id
        self._app = Application.builder().token(token).build()
        self._app.add_handler(
            PtbMessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text)
        )
        self._app.add_handler(PtbMessageHandler(filters.VOICE, self._on_voice))
        self._app.add_handler(CommandHandler("clear", self._on_clear))

    async def _on_text(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None or msg.text is None:
            return

        chat_id = msg.chat_id
        bot = msg.get_bot()

        typing_task = asyncio.create_task(self._keep_typing(bot, chat_id))
        try:
            reply = await self._handler.handle(user.id, msg.text)
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

        if reply is None:
            return

        for chunk in split_for_telegram(reply):
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=None)

    async def _on_clear(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        reply = await self._handler.handle_clear(user.id)
        if reply is None:
            return
        await msg.get_bot().send_message(chat_id=msg.chat_id, text=reply, parse_mode=None)

    async def _on_voice(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None or msg.voice is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected voice memo from user_id=%s", user.id)
            return

        chat_id = msg.chat_id
        bot = msg.get_bot()

        fd = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        ogg_path = Path(fd.name)
        fd.close()

        typing_task: asyncio.Task[None] | None = None
        try:
            tg_file = await msg.voice.get_file()
            await tg_file.download_to_drive(custom_path=ogg_path)

            typing_task = asyncio.create_task(self._keep_typing(bot, chat_id))

            try:
                transcription = await transcribe_audio(ogg_path)
            except TranscriptionEmpty:
                await bot.send_message(
                    chat_id=chat_id, text=_TRANSCRIPTION_EMPTY, parse_mode=None
                )
                return
            except TranscriptionError:
                log.exception("Transcription failed")
                await bot.send_message(
                    chat_id=chat_id, text=_TRANSCRIPTION_FAILED, parse_mode=None
                )
                return

            await bot.send_message(
                chat_id=chat_id,
                text=f"{_VOICE_ECHO_PREFIX}{transcription}",
                parse_mode=None,
            )

            reply = await self._handler.handle(user.id, transcription)
            if reply is None:
                return
            for chunk in split_for_telegram(reply):
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=None)
        finally:
            if typing_task is not None:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
            ogg_path.unlink(missing_ok=True)

    @staticmethod
    async def _keep_typing(bot, chat_id: int) -> None:
        while True:
            try:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
            except Exception:
                log.debug("send_chat_action failed", exc_info=True)
            await asyncio.sleep(_TYPING_REFRESH_SECONDS)

    async def run(self) -> None:
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        log.info("Telegram polling started")
        try:
            await asyncio.Event().wait()
        finally:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
