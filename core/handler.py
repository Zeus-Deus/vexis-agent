"""Per-message orchestration: auth, brain dispatch, error normalization."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from brains.base import Brain
from brains.claude_code import SessionLost
from core.auth import is_allowed
from core.sessions import SessionInfo, SessionStore

log = logging.getLogger(__name__)

_BRAIN_ERROR = "⚠️ Something broke. Logs have details."
_SESSION_LOST = (
    "⚠️ Couldn't resume the previous conversation. "
    "Starting fresh — please send your message again."
)
_EMPTY_RESPONSE = "(empty response)"
_CLEAR_OK = "Conversation cleared."


class MessageHandler:
    def __init__(
        self, brain: Brain, sessions: SessionStore, allowed_user_id: int
    ) -> None:
        self._brain = brain
        self._sessions = sessions
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
        new_id = self._sessions.rotate()
        log.info("Rotated active session uuid to %s", new_id)
        return _CLEAR_OK

    async def handle_new(self, user_id: int, name: str | None) -> str | None:
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected /new from user_id=%s", user_id)
            return None
        try:
            created = self._sessions.create(name)
        except ValueError as exc:
            return f"⚠️ {exc}"
        log.info("Created session '%s' and switched to it", created)
        return f"Started new session: {created}"

    async def handle_switch(self, user_id: int, name: str) -> str | None:
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected /switch from user_id=%s", user_id)
            return None
        if not self._sessions.switch(name):
            return f"No session named {name}. Try /sessions to list."
        log.info("Switched active session to '%s'", name)
        return f"Switched to {name}"

    async def handle_sessions(self, user_id: int) -> str | None:
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected /sessions from user_id=%s", user_id)
            return None
        return _format_sessions(self._sessions.list())

    async def handle_rename(self, user_id: int, old: str, new: str) -> str | None:
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected /rename from user_id=%s", user_id)
            return None
        try:
            ok = self._sessions.rename(old, new)
        except ValueError as exc:
            return f"⚠️ {exc}"
        if not ok:
            return (
                f"Could not rename: '{old}' doesn't exist or '{new}' is already taken."
            )
        log.info("Renamed session '%s' to '%s'", old, new)
        return f"Renamed {old} to {new}"

    async def handle_delete(self, user_id: int, name: str) -> str | None:
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected /delete from user_id=%s", user_id)
            return None
        try:
            ok = self._sessions.delete(name)
        except ValueError as exc:
            return f"⚠️ {exc}"
        if not ok:
            return f"No session named {name}."
        log.info("Deleted session '%s'", name)
        return f"Deleted {name}"


def _format_sessions(infos: list[SessionInfo]) -> str:
    now = datetime.now(timezone.utc)
    ordered = sorted(infos, key=lambda i: i.created_at, reverse=True)
    lines = []
    for info in ordered:
        marker = "→" if info.is_active else " "
        lines.append(
            f"{marker} {info.name} (created {_relative_time(info.created_at, now)})"
        )
    return "\n".join(lines)


def _relative_time(when: datetime, now: datetime) -> str:
    days = (now - when).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"
