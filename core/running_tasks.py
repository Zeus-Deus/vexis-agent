"""Registry for currently-running brain subprocesses, keyed by chat_id.

The reserve / attach split closes a race: the brain reserves the slot
*before* spawning the subprocess, then attaches the spawned proc once
it exists. A `/cancel` arriving between those two steps is captured by
the slot's cancelled flag and surfaced to the brain via attach() →
False, so it can kill the just-spawned proc and raise BrainCancelled
instead of running to completion.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass

log = logging.getLogger(__name__)


class TaskAlreadyRunning(Exception):
    """Raised when reserve() is called for a chat_id that already has a slot."""


@dataclass(frozen=True)
class Reservation:
    """Opaque token returned by reserve(); pass to attach() once spawn succeeds."""

    chat_id: int


@dataclass
class _Slot:
    proc: asyncio.subprocess.Process | None = None
    cancelled: bool = False


class RunningTasks:
    def __init__(self) -> None:
        self._slots: dict[int, _Slot] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, chat_id: int) -> Reservation:
        """Allocate a slot for chat_id before the subprocess exists.

        Raises TaskAlreadyRunning if chat_id already has a slot. The
        returned Reservation is passed to attach() once the proc spawns.
        """
        async with self._lock:
            if chat_id in self._slots:
                raise TaskAlreadyRunning(
                    f"chat_id {chat_id} already has a running task"
                )
            self._slots[chat_id] = _Slot()
        log.info("Reserved slot for chat %d", chat_id)
        return Reservation(chat_id=chat_id)

    async def attach(
        self,
        reservation: Reservation,
        proc: asyncio.subprocess.Process,
    ) -> bool:
        """Attach a spawned proc to a reservation.

        Returns False if a `/cancel` arrived during the reservation
        window (before attach) — caller must kill the proc itself and
        raise BrainCancelled. Returns True for the normal flow.
        """
        async with self._lock:
            slot = self._slots.get(reservation.chat_id)
            cancelled_during_window = slot is not None and slot.cancelled
            if slot is None or slot.cancelled:
                log.info(
                    "Attached PID %d to chat %d reservation "
                    "(cancelled_during_window=%s)",
                    proc.pid,
                    reservation.chat_id,
                    cancelled_during_window,
                )
                return False
            slot.proc = proc
        log.info(
            "Attached PID %d to chat %d reservation (cancelled_during_window=False)",
            proc.pid,
            reservation.chat_id,
        )
        return True

    async def cancel(self, chat_id: int, grace_seconds: float = 2.0) -> bool:
        """Cancel the running or pending task for chat_id.

        - Slot with attached proc: SIGTERM, 2s grace, SIGKILL.
        - Slot reserved but not yet attached: flag the slot so attach()
          returns False; the brain kills the about-to-spawn proc itself.
        - No slot: return False so the transport replies "Nothing to cancel."
        """
        async with self._lock:
            slot = self._slots.get(chat_id)
            slot_exists = slot is not None
            has_proc = slot is not None and slot.proc is not None
            if slot is None:
                log.info(
                    "Cancel requested for chat %d "
                    "(slot_exists=False, has_proc=False, returning=False)",
                    chat_id,
                )
                return False
            slot.cancelled = True
            proc = slot.proc
        log.info(
            "Cancel requested for chat %d "
            "(slot_exists=%s, has_proc=%s, returning=True)",
            chat_id,
            slot_exists,
            has_proc,
        )
        if proc is None:
            # Reservation window: nothing to kill yet. The flag is set; attach()
            # will see it and the brain will tear down the proc once it exists.
            return True
        await _kill_group(proc, grace_seconds, chat_id=chat_id)
        return True

    async def unregister(self, chat_id: int) -> None:
        """Drop the slot for chat_id. Called from the brain's `finally`
        on every exit path."""
        async with self._lock:
            existed = self._slots.pop(chat_id, None) is not None
        if existed:
            log.info("Unregistered chat %d", chat_id)

    def is_running(self, chat_id: int) -> bool:
        """True if chat_id has a reserved slot (with or without proc)."""
        return chat_id in self._slots

    def was_cancelled(self, chat_id: int) -> bool:
        """True if cancel() set the flag on the current slot. Cleared
        when the slot is removed via unregister()."""
        slot = self._slots.get(chat_id)
        return slot is not None and slot.cancelled


async def _kill_group(
    proc: asyncio.subprocess.Process,
    grace_seconds: float,
    *,
    chat_id: int | None = None,
) -> None:
    if proc.returncode is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    log.info("Sending SIGTERM to PID %d (chat %s)", proc.pid, chat_id)
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        return
    except asyncio.TimeoutError:
        pass
    log.info(
        "SIGTERM grace period expired, sending SIGKILL to PID %d (chat %s)",
        proc.pid,
        chat_id,
    )
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
    except asyncio.TimeoutError:
        log.error("brain subprocess (pid=%s) ignored SIGKILL", proc.pid)
