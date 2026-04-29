"""Tests for ClaudeCodeBrain integration with RunningTasks.

We patch asyncio.create_subprocess_exec to return a fake proc instead of
spawning real `claude -p`. The fake proc lets us drive the lifecycle:
normal exit, timeout, cancel-mid-call, or cancel-during-spawn.

Tests follow the codebase convention of sync test functions calling
asyncio.run() rather than pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import pytest

from brains import claude_code as brain_module
from brains.claude_code import (
    BrainCancelled,
    BrainError,
    BrainTimeoutError,
    ClaudeCodeBrain,
)
from core.running_tasks import RunningTasks, TaskAlreadyRunning


class FakeProc:
    """Programmable stand-in for asyncio.subprocess.Process.

    Tests script the behavior by setting `mode`:
      - "ok":   returns (stdout, b"") with rc=0 immediately
      - "fail": returns ("", stderr) with rc>0
      - "hang": communicate() blocks until finish() is called
    """

    def __init__(
        self,
        pid: int = 4242,
        mode: str = "ok",
        stdout: bytes = b"hi",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._mode = mode
        self._stdout = stdout
        self._stderr = stderr
        self._success_rc = returncode
        self._exit = asyncio.Event()
        self.signals: list[int] = []

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._mode == "ok":
            self.returncode = self._success_rc
            return self._stdout, self._stderr
        if self._mode == "fail":
            self.returncode = self._success_rc or 1
            return self._stdout, self._stderr
        await self._exit.wait()
        assert self.returncode is not None
        return self._stdout, self._stderr

    async def wait(self) -> int:
        if self.returncode is not None:
            return self.returncode
        await self._exit.wait()
        assert self.returncode is not None
        return self.returncode

    def finish(self, returncode: int = -signal.SIGTERM) -> None:
        if self.returncode is None:
            self.returncode = returncode
            self._exit.set()


@pytest.fixture
def patch_killpg(monkeypatch):
    procs: dict[int, FakeProc] = {}

    def _getpgid(pid: int) -> int:
        return pid

    def _killpg(pgid: int, sig: int) -> None:
        proc = procs.get(pgid)
        if proc is None:
            raise ProcessLookupError(pgid)
        proc.signals.append(sig)
        if sig in (signal.SIGTERM, signal.SIGKILL):
            proc.finish(-sig)

    monkeypatch.setattr("core.running_tasks.os.getpgid", _getpgid)
    monkeypatch.setattr("core.running_tasks.os.killpg", _killpg)
    monkeypatch.setattr("brains.claude_code.os.getpgid", _getpgid)
    monkeypatch.setattr("brains.claude_code.os.killpg", _killpg)
    return procs


class FakeSession:
    """Minimal SessionStore stand-in. Always 'initialized' so we go down
    the --resume path without triggering rotate."""

    def __init__(self, uid: str = "00000000-0000-0000-0000-000000000001") -> None:
        self._uid = uid
        self._initialized = True

    def get(self) -> str:
        return self._uid

    def is_initialized(self) -> bool:
        return self._initialized

    def mark_initialized(self) -> None:
        self._initialized = True

    def rotate(self) -> str:
        self._uid = "00000000-0000-0000-0000-000000000002"
        return self._uid


def _build_brain(running_tasks: RunningTasks, tmp_path: Path) -> ClaudeCodeBrain:
    return ClaudeCodeBrain(
        workspace=tmp_path,
        session=FakeSession(),
        running_tasks=running_tasks,
    )


def _patch_spawn(monkeypatch, proc: FakeProc) -> None:
    async def _fake_spawn(*_argv, **_kwargs) -> FakeProc:
        return proc

    monkeypatch.setattr(brain_module.asyncio, "create_subprocess_exec", _fake_spawn)


def test_brain_registers_and_unregisters_on_normal_exit(
    monkeypatch, tmp_path, patch_killpg
):
    proc = FakeProc(pid=111, mode="ok", stdout=b"hello sir")
    patch_killpg[111] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> str:
        return await brain.respond("ping", chat_id=10)

    reply = asyncio.run(scenario())
    assert reply == "hello sir"
    assert not reg.is_running(10)


def test_brain_unregisters_on_brain_error(monkeypatch, tmp_path, patch_killpg):
    proc = FakeProc(pid=112, mode="fail", stdout=b"", stderr=b"boom", returncode=2)
    patch_killpg[112] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        with pytest.raises(BrainError):
            await brain.respond("ping", chat_id=11)

    asyncio.run(scenario())
    assert not reg.is_running(11)


def test_brain_timeout_kills_proc_and_unregisters(monkeypatch, tmp_path, patch_killpg):
    proc = FakeProc(pid=113, mode="hang")
    patch_killpg[113] = proc
    _patch_spawn(monkeypatch, proc)
    monkeypatch.setattr(brain_module, "BRAIN_TIMEOUT_SECONDS", 0.05)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        with pytest.raises(BrainTimeoutError):
            await brain.respond("hang please", chat_id=12)

    asyncio.run(scenario())
    assert not reg.is_running(12)
    assert signal.SIGTERM in proc.signals


def test_brain_cancel_after_attach_raises_brain_cancelled(
    monkeypatch, tmp_path, patch_killpg
):
    proc = FakeProc(pid=114, mode="hang", stdout=b"partial", stderr=b"")
    patch_killpg[114] = proc
    _patch_spawn(monkeypatch, proc)
    monkeypatch.setattr(brain_module, "BRAIN_TIMEOUT_SECONDS", 5)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        respond_task = asyncio.create_task(brain.respond("work", chat_id=13))
        # Wait for the brain to reserve + attach the proc.
        for _ in range(100):
            if reg.is_running(13) and signal.SIGTERM not in proc.signals:
                # Reserved; small extra spin to ensure attach happened
                # before we cancel — otherwise we'd be testing the
                # reservation-window path instead.
                await asyncio.sleep(0.01)
                break
            await asyncio.sleep(0.01)
        cancelled = await reg.cancel(13)
        assert cancelled is True
        with pytest.raises(BrainCancelled):
            await respond_task

    asyncio.run(scenario())
    assert not reg.is_running(13)
    assert signal.SIGTERM in proc.signals


def test_brain_cancel_during_reservation_window_kills_spawned_proc(
    monkeypatch, tmp_path, patch_killpg
):
    """The race fix: /cancel arrives after reserve() but before the
    spawn returns. Brain's attach() gets False, kills the freshly-
    spawned proc, and raises BrainCancelled."""
    proc = FakeProc(pid=300, mode="hang")
    patch_killpg[300] = proc

    spawn_gate = asyncio.Event()
    cancel_done = asyncio.Event()

    async def _gated_spawn(*_argv, **_kwargs) -> FakeProc:
        # Tell the test the brain has reached spawn (slot is reserved),
        # then wait until the test fires /cancel before returning the proc.
        spawn_gate.set()
        await cancel_done.wait()
        return proc

    monkeypatch.setattr(brain_module.asyncio, "create_subprocess_exec", _gated_spawn)
    monkeypatch.setattr(brain_module, "BRAIN_TIMEOUT_SECONDS", 5)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        respond_task = asyncio.create_task(brain.respond("work", chat_id=20))
        await asyncio.wait_for(spawn_gate.wait(), timeout=1.0)
        # Brain has reserve()d but hasn't attach()ed yet.
        assert reg.is_running(20)
        cancelled = await reg.cancel(20)
        assert cancelled is True
        # Now release the spawn so attach() happens with cancelled=True.
        cancel_done.set()
        with pytest.raises(BrainCancelled):
            await respond_task

    asyncio.run(scenario())
    assert not reg.is_running(20)
    # Brain killed the orphan proc itself once attach returned False.
    assert signal.SIGTERM in proc.signals


def test_brain_concurrent_call_for_same_chat_raises_before_spawn(
    monkeypatch, tmp_path, patch_killpg
):
    """Reserve-first means the second respond() for a chat_id raises
    TaskAlreadyRunning before it even spawns a subprocess — the fix's
    side benefit: no leak to clean up because nothing was spawned."""
    first = FakeProc(pid=201, mode="hang")
    patch_killpg[201] = first

    spawn_calls = 0

    async def _fake_spawn(*_argv, **_kwargs) -> FakeProc:
        nonlocal spawn_calls
        spawn_calls += 1
        return first

    monkeypatch.setattr(brain_module.asyncio, "create_subprocess_exec", _fake_spawn)
    monkeypatch.setattr(brain_module, "BRAIN_TIMEOUT_SECONDS", 5)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        first_task = asyncio.create_task(brain.respond("a", chat_id=14))
        for _ in range(100):
            if reg.is_running(14):
                break
            await asyncio.sleep(0.01)
        assert reg.is_running(14)

        with pytest.raises(TaskAlreadyRunning):
            await brain.respond("b", chat_id=14)

        # Only the first call should have spawned a subprocess.
        assert spawn_calls == 1

        # Tear down the first call so the test exits cleanly.
        await reg.cancel(14)
        with pytest.raises(BrainCancelled):
            await first_task

    asyncio.run(scenario())
    assert not reg.is_running(14)
