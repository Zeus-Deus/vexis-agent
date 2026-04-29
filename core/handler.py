"""Per-message orchestration: auth, brain dispatch, error normalization."""

from __future__ import annotations

import asyncio
import logging

from brains.base import Brain
from brains.claude_code import SessionLost
from core.auth import is_allowed
from core.session import SessionStore

log = logging.getLogger(__name__)

_BRAIN_ERROR = "⚠️ Something broke. Logs have details."
_SESSION_LOST = (
    "⚠️ Couldn't resume the previous conversation. "
    "Starting fresh — please send your message again."
)
_EMPTY_RESPONSE = "(empty response)"
_NEW_SESSION_OK = "Started a new conversation."


class MessageHandler:
    def __init__(
        self, brain: Brain, session: SessionStore, allowed_user_id: int
    ) -> None:
        self._brain = brain
        self._session = session
        self._allowed_user_id = allowed_user_id
        self._lock = asyncio.Lock()

    async def handle(self, user_id: int, text: str) -> str | None:
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected message from user_id=%s", user_id)
            return None

        async with self._lock:
            try:
                reply = await self._brain.respond(text)
            except SessionLost:
                return _SESSION_LOST
            except Exception:
                log.exception("Brain call failed")
                return _BRAIN_ERROR

        return reply.strip() or _EMPTY_RESPONSE

    async def handle_clear(self, user_id: int) -> str | None:
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected /clear from user_id=%s", user_id)
            return None
        new_id = self._session.rotate()
        log.info("Rotated session id to %s", new_id)
        return _NEW_SESSION_OK
