"""Telegram transport: PTB Application + chunked sending + typing indicator."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from telegram import (
    BotCommand as TelegramBotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
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
from core.background_tasks import (
    BackgroundTasks,
    TaskNotFound,
    TaskStatus,
)
from core.commands import COMMANDS
from core.curator import CuratorController
from core.handler import MessageHandler
from core.notify import Notifier
from core.running_tasks import RunningTasks
from core.sessions import SessionInfo
from core.status import StatusSnapshot, cleanup_all as cleanup_status_files, read_status
from core.web_server import WebDashboard
from tools.desktop import CaptureError, capture_desktop
from tools.voxtype import TranscriptionEmpty, TranscriptionError, transcribe_audio

log = logging.getLogger(__name__)

_TYPING_REFRESH_SECONDS = 4
_MAX_CHUNK = 4000
_VOICE_ECHO_PREFIX = "🎙️ "
_VOICE_BRAIN_TAG = "[transcribed voice memo] "
_PICKING_UP_PREFIX = "Picking up: "
_PICKING_UP_PREVIEW_LEN = 40
_PICKING_UP_FALLBACK = "(empty)"
_INCOMING_IMAGE_PREFIX_RE = re.compile(r"^\[user sent image: [^\]]+\]\s*")
_DRAIN_TURN_BROKE = "⚠️ Something broke handling that. Logs have details."
_TRANSCRIPTION_EMPTY = "⚠️ Couldn't hear anything in that. Try again?"
_TRANSCRIPTION_FAILED = "⚠️ Couldn't transcribe that. Logs have details."
_VOICE_TOO_SHORT = "That voice memo was too short to transcribe."
_MIN_VOICE_DURATION_SECONDS = 1
_DELETE_CONFIRM_WINDOW = timedelta(seconds=60)
_CB_DATA_MAX_BYTES = 64
_HIDDEN_NOTE = "(Some sessions hidden — type the name directly to use them.)"
_SCREENSHOT_PATH_RE = re.compile(r"(?<![\w/])/tmp/vexis-screenshot-\d+\.png")
# Browser screenshots land in the user's workspace at
# ``<workspace>/browser/screenshots/<ts>.png`` where ``<ts>`` is the
# fixed UTC stamp ``YYYYMMDDTHHMMSSZ``. We anchor on the suffix
# ``/browser/screenshots/<ts>.png`` so the regex doesn't bake in a
# specific workspace location.
_BROWSER_SCREENSHOT_PATH_RE = re.compile(
    r"(?<![\w])(?:/[\w.-]+)+/browser/screenshots/\d{8}T\d{6}Z\.png"
)
_INCOMING_PHOTO_DIR = Path("/tmp")
_INCOMING_PHOTO_GLOB = "vexis-incoming-*.png"
_INCOMING_PHOTO_MAX_AGE = timedelta(hours=1)
_INCOMING_PHOTO_CLEANUP_INTERVAL_SECONDS = 600
_INCOMING_BRAIN_PREFIX = "[user sent image: {path}]"
_CANCEL_OK = "Cancelled, sir. What next?"
_NOTHING_TO_CANCEL = "Nothing to cancel — I'm not working on anything right now."
_STATUS_IDLE = "Nothing running, sir."
_STATUS_NO_TOOLS_YET = "No tool activity yet."

# Verbs for rendering the most-recent tool_use event in /status. Tools
# absent from this map fall back to "used <ToolName>".
_TOOL_VERB: dict[str, str] = {
    "Edit": "edited",
    "MultiEdit": "edited",
    "NotebookEdit": "edited",
    "Write": "wrote",
    "Read": "read",
    "Bash": "ran",
    "Task": "delegated",
    "Glob": "searched for",
    "Grep": "searched for",
    "WebFetch": "fetched",
    "WebSearch": "searched the web for",
}
_NO_BG_TASKS = "No background tasks running."
_DAEMON_RESTART_LOST = (
    "Sir, when the daemon restarted, background task `{name}` didn't survive. "
    "Want me to relaunch it?"
)


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


def _build_incoming_photo_path() -> Path:
    """Allocate a fresh /tmp path for an inbound Telegram photo."""
    return _INCOMING_PHOTO_DIR / f"vexis-incoming-{uuid.uuid4().hex}.png"


def _make_pickup_preview(text: str, max_len: int = _PICKING_UP_PREVIEW_LEN) -> str:
    """Render a short preview of a queued message for the 'Picking up:' ack.

    Strips the internal routing prefixes used for voice/photo so the user
    sees something they recognise from what they sent (echoed voice text,
    a 📷 marker for an image) rather than the synthetic ``[user sent
    image: /tmp/...]`` form.
    """
    cleaned = text
    if cleaned.startswith(_VOICE_BRAIN_TAG):
        cleaned = _VOICE_ECHO_PREFIX + cleaned[len(_VOICE_BRAIN_TAG) :]
    else:
        match = _INCOMING_IMAGE_PREFIX_RE.match(cleaned)
        if match:
            rest = cleaned[match.end() :].strip()
            cleaned = f"📷 {rest}" if rest else "📷"
    cleaned = cleaned.strip().replace("\n", " ")
    if not cleaned:
        return _PICKING_UP_FALLBACK
    if len(cleaned) > max_len:
        return cleaned[:max_len].rstrip() + "…"
    return cleaned


def _format_incoming_image_message(image_path: Path, caption: str | None) -> str:
    """Build the synthetic brain message for an inbound image."""
    prefix = _INCOMING_BRAIN_PREFIX.format(path=image_path)
    body = (caption or "").strip()
    if body:
        return f"{prefix} {body}"
    return prefix


def _cleanup_incoming_images(
    now: datetime,
    *,
    directory: Path = _INCOMING_PHOTO_DIR,
    max_age: timedelta = _INCOMING_PHOTO_MAX_AGE,
) -> int:
    """Delete vexis-incoming-*.png files older than max_age. Returns count removed."""
    removed = 0
    for path in directory.glob(_INCOMING_PHOTO_GLOB):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except FileNotFoundError:
            continue
        if now - mtime <= max_age:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            log.exception("Failed to clean up incoming image %s", path)
            continue
        removed += 1
    return removed


def _extract_screenshot_paths(text: str) -> tuple[list[Path], str]:
    """Pull every screenshot path out of `text`.

    Recognises two flavours:

    - ``/tmp/vexis-screenshot-<n>.png`` — desktop captures from
      vexis-look. Ephemeral; deleted after send.
    - ``<workspace>/browser/screenshots/<ts>.png`` — captures from
      vexis-browse screenshot. Archived; left on disk after send so
      the brain (or the user) can re-reference them later.

    Returns the list of unique paths (in first-seen order) and the
    cleaned text with each match replaced by the placeholder
    ``[screenshot]`` so the surrounding prose still reads naturally.
    """
    seen: list[Path] = []
    seen_set: set[str] = set()
    for pattern in (_SCREENSHOT_PATH_RE, _BROWSER_SCREENSHOT_PATH_RE):
        for match in pattern.findall(text):
            if match in seen_set:
                continue
            seen_set.add(match)
            seen.append(Path(match))
    cleaned = _SCREENSHOT_PATH_RE.sub("[screenshot]", text)
    cleaned = _BROWSER_SCREENSHOT_PATH_RE.sub("[screenshot]", cleaned)
    return seen, cleaned


def _is_ephemeral_screenshot(path: Path) -> bool:
    """``/tmp/...`` captures are deleted after send; workspace
    screenshots are archived for later reference."""
    return path.parent == Path("/tmp")


# Telegram's Bot API caps photo uploads at 10000px in either
# dimension. Full-page browser screenshots of long pages routinely
# blow past this, so we fall back to send_document (no dimension
# cap, 50 MB file ceiling). Same upload mechanism, the image still
# appears inline in the chat — just rendered as a file attachment.
# The check is purely dimensional; nothing here is keyed on URL,
# domain, or which page produced the image.
_TELEGRAM_PHOTO_MAX_DIM = 10000


def _photo_too_large_for_telegram(path: Path) -> bool:
    """True when ``path`` exceeds Telegram's 10000px-per-dimension
    photo cap and should be sent via send_document instead. Purely
    dimensional — same predicate for any image, any source.
    """
    try:
        with Image.open(path) as img:
            width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError):
        # Can't read dimensions — let the existing send_photo path
        # surface whatever the real error is.
        return False
    return width > _TELEGRAM_PHOTO_MAX_DIM or height > _TELEGRAM_PHOTO_MAX_DIM


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
        self,
        token: str,
        handler: MessageHandler,
        running_tasks: RunningTasks,
        allowed_user_id: int,
        background_tasks: BackgroundTasks,
        notifier: Notifier,
        curator: "CuratorController | None" = None,
        dashboard: "WebDashboard | None" = None,
    ) -> None:
        self._handler = handler
        self._running_tasks = running_tasks
        self._background_tasks = background_tasks
        self._notifier = notifier
        self._allowed_user_id = allowed_user_id
        self._curator = curator
        self._dashboard = dashboard
        # Telegram bot commands can't contain hyphens, so /confirm-delete from
        # the spec becomes /confirm_delete here.
        self._pending_deletes: dict[str, datetime] = {}
        # concurrent_updates(True) is load-bearing for /cancel: PTB's default
        # serializes every update through one task, so a /cancel sent while
        # a brain call is in flight queues behind it for up to 30 minutes
        # and the user's "Cancelled, sir" reply never arrives in time.
        self._app = Application.builder().token(token).concurrent_updates(True).build()
        self._app.add_handler(
            PtbMessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text)
        )
        self._app.add_handler(PtbMessageHandler(filters.VOICE, self._on_voice))
        self._app.add_handler(PtbMessageHandler(filters.PHOTO, self._on_photo))
        self._app.add_handler(CommandHandler("clear", self._on_clear))
        self._app.add_handler(CommandHandler("new", self._on_new))
        self._app.add_handler(CommandHandler("switch", self._on_switch))
        self._app.add_handler(CommandHandler("sessions", self._on_sessions))
        self._app.add_handler(CommandHandler("rename", self._on_rename))
        self._app.add_handler(CommandHandler("delete", self._on_delete))
        self._app.add_handler(CommandHandler("confirm_delete", self._on_confirm_delete))
        self._app.add_handler(CommandHandler("screenshot", self._on_screenshot))
        self._app.add_handler(CommandHandler("cancel", self._on_cancel))
        self._app.add_handler(CommandHandler("tasks", self._on_tasks))
        self._app.add_handler(CommandHandler("status", self._on_status))
        self._app.add_handler(CommandHandler("pin", self._on_pin))
        self._app.add_handler(CommandHandler("unpin", self._on_unpin))
        self._app.add_handler(CommandHandler("curator", self._on_curator))
        self._app.add_handler(CommandHandler("dashboard", self._on_dashboard))
        self._app.add_handler(CallbackQueryHandler(self._on_callback))

    async def _on_text(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None or msg.text is None:
            return
        await self._dispatch_to_brain(msg.get_bot(), msg.chat_id, user.id, msg.text)

    async def _dispatch_to_brain(
        self, bot, chat_id: int, user_id: int, text: str
    ) -> None:
        """Submit a message to the brain.

        If a drain loop is already processing this chat, the message is
        appended to its queue silently — the user gets a "Picking up:"
        ack when the drain reaches it. Otherwise we claim the chat and
        run the drain loop ourselves until the queue is empty.
        """
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected message from user_id=%s", user_id)
            return

        if not await self._running_tasks.claim(chat_id):
            await self._running_tasks.enqueue(chat_id, user_id, text)
            return

        typing_task = asyncio.create_task(self._keep_typing(bot, chat_id))
        try:
            await self._drain_chat(bot, chat_id, user_id, text)
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            # Belt-and-suspenders: if _drain_chat aborted via an
            # unexpected exception before its own pop_or_release ran,
            # the chat would stay permanently "claimed" and the user
            # would never get another reply without a daemon restart.
            # force_release_drain is a no-op when the drain released
            # cleanly, and logs at warning level when it had to force.
            await self._running_tasks.force_release_drain(chat_id)

    async def _drain_chat(
        self, bot, chat_id: int, user_id: int, first_text: str
    ) -> None:
        """Process one message after another for chat_id until the queue
        empties or /cancel flags the drain. The caller must already hold
        the drain claim; this method releases it on exit.

        Each turn's brain call and reply send are wrapped individually,
        so a single broken turn (handler raising, Telegram send failing)
        logs and surfaces an error reply but does not kill the drain —
        queued follow-ups still run.
        """
        text = first_text
        is_first = True
        while True:
            if not is_first:
                preview = _make_pickup_preview(text)
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"{_PICKING_UP_PREFIX}{preview}",
                        parse_mode=None,
                    )
                except Exception:
                    log.exception("Failed to send pickup ack for chat %s", chat_id)
            try:
                reply = await self._handler.handle(user_id, chat_id, text)
            except Exception:
                log.exception(
                    "Drain turn raised unexpectedly for chat %s", chat_id
                )
                reply = _DRAIN_TURN_BROKE
            if reply is not None:
                try:
                    await self._send_brain_reply(bot, chat_id, reply)
                except Exception:
                    log.exception(
                        "Failed to send brain reply for chat %s", chat_id
                    )
            next_msg = await self._running_tasks.pop_or_release(chat_id)
            if next_msg is None:
                # If pop_or_release released because /cancel fired and a
                # follow-up message landed *during* the cancel cleanup,
                # those items are still in the queue. Reclaim and keep
                # draining so they don't get silently lost.
                next_msg = await self._running_tasks.take_over_if_pending(chat_id)
                if next_msg is None:
                    return
            text = next_msg.text
            user_id = next_msg.user_id
            is_first = False

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
        transcription: str | None = None
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
        finally:
            if typing_task is not None:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
            ogg_path.unlink(missing_ok=True)

        if transcription is None:
            return
        await self._dispatch_to_brain(
            bot, chat_id, user.id, f"{_VOICE_BRAIN_TAG}{transcription}"
        )

    async def _on_photo(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None or not msg.photo:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected photo from user_id=%s", user.id)
            return

        chat_id = msg.chat_id
        bot = msg.get_bot()

        # PhotoSize tuples are sorted small → large; the last one is the
        # highest-resolution variant Telegram has on file.
        photo = msg.photo[-1]
        image_path = _build_incoming_photo_path()

        tg_file = await photo.get_file()
        await tg_file.download_to_drive(custom_path=image_path)
        synthetic = _format_incoming_image_message(image_path, msg.caption)
        await self._dispatch_to_brain(bot, chat_id, user.id, synthetic)

    async def _on_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /cancel from user_id=%s", user.id)
            return
        args = ctx.args or []
        if len(args) > 1:
            await msg.reply_text("Usage: /cancel [name]")
            return
        if args:
            name = args[0]
            log.info("Received /cancel %s (background) from chat %d", name, msg.chat_id)
            try:
                cancelled = await self._background_tasks.cancel(name)
            except TaskNotFound:
                await msg.reply_text(f"No background task named '{name}'.")
                return
            if cancelled:
                await msg.reply_text(f"Cancelled background task `{name}`.")
            else:
                await msg.reply_text(f"Task `{name}` isn't running anymore.")
            return
        log.info("Received /cancel (foreground) from chat %d", msg.chat_id)
        cancelled = await self._running_tasks.cancel(msg.chat_id)
        log.info("Cancel result for chat %d: %s", msg.chat_id, cancelled)
        await msg.reply_text(_CANCEL_OK if cancelled else _NOTHING_TO_CANCEL)

    async def _on_tasks(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /tasks from user_id=%s", user.id)
            return
        summary = await self._background_tasks.status_summary()
        await msg.reply_text(_format_tasks(summary))

    async def _on_status(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Read-only window into the running brain.

        Reads the per-chat status file (written by the brain as it
        processes stream-json tool events) plus the running-tasks
        queue depth and last-idle timestamp, and replies with a short
        summary. Touches no shared mutable state — safe to call while
        a drain is mid-flight.
        """
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /status from user_id=%s", user.id)
            return
        chat_id = msg.chat_id
        snapshot = read_status(chat_id)
        queue_depth = self._running_tasks.queue_depth(chat_id)
        last_idle = self._running_tasks.last_idle_at(chat_id)
        reply = _format_status_reply(
            snapshot, queue_depth, last_idle, datetime.now(timezone.utc)
        )
        await msg.reply_text(reply)

    async def _on_pin(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        args = ctx.args or []
        if len(args) != 1:
            await msg.reply_text("Usage: /pin <skill-name>")
            return
        reply = await self._handler.handle_pin(user.id, args[0])
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_unpin(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        args = ctx.args or []
        if len(args) != 1:
            await msg.reply_text("Usage: /unpin <skill-name>")
            return
        reply = await self._handler.handle_unpin(user.id, args[0])
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_curator(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Stub — wired up in the curator task. Keeping the handler
        registered so the slash menu is honest about supporting it."""
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /curator from user_id=%s", user.id)
            return
        if self._curator is None:
            await msg.reply_text("Curator not initialised yet.")
            return
        args = ctx.args or []
        sub = args[0] if args else "status"
        rest = args[1:]
        try:
            reply = await self._curator.handle_telegram(sub, rest)
        except Exception:
            log.exception("/curator handler failed")
            reply = "⚠️ Curator command failed; check the logs."
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_dashboard(
        self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Reply with the dashboard URL plus current token.

        The token-in-URL pattern is acceptable here because the only
        people who can reach the dashboard host are on the user's
        tailnet, same security model as the livestream URL surfaced by
        the brain. The token rotates on every daemon restart, so old
        URLs from a previous session stop working automatically.
        """
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /dashboard from user_id=%s", user.id)
            return
        if self._dashboard is None:
            await msg.reply_text("Dashboard not initialised.")
            return
        url = self._dashboard.url or self._dashboard.local_url
        token = self._dashboard.token
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}token={token}"
        if self._dashboard.url is None:
            full += "\n\nNote: Tailscale Serve mapping unavailable; "
            full += "this URL only resolves on this host."
        await msg.reply_text(full)

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
            ephemeral = _is_ephemeral_screenshot(path)
            try:
                if not path.is_file():
                    log.warning("Brain referenced missing screenshot %s", path)
                    continue
                oversize = _photo_too_large_for_telegram(path)
                with path.open("rb") as fh:
                    if oversize:
                        log.info(
                            "Sending %s as document (exceeds Telegram photo "
                            "dimension cap)",
                            path.name,
                        )
                        await bot.send_document(
                            chat_id=chat_id, document=fh, filename=path.name
                        )
                    else:
                        await bot.send_photo(chat_id=chat_id, photo=fh)
            except Exception:
                log.exception("Failed to forward screenshot %s", path)
            finally:
                if ephemeral:
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
        await _register_commands(self._app)
        # Sweep status files left behind by a previous daemon's brain
        # that exited via SIGKILL (its finally never ran). Without this
        # /status would show stale "Working for 3 days" data forever.
        try:
            removed = cleanup_status_files()
            if removed:
                log.info("Swept %d stale status file(s) at startup", removed)
        except Exception:
            log.exception("Status-file cleanup failed at startup")
        # Bind the notifier to the freshly-initialised PTB application
        # and route background-task completions through it. Lost-task
        # warnings from the previous daemon restart fire before polling
        # opens so they're queued in both Telegram and the brain context
        # buffer for the user's first reply of this session.
        self._notifier.bind_app(self._app)
        self._background_tasks.set_notify(self._notifier.send)
        await self._notify_lost_tasks()
        await self._app.start()
        await self._app.updater.start_polling()
        log.info("Telegram polling started")
        cleanup_task = asyncio.create_task(_incoming_image_cleanup_loop())
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def _notify_lost_tasks(self) -> None:
        try:
            lost = await self._background_tasks.detect_lost_from_previous_run()
        except Exception:
            log.exception("Lost-task detection failed during startup")
            return
        for entry in lost:
            try:
                await self._notifier.send(
                    entry["chat_id"], _DAEMON_RESTART_LOST.format(name=entry["name"])
                )
            except Exception:
                log.exception(
                    "Failed to send daemon-restart notice for task '%s'",
                    entry.get("name"),
                )


async def _incoming_image_cleanup_loop() -> None:
    """Sweep stale /tmp/vexis-incoming-*.png files every 10 minutes."""
    while True:
        try:
            removed = _cleanup_incoming_images(datetime.now(timezone.utc))
            if removed:
                log.info("Cleaned up %d expired incoming image(s)", removed)
        except Exception:
            log.exception("Incoming-image cleanup failed")
        await asyncio.sleep(_INCOMING_PHOTO_CLEANUP_INTERVAL_SECONDS)


def _format_tasks(tasks: list[dict]) -> str:
    """Render the /tasks reply: running first, then recently finished."""
    if not tasks:
        return _NO_BG_TASKS
    now = datetime.now(timezone.utc)
    running: list[str] = []
    finished: list[str] = []
    for task in tasks:
        name = task["name"]
        status = task["status"]
        if status in (TaskStatus.RUNNING.value, TaskStatus.PENDING.value):
            spawned = _parse_iso(task.get("spawned_at"))
            age = _short_duration(now - spawned) if spawned else "?"
            running.append(f"  {name} — running {age}")
            continue
        finished_at = _parse_iso(task.get("finished_at"))
        age = _short_duration(now - finished_at) if finished_at else "?"
        if status == TaskStatus.FINISHED.value:
            label = "success"
        elif status == TaskStatus.FAILED.value:
            label = f"failed (exit {task.get('exit_code')})"
        elif status == TaskStatus.CANCELLED.value:
            label = "cancelled"
        else:
            label = status
        finished.append(f"  {name} — finished {age} ago, {label}")
    sections: list[str] = []
    if running:
        sections.append("Running:\n" + "\n".join(running))
    if finished:
        sections.append("Recently finished (last hour):\n" + "\n".join(finished))
    return "\n\n".join(sections) if sections else _NO_BG_TASKS


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _short_duration(td: timedelta) -> str:
    seconds = max(0, int(td.total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if minutes:
        return f"{hours}h{minutes}m"
    return f"{hours}h"


def _format_status_duration(td: timedelta) -> str:
    """Render a duration the way /status wants it.

    < 60s → "8s"; 60s–1h → "4m 12s" (seconds dropped if zero);
    ≥ 1h → "1h 4m" (minutes dropped if zero). Negative input clamps
    to zero so a clock skew can't produce gibberish.
    """
    secs = max(0, int(td.total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        m, s = divmod(secs, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    h, rem = divmod(secs, 3600)
    m = rem // 60
    return f"{h}h {m}m" if m else f"{h}h"


def _format_last_action(tool: str | None, target: str | None) -> str | None:
    """Render the 'Last action: ...' line, or None to omit it."""
    if tool is None:
        return None
    verb = _TOOL_VERB.get(tool)
    if target and verb:
        return f"Last action: {verb} `{target}`"
    if target:
        return f"Last action: used {tool} on `{target}`"
    return f"Last action: {tool}"


def _format_status_reply(
    snapshot: StatusSnapshot | None,
    queue_depth: int,
    last_idle_at: datetime | None,
    now: datetime,
) -> str:
    """Compose the user-visible /status reply.

    Pure function: takes the three pieces of state /status reads and
    returns the formatted string. Easy to unit-test and avoids
    interleaving formatting concerns with the I/O in ``_on_status``.
    """
    if snapshot is not None:
        lines = [f"Working for {_format_status_duration(now - snapshot.started_at)}."]
        if snapshot.tool_count > 0:
            action_line = _format_last_action(
                snapshot.last_tool, snapshot.last_target
            )
            if action_line is not None:
                lines.append(action_line)
            lines.append(f"Tools used: {snapshot.tool_count}")
        else:
            lines[0] = lines[0].rstrip(".") + ". " + _STATUS_NO_TOOLS_YET
        if queue_depth > 0:
            lines.append(f"Queued follow-ups: {queue_depth}")
        return "\n".join(lines)
    if last_idle_at is not None:
        return f"{_STATUS_IDLE} Idle for {_format_status_duration(now - last_idle_at)}."
    return _STATUS_IDLE


async def _register_commands(application: Application) -> None:
    """Mirror the canonical COMMANDS list to Telegram's slash menu.

    Failure here (network error, bad token, API hiccup) must not block
    daemon startup — the menu would just stay stale until the next
    successful restart.
    """
    bot_commands = [
        TelegramBotCommand(cmd.command, cmd.description) for cmd in COMMANDS
    ]
    try:
        await application.bot.set_my_commands(bot_commands)
    except Exception as exc:
        log.warning("Could not register Telegram commands: %s", exc)
        return
    log.info("Registered %d Telegram commands with Bot API", len(bot_commands))
