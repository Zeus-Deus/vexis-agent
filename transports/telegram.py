"""Telegram transport: PTB Application + chunked sending + typing indicator."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler as PtbMessageHandler,
    filters,
)

from core.auth import is_allowed
from core.handler import MessageHandler
from core.sessions import SessionInfo
from tools.desktop import CaptureError, capture_desktop
from tools.voxtype import TranscriptionEmpty, TranscriptionError, transcribe_audio

log = logging.getLogger(__name__)

_TYPING_REFRESH_SECONDS = 4
_MAX_CHUNK = 4000
_VOICE_ECHO_PREFIX = "🎙️ "
_VOICE_BRAIN_TAG = "[transcribed voice memo] "
_TRANSCRIPTION_EMPTY = "⚠️ Couldn't hear anything in that. Try again?"
_TRANSCRIPTION_FAILED = "⚠️ Couldn't transcribe that. Logs have details."
_VOICE_TOO_SHORT = "That voice memo was too short to transcribe."
_MIN_VOICE_DURATION_SECONDS = 1
_DELETE_CONFIRM_WINDOW = timedelta(seconds=60)
_CB_DATA_MAX_BYTES = 64
_HIDDEN_NOTE = "(Some sessions hidden — type the name directly to use them.)"
_SCREENSHOT_PATH_RE = re.compile(r"(?<![\w/])/tmp/vexis-screenshot-\d+\.png")


def split_for_telegram(text: str, max_len: int = _MAX_CHUNK) -> list[str]:
    """Split text into Telegram-safe chunks, preferring paragraph/line boundaries."""
    if len(text) <= max_len:
        return [text]
    for sep in ("\n\n", "\n"):
        if sep in text:
            return _greedy_join(text.split(sep), sep, max_len)
    return [text[i : i + max_len] for i in range(0, len(text), max_len)]


def _build_button_rows(
    sessions: list[SessionInfo], action: str, *, skip_active: bool
) -> tuple[list[list[InlineKeyboardButton]], bool]:
    rows: list[list[InlineKeyboardButton]] = []
    hidden = False
    for s in sessions:
        if skip_active and s.is_active:
            continue
        data = f"{action}:{s.name}"
        if len(data.encode("utf-8")) > _CB_DATA_MAX_BYTES:
            hidden = True
            continue
        label = f"→ {s.name}" if s.is_active else s.name
        rows.append([InlineKeyboardButton(text=label, callback_data=data)])
    return rows, hidden


def _extract_screenshot_paths(text: str) -> tuple[list[Path], str]:
    """Pull every `/tmp/vexis-screenshot-*.png` path out of `text`.

    Returns the list of unique paths (in first-seen order) and the cleaned
    text with each match replaced by the placeholder ``[screenshot]`` so the
    surrounding prose still reads naturally.
    """
    seen: list[Path] = []
    seen_set: set[str] = set()
    for match in _SCREENSHOT_PATH_RE.findall(text):
        if match in seen_set:
            continue
        seen_set.add(match)
        seen.append(Path(match))
    cleaned = _SCREENSHOT_PATH_RE.sub("[screenshot]", text)
    return seen, cleaned


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
        # Telegram bot commands can't contain hyphens, so /confirm-delete from
        # the spec becomes /confirm_delete here.
        self._pending_deletes: dict[str, datetime] = {}
        self._app = Application.builder().token(token).build()
        self._app.add_handler(
            PtbMessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text)
        )
        self._app.add_handler(PtbMessageHandler(filters.VOICE, self._on_voice))
        self._app.add_handler(CommandHandler("clear", self._on_clear))
        self._app.add_handler(CommandHandler("new", self._on_new))
        self._app.add_handler(CommandHandler("switch", self._on_switch))
        self._app.add_handler(CommandHandler("sessions", self._on_sessions))
        self._app.add_handler(CommandHandler("rename", self._on_rename))
        self._app.add_handler(CommandHandler("delete", self._on_delete))
        self._app.add_handler(CommandHandler("confirm_delete", self._on_confirm_delete))
        self._app.add_handler(CommandHandler("screenshot", self._on_screenshot))
        self._app.add_handler(CallbackQueryHandler(self._on_callback))

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

        await self._send_brain_reply(bot, chat_id, reply)

    async def _on_clear(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        self._pending_deletes.clear()
        reply = await self._handler.handle_clear(user.id)
        if reply is None:
            return
        await msg.get_bot().send_message(
            chat_id=msg.chat_id, text=reply, parse_mode=None
        )

    async def _on_new(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        self._pending_deletes.clear()
        args = ctx.args or []
        if len(args) > 1:
            await msg.reply_text("Usage: /new [name]")
            return
        reply = await self._handler.handle_new(user.id, args[0] if args else None)
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_switch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        self._pending_deletes.clear()
        args = ctx.args or []
        if len(args) > 1:
            await msg.reply_text("Usage: /switch [name]")
            return
        if args:
            reply = await self._handler.handle_switch(user.id, args[0])
            if reply is not None:
                await msg.reply_text(reply)
            return
        sessions = self._handler.sessions_for(user.id)
        if sessions is None:
            return
        if len(sessions) <= 1:
            await msg.reply_text("Only one session exists. Nothing to switch to.")
            return
        ordered = sorted(sessions, key=lambda s: s.created_at, reverse=True)
        rows, hidden = _build_button_rows(ordered, "switch", skip_active=False)
        text = "Pick a session:" + (f"\n{_HIDDEN_NOTE}" if hidden else "")
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))

    async def _on_sessions(
        self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        self._pending_deletes.clear()
        reply = await self._handler.handle_sessions(user.id)
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_rename(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        self._pending_deletes.clear()
        args = ctx.args or []
        if len(args) != 2:
            await msg.reply_text("Usage: /rename <old> <new>")
            return
        reply = await self._handler.handle_rename(user.id, args[0], args[1])
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_delete(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /delete from user_id=%s", user.id)
            return
        args = ctx.args or []
        if len(args) > 1:
            await msg.reply_text("Usage: /delete [name]")
            return
        if args:
            await msg.reply_text(self._arm_delete_confirmation(args[0]))
            return
        sessions = self._handler.sessions_for(user.id)
        if sessions is None:
            return
        ordered = sorted(sessions, key=lambda s: s.created_at, reverse=True)
        rows, hidden = _build_button_rows(ordered, "delete", skip_active=True)
        if not rows:
            await msg.reply_text("No sessions can be deleted right now.")
            return
        text = "Pick a session to delete:" + (f"\n{_HIDDEN_NOTE}" if hidden else "")
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))

    def _arm_delete_confirmation(self, name: str) -> str:
        self._pending_deletes[name] = (
            datetime.now(timezone.utc) + _DELETE_CONFIRM_WINDOW
        )
        return (
            f"Are you sure you want to delete '{name}'? "
            f"Send /confirm_delete {name} within 60s to confirm."
        )

    async def _on_callback(
        self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if query is None or query.from_user is None or query.data is None:
            return
        user_id = query.from_user.id
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected callback from user_id=%s", user_id)
            return
        await query.answer()
        action, _, name = query.data.partition(":")
        if action == "switch":
            self._pending_deletes.clear()
            reply = await self._handler.handle_switch(user_id, name)
        elif action == "delete":
            reply = self._arm_delete_confirmation(name)
        else:
            return
        if reply is not None:
            await query.edit_message_text(reply)

    async def _on_confirm_delete(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /confirm_delete from user_id=%s", user.id)
            return
        args = ctx.args or []
        if len(args) != 1:
            await msg.reply_text("Usage: /confirm_delete <name>")
            return
        name = args[0]
        expiry = self._pending_deletes.pop(name, None)
        now = datetime.now(timezone.utc)
        if expiry is None or expiry <= now:
            await msg.reply_text(f"No pending deletion for '{name}'.")
            return
        reply = await self._handler.handle_delete(user.id, name)
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_voice(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None or msg.voice is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected voice memo from user_id=%s", user.id)
            return

        if msg.voice.duration < _MIN_VOICE_DURATION_SECONDS:
            log.info(
                "Skipping %ds voice memo from user_id=%s (under %ds threshold)",
                msg.voice.duration,
                user.id,
                _MIN_VOICE_DURATION_SECONDS,
            )
            await msg.reply_text(_VOICE_TOO_SHORT)
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

            reply = await self._handler.handle(
                user.id, f"{_VOICE_BRAIN_TAG}{transcription}"
            )
            if reply is None:
                return
            await self._send_brain_reply(bot, chat_id, reply)
        finally:
            if typing_task is not None:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
            ogg_path.unlink(missing_ok=True)

    async def _on_screenshot(
        self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /screenshot from user_id=%s", user.id)
            return
        try:
            result = await capture_desktop()
        except CaptureError as exc:
            log.warning("/screenshot failed: %s", exc)
            await msg.reply_text(f"⚠️ Screenshot failed: {exc}")
            return
        try:
            with result.image_path.open("rb") as fh:
                await msg.reply_photo(photo=fh, caption=result.summary or None)
        finally:
            result.image_path.unlink(missing_ok=True)

    async def _send_brain_reply(self, bot, chat_id: int, reply: str) -> None:
        """Send a brain response, extracting and forwarding any screenshot
        paths it referenced as photos before the text body."""
        paths, cleaned = _extract_screenshot_paths(reply)
        for path in paths:
            try:
                if not path.is_file():
                    log.warning("Brain referenced missing screenshot %s", path)
                    continue
                with path.open("rb") as fh:
                    await bot.send_photo(chat_id=chat_id, photo=fh)
            except Exception:
                log.exception("Failed to forward screenshot %s", path)
            finally:
                path.unlink(missing_ok=True)

        text = cleaned.strip()
        if not text:
            return
        for chunk in split_for_telegram(text):
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=None)

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
