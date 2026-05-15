"""Per-message orchestration: auth, brain dispatch, error normalization."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from vexis_agent.core.brain.base import (
    Brain,
    BrainCancelled,
    BrainPermanentError,
    BrainTimeoutError,
    BrainTransientError,
    SessionLost,
)
from vexis_agent.core.auth import is_allowed
from vexis_agent.core.notify import ContextNote, Notifier
from vexis_agent.core.paths import skills_dir
from vexis_agent.core.sessions import SessionInfo, SessionStore
from vexis_agent.core.skills import PinStore, archived_skill_names
from vexis_agent.core.transcripts import claude_session_jsonl_dir, iter_messages

log = logging.getLogger(__name__)

_BRAIN_ERROR = "⚠️ Something broke. Logs have details."
# Transient: the upstream API hiccupped. The brain wrapper has
# already retried once inline — if we got here both attempts failed.
# Be specific about what happened so the user knows whether to wait
# or check status.claude.com.
_BRAIN_TRANSIENT_PREFIX = "⚠️ Upstream API hiccup — "
# Permanent: auth, model id, malformed request. Retrying won't help;
# the user has to fix something (re-auth, switch model, top up credit).
_BRAIN_PERMANENT_PREFIX = "⚠️ "
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

# ── Streaming error taxonomy ─────────────────────────────────────
# The chat UI distinguishes error categories so it can render
# specific recovery affordances (e.g. an inline "Retry" button on
# transient brain errors but not on auth failures). The codes are
# wire-stable strings that travel through the SSE ``error`` event.
# Adding a new code = update the frontend's ``mapErrorCode`` helper.

# Brain returned a non-zero exit / unparseable output. Used as the
# catch-all when the classifier didn't find a known transient or
# permanent pattern. Retry button OK; the user is choosing to retry
# blindly.
_ERR_CODE_BRAIN_ERROR = "brain_error"
# Brain failed with a recognised transient cause (Anthropic 5xx /
# 429 / network blip) and the inline retry didn't recover.
# Distinguished from ``brain_error`` so the UI can render a softer
# affordance ("Try again in a moment") instead of a hard error.
_ERR_CODE_BRAIN_TRANSIENT = "brain_transient"
# Brain failed with a recognised permanent cause (auth, invalid
# model, malformed request). Retry button suppressed — the user has
# to fix the underlying problem.
_ERR_CODE_BRAIN_PERMANENT = "brain_permanent"
# Brain hung past the configured timeout. Retry won't help unless
# the user shortens the prompt or switches model.
_ERR_CODE_BRAIN_TIMEOUT = "brain_timeout"
# Underlying claude/opencode session vanished — usually because
# the brain rotated UUIDs. UI auto-recovers on the next send.
_ERR_CODE_SESSION_LOST = "session_lost"
# User-initiated cancel via Stop button. UI swallows silently —
# no toast, no retry button. Empty message; the code is what
# distinguishes it from genuine errors.
_ERR_CODE_CANCELLED = "cancelled"
# Caller-side allow-list rejection. UI surfaces as an auth wall;
# the message is None (don't leak which user was rejected).
_ERR_CODE_REJECTED = "rejected"
# Catch-all for the SSE generator's outermost ``except``. Logs
# already have the traceback; user gets a generic "stream
# interrupted" so we never render uncaught Python on the page.
_ERR_CODE_UNKNOWN = "unknown"

# Max length of a brain-error tail we'll inline into the user-facing
# message. The full text always lives in the daemon log; this is just
# what we send to Telegram/the chat bubble. 240 chars covers
# "claude -p exited 1: API Error: 500 Internal server error. This is
# a server-side issue, usually temporary — try again in a moment. If
# it persists, check status.claude.com." with margin to spare. Going
# longer risks Telegram message-length nag for a chain of retries.
_BRAIN_ERROR_TAIL_LIMIT = 240


@dataclass
class TurnOutcome:
    """Side-channel for telling the caller of :meth:`MessageHandler.handle`
    (or :meth:`MessageHandler.stream`) what *kind* of result the brain
    produced — distinct from the user-facing reply text.

    The handler's existing return signature is "text or None"; both
    branches collapse "brain succeeded" and "brain failed" into the
    same string shape (the failure string is the user-visible toast).
    The drain loop needs more than that — specifically the schedule
    manager wants to know whether to mark a fire ``ok`` or ``error``
    after the brain turn finishes.

    Callers create an instance, pass it in, and read the populated
    fields after the call returns. Callers that don't care leave the
    parameter ``None``; the handler skips the population work.

    ``kind`` values:

      * ``"ok"`` — brain returned a reply.
      * ``"empty"`` — brain returned an empty reply (the
        ``_EMPTY_RESPONSE`` toast was sent). Counts as success for
        schedule bookkeeping.
      * ``"cancelled"`` — /cancel fired; no reply was sent by us.
      * ``"rejected"`` — caller-side auth gate denied the message.
      * ``"timeout"`` — brain exceeded ``BRAIN_TIMEOUT_SECONDS``.
      * ``"session_lost"`` — claude session vanished; user got the
        rotation toast.
      * ``"transient"`` — upstream API hiccup (5xx, 429, network).
        Schedule manager treats as retryable error.
      * ``"permanent"`` — upstream rejected the request shape (auth,
        bad model id). Schedule manager auto-pauses immediately.
      * ``"unknown"`` — uncaught exception or unclassified failure.
    """

    kind: Literal[
        "unknown", "ok", "empty", "cancelled", "rejected",
        "timeout", "session_lost", "transient", "permanent",
    ] = "unknown"
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        """True for the brain-side success cases — ``ok`` and ``empty``.

        ``cancelled`` is NOT a success (user pulled the plug), but it
        also isn't a brain failure — the schedule manager treats it
        separately. See :meth:`ScheduleManager.report_fire_outcome`."""
        return self.kind in ("ok", "empty")

    @property
    def is_brain_failure(self) -> bool:
        """True iff the brain failed in a way that should advance
        the schedule's error counter. Excludes user-driven exits
        (cancelled, rejected) and timeouts (handled separately —
        timeout could be user-induced via a long-running tool)."""
        return self.kind in ("transient", "permanent", "unknown")

    @property
    def is_permanent_failure(self) -> bool:
        return self.kind == "permanent"


def _shorten_brain_error(text: str) -> str:
    """Trim a BrainError string to fit a user-facing toast.

    Strips the ``claude -p exited N: `` prefix (the user doesn't care
    about the exit code — they care about the *cause*) and caps at
    ``_BRAIN_ERROR_TAIL_LIMIT`` with an ellipsis. Falls back to a
    generic phrase if the input is empty.
    """
    s = (text or "").strip()
    if not s:
        return "claude -p failed with no diagnostic output."
    # Drop the "claude -p exited N: " framing if present — the
    # interesting bit is everything after the colon.
    for prefix in ("claude -p exited 1: ", "claude -p exited 2: "):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    else:
        # Generic "exited <N>:" match — substring from the first ": "
        # after "exited " is the diagnostic body.
        m = s.find("exited ")
        if m == 0:
            colon = s.find(": ", m)
            if colon != -1:
                s = s[colon + 2:]
    if len(s) > _BRAIN_ERROR_TAIL_LIMIT:
        s = s[: _BRAIN_ERROR_TAIL_LIMIT - 1].rstrip() + "…"
    return s


class MessageHandler:
    def __init__(
        self,
        brain: Brain,
        sessions: SessionStore,
        allowed_user_id: int,
        *,
        notifier: Notifier | None = None,
        workspace: Path | None = None,
    ) -> None:
        self._brain = brain
        self._sessions = sessions
        self._allowed_user_id = allowed_user_id
        self._workspace = workspace
        # The notifier owns the per-chat context buffer. We consume from
        # it at the start of every brain turn so events that fired since
        # the last reply (background task completions, daemon-restart
        # warnings) become visible to claude -p as a [SYSTEM CONTEXT]
        # block prepended to the user's message.
        self._notifier = notifier
        # v3b Day 3a: per-session "highest minted turn_index" cursor for
        # the relationships hook. In-memory only; on restart it's empty
        # and ``next_user_turn_index`` rebuilds from the JSONL.
        # ``claim_next_turn_index`` is the only mutation site, guarded
        # by ``_cursor_lock`` so two parallel hook fires (post-claim
        # the drain serialises per-chat, but defense in depth) cannot
        # both see the same ``proposed`` and pass.
        self._dispatched_turn_index: dict[str, int] = {}
        self._cursor_lock = asyncio.Lock()

    @property
    def brain(self) -> Brain:
        """Expose the bound brain to peers (web transport, learning
        curator) that need to call transcript-readback methods like
        ``iter_messages``. Read-only intentionally — the brain is
        bound at handler construction and never reassigned."""
        return self._brain

    async def handle(
        self,
        user_id: int,
        chat_id: int,
        text: str,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
        outcome: TurnOutcome | None = None,
    ) -> str | None:
        """Foreground turn entrypoint. ``model`` and ``reasoning_level``
        are optional per-turn overrides forwarded straight to
        ``Brain.respond``. ``None`` (the default) on either is the
        canonical foreground path — Voice call mode is the only caller
        passing non-None values, sourced from
        ``voice.call_mode.{model,reasoning_level}``.

        When the caller passes ``None``, the computer-use model
        selector gets a say (see :meth:`_apply_computer_use_override`):
        if the user opted in AND this turn is doing computer-use work,
        it may substitute a model. With no opt-in and no computer-use
        activity it's a no-op, so Telegram + text chat stay bit-for-bit
        unchanged.

        ``outcome`` is an optional :class:`TurnOutcome` outparam.
        Callers who need to distinguish "brain succeeded" from "brain
        failed transiently" vs "brain failed permanently" — i.e. the
        drain reporting back to the schedule manager — pass an
        instance; the handler populates ``outcome.kind`` and
        ``outcome.error_message`` before returning. Default ``None``
        keeps the legacy contract for callers that only care about
        the user-facing reply (web chat, voice, tests).
        """
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected message from user_id=%s", user_id)
            if outcome is not None:
                outcome.kind = "rejected"
            return None

        model, reasoning_level = self._apply_computer_use_override(
            model, reasoning_level,
        )
        message = await self._inject_context(chat_id, text)

        try:
            reply = await self._brain.respond(
                message, chat_id,
                model=model, reasoning_level=reasoning_level,
            )
        except BrainCancelled:
            # /cancel handler already replied; nothing more to send.
            if outcome is not None:
                outcome.kind = "cancelled"
            return None
        except BrainTimeoutError:
            log.warning("Brain timed out for chat_id=%s", chat_id)
            if outcome is not None:
                outcome.kind = "timeout"
                outcome.error_message = "brain timed out"
            return _BRAIN_TIMEOUT
        except SessionLost:
            if outcome is not None:
                outcome.kind = "session_lost"
            return _SESSION_LOST
        except BrainTransientError as exc:
            # Brain wrapper already retried once inline and still
            # failed. Surface the actual upstream wording — that's
            # the difference between an opaque "Something broke" and
            # an actionable "Anthropic 500, try again in a moment."
            log.warning("Brain transient failure for chat_id=%s: %s", chat_id, exc)
            if outcome is not None:
                outcome.kind = "transient"
                outcome.error_message = str(exc)
            return _BRAIN_TRANSIENT_PREFIX + _shorten_brain_error(str(exc))
        except BrainPermanentError as exc:
            log.warning("Brain permanent failure for chat_id=%s: %s", chat_id, exc)
            if outcome is not None:
                outcome.kind = "permanent"
                outcome.error_message = str(exc)
            return _BRAIN_PERMANENT_PREFIX + _shorten_brain_error(str(exc))
        except Exception as exc:
            log.exception("Brain call failed")
            if outcome is not None:
                outcome.kind = "unknown"
                outcome.error_message = str(exc) or "uncaught brain exception"
            return _BRAIN_ERROR

        stripped = reply.strip()
        if outcome is not None:
            outcome.kind = "ok" if stripped else "empty"
        return stripped or _EMPTY_RESPONSE

    async def stream(
        self,
        user_id: int,
        chat_id: int,
        text: str,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
        outcome: TurnOutcome | None = None,
    ):
        """Streaming variant of :meth:`handle`. Yields incremental
        text chunks as the brain generates them, plus a sentinel
        ``("done", full_text)`` at the end so callers can persist
        the final reply.

        On any failure (rejection, brain error, timeout, session
        lost) yields ``("error", message_or_None)`` and stops. The
        SSE route maps these to ``data: {type:..., ...}`` frames.

        Why a sentinel-tagged generator instead of two separate
        methods (chunks + final): keeps the contract single-pass.
        Callers don't have to coordinate two iterators or worry
        about the brain finishing between them. ``yield`` shape:

            ("chunk", str)         — incremental text
            ("tool",  dict)        — tool-use event (Phase A)
            ("done",  str)         — full reply, fired exactly once
                                     after the last chunk
            ("error", dict | None) — error event. Dict payload is
                                     ``{"code": str, "message": str}``
                                     where ``code`` is one of the
                                     ``_ERR_CODE_*`` constants. The
                                     SSE route maps each code to a
                                     specific user-facing recovery
                                     affordance (retry button,
                                     auto-recovery toast, etc.).
                                     ``None`` is the caller-side
                                     allow-list reject (route
                                     responds with 401).
        """
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected streaming message from user_id=%s", user_id)
            if outcome is not None:
                outcome.kind = "rejected"
            yield ("error", None)
            return

        model, reasoning_level = self._apply_computer_use_override(
            model, reasoning_level,
        )
        message = await self._inject_context(chat_id, text)

        full = ""
        try:
            async for event in self._brain.astream(
                message, chat_id,
                model=model, reasoning_level=reasoning_level,
            ):
                # Brain.astream yields a discriminated union (see
                # base.Brain.astream docstring): str = text delta,
                # dict = tool-use event. Tool events are forwarded
                # untouched — the caller (chat UI SSE route) renders
                # them as inline "Reading src/foo.py" status lines.
                if isinstance(event, dict):
                    yield ("tool", event)
                    continue
                # Text deltas: accumulate for the final ``done`` event
                # so the UI can swap streamed-content for a canonical
                # full-reply copy without parse-drift risk.
                if event:
                    full += event
                    yield ("chunk", event)
        except BrainCancelled:
            # User-initiated cancel — UI swallows silently, no toast.
            if outcome is not None:
                outcome.kind = "cancelled"
            yield ("error", {
                "code": _ERR_CODE_CANCELLED,
                "message": "",
            })
            return
        except BrainTimeoutError:
            log.warning("Brain stream timed out for chat_id=%s", chat_id)
            if outcome is not None:
                outcome.kind = "timeout"
                outcome.error_message = "brain timed out"
            yield ("error", {
                "code": _ERR_CODE_BRAIN_TIMEOUT,
                "message": _BRAIN_TIMEOUT,
            })
            return
        except SessionLost:
            if outcome is not None:
                outcome.kind = "session_lost"
            yield ("error", {
                "code": _ERR_CODE_SESSION_LOST,
                "message": _SESSION_LOST,
            })
            return
        except BrainTransientError as exc:
            log.warning(
                "Brain stream transient failure for chat_id=%s: %s",
                chat_id, exc,
            )
            if outcome is not None:
                outcome.kind = "transient"
                outcome.error_message = str(exc)
            yield ("error", {
                "code": _ERR_CODE_BRAIN_TRANSIENT,
                "message": _BRAIN_TRANSIENT_PREFIX + _shorten_brain_error(str(exc)),
            })
            return
        except BrainPermanentError as exc:
            log.warning(
                "Brain stream permanent failure for chat_id=%s: %s",
                chat_id, exc,
            )
            if outcome is not None:
                outcome.kind = "permanent"
                outcome.error_message = str(exc)
            yield ("error", {
                "code": _ERR_CODE_BRAIN_PERMANENT,
                "message": _BRAIN_PERMANENT_PREFIX + _shorten_brain_error(str(exc)),
            })
            return
        except Exception as exc:
            log.exception("Brain stream failed")
            if outcome is not None:
                outcome.kind = "unknown"
                outcome.error_message = str(exc) or "uncaught brain exception"
            yield ("error", {
                "code": _ERR_CODE_BRAIN_ERROR,
                "message": _BRAIN_ERROR,
            })
            return

        stripped = full.strip()
        if outcome is not None:
            outcome.kind = "ok" if stripped else "empty"
        yield ("done", stripped or _EMPTY_RESPONSE)

    @staticmethod
    def _apply_computer_use_override(
        model: str | None, reasoning_level: str | None,
    ) -> tuple[str | None, str | None]:
        """Let the computer-use model selector substitute a per-turn
        model when the caller didn't already pass one.

        An explicit caller override (voice call mode) always wins —
        if ``model`` is non-None we pass it straight through, so the
        voice-isolation contract is untouched. When ``model`` is None
        we consult ``core.computer_use``; it returns ``(None, None)``
        unless the user opted in AND a fresh ``vexis-ui`` snapshot says
        this turn is doing computer-use work. So with no opt-in this
        is a pure no-op and Telegram + text chat keep their existing
        semantics.
        """
        if model is not None:
            return model, reasoning_level
        try:
            from vexis_agent.core.computer_use import (
                resolve_computer_use_override,
            )

            cu_model, cu_reasoning = resolve_computer_use_override()
        except Exception:  # pragma: no cover - defensive
            log.debug("computer-use override resolution failed", exc_info=True)
            return model, reasoning_level
        if cu_model is not None:
            log.info(
                "computer-use: foreground turn using model override %r",
                cu_model,
            )
            return cu_model, cu_reasoning
        return model, reasoning_level

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

    def handle_history(
        self, user_id: int, name: str, limit: int = 50,
    ) -> list[dict] | None:
        """Return the last ``limit`` conversational turns of the
        named session as a list of plain dicts:
            [{"role": "user"|"assistant", "content": str, "ts": int}, ...]

        ``ts`` is unix milliseconds (matches what the chat UI's
        in-memory buffer uses). Returns:
          - ``None`` when ``user_id`` fails the allow-list (route
            translates to 401)
          - empty list when the session exists but has no messages
            (pristine session that's never been written to) OR the
            session name doesn't exist (caller can either 404 or
            treat as empty — the route currently 404s by checking
            sessions_for first)
          - list of messages otherwise

        Reads via ``brain.iter_messages(uuid)`` — both brains
        implement that ABC method (claude-code reads JSONL via
        ``core.transcripts``; opencode reads SQLite directly).
        Skips tool_call-only assistant messages with empty text
        because they don't render meaningfully in chat bubbles.
        """
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected history-for from user_id=%s", user_id)
            return None
        if limit <= 0:
            return []
        # Resolve name → uuid via SessionStore. Avoids exposing
        # ``brain.iter_messages`` to the dashboard which speaks in
        # names, not uuids.
        sessions = self._sessions.list()
        target = next((s for s in sessions if s.name == name), None)
        if target is None:
            return []
        # Drain the iterator into a list so we can ``[-limit:]``.
        # Tool-call-only messages (assistant turns where the model
        # only emitted tool_use blocks, no text) get filtered: their
        # text is empty so they'd render as blank bubbles.
        messages: list[dict] = []
        for tm in self._brain.iter_messages(target.uuid):
            if not tm.text:
                continue
            messages.append(
                {
                    "role": tm.role,
                    "content": tm.text,
                    # Unix milliseconds — what JS's ``Date`` constructor
                    # consumes, matches the in-memory ChatMessage shape
                    # the UI already uses.
                    "ts": int(tm.timestamp.timestamp() * 1000),
                }
            )
        return messages[-limit:] if len(messages) > limit else messages

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

    # ---------- skill pinning ----------

    def _pin_store(self) -> PinStore | None:
        """PinStore for the configured workspace, or None if the handler
        was constructed without a workspace path (test fixtures)."""
        if self._workspace is None:
            return None
        return PinStore(skills_dir(self._workspace))

    async def handle_pin(self, user_id: int, name: str) -> str | None:
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected /pin from user_id=%s", user_id)
            return None
        if not name:
            return "Usage: /pin <skill-name>"
        store = self._pin_store()
        if store is None:
            return "⚠️ Pinning unavailable: no workspace configured."
        added = store.pin(name)
        if not added:
            return f"Skill '{name}' was already pinned."
        log.info("Pinned skill '%s'", name)
        return f"Pinned `{name}`. The curator and skill edits will leave it alone."

    async def handle_unpin(self, user_id: int, name: str) -> str | None:
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected /unpin from user_id=%s", user_id)
            return None
        if not name:
            return "Usage: /unpin <skill-name>"
        store = self._pin_store()
        if store is None:
            return "⚠️ Pinning unavailable: no workspace configured."
        removed = store.unpin(name)
        if not removed:
            return f"Skill '{name}' is not pinned."
        log.info("Unpinned skill '%s'", name)
        return f"Unpinned `{name}`."

    def archived_skill_names(self) -> list[str]:
        """Used by /curator restore to list candidates."""
        if self._workspace is None:
            return []
        return archived_skill_names(skills_dir(self._workspace))

    # ---------- v3b Day 3a: relationships hook accessors ----------
    #
    # The relationships trigger hook in transports/telegram.py reads
    # the brain's session_uuid synchronously and asks for the next
    # user-turn_index that ``claude -p`` will write to. The pair
    # replaces the synthetic ``telegram-chat-{chat_id}`` UUID + per-
    # chat counter from Day 2.
    #
    # ``claim_next_turn_index`` is the load-bearing mutation: it
    # computes the proposed index from the JSONL and bumps the
    # in-memory cursor only when the JSONL has advanced past the
    # last mint. On collision it returns ``None`` and the caller
    # is expected to log a warning, skip staging, and let the brain
    # dispatch proceed normally.

    def current_session_uuid(self) -> str:
        """Active brain session UUID (synchronous read of SessionStore)."""
        return self._sessions.get()

    def next_user_turn_index(self, session_uuid: str) -> int:
        """Predict the user-turn ordinal that ``claude -p`` will write
        next for ``session_uuid``.

        Reads ``<encoded-cwd>/<session_uuid>.jsonl`` and counts
        user-role lines via ``iter_messages`` (which already skips
        sidechain + non-conversational types). Returns ``count + 1``.
        Returns 1 when the JSONL doesn't exist (first turn of a
        never-initialised session) or the workspace isn't configured
        on the handler (test fixtures).
        """
        if self._workspace is None:
            return 1
        pdir = claude_session_jsonl_dir(self._workspace)
        jsonl = pdir / f"{session_uuid}.jsonl"
        if not jsonl.exists():
            return 1
        n = 0
        for msg in iter_messages(jsonl):
            if msg.role == "user":
                n += 1
        return n + 1

    async def claim_next_turn_index(self, session_uuid: str) -> int | None:
        """Atomically reserve a turn_index for the relationships hook.

        Computes ``proposed = next_user_turn_index(session_uuid)`` and
        compares against the cursor. Returns the proposed index and
        bumps the cursor when ``proposed > last``; returns ``None``
        when the JSONL hasn't advanced past the last mint (the
        ``claude -p`` no-write edge — see scoping doc §3.1).

        Wrapped in ``_cursor_lock`` so the read-modify-write is
        atomic w.r.t. concurrent callers. The drain loop already
        serialises per-chat; the lock guards against any future
        codepath that fires the hook outside the drain.
        """
        async with self._cursor_lock:
            proposed = self.next_user_turn_index(session_uuid)
            last = self._dispatched_turn_index.get(session_uuid, 0)
            if proposed <= last:
                return None
            self._dispatched_turn_index[session_uuid] = proposed
            return proposed


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
