"""``vexis-agent backup`` / ``vexis-agent backup-restore`` — pack and
restore the user's personal agent state.

What "the agent" is, packaged as:

  $VEXIS_HOME/                — config.yaml, .env, curator state,
                                  learning state, goals.json,
                                  dashboard token, logs.
  $VEXIS_WORKSPACE/             — the soul of the agent:
                                    SOUL.md (personality)
                                    CLAUDE.md (project instructions)
                                    memories/MEMORY.md (situational notes)
                                    memories/USER.md (durable preferences)
                                    RELATIONSHIPS.md (third-party facts)
                                    skills/**/SKILL.md (procedural lessons)

  ~/.claude/projects/<encoded-cwd>/  (opt-in via include_brain_sessions)
                                — claude-code's conversation history.
                                  Vexis reads this for the curator;
                                  including it gives the new install
                                  the same conversational context.

  ~/.local/share/opencode/opencode.db  (opt-in via include_brain_sessions)
                                — opencode's conversation DB. Same
                                  reason as above for opencode users.

Excluded by design:
  - The wheel/source code (pipx upgrade or git pull restores it).
  - .git directories anywhere under the workspace (the user can
    re-clone their workspace's git history on the destination).
  - __pycache__ and .pyc / .pyo (regenerated on first import).
  - node_modules anywhere (npm install on destination).
  - SQLite WAL / SHM sidecars — torn pairs of (live db, stale sidecar)
    cause restore-time corruption; the .db itself is captured.
  - browser-profiles/ — regenerable + can be huge.
  - Runtime PID files (daemon.pid).

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
    brain_sessions_included: bool = False
    brain_session_files: int = 0


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

    Walks lazily so we never hold the full file list in memory — the
    workspace + curator state can grow large over time."""
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


def _brain_session_roots(workspace: Path) -> list[tuple[str, Path]]:
    """Where the brain stores conversation history. Returns
    (archive-prefix, path) pairs for everything that exists.

    claude-code's projects dir is keyed by the workspace path with
    slashes/dots replaced by hyphens — see vexis_agent.core.transcripts
    for the canonical encoder. We preserve the encoded directory name
    in the archive prefix so restore can put it back at the same path
    (which works on the destination iff the user's workspace lives at
    the same absolute path — typically ``~/vexis-workspace`` for both
    source and destination).

    opencode uses a single SQLite DB at a fixed path; that just lands
    back in place wherever Path.home() points on the destination.
    """
    out: list[tuple[str, Path]] = []
    encoded = str(workspace).replace("/", "-").replace(".", "-")
    cc_root = Path.home() / ".claude" / "projects" / encoded
    if cc_root.exists():
        out.append((f"brain-sessions/claude-code/{encoded}", cc_root))
    oc_db = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    if oc_db.exists():
        out.append(("brain-sessions/opencode/opencode.db", oc_db))
    return out


def run_backup(
    *,
    out: Optional[Path] = None,
    workspace: Optional[Path] = None,
    home: Optional[Path] = None,
    include_brain_sessions: bool = False,
) -> BackupResult:
    """Bundle $VEXIS_HOME, $VEXIS_WORKSPACE, and (optionally) the
    brain's conversation history into a zip.

    The archive is structured with top-level prefixes
    (``vexis-home/``, ``vexis-workspace/``, ``brain-sessions/``) so
    restore can reliably target each. Brain sessions are opt-in:
    they can be large (each turn is a JSONL file for claude-code) and
    not all users want to roundtrip past conversations.
    """
    from vexis_agent.core.paths import vexis_dir
    from vexis_agent.setup_wizard import workspace_path as _ws_path

    home = home or vexis_dir()
    workspace = workspace or _ws_path()
    out_path = out or _default_archive_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    file_count = 0
    brain_count = 0
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
        if include_brain_sessions:
            for prefix, src in _brain_session_roots(workspace):
                if src.is_file():
                    zf.write(src, prefix)
                    brain_count += 1
                else:
                    for p in _walk_for_backup(src):
                        arcname = Path(prefix) / p.relative_to(src)
                        zf.write(p, arcname)
                        brain_count += 1

    return BackupResult(
        archive=out_path,
        file_count=file_count + brain_count,
        home_root=home,
        workspace_root=workspace if workspace and workspace.exists() else None,
        brain_sessions_included=include_brain_sessions,
        brain_session_files=brain_count,
    )


@dataclass(frozen=True)
class RestoreResult:
    archive: Path
    home_files_restored: int
    workspace_files_restored: int
    home_dest: Path
    workspace_dest: Path
    brain_sessions_restored: int = 0


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

    brain_count = 0
    cc_dest = Path.home() / ".claude" / "projects"
    oc_db_dest = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

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
            elif member.startswith("brain-sessions/claude-code/"):
                rel = member[len("brain-sessions/claude-code/") :]
                dest_root = cc_dest
                brain_count += 1
            elif member == "brain-sessions/opencode/opencode.db":
                # Single-file destination — handled out-of-band.
                oc_db_dest.parent.mkdir(parents=True, exist_ok=True)
                if oc_db_dest.exists() and not overwrite:
                    log.info("skipping existing %s (use overwrite=True)", oc_db_dest)
                    continue
                with zf.open(member) as src, open(oc_db_dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                brain_count += 1
                continue
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
        brain_sessions_restored=brain_count,
    )
