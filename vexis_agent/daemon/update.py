"""``vexis-agent update`` — pipx-aware self-upgrade.

Detects how vexis-agent is installed (pipx venv, editable git checkout,
or system pip) and dispatches the right reinstall recipe. Refuses to
touch user state (``~/.vexis/``, ``~/vexis-workspace/``) — decision D7
in the packaging plan: code dir ≠ data dir.

Channel semantics for pipx installs (see ``_update_pipx``):
  * ``stable`` (default) — resolve the newest semver tag from the
    upstream remote and reinstall from there. Mirrors what
    install.sh does on a fresh curl-bash, so install + update
    converge on the same ref.
  * ``dev`` — main branch tip; tracks the maintainer's last push.
  * any other value — literal git ref, lets users pin to v0.3.0 etc.

Phase 5f hardens the path against bad-luck disconnects:
  * Pre-update snapshot — a quick backup of ``~/.vexis/`` lands at
    ``~/.vexis/backups/pre-update-<utc>.zip`` before any install
    work runs. Failed updates rollback via ``vexis-agent backup-restore``.
  * Output mirrored to ``~/.vexis/logs/update.log`` so a dropped
    terminal doesn't lose visibility into a long pip/git run.
  * SIGHUP ignored for the duration so closing the SSH session
    doesn't kill the update mid-flight.

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
import re
import shutil
import signal
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional

import vexis_agent

log = logging.getLogger(__name__)

# Canonical upstream URL. Mirrors the GH_REPO constant in install.sh —
# both files MUST use the same URL or "vexis-agent update" can pull
# from a different fork than the one curl-bash installed from.
_UPSTREAM_URL = "https://github.com/Zeus-Deus/vexis-agent.git"

# Match a leading semver-ish tag (v1.2.3, v1.2.3-rc1, ...). Same regex
# install.sh's resolve_default_version() uses, kept in sync so the two
# code paths always pick the same "latest tag".
_SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+")


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

    # pipx detection. Two complementary heuristics so we catch both
    # pipx layouts seen in the wild:
    #
    # (a) Prefix match on the *unresolved* absolute python path. Works
    #     when pipx copies the python interpreter into the venv (the
    #     happy path on most modern installs).
    # (b) Walk up from sys.executable looking for the structural
    #     fingerprint of a pipx venv: a directory whose parent is
    #     ``venvs`` and whose grandparent is the pipx root. Works when
    #     pipx symlinks the venv's python at the system interpreter
    #     (pipx 1.12 on Arch / Debian-derivatives) — Path.resolve()
    #     would follow that symlink out of pipx-land, so we walk the
    #     UNRESOLVED path instead.
    #
    # Combining both keeps the heuristic robust without a runtime
    # check of "is this python a symlink?" which itself can race.
    pipx_root = _pipx_home().resolve(strict=False)
    py_abs = py.absolute()
    matches_prefix = str(py_abs).startswith(str(pipx_root) + os.sep)

    walk_match: Optional[Path] = None
    candidate = py_abs
    for _ in range(6):
        parent = candidate.parent
        if parent == candidate:  # reached filesystem root
            break
        if parent.name == "venvs" and parent.parent == pipx_root:
            walk_match = candidate
            break
        candidate = parent

    if matches_prefix or walk_match is not None:
        return InstallInfo(
            kind=InstallType.PIPX,
            python_path=py,
            pipx_venv=walk_match,
        )

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


@contextmanager
def _hangup_protection() -> Iterator[None]:
    """Ignore SIGHUP for the duration so a closed terminal doesn't
    kill the update mid-flight. POSIX-only; the signal disposition
    is preserved across exec(), which means pip/git children inherit
    the protection.

    Restores the previous handler on exit even on exception.
    """
    if not hasattr(signal, "SIGHUP"):  # pragma: no cover — Windows
        yield
        return
    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGHUP, previous)


@contextmanager
def _mirror_to_log(log_path: Path) -> Iterator[None]:
    """Tee stdout + stderr through a log file in $VEXIS_HOME/logs so
    a dropped terminal doesn't lose visibility. The original streams
    are restored on context exit.

    Best-effort: if log_path can't be opened (permission, full disk),
    we skip the mirror and just log a warning — better to update
    without a log than to refuse to update over a logging issue.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_fp = open(log_path, "a", encoding="utf-8", buffering=1)
    except OSError as exc:
        log.warning("could not open update log %s: %s", log_path, exc)
        yield
        return

    class _Tee:
        def __init__(self, primary, secondary):
            self._primary = primary
            self._secondary = secondary

        def write(self, data):
            try:
                self._secondary.write(data)
            except (OSError, ValueError):
                pass
            return self._primary.write(data)

        def flush(self):
            try:
                self._secondary.flush()
            except (OSError, ValueError):
                pass
            self._primary.flush()

        def __getattr__(self, name):  # passthrough for isatty etc.
            return getattr(self._primary, name)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_fp.write(f"\n──── vexis-agent update {stamp} ────\n")
    log_fp.flush()

    orig_out, orig_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(orig_out, log_fp)
    sys.stderr = _Tee(orig_err, log_fp)
    try:
        yield
    finally:
        sys.stdout = orig_out
        sys.stderr = orig_err
        try:
            log_fp.close()
        except OSError:
            pass


def _pre_update_snapshot() -> Optional[Path]:
    """Pack a pre-update zip of $VEXIS_HOME so a botched update is
    recoverable. Returns the archive path, or None if backup raised
    (we don't block updates on snapshot failure)."""
    from vexis_agent.core.paths import vexis_dir
    from vexis_agent.daemon.backup import run_backup

    home = vexis_dir()
    archive = home / "backups" / (
        f"pre-update-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.zip"
    )
    try:
        result = run_backup(out=archive, home=home, workspace=None)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("pre-update snapshot failed: %s", exc)
        return None
    print(
        f"Pre-update snapshot: {result.archive} "
        f"({result.file_count} files)"
    )
    return result.archive


def _update_log_path() -> Path:
    """``$VEXIS_HOME/logs/update.log`` — mirrors the update transcript."""
    from vexis_agent.core.paths import vexis_dir

    return vexis_dir() / "logs" / "update.log"


def run_update(
    channel: str = "stable",
    *,
    info: InstallInfo | None = None,
    snapshot: bool = True,
) -> int:
    """Run the update appropriate for this install. Returns an exit
    code (0 = success, 1 = failure / unsupported install).

    Channel ``"stable"`` is the main branch; ``"dev"`` is the develop
    branch (only meaningful for pipx installs that read from git).
    Editable installs already have a working tree — channel is a no-op.

    Side-effects (Phase 5f):
      * Pre-update zip of $VEXIS_HOME at $VEXIS_HOME/backups/
        pre-update-<utc>.zip (skip with snapshot=False).
      * Output mirrored to $VEXIS_HOME/logs/update.log.
      * SIGHUP ignored for the duration.

    Never restarts the service: the caller (or the user) decides when
    that's safe. Plan §6.4 invariant.
    """
    if info is None:
        info = detect_install_type()

    with _hangup_protection(), _mirror_to_log(_update_log_path()):
        if snapshot:
            _pre_update_snapshot()

        if info.kind is InstallType.PIPX:
            return _update_pipx(channel)
        if info.kind is InstallType.EDITABLE:
            return _update_editable(info.source_root)  # type: ignore[arg-type]
        return _update_unknown()


def _resolve_latest_tag(repo_url: str = _UPSTREAM_URL) -> Optional[str]:
    """Return the newest semver tag on ``repo_url``, or None if none.

    Mirrors install.sh's ``resolve_default_version`` so the curl-bash
    one-liner and ``vexis-agent update`` always converge on the same
    ref. Uses ``git ls-remote`` (no clone) so this is fast even on
    a metered connection.

    Returns None when:
      * ``git`` isn't on PATH (rare, but the daemon doesn't depend on it).
      * The remote has no tags yet (fresh repo before first release).
      * ls-remote times out or errors (offline / 404 / rate-limit).

    Callers fall back to ``main`` on None — same behaviour as
    install.sh.
    """
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            [
                "git", "ls-remote",
                "--tags", "--refs",
                "--sort=-v:refname",
                repo_url,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("git ls-remote failed for %s: %s", repo_url, exc)
        return None
    if result.returncode != 0:
        log.warning(
            "git ls-remote exit %d for %s: %s",
            result.returncode, repo_url, result.stderr.strip(),
        )
        return None

    # Each line: "<sha>\trefs/tags/<tag>". --refs already strips peeled
    # ^{} suffixes; the regex filter further drops non-semver tags
    # (e.g. someone tagging "rc-staging" by accident).
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        ref = parts[1].strip()
        prefix = "refs/tags/"
        if not ref.startswith(prefix):
            continue
        tag = ref[len(prefix):]
        if _SEMVER_TAG_RE.match(tag):
            return tag
    return None


def _update_pipx(channel: str) -> int:
    """Re-run ``pipx install --force git+<repo>@<ref>`` to refresh
    the venv at the chosen channel.

    Channel semantics:
      * ``stable`` (default) — latest semver tag from the remote;
        falls back to ``main`` when the repo has no tags yet.
      * ``dev`` — main branch tip (i.e. last push, possibly unreleased).
      * any other value — treated as a literal git ref (branch / tag /
        sha) so power users can pin via
        ``vexis-agent update --channel v0.3.0``.

    ``pipx upgrade`` is intentionally NOT tried first. For git-source
    installs, pipx remembers the original install spec and re-fetches
    the same ref — meaning "upgrade" wouldn't advance from v0.1.0 to
    v0.2.0. ``pipx install --force`` with an explicitly resolved ref
    is the only verb that does what users mean by "update".
    """
    if shutil.which("pipx") is None:
        print(
            "pipx not found on PATH. Install pipx and re-run "
            "'vexis-agent update', or reinstall manually:\n"
            f"  pipx install git+{_UPSTREAM_URL}",
            flush=True,
        )
        return 1

    if channel == "stable":
        ref = _resolve_latest_tag()
        if ref is None:
            print(
                "No tagged release found on the remote — "
                "falling back to main branch tip.",
                flush=True,
            )
            ref = "main"
        else:
            print(f"Latest release: {ref}", flush=True)
    elif channel == "dev":
        ref = "main"
    else:
        # Power-user escape hatch: treat the channel string as a literal
        # git ref. Lets ``--channel v0.3.0`` pin to a specific release
        # without a separate flag.
        ref = channel

    repo_url = f"git+{_UPSTREAM_URL}@{ref}"
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
        f"  pipx install --force git+{_UPSTREAM_URL}\n"
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
