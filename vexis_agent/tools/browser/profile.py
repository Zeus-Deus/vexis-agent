"""Browser profile + ``BrowserProfile`` factory.

Defaults follow §10 of the browser-research doc: vanilla Chromium, the
Vexis-owned profile dir at ``~/.vexis/browser-profiles/<name>/``, and
the two Wayland flags that Phase 1 confirmed are needed under Hyprland
(``--ozone-platform=wayland --ozone-platform-hint=auto``).

All knobs are read from ``~/.vexis/config.yaml`` ``[browser]`` section
via ``core.yaml_config``. Missing config falls through to the defaults
below — the daemon must work out of the box without an extra config
file. The ``browser-use`` library creates the ``user_data_dir`` lazily
on first launch; we don't pre-create it.
"""

from __future__ import annotations

from pathlib import Path

from browser_use import BrowserProfile

from vexis_agent.core import yaml_config

DEFAULT_PROFILES_DIR = Path.home() / ".vexis" / "browser-profiles"
DEFAULT_PROFILE_NAME = "default"
DEFAULT_HEADLESS = False
DEFAULT_INACTIVITY_TIMEOUT_S = 120
DEFAULT_ACTION_TIMEOUT_S = 120
DEFAULT_CHROMIUM_PATH = "/usr/bin/chromium"
WAYLAND_ARGS: tuple[str, ...] = (
    "--ozone-platform=wayland",
    "--ozone-platform-hint=auto",
)


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


def chromium_path() -> str:
    return yaml_config.browser_chromium_path() or DEFAULT_CHROMIUM_PATH


def cdp_url() -> str | None:
    """Externally-launched-Chrome URL, if configured."""
    return yaml_config.browser_cdp_url()


def screenshots_dir(workspace: Path) -> Path:
    """``<workspace>/browser/screenshots/`` — created lazily."""
    path = workspace / "browser" / "screenshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_profile() -> BrowserProfile:
    """Build a BrowserProfile honoring ``[browser].cdp_url`` if set.

    When ``cdp_url`` is configured, browser-use connects to the
    externally-launched Chrome over CDP and ignores ``user_data_dir``
    / ``executable_path`` / ``args`` (the user owns the process). We
    leave those fields unset in that mode — passing them just
    pollutes the BrowserSession repr with values it won't use.
    """
    url = cdp_url()
    if url:
        return BrowserProfile(cdp_url=url, headless=headless(), keep_alive=True)
    return BrowserProfile(
        user_data_dir=str(profile_dir()),
        executable_path=chromium_path(),
        headless=headless(),
        keep_alive=False,
        args=list(WAYLAND_ARGS),
    )
