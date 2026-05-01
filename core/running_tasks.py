"""Per-chat brain-call coordination: drain ownership, queue, and slot.

Three layers of state per chat, all keyed by chat_id and guarded by a
single lock:

  1. **Drain ownership** (``drain_active``). Set when the transport's
     drain loop is processing this chat. Held across multiple turns so
     a follow-up message arriving mid-turn enqueues instead of starting
     a parallel drain.

  2. **Queue** (``queue``). Messages submitted while the drain was
     already active. The drain pops them after each turn finishes; an
     empty pop releases ownership. ``cancel`` clears it.

  3. **Slot** (``slot``). The per-spawn subprocess record. ``reserve``
     allocates it before the proc exists so a ``/cancel`` arriving in
     the spawn window can flag the slot and the brain tears the proc
     down once it materialises (the original race fix). ``attach``
     binds the spawned proc; ``unregister`` clears the slot at the end
     of every brain turn but leaves drain ownership and the queue in
     place so the loop can continue.

``cancel`` cuts across all three: kill the proc (if attached or
spawning), clear the queue, and flag the drain to stop after the
current turn unwinds. Returns False only when nothing was happening
for the chat.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class TaskAlreadyRunning(Exception):
    """Raised when reserve() is called for a chat_id that already has a slot."""


@dataclass(frozen=True)
class Reservation:
    """Opaque token returned by reserve(); pass to attach() once spawn succeeds."""

    chat_id: int


@dataclass(frozen=True)
class QueuedMessage:
    """One pending follow-up awaiting its turn in the drain loop."""

    user_id: int
    text: str


@dataclass
class _Slot:
    proc: asyncio.subprocess.Process | None = None
    cancelled: bool = False


@dataclass
class _ChatState:
    drain_active: bool = False
    drain_cancelled: bool = False
    queue: deque[QueuedMessage] = field(default_factory=deque)
    slot: _Slot | None = None


class RunningTasks:
    def __init__(self) -> None:
        self._chats: dict[int, _ChatState] = {}
        # Per-chat timestamp of the most recent drain release. Survives
        # the chat's _ChatState being dropped from _chats; cleared only
        # at process exit. /status reads this to render "Idle for Xm"
        # for chats that aren't currently busy.
        self._last_idle: dict[int, datetime] = {}
        self._lock = asyncio.Lock()

    # ----- drain-loop ownership (transport) ---------------------------

    async def claim(self, chat_id: int) -> bool:
        """Try to take ownership of the chat's drain loop.

        Returns True if the caller now owns the drain — they should run
        the brain turn for the message they just submitted, then loop
        on ``pop_or_release`` until it returns None. Returns False if
        another drain loop is already active; the caller should call
        ``enqueue`` instead.
        """
        async with self._lock:
            state = self._chats.setdefault(chat_id, _ChatState())
            if state.drain_active:
                return False
            state.drain_active = True
            state.drain_cancelled = False
        log.info("Claimed drain for chat %d", chat_id)
        return True

    async def enqueue(self, chat_id: int, user_id: int, text: str) -> int:
        """Append a follow-up message to the chat's queue. Should only be
        called after ``claim`` returned False. Returns the new depth."""
        async with self._lock:
            state = self._chats.setdefault(chat_id, _ChatState())
            state.queue.append(QueuedMessage(user_id=user_id, text=text))
            depth = len(state.queue)
        log.info("Enqueued message for chat %d (depth=%d)", chat_id, depth)
        return depth

    async def pop_or_release(self, chat_id: int) -> QueuedMessage | None:
        """Pop the next queued message, or release drain ownership.

        Atomic. Three cases:

        - Queue has items and cancellation is *not* flagged: pop and
          return the head.
        - Cancellation flagged: release ownership and return None
          *without* clearing the queue. ``cancel`` already dropped the
          items present at cancel-time; anything in the queue here was
          submitted *after* the cancel and must be preserved so a
          fresh drain (via ``take_over_if_pending`` or a new ``claim``)
          can process it. Otherwise the user's post-cancel message
          would be silently lost.
        - Queue empty: release ownership and return None.
        """
        async with self._lock:
            state = self._chats.get(chat_id)
            if state is None or not state.drain_active:
                return None
            if state.drain_cancelled:
                state.drain_active = False
                state.drain_cancelled = False
                survivors = len(state.queue)
                self._last_idle[chat_id] = datetime.now(timezone.utc)
                self._maybe_drop_state(chat_id, state)
                log.info(
                    "Released drain for chat %d (cancelled, %d post-cancel "
                    "items preserved for handover)",
                    chat_id,
                    survivors,
                )
                return None
            if not state.queue:
                state.drain_active = False
                state.drain_cancelled = False
                self._last_idle[chat_id] = datetime.now(timezone.utc)
                self._maybe_drop_state(chat_id, state)
                log.info("Released drain for chat %d (queue empty)", chat_id)
                return None
            msg = state.queue.popleft()
        log.info("Popped queued message for chat %d", chat_id)
        return msg

    async def take_over_if_pending(
        self, chat_id: int
    ) -> QueuedMessage | None:
        """Reclaim drain ownership and pop the head of the queue, but
        only if there's actually pending work and nothing else is
        running.

        Used by the drain loop *after* ``pop_or_release`` returns None
        to recover messages that were submitted while a previous drain
        was unwinding from a ``/cancel``. Atomic: if anything else has
        already claimed the chat (a fresh ``_dispatch_to_brain`` racing
        in), this returns None and the racing claimant handles the
        queue.

        Returns None if there's no queue, drain is already owned, or a
        spawn slot is still in flight.
        """
        async with self._lock:
            state = self._chats.get(chat_id)
            if (
                state is None
                or state.drain_active
                or state.slot is not None
                or not state.queue
            ):
                return None
            state.drain_active = True
            state.drain_cancelled = False
            msg = state.queue.popleft()
        log.info("Took over drain for chat %d (post-cancel resume)", chat_id)
        return msg

    async def force_release_drain(self, chat_id: int) -> None:
        """Defensive: drop drain ownership and any queued messages.

        Belt-and-suspenders cleanup for the transport's ``finally``
        block — guarantees ownership is released even if the drain loop
        aborted via an unexpected exception before reaching
        ``pop_or_release``. No-op when the drain already released
        cleanly. Logs at warning level when it actually had to force
        because that path is a bug to investigate.
        """
        async with self._lock:
            state = self._chats.get(chat_id)
            if state is None or not state.drain_active:
                return
            dropped = len(state.queue)
            state.drain_active = False
            state.drain_cancelled = False
            state.queue.clear()
            self._last_idle[chat_id] = datetime.now(timezone.utc)
            self._maybe_drop_state(chat_id, state)
        log.warning(
            "Force-released drain for chat %d (drain task aborted, "
            "dropped %d queued)",
            chat_id,
            dropped,
        )

    # ----- per-spawn slot (brain) ------------------------------------

    async def reserve(self, chat_id: int) -> Reservation:
        """Allocate the slot for an upcoming subprocess.

        Raises TaskAlreadyRunning if a slot already exists for this chat.
        Under the drain-loop discipline (one turn at a time per chat),
        this should never trip in normal operation; the exception is a
        defensive primitive.

        Propagates ``state.drain_cancelled`` into the new slot so a
        cancel that races between turns (after the previous slot
        unregistered, before this reserve) still aborts the next turn:
        ``attach`` will see ``slot.cancelled=True`` and return False,
        causing the brain to kill the freshly spawned proc and raise
        BrainCancelled.
        """
        async with self._lock:
            state = self._chats.setdefault(chat_id, _ChatState())
            if state.slot is not None:
                raise TaskAlreadyRunning(
                    f"chat_id {chat_id} already has a running task"
                )
            slot = _Slot()
            if state.drain_cancelled:
                slot.cancelled = True
            state.slot = slot
        log.info(
            "Reserved slot for chat %d (cancel-inherited=%s)",
            chat_id,
            slot.cancelled,
        )
        return Reservation(chat_id=chat_id)

    async def attach(
        self,
        reservation: Reservation,
        proc: asyncio.subprocess.Process,
    ) -> bool:
        """Bind a spawned proc to the reservation.

        Returns False if a ``/cancel`` arrived in the reservation window
        (before attach) — the caller must kill the proc itself and raise
        BrainCancelled.
        """
        async with self._lock:
            state = self._chats.get(reservation.chat_id)
            slot = state.slot if state is not None else None
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

    async def unregister(self, chat_id: int) -> None:
        """Clear the per-spawn slot at the end of a brain turn.

        Drain ownership and the queue are unaffected — the drain loop
        keeps going.
        """
        async with self._lock:
            state = self._chats.get(chat_id)
            if state is None or state.slot is None:
                return
            state.slot = None
            self._maybe_drop_state(chat_id, state)
        log.info("Unregistered slot for chat %d", chat_id)

    # ----- cancel: stop everything for the chat ----------------------

    async def cancel(self, chat_id: int, grace_seconds: float = 2.0) -> bool:
        """Cancel everything for chat_id: kill subprocess, clear queue,
        flag drain to stop.

        Returns True if anything was cancelled (proc, queued messages,
        active drain, or reserved slot). Returns False when there's
        nothing to stop *or* when a prior cancel for this chat is still
        in flight — second case prevents the user from getting a
        duplicate "Cancelled, sir" reply on a double-tapped /cancel.
        """
        async with self._lock:
            state = self._chats.get(chat_id)
            if state is None:
                log.info(
                    "Cancel requested for chat %d (nothing to cancel)", chat_id
                )
                return False
            if state.drain_cancelled:
                # A prior cancel is already unwinding this chat. Don't
                # report "Cancelled, sir" again.
                log.info(
                    "Cancel requested for chat %d (already cancelling)", chat_id
                )
                return False
            had_drain = state.drain_active
            had_queue = len(state.queue)
            had_slot = state.slot is not None
            had_proc = had_slot and state.slot.proc is not None  # type: ignore[union-attr]
            if not (had_drain or had_queue or had_slot):
                log.info(
                    "Cancel requested for chat %d (state present but idle)", chat_id
                )
                return False
            state.queue.clear()
            state.drain_cancelled = True
            slot = state.slot
            proc: asyncio.subprocess.Process | None = None
            if slot is not None:
                slot.cancelled = True
                proc = slot.proc
        log.info(
            "Cancel chat %d (drain=%s, queue_dropped=%d, slot=%s, proc=%s)",
            chat_id,
            had_drain,
            had_queue,
            had_slot,
            had_proc,
        )
        if proc is not None:
            await _kill_group(proc, grace_seconds, chat_id=chat_id)
        return True

    # ----- introspection ---------------------------------------------

    def is_running(self, chat_id: int) -> bool:
        """True if anything is in flight for chat_id: drain owned, queue
        non-empty, or slot reserved."""
        state = self._chats.get(chat_id)
        if state is None:
            return False
        return state.drain_active or bool(state.queue) or state.slot is not None

    def queue_depth(self, chat_id: int) -> int:
        state = self._chats.get(chat_id)
        return len(state.queue) if state is not None else 0

    def last_idle_at(self, chat_id: int) -> datetime | None:
        """Timestamp of the most recent drain release for chat_id, or
        None if the chat has never been busy in this daemon's lifetime.
        Used by /status to render 'Idle for Xm'."""
        return self._last_idle.get(chat_id)

    async def snapshot(self) -> list[dict]:
        """Active chats and their current state, for the dashboard.

        Returns one entry per chat that has any state at all (drain
        owned, queued items, or a slot reserved). Read under the lock
        so we never expose a half-mutated _ChatState. The dashboard
        reads this every refresh; cost is O(chats) which in practice
        is at most one or two."""
        async with self._lock:
            out: list[dict] = []
            for chat_id, state in self._chats.items():
                slot = state.slot
                out.append(
                    {
                        "chat_id": chat_id,
                        "drain_active": state.drain_active,
                        "queue_depth": len(state.queue),
                        "slot_reserved": slot is not None,
                        "slot_pid": (slot.proc.pid if slot and slot.proc else None),
                        "cancelled": slot.cancelled if slot is not None else False,
                    }
                )
            return out

    def was_cancelled(self, chat_id: int) -> bool:
        """True if cancel() flagged the current slot. Cleared when the
        slot is unregistered."""
        state = self._chats.get(chat_id)
        return (
            state is not None
            and state.slot is not None
            and state.slot.cancelled
        )

    # ----- internal --------------------------------------------------

    def _maybe_drop_state(self, chat_id: int, state: _ChatState) -> None:
        """Remove the chat's state dict entry once it's fully idle so
        ``is_running`` and ``was_cancelled`` return False as expected."""
        if (
            not state.drain_active
            and not state.queue
            and state.slot is None
        ):
            self._chats.pop(chat_id, None)


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
