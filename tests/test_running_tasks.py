"""Tests for core/running_tasks.py — reserve/attach + cancel escalation.

The registry holds asyncio.subprocess.Process references but never spawns
real subprocesses; we feed it fakes that record signal calls.

Tests follow the codebase convention of sync test functions calling
asyncio.run() rather than pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import signal

import pytest

from core.running_tasks import RunningTasks, TaskAlreadyRunning


class FakeProc:
    """Stand-in for asyncio.subprocess.Process.

    Records the signals delivered to its process group (we monkeypatch
    os.killpg to write into here). `wait()` blocks until `finish()` is
    called, mimicking a real proc that exits on signal.
    """

    def __init__(self, pid: int = 1234, *, honors_term: bool = True) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._exit = asyncio.Event()
        self.signals: list[int] = []
        self.honors_term = honors_term

    async def wait(self) -> int:
        await self._exit.wait()
        assert self.returncode is not None
        return self.returncode

    def finish(self, returncode: int = -signal.SIGTERM) -> None:
        if self.returncode is None:
            self.returncode = returncode
            self._exit.set()


@pytest.fixture
def patch_killpg(monkeypatch):
    """Route os.killpg(pgid, sig) to the FakeProc whose pid we registered."""
    procs: dict[int, FakeProc] = {}

    def _getpgid(pid: int) -> int:
        return pid

    def _killpg(pgid: int, sig: int) -> None:
        proc = procs.get(pgid)
        if proc is None:
            raise ProcessLookupError(pgid)
        proc.signals.append(sig)
        if sig == signal.SIGKILL:
            proc.finish(-signal.SIGKILL)
        elif sig == signal.SIGTERM and proc.honors_term:
            proc.finish(-signal.SIGTERM)

    monkeypatch.setattr("core.running_tasks.os.getpgid", _getpgid)
    monkeypatch.setattr("core.running_tasks.os.killpg", _killpg)
    return procs


def test_reserve_and_attach_round_trip():
    async def scenario() -> None:
        reg = RunningTasks()
        proc = FakeProc()
        assert not reg.is_running(42)
        reservation = await reg.reserve(42)
        assert reg.is_running(42)
        assert await reg.attach(reservation, proc) is True
        await reg.unregister(42)
        assert not reg.is_running(42)

    asyncio.run(scenario())


def test_unregister_unknown_chat_is_noop():
    async def scenario() -> None:
        reg = RunningTasks()
        await reg.unregister(99)  # must not raise

    asyncio.run(scenario())


def test_reserve_duplicate_raises():
    async def scenario() -> None:
        reg = RunningTasks()
        await reg.reserve(1)
        with pytest.raises(TaskAlreadyRunning):
            await reg.reserve(1)

    asyncio.run(scenario())


def test_reserve_after_unregister_succeeds():
    async def scenario() -> None:
        reg = RunningTasks()
        await reg.reserve(1)
        await reg.unregister(1)
        await reg.reserve(1)  # must not raise

    asyncio.run(scenario())


def test_cancel_returns_false_when_nothing_reserved():
    async def scenario() -> bool:
        reg = RunningTasks()
        return await reg.cancel(7)

    assert asyncio.run(scenario()) is False


def test_cancel_after_attach_sigterm_path(patch_killpg):
    async def scenario() -> tuple[bool, list[int], bool]:
        reg = RunningTasks()
        proc = FakeProc(pid=100)
        patch_killpg[100] = proc
        reservation = await reg.reserve(5)
        await reg.attach(reservation, proc)
        cancelled = await reg.cancel(5)
        return cancelled, list(proc.signals), reg.was_cancelled(5)

    cancelled, signals, was_cancelled = asyncio.run(scenario())
    assert cancelled is True
    assert signals == [signal.SIGTERM]
    assert was_cancelled is True


def test_cancel_after_attach_escalates_to_sigkill(patch_killpg):
    async def scenario() -> tuple[bool, list[int]]:
        reg = RunningTasks()
        proc = FakeProc(pid=200, honors_term=False)
        patch_killpg[200] = proc
        reservation = await reg.reserve(8)
        await reg.attach(reservation, proc)
        cancelled = await reg.cancel(8, grace_seconds=0.05)
        return cancelled, list(proc.signals)

    cancelled, signals = asyncio.run(scenario())
    assert cancelled is True
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_cancel_during_reservation_window_flips_attach_to_false():
    """The race we're fixing: cancel arrives after reserve() but before
    attach(). attach() returns False so the brain knows to kill the
    just-spawned proc itself."""

    async def scenario() -> tuple[bool, bool]:
        reg = RunningTasks()
        reservation = await reg.reserve(11)
        # Cancel arrives in the window between reserve and attach.
        cancelled = await reg.cancel(11)
        # Brain finishes its spawn and tries to attach.
        proc = FakeProc()
        attached = await reg.attach(reservation, proc)
        return cancelled, attached

    cancelled, attached = asyncio.run(scenario())
    assert cancelled is True
    assert attached is False


def test_double_cancel_in_reservation_window_both_succeed():
    async def scenario() -> tuple[bool, bool, bool]:
        reg = RunningTasks()
        reservation = await reg.reserve(12)
        first = await reg.cancel(12)
        second = await reg.cancel(12)
        proc = FakeProc()
        attached = await reg.attach(reservation, proc)
        return first, second, attached

    first, second, attached = asyncio.run(scenario())
    assert first is True
    assert second is True
    assert attached is False


def test_reserve_then_unregister_without_attach_cleans_up():
    """If spawn fails between reserve and attach, the brain's `finally`
    must still leave the registry clean."""

    async def scenario() -> tuple[bool, bool]:
        reg = RunningTasks()
        await reg.reserve(13)
        before = reg.is_running(13)
        await reg.unregister(13)
        return before, reg.is_running(13)

    before, after = asyncio.run(scenario())
    assert before is True
    assert after is False


def test_attach_returns_false_when_reservation_was_unregistered():
    """Defensive: if something unregistered the slot out from under the
    brain, attach() reports the same 'cancelled' shape so the caller
    cleans up the orphan proc."""

    async def scenario() -> bool:
        reg = RunningTasks()
        reservation = await reg.reserve(14)
        await reg.unregister(14)
        return await reg.attach(reservation, FakeProc())

    assert asyncio.run(scenario()) is False


def test_was_cancelled_cleared_on_unregister():
    async def scenario() -> tuple[bool, bool]:
        reg = RunningTasks()
        await reg.reserve(3)
        await reg.cancel(3)
        before = reg.was_cancelled(3)
        await reg.unregister(3)
        return before, reg.was_cancelled(3)

    before, after = asyncio.run(scenario())
    assert before is True
    assert after is False


def test_was_cancelled_cleared_on_re_reserve():
    async def scenario() -> bool:
        reg = RunningTasks()
        await reg.reserve(4)
        await reg.cancel(4)
        await reg.unregister(4)
        await reg.reserve(4)
        return reg.was_cancelled(4)

    assert asyncio.run(scenario()) is False


def test_concurrent_reserve_and_cancel_no_deadlock(patch_killpg):
    """Hit the lock from both sides at once and confirm we make progress."""

    async def churn(reg: RunningTasks, chat_id: int) -> None:
        for i in range(10):
            proc = FakeProc(pid=chat_id * 1000 + i)
            patch_killpg[proc.pid] = proc
            reservation = await reg.reserve(chat_id)
            await reg.attach(reservation, proc)
            await reg.cancel(chat_id)
            await reg.unregister(chat_id)

    async def scenario() -> RunningTasks:
        reg = RunningTasks()
        await asyncio.wait_for(
            asyncio.gather(churn(reg, 1), churn(reg, 2), churn(reg, 3)),
            timeout=2.0,
        )
        return reg

    reg = asyncio.run(scenario())
    for cid in (1, 2, 3):
        assert not reg.is_running(cid)
