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


def runtime_dir() -> Path:
    """Ephemeral runtime files (control socket, status files, locks).

    Defaults to `$XDG_RUNTIME_DIR/vexis-agent` falling back to
    `/run/user/<uid>/vexis-agent`. Tmpfs on most distros, so suitable
    for high-frequency small writes (e.g. brain status updates).
    """
    raw = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(raw).expanduser() if raw else Path(f"/run/user/{os.getuid()}")
    path = base / _APP
    path.mkdir(parents=True, exist_ok=True)
    return path


def vexis_dir() -> Path:
    """Operational state (config, curator state, logs). Literal `~/.vexis/`.

    Intentionally NOT XDG-based — by design this is a single-user
    private directory that's never gitted, sitting alongside the
    workspace at `~/vexis-workspace/` (which IS gittable). The split
    lets the user version-control their agent's brain (memories,
    skills, SOUL) without leaking secrets or per-machine state.
    """
    path = Path.home() / ".vexis"
    path.mkdir(parents=True, exist_ok=True)
    return path


def memories_dir(workspace: Path) -> Path:
    """`<workspace>/memories/` — gittable. Holds MEMORY.md and USER.md."""
    path = workspace / "memories"
    path.mkdir(parents=True, exist_ok=True)
    return path


def skills_dir(workspace: Path) -> Path:
    """`<workspace>/skills/` — gittable. Holds SKILL.md trees + telemetry."""
    path = workspace / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def curator_state_path() -> Path:
    """`~/.vexis/curator/state.json`. Holds last_run_at + paused flag.

    Parent dir is created lazily; the state file itself is created on
    first save. Returning a Path even when missing lets the curator
    check existence without an extra ``exists()`` round-trip.
    """
    parent = vexis_dir() / "curator"
    parent.mkdir(parents=True, exist_ok=True)
    return parent / "state.json"


def curator_logs_dir() -> Path:
    """`~/.vexis/logs/curator/`. Holds per-run REPORT.md / run.json subdirs."""
    path = vexis_dir() / "logs" / "curator"
    path.mkdir(parents=True, exist_ok=True)
    return path


def learning_state_path() -> Path:
    """`~/.vexis/learning/reviewed.json`. Per-session reviewed records.

    Sidecar `state.json` next to it (in the same directory) holds
    daemon-level state (paused, last_tick_at). The split keeps the
    high-write per-session file separate from the rarely-mutated
    daemon flags.
    """
    parent = vexis_dir() / "learning"
    parent.mkdir(parents=True, exist_ok=True)
    return parent / "reviewed.json"


def learning_logs_dir() -> Path:
    """`~/.vexis/logs/learning/`. Per-tick run reports."""
    path = vexis_dir() / "logs" / "learning"
    path.mkdir(parents=True, exist_ok=True)
    return path
