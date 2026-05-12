"""Subprocess wrapper around the OpenCode CLI (``opencode run``).

Phase C of the brain abstraction (.plans/brain-abstraction-research.md
§5 Days 3-4). Provides ``OpenCodeBrain`` — a sibling implementation
of ``ClaudeCodeBrain`` against the OpenCode binary. Day 3 shipped
foreground turns + aux spawns + healthcheck + MCP config writer
with empty-stub transcript readback; Day 4 lands session resume +
SessionLost recovery + SQLite transcript reader against
``~/.local/share/opencode/opencode.db``.

Session model. OpenCode generates session ids itself (format
``ses_<base32>``) — the brain doesn't get to pick. Vexis's
``SessionStore`` carries an opaque token; for OpenCode the brain
harvests the real id from the first ``sessionID`` field of the
JSON event stream and writes it back via ``SessionStore.set``.
Subsequent ``respond()`` calls pass ``--session <id>`` to resume.
NOT ``--continue`` — that picks up the newest top-level session
in the project directory which could be a session belonging to
another tool (``opencode tui``, manual ``opencode run`` from a
terminal). Pinning by id keeps vexis's resumes in vexis's lane.

SessionLost detection. When ``--session <stored_id>`` references
an id that no longer exists in ``opencode.db`` (DB pruning, manual
``DELETE``, or a fresh install on the same workspace), opencode
exits 1 with stderr ``"Session not found"`` and the run never
streams JSON events. The brain catches that, calls ``rotate_session``
to clear the dead id, and raises ``SessionLost``. The transport
layer's existing recovery path takes over — same machinery
``ClaudeCodeBrain`` already uses for the equivalent claude-code
case.

Transcript readback. ``opencode.db`` is a SQLite store with
``session``, ``message``, ``part`` tables. Each session row pins
to a project ``directory``; messages and parts join by
``session_id``. Vexis's reader opens the DB read-only
(``mode=ro`` URI + ``PRAGMA query_only=1`` belt-and-braces) and
flattens the schema into ``TranscriptMessage`` objects matching
the claude-code shape (role, text, timestamp, uuid, tool_calls,
raw). Concurrency: the curator scan can overlap a foreground
turn (which holds a write transaction); ``SQLITE_BUSY`` triggers
a 5-attempt × 100 ms backoff before giving up and skipping the
session this tick (per §8 risk #3 of the research doc).

System-prompt injection. OpenCode's ``run`` command does NOT accept
``--append-system-prompt`` (claude-code's flag). Three production
paths exist for system prompts; vexis picks ``OPENCODE_CONFIG_CONTENT``
per the audit-revised research doc §4 ("BrainOpenCode system-prompt
injection"). Each spawn serialises a JSON config blob containing an
``agent: { vexis: { prompt, model } }`` definition; OpenCode merges
this with the on-disk config (the persistent ``mcp:`` block written
by ``write_mcp_config``) at load time. Per-spawn isolation, no
shared file state, aux-spawn-concurrency safe.

MCP config wiring. Vexis's MCP servers (codemux, omarchy-kb, etc.)
are written to ``<workspace>/opencode.json`` under the ``mcp:``
block, namespaced with a ``vexis-`` prefix. The writer reads any
existing file, splits the ``mcp:`` block into vexis-prefixed and
user-owned halves, replaces the prefixed half with the new server
list, and re-emits the user-owned half byte-for-byte. Round-trip
invariant pinned by ``tests/test_brain_opencode_scaffold.py``.

Skill convention overlap. OpenCode auto-discovers
``<workspace>/skills/**/SKILL.md`` natively (verified at
``~/projects/_references/anomalyco-opencode/packages/opencode/src/skill/index.ts:24``)
and emits its own ``<available_skills>`` block in the system
prompt. To avoid double-injection, ``BrainOpenCode.build_system_prompt``
omits vexis's skill index — see §2 of the research doc.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import time
from collections.abc import Iterator
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
    BrainTimeoutError,
    McpServerSpec,
    SessionLost,
)
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore
from vexis_agent.core.status import StatusFile, extract_tool_target
from vexis_agent.core.transcripts import SessionMeta, TranscriptMessage

log = logging.getLogger(__name__)

# 30 min — same ceiling as ClaudeCodeBrain so user-facing turn
# semantics stay aligned across brains.
BRAIN_TIMEOUT_SECONDS = 1800

# Match ClaudeCodeBrain — full-page screenshots top out around 4 MB
# base64 and a single tool result can carry one. 32 MiB covers the
# realistic ceiling without breaking ``readline``.
_BRAIN_STREAM_LIMIT_BYTES = 32 * 1024 * 1024

# Vexis's namespace for MCP server entries inside the user's
# ``opencode.json``. Only entries whose key starts with this prefix
# get rewritten by ``write_mcp_config``. Anything the user added
# under a different key is preserved byte-for-byte.
VEXIS_MCP_PREFIX = "vexis-"

# Agent name that vexis spawns under inside OpenCode. The agent
# definition is injected per-spawn via ``OPENCODE_CONFIG_CONTENT``;
# the on-disk ``opencode.json`` may also carry a static agent of
# the same name, but env-var values win in OpenCode's merge order
# (verified at ``packages/opencode/src/config/config.ts:514``).
VEXIS_AGENT_NAME = "vexis"

# Aux spawn agent — same agent definition shape but with
# ``allow_tools`` honoured per-call rather than baked into the
# agent's permission ruleset. Distinct from the foreground agent
# so a future debug-skin can override it without affecting the
# foreground turn. Day 3 uses the same name; Day 4 may split.
VEXIS_AUX_AGENT_NAME = "vexis-aux"

# Default location for opencode's SQLite store on Linux. Verified
# against the running install at startup; tests override via
# ``_OPENCODE_DB_PATH_OVERRIDE`` set by the
# ``opencode_db_path_override`` test fixture so the SQL reader can
# point at a tmp-built DB instead of the user's real one.
_DEFAULT_OPENCODE_DB_PATH = (
    Path.home() / ".local" / "share" / "opencode" / "opencode.db"
)
_OPENCODE_DB_PATH_OVERRIDE: Path | None = None


def opencode_db_path() -> Path:
    """Resolve the opencode SQLite path. Production: the default
    XDG location. Tests: whatever ``set_opencode_db_path_override``
    last wrote (cleared in the autouse fixture)."""
    return _OPENCODE_DB_PATH_OVERRIDE or _DEFAULT_OPENCODE_DB_PATH


def set_opencode_db_path_override(path: Path | None) -> None:
    """Test hook. Set to None to revert to the default location.
    Used by ``tests/test_brain_opencode_transcripts.py`` to point
    the reader at a hand-built tmp DB."""
    global _OPENCODE_DB_PATH_OVERRIDE
    _OPENCODE_DB_PATH_OVERRIDE = path


# SQLITE_BUSY retry. Per §8 risk #3 of the research doc: opencode
# holds a write transaction open for the duration of a foreground
# turn, so a curator scan that overlaps will hit SQLITE_BUSY most
# of the time. 5 attempts × 100 ms = 500 ms tail-latency ceiling
# per query before we give up and skip the session this tick (the
# next curator tick will retry).
_SQLITE_RETRIES = 5
_SQLITE_RETRY_BACKOFF_S = 0.1


def _run_db_query(
    sql: str, params: tuple = (),
) -> list[tuple] | None:
    """Run one SELECT against opencode.db with read-only safety and
    SQLITE_BUSY backoff. Returns the row list on success, ``None``
    when:
      - the DB file doesn't exist (fresh OpenCode install with no
        sessions yet),
      - the DB is persistently locked after the retry budget,
      - any other ``OperationalError`` occurs.

    Belt-and-braces against accidental writes:
      - URI ``mode=ro`` — SQLite refuses any write at the engine
        level.
      - ``PRAGMA query_only = 1`` — defends against a cosmic-ray
        case where the URI flag was somehow misset.

    Each call opens and closes its own connection so the reader
    cannot accidentally hold a long-lived FD against the user's
    DB. Cheap on SQLite — open is ~microseconds.
    """
    db_path = opencode_db_path()
    if not db_path.exists():
        return None
    last_exc: Exception | None = None
    for attempt in range(_SQLITE_RETRIES):
        try:
            conn = sqlite3.connect(
                f"file:{db_path}?mode=ro", uri=True
            )
            try:
                conn.execute("PRAGMA query_only = 1")
                cur = conn.execute(sql, params)
                return cur.fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "busy" in msg or "locked" in msg:
                last_exc = exc
                if attempt < _SQLITE_RETRIES - 1:
                    time.sleep(_SQLITE_RETRY_BACKOFF_S)
                    continue
            log.warning(
                "opencode.db query failed (sql=%s): %s", sql.split()[0], exc
            )
            return None
    log.warning(
        "opencode.db persistently busy after %d attempts; "
        "skipping query (last: %s)",
        _SQLITE_RETRIES, last_exc,
    )
    return None


def _read_markdown(path: Path) -> str | None:
    """Read a UTF-8 markdown file. Missing → None. Mirrors
    ``ClaudeCodeBrain._read_markdown``."""
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


# Repo root: this file lives at vexis_agent/core/brain/opencode.py after
# the Phase 2 restructure; four `.parent`s reach the source-checkout
# repo root that holds CAPABILITIES.md.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _build_system_prompt_for_workspace(workspace: Path) -> str:
    """Compose vexis's system prompt for OpenCode.

    Layered like ``core.brain.claude_code.build_system_prompt`` —
    SOUL.md (or default), CAPABILITIES.md, MEMORY.md block,
    USER.md block, RELATIONSHIPS.md block — but DROPS the
    ``<available_skills>`` index because OpenCode auto-discovers
    skills under ``<workspace>/skills/**/SKILL.md`` and emits its
    own block. Without this drop, the model would see the same
    skill enumerated twice.

    Verified by source-read at
    ``~/projects/_references/anomalyco-opencode/packages/opencode/src/skill/index.ts:24``
    (``EXTERNAL_SKILL_PATTERN = "skills/**/SKILL.md"``) and
    ``:271-296`` (``fmt(list)`` — same ``<available_skills>`` /
    ``<skill>`` shape claude-code-side vexis emits).
    """
    # Lazy-import to avoid loading the relationships package at
    # module-import time (mirrors the claude-code build).
    from vexis_agent.core.brain.claude_code import DEFAULT_SOUL
    from vexis_agent.core.memory import MemoryStore
    from vexis_agent.core.paths import memories_dir
    from vexis_agent.core.relationships.store import (
        format_relationships_for_system_prompt,
    )

    # CAPABILITIES.md ships as package data — see brain/claude_code.py
    # for the matching read site; both adapters use the same source.
    from vexis_agent.data import read_capabilities

    soul = _read_markdown(workspace / "SOUL.md") or DEFAULT_SOUL
    capabilities = read_capabilities()
    parts: list[str] = [soul]
    if capabilities:
        parts.append(capabilities)

    # agent-platform-style skill self-authoring guidance — same call site
    # claude-code uses. opencode emits its own ``<available_skills>``
    # block downstream, so we still need this authoring block here:
    # opencode's auto-discovery only tells the brain WHICH skills
    # exist; it doesn't tell the brain WHEN to create or patch one.
    from vexis_agent.core.skills import build_skill_authoring_block

    parts.append(build_skill_authoring_block())

    memory_store = MemoryStore(memories_dir(workspace))
    mem_block = memory_store.format_for_system_prompt("memory")
    if mem_block:
        parts.append(mem_block)
    user_block = memory_store.format_for_system_prompt("user")
    if user_block:
        parts.append(user_block)

    relationships_block = format_relationships_for_system_prompt(workspace)
    if relationships_block:
        parts.append(relationships_block)

    # NB: skills index intentionally omitted — OpenCode injects
    # its own. See module docstring + §2 of the research doc.

    return "\n\n".join(parts)


# ──────────────────────────────────────────────────────────────────
# OpenCode JSON event parser
# ──────────────────────────────────────────────────────────────────


_SESSION_NOT_FOUND_RE = "session not found"


@dataclass(frozen=True)
class _StreamResult:
    """Outcome of one ``_read_opencode_event_stream`` pass.

    - ``final_text``: concatenated text-event payloads (the
      assistant's reply body).
    - ``harvested_session_id``: the ``sessionID`` we locked onto
      (caller-supplied for resumes, harvested-from-first-event
      for fresh spawns). ``None`` when the stream produced no
      events at all.
    - ``session_lost_via_event``: True when an ``error``-shaped
      event arrived whose typed payload matches the
      session-not-found marker. Belt-and-braces alongside the
      stderr substring path that ``respond`` already covers —
      catches the case where opencode emits the typed event
      mid-stream (e.g. the session was deleted between the spawn
      and our second message) BEFORE the process exits 1.
    - ``saw_any_event``: False ⇒ stream EOF without any parseable
      JSON event ⇒ ``respond`` raises BrainError with a
      diagnostic stderr message rather than silently returning
      "" (which would look like a clean turn that produced
      empty text — masking real failures).
    """

    final_text: str
    harvested_session_id: str | None
    session_lost_via_event: bool
    saw_any_event: bool


async def _read_opencode_event_stream(
    stream: asyncio.StreamReader | None,
    status_file: StatusFile,
    target_session_id: str | None,
) -> _StreamResult:
    """Consume ``opencode run --format json`` stdout, update the
    status file on tool events, accumulate the assistant's final
    text.

    OpenCode's JSON event shape (verified at
    ``~/projects/_references/anomalyco-opencode/packages/opencode/src/cli/cmd/run.ts:435``):

        {"type": "<type>", "timestamp": <ms>, "sessionID": "<id>", ...data}

    Event types we care about:

      - ``tool_use``: ``{part: ToolPart}`` — emitted on tool
        completion or error. Updates the status file.
      - ``text``: ``{part: {text: "..."}}`` — emitted when a text
        block's ``time.end`` is set. Concatenated into the final
        reply.
      - ``error``: ``{error: {name, data: {message}}}`` — typed
        error event. We inspect the ``name`` + ``data.message``
        for session-not-found markers and flag
        ``session_lost_via_event=True`` for ``respond`` to
        translate into a SessionLost rotation. Other errors are
        logged.
      - ``session.status`` with ``status.type == "idle"`` and
        matching ``sessionID`` — defensive break. In practice
        opencode's emit() doesn't write session.status events to
        stdout (only tool_use / text / error pass through); the
        terminator is stream EOF when the proc exits. Branch
        kept for forward-compat with future SDK changes.

    ``target_session_id`` is the id we're listening for. On the
    first call (no prior session), pass ``None``; we harvest the
    first event's ``sessionID`` and lock onto it. On resumes, pass
    the stored id so events from unrelated concurrent sessions
    (rare but possible) are ignored.
    """
    final_text_parts: list[str] = []
    locked_session_id: str | None = target_session_id
    session_lost = False
    saw_any_event = False

    if stream is None:
        return _StreamResult(
            final_text="",
            harvested_session_id=locked_session_id,
            session_lost_via_event=False,
            saw_any_event=False,
        )

    while True:
        try:
            line = await stream.readline()
        except asyncio.LimitOverrunError as exc:
            # A single JSON line bigger than 32 MiB — almost
            # certainly a bug on opencode's side (we don't write
            # multi-megabyte tool inputs). Log + break; better to
            # surface a real failure than truncate silently.
            log.error(
                "opencode emitted line bigger than %d-byte limit "
                "(consumed=%s); aborting stream read.",
                _BRAIN_STREAM_LIMIT_BYTES, exc.consumed,
            )
            break
        except Exception:
            log.warning("opencode stream readline raised", exc_info=True)
            break
        if not line:
            break
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # Partial / malformed JSON line. Opencode writes one
            # complete JSON object per line; a parse failure here
            # likely means buffer truncation or a bug we can't
            # recover from on this line. Skip and continue —
            # other events on the stream may still parse cleanly.
            continue
        if not isinstance(event, dict):
            continue
        saw_any_event = True

        evt_session = event.get("sessionID")
        if locked_session_id is None and isinstance(evt_session, str):
            locked_session_id = evt_session
        if (
            locked_session_id is not None
            and isinstance(evt_session, str)
            and evt_session != locked_session_id
        ):
            # Event from a different session — opencode can emit
            # cross-session bus events on the same stream. Ignore.
            continue

        kind = event.get("type")
        if kind == "tool_use":
            part = event.get("part") or {}
            if isinstance(part, dict):
                tool_name = str(part.get("tool") or "tool")
                state = part.get("state") or {}
                input_obj = (
                    state.get("input") if isinstance(state, dict) else None
                )
                target = extract_tool_target(
                    tool_name, input_obj if isinstance(input_obj, dict) else {}
                )
                status_file.record_tool(tool_name, target)
        elif kind == "text":
            part = event.get("part") or {}
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    final_text_parts.append(text)
        elif kind == "session.status":
            props = event.get("properties") or {}
            status = props.get("status") if isinstance(props, dict) else None
            if isinstance(status, dict) and status.get("type") == "idle":
                # Terminal break — defensive; in practice
                # opencode's stdout emit() doesn't write
                # session.status events (only tool_use / text /
                # error). Stream EOF is the real terminator.
                break
        elif kind == "error":
            err = event.get("error")
            log.warning("opencode stream error event: %r", err)
            if _is_session_not_found_error(err):
                session_lost = True
                # Don't break — let the stream drain naturally so
                # we capture any trailing text the model produced
                # before the error surfaced.

    return _StreamResult(
        final_text="".join(final_text_parts),
        harvested_session_id=locked_session_id,
        session_lost_via_event=session_lost,
        saw_any_event=saw_any_event,
    )


# Day 2 model UX: opencode bad-model detector. Mirrors
# ``_is_session_not_found_error`` shape — checks the typed error
# event the JSON event stream emits when ``--model`` references
# an unknown id. Verified empirically: opencode exits 0 even on
# bad model in ``--format json`` mode; the error event is the
# reliable signal.
_MODEL_NOT_FOUND_MESSAGE_MARKER = "model not found"


def _detect_model_not_found(stdout: str) -> bool:
    """Scan an opencode JSON event stream for a bad-model marker.

    Returns True when any line in ``stdout`` parses as a JSON
    object with ``type == "error"`` AND
    ``error.data.message`` containing ``"Model not found"``
    (case-insensitive). Defensive scan — malformed lines, non-dict
    events, and missing fields all skip without raising.

    Used by ``spawn_aux`` to raise ``BrainModelNotFoundError`` with
    the same suggested_fix copy the validator's rule 4 emits
    pre-write.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(evt, dict):
            continue
        if evt.get("type") != "error":
            continue
        err = evt.get("error")
        if not isinstance(err, dict):
            continue
        data = err.get("data")
        if not isinstance(data, dict):
            continue
        msg = data.get("message")
        if isinstance(msg, str) and _MODEL_NOT_FOUND_MESSAGE_MARKER in msg.lower():
            return True
    return False


def _is_session_not_found_error(err: object) -> bool:
    """Inspect an opencode ``error`` event payload for the
    session-not-found marker.

    Two surfaces:
      - ``error.name == "NotFoundError"`` — the typed error class
        opencode raises when ``--session <id>`` references a row
        that's no longer in the DB
        (``packages/opencode/src/session/projectors.ts:82``).
      - ``error.data.message`` containing "Session not found" —
        the human-facing text the SDK includes for that error.

    Either match returns True. Defensive against schema drift —
    if opencode renames the class or rewords the message, the
    other detector still catches it.
    """
    if not isinstance(err, dict):
        return False
    name = err.get("name")
    if isinstance(name, str) and name == "NotFoundError":
        # NotFoundError covers "session not found" AND "message
        # not found" — narrow via the message text.
        data = err.get("data")
        if isinstance(data, dict):
            msg = data.get("message")
            if isinstance(msg, str) and _SESSION_NOT_FOUND_RE in msg.lower():
                return True
    data = err.get("data")
    if isinstance(data, dict):
        msg = data.get("message")
        if isinstance(msg, str) and _SESSION_NOT_FOUND_RE in msg.lower():
            return True
    return False


# ──────────────────────────────────────────────────────────────────
# OPENCODE_CONFIG_CONTENT builder
# ──────────────────────────────────────────────────────────────────


def _build_opencode_config_content(
    *,
    agent_name: str,
    system_prompt: str,
    model: str | None,
    allow_tools: bool,
) -> str:
    """Serialise the per-spawn config blob.

    Shape:

        {
          "agent": {
            "<agent_name>": {
              "prompt": "<system prompt>",
              "model": "<provider/model>",   # only when set
              "permission": { ... }          # tool-permission ruleset
            }
          }
        }

    The persistent ``mcp:`` block is NOT included here — it lives
    in ``<workspace>/opencode.json`` written by
    ``write_mcp_config`` and merged with this env-var payload at
    OpenCode's config-load time.

    ``allow_tools=False`` (judges, extractors) sets a deny-by-
    default permission ruleset so the model can't accidentally use
    a tool without prompting (and since headless ``run`` mode
    can't prompt, the call fails loud rather than silently
    misbehaving). ``allow_tools=True`` (skill curator) leaves
    permissions open.
    """
    agent_def: dict = {"prompt": system_prompt}
    if model:
        agent_def["model"] = model
    if not allow_tools:
        # Deny everything by default; the prompt is text-only.
        # Mirrors the deny-rules in
        # ``packages/opencode/src/cli/cmd/run.ts:359-374`` (which
        # are the rules ``opencode run`` uses by default to make
        # headless sessions safe).
        agent_def["permission"] = {
            "edit": "deny",
            "write": "deny",
            "shell": "deny",
            "webfetch": "deny",
        }
    return json.dumps(
        {"agent": {agent_name: agent_def}},
        separators=(",", ":"),
    )


# ──────────────────────────────────────────────────────────────────
# OpenCodeBrain
# ──────────────────────────────────────────────────────────────────


class OpenCodeBrain(Brain):
    """Sibling of ``ClaudeCodeBrain`` against the ``opencode`` CLI.

    Constructor mirrors ``ClaudeCodeBrain`` for symmetry:
    ``workspace`` + ``session: SessionStore`` + ``running_tasks:
    RunningTasks``. The session token storage is the same — vexis
    holds the OpenCode-generated session id (harvested from the
    first event of each fresh respond call) in ``SessionStore``,
    just like a claude-code UUID.
    """

    def __init__(
        self,
        workspace: Path,
        session: SessionStore,
        running_tasks: RunningTasks,
    ) -> None:
        self._workspace = workspace
        self._session = session
        self._running_tasks = running_tasks
        # Per-session frozen system prompt — same prefix-cache
        # invariant ClaudeCodeBrain enforces. OpenCode caches
        # prompts per agent definition shape so byte-identical
        # ``OPENCODE_CONFIG_CONTENT`` hits the cache.
        self._system_prompt_cache: dict[str, str] = {}
        # Step 6.5: install the foreground-shell safety plugin into
        # the workspace before the first opencode run. The plugin
        # (vexis_agent/data/opencode_safety_plugin.mjs) gets copied
        # to <workspace>/.vexis-opencode-safety.mjs and registered
        # in opencode.json's plugin[] array. Idempotent + merge-
        # friendly — see vexis_agent.core.safety_install for the
        # contract. Failures are logged but don't raise: degraded
        # safety beats broken startup.
        from vexis_agent.core.safety_install import (
            ensure_opencode_safety_plugin,
        )

        ensure_opencode_safety_plugin(workspace)

    # ─── foreground turn ─────────────────────────────────────────

    async def respond(
        self,
        message: str,
        chat_id: int,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
    ) -> str:
        log.info(
            "OpenCodeBrain.respond starting for chat %d%s%s",
            chat_id,
            f" (model override: {model})" if model else "",
            f" (reasoning: {reasoning_level})" if reasoning_level else "",
        )

        # Phase C Day 4: ``is_initialized`` flips to True after the
        # first successful ``respond``, at which point ``self._session.get()``
        # returns the OpenCode-generated session id (harvested from
        # the first event of the original spawn and persisted via
        # ``SessionStore.set``). On subsequent calls we pass it as
        # ``--session <id>`` to resume; on the first call we pass
        # ``--title`` so opencode names the new session predictably
        # (which makes it identifiable in ``opencode tui`` and
        # avoids collision with sessions from other tools).
        is_initialized = self._session.is_initialized()
        stored_token = self._session.get() if is_initialized else None

        from vexis_agent.core.yaml_config import model_for_tier
        # Per-turn model override beats the config default. None
        # (the typical case for Telegram + text chat) falls through
        # to the brain's foreground choice via model_for_tier.
        model = model or model_for_tier("opencode", None)

        system_prompt = self._system_prompt_for(stored_token or "fresh")
        config_content = _build_opencode_config_content(
            agent_name=VEXIS_AGENT_NAME,
            system_prompt=system_prompt,
            model=model,
            allow_tools=True,  # foreground turn allows tools
        )

        argv = [
            "opencode", "run",
            "--format", "json",
            "--agent", VEXIS_AGENT_NAME,
            "--dangerously-skip-permissions",
        ]
        if stored_token:
            argv += ["--session", stored_token]
        else:
            argv += ["--title", f"vexis-chat-{chat_id}"]
        # Per-turn reasoning effort. opencode uses ``--variant`` for
        # per-call reasoning selection (mirrors spawn_aux). ``None``
        # means no flag, model uses its baked-in default.
        if reasoning_level:
            argv += ["--variant", reasoning_level]
        argv.append(message)

        env = {**os.environ, "VEXIS_CHAT_ID": str(chat_id)}
        env["OPENCODE_CONFIG_CONTENT"] = config_content

        log.debug(
            "Spawning opencode run (cwd=%s, agent=%s, resume=%s)",
            self._workspace, VEXIS_AGENT_NAME,
            stored_token if stored_token else "<fresh>",
        )

        reservation = await self._running_tasks.reserve(chat_id)
        status_file = StatusFile(chat_id)
        status_file.start()

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
            log.info("OpenCode spawned PID %d for chat %d", proc.pid, chat_id)

            attached = await self._running_tasks.attach(reservation, proc)
            if not attached:
                log.info(
                    "OpenCode raising BrainCancelled for chat %d "
                    "(cancel during reservation window)",
                    chat_id,
                )
                await self._kill_group(proc)
                raise BrainCancelled("opencode run cancelled via /cancel")

            stdout_task = asyncio.create_task(
                _read_opencode_event_stream(
                    proc.stdout,
                    status_file,
                    target_session_id=stored_token,
                )
            )
            stderr_task = asyncio.create_task(proc.stderr.read())

            try:
                await asyncio.wait_for(proc.wait(), timeout=BRAIN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                await self._kill_group(proc)
                await asyncio.gather(
                    stdout_task, stderr_task, return_exceptions=True
                )
                raise BrainTimeoutError(
                    f"opencode run timed out after {BRAIN_TIMEOUT_SECONDS}s"
                ) from exc

            try:
                stream_result = await stdout_task
            except Exception:
                log.exception(
                    "OpenCode stdout reader failed for chat %d", chat_id
                )
                stream_result = _StreamResult(
                    final_text="",
                    harvested_session_id=None,
                    session_lost_via_event=False,
                    saw_any_event=False,
                )
            try:
                stderr_bytes = await stderr_task
            except Exception:
                log.exception(
                    "OpenCode stderr reader failed for chat %d", chat_id
                )
                stderr_bytes = b""

            if self._running_tasks.was_cancelled(chat_id):
                log.info(
                    "OpenCode raising BrainCancelled for chat %d (proc killed)",
                    chat_id,
                )
                raise BrainCancelled("opencode run cancelled via /cancel")

            err_text = stderr_bytes.decode(errors="replace").strip()

            # SessionLost detection — two routes:
            #   1. Stream's typed ``error`` event matched the
            #      session-not-found marker
            #      (``_is_session_not_found_error``).
            #   2. Process exited 1 with "Session not found" on
            #      stderr (the path opencode takes when
            #      ``--session <dead_id>`` short-circuits before
            #      ever entering the event loop — verified at
            #      ``run.ts:632-634``).
            # Either rotates the session token + raises SessionLost
            # for the transport's existing recovery to retry on a
            # fresh session.
            stderr_session_marker = (
                proc.returncode != 0
                and "session not found" in err_text.lower()
            )
            if is_initialized and (
                stream_result.session_lost_via_event or stderr_session_marker
            ):
                old = stored_token
                new = self._session.rotate()
                log.warning(
                    "OpenCode lost session %s; rotated to %s "
                    "(stream_event=%s, stderr=%s)",
                    old, new,
                    stream_result.session_lost_via_event,
                    stderr_session_marker,
                )
                raise SessionLost(
                    "OpenCode session was lost. Rotated to new session."
                )

            if proc.returncode != 0:
                raise BrainError(
                    f"opencode run exited {proc.returncode}: "
                    f"{err_text or '(no stderr)'}"
                )

            # Clean exit but the event stream produced nothing —
            # opencode is supposed to emit at least one event per
            # turn (the assistant's text). Empty stream + 0 exit
            # is a degraded state: either a transient SDK glitch
            # or the model produced no output. Raise BrainError
            # with the stderr context so the transport surfaces
            # something actionable rather than echoing a blank
            # reply.
            if not stream_result.saw_any_event:
                raise BrainError(
                    "opencode run exited 0 but emitted no events; "
                    f"stderr: {err_text or '(empty)'}"
                )
        finally:
            status_file.delete()
            await self._running_tasks.unregister(chat_id)

        # First-call success path: harvest the OpenCode-generated
        # session id and persist it so the next ``respond()`` call
        # can resume via ``--session <id>``. ``mark_initialized``
        # flips the session_token() reading to True; subsequent
        # rotations (after SessionLost) clear both via ``rotate``.
        if not is_initialized:
            if stream_result.harvested_session_id:
                self._session.set(stream_result.harvested_session_id)
                log.info(
                    "OpenCode session established and persisted: %s",
                    stream_result.harvested_session_id,
                )
            else:
                log.warning(
                    "OpenCode reply succeeded but no sessionID was "
                    "harvested from the event stream. Subsequent "
                    "turns will start a new session — chat will "
                    "feel context-less."
                )
            self._session.mark_initialized()

        return (stream_result.final_text or "").strip()

    @staticmethod
    async def _kill_group(proc: asyncio.subprocess.Process) -> None:
        """Mirrors ``ClaudeCodeBrain._kill_group`` — same primitive
        works because OpenCode also spawns child processes (shell,
        MCP servers, formatters) and ``start_new_session=True`` at
        the parent puts them all under one process group."""
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
                log.error(
                    "opencode run (pid=%s) ignored SIGKILL", proc.pid
                )

    # ─── system prompt ───────────────────────────────────────────

    def _system_prompt_for(self, session_uuid: str) -> str:
        cached = self._system_prompt_cache.get(session_uuid)
        if cached is not None:
            return cached
        prompt = _build_system_prompt_for_workspace(self._workspace)
        # Cap mirrors ClaudeCodeBrain — 16 entries.
        if len(self._system_prompt_cache) >= 16:
            oldest = next(iter(self._system_prompt_cache))
            del self._system_prompt_cache[oldest]
        self._system_prompt_cache[session_uuid] = prompt
        return prompt

    def build_system_prompt(self) -> str:
        """ABC method. Returns the workspace-resolved prompt minus
        the ``<available_skills>`` block (OpenCode injects its own
        — see module docstring)."""
        return _build_system_prompt_for_workspace(self._workspace)

    # ─── aux spawn ───────────────────────────────────────────────

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
        """Run an aux call against ``opencode run``.

        Tier resolution via ``core.yaml_config.model_for_tier`` for
        ``"opencode"``. Builds ``OPENCODE_CONFIG_CONTENT`` carrying
        a stripped-down agent (no skills index, no MCP — just the
        aux prompt and the tier-resolved model). Sync subprocess
        wrapped in ``asyncio.to_thread`` for the async contract.
        """
        from vexis_agent.core.yaml_config import model_for_tier

        # Reasoning + context flags — added 2026-05-08 for the
        # picker's reasoning step. opencode's CLI accepts
        # ``--variant <name>`` (per-model variant names like
        # ``high``, ``max``, ``minimal`` come from the model's
        # ``variants`` block in ``opencode models --verbose``).
        # ``None`` → no flag, opencode picks the default. The
        # CLI errors cleanly on an unsupported variant/model
        # combination.
        argv: list[str] = [
            "opencode", "run",
            "--format", "json",
            "--agent", VEXIS_AUX_AGENT_NAME,
            "--dangerously-skip-permissions",
        ]
        if reasoning_level:
            argv += ["--variant", reasoning_level]
        # context_window: accepted for ABC stability but inert —
        # opencode's CLI has no runtime context flag (probe
        # 2026-05-08 against `opencode run --help`). Documented in
        # Brain.spawn_aux's docstring.
        _ = context_window
        argv.append(prompt)
        model = model_for_tier("opencode", model_tier)

        # Aux call has no system prompt of its own — the prompt is
        # the entire user message. The agent definition just pins
        # the model and the tool-permission policy.
        config_content = _build_opencode_config_content(
            agent_name=VEXIS_AUX_AGENT_NAME,
            system_prompt="",  # aux prompt is the user message
            model=model,
            allow_tools=allow_tools,
        )

        env = dict(os.environ)
        env["OPENCODE_CONFIG_CONTENT"] = config_content
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
                    f"opencode run aux call timed out after {timeout_seconds}s"
                ) from exc
            except FileNotFoundError as exc:
                raise BrainNotInstalled(
                    "`opencode` not on PATH; install OpenCode: "
                    "https://opencode.ai (curl -fsSL https://opencode.ai/install | bash)"
                ) from exc
            except OSError as exc:
                raise BrainError(
                    f"opencode run aux spawn failed: {exc}"
                ) from exc

            stdout = (cp.stdout or b"").decode("utf-8", errors="replace")
            stderr = (cp.stderr or b"").decode("utf-8", errors="replace")

            # Day 2 model UX: spawn-site backstop. opencode in
            # ``--format json`` mode exits 0 even on bad model;
            # the diagnostic surfaces as a typed error event in
            # the JSON stream:
            #   {"type":"error","error":{"name":"UnknownError",
            #    "data":{"message":"Model not found: <id>/."}}}
            # Verified empirically. Parse the event stream once
            # for the marker; on detection raise a structured
            # BrainModelNotFoundError with the same suggested_fix
            # copy the validator's rule 4 emits pre-write.
            if model and _detect_model_not_found(stdout):
                from vexis_agent.core.model_validator import (
                    OPENCODE_FORMAT_FIX_TEMPLATE,
                )
                raise BrainModelNotFoundError(
                    subsystem=subsystem or "<unknown>",
                    model_id=model,
                    brain_kind="opencode",
                    suggested_fix=OPENCODE_FORMAT_FIX_TEMPLATE.format(
                        model_id=model,
                        subsystem=subsystem or "this subsystem",
                    ),
                )

            # Aux callers consume ``stdout`` as the agent's reply
            # text. opencode --format json emits one JSON event
            # per line; we extract the concatenated ``text`` events
            # so the aux caller sees the same shape claude-code's
            # final-reply ``result`` event produced. Falls through
            # to the raw stdout when the JSON event stream is
            # malformed (better noisy than empty).
            extracted = _extract_text_from_event_stream(stdout)
            return AuxResult(
                stdout=extracted if extracted else stdout,
                stderr=stderr,
                returncode=cp.returncode,
            )

        return await asyncio.to_thread(_run)

    # ─── session model ───────────────────────────────────────────

    def session_token(self) -> str | None:
        """Returns the current opencode session id (format
        ``ses_<base32>``) once ``respond`` has harvested + persisted
        it, or the SessionStore-minted placeholder UUID before that
        first call. ``SessionStore`` doesn't distinguish — the token
        is opaque per the brain ABC contract."""
        return self._session.get()

    def rotate_session(self) -> str:
        """Mint a fresh placeholder. ``SessionStore.rotate`` flips
        ``initialized`` back to False so the next ``respond`` call
        spawns without ``--session`` and harvests a new id. Used by
        the SessionLost recovery path."""
        return self._session.rotate()

    # ─── transcript readback (real on Day 4) ─────────────────────

    def iter_session_metas(self) -> Iterator[SessionMeta]:
        """Enumerate sessions in ``opencode.db`` whose ``directory``
        column matches this brain's workspace.

        Issues one ``SELECT … LEFT JOIN message …`` to fetch session
        metadata + per-session message count in a single query;
        opens a fresh read-only connection (``mode=ro`` URI +
        ``PRAGMA query_only=1``). Returns ``SessionMeta`` objects
        with ``jsonl_path=None`` to flag "transcript reads for this
        session must go through ``brain.iter_messages``" — the
        learning curator's two ``iter_messages(meta.jsonl_path)``
        sites at ``learning_curator.py:1572,2223`` branch on this
        Optional path.

        ``last_message_timestamp`` comes from the session row's
        ``time_updated`` column (millisecond Unix epoch) — opencode
        bumps that on every message write, so it's a reliable proxy
        for "when did this session last see activity". No need to
        round-trip the ``message`` table for a MAX(time_created)
        per session.

        Returns an empty iterator when:
          - ``opencode.db`` doesn't exist (fresh install, no
            sessions yet);
          - the DB is persistently locked (5×100 ms backoff blown);
          - the schema is unreadable / corrupt.

        In every empty case the curator's eligibility scan continues
        silently rather than crashing — same safety guarantee as
        Day 3's stub.
        """
        workspace_str = str(self._workspace.resolve())
        rows = _run_db_query(
            """
            SELECT s.id, s.time_updated, COUNT(m.id) AS msg_count
            FROM session s
            LEFT JOIN message m ON s.id = m.session_id
            WHERE s.directory = ?
            GROUP BY s.id, s.time_updated
            ORDER BY s.time_updated DESC
            """,
            (workspace_str,),
        )
        if rows is None:
            return
        for sid, time_updated_ms, msg_count in rows:
            ts: datetime | None = None
            if isinstance(time_updated_ms, int):
                try:
                    ts = datetime.fromtimestamp(
                        time_updated_ms / 1000, tz=timezone.utc
                    )
                except (OverflowError, OSError, ValueError):
                    ts = None
            yield SessionMeta(
                session_uuid=str(sid),
                jsonl_path=None,
                last_message_timestamp=ts,
                message_count_estimate=int(msg_count or 0),
            )

    def iter_messages(self, session_id: str) -> Iterator[TranscriptMessage]:
        """Stream user + assistant turns from one opencode session.

        Two queries (one for messages, one for parts) — cheaper
        than a JOIN that would explode the row count by tool-call
        + step events. Parts are grouped by ``message_id`` in
        Python so each ``TranscriptMessage`` carries its full text
        + tool-call payload in the shape the curator expects.

        Schema cues (sampled live against opencode 1.14):

          message.data — JSON with ``role``, ``time.created``
            (millisecond epoch), ``agent``, ``model.providerID``,
            ``model.modelID``, ``summary`` (auto-generated title /
            diffs).
          part.data — JSON with ``type`` discriminator. Three types
            we care about:
              ``text`` — ``{type, text, time?}`` carries assistant
                or user message body.
              ``tool`` — ``{type, callID, tool, state: {input,
                output}, time}`` carries one tool invocation.
              ``step-start`` / ``step-finish`` — pacing markers,
                ignored.

        Returns an empty iterator on missing DB, persistent lock,
        unknown session_id, or schema corruption. Caller assumes
        empty == "no transcript content here, skip" rather than
        "scan failed" — same semantics as
        ``core.transcripts.iter_messages`` for an unreadable JSONL.
        """
        msg_rows = _run_db_query(
            """
            SELECT id, time_created, data FROM message
            WHERE session_id = ?
            ORDER BY time_created
            """,
            (session_id,),
        )
        if msg_rows is None:
            return
        part_rows = _run_db_query(
            """
            SELECT message_id, data FROM part
            WHERE session_id = ?
            ORDER BY message_id, time_created
            """,
            (session_id,),
        )
        if part_rows is None:
            part_rows = []

        parts_by_msg: dict[str, list[dict]] = {}
        for mid, part_raw in part_rows:
            try:
                part = json.loads(part_raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(part, dict):
                continue
            parts_by_msg.setdefault(str(mid), []).append(part)

        for mid, time_created_ms, msg_raw in msg_rows:
            try:
                msg = json.loads(msg_raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "")
            if role not in ("user", "assistant"):
                continue
            # Prefer message.time.created (the model-side
            # timestamp) over the row's bookkeeping time_created;
            # they're typically identical but the row column is
            # the safe fallback when the JSON is partial.
            created_ms: int | None = None
            time_obj = msg.get("time")
            if isinstance(time_obj, dict):
                c = time_obj.get("created")
                if isinstance(c, int):
                    created_ms = c
            if created_ms is None and isinstance(time_created_ms, int):
                created_ms = time_created_ms
            if created_ms is None:
                continue
            try:
                ts = datetime.fromtimestamp(
                    created_ms / 1000, tz=timezone.utc
                )
            except (OverflowError, OSError, ValueError):
                continue

            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for p in parts_by_msg.get(str(mid), []):
                ptype = p.get("type")
                if ptype == "text":
                    t = p.get("text")
                    if isinstance(t, str):
                        text_parts.append(t)
                elif ptype == "tool":
                    state = p.get("state")
                    input_obj = (
                        state.get("input") if isinstance(state, dict) else None
                    )
                    tool_calls.append({
                        "id": p.get("callID"),
                        "name": p.get("tool"),
                        "input": input_obj,
                    })

            yield TranscriptMessage(
                role=role,
                text="\n".join(text_parts),
                timestamp=ts,
                uuid=str(mid),
                tool_calls=tuple(tool_calls),
                raw=msg,
            )

    def is_brain_owned_session(self, session_id: str) -> bool:
        """Curator-recursion guard against opencode-stored sessions.

        Reads the first user-role message via ``iter_messages`` and
        checks its concatenated text against the same prompt
        prefixes the claude-code path uses
        (``CURATOR_REVIEW_PROMPT_PREFIX``, ``GOAL_JUDGE_PROMPT_PREFIX``).
        The opencode storage layout is different (DB row vs JSONL
        line) but the prefix-match is content-shaped — same answer
        for the same conversation.

        Lazy-imports the prefix constants to avoid the circular
        import the claude-code path already documents at
        ``core.transcripts._is_curator_owned``.
        """
        from vexis_agent.core.goal_judge import GOAL_JUDGE_PROMPT_PREFIX
        from vexis_agent.core.kanban.constants import KANBAN_WORKER_PREFIX
        from vexis_agent.core.learning_review import CURATOR_REVIEW_PROMPT_PREFIX

        for msg in self.iter_messages(session_id):
            if msg.role != "user":
                continue
            text = msg.text
            return (
                text.startswith(CURATOR_REVIEW_PROMPT_PREFIX)
                or text.startswith(GOAL_JUDGE_PROMPT_PREFIX)
                or text.startswith(KANBAN_WORKER_PREFIX)
            )
        return False

    # ─── MCP config wiring (real on Day 3) ───────────────────────

    def write_mcp_config(self, servers: list[McpServerSpec]) -> Path:
        """Merge vexis's MCP servers into ``<workspace>/opencode.json``.

        Strategy: namespace prefix ``vexis-``. The writer reads any
        existing file, splits the ``mcp:`` block into
        ``{vexis-prefixed: ..., user-owned: ...}``, replaces the
        prefixed half with the new server list (all entries get
        prefixed automatically), and re-emits the user-owned half
        byte-for-byte. Atomic write via tempfile + rename.

        Why namespace-prefix and not a separate file: OpenCode's
        ``.opencode/`` directory walker only loads
        ``opencode.json{,c}`` — arbitrary ``*.json`` files are
        ignored (verified at
        ``packages/opencode/src/config/config.ts:587-588``). So
        merging into the user's single ``opencode.json`` is the
        only available path.

        Round-trip invariant: parsing ``opencode.json``,
        round-tripping through ``write_mcp_config(same servers)``,
        re-parsing — user-owned ``mcp:`` entries are byte-identical.
        Pinned by ``tests/test_brain_opencode_scaffold.py``.
        """
        path = self._workspace / "opencode.json"

        # Read existing config (if any), preserve every top-level
        # key and every non-vexis-prefixed entry under ``mcp:``.
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(
                    path.read_text(encoding="utf-8")
                )
                if not isinstance(existing, dict):
                    log.warning(
                        "opencode.json at %s is not a JSON object; "
                        "rewriting from scratch", path,
                    )
                    existing = {}
            except (OSError, json.JSONDecodeError) as exc:
                log.warning(
                    "Could not parse %s (%s); rewriting from scratch",
                    path, exc,
                )
                existing = {}

        # Split ``mcp:`` block: keep user entries, drop vexis-
        # prefixed (we'll re-emit them from ``servers``).
        existing_mcp = existing.get("mcp")
        if not isinstance(existing_mcp, dict):
            existing_mcp = {}
        user_owned = {
            k: v
            for k, v in existing_mcp.items()
            if not (isinstance(k, str) and k.startswith(VEXIS_MCP_PREFIX))
        }

        # Re-emit vexis-owned servers under the prefix. Each
        # ``McpServerSpec`` translates to OpenCode's local-MCP
        # shape: ``{type: "local", command: [argv...], environment,
        # enabled: True}``. The legacy claude-code split of
        # ``command`` + ``args`` is collapsed into one list per
        # OpenCode's schema (``packages/opencode/src/config/mcp.ts``).
        vexis_owned: dict = {}
        for spec in servers:
            key = (
                spec.name
                if spec.name.startswith(VEXIS_MCP_PREFIX)
                else f"{VEXIS_MCP_PREFIX}{spec.name}"
            )
            entry: dict = {
                "type": "local",
                "command": [spec.command, *spec.args],
                "enabled": True,
            }
            if spec.env:
                entry["environment"] = dict(spec.env)
            vexis_owned[key] = entry

        merged_mcp = {**user_owned, **vexis_owned}

        # Merge back into the top-level config. Preserve every
        # other key (agent, provider, formatter, lsp, etc.) the
        # user added by hand.
        merged: dict = {**existing, "mcp": merged_mcp}
        if not merged_mcp:
            # Don't emit an empty ``mcp:`` block when there's
            # nothing to write — keeps the file clean for users
            # with no MCP servers configured.
            merged.pop("mcp", None)

        # Atomic write: tempfile + rename.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(merged, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        return path

    # ─── file conventions ────────────────────────────────────────

    def instruction_file_name(self) -> str:
        return "AGENTS.md"

    def instruction_search_paths(self, workspace: Path) -> list[Path]:
        """OpenCode reads ``AGENTS.md`` first, then ``CLAUDE.md`` as
        a fallback (unless ``OPENCODE_DISABLE_CLAUDE_CODE_PROMPT=1``
        is set). Plus a global at ``${XDG_CONFIG_HOME}/opencode/AGENTS.md``.
        Returned in lookup order so ``/status`` can render the
        path the model is actually reading."""
        global_path = (
            Path.home() / ".config" / "opencode" / "AGENTS.md"
        )
        return [
            workspace / "AGENTS.md",
            workspace / "CLAUDE.md",  # OpenCode's CLAUDE.md fallback
            global_path,
        ]

    # ─── lifecycle ───────────────────────────────────────────────

    async def healthcheck(self) -> BrainHealth:
        """Confirm ``opencode`` is on PATH and authenticated.

        Two checks: ``opencode --version`` (binary present) then
        ``opencode auth list`` (any provider configured). The auth
        check is best-effort — if ``opencode auth list`` exits
        non-zero (e.g. a future CLI change), we surface the
        version-only success and let the first ``respond`` call
        produce the actionable error.
        """
        if shutil.which("opencode") is None:
            return BrainHealth(
                ok=False,
                error="`opencode` not on PATH",
                hints=[
                    "Install OpenCode: curl -fsSL https://opencode.ai/install | bash",
                    "Then verify with: opencode --version",
                ],
            )

        # Auth check — non-fatal for healthcheck; first-spawn
        # surfaces the actionable error.
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["opencode", "auth", "list"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return BrainHealth(
                    ok=False,
                    error="opencode is installed but not authenticated",
                    hints=[
                        "Authenticate with: opencode auth login anthropic",
                        "(or: opencode auth login openai-codex / github-copilot)",
                    ],
                )
        except (subprocess.TimeoutExpired, OSError):
            # Best-effort — return ok if version succeeded.
            pass

        return BrainHealth(ok=True, error=None, hints=[])

    async def kill_in_flight(self) -> None:
        """No-op for Phase A/B parity — ``/cancel`` kills via
        ``RunningTasks.cancel()`` which calls ``proc.kill`` on the
        proc registered by ``RunningTasks.attach``. Same hook
        ``ClaudeCodeBrain`` exposes."""
        return None


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _extract_text_from_event_stream(raw: str) -> str:
    """Pull the concatenated ``text`` events out of an OpenCode JSON
    event stream. Used by ``spawn_aux`` to give callers a final-
    reply string in the shape the aux subsystems expect (a single
    text body, not the wire-format event log).

    Falls back to returning empty string when no ``text`` events
    are found; the caller then sees the raw stdout instead and can
    decide what to do."""
    parts: list[str] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(evt, dict):
            continue
        if evt.get("type") != "text":
            continue
        part = evt.get("part") or {}
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


__all__ = [
    "BRAIN_TIMEOUT_SECONDS",
    "BrainAuthRequired",
    "BrainCancelled",
    "BrainError",
    "BrainNotInstalled",
    "BrainTimeoutError",
    "OpenCodeBrain",
    "SessionLost",
    "VEXIS_AGENT_NAME",
    "VEXIS_AUX_AGENT_NAME",
    "VEXIS_MCP_PREFIX",
    "opencode_db_path",
    "set_opencode_db_path_override",
]
