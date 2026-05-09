"""Phase 4 — install.sh smoke tests.

Hard rule (from the user's Phase 4 directive): do NOT actually run
the installer over the dev machine. We exercise:

  * ``bash -n`` — pure syntax check.
  * ``--help`` — argument-parsing path.
  * ``--dry-run`` on a fake-sparse PATH where pipx is absent — verifies
    the missing-pipx branch logs sanely without invoking pipx.
  * ``VEXIS_CHANNEL`` validation — bad channel values exit non-zero.

The script's actual install logic (``pipx install``) is never reached.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


def test_install_sh_exists_and_is_executable() -> None:
    assert INSTALL_SH.is_file(), "install.sh missing from repo root"
    assert os.access(INSTALL_SH, os.X_OK), "install.sh is not executable"


def test_install_sh_passes_bash_n_syntax_check() -> None:
    """``bash -n`` parses but doesn't execute. Must succeed; otherwise
    the curl-bash one-liner blows up at the user's terminal."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_install_sh_help_lists_dry_run_and_env_vars() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--dry-run" in result.stdout
    assert "VEXIS_CHANNEL" in result.stdout
    assert "VEXIS_REPO" in result.stdout


def _empty_path_env(tmp_path: Path) -> dict[str, str]:
    """A subset of os.environ that drops pipx (and most else) from
    PATH but keeps the basic toolchain (bash, sudo if present, etc).
    The dry-run branch should not need anything beyond bash itself."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # We DO need bash and the basics in PATH for the script to run at
    # all — point at a curated subset of /usr/bin that excludes pipx.
    safe = []
    for tool in ("bash", "uname", "id"):
        src = shutil.which(tool)
        if src:
            link = fake_bin / tool
            if not link.exists():
                link.symlink_to(src)
            safe.append(tool)
    env = {
        "PATH": str(fake_bin),
        "HOME": str(tmp_path / "home"),
        "EUID": "1000",  # script reads $EUID via bash builtin; subprocess inherits
    }
    Path(env["HOME"]).mkdir()
    return env


def test_install_sh_dry_run_without_pipx(tmp_path) -> None:
    """With pipx not on PATH, --dry-run must hit the 'pipx not found'
    branch, log the missing-pipx warning, log the would-install
    placeholders, and exit zero — never trying to actually shell out."""
    env = _empty_path_env(tmp_path)
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
    )
    out = result.stdout + "\n" + result.stderr
    assert result.returncode == 0, (
        f"dry-run exited {result.returncode}\n--out--\n{out}"
    )
    assert "DRY-RUN" in out
    assert "pipx not found" in out or "would install pipx" in out
    assert "would run: pipx install --force" in out


def test_install_sh_rejects_unknown_channel(tmp_path) -> None:
    env = _empty_path_env(tmp_path)
    env["VEXIS_CHANNEL"] = "totally-bogus"
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "bad VEXIS_CHANNEL should exit non-zero"
    assert "VEXIS_CHANNEL" in result.stderr


def test_install_sh_dev_channel_uses_develop_branch(tmp_path) -> None:
    env = _empty_path_env(tmp_path)
    env["VEXIS_CHANNEL"] = "dev"
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    out = result.stdout + result.stderr
    assert "branch=develop" in out


def test_install_sh_rejects_unknown_arg(tmp_path) -> None:
    env = _empty_path_env(tmp_path)
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--no-such-flag"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
