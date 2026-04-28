"""Single-user auth check."""

from __future__ import annotations


def is_allowed(user_id: int, allowed_id: int) -> bool:
    return user_id == allowed_id
