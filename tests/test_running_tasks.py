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

from vexis_agent.core.running_tasks import QueuedMessage, RunningTasks, TaskAlreadyRunning


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

    monkeypatch.setattr("vexis_agent.core.running_tasks.os.getpgid", _getpgid)
    monkeypatch.setattr("vexis_agent.core.running_tasks.os.killpg", _killpg)
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


def test_double_cancel_in_reservation_window_first_takes_effect():
    """Double-tapped /cancel: the first call cancels the chat; the second
    is a no-op so the user doesn't get a duplicate "Cancelled, sir" reply.
    Either way the slot is flagged so attach() still returns False."""

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
    assert second is False
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


# --- drain ownership + queue ----------------------------------------------


def test_claim_grants_exclusive_drain_ownership():
    async def scenario() -> tuple[bool, bool]:
        reg = RunningTasks()
        first = await reg.claim(50)
        second = await reg.claim(50)
        return first, second

    first, second = asyncio.run(scenario())
    assert first is True
    assert second is False


def test_pop_or_release_returns_queued_message_then_releases():
    async def scenario() -> tuple[QueuedMessage | None, QueuedMessage | None, bool]:
        reg = RunningTasks()
        await reg.claim(60)
        await reg.enqueue(60, user_id=99, text="follow-up A")
        first = await reg.pop_or_release(60)
        # Queue is now empty; next pop releases ownership.
        second = await reg.pop_or_release(60)
        return first, second, reg.is_running(60)

    first, second, still_running = asyncio.run(scenario())
    assert isinstance(first, QueuedMessage)
    assert first.user_id == 99
    assert first.text == "follow-up A"
    assert second is None
    assert still_running is False


def test_after_release_a_new_claim_succeeds():
    async def scenario() -> tuple[bool, bool]:
        reg = RunningTasks()
        await reg.claim(61)
        await reg.pop_or_release(61)  # releases
        new_claim = await reg.claim(61)
        return reg.is_running(61), new_claim

    is_running, new_claim = asyncio.run(scenario())
    assert is_running is True
    assert new_claim is True


def test_pop_or_release_on_unclaimed_chat_returns_none():
    async def scenario() -> tuple[QueuedMessage | None, bool]:
        reg = RunningTasks()
        result = await reg.pop_or_release(62)
        return result, reg.is_running(62)

    result, running = asyncio.run(scenario())
    assert result is None
    assert running is False


def test_queue_preserves_fifo_order():
    async def scenario() -> list[str]:
        reg = RunningTasks()
        await reg.claim(70)
        await reg.enqueue(70, user_id=1, text="one")
        await reg.enqueue(70, user_id=1, text="two")
        await reg.enqueue(70, user_id=1, text="three")
        out: list[str] = []
        while True:
            msg = await reg.pop_or_release(70)
            if msg is None:
                break
            out.append(msg.text)
        return out

    assert asyncio.run(scenario()) == ["one", "two", "three"]


def test_cancel_clears_queue_and_stops_drain(patch_killpg):
    """The headline requirement: /cancel drops queued follow-ups too."""

    async def scenario() -> tuple[bool, QueuedMessage | None, bool]:
        reg = RunningTasks()
        proc = FakeProc(pid=400)
        patch_killpg[400] = proc
        await reg.claim(80)
        # Simulate the brain having reserved + attached for the current turn.
        reservation = await reg.reserve(80)
        await reg.attach(reservation, proc)
        # User stacked up two follow-ups while the brain was busy.
        await reg.enqueue(80, user_id=1, text="msg2")
        await reg.enqueue(80, user_id=1, text="msg3")
        cancelled = await reg.cancel(80)
        # Brain finishes (proc killed) and unregisters its slot.
        await reg.unregister(80)
        # Drain loop pops next message and should get None — queue was cleared.
        next_msg = await reg.pop_or_release(80)
        return cancelled, next_msg, reg.is_running(80)

    cancelled, next_msg, still_running = asyncio.run(scenario())
    assert cancelled is True
    assert next_msg is None
    assert still_running is False


def test_cancel_with_only_queued_messages_returns_true(patch_killpg):
    """No active subprocess but a non-empty queue still counts as 'something
    to cancel' — otherwise queued follow-ups would silently drain."""

    async def scenario() -> tuple[bool, QueuedMessage | None]:
        reg = RunningTasks()
        await reg.claim(81)
        await reg.enqueue(81, user_id=1, text="queued")
        cancelled = await reg.cancel(81)
        next_msg = await reg.pop_or_release(81)
        return cancelled, next_msg

    cancelled, next_msg = asyncio.run(scenario())
    assert cancelled is True
    assert next_msg is None


def test_cancel_when_truly_idle_returns_false():
    async def scenario() -> bool:
        reg = RunningTasks()
        return await reg.cancel(82)

    assert asyncio.run(scenario()) is False


def test_is_running_true_during_drain_between_turns():
    """Between turns the slot is unregistered but drain ownership persists,
    so a fresh message should still see the chat as busy."""

    async def scenario() -> bool:
        reg = RunningTasks()
        await reg.claim(83)
        # Simulate: brain finished a turn, slot unregistered, drain still owns.
        reservation = await reg.reserve(83)
        # No attach — same effect as a turn that finished and unregistered.
        await reg.unregister(83)
        return reg.is_running(83)

    assert asyncio.run(scenario()) is True


def test_post_cancel_message_survives_to_be_taken_over():
    """Scenario 4: /cancel fires, then a new message arrives during
    cleanup. The new message must NOT be silently lost — it should
    survive the drain release and be reclaimable via
    take_over_if_pending so the next drain turn processes it."""

    async def scenario() -> tuple[QueuedMessage | None, bool]:
        reg = RunningTasks()
        await reg.claim(90)
        # Cancel: clears any pre-cancel queued items, flags drain to stop.
        await reg.enqueue(90, user_id=1, text="pre-cancel item")
        await reg.cancel(90)
        # Caller's enqueue races in *after* the cancel cleared the queue.
        await reg.enqueue(90, user_id=1, text="post-cancel msg")
        # Old drain finishes its current turn and calls pop_or_release.
        # Cancellation is flagged so it returns None, but the post-cancel
        # message must remain in the queue.
        released = await reg.pop_or_release(90)
        # And take_over_if_pending should hand it back so the same drain
        # task can keep processing.
        recovered = await reg.take_over_if_pending(90)
        return recovered, released is None

    recovered, was_released = asyncio.run(scenario())
    assert was_released is True
    assert isinstance(recovered, QueuedMessage)
    assert recovered.text == "post-cancel msg"


def test_take_over_returns_none_when_someone_else_claimed():
    """If a fresh _dispatch_to_brain wins the race after pop_or_release
    releases, take_over_if_pending must return None — the racing
    claimant owns the queue."""

    async def scenario() -> QueuedMessage | None:
        reg = RunningTasks()
        await reg.claim(91)
        await reg.cancel(91)
        await reg.enqueue(91, user_id=1, text="orphan")
        await reg.pop_or_release(91)  # release after cancel
        # Simulate a new dispatch racing in.
        assert await reg.claim(91) is True
        return await reg.take_over_if_pending(91)

    assert asyncio.run(scenario()) is None


def test_take_over_returns_none_when_queue_empty():
    async def scenario() -> QueuedMessage | None:
        reg = RunningTasks()
        return await reg.take_over_if_pending(92)

    assert asyncio.run(scenario()) is None


def test_take_over_blocked_when_slot_still_in_flight(patch_killpg):
    """Defensive: if a previous spawn slot hasn't been unregistered yet
    (e.g., proc still being killed), take_over must back off. Otherwise
    a fresh reserve() would race against the not-yet-cleared slot.

    Uses ``patch_killpg`` so the cancel's ``_kill_group`` sees a fake
    killpg that delivers SIGTERM cleanly. Previously this test relied
    on real ``os.killpg(999, …)`` raising ``ProcessLookupError`` — but
    on hosts where pid 999 is allocated to another user, killpg raises
    ``PermissionError`` instead and the test fails. The fixture makes
    the test deterministic regardless of the host's pid allocation.
    """

    async def scenario() -> QueuedMessage | None:
        reg = RunningTasks()
        proc = FakeProc(pid=999)
        patch_killpg[999] = proc
        await reg.claim(93)
        reservation = await reg.reserve(93)
        await reg.attach(reservation, proc)
        await reg.cancel(93)
        await reg.enqueue(93, user_id=1, text="post-cancel")
        # Pop_or_release while slot still attached — it can run because
        # it doesn't touch the slot, just drain ownership.
        await reg.pop_or_release(93)
        # Slot still has the (dying) proc; take_over must refuse.
        return await reg.take_over_if_pending(93)

    assert asyncio.run(scenario()) is None


def test_force_release_drain_clears_state_after_exception():
    """Scenario 1: drain task aborted via unexpected exception. The
    force_release_drain safety net releases ownership and drops queued
    follow-ups so the chat isn't permanently 'busy'."""

    async def scenario() -> tuple[bool, bool, int]:
        reg = RunningTasks()
        await reg.claim(94)
        await reg.enqueue(94, user_id=1, text="lost")
        await reg.enqueue(94, user_id=1, text="also lost")
        running_before = reg.is_running(94)
        await reg.force_release_drain(94)
        return running_before, reg.is_running(94), reg.queue_depth(94)

    before, after, depth = asyncio.run(scenario())
    assert before is True
    assert after is False
    assert depth == 0


def test_force_release_drain_is_noop_when_drain_already_released():
    async def scenario() -> bool:
        reg = RunningTasks()
        await reg.claim(95)
        # Clean release.
        await reg.pop_or_release(95)
        # Now a fresh dispatch comes in for the same chat.
        await reg.claim(95)
        # force_release should NOT clobber this active drain — it only
        # acts when state.drain_active is True at call time, which it
        # is here, but importantly: the previous drain's force_release
        # in finally would have run before this claim. Verify that the
        # noop path covers the released-cleanly case.
        # (We re-test by releasing cleanly first and then calling.)
        await reg.pop_or_release(95)
        await reg.force_release_drain(95)  # noop
        return reg.is_running(95)

    assert asyncio.run(scenario()) is False


def test_cancel_between_turns_aborts_next_brain_spawn():
    """Scenario 2: drain just finished turn N (slot unregistered).
    /cancel fires before drain calls pop_or_release. The next
    reserve() must inherit the cancellation flag so the next brain
    turn aborts before running."""

    async def scenario() -> bool:
        reg = RunningTasks()
        await reg.claim(96)
        # Simulate turn N having just finished: slot was reserved,
        # attached, then unregistered in the brain's finally.
        reservation = await reg.reserve(96)
        await reg.unregister(96)
        # User /cancels in the gap before drain pops the next item.
        await reg.enqueue(96, user_id=1, text="next turn input")
        await reg.cancel(96)
        # Drain pops next; cancellation released ownership, queue
        # preserved. (We're verifying that the *reserve* would inherit
        # the cancellation flag, so simulate a brain spawning that
        # raced past the cancel into reserve.)
        # Reset drain ownership artificially to simulate the same chat
        # somehow getting to a new reserve.
        # Use a fresh reservation directly to test the propagation.
        del reservation  # unused; kept for clarity
        # Re-claim so reserve can run.
        await reg.pop_or_release(96)  # cancellation release
        # Reset drain_cancelled for a fresh claim path.
        await reg.take_over_if_pending(96)
        # Now reserve again — drain_cancelled should be False at this
        # point (claim/take_over both reset it), so the new slot is
        # fresh. Verify reverse: when drain_cancelled is set,
        # reserve() flags the slot.
        reg2 = RunningTasks()
        await reg2.claim(97)
        # Set drain_cancelled directly via cancel.
        await reg2.cancel(97)
        # cancel released ownership? No — cancel only sets flags. Slot is None.
        # We need to assert that a reserve done while drain_cancelled is True
        # produces a slot with cancelled=True.
        new_reservation = await reg2.reserve(97)
        return reg2.was_cancelled(97)

    assert asyncio.run(scenario()) is True


def test_queue_depth_reflects_pending_messages():
    async def scenario() -> tuple[int, int, int]:
        reg = RunningTasks()
        await reg.claim(84)
        d0 = reg.queue_depth(84)
        await reg.enqueue(84, user_id=1, text="a")
        await reg.enqueue(84, user_id=1, text="b")
        d2 = reg.queue_depth(84)
        await reg.pop_or_release(84)
        d1 = reg.queue_depth(84)
        return d0, d2, d1

    assert asyncio.run(scenario()) == (0, 2, 1)
