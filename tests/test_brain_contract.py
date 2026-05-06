"""Cross-brain contract tests for the Phase A `Brain` ABC.

Verifies, parameterised over ``[BrainNull, ClaudeCodeBrain]``:
- every abstract method is implemented (Python ABC enforcement —
  instantiating an ABC with unimplemented abstract methods raises
  ``TypeError`` at construction time);
- the exception hierarchy is intact (``BrainTimeoutError``,
  ``BrainCancelled``, ``SessionLost``, ``BrainNotInstalled``,
  ``BrainAuthRequired`` all subclass ``BrainError``);
- the ``BrainEvent`` variant dataclass shapes are stable;
- the inspection-only methods (``instruction_file_name``,
  ``instruction_search_paths``, ``session_token``, ``rotate_session``,
  ``healthcheck``, ``kill_in_flight``, ``build_system_prompt``,
  ``iter_session_metas``, ``iter_messages``, ``is_brain_owned_session``)
  return the right shapes without spawning subprocesses;
- methods explicitly deferred to Phase B/C (``spawn_aux``,
  ``write_mcp_config``) raise ``NotImplementedError`` on
  ``ClaudeCodeBrain`` (the safety guarantee — accidental Phase-B
  call sites surface immediately).

``BrainNull`` exercises the full surface (cheap canned responses,
no subprocess). ``ClaudeCodeBrain`` is constructed against a tmp
workspace and a ``SessionStore`` pointed at tmp paths so no real
``claude -p`` invocation can happen — the test fixture asserts on
construction and on inspection-only methods only. Tests follow the
codebase convention of sync test functions calling ``asyncio.run()``
rather than pytest-asyncio.

Design citation: ``.plans/brain-abstraction-research.md`` §5 Day 1
(Phase A) and §7 (test strategy).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from core.brain.base import (
    AuxResult,
    Brain,
    BrainAuthRequired,
    BrainCancelled,
    BrainError,
    BrainHealth,
    BrainNotInstalled,
    BrainTimeoutError,
    Finished,
    McpServerSpec,
    SessionEstablished,
    SessionLost,
    StreamError,
    TextDelta,
    TextEnd,
    ToolEnd,
    ToolStart,
)
from core.brain.claude_code import ClaudeCodeBrain
from core.brain.null import BrainNull
from core.running_tasks import RunningTasks
from core.sessions import SessionStore


# ──────────────────────────────────────────────────────────────────
# Fixtures — factories that DON'T actually spawn subprocesses
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A throwaway workspace for ClaudeCodeBrain construction tests.
    The brain never spawns under these tests so the workspace just
    needs to exist."""
    (tmp_path / "skills").mkdir()
    return tmp_path


@pytest.fixture
def session_store(tmp_path: Path) -> SessionStore:
    """SessionStore pointed at a tmp state file so the brain has a
    fresh UUID and doesn't touch the user's real ~/.vexis/."""
    return SessionStore(tmp_path / "sessions.json")


@pytest.fixture
def claude_brain(workspace: Path, session_store: SessionStore) -> ClaudeCodeBrain:
    """ClaudeCodeBrain constructed against tmp paths. The fixture
    NEVER calls respond() — these tests assert on the inspection-only
    methods. test_brain_cancel.py covers the spawn path with mocked
    subprocesses."""
    return ClaudeCodeBrain(
        workspace=workspace,
        session=session_store,
        running_tasks=RunningTasks(),
    )


@pytest.fixture
def null_brain() -> BrainNull:
    return BrainNull(responses=["canned-1", "canned-2"])


@pytest.fixture(params=["null", "claude_code"])
def brain_under_test(
    request: pytest.FixtureRequest,
    null_brain: BrainNull,
    claude_brain: ClaudeCodeBrain,
) -> Brain:
    """Parameterised over both brain implementations. Tests using this
    fixture should only exercise inspection-only methods or methods
    that work uniformly across implementations (e.g. exception
    hierarchy checks). Methods that diverge between brains
    (spawn_aux raises on claude_code, returns on null) get their own
    explicit per-brain tests."""
    if request.param == "null":
        return null_brain
    return claude_brain


# ──────────────────────────────────────────────────────────────────
# ABC enforcement + exception hierarchy
# ──────────────────────────────────────────────────────────────────


def test_brain_is_abstract():
    """Brain itself must not be directly instantiable — it has
    abstract methods. This pins the contract: any future direct
    `Brain()` call is a TypeError, not a silent partial brain."""
    with pytest.raises(TypeError):
        Brain()  # type: ignore[abstract]


def test_brain_null_implements_abc(null_brain: BrainNull):
    """BrainNull instantiates successfully → it implements every
    abstract method."""
    assert isinstance(null_brain, Brain)


def test_brain_claude_code_implements_abc(claude_brain: ClaudeCodeBrain):
    """ClaudeCodeBrain instantiates successfully → it implements
    every abstract method (some raise NotImplementedError; that's
    still a concrete implementation, see §5 Day 1)."""
    assert isinstance(claude_brain, Brain)


def test_exception_hierarchy_subclasses_brain_error():
    """Every domain exception subclasses BrainError. Transport-layer
    code does ``except BrainError: ...`` for the catch-all path; this
    test pins that contract."""
    for exc in (
        BrainTimeoutError,
        BrainCancelled,
        SessionLost,
        BrainNotInstalled,
        BrainAuthRequired,
    ):
        assert issubclass(exc, BrainError), (
            f"{exc.__name__} must subclass BrainError"
        )


def test_brain_error_subclasses_runtime_error():
    """Top of the hierarchy is RuntimeError so legacy
    ``except Exception:`` paths still catch us; we don't accidentally
    mask base BaseException-shaped concerns."""
    assert issubclass(BrainError, RuntimeError)


# ──────────────────────────────────────────────────────────────────
# BrainEvent variant shapes
# ──────────────────────────────────────────────────────────────────


def test_session_established_carries_id():
    evt = SessionEstablished(session_id="abc-123")
    assert evt.session_id == "abc-123"


def test_text_delta_carries_delta():
    evt = TextDelta(delta="hello")
    assert evt.delta == "hello"


def test_text_end_carries_text():
    evt = TextEnd(text="hello world")
    assert evt.text == "hello world"


def test_tool_start_carries_id_name_input():
    evt = ToolStart(tool_id="t1", name="read", input={"path": "/etc/hosts"})
    assert evt.tool_id == "t1"
    assert evt.name == "read"
    assert evt.input == {"path": "/etc/hosts"}


def test_tool_end_carries_status_output_error():
    evt = ToolEnd(tool_id="t1", status="completed", output="ok", error=None)
    assert evt.status == "completed"
    assert evt.output == "ok"
    err = ToolEnd(tool_id="t2", status="error", output=None, error="boom")
    assert err.status == "error"
    assert err.error == "boom"


def test_finished_carries_text_and_reason():
    evt = Finished(text="final reply", reason="idle")
    assert evt.text == "final reply"
    assert evt.reason == "idle"


def test_stream_error_carries_message():
    evt = StreamError(message="transient")
    assert evt.message == "transient"


def test_brain_event_union_includes_all_variants():
    """The ``BrainEvent`` union type must accept every variant — Python
    doesn't enforce Union membership at runtime, so we sanity-check
    that each variant constructs and that the dataclasses are frozen
    (immutable per the design — ``frozen=True`` raises
    ``FrozenInstanceError`` on attribute assignment via the
    descriptor protocol)."""
    from dataclasses import FrozenInstanceError

    variants = [
        SessionEstablished("s"),
        TextDelta("d"),
        TextEnd("t"),
        ToolStart("id", "name", {}),
        ToolEnd("id", "completed", "out", None),
        Finished("final", "idle"),
        StreamError("msg"),
    ]
    for v in variants:
        with pytest.raises(FrozenInstanceError):
            v.session_id = "mutated"  # type: ignore[misc]  # attr names vary by variant; any setattr fails


# ──────────────────────────────────────────────────────────────────
# Inspection-only methods (cross-brain)
# ──────────────────────────────────────────────────────────────────


def test_instruction_file_name_is_string(brain_under_test: Brain):
    name = brain_under_test.instruction_file_name()
    assert isinstance(name, str)
    assert name.endswith(".md")


def test_instruction_search_paths_returns_list(
    brain_under_test: Brain, workspace: Path
):
    paths = brain_under_test.instruction_search_paths(workspace)
    assert isinstance(paths, list)
    for p in paths:
        assert isinstance(p, Path)


def test_session_token_returns_str_or_none(brain_under_test: Brain):
    tok = brain_under_test.session_token()
    assert tok is None or isinstance(tok, str)


def test_rotate_session_returns_str(brain_under_test: Brain):
    new_tok = brain_under_test.rotate_session()
    assert isinstance(new_tok, str)
    assert new_tok  # non-empty


def test_iter_session_metas_returns_iterator(brain_under_test: Brain):
    metas = brain_under_test.iter_session_metas()
    # Must be iterable; tmp workspace has no sessions so we expect empty.
    assert iter(metas) is not None
    assert list(brain_under_test.iter_session_metas()) == []


def test_iter_messages_returns_iterator(brain_under_test: Brain):
    msgs = brain_under_test.iter_messages("nonexistent-session-id")
    assert iter(msgs) is not None
    assert list(brain_under_test.iter_messages("nonexistent-session-id")) == []


def test_is_brain_owned_session_returns_bool(brain_under_test: Brain):
    result = brain_under_test.is_brain_owned_session("nonexistent-session-id")
    assert isinstance(result, bool)
    assert result is False  # nonexistent → not brain-owned


def test_build_system_prompt_returns_str(brain_under_test: Brain):
    prompt = brain_under_test.build_system_prompt()
    assert isinstance(prompt, str)
    assert prompt  # non-empty


def test_healthcheck_returns_brain_health(brain_under_test: Brain):
    result = asyncio.run(brain_under_test.healthcheck())
    assert isinstance(result, BrainHealth)
    assert isinstance(result.ok, bool)
    assert isinstance(result.hints, list)


def test_kill_in_flight_returns_none(brain_under_test: Brain):
    """Phase A no-op contract: kill_in_flight() returns None without
    side effects when no subprocess is running. Both brains satisfy
    this; subclasses MAY override to do real work."""
    result = asyncio.run(brain_under_test.kill_in_flight())
    assert result is None


# ──────────────────────────────────────────────────────────────────
# ClaudeCodeBrain.spawn_aux — Phase B real implementation
# ──────────────────────────────────────────────────────────────────


def test_claude_code_spawn_aux_argv_shape_with_tier(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """Phase B verifies spawn_aux's argv composition with a mocked
    subprocess.run. Tier resolution: ``"small"`` → claude-code default
    map → ``"haiku"`` → ``--model haiku`` flag added."""
    captured: dict = {}

    class _FakeCP:
        stdout = b"verdict-stdout"
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        captured["timeout"] = kwargs.get("timeout")
        return _FakeCP()

    monkeypatch.setattr("core.brain.claude_code.subprocess.run", _fake_run)
    result = asyncio.run(
        claude_brain.spawn_aux(
            "test prompt",
            model_tier="small",
            timeout_seconds=12.0,
            env_overrides={"X_TEST_FLAG": "1"},
        )
    )
    assert isinstance(result, AuxResult)
    assert result.stdout == "verdict-stdout"
    assert result.returncode == 0
    # Argv: claude -p --model haiku "test prompt"
    assert captured["argv"][:2] == ["claude", "-p"]
    assert "--model" in captured["argv"]
    model_idx = captured["argv"].index("--model")
    assert captured["argv"][model_idx + 1] == "haiku"  # small → haiku default
    assert "test prompt" in captured["argv"]
    # No bypassPermissions by default — judges don't allow tools.
    assert "bypassPermissions" not in captured["argv"]
    # env_overrides merged on top of os.environ.
    assert captured["env"]["X_TEST_FLAG"] == "1"
    assert captured["timeout"] == 12.0


def test_claude_code_spawn_aux_allow_tools_adds_permission_flag(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """The skill curator passes ``allow_tools=True`` so its spawned
    consolidation pass can write files. Verify the flag is present."""
    captured: dict = {}

    class _FakeCP:
        stdout = b""
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return _FakeCP()

    monkeypatch.setattr("core.brain.claude_code.subprocess.run", _fake_run)
    asyncio.run(claude_brain.spawn_aux("p", model_tier=None, allow_tools=True))
    assert "--permission-mode" in captured["argv"]
    perm_idx = captured["argv"].index("--permission-mode")
    assert captured["argv"][perm_idx + 1] == "bypassPermissions"


def test_claude_code_spawn_aux_no_tier_omits_model_flag(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """``model_tier=None`` omits ``--model`` so claude picks its
    account default. Same for the sentinel ``"default"``."""
    captured: dict = {}

    class _FakeCP:
        stdout = b""
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return _FakeCP()

    monkeypatch.setattr("core.brain.claude_code.subprocess.run", _fake_run)
    asyncio.run(claude_brain.spawn_aux("p"))  # tier=None
    assert "--model" not in captured["argv"]


def test_claude_code_spawn_aux_legacy_raw_model_passes_through(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """Pre-Phase-B configs use ``models.<subsystem>: haiku`` raw
    strings. The shim returns "haiku"; spawn_aux passes it through
    to ``--model haiku`` directly (no abstract-tier translation,
    since "haiku" isn't in ABSTRACT_TIERS)."""
    captured: dict = {}

    class _FakeCP:
        stdout = b""
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return _FakeCP()

    monkeypatch.setattr("core.brain.claude_code.subprocess.run", _fake_run)
    asyncio.run(claude_brain.spawn_aux("p", model_tier="haiku"))
    assert "--model" in captured["argv"]
    model_idx = captured["argv"].index("--model")
    assert captured["argv"][model_idx + 1] == "haiku"


def test_claude_code_spawn_aux_timeout_raises_brain_timeout(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """``subprocess.TimeoutExpired`` translates to ``BrainTimeoutError``
    so callers can ``except BrainTimeoutError`` uniformly."""
    import subprocess as subprocess_module

    def _fake_run(argv, **kwargs):
        raise subprocess_module.TimeoutExpired(cmd=argv, timeout=1.0)

    monkeypatch.setattr("core.brain.claude_code.subprocess.run", _fake_run)
    with pytest.raises(BrainTimeoutError, match="timed out"):
        asyncio.run(claude_brain.spawn_aux("p", timeout_seconds=1.0))


def test_claude_code_spawn_aux_missing_binary_raises_not_installed(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """``FileNotFoundError`` translates to ``BrainNotInstalled`` with
    actionable hint text."""
    def _fake_run(argv, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file: 'claude'")

    monkeypatch.setattr("core.brain.claude_code.subprocess.run", _fake_run)
    with pytest.raises(BrainNotInstalled, match="not on PATH"):
        asyncio.run(claude_brain.spawn_aux("p"))


def test_claude_code_spawn_aux_returns_nonzero_returncode_without_raising(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """A non-zero exit (e.g. claude-side parse failure) is NOT a
    ``BrainError`` — subsystems get the ``AuxResult`` and decide.
    Lets fail-open patterns (judges) treat any non-success as
    "skipped/continue" without claiming a brain crash."""
    class _FakeCP:
        stdout = b""
        stderr = b"claude said no"
        returncode = 2

    def _fake_run(argv, **kwargs):
        return _FakeCP()

    monkeypatch.setattr("core.brain.claude_code.subprocess.run", _fake_run)
    result = asyncio.run(claude_brain.spawn_aux("p"))
    assert result.returncode == 2
    assert "claude said no" in result.stderr


# ──────────────────────────────────────────────────────────────────
# Phase C deferred methods raise on ClaudeCodeBrain
# ──────────────────────────────────────────────────────────────────


def test_claude_code_write_mcp_config_raises_not_implemented(
    claude_brain: ClaudeCodeBrain,
):
    """Phase A safety: a stray write_mcp_config call surfaces
    immediately. Phase C will land the writer."""
    with pytest.raises(NotImplementedError, match="Phase C"):
        claude_brain.write_mcp_config([])


# ──────────────────────────────────────────────────────────────────
# BrainNull's full Phase-A+ surface
# ──────────────────────────────────────────────────────────────────


def test_null_brain_respond_returns_canned_response(null_brain: BrainNull):
    reply = asyncio.run(null_brain.respond("hi", chat_id=1))
    assert reply == "canned-1"
    reply2 = asyncio.run(null_brain.respond("again", chat_id=1))
    assert reply2 == "canned-2"
    # Exhausted queue returns "" rather than raising.
    reply3 = asyncio.run(null_brain.respond("more?", chat_id=1))
    assert reply3 == ""


def test_null_brain_records_calls(null_brain: BrainNull):
    asyncio.run(null_brain.respond("first", chat_id=42))
    asyncio.run(null_brain.respond("second", chat_id=99))
    assert null_brain.calls() == [("first", 42), ("second", 99)]


def test_null_brain_next_raises_injects_exception(null_brain: BrainNull):
    null_brain.next_raises(SessionLost("injected"))
    with pytest.raises(SessionLost, match="injected"):
        asyncio.run(null_brain.respond("oops", chat_id=1))
    # Injection is single-shot — next call returns the canned response.
    reply = asyncio.run(null_brain.respond("recover", chat_id=1))
    assert reply == "canned-1"


def test_null_brain_spawn_aux_returns_canned():
    """spawn_aux works on null brain — returns AuxResult from the
    queue, defaulting to empty stdout/stderr if exhausted."""
    brain = BrainNull(
        aux_results=[AuxResult(stdout="hello", stderr="", returncode=0)]
    )
    result = asyncio.run(brain.spawn_aux("any prompt", model_tier="small"))
    assert result.stdout == "hello"
    assert result.returncode == 0
    # Exhausted: default empty result, not raise.
    result2 = asyncio.run(brain.spawn_aux("more", model_tier="tiny"))
    assert result2.stdout == ""
    assert result2.returncode == 0


def test_null_brain_aux_calls_recorded():
    brain = BrainNull()
    asyncio.run(brain.spawn_aux("p1", model_tier="small"))
    asyncio.run(brain.spawn_aux("p2", model_tier="large"))
    assert brain.aux_calls() == [("p1", "small"), ("p2", "large")]


def test_null_brain_next_aux_raises():
    brain = BrainNull()
    brain.next_aux_raises(BrainTimeoutError("aux timed out"))
    with pytest.raises(BrainTimeoutError, match="aux timed out"):
        asyncio.run(brain.spawn_aux("p", model_tier="small"))


def test_null_brain_session_rotate_advances():
    brain = BrainNull()
    first = brain.session_token()
    second = brain.rotate_session()
    third = brain.rotate_session()
    assert first != second != third
    assert brain.session_token() == third


def test_null_brain_write_mcp_config_records():
    brain = BrainNull()
    spec = McpServerSpec(name="codemux", command="/usr/bin/codemux", args=["mcp"])
    brain.write_mcp_config([spec])
    brain.write_mcp_config([])  # second call to verify accumulation
    writes = brain.mcp_writes()
    assert len(writes) == 2
    assert writes[0] == [spec]
    assert writes[1] == []


# ──────────────────────────────────────────────────────────────────
# ClaudeCodeBrain — Phase A delegating methods
# ──────────────────────────────────────────────────────────────────


def test_claude_code_session_token_returns_session_uuid(
    claude_brain: ClaudeCodeBrain, session_store: SessionStore
):
    """ClaudeCodeBrain.session_token delegates to SessionStore.get."""
    assert claude_brain.session_token() == session_store.get()


def test_claude_code_rotate_returns_new_uuid(
    claude_brain: ClaudeCodeBrain,
):
    before = claude_brain.session_token()
    after = claude_brain.rotate_session()
    assert before != after


def test_claude_code_instruction_file_name(
    claude_brain: ClaudeCodeBrain,
):
    assert claude_brain.instruction_file_name() == "CLAUDE.md"


def test_claude_code_instruction_search_paths_includes_workspace_and_global(
    claude_brain: ClaudeCodeBrain, workspace: Path
):
    paths = claude_brain.instruction_search_paths(workspace)
    assert workspace / "CLAUDE.md" in paths
    # Global ~/.claude/CLAUDE.md path is included even though the
    # tmp test environment doesn't have one (lookup-order-as-data).
    assert any(".claude" in str(p) for p in paths)


def test_claude_code_iter_session_metas_empty_for_fresh_workspace(
    claude_brain: ClaudeCodeBrain,
):
    """Fresh workspace has no Claude Code projects directory yet, so
    the iterator yields nothing without raising."""
    assert list(claude_brain.iter_session_metas()) == []


def test_claude_code_is_brain_owned_session_false_for_missing_path(
    claude_brain: ClaudeCodeBrain,
):
    """Nonexistent session id → False (not "brain-owned"); the recursion
    guard treats unknown sessions as not-owned, so they would be
    eligible for review (the curator's mtime/idle gates do the actual
    eligibility filtering downstream)."""
    assert claude_brain.is_brain_owned_session("does-not-exist") is False


def test_claude_code_build_system_prompt_returns_default_soul(
    claude_brain: ClaudeCodeBrain,
):
    """Fresh workspace has no SOUL.md so the prompt falls back to
    DEFAULT_SOUL. The CAPABILITIES.md from the project root is also
    appended."""
    prompt = claude_brain.build_system_prompt()
    assert "Vexis" in prompt  # DEFAULT_SOUL says "You are Vexis"


def test_claude_code_healthcheck_reports_install_status(
    claude_brain: ClaudeCodeBrain,
):
    """Healthcheck returns BrainHealth with ok reflecting whether
    `claude` is on PATH. We don't assert ok=True or False — that
    depends on the test environment — but the shape must be right."""
    result = asyncio.run(claude_brain.healthcheck())
    assert isinstance(result, BrainHealth)
    if shutil.which("claude") is None:
        assert result.ok is False
        assert result.error is not None
        assert any("Install Claude Code" in h for h in result.hints)
    else:
        assert result.ok is True
