"""Per-message orchestration: auth, brain dispatch, error normalization."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from brains.base import Brain
from brains.claude_code import BrainCancelled, BrainTimeoutError, SessionLost
from core.auth import is_allowed
from core.notify import ContextNote, Notifier
from core.sessions import SessionInfo, SessionStore

log = logging.getLogger(__name__)

_BRAIN_ERROR = "⚠️ Something broke. Logs have details."
_SESSION_LOST = (
    "⚠️ Couldn't resume the previous conversation. "
    "Starting fresh — please send your message again."
)
_BRAIN_TIMEOUT = (
    "Sir, that ran past my 30-minute ceiling. Either I got stuck or the "
    "task was bigger than I expected. Tell me what to do — retry, "
    "rephrase, or stop?"
)
_EMPTY_RESPONSE = "(empty response)"
_CLEAR_OK = "Conversation cleared."


class MessageHandler:
    def __init__(
        self,
        brain: Brain,
        sessions: SessionStore,
        allowed_user_id: int,
        *,
        notifier: Notifier | None = None,
    ) -> None:
        self._brain = brain
        self._sessions = sessions
        self._allowed_user_id = allowed_user_id
        # The notifier owns the per-chat context buffer. We consume from
        # it at the start of every brain turn so events that fired since
        # the last reply (background task completions, daemon-restart
        # warnings) become visible to claude -p as a [SYSTEM CONTEXT]
        # block prepended to the user's message.
        self._notifier = notifier

    async def handle(self, user_id: int, chat_id: int, text: str) -> str | None:
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected message from user_id=%s", user_id)
            return None

        message = await self._inject_context(chat_id, text)

        try:
            reply = await self._brain.respond(message, chat_id)
        except BrainCancelled:
            # /cancel handler already replied; nothing more to send.
            return None
        except BrainTimeoutError:
            log.warning("Brain timed out for chat_id=%s", chat_id)
            return _BRAIN_TIMEOUT
        except SessionLost:
            return _SESSION_LOST
        except Exception:
            log.exception("Brain call failed")
            return _BRAIN_ERROR

        return reply.strip() or _EMPTY_RESPONSE

    async def _inject_context(self, chat_id: int, text: str) -> str:
        """Prepend any pending system notes to the user's message.

        Notes are buffered by the notifier as side effects of background
        events (task completions, restart warnings) and consumed atomically
        here. If the buffer is empty the message is returned unchanged.
        """
        if self._notifier is None:
            return text
        notes = await self._notifier.consume_context(chat_id)
        if not notes:
            return text
        log.info(
            "Injecting %d system context note(s) into chat %d brain turn",
            len(notes),
            chat_id,
        )
        return _format_with_context(notes, text)

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

    def sessions_for(self, user_id: int) -> list[SessionInfo] | None:
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected session-list-for from user_id=%s", user_id)
            return None
        return self._sessions.list()

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


_SYSTEM_CONTEXT_HEADER = "[SYSTEM CONTEXT — events since your last reply]"
_USER_MESSAGE_HEADER = "[USER MESSAGE]"


def _format_with_context(notes: list[ContextNote], user_text: str) -> str:
    """Render the [SYSTEM CONTEXT] / [USER MESSAGE] envelope for the brain.

    Each note becomes one bullet line: ``- HH:MM <text>``. Multi-line
    note bodies have their continuation lines indented under the bullet
    so the structure stays readable to both the brain and a human
    inspecting the log.
    """
    lines: list[str] = []
    for note in notes:
        local_time = note.timestamp.astimezone().strftime("%H:%M")
        body_lines = note.text.splitlines() or [""]
        first, *rest = body_lines
        lines.append(f"- {local_time} {first}")
        for cont in rest:
            lines.append(f"  {cont}")
    block = "\n".join((_SYSTEM_CONTEXT_HEADER, *lines))
    return f"{block}\n\n{_USER_MESSAGE_HEADER}\n{user_text}"


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
