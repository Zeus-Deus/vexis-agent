"""Subprocess wrapper around the OpenAI Codex CLI (``codex exec``).

Third real brain backend beside ``claude-code`` and ``opencode``
(``brain.kind: codex``). All CLI behaviour below was verified
empirically against codex-cli 0.145.0 — see
``.plans/codex-brain-research.md`` for the design lock and the live
event dumps that back every decision here.

Non-interactive spawn. ``codex exec [OPTIONS] [PROMPT]`` starts a
fresh session; ``codex exec [OPTIONS] resume <THREAD_ID> [PROMPT]``
resumes one. OPTIONS must precede the ``resume`` subcommand (clap
rejects a flag after it). ``--json`` prints a JSONL event stream on
stdout. ``-C <dir>`` sets the working root; ``--skip-git-repo-check``
is always passed (harmless in a git repo, required outside one).
Every spawn uses ``stdin=DEVNULL`` — codex reads a piped stdin and
appends it as a ``<stdin>`` block otherwise.

System-prompt injection. ``-c developer_instructions=<TOML string>``
injects a developer-role prompt WITHOUT replacing codex's own base
instructions (the codex analogue of opencode's
``OPENCODE_CONFIG_CONTENT`` seam). ``json.dumps(text)`` is a valid
TOML basic string, so we pass ``developer_instructions=`` + that as
one argv element.

Session model. opencode-style: ``SessionStore`` holds a placeholder
until the first ``respond`` harvests ``thread.started.thread_id``,
then ``sess.set(id)`` + ``mark_initialized()``. Resume passes
``resume <token> <message>``. SessionLost fires on the stderr marker
``no rollout found for thread id`` (resume of an unknown id) →
``sess.rotate()`` + raise, matching the brain-agnostic recovery the
transport already runs for claude-code / opencode.

Transcript readback. codex persists sessions as rollout JSONLs under
``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`` (line 0
is a ``session_meta`` with ``cwd`` + ``id``; conversational turns are
``event_msg`` lines of subtype ``user_message`` / ``agent_message``).
The reader filters by ``cwd == workspace`` and flattens into
``TranscriptMessage`` — same shape claude-code / opencode emit, so
the curator is brain-agnostic. NEVER import ``claude_session_jsonl_dir``
(parity tripwire — codex has no ``~/.claude/projects`` layout).

MCP config. Written to ``$CODEX_HOME/vexis.config.toml`` — the
``vexis`` profile. Replace-all within the file (it is vexis-owned,
like claude-code's ``.mcp.json``); ``--profile vexis`` is passed on
every spawn only when that file exists. codex safety hooks require
per-user trusted-hash registration, so no PreToolUse analogue ships
in v1 (documented limitation).
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
    mcp_spec_to_codex_entry,
)
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore
from vexis_agent.core.status import StatusFile
from vexis_agent.core.transcripts import SessionMeta, TranscriptMessage
from vexis_agent.core.brain.usage import build_usage_event
from vexis_agent.core.workspace_snapshot import (
    diff as _snapshot_diff,
    snapshot as _take_snapshot,
)
from vexis_agent.core.yaml_config import brain_file_mutation_footer_enabled

log = logging.getLogger(__name__)

# 30 min — same ceiling as the other real brains so user-facing turn
# semantics stay aligned across brain kinds.
BRAIN_TIMEOUT_SECONDS = 1800

# Match the other brains — a single JSONL event can carry a
# multi-megabyte tool result (base64 screenshot). 32 MiB covers the
# realistic ceiling without breaking ``readline``.
_BRAIN_STREAM_LIMIT_BYTES = 32 * 1024 * 1024

# codex's SessionLost marker: a resume against an unknown thread id
# exits non-zero and writes this to stderr (verified — the code
# -32600 line). Matched case-insensitively.
_ROLLOUT_NOT_FOUND_MARKER = "no rollout found for thread id"

# The ``vexis`` profile file codex layers on top of its base config
# when ``--profile vexis`` is passed. write_mcp_config owns it.
_VEXIS_PROFILE_NAME = "vexis"
_VEXIS_PROFILE_FILE = f"{_VEXIS_PROFILE_NAME}.config.toml"

# allowed_tools categories that require an unsandboxed spawn. codex has
# no per-tool allowlist, so an allowlist naming any shell / web tool
# maps to the full-access flag; a purely file-editing allowlist maps
# to workspace-write; text-only maps to read-only.
_SHELL_WEB_TOOLS: frozenset[str] = frozenset(
    {"Bash", "Shell", "WebFetch", "WebSearch"}
)


def codex_home() -> Path:
    """Resolve ``$CODEX_HOME`` (env var wins) or the default
    ``~/.codex``. Read per call so an env change hot-reloads."""
    env = os.environ.get("CODEX_HOME")
    if env and env.strip():
        return Path(env).expanduser()
    return Path.home() / ".codex"


# Test hook. Production: sessions live under ``codex_home()/sessions``.
# Tests point the reader at a hand-built rollout dir via
# ``set_codex_sessions_dir_override`` (the autouse
# ``_isolate_codex_sessions`` fixture in tests/conftest.py sets it to a
# nonexistent tmp dir so the curator scan sees nothing by default).
_CODEX_SESSIONS_DIR_OVERRIDE: Path | None = None


def codex_sessions_dir() -> Path:
    """The rollout-JSONL root. Override wins for tests; otherwise
    ``codex_home()/sessions``."""
    if _CODEX_SESSIONS_DIR_OVERRIDE is not None:
        return _CODEX_SESSIONS_DIR_OVERRIDE
    return codex_home() / "sessions"


def set_codex_sessions_dir_override(path: Path | None) -> None:
    """Test hook. Set to None to revert to the default location.
    Used by ``tests/test_brain_codex_transcripts.py`` to point the
    reader at a hand-built tmp rollout tree."""
    global _CODEX_SESSIONS_DIR_OVERRIDE
    _CODEX_SESSIONS_DIR_OVERRIDE = path


# ──────────────────────────────────────────────────────────────────
# Error classification (stderr + collected error-event messages)
# ──────────────────────────────────────────────────────────────────


# The HTTP status arrives in codex's JSON wire shape (``"status":500``)
# as well as prose — the bridge class must cross the ``":`` characters.
_TRANSIENT_ERROR_RE = re.compile(
    r"status[\"':\s]*5\d\d"
    r"|429"
    r"|rate.?limit"
    r"|timed?\s*out"
    r"|connection\s+reset"
    r"|temporarily\s+unavailable"
    r"|overloaded"
    r"|stream\s+disconnected",
    re.IGNORECASE,
)
_PERMANENT_ERROR_RE = re.compile(
    r"status[\"':\s]*40[0-9]"
    r"|not\s+supported"
    r"|invalid_request_error"
    r"|authentication"
    r"|insufficient\s+quota"
    r"|codex\s+login",
    re.IGNORECASE,
)

# Model-not-found markers (spawn-site backstop). A ``turn.failed`` /
# ``error`` event carrying either substring, with a non-zero exit,
# means codex rejected the ``-m`` id.
_MODEL_NOT_FOUND_MARKERS = ("model is not supported", "Model metadata for")


def _classify_brain_failure(
    *, stderr_text: str, error_text: str,
) -> tuple[type[BrainError], str]:
    """Pick the most specific ``BrainError`` subclass + diagnostic for
    a non-zero ``codex exec`` exit.

    ``error_text`` is the concatenation of any ``error`` /
    ``turn.failed`` event messages seen on the stream — codex writes
    API errors there rather than to stderr, mirroring claude-code's
    stdout-carries-the-error quirk. Fallback is ``BrainError`` base:
    unknown failure, don't retry, surface verbatim."""
    parts = [s.strip() for s in (stderr_text, error_text) if s and s.strip()]
    combined = " | ".join(parts) or "(no stderr or error events)"
    if _TRANSIENT_ERROR_RE.search(combined):
        return BrainTransientError, combined
    if _PERMANENT_ERROR_RE.search(combined):
        return BrainPermanentError, combined
    return BrainError, combined


# ──────────────────────────────────────────────────────────────────
# codex JSON event parser
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _StreamResult:
    """Outcome of one ``_read_codex_event_stream`` pass.

    - ``final_text``: the LAST ``agent_message`` item's text (a turn
      may narrate between tools, then answer — the last message is
      the canonical reply). ``None`` when the turn produced none.
    - ``harvested_thread_id``: the ``thread.started.thread_id`` for a
      fresh spawn (caller-supplied on resumes). ``None`` when no
      ``thread.started`` arrived.
    - ``error_text``: concatenated ``error`` / ``turn.failed`` event
      messages — fed to error classification + model-not-found
      detection.
    - ``saw_any_event``: False ⇒ stream EOF with no parseable event ⇒
      ``respond`` raises BrainError rather than echoing a blank reply.
    """

    final_text: str | None
    harvested_thread_id: str | None
    error_text: str
    saw_any_event: bool


def _item_tool_identity(item: dict) -> tuple[str, str | None] | None:
    """Map one codex tool item onto Vexis's provider-neutral name/target.

    The MCP server is retained in the name when codex supplies it, so
    downstream metrics can group newly installed add-ons dynamically. No
    catalog, vehicle, brand, or model names are enumerated here.
    """
    itype = item.get("type")
    if itype == "command_execution":
        command = item.get("command")
        return "shell", command if isinstance(command, str) else None
    if itype == "mcp_tool_call":
        server = item.get("server")
        tool = item.get("tool")
        if isinstance(server, str) and server:
            suffix = str(tool) if tool else "tool"
            return f"mcp__{server}__{suffix}", None
        return str(tool or "mcp"), None
    if itype == "file_change":
        path = item.get("path")
        return "edit", path if isinstance(path, str) else None
    if itype == "web_search":
        query = item.get("query")
        return "websearch", query if isinstance(query, str) else None
    return None


def _record_item_tool(
    item: dict, status_file: StatusFile,
) -> tuple[str, str | None] | None:
    """Update the per-chat status file from one stream ``item``.

    Maps codex item types onto the ``(name, target)`` shape ``/status``
    renders: ``command_execution`` → ``shell`` + the command string,
    ``mcp_tool_call`` → the tool name, ``file_change`` → ``edit`` +
    path, ``web_search`` → ``websearch``. Unknown item types are
    ignored."""
    identity = _item_tool_identity(item)
    if identity is not None:
        status_file.record_tool(*identity)
    return identity


@dataclass(frozen=True)
class _PendingCodexTool:
    name: str
    target: str | None
    started_at: float
    started_ts: int


async def _read_codex_event_stream(
    stream: asyncio.StreamReader | None,
    status_file: StatusFile,
    target_thread_id: str | None,
    event_sink: Callable[[str | dict], None] | None = None,
) -> _StreamResult:
    """Consume ``codex exec --json`` stdout: harvest the thread id,
    track tool items in the status file, keep the last
    ``agent_message`` as the reply, and collect error-event messages.

    Event shapes (verified dumps, see the research doc §1):

      ``{"type":"thread.started","thread_id":"..."}``
      ``{"type":"item.completed","item":{"type":"agent_message","text":"..."}}``
      ``{"type":"item.started"/"item.completed","item":{"type":"command_execution",...}}``
      ``{"type":"error","message":"..."}``
      ``{"type":"turn.failed","error":{"message":"..."}}``

    Unknown item / event types are ignored (forward-compat)."""
    final_text: str | None = None
    harvested = target_thread_id
    error_parts: list[str] = []
    saw_any = False
    pending_tools: dict[str, _PendingCodexTool] = {}
    anonymous_tool_seq = 0

    def emit(value: str | dict) -> None:
        if event_sink is not None:
            event_sink(value)

    def finish_tool(
        tool_id: str,
        *,
        item: dict | None = None,
        force_error: bool = False,
    ) -> None:
        pending = pending_tools.pop(tool_id, None)
        if pending is None:
            return
        duration_ms = max(
            0, int((time.monotonic() - pending.started_at) * 1000),
        )
        item_status = str((item or {}).get("status") or "").lower()
        status = (
            "error"
            if force_error or item_status in {"error", "failed", "cancelled"}
            else "completed"
        )
        ts = int(time.time() * 1000)
        log.info(
            "tool-span chat=%s tool=%s duration_ms=%d status=%s "
            "target=%s",
            status_file.chat_id, pending.name, duration_ms, status,
            pending.target,
        )
        emit({
            "type": "tool_end",
            "name": pending.name,
            "target": pending.target,
            "id": tool_id,
            "ts": ts,
            "duration_ms": duration_ms,
            "status": status,
        })

    if stream is None:
        return _StreamResult(
            final_text=None,
            harvested_thread_id=harvested,
            error_text="",
            saw_any_event=False,
        )

    while True:
        try:
            line = await stream.readline()
        except asyncio.LimitOverrunError as exc:
            log.error(
                "codex emitted line bigger than %d-byte limit "
                "(consumed=%s); aborting stream read.",
                _BRAIN_STREAM_LIMIT_BYTES, exc.consumed,
            )
            break
        except Exception:
            log.warning("codex stream readline raised", exc_info=True)
            break
        if not line:
            break
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # codex writes one complete JSON object per line; a parse
            # failure is truncation or a bug on this line — skip it,
            # other lines may still parse.
            continue
        if not isinstance(event, dict):
            continue
        saw_any = True

        kind = event.get("type")
        if kind == "thread.started":
            tid = event.get("thread_id")
            if harvested is None and isinstance(tid, str) and tid:
                harvested = tid
        elif kind in ("item.started", "item.completed"):
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    # Last agent_message wins — narration between tools
                    # precedes the final answer.
                    final_text = text
                    emit(text)
            elif itype == "error":
                msg = item.get("message")
                if isinstance(msg, str) and msg:
                    error_parts.append(msg)
            else:
                identity = _item_tool_identity(item)
                if identity is None:
                    continue
                raw_id = item.get("id")
                if isinstance(raw_id, str) and raw_id:
                    tool_id = raw_id
                elif kind == "item.completed":
                    # Older/future codex shapes may omit ids. Pair by the
                    # provider-neutral identity before synthesizing a new call.
                    tool_id = next(
                        (
                            pending_id
                            for pending_id, pending in pending_tools.items()
                            if (pending.name, pending.target) == identity
                        ),
                        "",
                    )
                    if not tool_id:
                        anonymous_tool_seq += 1
                        tool_id = f"codex-tool-{anonymous_tool_seq}"
                else:
                    anonymous_tool_seq += 1
                    tool_id = f"codex-tool-{anonymous_tool_seq}"
                if kind == "item.started":
                    _record_item_tool(item, status_file)
                    # A duplicate start closes the stale span first so the
                    # paired coverage metric can never drift above 100%.
                    finish_tool(tool_id, force_error=True)
                    now_ts = int(time.time() * 1000)
                    pending_tools[tool_id] = _PendingCodexTool(
                        name=identity[0],
                        target=identity[1],
                        started_at=time.monotonic(),
                        started_ts=now_ts,
                    )
                    emit({
                        "type": "tool",
                        "name": identity[0],
                        "target": identity[1],
                        "id": tool_id,
                        "ts": now_ts,
                    })
                else:
                    # Codex normally sends a matching item.started. If a future
                    # CLI only sends completed, still surface a paired zero-ms
                    # span instead of silently losing the call.
                    if tool_id not in pending_tools:
                        _record_item_tool(item, status_file)
                        now_ts = int(time.time() * 1000)
                        pending_tools[tool_id] = _PendingCodexTool(
                            name=identity[0],
                            target=identity[1],
                            started_at=time.monotonic(),
                            started_ts=now_ts,
                        )
                        emit({
                            "type": "tool",
                            "name": identity[0],
                            "target": identity[1],
                            "id": tool_id,
                            "ts": now_ts,
                        })
                    finish_tool(tool_id, item=item)
        elif kind == "error":
            msg = event.get("message")
            if isinstance(msg, str) and msg:
                error_parts.append(msg)
            log.warning("codex stream error event: %r", msg)
        elif kind == "turn.failed":
            err = event.get("error")
            if isinstance(err, dict):
                msg = err.get("message")
                if isinstance(msg, str) and msg:
                    error_parts.append(msg)
        elif kind == "turn.completed":
            usage = event.get("usage")
            if isinstance(usage, dict):
                input_details = usage.get("input_tokens_details")
                output_details = usage.get("output_tokens_details")
                usage_event = build_usage_event(
                    input_tokens=usage.get("input_tokens"),
                    cache_read_tokens=(
                        input_details.get("cached_tokens")
                        if isinstance(input_details, dict)
                        else usage.get("cached_input_tokens")
                    ),
                    output_tokens=usage.get("output_tokens"),
                    reasoning_tokens=(
                        output_details.get("reasoning_tokens")
                        if isinstance(output_details, dict)
                        else usage.get("reasoning_tokens")
                    ),
                    total_tokens=usage.get("total_tokens"),
                )
                if usage_event is not None:
                    emit(usage_event)

    # EOF with an open item means the CLI/process ended before its result.
    for tool_id in list(pending_tools):
        finish_tool(tool_id, force_error=True)

    return _StreamResult(
        final_text=final_text,
        harvested_thread_id=harvested,
        error_text=" | ".join(error_parts),
        saw_any_event=saw_any,
    )


def _extract_error_text_from_stdout(raw: str) -> str:
    """Pull ``error`` / ``turn.failed`` messages out of a buffered
    codex event stream (spawn_aux's sync path). Mirrors the streaming
    reader's error-collection so both surfaces classify identically."""
    parts: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(evt, dict):
            continue
        kind = evt.get("type")
        if kind == "error":
            msg = evt.get("message")
            if isinstance(msg, str) and msg:
                parts.append(msg)
        elif kind == "turn.failed":
            err = evt.get("error")
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                parts.append(err["message"])
        elif kind in ("item.started", "item.completed"):
            item = evt.get("item")
            if (
                isinstance(item, dict)
                and item.get("type") == "error"
                and isinstance(item.get("message"), str)
            ):
                parts.append(item["message"])
    return " | ".join(parts)


def _detect_model_not_found(stdout: str, returncode: int) -> bool:
    """True when a non-zero codex run rejected the ``-m`` model id.

    The diagnostic is an ``error`` / ``turn.failed`` event carrying
    ``model is not supported`` or ``Model metadata for`` (verified —
    bad-model runs exit 1 and emit both an ``error`` item and a
    top-level ``error`` event). Used by ``spawn_aux`` to raise a
    structured ``BrainModelNotFoundError``."""
    if returncode == 0:
        return False
    error_text = _extract_error_text_from_stdout(stdout)
    return any(marker in error_text for marker in _MODEL_NOT_FOUND_MARKERS)


def _extract_agent_text_from_stdout(raw: str) -> str:
    """Concatenate ``agent_message`` item texts from a buffered codex
    event stream — spawn_aux hands callers the same final-reply shape
    the other brains produce. Empty when no agent_message parses; the
    caller then falls back to raw stdout."""
    parts: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(evt, dict):
            continue
        if evt.get("type") != "item.completed":
            continue
        item = evt.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


# ──────────────────────────────────────────────────────────────────
# Hand-rolled minimal TOML emitter (strings / string lists / string
# maps only — stdlib has no TOML writer and we don't add a dep).
# ──────────────────────────────────────────────────────────────────


def _toml_str(value: str) -> str:
    """Emit a TOML basic string. ``json.dumps`` of a string is a valid
    TOML basic string (JSON escapes are a subset of TOML's)."""
    return json.dumps(value)


# TOML bare keys are [A-Za-z0-9_-]+; anything else (dots, spaces,
# brackets) must be quoted or a hostile/typo'd server name from
# mcp-servers.yaml could inject extra tables or corrupt the file —
# and a corrupt vexis.config.toml breaks EVERY spawn that passes
# ``--profile vexis``.
_TOML_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(key: str) -> str:
    """Quote ``key`` unless it is a valid TOML bare key."""
    return key if _TOML_BARE_KEY_RE.match(key) else _toml_str(key)


def _emit_mcp_servers_toml(servers: dict[str, dict]) -> str:
    """Render ``{name: entry}`` (entries from
    :func:`mcp_spec_to_codex_entry`) as codex config TOML::

        [mcp_servers.<name>]
        command = "npx"
        args = ["-y", "server"]

        [mcp_servers.<name>.env]
        FOO = "bar"

    Only string / string-list / string-map values are supported — the
    full surface codex's ``mcp_servers`` entries need."""
    blocks: list[str] = []
    for name in sorted(servers):
        entry = servers[name]
        lines = [f"[mcp_servers.{_toml_key(name)}]"]
        env_table: dict | None = None
        for key in sorted(entry):
            val = entry[key]
            if isinstance(val, dict):
                # Nested table (``env``) — emit after the scalar keys.
                env_table = val
                continue
            if isinstance(val, list):
                items = ", ".join(_toml_str(str(v)) for v in val)
                lines.append(f"{_toml_key(key)} = [{items}]")
            else:
                lines.append(f"{_toml_key(key)} = {_toml_str(str(val))}")
        block = "\n".join(lines)
        if env_table:
            env_lines = [f"\n[mcp_servers.{_toml_key(name)}.env]"]
            for ekey in sorted(env_table):
                env_lines.append(
                    f"{_toml_key(ekey)} = {_toml_str(str(env_table[ekey]))}"
                )
            block += "\n" + "\n".join(env_lines)
        blocks.append(block)
    return "\n\n".join(blocks)


# ──────────────────────────────────────────────────────────────────
# CodexBrain
# ──────────────────────────────────────────────────────────────────


class CodexBrain(Brain):
    """Sibling of ``OpenCodeBrain`` against the ``codex`` CLI.

    Constructor mirrors the other real brains: ``workspace`` +
    ``session: SessionStore`` + ``running_tasks: RunningTasks``. No
    ``extra_prompt_blocks`` and no safety-hook install (codex hooks
    need per-user trusted-hash registration — deferred)."""

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
        # invariant the other brains enforce. Byte-identical
        # ``developer_instructions`` across a session's turns keeps the
        # provider prompt cache warm.
        self._system_prompt_cache: dict[str, str] = {}
        # Issue #9: per-chat file-mutation buffer, drained by the
        # handler's verifier-footer injector on the next turn.
        self._files_changed_by_chat: dict[int, list[str]] = {}
        # Session-id → rollout-path memo. Safe to keep for the brain's
        # lifetime: a rollout never changes identity once written, and
        # hits are re-validated with ``exists()`` before use.
        self._rollout_path_cache: dict[str, Path] = {}

    # ─── foreground turn ─────────────────────────────────────────

    async def respond(
        self,
        message: str,
        chat_id: int,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
        session: "SessionLike | None" = None,
        attachments: list[Path] | None = None,
    ) -> str:
        log.info(
            "CodexBrain.respond starting for chat %d%s%s",
            chat_id,
            f" (model override: {model})" if model else "",
            f" (reasoning: {reasoning_level})" if reasoning_level else "",
        )
        # Issue #9 — snapshot the workspace before the subprocess runs;
        # diff after (in the finally) so failures/cancels still record
        # partial writes.
        before_snapshot = await self._maybe_take_snapshot()
        try:
            return await self._respond_inner(
                message, chat_id,
                model=model, reasoning_level=reasoning_level,
                session=session, attachments=attachments,
            )
        finally:
            await self._record_files_changed(chat_id, before_snapshot)

    async def astream(
        self,
        message: str,
        chat_id: int,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
        session: "SessionLike | None" = None,
        attachments: list[Path] | None = None,
    ) -> AsyncIterator[str | dict]:
        """Stream codex JSONL as provider-neutral text/tool events.

        `item.started` / `item.completed` pairs become the same `tool` /
        `tool_end` wire contract Claude Code already emits. The last agent
        message is repeated once as the terminal `final` control event so
        consumers persist the canonical answer rather than narration.
        """
        log.info(
            "CodexBrain.astream starting for chat %d%s%s",
            chat_id,
            f" (model override: {model})" if model else "",
            f" (reasoning: {reasoning_level})" if reasoning_level else "",
        )
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        before_snapshot = await self._maybe_take_snapshot()

        async def run() -> None:
            try:
                reply = await self._respond_inner(
                    message,
                    chat_id,
                    model=model,
                    reasoning_level=reasoning_level,
                    session=session,
                    attachments=attachments,
                    event_sink=lambda event: queue.put_nowait(
                        ("event", event)
                    ),
                )
            except Exception as exc:
                queue.put_nowait(("error", exc))
            else:
                queue.put_nowait(("result", reply))
            finally:
                await self._record_files_changed(
                    chat_id, before_snapshot,
                )

        task = asyncio.create_task(run())
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "event":
                    yield payload  # type: ignore[misc]
                    continue
                if kind == "error":
                    raise payload  # type: ignore[misc]
                reply = str(payload)
                yield {"type": "final", "text": reply}
                return
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _respond_inner(
        self,
        message: str,
        chat_id: int,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
        session: "SessionLike | None" = None,
        attachments: list[Path] | None = None,
        event_sink: Callable[[str | dict], None] | None = None,
    ) -> str:
        # Issue #48: ``session`` selects which session this turn runs
        # against. ``None`` (Telegram, shared web chat) is the bound
        # active-session store; a non-``None`` ``SessionView`` (one per
        # web conversation) routes harvest/resume/rotate through it.
        sess = session if session is not None else self._session
        is_initialized = sess.is_initialized()
        stored_token = sess.get() if is_initialized else None

        from vexis_agent.core.yaml_config import model_for_tier
        # Per-turn override beats the config default; None (Telegram,
        # text chat) falls through to codex's account default.
        model = model or model_for_tier("codex", None)

        system_prompt = self._system_prompt_for(stored_token or "fresh")

        # OPTIONS first (they must precede the ``resume`` subcommand),
        # then either ``resume <token> <message>`` or ``<message>``.
        opts: list[str] = [
            "codex", "exec",
            "--json",
            "--skip-git-repo-check",
            "-C", str(self._workspace),
            "--dangerously-bypass-approvals-and-sandbox",
            "-c", "developer_instructions=" + json.dumps(system_prompt),
        ]
        if model:
            opts += ["-m", model]
        if reasoning_level:
            opts += [
                "-c",
                "model_reasoning_effort=" + json.dumps(reasoning_level),
            ]
        opts += self._profile_args()
        image_args: list[str] = []
        for path in attachments or []:
            image_args += ["--image", str(Path(path).resolve())]

        if stored_token:
            argv = opts + ["resume", stored_token] + image_args + [message]
        elif image_args:
            # Fresh-run ``--image`` is variadic, so a trailing prompt would
            # otherwise be consumed as another image path.
            argv = opts + image_args + ["--", message]
        else:
            argv = opts + [message]

        env = {**os.environ, "VEXIS_CHAT_ID": str(chat_id)}

        log.debug(
            "Spawning codex exec (cwd=%s, resume=%s)",
            self._workspace, stored_token if stored_token else "<fresh>",
        )

        reservation = await self._running_tasks.reserve(chat_id)
        status_file = StatusFile(chat_id)
        status_file.start()

        stderr_bytes = b""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self._workspace),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=env,
                limit=_BRAIN_STREAM_LIMIT_BYTES,
            )
            log.info("codex spawned PID %d for chat %d", proc.pid, chat_id)

            attached = await self._running_tasks.attach(reservation, proc)
            if not attached:
                log.info(
                    "codex raising BrainCancelled for chat %d "
                    "(cancel during reservation window)",
                    chat_id,
                )
                await self._kill_group(proc)
                raise BrainCancelled("codex exec cancelled via /cancel")

            stdout_task = asyncio.create_task(
                _read_codex_event_stream(
                    proc.stdout, status_file, target_thread_id=stored_token,
                    event_sink=event_sink,
                )
            )
            stderr_task = asyncio.create_task(proc.stderr.read())

            try:
                await asyncio.wait_for(
                    proc.wait(), timeout=BRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                await self._kill_group(proc)
                await asyncio.gather(
                    stdout_task, stderr_task, return_exceptions=True
                )
                raise BrainTimeoutError(
                    f"codex exec timed out after {BRAIN_TIMEOUT_SECONDS}s"
                ) from exc

            try:
                stream_result = await stdout_task
            except Exception:
                log.exception(
                    "codex stdout reader failed for chat %d", chat_id
                )
                stream_result = _StreamResult(
                    final_text=None,
                    harvested_thread_id=None,
                    error_text="",
                    saw_any_event=False,
                )
            try:
                stderr_bytes = await stderr_task
            except Exception:
                log.exception(
                    "codex stderr reader failed for chat %d", chat_id
                )
                stderr_bytes = b""

            if self._running_tasks.was_cancelled(chat_id):
                log.info(
                    "codex raising BrainCancelled for chat %d (proc killed)",
                    chat_id,
                )
                raise BrainCancelled("codex exec cancelled via /cancel")

            err_text = stderr_bytes.decode(errors="replace").strip()

            # SessionLost — a resume against an unknown thread id exits
            # non-zero with the rollout-not-found marker on stderr.
            # Rotate the dead token + raise for the transport's
            # existing recovery to retry on a fresh session.
            if (
                is_initialized
                and proc.returncode != 0
                and _ROLLOUT_NOT_FOUND_MARKER in err_text.lower()
            ):
                old = stored_token
                new = sess.rotate()
                log.warning(
                    "codex lost session %s; rotated to %s", old, new,
                )
                raise SessionLost(
                    "Codex session was lost. Rotated to new session."
                )

            if proc.returncode != 0:
                cls, msg = _classify_brain_failure(
                    stderr_text=err_text,
                    error_text=stream_result.error_text,
                )
                raise cls(f"codex exec exited {proc.returncode}: {msg}")

            # Clean exit but no event at all — codex emits at least a
            # turn.started per run. Empty stream + 0 exit is degraded;
            # surface it rather than echoing a blank reply.
            if not stream_result.saw_any_event:
                raise BrainError(
                    "codex exec exited 0 but emitted no events; "
                    f"stderr: {err_text or '(empty)'}"
                )
        finally:
            status_file.delete()
            await self._running_tasks.unregister(chat_id)

        # First-call success path: persist the harvested thread id so
        # the next turn resumes via ``resume <id>``.
        if not is_initialized:
            if stream_result.harvested_thread_id:
                sess.set(stream_result.harvested_thread_id)
                log.info(
                    "codex session established and persisted: %s",
                    stream_result.harvested_thread_id,
                )
            else:
                log.warning(
                    "codex reply succeeded but no thread_id was harvested "
                    "from the event stream. Subsequent turns will start a "
                    "new session — chat will feel context-less."
                )
            sess.mark_initialized()

        return (stream_result.final_text or "").strip()

    def _profile_args(self) -> list[str]:
        """``--profile vexis`` when ``$CODEX_HOME/vexis.config.toml``
        exists, else nothing. The profile layers vexis's MCP servers
        onto codex's base config; before ``write_mcp_config`` has run
        there's no profile to layer."""
        if (codex_home() / _VEXIS_PROFILE_FILE).exists():
            return ["--profile", _VEXIS_PROFILE_NAME]
        return []

    @staticmethod
    async def _kill_group(proc: asyncio.subprocess.Process) -> None:
        """Kill the whole process group. ``start_new_session=True`` at
        spawn puts codex + its children (shell, MCP servers) under one
        group so ``os.killpg`` reaches all of them."""
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
                log.error("codex exec (pid=%s) ignored SIGKILL", proc.pid)

    # ─── system prompt ───────────────────────────────────────────

    def _system_prompt_for(self, session_uuid: str) -> str:
        cached = self._system_prompt_cache.get(session_uuid)
        if cached is not None:
            return cached
        prompt = self._build_prompt()
        # Cap mirrors the other brains — 16 entries, FIFO eviction.
        if len(self._system_prompt_cache) >= 16:
            oldest = next(iter(self._system_prompt_cache))
            del self._system_prompt_cache[oldest]
        self._system_prompt_cache[session_uuid] = prompt
        return prompt

    def _build_prompt(self) -> str:
        """Full claude-code-style prompt INCLUDING the skills index —
        codex's native skill discovery uses ``$CODEX_HOME/skills``, not
        the workspace, so vexis must render its own index (unlike
        opencode, which auto-discovers ``<workspace>/skills`` and gets
        the index dropped)."""
        from vexis_agent.core.brain.claude_code import (
            build_system_prompt as _cc_build_system_prompt,
        )

        return _cc_build_system_prompt(self._workspace)

    def build_system_prompt(self) -> str:
        """ABC method. The workspace-resolved prompt with the skills
        index (see :meth:`_build_prompt`)."""
        return self._build_prompt()

    # ─── aux spawn ───────────────────────────────────────────────

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
        """Run an aux call against ``codex exec --ephemeral``.

        ``--ephemeral`` keeps aux sessions out of the rollout dir
        entirely (recursion-guard-clean by construction; the
        content-prefix check in ``is_brain_owned_session`` stays as
        defence in depth). The allowlist maps to a coarse sandbox flag
        (codex has no per-tool allowlist). Sync subprocess wrapped in
        ``asyncio.to_thread`` for the async contract; ``stdin=DEVNULL``
        so codex never blocks reading a piped stdin.
        """
        from vexis_agent.core.yaml_config import model_for_tier

        workdir = str(cwd if cwd is not None else self._workspace)
        model = model_for_tier("codex", model_tier)

        argv: list[str] = [
            "codex", "exec",
            "--json",
            "--skip-git-repo-check",
            "--ephemeral",
            "-C", workdir,
        ]
        argv += _sandbox_flags(allow_tools, allowed_tools)
        if model:
            argv += ["-m", model]
        if reasoning_level:
            argv += [
                "-c",
                "model_reasoning_effort=" + json.dumps(reasoning_level),
            ]
        argv += self._profile_args()
        # context_window: accepted for ABC stability but inert — codex
        # has no runtime context flag. Documented in Brain.spawn_aux.
        _ = context_window
        argv.append(prompt)

        env = dict(os.environ)
        if env_overrides:
            env.update(env_overrides)

        def _run() -> AuxResult:
            try:
                cp = subprocess.run(
                    argv,
                    env=env,
                    cwd=workdir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise BrainTimeoutError(
                    f"codex exec aux call timed out after {timeout_seconds}s"
                ) from exc
            except FileNotFoundError as exc:
                raise BrainNotInstalled(
                    "`codex` not on PATH; install Codex: "
                    "npm install -g @openai/codex (then: codex login)"
                ) from exc
            except OSError as exc:
                raise BrainError(
                    f"codex exec aux spawn failed: {exc}"
                ) from exc

            stdout = (cp.stdout or b"").decode("utf-8", errors="replace")
            stderr = (cp.stderr or b"").decode("utf-8", errors="replace")

            # Spawn-site model backstop. A bad ``-m`` id exits non-zero
            # with a model-not-supported / model-metadata error event.
            # Raise a structured BrainModelNotFoundError carrying the
            # same suggested_fix copy the validator emits pre-write.
            if model and _detect_model_not_found(stdout, cp.returncode):
                from vexis_agent.core.model_validator import (
                    CODEX_MODEL_NOT_FOUND_FIX_TEMPLATE,
                )
                raise BrainModelNotFoundError(
                    subsystem=subsystem or "<unknown>",
                    model_id=model,
                    brain_kind="codex",
                    suggested_fix=CODEX_MODEL_NOT_FOUND_FIX_TEMPLATE.format(
                        model_id=model,
                        subsystem=subsystem or "this subsystem",
                    ),
                )

            # Aux callers consume ``stdout`` as the agent's reply text.
            # Extract concatenated agent_message texts; fall back to the
            # raw stdout when no events parse (better noisy than empty).
            extracted = _extract_agent_text_from_stdout(stdout)
            return AuxResult(
                stdout=extracted if extracted else stdout,
                stderr=stderr,
                returncode=cp.returncode,
            )

        return await asyncio.to_thread(_run)

    # ─── session model ───────────────────────────────────────────

    def session_token(self) -> str | None:
        """The current codex thread id once ``respond`` has harvested
        it, or the SessionStore placeholder before that first call.
        Opaque per the ABC contract."""
        return self._session.get()

    def rotate_session(self) -> str:
        """Mint a fresh placeholder. ``SessionStore.rotate`` flips
        ``initialized`` back to False so the next ``respond`` spawns
        without ``resume`` and harvests a new thread id."""
        return self._session.rotate()

    # ─── transcript readback ─────────────────────────────────────

    def _iter_rollout_files(self) -> Iterator[Path]:
        """Yield rollout JSONLs under the sessions dir. Returns nothing
        when the dir is absent (fresh install / test isolation)."""
        root = codex_sessions_dir()
        if not root.exists():
            return
        yield from root.glob("**/rollout-*.jsonl")

    @staticmethod
    def _read_session_meta(path: Path) -> dict | None:
        """Parse a rollout's line-0 ``session_meta`` payload, or None
        when the file is empty / unreadable / the first line isn't a
        session_meta."""
        try:
            with path.open("r", encoding="utf-8") as fh:
                first = fh.readline()
        except OSError:
            return None
        if not first.strip():
            return None
        try:
            obj = json.loads(first)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(obj, dict) or obj.get("type") != "session_meta":
            return None
        payload = obj.get("payload")
        return payload if isinstance(payload, dict) else None

    def _rollout_path_for(self, session_id: str) -> Path | None:
        """Find the rollout JSONL whose session_meta id matches.

        Codex embeds the thread id in the filename
        (``rollout-<ts>-<uuid>.jsonl``), so the fast path is a
        filename-suffix match with no file reads; the line-0
        session_meta scan is the fallback for any rollout whose
        naming ever diverges. Hits are cached — the curator calls
        this once per eligible session per tick, and rollouts never
        change identity once written."""
        cached = self._rollout_path_cache.get(session_id)
        if cached is not None and cached.exists():
            return cached
        hit: Path | None = None
        suffix = f"-{session_id}.jsonl"
        for path in self._iter_rollout_files():
            if path.name.endswith(suffix):
                hit = path
                break
        if hit is None:
            for path in self._iter_rollout_files():
                meta = self._read_session_meta(path)
                if meta is not None and str(meta.get("id")) == session_id:
                    hit = path
                    break
        if hit is not None:
            self._rollout_path_cache[session_id] = hit
        return hit

    def iter_session_metas(self) -> Iterator[SessionMeta]:
        """Enumerate codex rollouts whose ``cwd`` matches this brain's
        workspace, newest-first.

        Yields ``SessionMeta`` with ``jsonl_path=None`` (opencode-style
        flag: "route reads through ``brain.iter_messages``"). Returns
        nothing when the sessions dir is absent — the curator scan
        continues silently rather than crashing on a fresh install."""
        workspace_str = str(self._workspace.resolve())
        collected: list[tuple[float, SessionMeta]] = []
        for path in self._iter_rollout_files():
            meta = self._read_session_meta(path)
            if meta is None:
                continue
            if str(meta.get("cwd")) != workspace_str:
                continue
            sid = meta.get("id")
            if not sid:
                continue
            ts = _parse_codex_timestamp(meta.get("timestamp"))
            count, last_ts = self._scan_rollout_activity(path)
            sort_key = (last_ts or ts or datetime.fromtimestamp(
                0, tz=timezone.utc
            )).timestamp()
            collected.append((
                sort_key,
                SessionMeta(
                    session_uuid=str(sid),
                    jsonl_path=None,
                    last_message_timestamp=last_ts or ts,
                    message_count_estimate=count,
                ),
            ))
        collected.sort(key=lambda pair: pair[0], reverse=True)
        for _, meta in collected:
            yield meta

    @staticmethod
    def _scan_rollout_activity(path: Path) -> tuple[int, datetime | None]:
        """Count user/agent ``event_msg`` lines and track the newest
        message timestamp. Cheap enough — rollouts are small."""
        count = 0
        last_ts: datetime | None = None
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("type") != "event_msg":
                        continue
                    payload = obj.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("type") not in (
                        "user_message", "agent_message",
                    ):
                        continue
                    count += 1
                    ts = _parse_codex_timestamp(payload.get("timestamp"))
                    if ts is not None and (last_ts is None or ts > last_ts):
                        last_ts = ts
        except OSError:
            return count, last_ts
        return count, last_ts

    def iter_messages(self, session_id: str) -> Iterator[TranscriptMessage]:
        """Stream user + assistant turns from one codex rollout.

        Parses ``event_msg`` lines of subtype ``user_message`` /
        ``agent_message`` into ``TranscriptMessage``. Corrupt lines are
        skipped. Returns nothing for an unknown session id or an
        unreadable file — same "empty == skip" semantics the other
        brains use."""
        path = self._rollout_path_for(session_id)
        if path is None:
            return
        meta = self._read_session_meta(path)
        session_ts = _parse_codex_timestamp(
            meta.get("timestamp") if meta else None
        ) or datetime.fromtimestamp(0, tz=timezone.utc)
        try:
            fh = path.open("r", encoding="utf-8")
        except OSError:
            return
        with fh:
            for idx, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "event_msg":
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict):
                    continue
                ptype = payload.get("type")
                if ptype == "user_message":
                    role = "user"
                elif ptype == "agent_message":
                    role = "assistant"
                else:
                    continue
                text = payload.get("message")
                if not isinstance(text, str):
                    text = ""
                ts = _parse_codex_timestamp(
                    payload.get("timestamp")
                ) or session_ts
                yield TranscriptMessage(
                    role=role,
                    text=text,
                    timestamp=ts,
                    uuid=f"{session_id}:{idx}",
                    tool_calls=(),
                    raw=payload,
                )

    def is_brain_owned_session(self, session_id: str) -> bool:
        """Curator-recursion guard: True when the first user turn opens
        with one of vexis's canonical prompt prefixes."""
        from vexis_agent.core.goal_judge import GOAL_JUDGE_PROMPT_PREFIX
        from vexis_agent.core.kanban.constants import KANBAN_WORKER_PREFIX
        from vexis_agent.core.learning_review import (
            CURATOR_REVIEW_PROMPT_PREFIX,
        )

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

    # ─── MCP config wiring ───────────────────────────────────────

    def write_mcp_config(self, servers: list[McpServerSpec]) -> Path:
        """Write vexis's MCP servers to ``$CODEX_HOME/vexis.config.toml``
        — the ``vexis`` profile codex layers via ``--profile vexis``.

        Replace-all semantics: the file is vexis-owned (like
        claude-code's ``.mcp.json``), so no user-entry merge is needed
        — the profile IS the namespace. Per-entry translation goes
        through :func:`mcp_spec_to_codex_entry`; the tiny hand-rolled
        emitter renders the ``[mcp_servers.<name>]`` tables. Atomic
        tmp + rename. Returns the path written."""
        home = codex_home()
        home.mkdir(parents=True, exist_ok=True)
        path = home / _VEXIS_PROFILE_FILE

        entries: dict[str, dict] = {
            spec.name: mcp_spec_to_codex_entry(spec) for spec in servers
        }
        body = _emit_mcp_servers_toml(entries)
        content = (body + "\n") if body else ""

        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
        return path

    # ─── file conventions ────────────────────────────────────────

    def instruction_file_name(self) -> str:
        return "AGENTS.md"

    def instruction_search_paths(self, workspace: Path) -> list[Path]:
        """codex reads ``AGENTS.md`` from the workspace, plus a global
        at ``$CODEX_HOME/AGENTS.md``. Returned in lookup order so
        ``/status`` can render where instructions are read from."""
        return [workspace / "AGENTS.md", codex_home() / "AGENTS.md"]

    # ─── lifecycle ───────────────────────────────────────────────

    async def healthcheck(self) -> BrainHealth:
        """Confirm ``codex`` is on PATH and logged in.

        ``codex login status`` exits 0 when authenticated (best-effort
        — a non-zero from a future CLI change surfaces as an actionable
        login hint rather than a hard failure)."""
        if shutil.which("codex") is None:
            return BrainHealth(
                ok=False,
                error="`codex` not on PATH",
                hints=[
                    "Install with: npm install -g @openai/codex",
                    "Then: codex login",
                ],
            )
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["codex", "login", "status"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return BrainHealth(
                    ok=False,
                    error="codex is installed but not authenticated",
                    hints=["Run: codex login"],
                )
        except (subprocess.TimeoutExpired, OSError):
            # Best-effort — return ok if the binary is present.
            pass
        return BrainHealth(ok=True, error=None, hints=[])

    async def kill_in_flight(self) -> None:
        """No-op for parity — ``/cancel`` kills via
        ``RunningTasks.cancel()`` against the attached proc."""
        return None

    # ─── conversation compression (detection-only stub) ──────────

    async def compress_if_needed(self, session_id: str) -> bool:
        """Detection-only: run the trigger so long-session diagnostics
        surface in the log, but DO NOT rewrite the rollout JSONL yet
        (deferred — codex writes an append-only rollout the running
        process owns; a safe in-place rewrite is a follow-up). Always
        returns False so the handler's pre-turn call is a no-op."""
        from vexis_agent.core.brain.compressor import (
            CompressionInputs,
            should_compress,
        )
        from vexis_agent.core.yaml_config import (
            compression_enabled,
            compression_threshold_ratio,
            compression_threshold_turns,
        )

        if not compression_enabled():
            return False

        messages: list[tuple[str, str]] = []
        try:
            for msg in self.iter_messages(session_id):
                if msg.role in ("user", "assistant") and msg.text:
                    messages.append((msg.role, msg.text))
        except Exception:  # pragma: no cover - defensive
            log.debug(
                "compress_if_needed(codex, %s): iter_messages failed",
                session_id, exc_info=True,
            )
            return False
        if not messages:
            return False

        system_prompt = self._system_prompt_for(session_id)
        decision = should_compress(
            CompressionInputs(
                messages=messages,
                system_prompt=system_prompt,
                tool_schemas_text="",
                threshold_ratio=compression_threshold_ratio(),
                threshold_turns=compression_threshold_turns(),
            )
        )
        if decision.compress:
            log.warning(
                "compress_if_needed(codex, %s): would compress but the "
                "codex rollout rewrite is not yet implemented — %s.",
                session_id, decision.reason,
            )
        else:
            log.debug(
                "compress_if_needed(codex, %s): %s",
                session_id, decision.reason,
            )
        return False

    # ─── Issue #9: file-mutation verifier footer plumbing ────────

    async def _maybe_take_snapshot(self):
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
        self._files_changed_by_chat[chat_id] = changed

    def consume_files_changed(self, chat_id: int) -> list[str]:
        return self._files_changed_by_chat.pop(chat_id, [])

    def peek_files_changed(self, chat_id: int) -> list[str]:
        return list(self._files_changed_by_chat.get(chat_id, []))


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _sandbox_flags(
    allow_tools: bool, allowed_tools: list[str] | None,
) -> list[str]:
    """Map the aux tool policy onto codex's coarse sandbox flag (codex
    has no per-tool allowlist).

      - text-only (``allowed_tools=[]``, or no list + ``allow_tools``
        False) → ``-s read-only``
      - allowlist WITHOUT any shell/web tool → ``-s workspace-write``
      - allowlist WITH a shell/web tool, or ``allow_tools=True``
        unrestricted → ``--dangerously-bypass-approvals-and-sandbox``
    """
    if allowed_tools is not None:
        if not allowed_tools:
            return ["-s", "read-only"]
        if any(t in _SHELL_WEB_TOOLS for t in allowed_tools):
            return ["--dangerously-bypass-approvals-and-sandbox"]
        return ["-s", "workspace-write"]
    if allow_tools:
        return ["--dangerously-bypass-approvals-and-sandbox"]
    return ["-s", "read-only"]


def _parse_codex_timestamp(value: object) -> datetime | None:
    """Parse a codex rollout timestamp into a tz-aware UTC datetime.
    Accepts an ISO-8601 string (``...Z`` normalised) or a numeric
    epoch (seconds or milliseconds). None on anything unparseable."""
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    if isinstance(value, (int, float)):
        # Heuristic: values past ~year 2001 in ms are >1e12.
        seconds = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


__all__ = [
    "BRAIN_TIMEOUT_SECONDS",
    "BrainAuthRequired",
    "BrainCancelled",
    "BrainError",
    "BrainNotInstalled",
    "BrainTimeoutError",
    "CodexBrain",
    "SessionLost",
    "codex_home",
    "codex_sessions_dir",
    "set_codex_sessions_dir_override",
]
