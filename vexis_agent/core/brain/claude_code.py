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
import time
import uuid as _uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
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
    SessionLike,
    SessionLost,
    mcp_spec_to_claude_code_entry,
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
from vexis_agent.core.workspace_snapshot import (
    diff as _snapshot_diff,
    snapshot as _take_snapshot,
)
from vexis_agent.core.brain._memory_scope import wrap_with_memory_scope
from vexis_agent.core.yaml_config import (
    brain_background_agent_wait,
    brain_file_mutation_footer_enabled,
)

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

# Issue #61 — how long the streaming parse loop waits for stdout to close
# AFTER the terminal ``result`` event before treating the still-alive CLI
# as lingering on background subagents. Deliberately short: on an ordinary
# turn the process closes stdout within milliseconds of the result event
# (EOF → the normal, byte-for-byte path), so 5s is pure slack. When it
# elapses AND the process is still running, the foreground turn is done but
# the CLI is holding open background subagents (Agent tool /
# ``run_in_background``, background-by-default since claude-code v2.1.198),
# so we hand the process to a supervisor and free the chat instead of
# pinning the drain + typing indicator for the full bg-wait ceiling.
_POST_RESULT_LINGER_GRACE_SECONDS = 5.0


def _apply_bg_wait_env(env: dict[str, str]) -> None:
    """Set ``CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`` on ``env`` from config.

    Issue #61: the headless ``claude -p`` process lingers at exit waiting
    for background subagents (Agent tool / ``run_in_background``), bounded
    by this env knob — default 600000ms (10 min) in the CLI since v2.1.182.
    vexis raises the ceiling to ``brain.background_agent_wait`` (default
    1800s → ``"1800000"``); a configured ``0`` seconds means unlimited and
    is exported as the literal ``"0"``. Mutates ``env`` in place so all
    three spawn sites (``respond``, ``astream``, ``spawn_aux``) share one
    source of truth; reads config on every call (hot-reload).
    """
    wait_seconds = brain_background_agent_wait()
    env["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] = (
        "0" if wait_seconds == 0 else str(wait_seconds * 1000)
    )

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

    Layers (top → bottom): SOUL.md (or default), the assembled
    capability docs (CAPABILITIES.md core + per-tool prompt blocks —
    see vexis_agent.core.capabilities, issue #30), MEMORY.md block,
    USER.md block, RELATIONSHIPS.md block, skills index. Each layer is
    independent and dropped if empty.
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

    # Capability docs are assembled from per-capability blocks that
    # live next to the code they document (issue #30). The shrunk
    # CAPABILITIES.md (identity + the add-on/skill/MCP model) is block
    # 0; each core tool (browser, desktop, sandbox, …) contributes its
    # own section so an engine change updates its own guidance in the
    # same PR. Byte-identical to the old monolith — guarded by
    # tests/test_capability_blocks.py. Ships as package data + Python
    # modules, readable identically under wheels and source checkouts.
    from vexis_agent.core.capabilities import assemble_capability_docs

    soul = _read_markdown(workspace / "SOUL.md") or DEFAULT_SOUL
    capabilities = assemble_capability_docs()
    parts: list[str] = [soul]
    if capabilities.strip():
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


# ── Tool-span observability ──────────────────────────────────────
# Issue #49: a slow turn (a 190s production lookup) was one opaque
# block — no way to attribute the seconds to a specific tool. These
# helpers pair each stream-json ``tool_use`` block with its later
# ``tool_result`` block so both parse paths can (a) log a greppable
# per-tool span line and (b) — on the streaming path — emit a
# ``tool_end`` UX event the dashboard can render as a finished span.


@dataclass
class _PendingSpan:
    """One tool call that has started but not yet reported a result."""

    name: str
    target: str | None
    # ``time.monotonic()`` at start. Durations are monotonic deltas so
    # a wall-clock jump mid-call (NTP step, suspend/resume) can't
    # produce a negative or wildly-wrong span length.
    started_at: float


class _ToolSpanTracker:
    """Pairs ``tool_use`` starts with ``tool_result`` ends within one
    turn, keyed by the block id (claude-code's globally-unique
    ``toolu_…``). Because ids are unique across the main thread and any
    subagent sidechain, pairing stays correct even when nested Task
    tools interleave with the main thread.

    ``end`` logs the span at INFO (``tool-span …``) so a slow turn is
    attributable from the daemon logs alone, regardless of which
    transport drove it. The streaming path additionally yields the
    returned dict as a ``tool_end`` UX event; the buffered path logs
    only (nothing downstream consumes events there).
    """

    def __init__(self, chat_id: int) -> None:
        self._chat_id = chat_id
        self._pending: dict[str, _PendingSpan] = {}

    def start(self, tool_id: str, name: str, target: str | None) -> None:
        # Last-writer-wins on a duplicate id (shouldn't happen — ids are
        # unique): the earlier entry is dropped and surfaces via
        # ``log_unclosed`` at stream end.
        self._pending[tool_id] = _PendingSpan(
            name=name, target=target, started_at=time.monotonic(),
        )

    def end(self, tool_id: str, is_error: bool) -> dict | None:
        """Close the span for ``tool_id`` and return the ``tool_end``
        event dict, or ``None`` for an id we never saw a start for
        (an out-of-band or already-closed result — nothing to
        attribute, so the caller emits nothing)."""
        pending = self._pending.pop(tool_id, None)
        if pending is None:
            return None
        duration_ms = int((time.monotonic() - pending.started_at) * 1000)
        status = "error" if is_error else "completed"
        # The daemon-log half of the observability ask. Greppable on
        # ``tool-span``; carries everything needed to attribute wall
        # time to a specific tool without opening the transcript.
        # ``target`` is free text (Bash commands carry spaces and
        # ``=``), so it goes LAST: every fixed-vocabulary key a log
        # parser matches on (``duration_ms=``, ``status=``) precedes
        # it, and a key=value tokenizer can take everything after
        # ``target=`` as the value.
        log.info(
            "tool-span chat=%d tool=%s duration_ms=%d status=%s target=%s",
            self._chat_id, pending.name, duration_ms, status, pending.target,
        )
        # ``ts`` is wall-clock (epoch ms) purely for correlation with
        # the timestamped log stream; ``duration_ms`` is the monotonic
        # measurement above, not ``ts`` minus a start ``ts``.
        return {
            "type": "tool_end",
            "name": pending.name,
            "target": pending.target,
            "id": tool_id,
            "ts": int(time.time() * 1000),
            "duration_ms": duration_ms,
            "status": status,
        }

    def log_unclosed(self) -> None:
        """Spans still open at stream end: the result never arrived
        (turn cancelled/timed out mid-tool, or the stream closed
        without a matching ``tool_result``). DEBUG only — there's no
        honest duration to emit, so no event is produced."""
        for tool_id, pending in self._pending.items():
            log.debug(
                "tool-span unclosed chat=%d tool=%s id=%s target=%s",
                self._chat_id, pending.name, tool_id, pending.target,
            )


def _unstreamed_remainder(block_text: str, segment: str) -> tuple[str, bool]:
    """Return the portion of a reconciled assistant text block that was
    NOT already streamed as ``text_delta`` chunks, plus a mismatch flag.

    ``segment`` is the concatenation of the deltas streamed for this
    block since the last reconciliation. Prefix-match dedup:

    * ``segment`` empty → nothing streamed; remainder is the whole
      block (the batched-model case — claude-sonnet-5 in particular
      delivers inter-tool text as a buffered block with no deltas).
    * block starts with ``segment`` → deltas covered a prefix; the
      remainder is the un-streamed suffix.
    * ``segment`` starts with block (or equal) → the block was fully
      streamed already; remainder is empty.
    * otherwise → deltas and the block genuinely disagree (shouldn't
      happen); remainder empty and ``mismatch=True`` so the caller can
      log it rather than emit possibly-wrong text.
    """
    if not segment:
        return block_text, False
    if block_text.startswith(segment):
        return block_text[len(segment):], False
    if segment.startswith(block_text):
        return "", False
    return "", True


async def _read_stream_events(
    stream: asyncio.StreamReader | None, status_file: StatusFile
) -> tuple[str, str]:
    """Consume the brain's stream-json stdout, updating ``status_file``
    on every tool_use event.

    Also emits a per-tool span log line (``tool-span …`` at INFO) by
    pairing each ``tool_use`` block with its later ``tool_result``
    block — the buffered-path half of issue #49's observability ask.
    The chat id for that log comes from ``status_file.chat_id`` (the
    caller already threads it through the StatusFile), so the return
    contract below is untouched. No UX events are emitted here: the
    buffered ``respond`` path has no live consumer for them — logs only.

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
    span_tracker = _ToolSpanTracker(status_file.chat_id)
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
                    tool_id = block.get("id")
                    if isinstance(tool_id, str) and tool_id:
                        span_tracker.start(tool_id, name, target)
                elif btype == "text":
                    text = block.get("text")
                    if isinstance(text, str) and text:
                        assistant_text_parts.append(text)
        elif kind == "user":
            # Tool results carry the closing edge of a span. The
            # buffered path emits no event — the span log inside
            # ``end`` is the whole point here.
            content = event.get("message", {}).get("content") or []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_result":
                        continue
                    tool_id = block.get("tool_use_id")
                    if isinstance(tool_id, str) and tool_id:
                        span_tracker.end(tool_id, bool(block.get("is_error")))
        elif kind == "result":
            result_text = event.get("result")
            if isinstance(result_text, str):
                final_text = result_text
    span_tracker.log_unclosed()
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
        *,
        extra_prompt_blocks: Callable[[], list[str]] | None = None,
    ) -> None:
        self._workspace = workspace
        self._session = session
        self._running_tasks = running_tasks
        # Codemux watcher hook (LAYER 2 of the watcher spec). The
        # daemon wires this to ``WatcherController.header_block()``
        # which returns AT MOST one short line listing the active
        # workspace count. None = no header injection. Per-session
        # cache below freezes the result for the session so the prefix
        # cache stays stable across turns; the per-spawn re-read of
        # this callable matches the spec's "at Vexis session spawn"
        # semantics. Always None when Codemux MCP isn't configured.
        self._extra_prompt_blocks = extra_prompt_blocks
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
        # by design, see the memory/skills capability blocks
        # (tools/memory_capability.py, issue #30) for the model-facing
        # documentation of this trap.
        self._system_prompt_cache: dict[str, str] = {}
        # Issue #9: per-chat buffer of files mutated during the most
        # recent ``respond``/``astream`` call. Drained by the handler
        # via :meth:`consume_files_changed` when it builds the next
        # turn's user message. Cleared on read so two reads in a row
        # don't double-report a single turn's mutations.
        self._files_changed_by_chat: dict[int, list[str]] = {}
        # Issue #61: still-running ``claude -p`` processes handed off by
        # the streaming path once their result event arrived but the CLI
        # lingered on background subagents. Keyed by chat_id; each value is
        # the supervisor task draining + bounding that process. A
        # subsequent turn for the same chat consults this to warn; daemon
        # shutdown / :meth:`cancel_lingering_supervisors` cancels them all.
        self._linger_supervisors: dict[int, asyncio.Task] = {}

    def _system_prompt_for(self, session_uuid: str) -> str:
        cached = self._system_prompt_cache.get(session_uuid)
        if cached is not None:
            return cached
        prompt = build_system_prompt(self._workspace)
        # Watcher header (LAYER 2). The provider returns a list (0 or
        # 1 strings today; future plugins may add more). Resolved once
        # per session UUID — the cache below freezes the result so
        # mid-session changes to the registry don't perturb the
        # prefix-cache hash. ``/clear`` rotates the UUID and the
        # header re-resolves naturally.
        if self._extra_prompt_blocks is not None:
            try:
                blocks = self._extra_prompt_blocks() or []
            except Exception:
                log.exception("extra_prompt_blocks provider raised; ignoring")
                blocks = []
            for block in blocks:
                if isinstance(block, str) and block.strip():
                    prompt = prompt + "\n\n" + block.strip()
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
        session: "SessionLike | None" = None,
    ) -> str:
        # Issue #48: ``session`` selects the session this turn runs
        # against. ``None`` (Telegram, the shared web chat) is the bound
        # active-session store — historical behaviour. A non-``None``
        # ``SessionView`` (one per web conversation) routes every
        # session read/write below through that handle instead, so
        # concurrent conversations never share a claude session id.
        sess = session if session is not None else self._session
        log.info(
            "Brain.respond starting for chat %d%s%s",
            chat_id,
            f" (model override: {model})" if model else "",
            f" (reasoning: {reasoning_level})" if reasoning_level else "",
        )
        session_id = sess.get()
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
            sess.is_initialized()
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

        # Issue #9 — file-mutation verifier footer. Snapshot the
        # workspace BEFORE the brain subprocess runs; diff AFTER (in
        # the finally below) so failures, cancellations, and timeouts
        # still record any partial writes the brain made. The
        # snapshot is best-effort: a failed walk returns ``{}`` and
        # the verifier footer degrades gracefully to "(none detected)".
        before_snapshot = await self._maybe_take_snapshot()

        # Inline retry on transient upstream failures (Anthropic 5xx /
        # 429 / network blip). See ``_TRANSIENT_RETRY_DELAY_SECONDS``
        # comment for the rationale: one retry absorbs sub-second
        # hiccups; anything longer wants caller-side backoff. /cancel
        # arriving between attempts short-circuits the loop so the
        # user's Stop button is honoured even mid-retry.
        try:
            for attempt in range(1, _TRANSIENT_MAX_ATTEMPTS + 1):
                try:
                    final_text = await self._attempt_respond(argv, chat_id, sess)
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
        finally:
            await self._record_files_changed(chat_id, before_snapshot)

        # Mark only after a successful exit so a failed first call doesn't
        # leave us thinking the UUID is live.
        if not sess.is_initialized():
            sess.mark_initialized()
        response = (final_text or "").strip()
        for reason, asked in audit_destructive_mentions(response):
            if asked:
                log.info("Vexis confirmed before destructive: %s", reason)
            else:
                log.info("Vexis ran without confirm: %s", reason)
        log.info("Brain.respond completed for chat %d", chat_id)
        return response

    async def _attempt_respond(
        self, argv: list[str], chat_id: int, sess: "SessionLike",
    ) -> str:
        """One spawn-and-await cycle for :meth:`respond`.

        ``sess`` is the session handle the turn runs against (issue #48)
        — the active-session store by default, or the conversation's
        :class:`SessionView`. The session-lost rotate-and-raise recovery
        below routes through it so a lost conversation rotates only that
        conversation's session id, not the shared active one.

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
        # Issue #61: bound how long this claude -p lingers on background
        # subagents at exit (see _apply_bg_wait_env / brain.background_agent_wait).
        _apply_bg_wait_env(env)

        status_file = StatusFile(chat_id)
        status_file.start()

        final_text = ""
        assistant_text = ""
        stderr_bytes = b""
        # Per-subagent memory isolation (2026-06-12 freeze fix): run the
        # brain (and its whole tool subtree) in its own memory-capped
        # systemd scope so a runaway tool OOMs in isolation instead of
        # throttle-freezing the shared bot cgroup. No-ops when disabled
        # or systemd-run is absent. start_new_session below keeps the
        # process group intact so _kill_group's killpg still reaches the
        # real claude through the scope wrapper.
        spawn_argv = wrap_with_memory_scope(argv)
        try:
            proc = await asyncio.create_subprocess_exec(
                *spawn_argv,
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

            # Issue #61: the buffered path deliberately keeps process-exit
            # semantics (blocks on proc.wait() up to BRAIN_TIMEOUT_SECONDS)
            # rather than the streaming path's linger decoupling. It returns
            # a single ``str`` with no event channel to emit a
            # ``background_lingering`` notice on, and its callers are the
            # already-background/low-priority turns (goal continuations,
            # streaming-disabled deployments) where holding the drain
            # matters less. The env-injected bg-wait ceiling above still
            # bounds how long the CLI lingers on background subagents; the
            # brain timeout is the outer bound. See docs/background-subagents.md.
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
                if sess.is_initialized() and "No conversation found" in err:
                    old_uuid = sess.get()
                    new_uuid = sess.rotate()
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
        session: "SessionLike | None" = None,
    ) -> AsyncIterator[str | dict]:
        """Native streaming. Spawns ``claude --print`` with
        ``--include-partial-messages`` so stream-json emits
        ``content_block_delta`` events as the model generates each
        chunk; yields the ``text_delta.text`` from each. Falls back
        to yielding the buffered final text only if the partial-
        message stream is empty (no tokens delivered — should never
        happen on success but defensive against API quirks).

        Yields the ``str | dict`` discriminated union documented on
        :meth:`Brain.astream <vexis_agent.core.brain.base.Brain.astream>`:
        text deltas as ``str``, and ``{"type": "tool", …}`` /
        ``{"type": "tool_end", …}`` observability events as ``dict``.

        Same per-turn override semantics as :meth:`respond`. Same
        cancellation, timeout, session-lost, and error mapping —
        the spawn/kill machinery is identical, only the event-loop
        differs.

        Tool-use events still update the StatusFile so /status
        works exactly like the buffered path. The ``result`` event
        (if any) is captured to verify against the concatenated
        deltas; mismatch is logged but not fatal.

        ``session`` has the same meaning as on :meth:`respond` (issue
        #48): ``None`` drives the active-session store; a
        ``SessionView`` runs the streamed turn against one conversation.
        """
        sess = session if session is not None else self._session
        log.info(
            "Brain.astream starting for chat %d%s%s",
            chat_id,
            f" (model override: {model})" if model else "",
            f" (reasoning: {reasoning_level})" if reasoning_level else "",
        )
        session_id = sess.get()
        # Same disk-state-authority --session-id-vs-resume decision
        # as :meth:`respond`. The streaming path is the *hottest*
        # path for the post-cancel bug because the web chat's Stop
        # button fires here; without this check, every Stop →
        # resend produces "Session ID already in use" even though
        # the in-memory ``is_initialized`` flag is still False.
        if (
            sess.is_initialized()
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

        # Issue #9 — same file-mutation snapshot dance as :meth:`respond`.
        # See the analogous block there for the rationale; the streaming
        # path runs the same brain subprocess so writes land identically.
        # The diff is recorded in the ``finally`` so a cancelled stream
        # or a transient that exhausts retries still surfaces any
        # partial writes the brain made.
        before_snapshot = await self._maybe_take_snapshot()

        try:
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
                    async for event in self._attempt_astream(argv, chat_id, sess):
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

            if not sess.is_initialized():
                sess.mark_initialized()

            log.info("Brain.astream completed for chat %d", chat_id)
        finally:
            await self._record_files_changed(chat_id, before_snapshot)

    async def _attempt_astream(
        self, argv: list[str], chat_id: int, sess: "SessionLike",
    ) -> AsyncIterator:
        """One spawn-and-stream cycle for :meth:`astream`.

        Async generator yielding the same discriminated union as
        :meth:`astream` (text ``str`` or a ``{"type": "tool" | "tool_end",
        …}`` observability ``dict``). Raises the same exception taxonomy
        as :meth:`_attempt_respond` — caller decides whether to retry,
        based on whether anything was yielded.

        ``sess`` is the per-turn session handle (issue #48); the
        session-lost rotate-and-raise recovery routes through it.
        """
        reservation = await self._running_tasks.reserve(chat_id)
        env = {**os.environ, "VEXIS_CHAT_ID": str(chat_id)}
        # Issue #61: bound how long this claude -p lingers on background
        # subagents at exit (see _apply_bg_wait_env / brain.background_agent_wait).
        _apply_bg_wait_env(env)

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
        # Issue #61: True once the terminal ``result`` event has been
        # seen. After that, a stdout read that stalls past the linger
        # grace while the process is still alive means the CLI is holding
        # open background subagents — the turn is done but the process
        # isn't. See the readline timeout branch below.
        result_seen = False
        assistant_text_parts: list[str] = []
        stderr_bytes = b""
        proc: asyncio.subprocess.Process | None = None
        # Issue #49 boundary-flush state (main thread only).
        # ``segment_delta_text`` is the running concatenation of the
        # text deltas streamed for the current text block; when that
        # block's ``assistant`` event arrives we diff the block against
        # it to recover any inter-tool text the model batched (delivered
        # as a block with no deltas) and stash the un-streamed remainder
        # in ``pending_tail``, to be flushed at the next tool boundary
        # (or at end-of-stream on success). See _unstreamed_remainder.
        segment_delta_text = ""
        pending_tail = ""
        # Issue #49 tool spans. Both the ``tool`` start dicts and the
        # ``tool_end`` dicts flow from here; ``end`` also logs the
        # ``tool-span`` INFO line. Sidechain (subagent) tools are
        # tracked too — ids are globally unique so pairing is safe, and
        # their names/targets already surface via the tool-start yields.
        span_tracker = _ToolSpanTracker(chat_id)
        # Same per-subagent memory scoping as _attempt_respond — see the
        # rationale there. Wrap before spawn; no-op when disabled/absent.
        spawn_argv = wrap_with_memory_scope(argv)
        try:
            proc = await asyncio.create_subprocess_exec(
                *spawn_argv,
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
                # Read budget. Before the result event, bound each line
                # by the remaining overall brain timeout so a hung
                # subprocess can't deadlock the iterator. After the result
                # event (issue #61), switch to the short linger grace: on
                # an ordinary turn stdout closes within milliseconds
                # (EOF → break, byte-for-byte the pre-#61 path), but if the
                # grace elapses with the process still alive the CLI is
                # lingering on background subagents and we hand it off.
                if result_seen:
                    read_timeout = _POST_RESULT_LINGER_GRACE_SECONDS
                else:
                    elapsed = (
                        asyncio.get_event_loop().time() - stream_started_at
                    )
                    read_timeout = max(1.0, BRAIN_TIMEOUT_SECONDS - elapsed)
                try:
                    line = await asyncio.wait_for(
                        stream.readline(), timeout=read_timeout,
                    )
                except asyncio.TimeoutError as exc:
                    if result_seen and proc.returncode is None:
                        # The turn's canonical reply already arrived but
                        # the CLI is still alive holding background
                        # subagents. Flush any batched trailing text,
                        # hand the process to a brain-owned supervisor,
                        # and return normally: the ``finally`` below runs
                        # (status_file.delete + slot unregister) which is
                        # exactly right — the user's turn IS done and the
                        # chat should be freed. The supervisor now owns
                        # stdout/stderr draining and the eventual kill.
                        if pending_tail:
                            accumulated += pending_tail
                            yield pending_tail
                            pending_tail = ""
                        wait_seconds = self._handoff_lingering(
                            chat_id, proc, stream, stderr_task,
                        )
                        yield {
                            "type": "background_lingering",
                            "wait_seconds": wait_seconds,
                        }
                        if result_text.strip():
                            yield {"type": "final", "text": result_text}
                        return
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
                # ``parent_tool_use_id`` is a top-level field: None on
                # the main thread, set for a subagent (Task) sidechain.
                # The boundary-flush machinery is main-thread only —
                # sidechain text must never leak into the reply stream.
                is_sidechain = bool(event.get("parent_tool_use_id"))
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
                                # A pending batched remainder always
                                # predates any NEW delta text (its block
                                # was reconciled before these tokens were
                                # generated), so flush it first — without
                                # this, a batched text block followed by
                                # a streamed one with no tool call in
                                # between would land out of order in
                                # both the live stream and the ``done``
                                # concatenation.
                                if pending_tail:
                                    accumulated += pending_tail
                                    yield pending_tail
                                    pending_tail = ""
                                accumulated += text
                                yield text
                                # Only main-thread deltas count toward the
                                # segment we reconcile against the text
                                # block. Sidechain deltas are still yielded
                                # above (status quo) but never reconciled.
                                if not is_sidechain:
                                    segment_delta_text += text
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
                                tool_id = block.get("id")
                                # Boundary flush: with per-block assistant
                                # events the text block's event precedes
                                # this tool_use block's event, so any
                                # inter-tool text the model batched sits
                                # in ``pending_tail`` right now. Emit it
                                # as a live chunk BEFORE the tool event so
                                # the "Checking the OE catalog…" marker
                                # lands at the tool boundary, not at turn
                                # end. Main thread only.
                                if not is_sidechain and pending_tail:
                                    accumulated += pending_tail
                                    yield pending_tail
                                    pending_tail = ""
                                if isinstance(tool_id, str) and tool_id:
                                    span_tracker.start(tool_id, name, target)
                                # Tool event → chat UI. Distinct from
                                # text deltas; consumers must distinguish
                                # via ``isinstance``. ``id`` + ``ts``
                                # (epoch ms) enrich it for span
                                # correlation. Documented contract on
                                # Brain.astream.
                                yield {
                                    "type": "tool",
                                    "name": name,
                                    "target": target,
                                    "id": tool_id,
                                    "ts": int(time.time() * 1000),
                                }
                            elif btype == "text":
                                # Always captured for the failure-
                                # classification path (byte-for-byte as
                                # before — API errors arrive HERE as one
                                # buffered text block with no preceding
                                # deltas). On the main thread we ALSO
                                # reconcile it against the streamed
                                # deltas: any un-streamed remainder is
                                # stashed for a boundary/end flush so
                                # batched inter-tool text still reaches
                                # the stream. Sidechain text is captured
                                # but never reconciled/flushed.
                                text = block.get("text")
                                if isinstance(text, str) and text:
                                    assistant_text_parts.append(text)
                                    if not is_sidechain:
                                        remainder, mismatch = (
                                            _unstreamed_remainder(
                                                text, segment_delta_text,
                                            )
                                        )
                                        if mismatch:
                                            log.debug(
                                                "Brain.astream: delta/text "
                                                "mismatch for chat %d "
                                                "(segment=%d chars, "
                                                "block=%d chars) — dropping "
                                                "unreconciled remainder",
                                                chat_id,
                                                len(segment_delta_text),
                                                len(text),
                                            )
                                        if remainder:
                                            pending_tail += remainder
                                        # Consume the matched prefix
                                        # rather than resetting: when a
                                        # batched (old-CLI) assistant
                                        # event carries SEVERAL text
                                        # blocks, the streamed deltas
                                        # span all of them, so the
                                        # surplus after this block
                                        # belongs to the next block —
                                        # resetting here would make
                                        # that block look un-streamed
                                        # and double-flush it.
                                        if segment_delta_text.startswith(
                                            text,
                                        ):
                                            segment_delta_text = (
                                                segment_delta_text[
                                                    len(text):
                                                ]
                                            )
                                        else:
                                            segment_delta_text = ""
                elif kind == "user":
                    # Tool results carry the closing edge of a span.
                    # Emit ``tool_end`` (and log the span) for both main
                    # and sidechain tools — names/targets already surface,
                    # so this adds observability without new leakage.
                    content = event.get("message", {}).get("content") or []
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") != "tool_result":
                                continue
                            tool_id = block.get("tool_use_id")
                            if not (isinstance(tool_id, str) and tool_id):
                                continue
                            span = span_tracker.end(
                                tool_id, bool(block.get("is_error")),
                            )
                            if span is not None:
                                yield span
                elif kind == "result":
                    rt = event.get("result")
                    if isinstance(rt, str):
                        result_text = rt
                    # Issue #61: from here on, a stalled stdout read while
                    # the process is still alive is a lingering-subagent
                    # signal, not a hang. Flip AFTER capturing the text so
                    # a handoff on the very next read carries the reply.
                    result_seen = True

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
                if sess.is_initialized() and "No conversation found" in err:
                    old_uuid = sess.get()
                    new_uuid = sess.rotate()
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
            # DEBUG-log any span whose result never arrived (cancel /
            # timeout mid-tool). Harmless no-op on a clean turn.
            span_tracker.log_unclosed()
            status_file.delete()
            await self._running_tasks.unregister(chat_id)

        # Success path only — this line is unreachable when the
        # returncode check above raised (the finally ran, the
        # exception propagated). Final boundary flush: any inter-tool
        # or trailing text the model batched (delivered as an
        # ``assistant`` text block with no deltas, e.g. the final
        # message on a batched turn) is still sitting in
        # ``pending_tail``; emit it now so it reaches the stream. This
        # is deliberately BELOW the raise: on the failure path an
        # API-error message lands in ``pending_tail`` the same way but
        # must NEVER be streamed — it reaches the handler only via the
        # raised, classified exception (assistant_text capture above is
        # what carries the wording into the raise).
        if pending_tail:
            accumulated += pending_tail
            yield pending_tail
            pending_tail = ""

        # Defensive last resort: if NOTHING streamed and nothing was
        # flushed (no deltas, no batched blocks) fall back to the
        # result-event text so the caller's bubble isn't empty. With
        # the boundary flush above this now rarely fires.
        if not accumulated and result_text:
            yield result_text
            accumulated = result_text

        # Issue #56 — canonical final event, emitted LAST on the success
        # path. The persisted reply is the ``result`` event text (the
        # same string the buffered ``respond`` path returns), NOT the
        # concatenated live stream. Mid-turn narration the model batches
        # between tools is boundary-flushed above as live-progress
        # observability (the issue #49 feature) but must NOT survive into
        # the persisted message — sonnet-5 @ effort=low delivers a final-
        # segment working note ("… Now finalize the answer.") as a
        # buffered block that would otherwise land contiguous with the
        # real answer in ``done``. The handler prefers this event's text
        # and falls back to the accumulated stream only when it's absent
        # (older brains) or strips empty. Failure paths raised above and
        # emit no final event, so an API-error block never reaches here.
        if result_text.strip():
            yield {"type": "final", "text": result_text}

        # Cross-check (logged only — never raises). Useful for spotting
        # silent stream-json schema drift. A result-vs-accumulated
        # mismatch is now the EXPECTED shape whenever the model narrated
        # between tools (accumulated ⊇ result — the narration streamed
        # live but is excluded from the canonical result): it is no
        # longer drift evidence, just an observation the log records.
        result_clean = result_text.strip()
        accumulated_clean = accumulated.strip()
        if result_clean and accumulated_clean and result_clean != accumulated_clean:
            log.debug(
                "Brain.astream: result/delta mismatch for chat %d "
                "(result=%d chars, deltas=%d chars) — expected when the "
                "model narrated between tools",
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

    # ─── Background-subagent linger supervisor (issue #61) ───────
    #
    # When the streaming parse loop sees the terminal ``result`` event but
    # the CLI stays alive past the linger grace, the foreground turn is
    # done yet the process is holding open background subagents. Rather
    # than pin the drain + typing indicator for the full bg-wait ceiling,
    # ``_attempt_astream`` hands the process here and returns. The
    # supervisor keeps stdout/stderr draining (pipe backpressure would
    # otherwise stall the CLI), waits for the process up to the configured
    # ``brain.background_agent_wait`` measured from handoff, and SIGKILLs
    # the group on timeout — logging the outcome so a killed vs completed
    # background run is greppable from the daemon logs alone.

    def _handoff_lingering(
        self,
        chat_id: int,
        proc: asyncio.subprocess.Process,
        stream: asyncio.StreamReader,
        stderr_task: asyncio.Task | None,
    ) -> int:
        """Detach a lingering ``claude -p`` to a supervisor task.

        Returns the configured background-agent wait (seconds) so the
        caller can surface it in the ``background_lingering`` event AND
        drive the supervisor's own timeout from the same value.
        """
        wait_seconds = brain_background_agent_wait()
        existing = self._linger_supervisors.get(chat_id)
        if existing is not None and not existing.done():
            # Rare: the previous turn's background subagents are still
            # running when this turn ALSO lingers. Append-only session
            # JSONLs make concurrent writes tolerable (each claude -p owns
            # its own turn's writes; neither forks the other's session
            # state), so we don't kill the older one — its work may still
            # be useful. We just warn and let both supervisors run; each
            # removes only its own dict entry on completion.
            log.warning(
                "chat %d already has a lingering background supervisor; "
                "starting a second (each drains its own claude -p)",
                chat_id,
            )
        task = asyncio.create_task(
            self._supervise_lingering(
                chat_id, proc, stream, stderr_task, wait_seconds,
            ),
            name=f"vexis-bg-subagent-supervisor-{chat_id}",
        )
        self._linger_supervisors[chat_id] = task
        log.info(
            "Handed lingering claude -p (pid=%s) for chat %d to supervisor "
            "(background_agent_wait=%s)",
            proc.pid, chat_id,
            "unlimited" if wait_seconds == 0 else f"{wait_seconds}s",
        )
        return wait_seconds

    async def _supervise_lingering(
        self,
        chat_id: int,
        proc: asyncio.subprocess.Process,
        stream: asyncio.StreamReader,
        stderr_task: asyncio.Task | None,
        wait_seconds: int,
    ) -> None:
        """Drain + bound a handed-off lingering ``claude -p`` (issue #61).

        Keeps reading stdout so the CLI's pipe writes never block, awaits
        the process up to ``wait_seconds`` (``0`` = unlimited), and
        SIGKILLs the group on timeout. Logs the outcome (completed vs
        killed) at INFO. On task cancellation (daemon shutdown, see
        :meth:`cancel_lingering_supervisors`) the ``finally`` kills any
        still-running process so a clean shutdown leaves no orphan.
        """
        started = time.monotonic()

        async def _drain_stdout() -> None:
            try:
                while True:
                    chunk = await stream.readline()
                    if not chunk:
                        break
            except Exception:
                log.debug(
                    "linger stdout drain raised for chat %d", chat_id,
                    exc_info=True,
                )

        drain_task = asyncio.create_task(_drain_stdout())
        timeout = None if wait_seconds <= 0 else float(wait_seconds)
        status = "completed"
        try:
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                # Exceeded the configured wait — SIGTERM/SIGKILL the group.
                status = "killed"
                await self._kill_group(proc)
        except asyncio.CancelledError:
            # Daemon shutdown cancelled us mid-wait; the ``finally`` kills
            # the still-running process before we re-raise.
            status = "cancelled"
            raise
        finally:
            elapsed = time.monotonic() - started
            # Belt-and-suspenders: on the cancel path (and any exit that
            # somehow left the process alive) make sure no detached
            # claude -p survives. Idempotent — ``_kill_group`` returns
            # immediately when the process already exited.
            if proc.returncode is None:
                await self._kill_group(proc)
            if status == "completed":
                log.info(
                    "Background subagent(s) for chat %d finished; claude -p "
                    "(pid=%s) exited rc=%s after %.0fs",
                    chat_id, proc.pid, proc.returncode, elapsed,
                )
            else:
                log.info(
                    "Background subagent(s) for chat %d %s after %.0fs "
                    "(wait=%ss, pid=%s, rc=%s)",
                    chat_id, status, elapsed, wait_seconds, proc.pid,
                    proc.returncode,
                )
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.debug(
                    "linger drain cleanup raised for chat %d", chat_id,
                    exc_info=True,
                )
            if stderr_task is not None:
                await asyncio.gather(stderr_task, return_exceptions=True)
            # Remove only our own entry: a second supervisor for the same
            # chat (see _handoff_lingering) may have overwritten the dict
            # slot, and it will pop its own entry when it completes.
            if self._linger_supervisors.get(chat_id) is asyncio.current_task():
                self._linger_supervisors.pop(chat_id, None)

    async def cancel_lingering_supervisors(self) -> None:
        """Cancel every in-flight linger supervisor (issue #61).

        The explicit brain-close / daemon-shutdown hook: a clean
        shutdown shouldn't leave detached ``claude -p`` processes
        running. Cancelling each supervisor task interrupts its
        ``proc.wait()``; the supervisor's ``finally`` then SIGKILLs its
        still-running process. Best-effort and idempotent — safe to call
        with no supervisors in flight.
        """
        supervisors = list(self._linger_supervisors.values())
        for task in supervisors:
            task.cancel()
        for task in supervisors:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.debug(
                    "linger supervisor raised during shutdown cancel",
                    exc_info=True,
                )
        self._linger_supervisors.clear()

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
        allowed_tools: list[str] | None = None,
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

        ``allowed_tools`` is a defense-in-depth per-call allowlist
        (Issue #10). When non-None it overrides ``allow_tools``:

          - ``allowed_tools=[]`` → text-only spawn (no
            ``--allowedTools``, no bypass flag); a stray tool
            attempt fails loud rather than hanging on a permission
            prompt that headless mode can't answer.
          - ``allowed_tools=['Read', 'Grep']`` → emits
            ``--allowedTools Read Grep`` + ``--permission-mode
            bypassPermissions`` so the named tools run without a
            prompt. ``DISALLOWED_TOOLS`` continues to apply on top.

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
        # Issue #10 — defense-in-depth: explicit per-call allowlist
        # wins over the legacy boolean. Text-only mode (None+False
        # OR explicit []) emits neither flag; tool-using modes emit
        # the appropriate combination of --allowedTools (when an
        # explicit list is provided) and --permission-mode
        # bypassPermissions (always required in headless -p to avoid
        # an interactive prompt deadlock when ANY tool is allowed).
        if allowed_tools is not None:
            if allowed_tools:
                argv += ["--allowedTools", *allowed_tools]
                argv += ["--permission-mode", "bypassPermissions"]
            # explicit [] → text-only; no flags added. A stray tool
            # attempt fails loud because headless -p has no UI to
            # answer the resulting permission prompt.
        elif allow_tools:
            argv += ["--permission-mode", "bypassPermissions"]

        env = dict(os.environ)
        # Issue #61: seed the background-subagent wait ceiling from config
        # FIRST so an explicit ``env_overrides`` entry for the same var
        # (a caller that wants a per-spawn ceiling) still wins on update.
        _apply_bg_wait_env(env)
        if env_overrides:
            env.update(env_overrides)

        workdir = str(cwd if cwd is not None else self._workspace)

        # Per-subagent memory isolation (2026-06-12 freeze fix), same as
        # the foreground spawns: aux subsystems (curators, judges, kanban
        # workers) get their own memory-capped scope too. Verified that
        # subprocess.run's timeout (which SIGKILLs the systemd-run client)
        # still tears the scoped claude down — no orphan on BrainTimeout.
        scoped_argv = wrap_with_memory_scope(argv)

        def _run() -> AuxResult:
            try:
                cp = subprocess.run(
                    scoped_argv,
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

    # ─── Issue #11: conversation compression ─────────────────────

    async def compress_if_needed(self, session_id: str) -> bool:
        """Summarise the older half of the session JSONL when the
        configured threshold is crossed.

        Implementation outline:

          1. Walk the JSONL, collecting (role, text, original_line)
             tuples in chronological order for every conversational
             turn. Non-conversational lines (permission-mode,
             file-history-snapshot, queue-operation, attachment) are
             retained as ``preamble``/``epilogue`` segments so we
             never strip claude-code's bookkeeping out from under it.
          2. Build a :class:`CompressionInputs` with the live system
             prompt + an empty tool_schemas_text (claude-code surfaces
             tool schemas via subprocess flags, not strings we hold
             in memory — the conservative estimate is fine because
             the threshold ratio is already an 80% safety margin).
          3. If :func:`should_compress` says no, log and return False.
          4. Compute a :class:`ReplacementPlan` (handles iterative
             summaries — a session whose first conversational turn
             is a SUMMARY_PREFIX message folds the previous summary
             into the new one).
          5. Render the summariser prompt, call
             :meth:`spawn_aux(model_tier=subsystem_tier("compressor"))`
             with text-only tool allowlist (``allowed_tools=[]``) and
             ``VEXIS_COMPRESSOR=1`` for audit logs.
          6. On a clean stdout, atomically rewrite the JSONL:
             ``preamble`` + synthetic SUMMARY_PREFIX user turn +
             verbatim copy of the protected-tail original lines.

        Compression is a best-effort optimisation — any failure
        (summariser timed out, returned junk, JSONL parse failed,
        rename failed) logs and returns False rather than raising.
        The next turn runs without compression; the handler tries
        again on the following turn.

        Recursion-guard invariant: the synthetic summary message
        starts with :data:`~vexis_agent.core.brain.compressor.SUMMARY_PREFIX`
        which does NOT overlap any of the recursion-guard prefixes,
        so a compressed foreground transcript still passes the
        curator's content-prefix filter (which is the right answer
        — we WANT the curator to be able to review compressed
        sessions for lessons).
        """
        from vexis_agent.core.brain.compressor import (
            CompressionInputs,
            build_first_compaction_prompt,
            build_iterative_compaction_prompt,
            plan_replacement,
            serialize_messages_for_summary,
            should_compress,
            wrap_with_summary_prefix,
        )
        from vexis_agent.core.transcripts import claude_session_jsonl_dir
        from vexis_agent.core.yaml_config import (
            compression_enabled,
            compression_protect_last_n_turns,
            compression_threshold_ratio,
            compression_threshold_turns,
            subsystem_reasoning,
            subsystem_tier,
        )

        if not compression_enabled():
            return False

        jsonl_path = (
            claude_session_jsonl_dir(self._workspace) / f"{session_id}.jsonl"
        )
        if not jsonl_path.is_file():
            return False

        # Step 1: parse the JSONL into ordered segments. We keep the
        # raw line bytes for the protected tail so the byte-for-byte
        # invariant survives the rewrite — pull-and-rewrite via the
        # flattened TranscriptMessage shape would lose tool-call
        # blocks and metadata.
        parsed = await asyncio.to_thread(_parse_jsonl_for_compression, jsonl_path)
        if parsed is None:
            return False
        preamble_lines, message_records, epilogue_lines = parsed
        if not message_records:
            return False

        messages = [(rec.role, rec.text) for rec in message_records]

        # Step 2: trigger decision.
        system_prompt = self._system_prompt_for(session_id)
        inputs = CompressionInputs(
            messages=messages,
            system_prompt=system_prompt,
            tool_schemas_text="",  # claude-code surfaces tools via CLI flags
            context_window_tokens=None,
            threshold_ratio=compression_threshold_ratio(),
            threshold_turns=compression_threshold_turns(),
        )
        decision = should_compress(inputs)
        if not decision.compress:
            log.debug(
                "compress_if_needed(claude-code, %s): %s",
                session_id, decision.reason,
            )
            return False
        log.info(
            "compress_if_needed(claude-code, %s): triggering — %s",
            session_id, decision.reason,
        )

        # Step 3: plan the replacement.
        protect = compression_protect_last_n_turns()
        plan = plan_replacement(messages, protect_last_n_turns=protect)
        if not plan.messages_to_summarise:
            log.debug(
                "compress_if_needed(claude-code, %s): nothing to summarise "
                "(protected tail of %d already covers all turns)",
                session_id, protect,
            )
            return False

        # Step 4: build the summariser prompt + spawn the aux call.
        new_block = serialize_messages_for_summary(plan.messages_to_summarise)
        if plan.previous_summary is not None:
            prompt = build_iterative_compaction_prompt(
                plan.previous_summary, new_block,
            )
        else:
            prompt = build_first_compaction_prompt(new_block)

        try:
            result = await self.spawn_aux(
                prompt,
                model_tier=subsystem_tier("compressor"),
                # Effort defers to the CLI default unless the deployment
                # pins one via the dict-shaped compressor subsystem config;
                # summarisation is a bounded lookup-and-condense job, so a
                # low effort is the natural knob to reach for here.
                reasoning_level=subsystem_reasoning("compressor"),
                # 180s ceiling — summariser output is bounded by the
                # template (~10 sections); a multi-minute spawn is
                # almost certainly stuck rather than productive.
                timeout_seconds=180.0,
                # Forensic marker so audit logs / curator scans can
                # tell vexis-spawned compressions apart. The content-
                # prefix check on the resulting JSONL is the canonical
                # filter; this env var is for `ps` and logging.
                env_overrides={"VEXIS_COMPRESSOR": "1"},
                # Defense in depth: the summariser writes prose, not
                # tool calls. An explicit text-only allowlist makes a
                # poisoned transcript that tries to coax the summariser
                # into running Bash fail loud instead of execute.
                allowed_tools=[],
                cwd=self._workspace,
                subsystem="compressor",
            )
        except Exception as exc:
            log.warning(
                "compress_if_needed(claude-code, %s): spawn_aux failed: %s",
                session_id, exc,
            )
            return False
        if result.returncode != 0:
            log.warning(
                "compress_if_needed(claude-code, %s): summariser exited %d "
                "(stderr=%r)",
                session_id, result.returncode,
                (result.stderr or "")[:200],
            )
            return False
        summary_body = (result.stdout or "").strip()
        if not summary_body:
            log.warning(
                "compress_if_needed(claude-code, %s): summariser returned empty body",
                session_id,
            )
            return False

        synthetic_user_text = wrap_with_summary_prefix(summary_body)

        # Step 5: atomically rewrite the JSONL.
        try:
            await asyncio.to_thread(
                _rewrite_jsonl_with_summary,
                jsonl_path,
                session_id,
                preamble_lines,
                synthetic_user_text,
                message_records,
                plan.protected_tail_indices,
                epilogue_lines,
                self._workspace,
            )
        except Exception:
            log.exception(
                "compress_if_needed(claude-code, %s): JSONL rewrite failed",
                session_id,
            )
            return False
        log.info(
            "compress_if_needed(claude-code, %s): rewrote transcript "
            "(%d summarised → 1 summary turn + %d protected)",
            session_id,
            len(plan.messages_to_summarise),
            len(plan.protected_tail),
        )
        return True

    def write_mcp_config(self, servers: list[McpServerSpec]) -> Path:
        """Write claude-code's MCP server config to
        ``<workspace>/.mcp.json``.

        Phase C Day 6: replaces the pre-Day-6 NotImplementedError
        with the real writer. The format is claude-code's native
        ``mcpServers`` shape:

            {"mcpServers": {<name>: <native entry>}}

        Each entry is serialised by ``mcp_spec_to_claude_code_entry``
        (shared with the wizard mirror writer): a local stdio server
        becomes ``{command, args, env}``; a remote HTTP server
        becomes ``{type, url, headers}``.

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
        servers_dict: dict = {
            spec.name: mcp_spec_to_claude_code_entry(spec)
            for spec in servers
        }
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

    # ─── Issue #9: file-mutation verifier footer plumbing ────────

    async def _maybe_take_snapshot(self):
        """Pre-turn workspace snapshot used by the file-mutation
        verifier footer.

        Returns ``None`` when the feature is disabled in config
        (``brain.file_mutation_footer: false``) — the matching
        ``_record_files_changed`` call short-circuits on ``None``,
        so disabling the feature truly skips both walks for ~zero
        overhead.

        Snapshot work is CPU-bound (one ``stat`` per file plus
        directory iteration); offload to a worker thread so we
        don't stall the event loop on a slow disk.
        """
        if not brain_file_mutation_footer_enabled():
            return None
        try:
            return await asyncio.to_thread(_take_snapshot, self._workspace)
        except Exception:  # pragma: no cover - defensive
            log.exception(
                "workspace snapshot (pre-turn) failed for chat workspace %s",
                self._workspace,
            )
            return None

    async def _record_files_changed(
        self, chat_id: int, before_snapshot,
    ) -> None:
        """Diff the post-turn workspace state against ``before_snapshot``
        and stash the result for :meth:`consume_files_changed`.

        Runs in the ``finally`` of ``respond`` / ``astream`` so we
        capture mutations even on cancellation, timeout, or transient-
        exhaustion failure paths. A snapshot that fails (None passed
        in, or the after-walk errors) collapses to "no diff recorded"
        rather than poisoning the buffer with a stale entry.
        """
        if before_snapshot is None:
            return
        try:
            after_snapshot = await asyncio.to_thread(
                _take_snapshot, self._workspace,
            )
        except Exception:  # pragma: no cover - defensive
            log.exception(
                "workspace snapshot (post-turn) failed for chat %d", chat_id,
            )
            return
        try:
            changed = _snapshot_diff(before_snapshot, after_snapshot)
        except Exception:  # pragma: no cover - defensive
            log.exception(
                "workspace snapshot diff failed for chat %d", chat_id,
            )
            return
        if changed:
            log.info(
                "chat %d: %d file(s) mutated this turn (first 5: %s)",
                chat_id, len(changed), changed[:5],
            )
        # Always overwrite — each turn's diff supersedes the previous
        # one even if the next handler turn hasn't consumed it. The
        # alternative (merge) would let a turn with no mutations
        # *clear* a stale diff but accumulate diffs across consumer
        # gaps, which surfaces the wrong "previous turn" to the user.
        self._files_changed_by_chat[chat_id] = changed

    def consume_files_changed(self, chat_id: int) -> list[str]:
        """Pop the most recent turn's file-mutation list for
        ``chat_id``. Returns ``[]`` when no turn has run or the
        previous reader already drained the buffer.

        Drain semantics keep the verifier footer "per turn": once
        the handler injects it onto the next turn's prompt, a
        subsequent reader (the goal judge) gets a separate call
        and a separate buffer if it needs the same diff — currently
        both the handler and the goal hook fire from the same drain
        iteration so one drain suffices, but the contract leaves
        room for future readers via :meth:`peek_files_changed`.
        """
        return self._files_changed_by_chat.pop(chat_id, [])

    def peek_files_changed(self, chat_id: int) -> list[str]:
        """Non-draining read of the same buffer. Used by the goal
        judge hook in ``transports/telegram.py`` so the judge sees
        the same file-mutation summary the handler injected on the
        next user message — without racing the handler's consume."""
        return list(self._files_changed_by_chat.get(chat_id, []))


# ──────────────────────────────────────────────────────────────────
# Issue #11 — JSONL parse + atomic rewrite helpers
#
# Kept at module level so the conversation-compressor module can be
# exercised in unit tests without needing a full ClaudeCodeBrain
# subprocess. The per-brain method on the class above is a thin
# orchestrator over these.
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _CompressionMsgRecord:
    """One conversational turn's contribution to the compressor's
    flat representation.

    ``line_index``: 0-based offset into the JSONL line list — used
    by the rewrite helper to copy protected-tail lines byte-for-byte.
    ``role`` / ``text``: flattened shape the compressor's prompt
    builder consumes.
    """

    line_index: int
    role: str
    text: str


def _parse_jsonl_for_compression(
    jsonl_path: Path,
) -> tuple[list[str], list[_CompressionMsgRecord], list[str]] | None:
    """Walk a claude-code JSONL and split it into the three segments
    the compressor cares about.

    Returns ``(preamble_lines, message_records, epilogue_lines)`` where:

    - ``preamble_lines``: every non-conversational JSONL line that
      preceded the FIRST conversational turn. Things like
      ``permission-mode``, the initial ``file-history-snapshot``
      — claude-code reads these on resume, dropping them would
      break the session.
    - ``message_records``: one record per ``user`` / ``assistant``
      turn (sidechain excluded), in chronological order. Each
      carries the 0-based ``line_index`` into the raw line list so
      protected-tail lines can be copied byte-for-byte by the
      rewriter.
    - ``epilogue_lines``: any trailing non-conversational lines
      after the LAST conversational turn (``stop_hook_summary``,
      ``last-prompt`` metadata). These come AFTER the protected
      tail in the rewrite, preserving order.

    Returns ``None`` on read error so the caller can bail without
    rewriting. The "no work to do" answer is ``([], [], [])`` —
    a JSONL that exists but has no parseable lines.

    Sidechain lines (``isSidechain: true``) are preserved in the
    epilogue tail so subagent-thread metadata is never lost.
    Non-conversational lines INTERLEAVED with conversational lines
    (rare — claude-code typically emits all metadata up front)
    are emitted as part of the surrounding conversational segment:
    we attach them to the most recent message's segment so a
    ``permission-mode`` flip mid-conversation rides with the turn
    it relates to.
    """
    try:
        raw_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        log.debug("compressor: could not read %s: %s", jsonl_path, exc)
        return None

    preamble: list[str] = []
    message_records: list[_CompressionMsgRecord] = []
    epilogue: list[str] = []
    first_conv_idx: int | None = None
    last_conv_idx: int | None = None

    # First pass: locate conversational lines and extract their text.
    # Sidechain lines AND non-conversational metadata stay in
    # preamble/epilogue around the conversational window.
    conv_indices: list[int] = []
    parsed_by_idx: dict[int, dict] = {}
    for idx, raw in enumerate(raw_lines):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") not in ("user", "assistant"):
            continue
        if obj.get("isSidechain") is True:
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        conv_indices.append(idx)
        parsed_by_idx[idx] = obj
        if first_conv_idx is None:
            first_conv_idx = idx
        last_conv_idx = idx

    if not conv_indices:
        # No conversational content — nothing to compress.
        return [], [], list(raw_lines)

    # Build the message records, flattening the message content the
    # same way ``core.transcripts._flatten_content`` does.
    for idx in conv_indices:
        obj = parsed_by_idx[idx]
        msg = obj.get("message", {})
        role = str(msg.get("role") or obj.get("type") or "")
        content = msg.get("content")
        text = _flatten_content_for_compressor(content)
        message_records.append(
            _CompressionMsgRecord(
                line_index=idx, role=role, text=text,
            )
        )

    # Preamble: every line BEFORE the first conversational line.
    # Epilogue: every line AFTER the last conversational line.
    # Lines between the first and last conversational line stay
    # interleaved in the raw_lines slice that the rewriter copies
    # — they ride with the messages around them.
    assert first_conv_idx is not None and last_conv_idx is not None
    preamble = [raw_lines[i] for i in range(first_conv_idx)]
    epilogue = [
        raw_lines[i] for i in range(last_conv_idx + 1, len(raw_lines))
    ]
    return preamble, message_records, epilogue


def _flatten_content_for_compressor(content: object) -> str:
    """Same shape as :func:`core.transcripts._flatten_content` but
    inlined so the compressor doesn't have to import transcripts (which
    would tighten the brain-isolation invariant a notch too far —
    transcripts.py is allowed to be claude-code-specific)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            t = block.get("text")
            if isinstance(t, str):
                parts.append(t)
    return "\n".join(parts)


def _build_synthetic_summary_jsonl_line(
    session_id: str,
    workspace: Path,
    summary_text: str,
) -> str:
    """Render the synthetic user-turn JSONL line for the rewrite.

    Shape matches the user-turn lines claude-code itself writes
    (see the sample in ``docs/compression.md``). The fields we
    can supply deterministically (uuid, timestamp, sessionId, cwd,
    type, isSidechain, parentUuid, message) are populated; the
    rest are omitted — claude-code tolerates missing optional
    fields on resume.

    ``message.content`` is a plain string (not a list of content
    blocks) so flatteners that look at ``[role=user, content=str]``
    pick it up uniformly. ``promptId`` matches ``uuid`` for
    self-consistency.
    """
    msg_uuid = str(_uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = {
        "parentUuid": None,
        "isSidechain": False,
        "promptId": msg_uuid,
        "type": "user",
        "message": {"role": "user", "content": summary_text},
        "uuid": msg_uuid,
        "timestamp": now,
        "userType": "vexis-compressor",
        "cwd": str(workspace),
        "sessionId": session_id,
    }
    return json.dumps(record, ensure_ascii=False)


def _rewrite_jsonl_with_summary(
    jsonl_path: Path,
    session_id: str,
    preamble_lines: list[str],
    synthetic_user_text: str,
    message_records: list[_CompressionMsgRecord],
    protected_tail_indices: list[int],
    epilogue_lines: list[str],
    workspace: Path,
) -> None:
    """Atomically replace ``jsonl_path`` with: ``preamble`` +
    synthetic SUMMARY user turn + verbatim lines of the protected
    tail + ``epilogue``.

    The protected-tail lines come from the ORIGINAL JSONL by index
    so the byte-for-byte preservation invariant survives the
    rewrite. Tempfile + rename keeps the swap atomic — a crash
    mid-rewrite leaves the original JSONL untouched.

    ``protected_tail_indices`` is the list of indices into
    ``message_records`` (NOT into the raw JSONL) that the
    compressor decided to keep. Each record's ``line_index`` then
    locates the raw JSONL line to copy. We re-read the JSONL once
    to avoid carrying the whole line list across the
    asyncio.to_thread boundary.
    """
    raw_lines = jsonl_path.read_text(encoding="utf-8").splitlines()

    out_lines: list[str] = []
    out_lines.extend(preamble_lines)

    summary_line = _build_synthetic_summary_jsonl_line(
        session_id, workspace, synthetic_user_text,
    )
    out_lines.append(summary_line)

    # Copy protected tail lines verbatim, preserving order.
    protected_line_indices = [
        message_records[i].line_index for i in protected_tail_indices
    ]
    if protected_line_indices:
        # The slice from first-protected-line to last-protected-line
        # preserves any interleaved non-conversational lines that
        # belong with them (queue-operation, permission-mode flips,
        # etc.). The original claude-code JSONL keeps those in
        # chronological order; the rewriter must too.
        start = min(protected_line_indices)
        end = max(protected_line_indices)
        for i in range(start, end + 1):
            out_lines.append(raw_lines[i])

    out_lines.extend(epilogue_lines)

    # Atomic swap. The ``.compressing`` suffix is distinctive so a
    # `ls` after a crash makes the abandoned tempfile obvious for
    # cleanup. ``Path.replace`` is the POSIX rename — atomic on the
    # same filesystem.
    tmp_path = jsonl_path.with_suffix(jsonl_path.suffix + ".compressing")
    tmp_path.write_text(
        "\n".join(out_lines) + "\n", encoding="utf-8",
    )
    tmp_path.replace(jsonl_path)
