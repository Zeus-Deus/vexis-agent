"""Browser profile + ``BrowserProfile`` factory.

Defaults follow §10 of the browser-research doc: vanilla Chromium and
the Vexis-owned profile dir at ``~/.vexis/browser-profiles/<name>/``.

**Headless is the default.** A laptop-as-home-server runs with the lid
closed and the screen locked — there is no usable host display, and a
headed Chromium either fails to launch or renders into a blanked
Wayland session. Headless Chromium renders to an off-screen
framebuffer, so navigate / snapshot / click / screenshot all work
identically whether the host is unlocked, locked, or has no display at
all. Set ``[browser].headless: false`` to opt back into a visible
window when physically at the machine (e.g. to watch a manual login).

The two Wayland flags Phase 1 confirmed are needed under Hyprland
(``--ozone-platform=wayland --ozone-platform-hint=auto``) are applied
**only in headed mode** — they position a real window on the host
compositor. A headless launch must not carry them: there is no
compositor to reach (and on a locked session, attempting one is
exactly the failure we're removing).

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
DEFAULT_HEADLESS = True
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

    ``WAYLAND_ARGS`` are appended only in headed mode: they place a
    real window on the Hyprland compositor. Headless Chromium has no
    window and no compositor to reach, so carrying those flags would
    only invite a connection attempt against a possibly-locked session.
    """
    url = cdp_url()
    if url:
        return BrowserProfile(cdp_url=url, headless=headless(), keep_alive=True)
    is_headless = headless()
    return BrowserProfile(
        user_data_dir=str(profile_dir()),
        executable_path=chromium_path(),
        headless=is_headless,
        keep_alive=False,
        args=[] if is_headless else list(WAYLAND_ARGS),
    )
