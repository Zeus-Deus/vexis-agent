"""Daemon-side Telegram messaging plus a per-chat context buffer.

Background events (task completion, daemon restart) need two things to
reach Vexis correctly:

  1. The user must see them in Telegram.
  2. The brain must learn about them so the next user reply is
     interpreted in light of the event.

A bare ``bot.send_message`` only does (1). Without (2), the brain's
session ends with the last user-visible reply it produced; the
"finished" toast lands in Telegram but is invisible to claude -p, so
when the user types "yea" the brain answers whatever it last asked
about.

This module is the bridge for both halves. ``send`` does the Telegram
delivery AND buffers a parallel context note keyed by chat_id. The
foreground handler calls ``consume_context`` at the start of each
brain turn and prepends a ``[SYSTEM CONTEXT]`` block to the user's
message so the brain sees the events that fired since its last reply.

Buffering is independent of delivery: the context note is stored even
if Telegram delivery fails or the app isn't bound yet. The brain
should still know about an event the user *would have* seen.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextNote:
    """One buffered system event awaiting injection into a brain turn."""

    timestamp: datetime
    text: str


class Notifier:
    """Send Telegram messages and buffer parallel context notes.

    The PTB application is optional at construction so the same
    instance can be wired into the handler before Telegram has
    initialised. Call ``bind_app`` once the application is ready.
    """

    def __init__(self, telegram_app=None) -> None:
        self._app = telegram_app
        self._pending: dict[int, list[ContextNote]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def bind_app(self, telegram_app) -> None:
        """Attach the PTB application once it has been initialised."""
        self._app = telegram_app

    async def send(self, chat_id: int, text: str) -> None:
        """Send a message AND buffer a parallel context note.

        The buffer fires regardless of Telegram delivery — the brain's
        view should not depend on whether the toast made it through
        Bot API plumbing.
        """
        await self.append_context(chat_id, text)
        await self._send_telegram(chat_id, text)

    async def append_context(self, chat_id: int, text: str) -> None:
        note = ContextNote(timestamp=datetime.now(timezone.utc), text=text)
        async with self._lock:
            self._pending[chat_id].append(note)

    async def consume_context(self, chat_id: int) -> list[ContextNote]:
        """Return and clear pending notes for a chat. Order: chronological."""
        async with self._lock:
            notes = self._pending.pop(chat_id, [])
        return list(notes)

    async def _send_telegram(self, chat_id: int, text: str) -> None:
        if self._app is None:
            log.warning(
                "notifier app not bound; dropping telegram send for chat %s",
                chat_id,
            )
            return
        try:
            await self._app.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="Markdown"
            )
            return
        except Exception as exc:
            # Markdown can fail on stray characters; retry as plain so
            # the user still sees the message.
            log.warning(
                "Markdown send_message failed (chat=%s): %s; retrying plain",
                chat_id,
                exc,
            )
        try:
            await self._app.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=None
            )
        except Exception:
            log.exception("Plain send_message also failed (chat=%s)", chat_id)
