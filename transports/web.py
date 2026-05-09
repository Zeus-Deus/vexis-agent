"""Web chat transport — handler-call shim for the dashboard chat UI.

Mirrors the role of :mod:`transports.telegram` but with no protocol
plumbing. The dashboard's FastAPI routes own HTTP, JSON, and the
``_require_auth`` dependency; this module is the single seam they
call into so the chat UI and Telegram bot share one
:class:`core.handler.MessageHandler` instance — and therefore one
:class:`core.sessions.SessionStore`, one :class:`core.notify.Notifier`,
and one brain.

Single-user by design (CLAUDE.md). Every handler call is dispatched
on behalf of ``allowed_user_id``; the ``chat_id`` namespace is a
distinct negative magic constant so the notifier's per-chat context
buffer can't cross-contaminate Telegram and web (Telegram chat ids
are conventionally positive int64 user/chat ids).

Why not stream replies yet? :meth:`Brain.respond` returns the full
reply once the brain finishes the turn — there's no streaming
primitive on the ABC. Phase 1 ships buffered ("thinking…" → full
reply); a streaming variant is a separate piece of work that needs
to thread through every brain implementation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.handler import MessageHandler

log = logging.getLogger(__name__)


# Negative so it can never collide with a Telegram chat_id (which
# Python-Telegram-Bot reports as positive int64 for user chats and
# negative for groups, but those cluster around -10**12 — we sit
# safely outside both bands). One web chat_id for all browser tabs:
# Vexis is single-user and the notifier's per-chat buffer doesn't
# need finer granularity than "Telegram vs web". Future per-tab
# isolation would refine this; for now context is unified across
# whatever browser session you happen to have open.
WEB_CHAT_ID: int = -1


# Preview-snippet length. 80 chars fits comfortably under a session
# row name on a 256px-wide sidebar without horizontal scroll, and
# carries enough leading context that the user can recognise the
# topic at a glance ("write a script to…", "help me debug…").
_PREVIEW_MAX_CHARS: int = 80


def _truncate_preview(text: str) -> str:
    """Collapse multi-line / extra-whitespace text into a single
    line, cap at ``_PREVIEW_MAX_CHARS`` with an ellipsis when
    truncated. Stripped before measuring so leading newlines or
    indentation don't burn budget."""
    cleaned = " ".join(text.split())  # collapse all whitespace runs
    if len(cleaned) <= _PREVIEW_MAX_CHARS:
        return cleaned
    return cleaned[: _PREVIEW_MAX_CHARS - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class WebSessionInfo:
    """Wire-format session record for the chat UI.

    Subset of :class:`core.sessions.SessionInfo` — drops the brain
    UUID (irrelevant to the UI, leaks an implementation detail) and
    the ``initialized`` flag (UI doesn't differentiate yet). ISO-8601
    UTC timestamp so the browser can format with ``Intl.DateTimeFormat``
    in the user's locale without hauling a date library through the API.

    ``preview`` is a short snippet of the session's first user message
    (truncated to ~80 chars), shown under the session name in the
    sidebar so the user can find conversations by content rather than
    scrolling through a list of date-stamped names. ``None`` when the
    session is empty or its transcript can't be read (e.g. fresh
    just-created session, brain backend unavailable).
    """

    name: str
    is_active: bool
    created_at: str  # ISO-8601 UTC
    preview: str | None = None


class WebChatTransport:
    """Handler-call shim. No HTTP, no JSON, no auth — those live in
    :mod:`core.web_server`. This class exists so the dashboard
    routes have one cohesive object to call into and the test
    surface is small.
    """

    def __init__(self, handler: MessageHandler, allowed_user_id: int) -> None:
        self._handler = handler
        self._user_id = allowed_user_id
        # Cache of session_uuid → first-user-message preview snippet.
        # The first user turn is append-only on the brain side (claude
        # writes it once at session init and never rewrites earlier
        # turns), so a cached preview never goes stale. We don't bound
        # the cache because session count is single-user and grows
        # slowly; if it gets to 10k+ entries we can revisit.
        self._preview_cache: dict[str, str | None] = {}

    # ---------- conversation ----------

    async def send(
        self,
        text: str,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
    ) -> str | None:
        """Send a user message; return the brain's reply (or ``None``
        if the handler suppressed it — currently only happens when the
        underlying user_id fails the allow-list check, which shouldn't
        be reachable through the dashboard's token-gated route, but
        we forward the ``None`` rather than raising so the route can
        respond with a clean 401 if it ever does).

        ``model`` and ``reasoning_level`` are optional per-turn
        overrides (voice call mode passes them through from
        ``voice.call_mode.{model,reasoning_level}`` config). ``None``
        on either keeps the brain's account default; Telegram and
        the text-chat tab always pass ``None`` for both."""
        return await self._handler.handle(
            self._user_id, WEB_CHAT_ID, text,
            model=model, reasoning_level=reasoning_level,
        )

    async def clear(self) -> str | None:
        return await self._handler.handle_clear(self._user_id)

    async def cancel(self, running_tasks) -> bool:
        """Cancel any in-flight brain turn for the web chat.

        Routes through ``RunningTasks.cancel(WEB_CHAT_ID)`` — same
        kill-the-subprocess path Telegram's ``/cancel`` slash uses.
        Returns True iff something was actually cancelled (a turn
        was running). False when there's nothing in flight (still a
        valid call — the stop button might double-fire).
        """
        return await running_tasks.cancel(WEB_CHAT_ID)

    async def stream(
        self,
        text: str,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
    ):
        """Streaming variant of :meth:`send`. Yields ``("chunk", str)``
        per incremental text fragment, ``("done", full_reply)`` once
        at the end, or ``("error", message)`` on failure. Same
        per-turn override semantics as ``send``."""
        async for event in self._handler.stream(
            self._user_id, WEB_CHAT_ID, text,
            model=model, reasoning_level=reasoning_level,
        ):
            yield event

    # ---------- session management ----------

    async def new_session(self, name: str | None = None) -> str | None:
        return await self._handler.handle_new(self._user_id, name)

    async def switch_session(self, name: str) -> str | None:
        return await self._handler.handle_switch(self._user_id, name)

    async def rename_session(self, old: str, new: str) -> str | None:
        return await self._handler.handle_rename(self._user_id, old, new)

    async def delete_session(self, name: str) -> str | None:
        return await self._handler.handle_delete(self._user_id, name)

    def history(self, name: str, limit: int = 50) -> list[dict] | None:
        """Backfill the last ``limit`` turns of a named session for
        the chat UI. Returns ``None`` only when the user_id allow-
        list rejects (route maps to 401); empty list for unknown /
        empty sessions (route returns 200 with empty messages).
        Each entry: ``{role, content, ts}`` where ``ts`` is
        unix milliseconds (matches the in-memory ChatMessage shape)."""
        return self._handler.handle_history(self._user_id, name, limit=limit)

    def list_sessions(self) -> list[WebSessionInfo] | None:
        """Snapshot the session list in wire format. Returns ``None``
        only when the handler rejects the user_id (shouldn't happen
        behind the auth gate, but we forward the signal rather than
        masking it).

        Each entry includes a ``preview`` snippet sourced from the
        session's first user message — lets the sidebar render
        searchable previews under each date-stamped name. Previews
        are cached by session UUID; first call cold-reads the
        transcript (cheap on tmpfs; first line of a JSONL), subsequent
        calls hit the in-process cache.
        """
        infos = self._handler.sessions_for(self._user_id)
        if infos is None:
            return None
        return [
            WebSessionInfo(
                name=info.name,
                is_active=info.is_active,
                created_at=info.created_at.isoformat(),
                preview=self._preview_for(info.uuid),
            )
            for info in infos
        ]

    def _preview_for(self, session_uuid: str) -> str | None:
        """Return the first-user-message preview snippet for the
        given session, computing+caching on first hit.

        Reads at most a handful of messages until it finds a
        user-role turn — defensive against transcripts that lead
        with a system or assistant message (shouldn't happen for
        vexis-spawned sessions but cheap insurance). Returns
        ``None`` when the brain has no transcript reader, the
        session is empty, or anything in the read path raises —
        the sidebar gracefully renders just the name in that case.
        """
        cached = self._preview_cache.get(session_uuid)
        if cached is not None or session_uuid in self._preview_cache:
            return cached
        snippet: str | None = None
        try:
            brain = self._handler.brain
            # Walk a small prefix of messages so a malformed early
            # turn doesn't shadow a perfectly good user message a
            # few entries in. Cap at 5 to bound worst-case cost.
            for i, msg in enumerate(brain.iter_messages(session_uuid)):
                if i > 5:
                    break
                role = getattr(msg, "role", None)
                text = getattr(msg, "text", None)
                if role == "user" and isinstance(text, str) and text.strip():
                    snippet = _truncate_preview(text)
                    break
        except Exception:
            # Any read failure (missing transcript, malformed JSONL,
            # opencode SQLite locked, brain not initialized) → no
            # preview. Don't surface as an error to the user —
            # the session row stays usable without one.
            log.debug(
                "preview lookup failed for session %s",
                session_uuid, exc_info=True,
            )
            snippet = None
        self._preview_cache[session_uuid] = snippet
        return snippet
