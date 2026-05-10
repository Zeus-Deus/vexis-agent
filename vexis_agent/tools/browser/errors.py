"""Error normalization for browser tool results.

Two shapes leave this module:

- Success: ``{"ok": True, ...tool-specific fields...}``
- Failure: ``{"ok": False, "error": "<one-liner>", "hint": "<optional>"}``

A third soft-hint shape rides on a successful action when the snapshot
went stale mid-call (``ok: True, snapshot_stale: True, suggestion: ...``)
— matches browser-use's ``extracted_content`` convention so the brain
treats it as "snapshot, then retry" instead of an error.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Stale-index probe pattern (browser_use/tools/service.py emits this
# verbatim from four call sites — substring is the stable bit).
_STALE_INDEX_FRAGMENT = "not available - page may have changed"


def is_stale_index_hint(extracted_content: str | None) -> bool:
    if not extracted_content:
        return False
    return _STALE_INDEX_FRAGMENT in extracted_content


def stale_index_payload(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "snapshot_stale": True,
        "suggestion": (
            "Element index is no longer valid; call browser_snapshot to refresh."
        ),
    }
    if extra:
        payload.update(extra)
    return payload


def error_payload(message: str, hint: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": message}
    if hint:
        payload["hint"] = hint
    return payload


def normalize_exception(exc: BaseException, *, action: str) -> dict[str, Any]:
    if isinstance(exc, asyncio.TimeoutError):
        return error_payload(
            f"{action} timed out",
            "The browser may be unresponsive; try the same call again, or "
            "browser_snapshot to inspect the current state.",
        )
    name = type(exc).__name__
    msg = str(exc).strip() or name
    return error_payload(f"{action} failed: {msg}")
