"""In-memory holder for the active Claude Code session UUID."""

from __future__ import annotations

import uuid


class SessionStore:
    def __init__(self) -> None:
        self._session_id = str(uuid.uuid4())
        self._initialized = False

    def get(self) -> str:
        return self._session_id

    def is_initialized(self) -> bool:
        return self._initialized

    def mark_initialized(self) -> None:
        self._initialized = True

    def rotate(self) -> str:
        self._session_id = str(uuid.uuid4())
        self._initialized = False
        return self._session_id
