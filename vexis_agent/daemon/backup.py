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
    # ``~/.vexis/backups/`` lives inside $VEXIS_HOME so the walker
    # crawls into it on every run. Including PRIOR backups in the
    # current backup compounds linearly per release ("backup of a
    # backup of a backup..."); worse, the in-progress output file
    # itself sits in that directory while being written, so the
    # walker also catches the partially-written zip and produces
    # an unbounded recursive write that fills the disk before the
    # process gets killed. Surfaced on the first migration backup
    # of a real install (10+ GB and growing in seconds, when the
    # actual state was ~300 MB). Pre-update snapshots
    # (``pre-update-*.zip`` from ``daemon.update``) live here too
    # for the same reason — both are regenerable artifacts, not
    # state worth preserving across machines.
    "backups",
}

_EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".db-wal", ".db-shm", ".db-journal")

_EXCLUDED_NAMES = {"daemon.pid"}

# Files restored at mode 0600 because they hold secrets.
_SECRET_NAMES = {".env", "dashboard_token"}

# Files that are MACHINE-SPECIFIC, not brain-state. Even with
# ``--migrate`` (where the user wants the source's brain state to
# replace the destination's), these files must keep the
# destination's values. Otherwise migrating from a dev box would
# clobber the home server's freshly-wizard-generated bot token,
# config.yaml, dashboard token, etc. — turning the migration into
# a brain-and-config takeover instead of a brain-only one.
_MACHINE_LOCAL_HOME_FILES = frozenset({
    ".env",                # TELEGRAM_BOT_TOKEN, etc.
    "config.yaml",         # brain.kind, paths, model assignments
    "config.yaml.bak",     # comment-preservation backup of above
    "dashboard_token",     # generated per-install
    "daemon.pid",          # runtime PID file
})


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
    state: Optional[Path] = None,
    include_brain_sessions: bool = False,
) -> BackupResult:
    """Bundle $VEXIS_HOME, $VEXIS_WORKSPACE, $XDG_STATE_HOME/vexis-agent,
    and (optionally) the brain's conversation history into a zip.

    The archive is structured with top-level prefixes
    (``vexis-home/``, ``vexis-workspace/``, ``vexis-state/``,
    ``brain-sessions/``) so restore can reliably target each.

    The ``vexis-state/`` prefix was added in v0.1.6 after the first
    real migration found that the dashboard's chat-session list
    (``session.json``) lives under ``state_dir()`` =
    ``$XDG_STATE_HOME/vexis-agent/`` (defaults to
    ``~/.local/state/vexis-agent/``), NOT under ``vexis_dir()``
    (``~/.vexis/``). Pre-fix the backup walked vexis_dir + workspace
    only, so every dev → home-server migration silently dropped the
    dashboard's session list. Users saw "1 session" on the home
    server (just the daemon-bootstrapped one) instead of the
    accumulated dev list.

    Brain sessions are opt-in: they can be large (each turn is a
    JSONL file for claude-code) and not all users want to roundtrip
    past conversations.
    """
    from vexis_agent.core.paths import state_dir, vexis_dir
    from vexis_agent.setup_wizard import workspace_path as _ws_path

    home = home or vexis_dir()
    workspace = workspace or _ws_path()
    state = state or state_dir()
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
        if state and state.exists():
            for p in _walk_for_backup(state):
                arcname = Path("vexis-state") / p.relative_to(state)
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
    state_files_restored: int = 0


def _encode_workspace_for_claude_code(workspace: Path) -> str:
    """Mirror of vexis_agent.core.transcripts encoding: replaces both
    ``/`` and ``.`` with ``-`` so absolute paths become valid single-
    segment directory names under ``~/.claude/projects/``. Used at
    restore time to re-key brain sessions to the destination's
    workspace path — see the comment in the brain-sessions branch
    of ``run_restore`` for why."""
    return str(workspace).replace("/", "-").replace(".", "-")


def run_restore(
    archive: Path,
    *,
    workspace: Optional[Path] = None,
    home: Optional[Path] = None,
    state: Optional[Path] = None,
    overwrite: bool = False,
    migrate: bool = False,
) -> RestoreResult:
    """Extract a backup zip back into $VEXIS_HOME / $VEXIS_WORKSPACE.

    Three restore modes, in increasing aggressiveness:

      * Default (``overwrite=False``, ``migrate=False``) — strict
        non-destructive overlay. Existing files are skipped. Right for
        topping up a partial install with backed-up extras.

      * ``migrate=True`` — the new-machine migration mode (added
        v0.1.5 after the first real dev→home-server migration ran into
        the daemon-stub trap). Overwrites EVERY file from the archive
        EXCEPT machine-specific ones (``_MACHINE_LOCAL_HOME_FILES``).
        The trap: if pandora's daemon ran for a few minutes between
        the wizard install and the restore, the curator silently
        wrote a stub ``USER.md``; the default overlay then refused to
        replace it with dev's accumulated 4-line version. ``migrate``
        explicitly says "I want my source brain state on this box,
        but keep this box's bot token / config / paths" — which is
        what users actually want when seeding a new machine.

      * ``overwrite=True`` — replaces EVERYTHING including the bot
        token. Almost never what users want; preserved for tests and
        for the rare case of a true bit-for-bit restore on the same
        machine.

    Brain-session re-encoding: claude-code's session storage at
    ``~/.claude/projects/<encoded-cwd>/`` keys directories on the
    workspace's absolute path. dev's ``/home/zeus/vexis-workspace``
    encodes to ``-home-zeus-vexis-workspace``; pandora's
    ``/home/deus/vexis-workspace`` encodes to
    ``-home-deus-vexis-workspace``. Without re-encoding at restore,
    the dev sessions land at the source-encoded path on the
    destination — orphaned because the destination's daemon reads
    from a different directory. We strip the source-encoded prefix
    and prepend the destination-encoded one so sessions actually
    reach the daemon's curator.

    Secrets get re-tightened to mode 0600 on extract because
    zipfile.open drops Unix permission bits.
    """
    from vexis_agent.core.paths import state_dir, vexis_dir
    from vexis_agent.setup_wizard import workspace_path as _ws_path

    if not archive.exists():
        raise FileNotFoundError(f"backup archive not found: {archive}")

    home = home or vexis_dir()
    workspace = workspace or _ws_path()
    state = state or state_dir()
    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)

    home_count = 0
    ws_count = 0
    state_count = 0

    brain_count = 0
    cc_dest = Path.home() / ".claude" / "projects"
    oc_db_dest = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    dest_workspace_encoded = _encode_workspace_for_claude_code(workspace)

    with zipfile.ZipFile(archive, "r") as zf:
        for member in zf.namelist():
            # Skip directory entries; zipfile creates them on demand.
            if member.endswith("/"):
                continue
            is_machine_local = False
            if member.startswith("vexis-home/"):
                rel = member[len("vexis-home/") :]
                dest_root = home
                home_count += 1
                # Track machine-specific files: even ``migrate`` mode
                # must NOT overwrite these. The comparison is on the
                # leaf basename — ``.env`` at the home root, not any
                # nested file that happens to be named the same.
                is_machine_local = (
                    "/" not in rel and rel in _MACHINE_LOCAL_HOME_FILES
                )
            elif member.startswith("vexis-workspace/"):
                rel = member[len("vexis-workspace/") :]
                dest_root = workspace
                ws_count += 1
            elif member.startswith("vexis-state/"):
                # XDG state dir: dashboard chat sessions (session.json),
                # background-task logs, daemon log. Keyed under its own
                # prefix because state_dir() is independent of vexis_dir()
                # — see run_backup's docstring for the v0.1.6 origin.
                rel = member[len("vexis-state/") :]
                dest_root = state
                state_count += 1
            elif member.startswith("brain-sessions/claude-code/"):
                # Re-encode the workspace prefix so dev's sessions
                # land at the destination's actual session-dir path,
                # not orphaned at dev's encoded path.
                raw_rel = member[len("brain-sessions/claude-code/") :]
                parts = raw_rel.split("/", 1)
                if len(parts) == 2:
                    # parts[0] = source-encoded workspace dir name;
                    # replace with destination-encoded.
                    rel = f"{dest_workspace_encoded}/{parts[1]}"
                else:
                    # No subpath — unusual, skip rather than dump
                    # at the projects-dir root.
                    log.warning(
                        "skipping malformed brain-sessions entry: %s",
                        member,
                    )
                    continue
                dest_root = cc_dest
                brain_count += 1
            elif member == "brain-sessions/opencode/opencode.db":
                # Single-file destination — handled out-of-band.
                oc_db_dest.parent.mkdir(parents=True, exist_ok=True)
                if oc_db_dest.exists() and not (overwrite or migrate):
                    log.info(
                        "skipping existing %s (use overwrite=True or migrate=True)",
                        oc_db_dest,
                    )
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
            # Tri-mode existence check. ``overwrite`` always replaces.
            # ``migrate`` replaces UNLESS the file is machine-local.
            # Default replaces nothing.
            if target.exists():
                if overwrite:
                    pass  # always replace
                elif migrate and not is_machine_local:
                    pass  # migrate replaces brain state
                else:
                    log.info(
                        "skipping existing file (use overwrite or migrate): %s",
                        target,
                    )
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
        state_files_restored=state_count,
        home_dest=home,
        workspace_dest=workspace,
        brain_sessions_restored=brain_count,
    )
