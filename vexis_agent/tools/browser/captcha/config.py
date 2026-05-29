"""Config-driven solver selection + the navigate-time captcha hook.

``get_solver`` reads ``[browser].captcha_solver`` / ``captcha_solver_api_key``
from ``~/.vexis/config.yaml`` and returns the configured provider instance, or
``None`` when disabled / unkeyed. ``apply_captcha`` is the single integration
point ``BrowserTools.navigate`` calls: detect -> (no solver) hint / (solver)
solve+inject. It's kept here, separate from ``tools.py``, and parameterized by
a ``solver_factory`` so it's unit-testable with a fake page and fake solver.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from vexis_agent.core import yaml_config
from vexis_agent.tools.browser import snapshot as snapshot_mod
from vexis_agent.tools.browser.captcha.base import (
    CaptchaSolver,
    CaptchaSolverError,
    Transport,
    default_transport,
)
from vexis_agent.tools.browser.captcha.capsolver import CapSolver
from vexis_agent.tools.browser.captcha.detect import detect_captcha_on_page
from vexis_agent.tools.browser.captcha.inject import inject_token
from vexis_agent.tools.browser.captcha.twocaptcha import TwoCaptcha

log = logging.getLogger(__name__)

_PROVIDERS: dict[str, type[CaptchaSolver]] = {
    "capsolver": CapSolver,
    "twocaptcha": TwoCaptcha,
}

# Providers the dashboard offers; "none" disables solving.
VALID_PROVIDERS = ("none", "capsolver", "twocaptcha")

_CONFIGURE_HINT = (
    "A captcha was detected but no solver is configured. Configure CapSolver "
    "or 2Captcha in the dashboard Browser tab → Captcha solver panel to solve "
    "it automatically."
)


def mask_key(key: str | None) -> str:
    """Render a key for display: ``"•••• 1234"`` for the last 4, else
    ``"not set"``. Never returns the raw key."""
    if not key:
        return "not set"
    key = key.strip()
    if len(key) <= 4:
        return "••••"
    return f"•••• {key[-4:]}"


def get_solver(transport: Transport = default_transport) -> CaptchaSolver | None:
    """Return the configured solver, or ``None`` when disabled / unkeyed."""
    provider = (yaml_config.browser_captcha_solver() or "none").lower()
    cls = _PROVIDERS.get(provider)
    if cls is None:
        return None
    api_key = yaml_config.browser_captcha_solver_api_key()
    if not api_key:
        return None
    return cls(api_key, transport=transport)


async def apply_captcha(
    page: Any,
    page_url: str,
    result: dict[str, Any],
    *,
    solver_factory: Callable[[], CaptchaSolver | None] = get_solver,
) -> dict[str, Any]:
    """Detect a captcha on ``page`` and, if a solver is configured, solve it.

    Mutates and returns ``result`` (the navigate snapshot dict):

    - no captcha            -> unchanged
    - captcha, no solver    -> ``captcha={kind, configured: False, solved: False}``
                               + top-level ``hint`` pointing to the dashboard
    - captcha, solver ok    -> solve + inject token, re-render snapshot, set
                               ``captcha={kind, provider, solved: True}``
    - captcha, solver fails -> ``captcha={..., solved: False, error}`` + ``hint``
                               carrying the provider's verbatim response

    Navigation that loaded a page stays ``ok: True`` throughout — captcha state
    rides in the structured ``captcha`` field, mirroring the ``snapshot_stale``
    soft-hint pattern.
    """
    challenge = await detect_captcha_on_page(page)
    if challenge is None:
        return result

    solver = solver_factory()
    if solver is None:
        result["captcha"] = {
            "kind": challenge.kind,
            "configured": False,
            "solved": False,
        }
        result["hint"] = _CONFIGURE_HINT
        return result

    try:
        token = await solver.solve(challenge, page_url)
        await inject_token(page, challenge, token)
        # The page DOM changed (token written, callback maybe fired); hand the
        # brain a fresh snapshot rather than the pre-solve one.
        try:
            refreshed = await snapshot_mod.render(page)
            if isinstance(refreshed, dict):
                result.update(refreshed)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("[browser] post-captcha snapshot failed: %s", exc)
        result["captcha"] = {
            "kind": challenge.kind,
            "configured": True,
            "provider": solver.name,
            "solved": True,
        }
    except CaptchaSolverError as exc:
        result["captcha"] = {
            "kind": challenge.kind,
            "configured": True,
            "provider": solver.name,
            "solved": False,
            "error": exc.detail,
        }
        result["hint"] = (
            f"{solver.name} could not solve the {challenge.kind} captcha: "
            f"{exc.detail}"
        )
    return result
