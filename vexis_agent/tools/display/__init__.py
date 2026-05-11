"""Headless display backends for the build-and-test loop.

A :class:`HeadlessDisplay` provisions a virtual screen *inside* a
:class:`~vexis_agent.tools.sandbox.Sandbox` so GUI apps under test
never touch the host's live Wayland/X session. The plan calls for two
backend modes:

* ``xvfb`` — pure X11. Universal, lightweight, software-rendered.
* ``wayland-headless`` — Cage or ``Hyprland --headless`` for Wayland-
  native apps. Requires the image to ship the compositor binary.

``auto`` (the default) picks Xvfb unless ``wayland`` is explicitly
requested, because Xvfb has the broadest image-package coverage.

The public surface is small: :meth:`HeadlessDisplay.start`,
:meth:`HeadlessDisplay.stop`, :meth:`HeadlessDisplay.env`, and a
:meth:`HeadlessDisplay.list_all` classmethod for the CLI's ``list``
subcommand. Everything else (PID tracking, log capture) is private.
"""

from .display import (
    DEFAULT_DISPLAY_NUMBER,
    DEFAULT_RESOLUTION,
    SUPPORTED_BACKENDS,
    DisplayError,
    DisplayMetadata,
    DisplayNotFound,
    DisplayStartFailed,
    HeadlessDisplay,
    UnsupportedBackend,
    resolve_backend,
)

__all__ = [
    "DEFAULT_DISPLAY_NUMBER",
    "DEFAULT_RESOLUTION",
    "SUPPORTED_BACKENDS",
    "DisplayError",
    "DisplayMetadata",
    "DisplayNotFound",
    "DisplayStartFailed",
    "HeadlessDisplay",
    "UnsupportedBackend",
    "resolve_backend",
]
