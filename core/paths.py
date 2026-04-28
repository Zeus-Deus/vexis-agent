"""Single source of truth for all daemon directory paths."""

from __future__ import annotations

import os
from pathlib import Path

_APP = "vexis-agent"


def _xdg(env_var: str, fallback: Path) -> Path:
    raw = os.environ.get(env_var)
    base = Path(raw).expanduser() if raw else fallback
    path = base / _APP
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_dir(configured: Path | str) -> Path:
    """Claude Code's cwd. User-facing; default `~/vexis-workspace`."""
    path = Path(configured).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_dir() -> Path:
    """Logs and runtime state. `$XDG_STATE_HOME/vexis-agent`."""
    return _xdg("XDG_STATE_HOME", Path.home() / ".local" / "state")


def data_dir() -> Path:
    """Reserved for Step 3+. `$XDG_DATA_HOME/vexis-agent`."""
    return _xdg("XDG_DATA_HOME", Path.home() / ".local" / "share")


def config_dir() -> Path:
    """Reserved for Step 4+. `$XDG_CONFIG_HOME/vexis-agent`."""
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config")
