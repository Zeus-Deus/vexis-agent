"""Browser profile + ``StealthySession`` keyword factory.

The browser is `scrapling`'s Camoufox-backed ``StealthySession`` — a
stealth Firefox that beats the bot-detection / fingerprinting walls a
vanilla Chromium trips on. There is no fallback engine and no CDP
attach mode: this *is* the browser.

**Headless is the default.** A laptop-as-home-server runs with the lid
closed and the screen locked — there is no usable host display. Camoufox
renders to an off-screen surface in headless mode, so navigate / snapshot
/ click / screenshot all work identically whether the host is unlocked,
locked, or has no display at all. Set ``[browser].headless: false`` to opt
into a visible window when physically at the machine (e.g. to watch a
manual login).

Login state lives in the Camoufox ``user_data_dir`` — a real Firefox
profile at ``~/.vexis/browser-profiles/<name>/``. Cookies and storage
survive process restart and idle-recycle, so recycling the live session
is cheap.

All knobs are read from ``~/.vexis/config.yaml`` ``[browser]`` via
``core.yaml_config``. Missing config falls through to the defaults below —
the daemon must work out of the box. ``StealthySession`` creates the
``user_data_dir`` lazily on first launch; we don't pre-create it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vexis_agent.core import yaml_config

DEFAULT_PROFILES_DIR = Path.home() / ".vexis" / "browser-profiles"
DEFAULT_PROFILE_NAME = "default"
DEFAULT_HEADLESS = True
DEFAULT_INACTIVITY_TIMEOUT_S = 120
DEFAULT_ACTION_TIMEOUT_S = 120


def profiles_dir() -> Path:
    raw = yaml_config.browser_profiles_dir() or str(DEFAULT_PROFILES_DIR)
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_profile_name() -> str:
    return yaml_config.browser_default_profile() or DEFAULT_PROFILE_NAME


def profile_dir() -> Path:
    return profiles_dir() / default_profile_name()


def headless() -> bool:
    return yaml_config.browser_headless()


def inactivity_timeout_seconds() -> int:
    return yaml_config.browser_inactivity_timeout_seconds()


def action_timeout_seconds() -> int:
    return yaml_config.browser_action_timeout_seconds()


def solve_cloudflare() -> bool:
    """Whether navigation auto-solves Cloudflare Turnstile/Interstitial.

    On by default — the whole point of the Camoufox engine is to walk
    through the bot walls a plain browser bounces off. Costs 5–15s only
    when a challenge is actually present; pages without one are
    unaffected. Disable via ``[browser].solve_cloudflare: false``.
    """
    return yaml_config.browser_solve_cloudflare()


def captcha_solver() -> str:
    """Selected captcha solver provider (``none`` | ``capsolver`` |
    ``twocaptcha``). Default ``none``. See
    ``vexis_agent.tools.browser.captcha`` for the solver layer."""
    return yaml_config.browser_captcha_solver()


def captcha_solver_api_key() -> str | None:
    """API key for the selected captcha solver, or ``None`` when unset."""
    return yaml_config.browser_captcha_solver_api_key()


def screenshots_dir(workspace: Path) -> Path:
    """``<workspace>/browser/screenshots/`` — created lazily."""
    path = workspace / "browser" / "screenshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_kwargs() -> dict[str, Any]:
    """Build the ``AsyncStealthySession(**kwargs)`` for the live session.

    ``solve_cloudflare`` is passed as the session default so navigations
    inherit it; ``timeout`` is left at scrapling's default (it bumps
    itself to 60s when ``solve_cloudflare`` is on). ``geoip`` stays off —
    it's a proxy-affinity feature we don't use and it adds a startup
    lookup. ``block_webrtc`` is on to close the classic WebRTC IP leak.
    """
    return {
        "headless": headless(),
        "user_data_dir": str(profile_dir()),
        "solve_cloudflare": solve_cloudflare(),
        "block_webrtc": True,
        "geoip": False,
    }
