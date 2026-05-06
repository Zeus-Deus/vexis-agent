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
from core.memory import MemoryStore
from core.paths import memories_dir, skills_dir
from core.running_tasks import RunningTasks
from core.safety import DESTRUCTIVE_PATTERNS
from core.sessions import SessionStore
from core.skills import build_skills_index_block
from core.status import StatusFile, extract_tool_target

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
    "BrainTimeoutError",
    "ClaudeCodeBrain",
    "McpServerSpec",
    "SessionLost",
    "audit_destructive_mentions",
    "build_system_prompt",
]

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

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
    from core.relationships.store import (
        format_relationships_for_system_prompt,
    )

    soul = _read_markdown(workspace / "SOUL.md") or DEFAULT_SOUL
    capabilities = _read_markdown(_PROJECT_ROOT / "CAPABILITIES.md")
    parts: list[str] = [soul]
    if capabilities:
        parts.append(capabilities)

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
) -> str:
    """Consume the brain's stream-json stdout, updating ``status_file``
    on every tool_use event and returning the assistant's final text.

    The final text is the ``result`` field of the ``result`` event
    Claude Code emits last. Malformed lines are logged and skipped so a
    single corrupt event can't break the whole turn — historically
    rare but we shouldn't lose a real reply over one bad line.
    """
    final_text = ""
    if stream is None:
        return final_text
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
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name") or "Tool"
                target = extract_tool_target(name, block.get("input") or {})
                status_file.record_tool(name, target)
        elif kind == "result":
            result_text = event.get("result")
            if isinstance(result_text, str):
                final_text = result_text
    return final_text


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

    async def respond(self, message: str, chat_id: int) -> str:
        log.info("Brain.respond starting for chat %d", chat_id)
        session_id = self._session.get()
        # First call pins the UUID with --session-id; subsequent calls resume it.
        if self._session.is_initialized():
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
        # for each tool use and the call would hang. Step 6.5 will add a
        # PreToolUse hook that consults core.safety for hard enforcement.
        argv += ["--permission-mode", "bypassPermissions"]
        log.debug(
            "Spawning claude -p (%s=%s, cwd=%s)",
            session_flag[0],
            session_id,
            self._workspace,
        )

        # Reserve before spawning so a /cancel arriving while claude -p is
        # starting up is captured by the slot and surfaced via attach() →
        # False below — closes the spawn-vs-register race.
        reservation = await self._running_tasks.reserve(chat_id)
        # Propagate chat_id to spawned tools (vexis-bg etc.) so they can
        # route notifications back to the right Telegram conversation.
        env = {**os.environ, "VEXIS_CHAT_ID": str(chat_id)}

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
                final_text = await stdout_task
            except Exception:
                log.exception("Brain stdout reader failed for chat %d", chat_id)
                final_text = ""
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
                # Claude Code's wording has varied across versions; substring
                # match is more robust than pinning an exact string.
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
                raise BrainError(
                    f"claude -p exited {proc.returncode}: {err or '(no stderr)'}"
                )
        finally:
            status_file.delete()
            await self._running_tasks.unregister(chat_id)

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
        from core.yaml_config import model_for_tier

        argv: list[str] = ["claude", "-p"]
        model_id = model_for_tier("claude-code", model_tier)
        if model_id:
            argv += ["--model", model_id]
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
        from core.transcripts import iter_session_metas

        return iter_session_metas(self._workspace)

    def iter_messages(self, session_id: str) -> Iterator:
        """Stream user+assistant turns from one session JSONL.
        Delegates to ``core.transcripts.iter_messages`` after
        translating ``session_id`` (a UUID) into the JSONL path."""
        from core.transcripts import claude_session_jsonl_dir, iter_messages

        jsonl_path = claude_session_jsonl_dir(self._workspace) / f"{session_id}.jsonl"
        return iter_messages(jsonl_path)

    def is_brain_owned_session(self, session_id: str) -> bool:
        """Content-prefix check against the first user-turn of the
        session JSONL. Delegates to ``core.transcripts._is_curator_owned``
        — the recursion guard the learning curator already uses."""
        from core.transcripts import _is_curator_owned, claude_session_jsonl_dir

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
