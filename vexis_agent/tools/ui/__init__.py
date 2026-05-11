"""Native-app driver for the build-and-test loop.

The desktop analogue of ``vexis-browse``: snapshot the focused window's
AT-SPI tree, return indexed widgets, click/type/press by index. Runs
inside the per-task sandbox container so the host's live Wayland/X
session is never touched.

When AT-SPI returns a sparse tree (some Electron apps, games, custom
renderers), the CLI hints the caller to fall back to ``vision-snapshot``
which captures a screenshot via ``import``/``grim`` and to coordinate
clicks via ``xdotool``/``ydotool``.
"""

from .ui import (
    ATSPIError,
    SnapshotResult,
    UIAction,
    UIDriver,
    build_action_argv,
)

__all__ = [
    "ATSPIError",
    "SnapshotResult",
    "UIAction",
    "UIDriver",
    "build_action_argv",
]
