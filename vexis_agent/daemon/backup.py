"""``vexis-agent backup`` / ``vexis-agent backup-restore`` — pack and
restore the user's personal state.

Targets:
  $VEXIS_HOME/                — config.yaml, .env, curator state,
                                  learning state, goals.json, browser
                                  profiles, dashboard token.
  $VEXIS_WORKSPACE/             — gittable agent brain (CLAUDE.md,
                                  SOUL.md, MEMORY.md, USER.md,
                                  RELATIONSHIPS.md, memories/, skills/).

Excluded by design:
  - The wheel/source code (pipx upgrade or git pull restores it).
  - .git directories anywhere under the workspace (the user can
    re-clone their workspace's git history on the destination).
  - __pycache__ and .pyc / .pyo (regenerated on first import).
  - node_modules anywhere (npm install on destination).
  - SQLite WAL / SHM sidecars — torn pairs of (live db, stale sidecar)
    cause restore-time corruption; the .db itself is captured.
  - Runtime PID files (daemon.pid).

Pattern cribbed from hermes_cli/backup.py — kept much smaller because
vexis's state surface is smaller and there's no SQLite to round-trip.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)


_EXCLUDED_DIR_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    "browser-profiles",  # cached chromium profiles — regenerable, large
}

_EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".db-wal", ".db-shm", ".db-journal")

_EXCLUDED_NAMES = {"daemon.pid"}

# Files restored at mode 0600 because they hold secrets.
_SECRET_NAMES = {".env", "dashboard_token"}


@dataclass(frozen=True)
class BackupResult:
    archive: Path
    file_count: int
    home_root: Path
    workspace_root: Optional[Path]


def _should_skip(rel: Path) -> bool:
    if any(p in _EXCLUDED_DIR_NAMES for p in rel.parts):
        return True
    if rel.name in _EXCLUDED_NAMES:
        return True
    if rel.suffix in _EXCLUDED_SUFFIXES:
        return True
    return False


def _walk_for_backup(root: Path) -> Iterable[Path]:
    """Yield every file under ``root`` that survives the exclude rules.

    Walks lazily so we never hold the full file list in memory — same
    posture hermes uses for ``~/.hermes`` (which can grow large)."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in-place so os.walk doesn't recurse into excluded dirs.
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIR_NAMES]
        base = Path(dirpath)
        for name in filenames:
            full = base / name
            try:
                rel = full.relative_to(root)
            except ValueError:  # pragma: no cover — same root by walk contract
                continue
            if _should_skip(rel):
                continue
            yield full


def _default_archive_path() -> Path:
    """``~/.vexis/backups/vexis-<utcstamp>.zip``."""
    from vexis_agent.core.paths import vexis_dir

    parent = vexis_dir() / "backups"
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return parent / f"vexis-{stamp}.zip"


def run_backup(
    *,
    out: Optional[Path] = None,
    workspace: Optional[Path] = None,
    home: Optional[Path] = None,
) -> BackupResult:
    """Bundle $VEXIS_HOME and $VEXIS_WORKSPACE into a zip.

    The archive is structured with two top-level prefixes (``vexis-home/``
    and ``vexis-workspace/``) so restore can reliably target each.
    """
    from vexis_agent.core.paths import vexis_dir
    from vexis_agent.setup_wizard import workspace_path as _ws_path

    home = home or vexis_dir()
    workspace = workspace or _ws_path()
    out_path = out or _default_archive_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    file_count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if home.exists():
            for p in _walk_for_backup(home):
                arcname = Path("vexis-home") / p.relative_to(home)
                zf.write(p, arcname)
                file_count += 1
        if workspace and workspace.exists():
            for p in _walk_for_backup(workspace):
                arcname = Path("vexis-workspace") / p.relative_to(workspace)
                zf.write(p, arcname)
                file_count += 1

    return BackupResult(
        archive=out_path,
        file_count=file_count,
        home_root=home,
        workspace_root=workspace if workspace and workspace.exists() else None,
    )


@dataclass(frozen=True)
class RestoreResult:
    archive: Path
    home_files_restored: int
    workspace_files_restored: int
    home_dest: Path
    workspace_dest: Path


def run_restore(
    archive: Path,
    *,
    workspace: Optional[Path] = None,
    home: Optional[Path] = None,
    overwrite: bool = False,
) -> RestoreResult:
    """Extract a backup zip back into $VEXIS_HOME / $VEXIS_WORKSPACE.

    By default refuses to overwrite an existing file unless ``overwrite``
    is True. Secrets get re-tightened to mode 0600 on extract because
    zipfile.open drops Unix permission bits.
    """
    from vexis_agent.core.paths import vexis_dir
    from vexis_agent.setup_wizard import workspace_path as _ws_path

    if not archive.exists():
        raise FileNotFoundError(f"backup archive not found: {archive}")

    home = home or vexis_dir()
    workspace = workspace or _ws_path()
    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    home_count = 0
    ws_count = 0

    with zipfile.ZipFile(archive, "r") as zf:
        for member in zf.namelist():
            # Skip directory entries; zipfile creates them on demand.
            if member.endswith("/"):
                continue
            if member.startswith("vexis-home/"):
                rel = member[len("vexis-home/") :]
                dest_root = home
                home_count += 1
            elif member.startswith("vexis-workspace/"):
                rel = member[len("vexis-workspace/") :]
                dest_root = workspace
                ws_count += 1
            else:
                # Unknown prefix — skip rather than dump into home/cwd.
                log.warning("skipping archive entry with unknown prefix: %s", member)
                continue

            target = dest_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and not overwrite:
                log.info("skipping existing file (use overwrite=True): %s", target)
                continue
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if target.name in _SECRET_NAMES:
                try:
                    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
                except OSError:  # pragma: no cover — permission-only filesystem
                    pass

    return RestoreResult(
        archive=archive,
        home_files_restored=home_count,
        workspace_files_restored=ws_count,
        home_dest=home,
        workspace_dest=workspace,
    )
