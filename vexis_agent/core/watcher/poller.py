"""Watcher polling loop.

The loop walks the registry every ``poll_interval_seconds`` and, per
agent, asks its plugin for recent output. Output diff is computed by
hashing the bytes so we never have to buffer the scrollback. State
transitions are then:

  running → idle    : no new bytes for ``idle_after_seconds``.
                       Fires ONE notification per transition.
  idle    → running : new bytes after going idle. NO notification
                       (we don't ping the user on "started moving
                       again"; the next idle settles the round).
  *       → dead    : Source.is_alive returned False or the source
                       raised SourceUnavailable on read. No notification.

Debounce contract:
  A burst running→idle→running→idle inside ``oscillation_window_seconds``
  is NOT re-notified. Concretely: a second idle transition that lands
  within the window of the previous one is silently absorbed (state
  flips but notification path is skipped). Default 60s; tuneable for
  tests.

Bytes hashing only — we never keep the raw output around, so the
runtime memory cost is O(agents), not O(scrollback).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from vexis_agent.core.watcher.registry import (
    WatchStatus,
    WatchedAgent,
    WatcherRegistry,
)
from vexis_agent.core.watcher.sources import (
    Source,
    SourceUnavailable,
    get_source,
)

log = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_OSCILLATION_WINDOW_SECONDS = 60

NotifyFn = Callable[[int, str], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


@dataclass
class _AgentRuntimeState:
    """In-memory poll bookkeeping. Never persisted."""

    last_hash: Optional[str] = None
    # Wall clock of the most recent observed output mutation.
    last_change_at: datetime = field(default_factory=_utcnow)
    # Wall clock of the most recent fired idle notification — used
    # for oscillation debounce. ``None`` means we've never notified.
    last_notified_at: Optional[datetime] = None


def _digest(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    hours = seconds / 3600
    return f"{hours:.1f}h"


def render_idle_notification(agent: WatchedAgent, idle_seconds: float) -> str:
    """The text body for the one Telegram ping per idle transition.

    Kept module-level so tests can pin the wording without going
    through the notify-callback indirection.
    """
    parts: list[str] = [
        f"Workspace `{agent.name}` (agent: {agent.agent_kind}) went "
        f"idle after {_fmt_elapsed(idle_seconds)}.",
    ]
    if agent.goal_hint:
        parts.append(f"Goal: {agent.goal_hint}")
    if agent.last_line:
        parts.append(f"Last line: {agent.last_line[:200]}")
    parts.append(
        f"Reply `tail {agent.name}` to peek, "
        f"`peek {agent.name}` to summarize, "
        f"`mute {agent.name}` to silence."
    )
    return "\n".join(parts)


class WatcherPoller:
    """Drives the polling loop.

    Owns the registry, in-memory runtime state, and the notify callback.
    Start/stop are async-friendly: :meth:`start` schedules the loop on
    the running event loop, :meth:`stop` cancels and awaits it.
    """

    def __init__(
        self,
        registry: WatcherRegistry,
        *,
        notify: Optional[NotifyFn] = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        oscillation_window_seconds: float = DEFAULT_OSCILLATION_WINDOW_SECONDS,
        source_lookup: Callable[[str], Optional[Source]] = get_source,
    ) -> None:
        self._registry = registry
        self._notify = notify
        self._interval = poll_interval_seconds
        self._oscillation_window = oscillation_window_seconds
        self._source_lookup = source_lookup
        self._state: dict[str, _AgentRuntimeState] = {}
        self._task: Optional[asyncio.Task] = None

    def set_notify(self, notify: NotifyFn) -> None:
        self._notify = notify

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="watcher-poller")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        try:
            while True:
                try:
                    await self.tick()
                except Exception:
                    # The loop must outlive any per-tick blow-up so a
                    # single bad source never silences the whole watcher.
                    log.exception("watcher poll tick raised")
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            raise

    async def tick(self) -> None:
        """One full pass over the registry. Public for tests."""
        for agent in self._registry.list():
            await self._tick_one(agent)

    async def _tick_one(self, agent: WatchedAgent) -> None:
        if agent.status == WatchStatus.DEAD.value:
            return
        source = self._source_lookup(agent.source_type)
        if source is None:
            log.debug(
                "no source plugin registered for %s; skipping %s",
                agent.source_type, agent.name,
            )
            return
        runtime = self._state.setdefault(agent.name, _AgentRuntimeState())
        # Liveness probe. If the source says the agent is gone, mark
        # dead and bail — no notification on death by design.
        try:
            alive = await source.is_alive(agent.identifier)
        except Exception:
            log.exception("is_alive raised for %s", agent.name)
            alive = True  # Bias toward keeping the entry; transient probe failures shouldn't reap.
        if not alive:
            await self._registry.update_status(
                agent.name, status=WatchStatus.DEAD,
            )
            return
        try:
            output = await source.read_recent_output(agent.identifier)
        except SourceUnavailable as exc:
            log.info("source unavailable for %s: %s", agent.name, exc)
            await self._registry.update_status(
                agent.name, status=WatchStatus.DEAD,
            )
            return
        except Exception:
            log.exception("read_recent_output raised for %s", agent.name)
            return
        digest = _digest(output) if output else None
        now = _utcnow()
        changed = digest != runtime.last_hash
        if changed:
            runtime.last_hash = digest
            runtime.last_change_at = now
            await self._on_output_observed(agent, now, output)
            return
        # No change: check if we crossed the idle threshold.
        idle_for = (now - runtime.last_change_at).total_seconds()
        if (
            idle_for >= agent.idle_after_seconds
            and agent.status == WatchStatus.RUNNING.value
        ):
            await self._transition_idle(agent, runtime, now, idle_for)

    async def _on_output_observed(
        self,
        agent: WatchedAgent,
        now: datetime,
        output: bytes,
    ) -> None:
        last_line = _last_nonempty_line(output)
        if agent.status == WatchStatus.IDLE.value:
            await self._registry.update_status(
                agent.name,
                status=WatchStatus.RUNNING,
                last_output_at=now.isoformat(),
                last_line=last_line,
            )
        else:
            await self._registry.update_status(
                agent.name,
                last_output_at=now.isoformat(),
                last_line=last_line,
            )

    async def _transition_idle(
        self,
        agent: WatchedAgent,
        runtime: _AgentRuntimeState,
        now: datetime,
        idle_for: float,
    ) -> None:
        updated = await self._registry.update_status(
            agent.name, status=WatchStatus.IDLE,
        )
        if updated.muted:
            log.debug("watcher: %s went idle but is muted", agent.name)
            return
        if runtime.last_notified_at is not None:
            since_last = (now - runtime.last_notified_at).total_seconds()
            if since_last < self._oscillation_window:
                log.info(
                    "watcher: %s went idle again within %.0fs (debounce window) — skipping notify",
                    agent.name, self._oscillation_window,
                )
                return
        runtime.last_notified_at = now
        await self._registry.update_status(
            agent.name, last_notified_at=now.isoformat(),
        )
        if self._notify is None:
            log.warning(
                "watcher: %s went idle but no notify callback wired",
                agent.name,
            )
            return
        try:
            await self._notify(
                agent.chat_id,
                render_idle_notification(updated, idle_for),
            )
        except Exception:
            log.exception("watcher notify failed for %s", agent.name)


def _last_nonempty_line(output: bytes) -> Optional[str]:
    if not output:
        return None
    for raw in reversed(output.splitlines()):
        text = raw.decode("utf-8", errors="replace").strip()
        if text:
            return text
    return None
