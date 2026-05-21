"""Per-skill version history.

Every edit to a workspace skill's SKILL.md — whether by the agent
mid-session, the background curator, or the user via the dashboard —
snapshots the *pre-edit* content into
``<workspace>/skills/.history/<skill-name>/``. This gives a
recoverable timeline: "what did this skill say before the curator
rewrote it last week?"

Storage
-------
One file per superseded version::

    .history/<skill-name>/<recorded-at>__<actor>.md

* ``recorded-at`` — compact UTC timestamp ``YYYYMMDDThhmmssffffffZ``,
  also the version id used in API routes. Microsecond precision so
  two writes in the same second don't collide.
* ``actor`` — who triggered the edit that replaced this version:
  ``agent`` (Vexis in-session), ``curator`` (background curator), or
  ``dashboard`` (the user). An unrecognised actor degrades to
  ``agent`` rather than corrupting the filename grammar.

The file body is the verbatim SKILL.md content as it was *before*
the edit. The live SKILL.md is always the current version; history
holds only what's been replaced — so a freshly-created skill has an
empty history until its first edit.

Best-effort by contract
-----------------------
``record_version`` never raises into its caller. A skill write must
succeed even if the history snapshot can't be written — losing a
history entry is annoying; blocking a skill edit is broken. Same
two-tier philosophy as ``.usage.json`` telemetry (see ``core/skills.py``).

Retention: the newest ``MAX_VERSIONS_PER_SKILL`` snapshots are kept;
older ones are pruned on each write.

Scope: only SKILL.md is versioned. Supporting files under
``references/`` / ``templates/`` / ``scripts/`` are not — SKILL.md is
the skill body and the thing edits actually churn.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Dotfile dir under <workspace>/skills/. iter_skill_dirs (core/skills.py)
# skips dotfile dirs, so history snapshots never leak into the
# system-prompt skill index.
HISTORY_DIR_NAME = ".history"

# Keep the newest N snapshots per skill; prune older on every write.
MAX_VERSIONS_PER_SKILL = 20

# Recognised edit actors. The filename embeds one of these; anything
# else degrades to ACTOR_AGENT so the ``<vid>__<actor>`` grammar holds.
ACTOR_AGENT = "agent"
ACTOR_CURATOR = "curator"
ACTOR_DASHBOARD = "dashboard"
_VALID_ACTORS: frozenset[str] = frozenset(
    {ACTOR_AGENT, ACTOR_CURATOR, ACTOR_DASHBOARD}
)

# Compact UTC stamp: YYYYMMDD 'T' HHMMSS ffffff 'Z' → 8 + 1 + 12 + 1.
_VID_FMT = "%Y%m%dT%H%M%S%fZ"
_VERSION_ID_RE = re.compile(r"^\d{8}T\d{12}Z$")

# Skill names are lowercase kebab-case (mirrors _NAME_RE in core/skills.py).
# Re-declared here rather than imported to avoid a circular import:
# core/skills.py imports record_version from this module.
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


@dataclass(frozen=True)
class SkillVersion:
    """One superseded SKILL.md snapshot.

    ``version_id`` is the compact stamp (also the API route segment);
    ``recorded_at`` is the same instant rendered as ISO 8601 for
    display. ``size`` is the snapshot's byte length.
    """

    version_id: str
    actor: str
    recorded_at: str
    size: int


# --------------------------------------------------------------------
# Path + parsing helpers
# --------------------------------------------------------------------


def _history_root(skills_root: Path) -> Path:
    return skills_root / HISTORY_DIR_NAME


def _skill_history_dir(skills_root: Path, name: str) -> Path:
    return _history_root(skills_root) / name


def _safe_name(name: str) -> bool:
    """Guard the read path: a route param must not escape the tree."""
    return bool(name) and _NAME_RE.match(name) is not None


def _now_version_id() -> str:
    return datetime.now(timezone.utc).strftime(_VID_FMT)


def _vid_to_iso(vid: str) -> str | None:
    """Compact version id → ISO 8601 (``...Z``). None if unparseable."""
    try:
        dt = datetime.strptime(vid, _VID_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_text(path: Path, content: str) -> None:
    """Temp-file + rename write. Mirrors the helper in core/skills.py so
    a partial write can never leave a half-formed snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _parse_version_file(path: Path) -> SkillVersion | None:
    """Build a SkillVersion from a ``<vid>__<actor>.md`` file. None when
    the filename doesn't match the grammar (hand-dropped junk, etc)."""
    stem = path.stem  # filename without ".md"
    if "__" not in stem:
        return None
    vid, actor = stem.rsplit("__", 1)
    if not _VERSION_ID_RE.match(vid):
        return None
    iso = _vid_to_iso(vid)
    if iso is None:
        return None
    if actor not in _VALID_ACTORS:
        actor = ACTOR_AGENT
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return SkillVersion(version_id=vid, actor=actor, recorded_at=iso, size=size)


def _prune(hist_dir: Path) -> None:
    """Keep the newest MAX_VERSIONS_PER_SKILL snapshots, drop the rest.

    Version ids sort lexicographically in timestamp order, so a plain
    name sort is chronological.
    """
    try:
        files = sorted(
            (p for p in hist_dir.iterdir() if p.is_file() and p.suffix == ".md"),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError:
        return
    for old in files[MAX_VERSIONS_PER_SKILL:]:
        try:
            old.unlink()
        except OSError:
            log.debug("could not prune skill-history file %s", old)


# --------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------


def record_version(
    skills_root: Path, name: str, content: str, actor: str = ACTOR_AGENT
) -> None:
    """Snapshot ``content`` as a superseded version of skill ``name``.

    Best-effort: any failure is swallowed (logged at debug). Callers
    pass the *pre-edit* SKILL.md content right before they overwrite
    it. An empty ``content`` is ignored — there's nothing to recover.
    """
    if not content or not _safe_name(name):
        return
    if actor not in _VALID_ACTORS:
        actor = ACTOR_AGENT
    try:
        hist_dir = _skill_history_dir(skills_root, name)
        hist_dir.mkdir(parents=True, exist_ok=True)
        vid = _now_version_id()
        dest = hist_dir / f"{vid}__{actor}.md"
        # Same-microsecond collision is near-impossible but cheap to
        # rule out — spin a fresh stamp until the path is free.
        while dest.exists():
            vid = _now_version_id()
            dest = hist_dir / f"{vid}__{actor}.md"
        _atomic_write_text(dest, content)
        _prune(hist_dir)
    except OSError as exc:
        log.debug("skill-history snapshot failed for %r: %s", name, exc)


def list_versions(skills_root: Path, name: str) -> list[SkillVersion]:
    """Every recorded version of skill ``name``, newest first.

    Empty list when the skill has no history yet (never edited) or the
    name is malformed.
    """
    if not _safe_name(name):
        return []
    hist_dir = _skill_history_dir(skills_root, name)
    if not hist_dir.is_dir():
        return []
    out: list[SkillVersion] = []
    try:
        entries = list(hist_dir.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".md":
            continue
        version = _parse_version_file(entry)
        if version is not None:
            out.append(version)
    out.sort(key=lambda v: v.version_id, reverse=True)
    return out


def read_version(
    skills_root: Path, name: str, version_id: str
) -> str | None:
    """Full SKILL.md content of one recorded version, or None if absent.

    ``version_id`` is regex-validated and ``name`` kebab-checked before
    any path is built, so a hostile route param can't traverse out of
    the history tree.
    """
    if not _safe_name(name) or not _VERSION_ID_RE.match(version_id or ""):
        return None
    hist_dir = _skill_history_dir(skills_root, name)
    if not hist_dir.is_dir():
        return None
    # version_id is the unique prefix; the actor suffix varies.
    for match in sorted(hist_dir.glob(f"{version_id}__*.md")):
        try:
            return match.read_text(encoding="utf-8")
        except OSError as exc:
            log.debug("could not read skill-history file %s: %s", match, exc)
            return None
    return None


__all__ = [
    "HISTORY_DIR_NAME",
    "MAX_VERSIONS_PER_SKILL",
    "ACTOR_AGENT",
    "ACTOR_CURATOR",
    "ACTOR_DASHBOARD",
    "SkillVersion",
    "record_version",
    "list_versions",
    "read_version",
]
