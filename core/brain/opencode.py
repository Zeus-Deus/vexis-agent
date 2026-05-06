"""Subprocess wrapper around the OpenCode CLI (``opencode run``).

Phase C of the brain abstraction (.plans/brain-abstraction-research.md
§5 Day 3). Provides ``OpenCodeBrain`` — a sibling implementation of
``ClaudeCodeBrain`` against the OpenCode binary. Foreground turns
and aux spawns work end-to-end on Day 3; transcript readback
(``iter_session_metas``, ``iter_messages``, ``is_brain_owned_session``)
returns empty/False stubs until Day 4 lands the SQLite reader
against ``~/.local/share/opencode/opencode.db``.

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

Day 3 caveats. Session resume is single-call only: every
``respond()`` spawns a fresh session because we don't yet harvest
the OpenCode-generated session id. Day 4 wires
``--session <id>`` for subsequent calls. Day 3 also stubs the
transcript-readback methods (curator scan returns empty, no crash);
real SQL reader lands Day 4.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
from collections.abc import Iterator
from pathlib import Path

from core.brain.base import (
    AuxResult,
    Brain,
    BrainAuthRequired,
    BrainCancelled,
    BrainError,
    BrainHealth,
    BrainNotInstalled,
    BrainTimeoutError,
    McpServerSpec,
    SessionLost,
)
from core.running_tasks import RunningTasks
from core.sessions import SessionStore
from core.status import StatusFile, extract_tool_target

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


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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
    from core.brain.claude_code import DEFAULT_SOUL
    from core.memory import MemoryStore
    from core.paths import memories_dir
    from core.relationships.store import (
        format_relationships_for_system_prompt,
    )

    soul = _read_markdown(workspace / "SOUL.md") or DEFAULT_SOUL
    capabilities = _read_markdown(_PROJECT_ROOT / "CAPABILITIES.md")
    parts: list[str] = [soul]
    if capabilities:
        parts.append(capabilities)

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


async def _read_opencode_event_stream(
    stream: asyncio.StreamReader | None,
    status_file: StatusFile,
    target_session_id: str | None,
) -> tuple[str, str | None]:
    """Consume ``opencode run --format json`` stdout, update the
    status file on tool events, accumulate the assistant's final
    text, and return ``(final_text, harvested_session_id)``.

    OpenCode's JSON event shape (verified at
    ``~/projects/_references/anomalyco-opencode/packages/opencode/src/cli/cmd/run.ts:435``):

        {"type": "<type>", "timestamp": <ms>, "sessionID": "<id>", ...data}

    Event types we care about:

      - ``tool_use``: ``{part: ToolPart}`` — emitted on tool
        completion or error. Updates the status file.
      - ``text``: ``{part: {text: "..."}}`` — emitted when a text
        block's ``time.end`` is set. Concatenated into the final
        reply.
      - ``error``: ``{error: {...}}`` — soft error mid-stream;
        logged, doesn't terminate.
      - ``session.status`` with ``status.type == "idle"`` and
        matching ``sessionID`` — terminal. We break on this.

    ``target_session_id`` is the id we're listening for. On the
    first call (no prior session), pass ``None``; we harvest the
    first event's ``sessionID`` and lock onto it. On resumes, pass
    the stored id so events from unrelated concurrent sessions
    (rare but possible) are ignored.

    Return ``(final_text, harvested_session_id)``. The harvested
    id is None when the stream produced no events (e.g. the brain
    crashed before emitting anything) and the original
    ``target_session_id`` is None.
    """
    final_text_parts: list[str] = []
    locked_session_id: str | None = target_session_id
    if stream is None:
        return "", locked_session_id
    while True:
        try:
            line = await stream.readline()
        except Exception:
            log.warning("opencode stream readline raised", exc_info=True)
            break
        if not line:
            break
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue

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
                # Terminal — but only when sessionID matches.
                # ``opencode run`` emits this just before exit; the
                # process will be reaped by ``proc.wait``.
                break
        elif kind == "error":
            err = event.get("error")
            log.warning("opencode stream error event: %r", err)

    return "".join(final_text_parts), locked_session_id


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

    # ─── foreground turn ─────────────────────────────────────────

    async def respond(self, message: str, chat_id: int) -> str:
        log.info("OpenCodeBrain.respond starting for chat %d", chat_id)
        session_id = self._session.get()

        # Day 3 scaffold: every call is a fresh session because we
        # don't yet harvest + persist the OpenCode-generated id
        # across calls. Day 4 wires --session <id> for resumes.
        # The ``session_id`` from SessionStore is currently a UUID
        # we won't pass to opencode (it doesn't accept
        # caller-supplied ids); kept for forward-compat with the
        # Day-4 resume path.
        is_initialized = self._session.is_initialized()

        from core.yaml_config import model_for_tier
        model = model_for_tier("opencode", None)  # default: brain's foreground choice

        system_prompt = self._system_prompt_for(session_id)
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
            "--title", f"vexis-chat-{chat_id}",
            message,
        ]
        if is_initialized:
            # Day 4: argv += ["--session", session_id]
            # Day 3 placeholder: comment-only.
            pass

        env = {**os.environ, "VEXIS_CHAT_ID": str(chat_id)}
        env["OPENCODE_CONFIG_CONTENT"] = config_content

        log.debug(
            "Spawning opencode run (cwd=%s, agent=%s, fresh=True)",
            self._workspace, VEXIS_AGENT_NAME,
        )

        reservation = await self._running_tasks.reserve(chat_id)
        status_file = StatusFile(chat_id)
        status_file.start()

        final_text = ""
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
                    proc.stdout, status_file, target_session_id=None,
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
                # ``harvested_id`` (Day 4) will be persisted via
                # ``self._session.set`` once transcript readback lands;
                # for now we drop it on the floor on purpose. The
                # leading underscore silences F841.
                final_text, _harvested_id = await stdout_task
            except Exception:
                log.exception(
                    "OpenCode stdout reader failed for chat %d", chat_id
                )
                final_text, _harvested_id = "", None
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

            if proc.returncode != 0:
                err = stderr_bytes.decode(errors="replace").strip()
                # Best-effort session-lost detection. Day 4 will
                # pin the exact ``session.error`` event payload
                # shape; Day 3 just looks for the substring in
                # stderr.
                if (
                    is_initialized
                    and "session" in err.lower()
                    and "not found" in err.lower()
                ):
                    old = self._session.get()
                    new = self._session.rotate()
                    log.warning(
                        "OpenCode lost session %s; rotated to %s", old, new
                    )
                    raise SessionLost(
                        "OpenCode session was lost. Rotated to new session."
                    )
                raise BrainError(
                    f"opencode run exited {proc.returncode}: "
                    f"{err or '(no stderr)'}"
                )
        finally:
            status_file.delete()
            await self._running_tasks.unregister(chat_id)

        # Day 3: always mark initialised after first success so
        # Day 4 wiring of --session <id> activates correctly. The
        # ``harvested_id`` will be persisted in Day 4.
        if not self._session.is_initialized():
            self._session.mark_initialized()

        return (final_text or "").strip()

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
    ) -> AuxResult:
        """Run an aux call against ``opencode run``.

        Tier resolution via ``core.yaml_config.model_for_tier`` for
        ``"opencode"``. Builds ``OPENCODE_CONFIG_CONTENT`` carrying
        a stripped-down agent (no skills index, no MCP — just the
        aux prompt and the tier-resolved model). Sync subprocess
        wrapped in ``asyncio.to_thread`` for the async contract.
        """
        from core.yaml_config import model_for_tier

        argv: list[str] = [
            "opencode", "run",
            "--format", "json",
            "--agent", VEXIS_AUX_AGENT_NAME,
            "--dangerously-skip-permissions",
            prompt,
        ]
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
        """Day 3 placeholder: returns whatever ``SessionStore`` has
        (a UUID generated at first construction). Day 4 will swap
        this for the OpenCode-generated session id harvested from
        ``respond``'s first event."""
        return self._session.get()

    def rotate_session(self) -> str:
        """Mint a fresh placeholder UUID. After a Day-4
        ``SessionLost`` recovery, this resets the ``--session``
        flag so the next ``respond`` call creates a new session.
        Returns the new placeholder; the real id arrives on the
        first event of the next ``respond``."""
        return self._session.rotate()

    # ─── transcript readback (Day 3 stubs) ───────────────────────

    def iter_session_metas(self) -> Iterator:
        """Day 3: returns an empty iterator. Curator scans see no
        sessions — correct, because OpenCode hasn't run any vexis
        sessions yet at Day 3 (the first run will write a row to
        ``opencode.db`` but Day 3 lacks the SQL reader). Day 4
        swaps this for ``SELECT id, time_updated FROM session
        WHERE directory = ?`` against
        ``~/.local/share/opencode/opencode.db``."""
        return iter(())

    def iter_messages(self, session_id: str) -> Iterator:
        """Day 3: empty iterator. Day 4 swaps for SQL read."""
        return iter(())

    def is_brain_owned_session(self, session_id: str) -> bool:
        """Day 3: defensive ``False`` (treats unknown sessions as
        not brain-owned). The recursion guard then doesn't
        accidentally skip a real session. Day 4 swaps for the
        content-prefix check against the first user message's
        first text part in the ``message`` table."""
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
]
