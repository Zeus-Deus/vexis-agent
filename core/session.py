"""Persistent holder for the active Claude Code session UUID."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

log = logging.getLogger(__name__)


class SessionStore:
    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._session_id: str = ""
        self._initialized: bool = False
        self._load_or_init()

    def _load_or_init(self) -> None:
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text())
                self._session_id = data["session_id"]
                self._initialized = data["initialized"]
                return
            except (json.JSONDecodeError, KeyError, OSError, TypeError):
                log.warning(
                    "Session state file corrupt at %s; starting fresh",
                    self._state_path,
                )
        self._session_id = str(uuid.uuid4())
        self._initialized = False
        self._save()

    def _save(self) -> None:
        # Atomic write: same-fs temp + rename, so a crash mid-write
        # can't leave a half-written state file behind.
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "session_id": self._session_id,
                    "initialized": self._initialized,
                }
            )
        )
        tmp.replace(self._state_path)

    def get(self) -> str:
        return self._session_id

    def is_initialized(self) -> bool:
        return self._initialized

    def mark_initialized(self) -> None:
        self._initialized = True
        self._save()

    def rotate(self) -> str:
        self._session_id = str(uuid.uuid4())
        self._initialized = False
        self._save()
        return self._session_id
