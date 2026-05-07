"""Per-brain model discovery — Day 4 of model UX.

Two surfaces:

  - ``discover_claude_code_models()`` — hardcoded curated list of
    aliases + full names. claude-code has no model-list CLI
    command, so vexis ships the canonical set and updates it via
    PR when Anthropic releases new models. The validator's rule
    6 (available-models membership) treats this list as advisory
    on claude-code precisely because it goes stale.

  - ``discover_opencode_models()`` — runs ``opencode models``
    subprocess (timeout 10s), parses stdout, returns the set.
    Returns empty set on missing binary (claude-code-only users)
    or persistent timeout — gracefully degrades the validator's
    rule 6 to silent-skip rather than blocking.

Both are cached in-process for 5 minutes. The cache is shared
across the slash command's ``/model list <brain>`` and the
dashboard's available-models dropdown so the two surfaces stay
in sync. Cache invalidation via :func:`invalidate_discovery_cache`
(called by the dashboard's refresh button + by tests).

Design citation: ``.plans/model-management-ux-research.md`` §4
"Model discovery" + §6 Day 4.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Iterable

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# claude-code: hardcoded curated list
# ──────────────────────────────────────────────────────────────────


# Aliases the claude CLI accepts (``--model haiku`` etc.) plus the
# current full-name set. Update via PR when Anthropic ships new
# models. The validator's rule 6 is *advisory* on claude-code —
# a model not in this list is flagged as a warning ("vexis hasn't
# seen this name; trying anyway") rather than refused, so PR-lag
# isn't a release blocker. The smoke probe in
# ``tests/test_brain_claude_code_smoke.py`` against the real
# binary catches drift if an alias gets retired.
MODEL_DISCOVERY_CLAUDE_CODE: list[str] = [
    # Aliases — recommended for end users, claude-code resolves
    # them to whatever the latest model in that family is.
    "haiku",
    "sonnet",
    "opus",
    # Full names — pin to current generation. Vexis updates these
    # when Anthropic releases. Order: cheapest → most capable
    # within each generation, generations descending.
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-1",
    # Earlier generation kept for users on subscription plans
    # that haven't yet rolled the latest. Drop when usage drops.
    "claude-haiku-3-5",
    "claude-sonnet-3-7",
    "claude-opus-4",
]


def discover_claude_code_models() -> set[str]:
    """Return the curated set of claude-code model identifiers."""
    return set(MODEL_DISCOVERY_CLAUDE_CODE)


# ──────────────────────────────────────────────────────────────────
# opencode: live `opencode models` subprocess
# ──────────────────────────────────────────────────────────────────


_OPENCODE_DISCOVERY_TIMEOUT_SECONDS = 10.0


def _discover_opencode_models_uncached() -> set[str]:
    """One-shot subprocess call. Caller handles caching.

    Failure modes (each → empty set, advisory-only impact on
    rule 6 in the validator):

      - ``FileNotFoundError`` (``opencode`` binary missing — the
        common case for claude-code-only users).
      - ``subprocess.TimeoutExpired`` (binary present but slow;
        models.dev cache fetch under contention).
      - Non-zero exit (auth issue or models.dev unreachable).
      - Empty stdout (no providers configured).
    """
    try:
        proc = subprocess.run(
            ["opencode", "models"],
            capture_output=True,
            text=True,
            timeout=_OPENCODE_DISCOVERY_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return set()
    except subprocess.TimeoutExpired:
        log.warning(
            "opencode models discovery timed out after %.1fs",
            _OPENCODE_DISCOVERY_TIMEOUT_SECONDS,
        )
        return set()
    except OSError as exc:
        log.warning("opencode models discovery OS error: %s", exc)
        return set()

    if proc.returncode != 0:
        log.warning(
            "opencode models discovery exited %d: %s",
            proc.returncode, (proc.stderr or "").strip(),
        )
        return set()

    return {
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip()
    }


# ──────────────────────────────────────────────────────────────────
# 5-minute per-brain cache
# ──────────────────────────────────────────────────────────────────


_CACHE_TTL_SECONDS = 5 * 60.0
_DISCOVERY_CACHE: dict[str, tuple[float, set[str]]] = {}


def discover_opencode_models() -> set[str]:
    """Cached opencode model list. Refreshes every 5 minutes per
    process; clearable via :func:`invalidate_discovery_cache`."""
    return _cached("opencode", _discover_opencode_models_uncached)


def discover_models(brain_kind: str) -> set[str]:
    """Brain-agnostic dispatch. Returns the empty set for unknown
    brain kinds (e.g. ``null``) rather than raising — the
    validator's rule 6 silently skips when the discovered set is
    empty."""
    if brain_kind == "claude-code":
        return discover_claude_code_models()
    if brain_kind == "opencode":
        return discover_opencode_models()
    return set()


def discovery_for_validator(brain_kinds: Iterable[str]) -> dict[str, set[str]]:
    """Build the ``available_models_per_brain`` dict the validator
    expects. Helper so callers don't have to remember the shape."""
    return {b: discover_models(b) for b in brain_kinds}


def invalidate_discovery_cache(brain_kind: str | None = None) -> None:
    """Clear the cache. ``brain_kind=None`` clears all entries; a
    string clears just that brain's. Called by the dashboard's
    POST /api/v1/models/discovery/refresh + by tests."""
    if brain_kind is None:
        _DISCOVERY_CACHE.clear()
    else:
        _DISCOVERY_CACHE.pop(brain_kind, None)


def refresh_opencode_models() -> set[str]:
    """Force-refresh opencode models AND run
    ``opencode models --refresh`` to refresh opencode's own
    models.dev cache. Returns the fresh list. Used by the
    dashboard's refresh button so a user adding a provider
    sees the new models without restarting vexis."""
    invalidate_discovery_cache("opencode")
    try:
        subprocess.run(
            ["opencode", "models", "--refresh"],
            capture_output=True,
            timeout=_OPENCODE_DISCOVERY_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        # Non-fatal — the next discovery call still tries the
        # cached models.dev list (or whatever's reachable).
        log.warning(
            "opencode models --refresh failed (%s); proceeding with "
            "cached discovery", exc,
        )
    return discover_opencode_models()


# ──────────────────────────────────────────────────────────────────
# Internal cache machinery
# ──────────────────────────────────────────────────────────────────


def _cached(key: str, fetcher) -> set[str]:
    now = time.monotonic()
    entry = _DISCOVERY_CACHE.get(key)
    if entry is not None:
        cached_at, value = entry
        if now - cached_at < _CACHE_TTL_SECONDS:
            return value
    value = fetcher()
    _DISCOVERY_CACHE[key] = (now, value)
    return value


__all__ = [
    "MODEL_DISCOVERY_CLAUDE_CODE",
    "discover_claude_code_models",
    "discover_models",
    "discover_opencode_models",
    "discovery_for_validator",
    "invalidate_discovery_cache",
    "refresh_opencode_models",
]
