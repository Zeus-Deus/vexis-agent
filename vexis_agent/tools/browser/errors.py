"""Error normalization for browser tool results.

Two shapes leave this module:

- Success: ``{"ok": True, ...tool-specific fields...}``
- Failure: ``{"ok": False, "error": "<one-liner>", "hint": "<optional>"}``

A third soft-hint shape rides on a successful action when the element
index handed out by the last snapshot no longer resolves to anything on
the page (``ok: True, snapshot_stale: True, suggestion: ...``) — the brain
treats it as "snapshot, then retry" instead of a hard error.
"""

from __future__ import annotations

import asyncio
from typing import Any

#: Hint stamped on a navigation-timeout failure once the consecutive-timeout
#: streak tripped an auto force-recycle (issue #55). Single-sourced here so
#: the streak logic in ``session.py`` and the payload wiring in ``tools.py``
#: can't drift on the copy. States the three things the brain needs: what
#: happened, that it's safe to retry, and that login state survives on disk.
FORCE_RECYCLE_HINT = (
    "The browser session was force-recycled after repeated navigation "
    "timeouts — the engine had wedged. Retry the navigation; a fresh "
    "session starts on the next call, and your login state is preserved on "
    "disk (the profile survives the recycle)."
)


def is_timeout(exc: BaseException) -> bool:
    """True when ``exc`` is a navigation/action timeout.

    Matches ``asyncio.TimeoutError`` (which IS the builtin ``TimeoutError``
    on 3.11+) and Playwright's own ``TimeoutError`` — the latter by class
    NAME so this module never imports playwright. Keeping the lazy-import
    discipline matters: an eager scrapling/playwright import at module top
    would break importing the browser package (and transitively the daemon)
    on a host where the Camoufox binary isn't fetched yet — see
    ``session.acquire``'s note. The name check catches Playwright's
    ``TimeoutError`` regardless of its (private) module path.
    """
    if isinstance(exc, asyncio.TimeoutError):
        return True
    return type(exc).__name__ == "TimeoutError"


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
    # Playwright stuffs a multi-line "Call log:" into the message; keep
    # the first line so the brain gets a clean one-liner.
    msg = msg.splitlines()[0].strip()
    return error_payload(f"{action} failed: {msg}")
