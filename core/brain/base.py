"""Brain abstraction — contract every brain implementation honours.

Vexis runs on top of an agent CLI. ``Brain`` is the contract: foreground
turn (``respond``), aux spawn (``spawn_aux``), session model
(``session_token`` / ``rotate_session``), transcript readback
(``iter_session_metas`` / ``iter_messages`` / ``is_brain_owned_session``),
MCP config wiring (``write_mcp_config``), file conventions
(``instruction_file_name`` / ``instruction_search_paths``), and lifecycle
(``healthcheck`` / ``kill_in_flight``).

Phase A (Day 1) introduces this module as a refactor: ``BrainClaudeCode``
formally implements ``Brain`` with byte-identical external behaviour.
Phase B will route every aux-spawn site through ``Brain.spawn_aux`` and
every transcripts read through ``Brain.iter_session_metas`` /
``iter_messages`` (today the curator & friends call out directly to
``claude -p`` and to ``~/.claude/projects/`` — the abstraction is in
place but not yet load-bearing). Phase C will add ``BrainOpenCode``.

Design citations: ``.plans/brain-abstraction-research.md`` §4 (the ABC
contract) and §5 (the phased rollout). The exception hierarchy and
``BrainEvent`` union are documented at the bottom of §4.

Methods that don't have natural Phase-A implementations
(``spawn_aux``, ``write_mcp_config``) raise ``NotImplementedError`` on
``BrainClaudeCode`` until Phase B / C lands; ``BrainNull`` implements
every method as a no-op or canned response so the unit-test suite has
zero subprocess dependencies. The cross-brain contract test
(``tests/test_brain_contract.py``) pins both shapes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Union


# ──────────────────────────────────────────────────────────────────
# Exception hierarchy
# ──────────────────────────────────────────────────────────────────


class BrainError(RuntimeError):
    """Generic brain failure. Base of the hierarchy.

    Transport-layer code catches this for "something broke; tell the
    user, log details." Subclasses carry more specific recovery
    semantics (timeout retry vs session-lost rotate vs cancel-acked).
    """


class BrainTimeoutError(BrainError):
    """The brain subprocess didn't exit within the configured timeout
    (``BRAIN_TIMEOUT_SECONDS``, default 1800 s). The process was killed
    via ``kill_in_flight``-shaped logic before the exception was raised.
    """


class BrainCancelled(BrainError):
    """The brain subprocess was cancelled via ``/cancel`` (or the
    ``/goal pause`` path for goal-continuation messages). Surfaced to
    the caller so the transport layer can render the appropriate
    cancel acknowledgement instead of a generic error.
    """


class SessionLost(BrainError):
    """The brain's session is no longer accessible. Vexis must rotate
    the session token via ``rotate_session()`` and tell the user the
    previous conversation was lost; do NOT retry the failing message
    on the new session — restart fresh.

    For ``BrainClaudeCode``: triggered by claude-code's
    "No conversation found" stderr. For ``BrainOpenCode``: triggered
    by a typed ``session.error`` event with a session-not-found tag.
    """


class BrainNotInstalled(BrainError):
    """The brain binary is not on PATH. Raised by ``healthcheck`` and
    by ``respond`` / ``spawn_aux`` on the first failed spawn if
    ``healthcheck`` wasn't called at startup. Carries actionable
    install hints in its message string.
    """


class BrainAuthRequired(BrainError):
    """The brain binary is installed but not authenticated. Raised by
    ``healthcheck`` and by ``respond`` / ``spawn_aux`` on the first
    failed spawn that returns a recognisable auth-error stderr. Carries
    actionable login hints in its message string.
    """


class BrainModelNotFoundError(BrainError):
    """The brain CLI rejected the configured model id at spawn time.

    Day 2 of model-management UX (model UX research §4 "Spawn-site
    error vocabulary") — the structured backstop the validator's
    rule 4/5 catch-all relies on. The validator catches the same
    condition pre-write at every UX surface; this exception is the
    safety net for cases the validator missed (stale claude-code
    discovery list, opencode discovery cache empty, validator rule
    edge case in flight).

    Per-brain detection:

      - **claude-code**: stdout substring ``"There's an issue with
        the selected model"`` AND ``returncode != 0``. Verified
        empirically — claude-code prints the diagnostic to STDOUT
        (not stderr) and exits 1.
      - **opencode**: typed JSON event ``{"type":"error","error":{
        "name":"UnknownError","data":{"message":"Model not
        found:..."}}}`` parsed from ``opencode run --format json``
        stdout. Verified empirically — opencode exits 0 even on
        bad model in JSON mode; the typed event is the reliable
        signal.

    Attributes are populated by the per-brain spawn_aux on detection
    so every consumer (curator log, dashboard error toast, slash-
    command failure reply) shows the same actionable text the
    validator would have shown if it'd caught the case pre-write.
    The ``suggested_fix`` field carries the canonical copy imported
    from ``core.model_validator``'s template constants — single
    source of truth across validator and backstop.

    This exception is NOT a subclass-of-subclass — it sits at the
    same level as ``SessionLost`` / ``BrainAuthRequired`` because
    the recovery semantic is distinct: caller should NOT retry
    (the model id won't fix itself), should surface the
    suggested_fix to the user, and should expect the same error
    on the next spawn until the user runs ``/model set
    <subsystem> <new-id>``.
    """

    def __init__(
        self,
        *,
        subsystem: str,
        model_id: str,
        brain_kind: str,
        suggested_fix: str,
    ) -> None:
        self.subsystem = subsystem
        self.model_id = model_id
        self.brain_kind = brain_kind
        self.suggested_fix = suggested_fix
        super().__init__(
            f"Model {model_id!r} rejected by {brain_kind} for "
            f"subsystem {subsystem!r}. Fix: {suggested_fix}"
        )


# ──────────────────────────────────────────────────────────────────
# BrainEvent — normalised event stream
#
# Both brains' native outputs convert into this shape. ``respond()`` is
# *not* abstract over the event stream (it returns final text) — events
# are an internal implementation detail consumed by the brain itself
# (StatusFile updates, tool tracking). Phase A defines the dataclasses
# so a future ``Brain.respond_streaming`` can be added additively.
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SessionEstablished:
    """Emitted exactly once per ``respond()`` call after the brain
    confirms the session id. claude-code: the ``--session-id`` we
    pinned. opencode: the id opencode generated, extracted from the
    first event's ``sessionID`` field."""

    session_id: str


@dataclass(frozen=True)
class TextDelta:
    """Streaming text chunk. Brains MAY emit zero or one ``TextEnd``
    per logical text block, but they MUST emit at least one
    ``TextDelta`` or ``TextEnd`` before the ``Finished`` event so the
    caller can accumulate the assistant reply."""

    delta: str


@dataclass(frozen=True)
class TextEnd:
    """Marks a text block as complete. ``text`` is the cumulative text
    of THIS block only (not the whole response). Brains emitting
    ``TextEnd`` are expected to emit it instead of (or after) the
    final ``TextDelta``."""

    text: str


@dataclass(frozen=True)
class ToolStart:
    """Tool call announced. ``name`` is canonical lowercase
    (``read``/``edit``/``shell``/...) — brains normalise case
    (claude-code's ``Read`` → ``read``)."""

    tool_id: str  # opaque per-call id from the brain
    name: str
    input: dict


@dataclass(frozen=True)
class ToolEnd:
    """Tool call finished or failed."""

    tool_id: str
    status: Literal["completed", "error"]
    output: str | None
    error: str | None


@dataclass(frozen=True)
class Finished:
    """Terminal event. ``text`` is the full assistant reply
    (concatenation of all ``TextDelta`` / ``TextEnd`` content).
    ``reason`` explains why the stream ended."""

    text: str
    reason: Literal["idle", "error", "cancelled", "timeout"]


@dataclass(frozen=True)
class StreamError:
    """Non-terminal error — the stream may continue or may end with
    ``Finished`` after this. For terminal errors brains should emit
    ``Finished(reason="error")`` instead so callers don't have to
    handle two terminal shapes."""

    message: str


BrainEvent = Union[
    SessionEstablished,
    TextDelta,
    TextEnd,
    ToolStart,
    ToolEnd,
    Finished,
    StreamError,
]


# ──────────────────────────────────────────────────────────────────
# Aux + healthcheck + MCP dataclasses
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuxResult:
    """Result of one ``spawn_aux`` call. Used by curator, judges,
    extractors, and classifiers — each consumes ``stdout`` and treats
    a non-zero ``returncode`` as failure."""

    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True)
class BrainHealth:
    """Result of ``healthcheck``. ``ok=True`` means the brain binary
    is installed AND authenticated. ``hints`` carries actionable
    suggestions for the user (e.g. "Install with: …", "Run: claude
    /login")."""

    ok: bool
    error: str | None
    hints: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class McpServerSpec:
    """Canonical MCP server description. Per-brain writers translate
    this into the brain's native config shape (claude-code's
    ``mcpServers: {name: {command, args, env}}`` vs opencode's
    ``mcp: {name: {type, command: [argv...], environment, ...}}``).

    ``command`` is the executable; ``args`` are positional args that
    follow it. ``env`` is per-server environment overrides applied at
    spawn time by the brain."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────
# Brain ABC
# ──────────────────────────────────────────────────────────────────


class Brain(ABC):
    """Vexis's brain contract. See module docstring for the design.

    Every implementation must provide all abstract methods. Methods
    that have no Phase-A implementation on ``BrainClaudeCode`` (e.g.
    ``spawn_aux``, ``write_mcp_config``) raise ``NotImplementedError``
    until Phase B/C wires them; the abstraction is still load-bearing
    for typing and for ``BrainNull`` to fully exercise the transport
    layer in unit tests.
    """

    # ─── foreground turn ─────────────────────────────────────────

    async def astream(
        self,
        message: str,
        chat_id: int,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
    ) -> AsyncIterator[str | dict]:
        """Streaming variant of :meth:`respond`. Yields incremental
        text chunks as the model generates them.

        **Yield types** (callers must accept both):

        - ``str`` — a text delta (``content_block_delta.text_delta``
          on claude-code; whatever the brain produces incrementally).
          Concatenate these to reconstruct the assistant's reply.
        - ``dict`` — a tool-use event with shape
          ``{"type": "tool", "name": str, "target": str | None}``.
          Surfaced to the chat UI as inline "Reading src/foo.py" /
          "Running git status" lines so the user sees the brain
          working through its tools instead of staring at a pulse
          for 30+ seconds. Tool events do NOT contribute to the
          assistant's text reply — they're a separate UX channel.

        Default implementation here delegates to ``respond`` and
        yields the full reply once at the end — non-streaming brains
        still satisfy the contract (no tool events, just a single
        text yield). Implementations that natively stream (claude-code
        via ``--include-partial-messages``, future opencode) override
        this to yield per-delta plus tool events as they fire.

        Same per-turn override semantics as ``respond``: ``model``
        and ``reasoning_level`` flow through identically. Telegram
        and the text-chat tab don't call this — the streaming SSE
        route on the dashboard is the only caller today. Same
        exception surface: BrainTimeout, BrainCancelled, SessionLost,
        BrainError.

        Marked NOT-abstract so concrete brains are free to inherit
        the default fallback. Brains that override it MUST yield at
        least one text chunk on success (empty reply → yield "") so
        downstream callers can rely on that for the "done" signal.
        """
        reply = await self.respond(
            message, chat_id,
            model=model, reasoning_level=reasoning_level,
        )
        yield reply

    @abstractmethod
    async def respond(
        self,
        message: str,
        chat_id: int,
        *,
        model: str | None = None,
        reasoning_level: str | None = None,
    ) -> str:
        """Run one foreground turn. Returns the assistant's final text.

        ``model`` is an optional per-turn override. ``None`` (the
        default) means "use the brain's account default" — the
        canonical foreground behaviour, preserved bit-for-bit so
        Telegram and the text-chat tab keep their existing semantics.
        When set, the brain spawns its CLI with ``--model <id>`` for
        this turn only. Used by voice call mode (``voice.call_mode.model``
        config knob) so the call surface can run a faster model
        without shifting every other foreground turn.

        ``reasoning_level`` is the matching per-turn reasoning override
        (``low``/``medium``/``high``/``max`` for claude-code, where it
        translates to ``--effort``; ``low``/``medium``/``high`` for
        opencode where it becomes ``--variant``). ``None`` means
        "no flag — let the model use its default". Should only be set
        when the chosen ``model`` actually supports reasoning levels;
        the dashboard validates this upstream by reading capabilities
        from ``core.model_discovery``. Same per-turn isolation as
        ``model`` — Telegram and text-chat always pass ``None``.

        Side effects: writes to the per-chat ``StatusFile`` for
        ``/status``, registers the running subprocess with
        ``RunningTasks`` for ``/cancel``.

        Raises:
            BrainTimeoutError: subprocess didn't exit in time.
            BrainCancelled: ``/cancel`` fired during the turn.
            SessionLost: brain session is no longer accessible.
            BrainNotInstalled: brain binary missing from PATH.
            BrainAuthRequired: brain binary present but not authed.
            BrainError: catch-all for other subprocess failures.
        """

    # ─── system prompt assembly ──────────────────────────────────

    @abstractmethod
    def build_system_prompt(self) -> str:
        """Compose the full system prompt for this brain. Default
        composition: SOUL.md + CAPABILITIES.md + memory blocks +
        relationships block + skills index. Subclasses MAY drop
        sections that the brain duplicates natively (e.g.
        ``BrainOpenCode`` skips the skills index because OpenCode
        auto-discovers ``<workspace>/skills/**/SKILL.md`` itself)."""

    # ─── aux spawn (Phase B routes curator/judges through this) ──

    @abstractmethod
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
        """Run a one-shot fresh-session aux call.

        ``model_tier`` is an abstract size tier (``"tiny"`` /
        ``"small"`` / ``"medium"`` / ``"large"``), or a legacy raw
        model name (e.g. ``"haiku"``, ``"claude-sonnet-4-6"``) for
        back-compat with pre-Phase-B configs. The brain translates
        via ``core.yaml_config.model_for_tier``; raw strings pass
        through untranslated. ``model_tier=None`` (or the sentinel
        ``"default"``) means "no ``--model`` flag — let the brain
        CLI pick its native default."

        ``timeout_seconds`` is the hard wall on the subprocess.
        Exceeding it raises :class:`BrainTimeoutError`.

        ``env_overrides`` are merged into the spawned subprocess's
        environment on top of ``os.environ``. Used for recursion-guard
        markers (``VEXIS_CURATOR=1``, ``COHERENCE_JUDGE_ENV_VAR=1``,
        etc.) so the spawned process can self-identify in audit logs.

        ``allow_tools`` controls whether the spawned brain can use
        tools. ``False`` (the default for judges and extractors) means
        the call is text-only — if the model tries a tool, the call
        will hang waiting for a permission prompt. ``True`` adds the
        appropriate "bypass permissions" flag so tool calls succeed
        without prompting (used by the skill curator's consolidation
        pass and the learning review).

        ``cwd`` is the working directory for the spawned subprocess.
        Defaults to the brain's workspace (so the spawned session's
        on-disk artefacts land in the workspace's transcript
        directory and the recursion guard can find them on the next
        curator tick). Override only for cases where a different cwd
        is meaningful (rare).

        ``subsystem`` is the caller's subsystem name (``"curator"`` /
        ``"goal_judge"`` / etc.) — feeds the
        ``BrainModelNotFoundError.subsystem`` field on detection so
        the surfaced error tells the user which subsystem to fix.
        Optional with default ``None`` for back-compat with test
        callers that don't care; production callers should always
        pass it.

        ``reasoning_level`` is the per-call reasoning effort level
        (``"low"`` / ``"medium"`` / ``"high"`` / ``"max"`` on
        claude-code; arbitrary variant names like ``"high"`` /
        ``"max"`` on opencode — what's accepted is per-model, see
        :func:`core.model_discovery.reasoning_levels_for`).
        ``None`` (the default) means "let the brain pick the
        default reasoning". Each brain translates to its native
        flag: claude-code uses ``--effort <level>``; opencode uses
        ``--variant <level>``. Subsystems read this from
        ``core.yaml_config.subsystem_reasoning`` and pass through.

        ``context_window`` is reserved for future per-call context
        sizing. Currently both shipping brains (claude-code,
        opencode) expose only a single context size per model id —
        no CLI flag exists to override. The kwarg is accepted for
        API stability but ignored by the implementations; if a
        future brain exposes a runtime context flag, the impl
        will start consuming this. Defaults to ``None``.

        Used by the learning curator, coherence judge, goal judge,
        relationships extractor, and relationships classifier — each
        consumes ``AuxResult.stdout`` and treats a non-zero
        ``returncode`` as failure.

        Raises:
            BrainTimeoutError: subprocess didn't exit in time.
            BrainNotInstalled: brain binary missing from PATH.
            BrainAuthRequired: brain binary present but not authed
                (best-effort detection — depends on stderr shape).
            BrainModelNotFoundError: brain CLI rejected the
                configured model id at spawn time. Carries
                actionable suggested_fix imported from
                ``core.model_validator``'s template constants —
                same vocabulary the validator emits pre-write.
            BrainError: catch-all for other subprocess failures
                (OSError, FileNotFoundError on the binary itself).
        """

    # ─── session model ───────────────────────────────────────────

    @abstractmethod
    def session_token(self) -> str | None:
        """Opaque token vexis stores to identify the current session.
        Format is brain-specific. ``None`` means no session has been
        started for this brain yet (rare — most brains generate or
        accept a token at first ``respond``)."""

    @abstractmethod
    def rotate_session(self) -> str:
        """Discard the current session token and return a new one. The
        new token may be a placeholder until the first ``respond()``
        call confirms it (claude-code accepts a caller-pinned UUID;
        opencode generates the id and reports it in the first event).
        Used after ``SessionLost`` to recover."""

    # ─── transcript readback ─────────────────────────────────────

    @abstractmethod
    def iter_session_metas(self) -> Iterator[Any]:
        """Cheap enumeration of sessions known to this brain in the
        current workspace. One entry per session. Used by the learning
        curator for eligibility scans; the curator filters by mtime
        and content prefix.

        Returns ``Iterator[SessionMeta]`` (the dataclass currently
        lives in ``core.transcripts`` and may be relaxed in Phase C
        when its claude-code-specific fields like ``jsonl_path`` need
        to coexist with opencode's SQL-row identity)."""

    @abstractmethod
    def iter_messages(self, session_id: str) -> Iterator[Any]:
        """Full read of one session's user+assistant turns. claude-code:
        line-by-line JSONL parse. opencode (Phase C): SELECT against
        ``message`` table. Returns ``Iterator[TranscriptMessage]``."""

    @abstractmethod
    def is_brain_owned_session(self, session_id: str) -> bool:
        """Return True if the session was spawned by an aux call
        (curator review, goal judge, etc.) — used by the recursion
        guard to skip brain-owned sessions during eligibility scans.
        Both brains check the first user-turn text against vexis's
        canonical prompt prefixes (``CURATOR_REVIEW_PROMPT_PREFIX``,
        ``GOAL_JUDGE_PROMPT_PREFIX``); the storage layer differs but
        the prefix-match is content-shaped."""

    # ─── MCP config wiring ───────────────────────────────────────

    @abstractmethod
    def write_mcp_config(self, servers: list[McpServerSpec]) -> Path:
        """Write the MCP server config in this brain's expected
        location and format. Returns the path written. Idempotent.

        claude-code: ``<workspace>/.mcp.json`` with
        ``{"mcpServers": {name: {command, args, env}}}``.
        opencode: merges ``vexis-``-prefixed entries into
        ``<workspace>/opencode.json`` ``mcp:`` block, preserving
        user-owned non-prefixed entries byte-for-byte (Phase C)."""

    # ─── file conventions ────────────────────────────────────────

    @abstractmethod
    def instruction_file_name(self) -> str:
        """Project-instruction filename this brain reads.
        claude-code: ``"CLAUDE.md"``. opencode: ``"AGENTS.md"``
        (though it also reads ``CLAUDE.md`` as a fallback,
        ``AGENTS.md`` is canonical)."""

    @abstractmethod
    def instruction_search_paths(self, workspace: Path) -> list[Path]:
        """Paths this brain looks at for instructions, in lookup
        order. Used by the install script to set up the
        ``AGENTS.md`` ↔ ``CLAUDE.md`` symlink and by ``/status`` to
        surface where instructions are read from."""

    # ─── lifecycle ───────────────────────────────────────────────

    async def healthcheck(self) -> BrainHealth:
        """Optional: confirm the brain binary is installed and
        authenticated. Default implementation returns ``ok=True``;
        subclasses MAY override to actually run ``<binary> --version``
        and parse auth state.

        Not abstract — every brain is allowed to skip the check; the
        first ``respond`` will surface the error if the binary is
        missing or unauth'd. Implementations that do override should
        return ``BrainHealth(ok=False, error=…, hints=[…])`` for
        actionable failures."""
        return BrainHealth(ok=True, error=None, hints=[])

    async def kill_in_flight(self) -> None:
        """Kill the currently-running ``respond()`` subprocess if any.

        Default implementation is a no-op — today vexis kills via
        ``RunningTasks.cancel()`` (which calls ``proc.kill()`` against
        the proc registered with ``RunningTasks.attach()``). This hook
        is exposed on the ABC for a future world where ``/cancel``
        wants to talk to the brain directly; subclasses MAY override
        to track ``self._current_proc`` and call ``os.killpg`` when
        invoked."""
        return None
