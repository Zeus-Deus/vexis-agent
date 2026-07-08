# ruff: noqa: F811
# (pytest fixtures re-bind names imported from test_brain_cancel; ruff
# misreads the re-import as unused-redefinition.)
"""Issue #61 — background-subagent linger lifecycle (brain half).

Covers phases 1 + 3 of the fix plan:

  1. ``yaml_config.brain_background_agent_wait`` — default / int / numeric
     string / ``0`` (unlimited) / garbage / negative / bool handling.
  2. Env injection of ``CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`` at all three
     ``claude -p`` spawn sites (respond, astream, spawn_aux), including the
     ``env_overrides``-wins case for spawn_aux and the ``0`` → ``"0"`` case.
  3. The streaming linger path: a CLI that emits its result event then
     stays alive is detected at the short grace (NOT the full ceiling),
     yields ``background_lingering`` + ``final``, and is handed to a
     supervisor that drains + bounds it (completes cleanly, or is killed
     after the configured wait / on shutdown-cancel). Non-lingering turns
     are byte-for-byte unchanged (pinned by tests/test_tool_spans.py).
  4. Handler forwarding of ``background_lingering`` as ``("notice", …)``.

Subprocess fakes reuse the FakeProc / monkeypatched
``create_subprocess_exec`` machinery from test_brain_cancel; the linger
tests add a stdout stream that blocks after its preloaded lines (an
ordinary FakeStream returns EOF immediately and never lingers).
"""

from __future__ import annotations

import asyncio
import json
import signal
from pathlib import Path
from typing import AsyncIterator
from unittest import mock

import pytest

from vexis_agent.core import yaml_config
from vexis_agent.core.brain import claude_code as brain_module
from vexis_agent.core.brain.null import BrainNull
from vexis_agent.core.handler import MessageHandler
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore

from tests.test_brain_cancel import (  # noqa: F401
    FakeProc,
    FakeSession,
    _build_brain,
    _patch_spawn,
    _stream_json_result,
    patch_killpg,
    patch_runtime_dir,
)


# ══════════════════════════════════════════════════════════════════
# 1. yaml_config.brain_background_agent_wait
# ══════════════════════════════════════════════════════════════════


def _patch_config(tmp_path: Path, body: str | None):
    """Point yaml_config at a tmp ~/.vexis/config.yaml with ``body``.

    body=None means no file (the missing-file default path)."""
    if body is not None:
        (tmp_path / "config.yaml").write_text(body, encoding="utf-8")
    return mock.patch(
        "vexis_agent.core.yaml_config.vexis_dir", side_effect=lambda: tmp_path,
    )


def test_bg_wait_default_missing_file(tmp_path):
    with _patch_config(tmp_path, body=None):
        assert yaml_config.brain_background_agent_wait() == 1800


def test_bg_wait_default_missing_key(tmp_path):
    with _patch_config(tmp_path, body="brain:\n  kind: claude-code\n"):
        assert yaml_config.brain_background_agent_wait() == 1800


def test_bg_wait_explicit_int(tmp_path):
    with _patch_config(tmp_path, body="brain:\n  background_agent_wait: 3600\n"):
        assert yaml_config.brain_background_agent_wait() == 3600


def test_bg_wait_numeric_string(tmp_path):
    """A quoted numeric string (YAML round-trip artefact) still parses."""
    with _patch_config(tmp_path, body='brain:\n  background_agent_wait: "900"\n'):
        assert yaml_config.brain_background_agent_wait() == 900


def test_bg_wait_zero_means_unlimited(tmp_path):
    """0 is a valid, meaningful value (unlimited) — NOT coerced to the
    default the way a below-minimum int would be under _int_or_default."""
    with _patch_config(tmp_path, body="brain:\n  background_agent_wait: 0\n"):
        assert yaml_config.brain_background_agent_wait() == 0


def test_bg_wait_garbage_falls_back_to_default(tmp_path):
    with _patch_config(tmp_path, body="brain:\n  background_agent_wait: soon\n"):
        assert yaml_config.brain_background_agent_wait() == 1800


def test_bg_wait_negative_falls_back_to_default(tmp_path):
    with _patch_config(tmp_path, body="brain:\n  background_agent_wait: -5\n"):
        assert yaml_config.brain_background_agent_wait() == 1800


def test_bg_wait_bool_falls_back_to_default(tmp_path):
    """``bool`` is an ``int`` subclass; a stray ``true`` must NOT coerce to
    1 second — reject it so a typo never silently caps the wait at 1s."""
    with _patch_config(tmp_path, body="brain:\n  background_agent_wait: true\n"):
        assert yaml_config.brain_background_agent_wait() == 1800


# ══════════════════════════════════════════════════════════════════
# 2. Env injection at the three spawn sites
# ══════════════════════════════════════════════════════════════════


def _capture_spawn_env(monkeypatch, proc: FakeProc) -> dict:
    """Patch create_subprocess_exec to capture the env kwarg + return
    ``proc``. Returns the mutable ``captured`` dict."""
    captured: dict = {}

    async def _fake_spawn(*_argv, **kwargs):
        captured["env"] = kwargs.get("env")
        return proc

    monkeypatch.setattr(
        brain_module.asyncio, "create_subprocess_exec", _fake_spawn,
    )
    return captured


def test_env_injected_in_respond(monkeypatch, tmp_path, patch_killpg, patch_runtime_dir):
    monkeypatch.setattr(brain_module, "brain_background_agent_wait", lambda: 1800)
    proc = FakeProc(pid=701, mode="ok", stdout=_stream_json_result("hi"))
    patch_killpg[701] = proc
    captured = _capture_spawn_env(monkeypatch, proc)

    brain = _build_brain(RunningTasks(), tmp_path)
    asyncio.run(brain.respond("ping", chat_id=701))

    assert captured["env"]["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] == "1800000"


def test_env_injected_in_astream(monkeypatch, tmp_path, patch_killpg, patch_runtime_dir):
    monkeypatch.setattr(brain_module, "brain_background_agent_wait", lambda: 42)
    proc = FakeProc(pid=702, mode="ok", stdout=_stream_json_result("hi"))
    patch_killpg[702] = proc
    captured = _capture_spawn_env(monkeypatch, proc)

    brain = _build_brain(RunningTasks(), tmp_path)

    async def scenario() -> None:
        async for _ in brain.astream("hi", chat_id=702):
            pass

    asyncio.run(scenario())
    assert captured["env"]["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] == "42000"


def test_env_zero_maps_to_string_zero(monkeypatch, tmp_path, patch_killpg, patch_runtime_dir):
    """Unlimited (0 seconds) is exported as the literal ``"0"``, not
    ``"0000"`` — the CLI reads "0" as unbounded."""
    monkeypatch.setattr(brain_module, "brain_background_agent_wait", lambda: 0)
    proc = FakeProc(pid=703, mode="ok", stdout=_stream_json_result("hi"))
    patch_killpg[703] = proc
    captured = _capture_spawn_env(monkeypatch, proc)

    brain = _build_brain(RunningTasks(), tmp_path)
    asyncio.run(brain.respond("ping", chat_id=703))
    assert captured["env"]["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] == "0"


def test_env_injected_in_spawn_aux(monkeypatch, tmp_path):
    """spawn_aux uses subprocess.run (not create_subprocess_exec); config
    seeds the ceiling."""
    monkeypatch.setattr(brain_module, "brain_background_agent_wait", lambda: 600)
    monkeypatch.setattr(
        "vexis_agent.core.brain.claude_code.wrap_with_memory_scope",
        lambda argv: argv,
    )
    captured: dict = {}

    class _FakeCP:
        stdout = b"ok"
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeCP()

    monkeypatch.setattr(
        "vexis_agent.core.brain.claude_code.subprocess.run", _fake_run,
    )
    brain = _build_brain(RunningTasks(), tmp_path)
    asyncio.run(brain.spawn_aux("p", model_tier=None))
    assert captured["env"]["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] == "600000"


def test_env_overrides_win_in_spawn_aux(monkeypatch, tmp_path):
    """An explicit env_overrides entry for the ceiling beats the config
    value — a caller wanting a per-spawn ceiling still gets it."""
    monkeypatch.setattr(brain_module, "brain_background_agent_wait", lambda: 1800)
    monkeypatch.setattr(
        "vexis_agent.core.brain.claude_code.wrap_with_memory_scope",
        lambda argv: argv,
    )
    captured: dict = {}

    class _FakeCP:
        stdout = b""
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeCP()

    monkeypatch.setattr(
        "vexis_agent.core.brain.claude_code.subprocess.run", _fake_run,
    )
    brain = _build_brain(RunningTasks(), tmp_path)
    asyncio.run(
        brain.spawn_aux(
            "p",
            model_tier=None,
            env_overrides={"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "5000"},
        )
    )
    assert captured["env"]["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] == "5000"


# ══════════════════════════════════════════════════════════════════
# 3. Streaming linger detection + supervisor
# ══════════════════════════════════════════════════════════════════


def _line(obj: dict) -> bytes:
    return json.dumps(obj).encode() + b"\n"


def _sys_init() -> bytes:
    return _line({"type": "system", "subtype": "init", "session_id": "s1"})


def _assistant_text(text: str) -> bytes:
    return _line(
        {
            "type": "assistant",
            "parent_tool_use_id": None,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        }
    )


def _result(text: str) -> bytes:
    return _line({"type": "result", "subtype": "success", "result": text})


class _BlockingStream:
    """StreamReader stand-in: yields preloaded lines, then BLOCKS on
    readline until ``release()`` (after which it returns EOF).

    Models a ``claude -p`` that emitted its result event but keeps stdout
    open while background subagents run — the exact shape the linger
    detection fires on. (The plain FakeStream returns EOF immediately and
    would fall straight through to the normal path.)
    """

    def __init__(self, data: bytes) -> None:
        self._buf = data
        self._released = asyncio.Event()

    async def readline(self) -> bytes:
        if self._buf:
            nl = self._buf.find(b"\n")
            if nl < 0:
                line, self._buf = self._buf, b""
                return line
            line, self._buf = self._buf[: nl + 1], self._buf[nl + 1:]
            return line
        await self._released.wait()
        return b""

    def release(self) -> None:
        self._released.set()

    async def read(self) -> bytes:  # stderr path — never blocks
        return b""


class _LingerProc:
    """Programmable proc whose ``wait()`` blocks until ``finish()``.

    Compatible with the test_brain_cancel ``patch_killpg`` fixture (has
    ``pid`` / ``signals`` / ``finish``) so ``_kill_group`` works."""

    def __init__(self, pid: int, stdout: _BlockingStream) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdout = stdout
        self.stderr = _BlockingStream(b"")  # read() returns b"" at once
        self.signals: list[int] = []
        self._exit = asyncio.Event()

    async def wait(self) -> int:
        await self._exit.wait()
        assert self.returncode is not None
        return self.returncode

    def finish(self, returncode: int = 0) -> None:
        if self.returncode is None:
            self.returncode = returncode
            self._exit.set()


def _linger_stdout(answer: str = "answer") -> bytes:
    return b"".join([_sys_init(), _assistant_text(answer), _result(answer)])


def test_astream_linger_detected_and_final_yielded(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """Result event seen, process stays alive → after the SHORT grace
    (not the full wait) the stream yields ``background_lingering`` then
    the canonical ``final`` and returns; the chat slot is freed and a
    supervisor is registered."""
    monkeypatch.setattr(brain_module, "_POST_RESULT_LINGER_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(brain_module, "brain_background_agent_wait", lambda: 120)

    stream = _BlockingStream(_linger_stdout("the answer"))
    proc = _LingerProc(pid=991, stdout=stream)
    patch_killpg[991] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> list:
        events = [evt async for evt in brain.astream("hi", chat_id=991)]
        # Supervisor is running detached; capture + finish it so the
        # test doesn't leak a pending task.
        sup = brain._linger_supervisors.get(991)
        assert sup is not None
        stream.release()
        proc.finish(0)
        await sup
        return events

    events = asyncio.run(scenario())

    dicts = [e for e in events if isinstance(e, dict)]
    types = [d["type"] for d in dicts]
    assert "background_lingering" in types
    assert types[-1] == "final"
    linger = next(d for d in dicts if d["type"] == "background_lingering")
    assert linger["wait_seconds"] == 120
    final = dicts[-1]
    assert final == {"type": "final", "text": "the answer"}
    # Chat slot freed (turn is done) and supervisor deregistered on exit.
    assert not reg.is_running(991)
    assert brain._linger_supervisors == {}


def test_astream_linger_supervisor_completes_without_kill(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """When the background subagent finishes within the wait, the
    supervisor reaps the process cleanly — no SIGTERM/SIGKILL."""
    monkeypatch.setattr(brain_module, "_POST_RESULT_LINGER_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(brain_module, "brain_background_agent_wait", lambda: 120)

    stream = _BlockingStream(_linger_stdout())
    proc = _LingerProc(pid=992, stdout=stream)
    patch_killpg[992] = proc
    _patch_spawn(monkeypatch, proc)

    brain = _build_brain(RunningTasks(), tmp_path)

    async def scenario() -> None:
        async for _ in brain.astream("hi", chat_id=992):
            pass
        sup = brain._linger_supervisors.get(992)
        assert sup is not None
        stream.release()
        proc.finish(0)
        await sup

    asyncio.run(scenario())
    assert signal.SIGTERM not in proc.signals
    assert signal.SIGKILL not in proc.signals
    assert brain._linger_supervisors == {}


def test_astream_linger_supervisor_kills_after_wait(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """A background subagent that overruns the configured wait is killed
    by the supervisor (the issue #61 SIGKILL that used to happen silently
    at the CLI's own ceiling — now vexis-bounded + logged)."""
    monkeypatch.setattr(brain_module, "_POST_RESULT_LINGER_GRACE_SECONDS", 0.05)
    # Tiny wait so the supervisor kills fast. (Passed straight through to
    # asyncio.wait_for's timeout; int-vs-float is irrelevant at runtime.)
    monkeypatch.setattr(brain_module, "brain_background_agent_wait", lambda: 0.15)

    stream = _BlockingStream(_linger_stdout())
    proc = _LingerProc(pid=993, stdout=stream)
    patch_killpg[993] = proc
    _patch_spawn(monkeypatch, proc)

    brain = _build_brain(RunningTasks(), tmp_path)

    async def scenario() -> None:
        async for _ in brain.astream("hi", chat_id=993):
            pass
        sup = brain._linger_supervisors.get(993)
        assert sup is not None
        await sup  # never finish the proc → supervisor times out + kills

    asyncio.run(scenario())
    assert signal.SIGTERM in proc.signals
    assert brain._linger_supervisors == {}


def test_cancel_lingering_supervisors_kills_process(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """The shutdown / brain-close hook cancels supervisors and leaves no
    orphan claude -p behind."""
    monkeypatch.setattr(brain_module, "_POST_RESULT_LINGER_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(brain_module, "brain_background_agent_wait", lambda: 999)

    stream = _BlockingStream(_linger_stdout())
    proc = _LingerProc(pid=994, stdout=stream)
    patch_killpg[994] = proc
    _patch_spawn(monkeypatch, proc)

    brain = _build_brain(RunningTasks(), tmp_path)

    async def scenario() -> None:
        async for _ in brain.astream("hi", chat_id=994):
            pass
        assert brain._linger_supervisors.get(994) is not None
        await brain.cancel_lingering_supervisors()

    asyncio.run(scenario())
    assert signal.SIGTERM in proc.signals
    assert brain._linger_supervisors == {}


def test_astream_non_lingering_turn_starts_no_supervisor(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir,
):
    """The overwhelmingly-common case: the process exits right after the
    result event. No linger, no supervisor, canonical final unchanged."""
    monkeypatch.setattr(brain_module, "_POST_RESULT_LINGER_GRACE_SECONDS", 0.05)
    proc = FakeProc(pid=995, mode="ok", stdout=_linger_stdout("done"))
    patch_killpg[995] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> list:
        return [evt async for evt in brain.astream("hi", chat_id=995)]

    events = asyncio.run(scenario())
    dicts = [e for e in events if isinstance(e, dict)]
    assert [d["type"] for d in dicts] == ["final"]
    assert dicts[-1] == {"type": "final", "text": "done"}
    assert brain._linger_supervisors == {}
    assert not reg.is_running(995)


# ══════════════════════════════════════════════════════════════════
# 4. Handler forwards background_lingering as ("notice", …)
# ══════════════════════════════════════════════════════════════════


def _make_sessions(tmp_path: Path) -> SessionStore:
    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = tmp_path / "sessions.json"  # type: ignore[attr-defined]
    sessions._active = "test"  # type: ignore[attr-defined]
    sessions._sessions = {  # type: ignore[attr-defined]
        "test": {
            "uuid": "00000000-0000-0000-0000-000000000000",
            "initialized": True,
            "created_at": "2026-05-09T00:00:00+00:00",
        },
    }
    return sessions


def test_handler_forwards_background_lingering_as_notice(tmp_path: Path) -> None:
    """The brain's ``background_lingering`` dict is forwarded under the
    ``("notice", …)`` tag — NOT ``("tool", …)`` (which Telegram drops) and
    NOT folded into ``("done", …)``."""

    class LingerBrain(BrainNull):
        async def astream(
            self, message: str, chat_id: int, *,
            model=None, reasoning_level=None,
        ) -> AsyncIterator[str | dict]:
            yield "the answer"
            yield {"type": "background_lingering", "wait_seconds": 1800}
            yield {"type": "final", "text": "the answer"}

    handler = MessageHandler(
        brain=LingerBrain(responses=[]), sessions=_make_sessions(tmp_path),
        allowed_user_id=99, notifier=None,
    )

    async def run() -> list:
        return [evt async for evt in handler.stream(99, 1, "x")]

    events = asyncio.run(run())
    assert ("notice", {"type": "background_lingering", "wait_seconds": 1800}) in events
    # Notice never rides the tool lane, and done carries the reply only.
    assert all(tag != "tool" for tag, _ in events)
    done = [payload for tag, payload in events if tag == "done"]
    assert done == ["the answer"]


# ══════════════════════════════════════════════════════════════════
# 5. Notice-text formatter (Telegram transport helper)
# ══════════════════════════════════════════════════════════════════


def test_notice_text_rounds_up_to_minutes():
    from vexis_agent.transports.telegram import (
        _format_background_lingering_notice,
    )

    assert "30 minutes" in _format_background_lingering_notice(1800)
    # ceil: 90s → 2 minutes, never an under-promise.
    assert "2 minutes" in _format_background_lingering_notice(90)
    assert "1 minute" in _format_background_lingering_notice(30)


def test_notice_text_unlimited():
    from vexis_agent.transports.telegram import (
        _format_background_lingering_notice,
    )

    text = _format_background_lingering_notice(0)
    assert "no time limit" in text
