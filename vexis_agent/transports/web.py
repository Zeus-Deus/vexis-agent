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

Per-conversation web sessions (issue #48). A caller MAY pass a
transport-level ``conversation_id`` (an arbitrary opaque string the
frontend owns — a browser tab id, a "New chat" uuid) to ``send`` /
``stream`` / ``clear`` / ``cancel`` / history. This module — and ONLY
this module — maps that transport concept onto core's per-turn session
seam: each ``conversation_id`` deterministically derives (a) a named
``SessionStore`` session (``conversation_session_name``) so each
conversation has its own brain session id, and (b) a private ``chat_id``
band (``conversation_chat_id``) so the notifier's per-chat context
buffer stays isolated per conversation. Core never learns the word
"conversation"; it only ever sees a named :class:`SessionView` and an
int ``chat_id``. Requests WITHOUT a ``conversation_id`` are byte-for-byte
the historical shared-web-chat path (``WEB_CHAT_ID``, active session).

Not an auth boundary. ``conversation_id`` namespaces *context*, not
access: Vexis is single-user, the dashboard token is one trust domain,
and anyone holding it can read or drive any conversation. Conversation
ids partition memory, not permissions.

Why not stream replies yet? :meth:`Brain.respond` returns the full
reply once the brain finishes the turn — there's no streaming
primitive on the ABC. Phase 1 ships buffered ("thinking…" → full
reply); a streaming variant is a separate piece of work that needs
to thread through every brain implementation.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from vexis_agent.core.handler import MessageHandler
from vexis_agent.core.sessions import SessionView

log = logging.getLogger(__name__)


# The default/legacy shared web chat_id. Negative so it can never
# collide with a Telegram chat_id (Python-Telegram-Bot reports positive
# int64 for user chats and negative for groups clustered around
# -10**12 — we sit safely outside both bands). Requests with no
# ``conversation_id`` still land here: one shared session, one context
# buffer, exactly as before issue #48. Per-conversation traffic gets its
# own disjoint chat-id band instead (see ``conversation_chat_id``).
WEB_CHAT_ID: int = -1


# ── Per-conversation mapping (issue #48) ─────────────────────────────
#
# A transport-level ``conversation_id`` is an opaque, frontend-owned
# string. These pure helpers derive the two core-facing identifiers a
# conversation needs — a named session and a private chat_id — with NO
# shared state, so the mapping is deterministic and restart-safe: the
# same id always resolves to the same session name and chat_id across
# daemon restarts, and two ids never collide (the sha256 suffix breaks
# ties even when two ids sanitise to the same slug).

# Named-session prefix. Conversation sessions are ordinary named
# ``SessionStore`` sessions — visible/renameable/deletable in the
# dashboard sidebar — so the prefix is purely a human-readable "this
# came from a web conversation" marker, not a reserved namespace.
_CONVERSATION_SESSION_PREFIX = "web-"
# Hard cap on the raw id we accept. 200 chars is generous for any tab
# id / uuid / slug a frontend would mint, and bounds the work the
# sanitiser and the session store do per call.
_CONVERSATION_ID_MAX_CHARS = 200
# Slug budget inside the derived name. Prefix (4) + slug (≤40) + "-" (1)
# + 8-hex hash (8) = ≤53 chars, comfortably under SessionStore's 64-char
# ``_NAME_RE`` ceiling.
_CONVERSATION_SLUG_MAX_CHARS = 40
# Runs of characters outside the SessionStore name alphabet collapse to
# a single "-" in the slug.
_CONVERSATION_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")
# chat-id band: -(2**52) - n, where n is a 48-bit sha256 prefix. The
# band [-(2**52 + 2**48), -(2**52)] is disjoint from WEB_CHAT_ID (-1)
# and from Telegram group ids (~-10**12 .. -10**13), and every value
# fits comfortably in a signed int64. Negative like every other web
# chat_id so it can never be mistaken for a Telegram user chat.
_CONVERSATION_CHAT_ID_BASE = 2 ** 52


def validate_conversation_id(cid) -> str:
    """Validate a transport-level ``conversation_id`` and return its
    stripped form. Raises ``ValueError`` (with a user-facing message
    the route turns into a 400) when it is not a non-empty string of at
    most ``_CONVERSATION_ID_MAX_CHARS`` characters after stripping.

    The stripped value is what the mapping helpers key on, so
    ``" abc "`` and ``"abc"`` resolve to the same conversation."""
    if not isinstance(cid, str):
        raise ValueError("conversation_id must be a string")
    stripped = cid.strip()
    if not stripped:
        raise ValueError("conversation_id must not be empty")
    if len(stripped) > _CONVERSATION_ID_MAX_CHARS:
        raise ValueError(
            "conversation_id must be at most "
            f"{_CONVERSATION_ID_MAX_CHARS} characters"
        )
    return stripped


def _conversation_hash8(cid: str) -> str:
    """First 8 hex chars of sha256(cid) — the tie-breaker suffix that
    keeps two distinct ids with the same sanitised slug distinct."""
    return hashlib.sha256(cid.encode("utf-8")).hexdigest()[:8]


def conversation_session_name(cid: str) -> str:
    """Derive the ``SessionStore`` session name for a conversation id.

    Deterministic. Slug = the id with every run of non-``[A-Za-z0-9_-]``
    collapsed to ``-`` and leading/trailing ``-`` stripped, truncated to
    ``_CONVERSATION_SLUG_MAX_CHARS`` (then re-stripped so truncation
    can't leave a trailing ``-``). Name = ``web-{slug}-{hash8}``, or
    ``web-{hash8}`` when the slug is empty (an id of only punctuation).
    Always ≤ 64 chars and always matches SessionStore's ``_NAME_RE``; the
    hash suffix makes ids with identical slugs (``a/b`` vs ``a-b``) map to
    different sessions."""
    slug = _CONVERSATION_SLUG_RE.sub("-", cid).strip("-")
    slug = slug[:_CONVERSATION_SLUG_MAX_CHARS].strip("-")
    hash8 = _conversation_hash8(cid)
    if slug:
        return f"{_CONVERSATION_SESSION_PREFIX}{slug}-{hash8}"
    return f"{_CONVERSATION_SESSION_PREFIX}{hash8}"


def conversation_chat_id(cid: str) -> int:
    """Derive the private notifier/RunningTasks ``chat_id`` for a
    conversation id. Deterministic; lands strictly inside the band
    ``[-(2**52 + 2**48), -(2**52)]`` (48-bit sha256 prefix), disjoint
    from ``WEB_CHAT_ID`` and Telegram ids, and never collides for two
    distinct ids short of a 48-bit sha256 prefix collision."""
    n = int.from_bytes(hashlib.sha256(cid.encode("utf-8")).digest()[:6], "big")
    return -(_CONVERSATION_CHAT_ID_BASE + n)


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

    # ---------- conversation mapping (issue #48) ----------

    def _conversation_view(
        self, cid: str, *, create: bool = True,
    ) -> tuple[SessionView | None, int]:
        """Resolve a validated ``conversation_id`` to ``(view, chat_id)``.

        With ``create=True`` (the default for send/stream/clear) the
        conversation's named session is ``ensure``-created if missing —
        WITHOUT disturbing the active session pointer — and a
        :class:`SessionView` bound to it is returned. With
        ``create=False`` (history reads) the session is NOT created:
        ``(None, chat_id)`` is returned when it doesn't exist yet, so a
        history request for a never-used conversation reports "empty"
        rather than minting an empty session as a side effect.

        The ``chat_id`` is always returned (it's a pure derivation) so
        callers that only need it — ``cancel`` — don't touch the store.
        """
        name = conversation_session_name(cid)
        chat_id = conversation_chat_id(cid)
        sessions = self._handler.sessions
        if create:
            sessions.ensure(name)
            return SessionView(sessions, name), chat_id
        exists = any(info.name == name for info in sessions.list())
        if not exists:
            return None, chat_id
        return SessionView(sessions, name), chat_id

    # ---------- conversation ----------

    async def send(
        self,
        text: str,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
        conversation_id: str | None = None,
        attachments: list[Path] | None = None,
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
        the text-chat tab always pass ``None`` for both.

        ``conversation_id`` (issue #48, already validated by the route)
        routes the turn to that conversation's own session + chat_id.
        ``None`` is the legacy shared-web-chat path, byte-identical to
        before.

        ``attachments`` (already validated by the route against the
        upload sandbox) is forwarded to the handler only when
        non-empty, so a legacy caller passing nothing keeps the
        historical call shape."""
        attachment_kwargs = {"attachments": attachments} if attachments else {}
        if conversation_id is not None:
            view, chat_id = self._conversation_view(conversation_id)
            return await self._handler.handle(
                self._user_id, chat_id, text,
                model=model, reasoning_level=reasoning_level,
                session=view, **attachment_kwargs,
            )
        return await self._handler.handle(
            self._user_id, WEB_CHAT_ID, text,
            model=model, reasoning_level=reasoning_level,
            **attachment_kwargs,
        )

    async def clear(self, conversation_id: str | None = None) -> str | None:
        """Clear a conversation (issue #48) or the shared web chat.

        With ``conversation_id`` set we ``ensure``-create the session
        first: clearing a conversation that was never used is a harmless
        fresh rotate (the session comes into existence already-empty and
        gets a new uuid), which keeps the route's contract simple —
        clear always succeeds. ``None`` rotates the active session,
        exactly as before."""
        if conversation_id is not None:
            view, _ = self._conversation_view(conversation_id)
            return await self._handler.handle_clear(
                self._user_id, session=view,
            )
        return await self._handler.handle_clear(self._user_id)

    async def cancel(self, running_tasks, conversation_id: str | None = None) -> bool:
        """Cancel any in-flight brain turn for a conversation (or the
        shared web chat).

        Routes through ``RunningTasks.cancel`` — same kill-the-subprocess
        path Telegram's ``/cancel`` slash uses — against the
        conversation's derived chat_id (issue #48) or ``WEB_CHAT_ID``.
        Returns True iff something was actually cancelled (a turn
        was running). False when there's nothing in flight (still a
        valid call — the stop button might double-fire).
        """
        chat_id = (
            conversation_chat_id(conversation_id)
            if conversation_id is not None else WEB_CHAT_ID
        )
        return await running_tasks.cancel(chat_id)

    async def stream(
        self,
        text: str,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
        conversation_id: str | None = None,
        attachments: list[Path] | None = None,
    ):
        """Streaming variant of :meth:`send`. Yields ``("chunk", str)``
        per incremental text fragment, ``("done", full_reply)`` once
        at the end, or ``("error", payload)`` on failure. Same
        per-turn override + ``conversation_id`` + ``attachments``
        semantics as ``send``."""
        attachment_kwargs = {"attachments": attachments} if attachments else {}
        if conversation_id is not None:
            view, chat_id = self._conversation_view(conversation_id)
            async for event in self._handler.stream(
                self._user_id, chat_id, text,
                model=model, reasoning_level=reasoning_level,
                session=view, **attachment_kwargs,
            ):
                yield event
            return
        async for event in self._handler.stream(
            self._user_id, WEB_CHAT_ID, text,
            model=model, reasoning_level=reasoning_level,
            **attachment_kwargs,
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

    def history_for_conversation(
        self, conversation_id: str, limit: int = 50,
    ) -> list[dict] | None:
        """Backfill a conversation's history by its ``conversation_id``
        (issue #48). Resolves the id to its derived session name and
        delegates to :meth:`history`. Because ``handle_history`` returns
        ``[]`` for an unknown name and never creates a session, a
        history read for a never-used conversation is a pure read — it
        reports empty without minting a session. ``None`` only on the
        allow-list reject (route → 401), same as :meth:`history`."""
        name = conversation_session_name(conversation_id)
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
