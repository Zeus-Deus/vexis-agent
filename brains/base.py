"""Brain interface. One method, returns plain text."""

from __future__ import annotations

from typing import Protocol


class Brain(Protocol):
    async def respond(self, message: str) -> str: ...
