"""``vexis-agent update`` — pipx-aware self-upgrade.

Detects how vexis-agent is installed (pipx venv, editable git checkout,
or system pip) and dispatches the right reinstall recipe. Refuses to
touch user state (``~/.vexis/``, ``~/vexis-workspace/``) — decision D7
in the packaging plan: code dir ≠ data dir.

Detection heuristic:

  pipx     : ``sys.executable`` lives under
             ``~/.local/share/pipx/venvs/vexis-agent/`` (or the platform
             equivalent for ``$PIPX_HOME``).
  editable : the package's source root contains a ``.git`` directory —
             a developer running ``pip install -e .``.
  unknown  : neither — likely system pip or a frozen wheel install.
             ``update`` refuses to do anything destructive in this
             mode and prints manual instructions instead.

Public API:

  detect_install_type() → InstallType
  source_root()         → Path | None  — the dev checkout for editable installs
  pipx_venv_root()      → Path | None  — for pipx installs
  run_update(channel)   → int          — exit code for the CLI
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

import vexis_agent

log = logging.getLogger(__name__)


class InstallType(Enum):
    PIPX = "pipx"
    EDITABLE = "editable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InstallInfo:
    """Snapshot of how this process was installed.

    Tests construct one of these directly to exercise the dispatch
    logic without depending on the live filesystem.
    """

    kind: InstallType
    python_path: Path
    source_root: Optional[Path] = None  # editable mode only
    pipx_venv: Optional[Path] = None  # pipx mode only


def _pipx_home() -> Path:
    """Where pipx keeps its venvs.

    Honors ``$PIPX_HOME`` (the override pipx itself reads). Default is
    ``~/.local/share/pipx`` on Linux/macOS, which is where pipx 1.x+
    installs by default. Older pipx used ``~/.local/pipx``; we don't
    try to detect that — it'll just fall through to InstallType.UNKNOWN.
    """
    raw = os.environ.get("PIPX_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "share" / "pipx"


def detect_install_type(
    *,
    python_path: Path | None = None,
    package_file: Path | None = None,
) -> InstallInfo:
    """Decide whether the current process is pipx-installed, editable,
    or unknown. Both args are dependency-injection seams for tests.

    The editable check looks for a ``.git`` dir in the package's source
    parent (the repo root) — ``pip install -e .`` exposes the source
    location through ``__file__``, so following back from
    ``vexis_agent/__init__.py`` lands in the working checkout. A
    pipx-installed wheel lives inside a venv prefix and won't have a
    sibling ``.git``.
    """
    py = Path(python_path) if python_path is not None else Path(sys.executable)
    pkg_file = (
        Path(package_file)
        if package_file is not None
        else Path(vexis_agent.__file__).resolve()
    )

    # pipx detection: the python interpreter is inside a pipx venv. We
    # check by prefix-match against the resolved pipx home so symlinks
    # and the differing ``venvs/<name>/bin/python`` shape don't trip us
    # up. The venv root is the parent of the bin/ that holds python.
    pipx_root = _pipx_home().resolve(strict=False)
    py_resolved = py.resolve(strict=False)
    if str(py_resolved).startswith(str(pipx_root) + os.sep):
        # Walk up from python until we hit a directory whose parent is
        # the pipx venvs/ directory; that's our venv.
        venv = py_resolved
        for _ in range(6):  # safety bound; venvs are 2-3 levels deep
            if venv.parent.name == "venvs":
                return InstallInfo(
                    kind=InstallType.PIPX,
                    python_path=py,
                    pipx_venv=venv,
                )
            if venv.parent == venv:  # reached filesystem root
                break
            venv = venv.parent
        # Found pipx prefix but couldn't isolate the venv — still pipx,
        # just less precise. The CLI dispatcher only needs the kind.
        return InstallInfo(kind=InstallType.PIPX, python_path=py)

    # Editable detection: __file__ for ``vexis_agent.__init__`` lives
    # in the source tree when the package was installed with -e .
    # Walk up looking for a sibling ``.git`` directory.
    candidate = pkg_file.parent  # vexis_agent/
    for _ in range(4):
        candidate = candidate.parent
        if candidate == candidate.parent:
            break
        if (candidate / ".git").exists():
            return InstallInfo(
                kind=InstallType.EDITABLE,
                python_path=py,
                source_root=candidate,
            )

    return InstallInfo(kind=InstallType.UNKNOWN, python_path=py)


def source_root() -> Path | None:
    """Convenience: returns the editable source root if detectable."""
    info = detect_install_type()
    return info.source_root


def pipx_venv_root() -> Path | None:
    """Convenience: returns the pipx venv root if detectable."""
    info = detect_install_type()
    return info.pipx_venv


def run_update(channel: str = "stable", *, info: InstallInfo | None = None) -> int:
    """Run the update appropriate for this install. Returns an exit
    code (0 = success, 1 = failure / unsupported install).

    Channel ``"stable"`` is the main branch; ``"dev"`` is the develop
    branch (only meaningful for pipx installs that read from git).
    Editable installs already have a working tree — channel is a no-op.

    Never restarts the service: the caller (or the user) decides when
    that's safe. Plan §6.4 invariant.
    """
    if info is None:
        info = detect_install_type()

    if info.kind is InstallType.PIPX:
        return _update_pipx(channel)
    if info.kind is InstallType.EDITABLE:
        return _update_editable(info.source_root)  # type: ignore[arg-type]
    return _update_unknown()


def _update_pipx(channel: str) -> int:
    """Re-run ``pipx install --force git+<repo>`` so the venv code
    points at the latest ``main`` (or ``develop``) branch tip.

    pipx upgrade is the simpler verb but only works when the original
    install was from PyPI; for git-source installs, ``--force`` reinstall
    is the documented recipe. We choose the verb based on which mode the
    user is in by trying upgrade first and falling back to the reinstall
    on failure.
    """
    if shutil.which("pipx") is None:
        print(
            "pipx not found on PATH. Install pipx and re-run "
            "'vexis-agent update', or reinstall manually:\n"
            "  pipx install git+https://github.com/Zeus-Deus/vexis-agent.git",
            flush=True,
        )
        return 1

    # First try: simple upgrade (works for any install that pipx
    # already knows about).
    upgrade = subprocess.run(
        ["pipx", "upgrade", "vexis-agent"],
        capture_output=True,
        text=True,
    )
    if upgrade.returncode == 0:
        _print_post_update_hint()
        return 0

    # Fallback: force-reinstall from the configured channel.
    branch = "main" if channel == "stable" else channel
    repo_url = (
        f"git+https://github.com/Zeus-Deus/vexis-agent.git@{branch}"
    )
    reinstall = subprocess.run(
        ["pipx", "install", "--force", repo_url],
        capture_output=False,
        text=True,
    )
    if reinstall.returncode != 0:
        print(
            f"pipx reinstall failed (exit {reinstall.returncode}). "
            "Inspect the output above and try again, or reinstall manually.",
            flush=True,
        )
        return 1
    _print_post_update_hint()
    return 0


def _update_editable(repo: Path) -> int:
    """git pull + pip install -e . in the existing checkout."""
    if not (repo / ".git").exists():
        print(
            f"Editable source root {repo} no longer has a .git directory — "
            "refusing to update.",
            flush=True,
        )
        return 1

    pull = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=str(repo),
        capture_output=False,
        text=True,
    )
    if pull.returncode != 0:
        print(
            "git pull failed (likely a dirty tree or non-fast-forward). "
            "Resolve manually and re-run.",
            flush=True,
        )
        return 1

    reinstall = subprocess.run(
        [str(Path(sys.executable)), "-m", "pip", "install", "-e", "."],
        cwd=str(repo),
        capture_output=False,
        text=True,
    )
    if reinstall.returncode != 0:
        print(
            "pip install -e . failed. Inspect the output above and re-run.",
            flush=True,
        )
        return 1

    _print_post_update_hint()
    return 0


def _update_unknown() -> int:
    print(
        "Couldn't detect how vexis-agent was installed. To update manually, "
        "run one of:\n"
        "  pipx install --force git+https://github.com/Zeus-Deus/vexis-agent.git\n"
        "  git -C <repo> pull && pip install -e <repo>",
        flush=True,
    )
    return 1


def _print_post_update_hint() -> None:
    print(
        "Updated vexis-agent. Restart the service to pick up the new code:\n"
        "  vexis-agent service restart",
        flush=True,
    )
