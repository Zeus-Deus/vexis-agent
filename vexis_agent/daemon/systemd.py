"""systemd user-unit lifecycle for vexis-agent.

Renders the unit at install time so the actual venv python (sys.executable
of the running ``vexis-agent`` process) and the resolved ``VEXIS_HOME`` get
baked into the unit. That mirrors hermes' ``gateway install`` and
openclaw's ``daemon install`` patterns: a static ``.service`` file
shipped in the repo would point at the wrong python on every machine
that doesn't match the dev's pipx layout.

Decision D6 in ``.plans/packaging-implementation-plan.md`` §2.

Public API:

  render_user_unit(...)   → str  — pure renderer, used by tests too
  install_user_unit(...)  → Path — writes + daemon-reloads
  uninstall_user_unit()   → bool — stops, disables, removes, reloads

Functions that shell out (install / uninstall / start / stop / status /
logs) live here too so the Typer subcommands in ``vexis_agent.cli``
stay a thin presentation layer.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

SERVICE_NAME = "vexis-agent"
UNIT_FILENAME = f"{SERVICE_NAME}.service"
DESCRIPTION = "vexis-agent — Telegram bot + agent CLI bridge"


def user_unit_dir() -> Path:
    """``~/.config/systemd/user`` (or ``$XDG_CONFIG_HOME/systemd/user``)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "systemd" / "user"


def user_unit_path() -> Path:
    """Full path to the installed user unit file."""
    return user_unit_dir() / UNIT_FILENAME


def render_user_unit(
    *,
    python_path: Path | str,
    vexis_home: Path | str,
    description: str = DESCRIPTION,
) -> str:
    """Render a systemd user unit body for ``vexis-agent run``.

    Pure function — no filesystem side-effects, no subprocess. The
    rendered string is what ``install_user_unit`` writes; tests can
    snapshot this directly.

    ``python_path`` should be the absolute path to the interpreter that
    has the ``vexis_agent`` package installed (usually the pipx venv
    python). ``vexis_home`` becomes both the WorkingDirectory and the
    ``VEXIS_HOME`` env var so the daemon resolves state under the same
    root the install knew about — even if the user later edits their
    shell to set a different value, the service stays pinned.
    """
    python = str(python_path)
    home = str(vexis_home)
    return (
        "[Unit]\n"
        f"Description={description}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={python} -m vexis_agent.cli run\n"
        f"WorkingDirectory={home}\n"
        f"Environment=VEXIS_HOME={home}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def install_user_unit(
    *,
    python_path: Path | str | None = None,
    vexis_home: Path | str | None = None,
) -> Path:
    """Render and install the user unit, then ``daemon-reload``.

    Defaults: ``python_path = sys.executable`` (the interpreter running
    this process — the pipx venv python when invoked through the
    console script), ``vexis_home = vexis_dir()`` (resolved with
    VEXIS_HOME applied).

    Returns the path the unit was written to.
    """
    from vexis_agent.core.paths import vexis_dir

    python = Path(python_path) if python_path is not None else Path(sys.executable)
    home = Path(vexis_home) if vexis_home is not None else vexis_dir()

    body = render_user_unit(python_path=python, vexis_home=home)

    unit_dir = user_unit_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    target = user_unit_path()
    target.write_text(body, encoding="utf-8")

    # daemon-reload tells systemd to re-scan unit files. Without it,
    # "systemctl --user start" can race against a stale view of the
    # filesystem on first install. Tolerate failure here — a missing
    # systemctl (containers, WSL without --user wired up) shouldn't
    # block the file write; ``vexis-agent doctor`` surfaces the issue.
    _systemctl(["daemon-reload"], check=False)

    return target


def uninstall_user_unit() -> bool:
    """Stop, disable, remove, and ``daemon-reload``.

    Returns ``True`` if the unit file was present (and was removed),
    ``False`` if it was already missing. Does NOT raise on a non-zero
    systemctl return — best-effort cleanup, the file removal is the
    authoritative bit.
    """
    target = user_unit_path()
    existed = target.exists()

    # Stop first so we don't leave a running daemon orphaned from its
    # unit file. Best-effort: if the unit isn't loaded, stop returns
    # non-zero and we move on.
    _systemctl(["stop", UNIT_FILENAME], check=False)
    _systemctl(["disable", UNIT_FILENAME], check=False)

    if existed:
        target.unlink()

    _systemctl(["daemon-reload"], check=False)
    return existed


def _systemctl(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``systemctl --user <args>`` with stdout/stderr captured.

    Raises ``FileNotFoundError`` (the natural one from subprocess) if
    systemctl isn't on PATH — caller can decide whether to swallow it.
    """
    cmd = ["systemctl", "--user", *args]
    log.debug("running %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
    )


def systemctl_available() -> bool:
    """Cheap probe used by ``vexis-agent doctor`` — is systemctl
    callable at all? Doesn't validate the user-bus is reachable; that
    only fires on the first real call."""
    return shutil.which("systemctl") is not None


def start() -> subprocess.CompletedProcess[str]:
    """Start the user service. Raises CalledProcessError on failure."""
    return _systemctl(["start", UNIT_FILENAME])


def stop() -> subprocess.CompletedProcess[str]:
    """Stop the user service. Best-effort — returns even on non-zero."""
    return _systemctl(["stop", UNIT_FILENAME], check=False)


def restart() -> subprocess.CompletedProcess[str]:
    return _systemctl(["restart", UNIT_FILENAME])


def status() -> subprocess.CompletedProcess[str]:
    """Status output. Uses ``--no-pager`` so we don't accidentally invoke
    a pager when called from a non-tty Typer context."""
    return _systemctl(["status", UNIT_FILENAME, "--no-pager"], check=False)


def logs(*, follow: bool = False, lines: int = 200) -> int:
    """journalctl --user-unit ... — exec'd directly so output streams
    to the user's terminal in real time (capture would defeat -f).
    Returns the exit code."""
    cmd = [
        "journalctl",
        "--user-unit",
        UNIT_FILENAME,
        "-n",
        str(lines),
    ]
    if follow:
        cmd.append("-f")
    return subprocess.call(cmd)
