"""Persistent multi-session store. Brain still sees a single 'active' session."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_AUTO_NAME_FMT = "%Y-%m-%d-%H%M"
_MAX_AUTO_SUFFIX = 100


@dataclass(frozen=True)
class SessionInfo:
    name: str
    uuid: str
    initialized: bool
    created_at: datetime
    is_active: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid session name '{name}'. "
            "Use letters, digits, hyphens, or underscores (1-64 chars)."
        )


def _gen_name(taken: set[str]) -> str:
    # Local time for the human-facing name; created_at storage stays UTC.
    base = datetime.now().astimezone().strftime(_AUTO_NAME_FMT)
    if base not in taken:
        return base
    for n in range(2, _MAX_AUTO_SUFFIX + 1):
        candidate = f"{base}-{n}"
        if candidate not in taken:
            return candidate
    raise RuntimeError(
        f"Could not generate a unique auto-name after {_MAX_AUTO_SUFFIX} attempts"
    )


class SessionStore:
    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._active: str = ""
        self._sessions: dict[str, dict] = {}
        self._load_or_init()

    # ----- load / migrate / save -----

    def _load_or_init(self) -> None:
        if not self._state_path.exists():
            self._init_fresh()
            return
        try:
            data = json.loads(self._state_path.read_text())
        except (json.JSONDecodeError, OSError):
            self._handle_corrupt()
            return
        if not isinstance(data, dict):
            self._handle_corrupt()
            return
        # Step 3 single-session format.
        if "session_id" in data and "initialized" in data:
            try:
                self._migrate_from_old(data)
            except (KeyError, TypeError, ValueError):
                log.exception("Migration from single-session format failed")
                self._handle_corrupt()
            return
        # Step 4 multi-session format.
        if "active" in data and "sessions" in data:
            try:
                self._load_new_format(data)
            except (KeyError, TypeError, ValueError):
                log.exception("Loading multi-session format failed")
                self._handle_corrupt()
            return
        self._handle_corrupt()

    def _load_new_format(self, data: dict) -> None:
        sessions = data["sessions"]
        active = data["active"]
        if not isinstance(sessions, dict) or not sessions:
            raise ValueError("'sessions' must be a non-empty object")
        if active not in sessions:
            raise ValueError(f"active '{active}' not present in sessions")
        for name, meta in sessions.items():
            if not isinstance(meta, dict):
                raise ValueError(f"session '{name}' is not an object")
            for key in ("uuid", "initialized", "created_at"):
                if key not in meta:
                    raise ValueError(f"session '{name}' missing key '{key}'")
        self._sessions = sessions
        self._active = active

    def _migrate_from_old(self, old: dict) -> None:
        name = _gen_name(set())
        self._sessions = {
            name: {
                "uuid": str(old["session_id"]),
                "initialized": bool(old["initialized"]),
                "created_at": _utcnow().isoformat(),
            }
        }
        self._active = name
        self._save()
        log.info("Migrated single session to multi-session format as '%s'", name)

    def _handle_corrupt(self) -> None:
        ts = int(_utcnow().timestamp())
        backup = self._state_path.with_name(f"{self._state_path.name}.corrupt-{ts}")
        try:
            self._state_path.replace(backup)
            log.warning(
                "Session state corrupt at %s; backed up to %s and starting fresh",
                self._state_path,
                backup,
            )
        except OSError:
            log.warning(
                "Session state corrupt at %s; starting fresh (backup failed)",
                self._state_path,
            )
        self._init_fresh()

    def _init_fresh(self) -> None:
        name = _gen_name(set())
        self._sessions = {
            name: {
                "uuid": str(uuid.uuid4()),
                "initialized": False,
                "created_at": _utcnow().isoformat(),
            }
        }
        self._active = name
        self._save()

    def _save(self) -> None:
        # Atomic write: same-fs temp + rename.
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"active": self._active, "sessions": self._sessions}, indent=2)
        )
        tmp.replace(self._state_path)

    # ----- brain-facing API (operates on the active session) -----

    def get(self) -> str:
        return self._sessions[self._active]["uuid"]

    def is_initialized(self) -> bool:
        return self._sessions[self._active]["initialized"]

    def mark_initialized(self) -> None:
        self._sessions[self._active]["initialized"] = True
        self._save()

    def rotate(self) -> str:
        new = str(uuid.uuid4())
        self._sessions[self._active]["uuid"] = new
        self._sessions[self._active]["initialized"] = False
        self._save()
        return new

    def set(self, token: str) -> None:
        """Overwrite the active session's token without rotating to a
        fresh UUID and without flipping ``initialized``.

        Phase C Day 4: ``BrainOpenCode`` doesn't accept caller-pinned
        session ids — opencode generates the id itself and reports
        it on the first ``SessionEstablished`` event. The brain
        harvests that id and writes it back via this setter so
        subsequent ``respond()`` calls can pass ``--session <id>`` to
        resume the same conversation.

        The token is opaque to ``SessionStore`` — claude-code stores
        a UUID, opencode stores its own id (typically prefixed
        ``ses_``). Validation is the brain's job, not the store's.
        """
        if not isinstance(token, str) or not token:
            raise ValueError("session token must be a non-empty string")
        self._sessions[self._active]["uuid"] = token
        self._save()

    # ----- multi-session API -----

    def list(self) -> list[SessionInfo]:
        return [
            SessionInfo(
                name=name,
                uuid=meta["uuid"],
                initialized=meta["initialized"],
                created_at=datetime.fromisoformat(meta["created_at"]),
                is_active=(name == self._active),
            )
            for name, meta in self._sessions.items()
        ]

    def active_name(self) -> str:
        return self._active

    def create(self, name: str | None = None) -> str:
        if name is None:
            name = _gen_name(set(self._sessions.keys()))
        else:
            _validate_name(name)
            if name in self._sessions:
                raise ValueError(
                    f"A session named '{name}' already exists. "
                    f"Try /switch {name} or pick a different name."
                )
        self._sessions[name] = {
            "uuid": str(uuid.uuid4()),
            "initialized": False,
            "created_at": _utcnow().isoformat(),
        }
        self._active = name
        self._save()
        return name

    def switch(self, name: str) -> bool:
        if name not in self._sessions:
            return False
        if self._active != name:
            self._active = name
            self._save()
        return True

    def rename(self, old: str, new: str) -> bool:
        if old not in self._sessions:
            return False
        if new == old:
            return True
        _validate_name(new)
        if new in self._sessions:
            return False
        # Preserve insertion order so /sessions output stays stable.
        self._sessions = {
            (new if k == old else k): v for k, v in self._sessions.items()
        }
        if self._active == old:
            self._active = new
        self._save()
        return True

    def delete(self, name: str) -> bool:
        if name not in self._sessions:
            return False
        if len(self._sessions) <= 1:
            raise ValueError("Cannot delete the last remaining session.")
        if name == self._active:
            raise ValueError(
                "Cannot delete the active session. Switch to another first."
            )
        del self._sessions[name]
        self._save()
        return True
