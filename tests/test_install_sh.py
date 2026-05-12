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
    assert "VEXIS_VERSION" in result.stdout
    assert "VEXIS_REPO" in result.stdout


def _empty_path_env(tmp_path: Path) -> dict[str, str]:
    """A subset of os.environ that drops pipx + brain CLIs + Wayland
    tools from PATH but keeps the basic toolchain (bash, uname,
    whoami) so the installer's banner / platform / soft-dep
    sections can render. The --dry-run branch never tries to
    actually invoke pipx, sudo, or the Wayland tools — it only
    checks command -v for each."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # The installer prints the OS arch (uname -m), the current user
    # (whoami), and probes 'command -v' for many soft deps. command
    # is a bash builtin so it doesn't need a PATH entry; uname and
    # whoami do. Anything else should be absent so the soft-dep
    # branches all hit the missing-tool path.
    for tool in ("bash", "uname", "whoami", "id", "cat", "tr", "sed", "grep", "awk"):
        src = shutil.which(tool)
        if src:
            link = fake_bin / tool
            if not link.exists():
                link.symlink_to(src)
    env = {
        "PATH": str(fake_bin),
        "HOME": str(tmp_path / "home"),
        "EUID": "1000",
        "XDG_SESSION_TYPE": "wayland",  # avoid the X11 warning
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


def test_install_sh_default_falls_back_to_main_without_git(tmp_path) -> None:
    """When git isn't on PATH the resolver can't probe remote tags;
    fall back to main rather than refusing to install."""
    env = _empty_path_env(tmp_path)  # git intentionally absent
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    out = result.stdout + result.stderr
    assert "@main" in out
    assert "latest main" in out


def test_install_sh_version_pin_uses_tag(tmp_path) -> None:
    """VEXIS_VERSION=v1.2.3 → install from that git ref."""
    env = _empty_path_env(tmp_path)
    env["VEXIS_VERSION"] = "v1.2.3"
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    out = result.stdout + result.stderr
    assert "pinned to v1.2.3" in out
    assert "@v1.2.3" in out


def test_install_sh_dry_run_detects_existing_install(tmp_path) -> None:
    """When pipx is on PATH and reports vexis-agent already installed,
    --dry-run should describe the update path, not the fresh-install
    path. This is the curl-bash re-run UX."""
    env = _empty_path_env(tmp_path)
    fake_bin = Path(env["PATH"])

    # Fake pipx that returns 'vexis-agent ...' from `pipx list --short`.
    pipx_stub = fake_bin / "pipx"
    pipx_stub.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = list ] && [ \"$2\" = --short ]; then\n"
        "    echo 'vexis-agent 0.0.1'\n"
        "    exit 0\n"
        "fi\n"
        "echo 'fake pipx: unhandled $@' >&2\n"
        "exit 0\n"
    )
    pipx_stub.chmod(0o755)

    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    out = result.stdout + result.stderr
    # Anchor against the specific update-path messages (not the
    # generic "pipx already installed" line which also matches
    # 'already installed').
    assert "vexis-agent is already installed" in out
    assert "would update" in out
    assert "skip the setup wizard" in out


def test_install_sh_rejects_unknown_arg(tmp_path) -> None:
    env = _empty_path_env(tmp_path)
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--no-such-flag"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_install_sh_help_documents_skip_setup() -> None:
    """Phase 5e added --skip-setup; the help text must surface it."""
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--skip-setup" in result.stdout


def test_install_sh_dry_run_includes_banner_and_section_headers(tmp_path) -> None:
    """Banner + section markers should render in --dry-run mode so
    the user sees what agent-platform-style installers do."""
    env = _empty_path_env(tmp_path)
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    out = result.stdout + result.stderr
    # Box-drawing banner
    assert "vexis-agent installer" in out
    # Section markers
    assert "◆ Platform" in out
    assert "◆ Source" in out
    assert "◆ pipx" in out


def test_install_sh_no_color_strips_ansi(tmp_path) -> None:
    """NO_COLOR=1 should suppress every ANSI escape sequence."""
    env = _empty_path_env(tmp_path)
    env["NO_COLOR"] = "1"
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "\033[" not in result.stdout, "NO_COLOR did not strip ANSI escapes"
