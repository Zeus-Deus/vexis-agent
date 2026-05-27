"""Watched-agent registry: dataclass, persistence, mutators.

The on-disk format is a single JSON file at
``$XDG_STATE_HOME/vexis-agent/watcher-registry.json`` written
atomically (temp + rename). Mirrors the ``BackgroundTasks`` shape so
the operational layer (backup, doctor) treats both registries the
same way.

The registry deliberately stores ONLY the per-agent registration
metadata + last-known state. The content hash and per-poll bookkeeping
live in the in-memory ``WatcherState`` map alongside it — those bytes
are noise to disk, and a daemon restart legitimately resets them
(every freshly-loaded agent starts "running" and gets re-evaluated
on the next poll tick).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from vexis_agent.core.paths import state_dir

log = logging.getLogger(__name__)

REGISTRY_FILENAME = "watcher-registry.json"
DEFAULT_IDLE_AFTER_SECONDS = 30


class WatchStatus(str, Enum):
    RUNNING = "running"
    IDLE = "idle"
    DEAD = "dead"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WatchedAgent:
    """One registered agent.

    ``name`` is the user-facing handle (must be unique). ``source_type``
    is the plugin slug (``codemux``, future ``pty``, ``tmux``).
    ``identifier`` is the source-specific id the plugin's read /
    is_alive / describe methods consume. For the Codemux source this
    is the terminal **session id** (``session-NNNN``), NOT the
    workspace id — Codemux's ``terminal_read`` MCP tool operates on
    sessions, and a session id is stable across workspace switches
    while a workspace id only identifies the container.

    ``workspace_id`` is the user-facing handle Codemux assigns the
    enclosing workspace; we keep it around for display (``/codemux``
    output, dashboard status) and for re-resolution if the session
    is restarted. Optional because non-Codemux sources don't have one.
    """

    name: str
    source_type: str
    identifier: str
    agent_kind: str          # "claude-code", "opencode", "aider", …
    chat_id: int             # Telegram chat to notify on idle.
    registered_at: str
    idle_after_seconds: int = DEFAULT_IDLE_AFTER_SECONDS
    repo_path: Optional[str] = None
    goal_hint: Optional[str] = None
    workspace_id: Optional[str] = None
    status: str = WatchStatus.RUNNING.value
    last_output_at: Optional[str] = None
    last_notified_at: Optional[str] = None
    muted: bool = False
    last_line: Optional[str] = None
    # ``last_status_transition_at`` is the timestamp of the most
    # recent running↔idle flip. Used by the debounce logic so a
    # bursty agent that flickers idle→running→idle within a short
    # window doesn't re-trigger the notification.
    last_status_transition_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WatchedAgent":
        # Drop unknown keys so an old daemon can read a newer registry
        # without crashing on a forward-incompatible field. ``muted``
        # / ``last_line`` defaulted-in cleanly.
        allowed = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in allowed}
        return cls(**clean)


class DuplicateName(Exception):
    """register() called with a name that already exists in the registry."""


class UnknownName(Exception):
    """unregister/get/mute called with a name not in the registry."""


class WatcherRegistry:
    """In-memory registry with synchronous mutators and one big lock.

    Persistence is fire-and-forget after each mutation: we serialise to
    a temp file and atomically rename, same pattern as background_tasks
    and schedule_state. Reads do NOT touch disk — the in-memory state
    is the source of truth between writes.
    """

    def __init__(self, *, state_path: Optional[Path] = None) -> None:
        self._path = state_path or (state_dir() / REGISTRY_FILENAME)
        self._agents: dict[str, WatchedAgent] = {}
        self._lock = asyncio.Lock()
        self._load_from_disk()

    @property
    def path(self) -> Path:
        return self._path

    def _load_from_disk(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(
                "could not read watcher registry %s: %s; starting empty",
                self._path, exc,
            )
            return
        entries = data.get("agents") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                agent = WatchedAgent.from_dict(entry)
            except (TypeError, ValueError) as exc:
                log.warning("skipping malformed registry entry: %s", exc)
                continue
            self._agents[agent.name] = agent

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"agents": [a.to_dict() for a in self._agents.values()]}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # ----- reads (no lock; mutators take it) -----

    def list(self) -> list[WatchedAgent]:
        return list(self._agents.values())

    def get(self, name: str) -> Optional[WatchedAgent]:
        return self._agents.get(name)

    def active_count(self) -> int:
        """Number of agents whose status is anything except ``dead``."""
        return sum(
            1 for a in self._agents.values()
            if a.status != WatchStatus.DEAD.value
        )

    # ----- mutators -----

    async def register(self, agent: WatchedAgent) -> WatchedAgent:
        async with self._lock:
            if agent.name in self._agents:
                raise DuplicateName(
                    f"watcher already has an agent named {agent.name!r}"
                )
            self._agents[agent.name] = agent
            self._persist()
            return agent

    async def unregister(self, name: str) -> WatchedAgent:
        async with self._lock:
            agent = self._agents.pop(name, None)
            if agent is None:
                raise UnknownName(f"no watched agent named {name!r}")
            self._persist()
            return agent

    async def set_muted(self, name: str, muted: bool) -> WatchedAgent:
        async with self._lock:
            agent = self._agents.get(name)
            if agent is None:
                raise UnknownName(f"no watched agent named {name!r}")
            agent.muted = muted
            self._persist()
            return agent

    async def update_status(
        self,
        name: str,
        *,
        status: Optional[WatchStatus] = None,
        last_output_at: Optional[str] = None,
        last_notified_at: Optional[str] = None,
        last_line: Optional[str] = None,
        last_status_transition_at: Optional[str] = None,
    ) -> WatchedAgent:
        async with self._lock:
            agent = self._agents.get(name)
            if agent is None:
                raise UnknownName(f"no watched agent named {name!r}")
            if status is not None and agent.status != status.value:
                agent.status = status.value
                agent.last_status_transition_at = (
                    last_status_transition_at or _utcnow_iso()
                )
            if last_output_at is not None:
                agent.last_output_at = last_output_at
            if last_notified_at is not None:
                agent.last_notified_at = last_notified_at
            if last_line is not None:
                agent.last_line = last_line
            self._persist()
            return agent
