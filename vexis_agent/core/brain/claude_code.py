"""Subprocess wrapper around `claude -p` with persistent session id.

Output is parsed as `--output-format stream-json` so we can emit a
per-chat status file as tool events arrive (powers /status). Input is
still passed text-format on argv — we don't need streaming-input
queueing here since the application-level queue (core/running_tasks)
already handles follow-up messages.

Phase A (Day 1) of the brain abstraction moved this module from
``brains/claude_code.py`` to ``core/brain/claude_code.py`` and added
formal ``Brain`` ABC inheritance. The ``respond()`` body is
byte-identical to the pre-move implementation. Methods that have
natural Phase-A wiring (``build_system_prompt``, ``session_token``,
``rotate_session``, ``iter_session_metas``, ``iter_messages``,
``is_brain_owned_session``, ``instruction_file_name``,
``instruction_search_paths``, ``healthcheck``, ``kill_in_flight``)
delegate to the existing module-level functions so behaviour is
unchanged. Methods deferred to Phase B / C (``spawn_aux``,
``write_mcp_config``) raise ``NotImplementedError`` until those phases
land. See ``.plans/brain-abstraction-research.md`` §5 for the rollout.

Exception classes are re-exported from ``core.brain.base`` so existing
``from core.brain.claude_code import BrainCancelled`` imports keep
working — the canonical home is now ``core.brain.base``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from vexis_agent.core.brain.base import (
    AuxResult,
    Brain,
    BrainAuthRequired,
    BrainCancelled,
    BrainError,
    BrainHealth,
    BrainModelNotFoundError,
    BrainNotInstalled,
    BrainPermanentError,
    BrainTimeoutError,
    BrainTransientError,
    McpServerSpec,
    SessionLost,
)
from vexis_agent.core.memory import MemoryStore
from vexis_agent.core.paths import memories_dir, skills_dir
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.safety import DESTRUCTIVE_PATTERNS
from vexis_agent.core.safety_install import ensure_workspace_safety_hook
from vexis_agent.core.sessions import SessionStore
from vexis_agent.core.skills import (
    build_skill_authoring_block,
    build_skills_index_block,
)
from vexis_agent.core.status import StatusFile, extract_tool_target

# Re-export the exception types so existing import sites
# (``from core.brain.claude_code import BrainCancelled, ...``) keep
# working. The canonical definition home is ``core.brain.base``.
__all__ = [
    "AuxResult",
    "BrainAuthRequired",
    "BrainCancelled",
    "BrainError",
    "BrainHealth",
    "BrainNotInstalled",
    "BrainPermanentError",
    "BrainTimeoutError",
    "BrainTransientError",
    "ClaudeCodeBrain",
    "McpServerSpec",
    "SessionLost",
    "audit_destructive_mentions",
    "build_system_prompt",
]

log = logging.getLogger(__name__)

# Repo root resolution: this file lives at
# vexis_agent/core/brain/claude_code.py post-Phase-2 packaging, so four
# `.parent`s lift us from the file to the repo root that holds
# CAPABILITIES.md and the source checkout.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# 30 min — generous for long multi-step work, hard ceiling for runaway calls.
BRAIN_TIMEOUT_SECONDS = 1800

# StreamReader buffer for the brain's stdout. claude -p's stream-json
# emits one JSON object per line, and a single line can carry a
# multi-megabyte tool result (e.g. a base64-encoded screenshot). The
# asyncio default of 64 KiB makes readline raise LimitOverrunError on
# lines longer than that — the stream then dies and the brain process
# hangs without ever firing a result event. 32 MiB covers the largest
# realistic tool payload we'll see (full-page screenshots top out
# around 4 MB base64); cheap because we only ever hold one line in
# the buffer at a time.
_BRAIN_STREAM_LIMIT_BYTES = 32 * 1024 * 1024

# ── Transient-error inline retry ─────────────────────────────────
# When the Anthropic API returns a 5xx / 429 / "overloaded" / network
# blip, claude-code exits 1 and writes the error into the stream-json
# output (NOT stderr) as the assistant's final message. The May 2026
# scheduled-fire crash that prompted this hierarchy was caused by
# exactly one such transient 500. To absorb sub-second hiccups
# without the user seeing them, ``respond`` and ``astream`` retry
# once on ``BrainTransientError`` with a short delay. One retry only:
# more invites cascading double-charges on rate-limit cases where the
# upstream is intentionally throttling us, and any outage longer
# than a few seconds wants caller-side backoff (the schedule manager,
# the user re-typing) not silent burn-in here.
_TRANSIENT_RETRY_DELAY_SECONDS = 3.0
_TRANSIENT_MAX_ATTEMPTS = 2  # initial + one retry

# Pattern: claude-code wraps Anthropic API errors as
#   "API Error: <HTTP code> <message>"
# and prints the whole thing inside the final assistant text block. We
# match the wording (not a parsed status code) because that's what's
# actually visible to us on the failure path; the regression tests in
# tests/test_brain_error_classification.py pin known wordings against
# the right subclass. Update both alongside upstream wording changes.
_TRANSIENT_ERROR_RE = re.compile(
    r"API\s+Error:\s*5\d\d"          # any HTTP 5xx
    r"|API\s+Error:\s*429"           # rate limit
    r"|overloaded_error"             # Anthropic SDK-style code
    r"|overloaded"                   # natural language
    r"|rate.?limit"
    r"|timed?\s*out"
    r"|connection\s+reset"
    r"|temporarily\s+unavailable"
    r"|service\s+unavailable",
    re.IGNORECASE,
)
_PERMANENT_ERROR_RE = re.compile(
    r"API\s+Error:\s*40[013-9]"       # 4xx except 402/429 — 429 above
    r"|API\s+Error:\s*41\d"
    r"|API\s+Error:\s*42[0-8]"
    r"|authentication"
    r"|invalid_api_key"
    r"|invalid_request_error"
    r"|model\s+not\s+found"
    r"|There's\s+an\s+issue\s+with\s+the\s+selected\s+model"
    r"|insufficient\s+(credit|quota|balance)",
    re.IGNORECASE,
)


def _classify_brain_failure(
    *,
    stderr_text: str,
    assistant_text: str,
) -> tuple[type[BrainError], str]:
    """Pick the most specific ``BrainError`` subclass + diagnostic text
    for a non-zero ``claude -p`` exit.

    Returns ``(error_class, human_message)`` so the caller can do::

        cls, msg = _classify_brain_failure(...)
        raise cls(msg)

    Why two text sources: claude-code's CLI is inconsistent about
    *where* it writes failure detail. ``stderr`` carries low-level
    crashes (subprocess died, JSON-RPC parse error). API errors and
    permission denials land in ``stdout`` as a final assistant text
    block instead, with stderr empty — which is exactly the scenario
    that bit us on 15 May 2026 when an Anthropic 500 produced exit 1
    + empty stderr + a single ``assistant`` event saying
    "API Error: 500 Internal server error…". We combine both
    sources, classify against the combined text, and surface the
    actual wording in the message.

    Fallback when neither pattern matches: ``BrainError`` base — the
    caller treats that as "unknown failure, don't retry, surface
    verbatim."
    """
    parts = [s.strip() for s in (stderr_text, assistant_text) if s and s.strip()]
    combined = " | ".join(parts)
    if not combined:
        combined = "(no stderr or assistant text)"
    if _TRANSIENT_ERROR_RE.search(combined):
        return BrainTransientError, combined
    if _PERMANENT_ERROR_RE.search(combined):
        return BrainPermanentError, combined
    return BrainError, combined


def _session_jsonl_exists(workspace: Path, session_id: str) -> bool:
    """True when claude-code already has a transcript on disk for
    this session UUID — i.e. when ``--session-id <uuid>`` would be
    rejected with "Session ID is already in use".

    claude stores transcripts at::

        ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl

    where encoded-cwd is the workspace path with ``/`` replaced by
    ``-`` and a leading ``-`` (verified against claude-code 2.1.138
    by inspecting the live projects directory).

    This is the ONLY signal claude uses to decide whether a session
    UUID is "in use" — it's a pure file-existence check, not a
    process-lock or sessions-database lookup. Disassembled from
    the binary's ``Hf$(H)`` function::

        function Hf$(H) {
          let $ = WQ() ?? mf(q6()),
              q = Ff.join($, `${H}.jsonl`);
          try { return statSync(q), true; }
          catch { return false; }
        }

    The bug we're working around: vexis's first-turn-vs-subsequent
    branch in :meth:`respond` / :meth:`astream` picks ``--session-id``
    when ``SessionStore.is_initialized()`` is False. ``mark_initialized``
    only gets called at the *end* of a successful turn, so a turn
    that's cancelled mid-stream (Stop button, /cancel) leaves the
    in-memory flag at False even though claude has already written
    a partial transcript JSONL. The next turn re-spawns with
    ``--session-id`` against a UUID whose JSONL exists, and
    claude exits 1 with "Session ID is already in use".

    This helper is the disk-state authority that breaks that race:
    if the JSONL exists, use ``--resume`` regardless of what
    ``is_initialized()`` says. Idempotent and side-effect-free.
    """
    encoded = "-" + str(workspace).strip("/").replace("/", "-")
    jsonl = Path.home() / ".claude" / "projects" / encoded / f"{session_id}.jsonl"
    return jsonl.is_file()

# Day 2 model UX: claude-code prints the bad-model diagnostic to
# STDOUT (not stderr) and exits 1. Verified empirically:
#   $ claude --model definitely-not-a-real-model -p "hi"
#   exit=1, stderr=(empty)
#   stdout: "There's an issue with the selected model
#           (definitely-not-a-real-model). It may not exist or
#           you may not have access to it. Run --model to pick
#           a different model."
# The substring below is the stable prefix; the parenthetical
# changes per call but the leading sentence is canonical.
_CC_MODEL_NOT_FOUND_STDOUT_MARKER = (
    "There's an issue with the selected model"
)

# How many session UUIDs to cache system prompts for. Each entry is
# small (a few KB), but rotations accrete over a long-running daemon
# so we cap to keep memory bounded. FIFO eviction is fine — we only
# care about the active session's cache being warm.
_SYSTEM_PROMPT_CACHE_MAX = 16

DISALLOWED_TOOLS: list[str] = []  # All tools enabled in Step 6

DEFAULT_SOUL = (
    "You are Vexis, the user's personal agent. Be concise, truth-seeking, "
    "and genuinely useful. Never invent information; admit uncertainty. "
    "Address the user as 'sir' occasionally where it fits.\n\n"
    "Facts in RELATIONSHIPS.md are durable but not necessarily current — "
    "defer to in-conversation evidence on conflict."
)

# Phrases that suggest Vexis is asking permission rather than reporting
# execution. Heuristic for dogfooding signal only — the model decides what
# to run; this just classifies the textual reply.
_ASKING_RE = re.compile(
    r"\b(should|shall|may|do you want|want me|would you like|"
    r"okay to|ok to|confirm|before I|about to|going to|"
    r"plan(ning)? to|ready to|may I|is it ok|is that ok)\b",
    re.IGNORECASE,
)
# Sentence terminator: ., !, ? followed by whitespace/EOS, or a newline.
# Bounding the asking-language scan to a single sentence prevents a `?` in
# one sentence from misclassifying a destructive mention in the next.
_SENTENCE_END = re.compile(r"[.!?](?:\s+|$)|\n+")


def _sentence_around(text: str, start: int, end: int) -> str:
    left = 0
    for m in _SENTENCE_END.finditer(text, 0, start):
        left = m.end()
    m = _SENTENCE_END.search(text, end)
    right = m.start() + 1 if m else len(text)
    return text[left:right]


def _read_markdown(path: Path) -> str | None:
    """Read a UTF-8 markdown file. Missing file is fine (returns None);
    unreadable / non-UTF-8 file logs a warning and also returns None."""
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("Could not read %s: %s", path, exc)
        return None
    except UnicodeDecodeError as exc:
        log.warning("%s is not valid UTF-8: %s", path, exc)
        return None
    return content or None


def build_system_prompt(workspace: Path) -> str:
    """Compose the system prompt fed to claude -p.

    Layers (top → bottom): SOUL.md (or default), CAPABILITIES.md,
    MEMORY.md block, USER.md block, RELATIONSHIPS.md block, skills
    index. Each layer is independent and dropped if empty.
    Re-reads from disk on every call so file edits take effect on
    the next spawn without restarting the daemon — the foreground
    brain caches the result per session UUID for prefix-cache
    stability (see ``ClaudeCodeBrain._system_prompt_for``);
    background tasks call this directly and naturally get a fresh
    snapshot per spawn.

    v3c (Day 4a) wires RELATIONSHIPS.md into this prompt — without
    that wiring, approval has no product effect. Brain isolation
    contract: this function reads ONLY the live file via
    ``format_relationships_for_system_prompt``. It does NOT read
    ``RELATIONSHIPS-SHADOW.md``, ``RELATIONSHIPS-ARCHIVE.md``, or
    ``.vexis/relationships-candidates.json``. Enforced by
    ``tests/test_brain_isolation.py``.
    """
    # Lazy-import to keep the brain layer's startup fast (the
    # relationships package pulls in YAML + the trigger detector
    # modules on first import).
    from vexis_agent.core.relationships.store import (
        format_relationships_for_system_prompt,
    )

    # CAPABILITIES.md ships as package data — readable identically
    # under pipx-installed wheels and editable source checkouts.
    from vexis_agent.data import read_capabilities

    soul = _read_markdown(workspace / "SOUL.md") or DEFAULT_SOUL
    capabilities = read_capabilities()
    parts: list[str] = [soul]
    if capabilities:
        parts.append(capabilities)

    # agent-platform-style in-session skill self-authoring guidance. Injected
    # AFTER capabilities and BEFORE memory/user/relationships so it
    # sits with the other "how to work" rules. Always non-empty —
    # exists specifically to drive bootstrap from zero skills, where
    # ``build_skills_index_block`` returns ""  and would otherwise
    # leave the brain with no nudge to ever create one.
    parts.append(build_skill_authoring_block())

    # Memory blocks — agent notes first, user profile second. Empty
    # blocks return None and are dropped here.
    memory_store = MemoryStore(memories_dir(workspace))
    mem_block = memory_store.format_for_system_prompt("memory")
    if mem_block:
        parts.append(mem_block)
    user_block = memory_store.format_for_system_prompt("user")
    if user_block:
        parts.append(user_block)

    # v3c Day 4a: RELATIONSHIPS.md after USER.md. The brain's
    # mental model is "first who I'm talking to (USER), then who
    # they talk about (RELATIONSHIPS)." Empty file → no block.
    relationships_block = format_relationships_for_system_prompt(workspace)
    if relationships_block:
        parts.append(relationships_block)

    # Skills index — last so it sits next to where the model is most
    # likely to consult it (right before the conversation starts).
    skills_block = build_skills_index_block(skills_dir(workspace))
    if skills_block:
        parts.append(skills_block)

    return "\n\n".join(parts)


async def _read_stream_events(
    stream: asyncio.StreamReader | None, status_file: StatusFile
) -> tuple[str, str]:
    """Consume the brain's stream-json stdout, updating ``status_file``
    on every tool_use event.

    Returns ``(final_text, last_assistant_text)``:

    * ``final_text`` — the ``result`` field of the terminal ``result``
      event Claude Code emits last. This is the canonical reply on a
      successful turn. Empty when the brain crashed before emitting a
      ``result``.

    * ``last_assistant_text`` — the concatenation of all text blocks
      from ``assistant`` events seen during the stream. We only need
      this for the failure path: when ``claude -p`` exits non-zero
      because of an upstream API error, the error wording lands in an
      ``assistant`` text block (NOT in stderr, NOT in a ``result``
      event). Carrying it out of this helper lets the caller classify
      the failure as transient / permanent / unknown and surface the
      actual wording instead of "(no stderr)". Unused on success.

    Malformed lines are logged and skipped so a single corrupt event
    can't break the whole turn — historically rare but we shouldn't
    lose a real reply over one bad line.
    """
    final_text = ""
    assistant_text_parts: list[str] = []
    if stream is None:
        return final_text, ""
    while True:
        try:
            line = await stream.readline()
        except Exception:
            log.warning("brain stream readline raised", exc_info=True)
            break
        if not line:
            break
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "assistant":
            content = event.get("message", {}).get("content") or []
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    name = block.get("name") or "Tool"
                    target = extract_tool_target(name, block.get("input") or {})
                    status_file.record_tool(name, target)
                elif btype == "text":
                    text = block.get("text")
                    if isinstance(text, str) and text:
                        assistant_text_parts.append(text)
        elif kind == "result":
            result_text = event.get("result")
            if isinstance(result_text, str):
                final_text = result_text
    last_assistant_text = "\n".join(assistant_text_parts).strip()
    return final_text, last_assistant_text


def audit_destructive_mentions(response: str) -> Iterator[tuple[str, bool]]:
    """Yield (reason, asked_first) for each destructive pattern hit in response.

    asked_first is True when the enclosing sentence contains a question mark
    or asking-language, suggesting Vexis sought confirmation rather than
    reporting after the fact. False = appears to have run it.
    """
    for pattern, reason in DESTRUCTIVE_PATTERNS:
        for match in pattern.finditer(response):
            sentence = _sentence_around(response, *match.span())
            asked = "?" in sentence or bool(_ASKING_RE.search(sentence))
            yield reason, asked


class ClaudeCodeBrain(Brain):
    def __init__(
        self,
        workspace: Path,
        session: SessionStore,
        running_tasks: RunningTasks,
    ) -> None:
        self._workspace = workspace
        self._session = session
        self._running_tasks = running_tasks
        # Step 6.5: install the PreToolUse safety hook into
        # <workspace>/.claude/settings.json before the first claude -p
        # spawn. Idempotent + merge-friendly — see
        # vexis_agent.core.safety_install for the contract. Failures
        # are logged but don't raise: the daemon must come up even if
        # hook installation fails (degraded safety > broken startup).
        ensure_workspace_safety_hook(workspace)
        # Per-session frozen snapshot. The system prompt MUST be
        # byte-identical across all turns of one Claude session for
        # Anthropic's prefix cache to hit. We key by session UUID
        # because that's what claude -p uses to identify a resumable
        # conversation; rotating the UUID (via /clear, /new, /switch,
        # or a SessionLost recovery) naturally invalidates the cache
        # entry without explicit eviction. Mid-session memory/skills
        # writes mutate disk but are NOT visible to this cache —
        # by design, see CAPABILITIES.md for the model-facing
        # documentation of this trap.
        self._system_prompt_cache: dict[str, str] = {}

    def _system_prompt_for(self, session_uuid: str) -> str:
        cached = self._system_prompt_cache.get(session_uuid)
        if cached is not None:
            return cached
        prompt = build_system_prompt(self._workspace)
        # FIFO trim: dicts preserve insertion order in Python 3.7+, so
        # the first key is always the oldest. Cap is a safety net for
        # long-running daemons that accumulate many session rotations.
        if len(self._system_prompt_cache) >= _SYSTEM_PROMPT_CACHE_MAX:
            oldest = next(iter(self._system_prompt_cache))
            del self._system_prompt_cache[oldest]
        self._system_prompt_cache[session_uuid] = prompt
        return prompt

    async def respond(
        self,
        message: str,
        chat_id: int,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
    ) -> str:
        log.info(
            "Brain.respond starting for chat %d%s%s",
            chat_id,
            f" (model override: {model})" if model else "",
            f" (reasoning: {reasoning_level})" if reasoning_level else "",
        )
        session_id = self._session.get()
        # First call pins the UUID with --session-id; subsequent
        # calls resume it. The decision is grounded in DISK state
        # (does the transcript JSONL exist?) rather than the in-
        # memory ``is_initialized`` flag, because a turn cancelled
        # mid-stream (Stop button / /cancel / SIGKILL) writes a
        # partial transcript without ever flipping the flag —
        # hitting ``--session-id`` on the next turn would surface
        # claude's "Session ID already in use" check. The disk
        # check is what claude itself uses to decide; aligning
        # vexis with that closes the race entirely.
        if (
            self._session.is_initialized()
            or _session_jsonl_exists(self._workspace, session_id)
        ):
            session_flag = ["--resume", session_id]
        else:
            session_flag = ["--session-id", session_id]

        system_prompt = self._system_prompt_for(session_id)

        argv = [
            "claude",
            "-p",
            message,
            *session_flag,
            "--append-system-prompt",
            system_prompt,
            # stream-json output gives us tool_use events in real time
            # for the /status command. --verbose is required by Claude
            # Code whenever -p is paired with --output-format stream-json.
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if DISALLOWED_TOOLS:
            argv += ["--disallowedTools", *DISALLOWED_TOOLS]
        # bypassPermissions: required when running headless (-p) with tools
        # enabled. Otherwise Claude Code would try to prompt interactively
        # for each tool use and the call would hang. The Step 6.5
        # PreToolUse hook (see core.safety_install + core.safety_hook) is
        # installed at __init__ time and still fires under bypassPermissions
        # — that's how destructive-command denials are enforced today.
        argv += ["--permission-mode", "bypassPermissions"]
        # Per-turn model override (voice call mode is the only caller
        # today; see ``voice.call_mode.model`` in ~/.vexis/config.yaml).
        # ``None`` keeps the canonical "no --model flag, use account
        # default" behavior — Telegram and text-chat tab path through
        # here unchanged.
        if model:
            argv += ["--model", model]
        # Per-turn reasoning effort. ``--effort`` is the same flag
        # spawn_aux uses; mapping is identical so the user can pick
        # any level the discovery surface reports for the model
        # they chose. ``None`` means no flag.
        if reasoning_level:
            argv += ["--effort", reasoning_level]
        log.debug(
            "Spawning claude -p (%s=%s, cwd=%s)",
            session_flag[0],
            session_id,
            self._workspace,
        )

        # Inline retry on transient upstream failures (Anthropic 5xx /
        # 429 / network blip). See ``_TRANSIENT_RETRY_DELAY_SECONDS``
        # comment for the rationale: one retry absorbs sub-second
        # hiccups; anything longer wants caller-side backoff. /cancel
        # arriving between attempts short-circuits the loop so the
        # user's Stop button is honoured even mid-retry.
        for attempt in range(1, _TRANSIENT_MAX_ATTEMPTS + 1):
            try:
                final_text = await self._attempt_respond(argv, chat_id)
                break
            except BrainTransientError as exc:
                if attempt >= _TRANSIENT_MAX_ATTEMPTS:
                    raise
                if self._running_tasks.was_cancelled(chat_id):
                    raise
                log.warning(
                    "claude -p transient failure (attempt %d/%d) for "
                    "chat %d: %s — retrying in %.1fs",
                    attempt, _TRANSIENT_MAX_ATTEMPTS, chat_id,
                    exc, _TRANSIENT_RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(_TRANSIENT_RETRY_DELAY_SECONDS)

        # Mark only after a successful exit so a failed first call doesn't
        # leave us thinking the UUID is live.
        if not self._session.is_initialized():
            self._session.mark_initialized()
        response = (final_text or "").strip()
        for reason, asked in audit_destructive_mentions(response):
            if asked:
                log.info("Vexis confirmed before destructive: %s", reason)
            else:
                log.info("Vexis ran without confirm: %s", reason)
        log.info("Brain.respond completed for chat %d", chat_id)
        return response

    async def _attempt_respond(
        self, argv: list[str], chat_id: int,
    ) -> str:
        """One spawn-and-await cycle for :meth:`respond`.

        Returns the buffered ``result``-event text. Raises:

        * ``BrainCancelled`` — /cancel fired (caller does not retry).
        * ``BrainTimeoutError`` — exceeded ``BRAIN_TIMEOUT_SECONDS``.
        * ``SessionLost`` — claude lost its session JSONL.
        * ``BrainTransientError`` — upstream API hiccup; caller may retry.
        * ``BrainPermanentError`` — upstream rejected the request shape.
        * ``BrainError`` — anything else.

        Extracted from ``respond`` so the caller can wrap the attempt in
        a retry loop without duplicating the reserve/attach/spawn
        machinery. Reserve + register live INSIDE this helper so each
        attempt gets a fresh slot (the previous attempt's slot is
        unregistered in ``finally`` before retry).
        """
        reservation = await self._running_tasks.reserve(chat_id)
        env = {**os.environ, "VEXIS_CHAT_ID": str(chat_id)}

        status_file = StatusFile(chat_id)
        status_file.start()

        final_text = ""
        assistant_text = ""
        stderr_bytes = b""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=env,
                limit=_BRAIN_STREAM_LIMIT_BYTES,
            )
            log.info("Brain spawned PID %d for chat %d", proc.pid, chat_id)

            attached = await self._running_tasks.attach(reservation, proc)
            if not attached:
                log.info(
                    "Brain raising BrainCancelled for chat %d "
                    "(cancel during reservation window)",
                    chat_id,
                )
                await self._kill_group(proc)
                raise BrainCancelled("brain subprocess cancelled via /cancel")

            # Drain stdout/stderr concurrently with proc.wait(). We can't
            # use proc.communicate() here because that blocks until exit,
            # which would mean status updates only arrive *after* the
            # brain finishes — useless for /status. Concurrent reading
            # also keeps the OS pipe buffer from filling and stalling
            # the subprocess on long outputs.
            stdout_task = asyncio.create_task(
                _read_stream_events(proc.stdout, status_file)
            )
            stderr_task = asyncio.create_task(proc.stderr.read())

            try:
                await asyncio.wait_for(proc.wait(), timeout=BRAIN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                await self._kill_group(proc)
                # Let readers reach EOF after the kill so we don't leak tasks.
                await asyncio.gather(
                    stdout_task, stderr_task, return_exceptions=True
                )
                raise BrainTimeoutError(
                    f"claude -p timed out after {BRAIN_TIMEOUT_SECONDS}s"
                ) from exc

            try:
                final_text, assistant_text = await stdout_task
            except Exception:
                log.exception("Brain stdout reader failed for chat %d", chat_id)
                final_text, assistant_text = "", ""
            try:
                stderr_bytes = await stderr_task
            except Exception:
                log.exception("Brain stderr reader failed for chat %d", chat_id)
                stderr_bytes = b""

            if self._running_tasks.was_cancelled(chat_id):
                log.info(
                    "Brain raising BrainCancelled for chat %d (proc killed)",
                    chat_id,
                )
                raise BrainCancelled("brain subprocess cancelled via /cancel")

            if proc.returncode != 0:
                err = stderr_bytes.decode(errors="replace").strip()
                # Session-lost detection takes precedence — wording is
                # specific and recovery is a UUID rotation, not a retry.
                if self._session.is_initialized() and "No conversation found" in err:
                    old_uuid = self._session.get()
                    new_uuid = self._session.rotate()
                    log.warning(
                        "Claude Code lost session %s; rotated to %s",
                        old_uuid,
                        new_uuid,
                    )
                    raise SessionLost(
                        "Claude Code session was lost. Rotated to new session."
                    )
                # Everything else: classify by combined stderr +
                # assistant-text body. The May 2026 schedule crash was
                # exactly this path — stderr empty, assistant text
                # "API Error: 500…". Before this change we raised
                # ``BrainError("claude -p exited 1: (no stderr)")``
                # and lost the actual cause.
                cls, message = _classify_brain_failure(
                    stderr_text=err, assistant_text=assistant_text,
                )
                raise cls(
                    f"claude -p exited {proc.returncode}: {message}"
                )
        finally:
            status_file.delete()
            await self._running_tasks.unregister(chat_id)

        return final_text

    async def astream(
        self,
        message: str,
        chat_id: int,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
    ) -> AsyncIterator[str]:
        """Native streaming. Spawns ``claude --print`` with
        ``--include-partial-messages`` so stream-json emits
        ``content_block_delta`` events as the model generates each
        chunk; yields the ``text_delta.text`` from each. Falls back
        to yielding the buffered final text only if the partial-
        message stream is empty (no tokens delivered — should never
        happen on success but defensive against API quirks).

        Same per-turn override semantics as :meth:`respond`. Same
        cancellation, timeout, session-lost, and error mapping —
        the spawn/kill machinery is identical, only the event-loop
        differs.

        Tool-use events still update the StatusFile so /status
        works exactly like the buffered path. The ``result`` event
        (if any) is captured to verify against the concatenated
        deltas; mismatch is logged but not fatal.
        """
        log.info(
            "Brain.astream starting for chat %d%s%s",
            chat_id,
            f" (model override: {model})" if model else "",
            f" (reasoning: {reasoning_level})" if reasoning_level else "",
        )
        session_id = self._session.get()
        # Same disk-state-authority --session-id-vs-resume decision
        # as :meth:`respond`. The streaming path is the *hottest*
        # path for the post-cancel bug because the web chat's Stop
        # button fires here; without this check, every Stop →
        # resend produces "Session ID already in use" even though
        # the in-memory ``is_initialized`` flag is still False.
        if (
            self._session.is_initialized()
            or _session_jsonl_exists(self._workspace, session_id)
        ):
            session_flag = ["--resume", session_id]
        else:
            session_flag = ["--session-id", session_id]

        system_prompt = self._system_prompt_for(session_id)

        argv = [
            "claude",
            "-p",
            message,
            *session_flag,
            "--append-system-prompt",
            system_prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            # The streaming-only addition: makes claude emit
            # ``content_block_delta`` events with ``text_delta.text``
            # for each chunk the model generates, instead of buffering
            # the whole reply into a single ``assistant`` event.
            "--include-partial-messages",
        ]
        if DISALLOWED_TOOLS:
            argv += ["--disallowedTools", *DISALLOWED_TOOLS]
        argv += ["--permission-mode", "bypassPermissions"]
        if model:
            argv += ["--model", model]
        if reasoning_level:
            argv += ["--effort", reasoning_level]
        log.debug(
            "Spawning claude -p (stream %s=%s, cwd=%s)",
            session_flag[0],
            session_id,
            self._workspace,
        )

        # Inline transient-retry. Matches the policy in :meth:`respond`,
        # with one extra constraint: retry only if NOTHING was yielded
        # downstream yet. Once we've emitted a text delta or a tool
        # event the user/UI has consumed it, and retrying would
        # double-render the same prefix and (worse) re-run any tool
        # the brain already started. So a transient that hits mid-
        # stream still propagates — only first-millisecond failures
        # (API 5xx on the opening call) get the silent retry.
        for attempt in range(1, _TRANSIENT_MAX_ATTEMPTS + 1):
            yielded_anything = False
            try:
                async for event in self._attempt_astream(argv, chat_id):
                    yielded_anything = True
                    yield event
                break  # clean completion
            except BrainTransientError as exc:
                if yielded_anything:
                    raise
                if attempt >= _TRANSIENT_MAX_ATTEMPTS:
                    raise
                if self._running_tasks.was_cancelled(chat_id):
                    raise
                log.warning(
                    "claude -p (stream) transient failure (attempt "
                    "%d/%d) for chat %d: %s — retrying in %.1fs",
                    attempt, _TRANSIENT_MAX_ATTEMPTS, chat_id,
                    exc, _TRANSIENT_RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(_TRANSIENT_RETRY_DELAY_SECONDS)

        if not self._session.is_initialized():
            self._session.mark_initialized()

        log.info("Brain.astream completed for chat %d", chat_id)

    async def _attempt_astream(
        self, argv: list[str], chat_id: int,
    ) -> AsyncIterator:
        """One spawn-and-stream cycle for :meth:`astream`.

        Async generator yielding the same discriminated union as
        :meth:`astream` (text str or ``{"type": "tool", ...}`` dict).
        Raises the same exception taxonomy as :meth:`_attempt_respond`
        — caller decides whether to retry, based on whether anything
        was yielded.
        """
        reservation = await self._running_tasks.reserve(chat_id)
        env = {**os.environ, "VEXIS_CHAT_ID": str(chat_id)}

        status_file = StatusFile(chat_id)
        status_file.start()

        # Concatenated deltas (for cross-check against the result
        # event) and the result-event text (used as fallback if no
        # deltas arrived for some reason). ``assistant_text`` accumulates
        # text-block bodies from ``assistant`` events — distinct from
        # the streamed ``accumulated`` deltas because API-error
        # messages arrive as one buffered assistant text block, not
        # as content_block_delta deltas. See _classify_brain_failure.
        accumulated = ""
        result_text = ""
        assistant_text_parts: list[str] = []
        stderr_bytes = b""
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=env,
                limit=_BRAIN_STREAM_LIMIT_BYTES,
            )
            log.info("Brain (stream) spawned PID %d for chat %d", proc.pid, chat_id)

            attached = await self._running_tasks.attach(reservation, proc)
            if not attached:
                log.info(
                    "Brain raising BrainCancelled for chat %d "
                    "(cancel during reservation window)",
                    chat_id,
                )
                await self._kill_group(proc)
                raise BrainCancelled("brain subprocess cancelled via /cancel")

            # Drain stderr in the background so a verbose stderr
            # doesn't fill the OS pipe buffer and stall the brain.
            stderr_task = asyncio.create_task(proc.stderr.read())

            stream = proc.stdout
            if stream is None:
                raise BrainError("claude -p produced no stdout pipe")

            # Per-line stream-json parse. Each yield is a tight
            # async event (the caller's SSE loop forwards it
            # immediately to the browser).
            stream_started_at = asyncio.get_event_loop().time()
            while True:
                # Bound the per-line read by the overall brain
                # timeout so a hung subprocess doesn't deadlock the
                # iterator. The remainder of BRAIN_TIMEOUT_SECONDS
                # decreases as the stream progresses.
                elapsed = asyncio.get_event_loop().time() - stream_started_at
                remaining = max(1.0, BRAIN_TIMEOUT_SECONDS - elapsed)
                try:
                    line = await asyncio.wait_for(
                        stream.readline(), timeout=remaining,
                    )
                except asyncio.TimeoutError as exc:
                    await self._kill_group(proc)
                    await asyncio.gather(stderr_task, return_exceptions=True)
                    raise BrainTimeoutError(
                        f"claude -p stream timed out after {BRAIN_TIMEOUT_SECONDS}s"
                    ) from exc
                if not line:
                    break
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind == "stream_event":
                    inner = event.get("event") or {}
                    if not isinstance(inner, dict):
                        continue
                    if inner.get("type") == "content_block_delta":
                        delta = inner.get("delta") or {}
                        if (
                            isinstance(delta, dict)
                            and delta.get("type") == "text_delta"
                        ):
                            text = delta.get("text")
                            if isinstance(text, str) and text:
                                accumulated += text
                                yield text
                elif kind == "assistant":
                    # Tool-use tracking. Two consumers:
                    #   1. StatusFile (per-chat tmpfs JSON) — read by
                    #      Telegram /status. Unchanged.
                    #   2. The chat UI streaming bubble — yielded as
                    #      a tool event dict so the user sees inline
                    #      "Reading src/foo.py" lines while the brain
                    #      is grinding through tools. Without this
                    #      the bubble is just a pulse for 30+s during
                    #      heavy tool turns and feels frozen.
                    content = event.get("message", {}).get("content") or []
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type")
                            if btype == "tool_use":
                                name = block.get("name") or "Tool"
                                target = extract_tool_target(
                                    name, block.get("input") or {},
                                )
                                status_file.record_tool(name, target)
                                # Tool event → chat UI. Distinct from
                                # text deltas; consumers must distinguish
                                # via ``isinstance``. Documented contract
                                # on Brain.astream.
                                yield {
                                    "type": "tool",
                                    "name": name,
                                    "target": target,
                                }
                            elif btype == "text":
                                # Captured for the failure-classification
                                # path; not yielded — the streamed
                                # content_block_delta deltas above are
                                # the canonical UI source. API errors
                                # arrive HERE as one buffered text
                                # block with no preceding deltas.
                                text = block.get("text")
                                if isinstance(text, str) and text:
                                    assistant_text_parts.append(text)
                elif kind == "result":
                    rt = event.get("result")
                    if isinstance(rt, str):
                        result_text = rt

            await proc.wait()
            try:
                stderr_bytes = await stderr_task
            except Exception:
                stderr_bytes = b""

            if self._running_tasks.was_cancelled(chat_id):
                log.info(
                    "Brain.astream raising BrainCancelled for chat %d",
                    chat_id,
                )
                raise BrainCancelled("brain subprocess cancelled via /cancel")

            if proc.returncode != 0:
                err = stderr_bytes.decode(errors="replace").strip()
                if self._session.is_initialized() and "No conversation found" in err:
                    old_uuid = self._session.get()
                    new_uuid = self._session.rotate()
                    log.warning(
                        "Claude Code lost session %s; rotated to %s",
                        old_uuid, new_uuid,
                    )
                    raise SessionLost(
                        "Claude Code session was lost. Rotated to new session.",
                    )
                # Classify against stderr + buffered assistant text.
                # Without this fallback, the May 2026 schedule crash
                # surfaced as "(no stderr)" instead of "API Error:
                # 500…" — see ``_classify_brain_failure``.
                cls, message = _classify_brain_failure(
                    stderr_text=err,
                    assistant_text="\n".join(assistant_text_parts).strip(),
                )
                raise cls(
                    f"claude -p exited {proc.returncode}: {message}",
                )
        finally:
            status_file.delete()
            await self._running_tasks.unregister(chat_id)

        # Defensive: if no deltas streamed (unusual but observed in
        # very-short replies on some backends) fall back to the
        # result-event text so the caller's bubble isn't empty.
        if not accumulated and result_text:
            yield result_text
            accumulated = result_text

        # Cross-check (logged only — never raises). Useful for
        # spotting silent stream-json schema drift.
        result_clean = result_text.strip()
        accumulated_clean = accumulated.strip()
        if result_clean and accumulated_clean and result_clean != accumulated_clean:
            log.debug(
                "Brain.astream: result/delta mismatch for chat %d "
                "(result=%d chars, deltas=%d chars)",
                chat_id, len(result_clean), len(accumulated_clean),
            )

    @staticmethod
    async def _kill_group(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                log.error("claude -p (pid=%s) ignored SIGKILL", proc.pid)

    # ─── Brain ABC implementations beyond ``respond`` ────────────
    #
    # Phase A wires every method that has a natural existing
    # implementation to its existing call site so behaviour is
    # unchanged. Methods deferred to Phase B / C raise
    # ``NotImplementedError`` with the phase tag so a stray call
    # surfaces immediately rather than silently misbehaving.

    def build_system_prompt(self) -> str:
        """ABC method; delegates to the module-level
        ``build_system_prompt(workspace)`` so the cached ``respond()``
        path and direct callers (background tasks) see byte-identical
        prompts."""
        return build_system_prompt(self._workspace)

    async def spawn_aux(
        self,
        prompt: str,
        *,
        model_tier: str | None = None,
        timeout_seconds: float = 60.0,
        env_overrides: dict[str, str] | None = None,
        allow_tools: bool = False,
        cwd: Path | None = None,
        subsystem: str | None = None,
        reasoning_level: str | None = None,
        context_window: int | None = None,
    ) -> AuxResult:
        """Phase B implementation. Spawns ``claude -p`` synchronously
        via :func:`subprocess.run` (wrapped in :func:`asyncio.to_thread`
        for the async contract). Used by every aux subsystem — curator,
        judges, extractors — instead of each one shelling out itself.

        ``model_tier`` is resolved via
        :func:`core.yaml_config.model_for_tier` for ``"claude-code"``;
        the resolution accepts both abstract tiers (``small``,
        ``large``) and legacy raw model names (``haiku``,
        ``claude-sonnet-4-6``) for back-compat with existing
        ``models.<subsystem>`` keys. ``None`` → no ``--model`` flag,
        let claude-code pick its native default.

        ``allow_tools=True`` adds ``--permission-mode bypassPermissions``
        so the spawned brain can use tools without an interactive
        prompt (used by the skill curator and learning review). Off
        by default so judges and classifiers — which expect text-only
        verdicts — fail loud if the model unexpectedly tries a tool.

        On a non-zero exit, returns the ``AuxResult`` with the
        non-zero ``returncode``; subsystems decide how to handle it.
        Timeout raises :class:`BrainTimeoutError`.
        """
        from vexis_agent.core.yaml_config import model_for_tier

        argv: list[str] = ["claude", "-p"]
        model_id = model_for_tier("claude-code", model_tier)
        if model_id:
            argv += ["--model", model_id]
        # Reasoning effort flag — added 2026-05-08 for the picker's
        # reasoning step. claude-code's CLI accepts ``--effort
        # <level>`` (low/medium/high/xhigh/max). The picker only
        # surfaces levels the API capability response advertises
        # for the chosen model, but we don't validate here at the
        # spawn level; the CLI itself errors out cleanly on an
        # unsupported level/model pair. ``None`` → no flag, brain
        # picks default.
        if reasoning_level:
            argv += ["--effort", reasoning_level]
        # context_window: accepted for ABC stability but inert —
        # claude-code's CLI has no runtime context flag (probe
        # 2026-05-08 against `claude --help`). Documented in
        # Brain.spawn_aux's docstring.
        _ = context_window
        argv.append(prompt)
        if allow_tools:
            argv += ["--permission-mode", "bypassPermissions"]

        env = dict(os.environ)
        if env_overrides:
            env.update(env_overrides)

        workdir = str(cwd if cwd is not None else self._workspace)

        def _run() -> AuxResult:
            try:
                cp = subprocess.run(
                    argv,
                    env=env,
                    cwd=workdir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise BrainTimeoutError(
                    f"claude -p aux call timed out after {timeout_seconds}s"
                ) from exc
            except FileNotFoundError as exc:
                raise BrainNotInstalled(
                    "`claude` not on PATH; install Claude Code: "
                    "https://docs.anthropic.com/claude/claude-code"
                ) from exc
            except OSError as exc:
                raise BrainError(f"claude -p aux spawn failed: {exc}") from exc

            stdout = (cp.stdout or b"").decode("utf-8", errors="replace")
            stderr = (cp.stderr or b"").decode("utf-8", errors="replace")

            # Day 2 model UX: spawn-site backstop. claude-code's
            # bad-model diagnostic lands on stdout with a stable
            # prefix; non-zero exit confirms the rejection. We
            # raise a structured BrainModelNotFoundError carrying
            # the same suggested_fix copy the validator emits
            # pre-write so the caller's surface (curator log /
            # dashboard / slash) shows the same actionable text
            # regardless of which gate caught the condition.
            if (
                cp.returncode != 0
                and _CC_MODEL_NOT_FOUND_STDOUT_MARKER in stdout
                and model_id  # only attribute the failure when we set --model
            ):
                from vexis_agent.core.model_validator import (
                    CLAUDE_CODE_MODEL_NOT_FOUND_FIX_TEMPLATE,
                )
                raise BrainModelNotFoundError(
                    subsystem=subsystem or "<unknown>",
                    model_id=model_id,
                    brain_kind="claude-code",
                    suggested_fix=CLAUDE_CODE_MODEL_NOT_FOUND_FIX_TEMPLATE.format(
                        model_id=model_id,
                        subsystem=subsystem or "this subsystem",
                    ),
                )

            return AuxResult(
                stdout=stdout,
                stderr=stderr,
                returncode=cp.returncode,
            )

        return await asyncio.to_thread(_run)

    def session_token(self) -> str | None:
        """Return the active SessionStore UUID. Always a string for
        ``ClaudeCodeBrain`` — the SessionStore generates a UUID at
        construction time, so there is never a "no token yet" state.
        ``None`` is part of the ABC's contract for brains that
        generate the id only on first use (e.g. opencode)."""
        return self._session.get()

    def rotate_session(self) -> str:
        """Mint a fresh UUID and return it. Used by
        ``MessageHandler.handle_clear`` and the ``SessionLost``
        recovery path inside ``respond``."""
        return self._session.rotate()

    def iter_session_metas(self) -> Iterator:
        """Walk the workspace's claude-code projects directory.
        Delegates to ``core.transcripts.iter_session_metas`` so the
        existing curator/relationships eligibility scan is unchanged."""
        from vexis_agent.core.transcripts import iter_session_metas

        return iter_session_metas(self._workspace)

    def iter_messages(self, session_id: str) -> Iterator:
        """Stream user+assistant turns from one session JSONL.
        Delegates to ``core.transcripts.iter_messages`` after
        translating ``session_id`` (a UUID) into the JSONL path."""
        from vexis_agent.core.transcripts import claude_session_jsonl_dir, iter_messages

        jsonl_path = claude_session_jsonl_dir(self._workspace) / f"{session_id}.jsonl"
        return iter_messages(jsonl_path)

    def is_brain_owned_session(self, session_id: str) -> bool:
        """Content-prefix check against the first user-turn of the
        session JSONL. Delegates to ``core.transcripts._is_curator_owned``
        — the recursion guard the learning curator already uses."""
        from vexis_agent.core.transcripts import _is_curator_owned, claude_session_jsonl_dir

        jsonl_path = claude_session_jsonl_dir(self._workspace) / f"{session_id}.jsonl"
        if not jsonl_path.exists():
            return False
        return _is_curator_owned(jsonl_path)

    def write_mcp_config(self, servers: list[McpServerSpec]) -> Path:
        """Write claude-code's MCP server config to
        ``<workspace>/.mcp.json``.

        Phase C Day 6: replaces the pre-Day-6 NotImplementedError
        with the real writer. The format is claude-code's native
        ``mcpServers`` shape:

            {"mcpServers": {<name>: {"command", "args", "env"}}}

        Strategy: replace-all rather than namespace-merge.
        claude-code's ``.mcp.json`` is a workspace-scoped config
        the user owns end-to-end; vexis's installer is the only
        programmatic writer (the curator never rewrites it). If
        the user maintains custom entries by hand, they live in
        ``~/.claude/settings.json`` (per-user) or in a separate
        ``.mcp.json`` outside the workspace — not here. This
        keeps the writer simple and matches claude-code's own
        installer convention.

        Atomic write via tempfile + rename. Empty server list
        produces ``{"mcpServers": {}}`` (still valid JSON
        claude-code will read without error).
        """
        path = self._workspace / ".mcp.json"
        servers_dict: dict = {}
        for spec in servers:
            entry: dict = {
                "command": spec.command,
                "args": list(spec.args),
            }
            if spec.env:
                entry["env"] = dict(spec.env)
            servers_dict[spec.name] = entry
        merged = {"mcpServers": servers_dict}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(merged, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        return path

    def instruction_file_name(self) -> str:
        return "CLAUDE.md"

    def instruction_search_paths(self, workspace: Path) -> list[Path]:
        """claude-code reads project ``CLAUDE.md`` first, then the
        per-user global at ``~/.claude/CLAUDE.md``. Returned in
        lookup order so ``/status`` can render "instructions read
        from: …" in the same order claude-code consults them."""
        return [workspace / "CLAUDE.md", Path.home() / ".claude" / "CLAUDE.md"]

    async def healthcheck(self) -> BrainHealth:
        """Confirm the ``claude`` binary is on PATH. Phase A keeps
        this minimal — no auth check yet. Phase C may extend."""
        if shutil.which("claude") is None:
            return BrainHealth(
                ok=False,
                error="`claude` not on PATH",
                hints=[
                    "Install Claude Code: https://docs.anthropic.com/claude/claude-code",
                    "Then verify with: claude --version",
                ],
            )
        return BrainHealth(ok=True, error=None, hints=[])

    async def kill_in_flight(self) -> None:
        """No-op for Phase A — today ``/cancel`` kills the in-flight
        proc via ``RunningTasks.cancel()`` (which calls ``proc.kill``
        on the proc registered by ``RunningTasks.attach``). This hook
        is exposed on the ABC for a future world where ``/cancel``
        wants to talk to the brain directly."""
        return None
