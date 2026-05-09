"""Skill staging tree + flip-shadow lifecycle for the learning curator.

The learning curator's procedural-tier writes never touch the live
skill tree directly. Every S1 patch, S2 support-file add, and S3
new-umbrella create lands first in ``<workspace>/skills/.shadow/``,
mirroring the live tree layout. The user reviews staged content via
``/learning audit`` (or by inspecting ``.shadow/`` directly) and
flips it live with ``vexis-skill flip-shadow [--all|--skill NAME]``.

Why a staging tree instead of an in-place gate
----------------------------------------------
Skills are procedural knowledge that fires on every relevant task.
A bad write affects every future task in that class. Memory writes
have a similar always-injected character but are factual statements
the model reads and weighs; skills are instructions the model is
told to follow. The staging-tree pattern mirrors how MEMORY-SHADOW.md
works for memory writes — soak in a non-loaded location, flip after
review.

``iter_skill_dirs`` (``core/skills.py:199-218``) skips dotfile
directories, so anything under ``.shadow/`` is invisible to
``discover_skills`` and never lands in the system-prompt skill index.
Same property MEMORY-SHADOW.md relies on.

Per-skill atomicity
-------------------
``flip_shadow_to_live`` moves one skill at a time. Within a single
skill flip we use ``os.replace`` for SKILL.md and each support file —
those are individually atomic. A crash between files leaves the live
tree partially updated for that one skill; re-running the flip is
idempotent (the second pass copies remaining files and clears
staging). Other skills' staged content is untouched.

Day 2 scope: S1 patch, S2 support file, S3 create, list, flip.
Live-tree write is **never** initiated from this module — only from
the explicit ``flip_shadow_to_live`` call which the user triggers
via ``vexis-skill flip-shadow``.
"""

from __future__ import annotations

import difflib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from vexis_agent.core.paths import skills_dir
from vexis_agent.core.skills import (
    ALLOWED_SUBDIRS,
    ARCHIVE_DIR_NAME,
    PinStore,
    UsageStore,
    _find_skill_dir,
    _fuzzy_replace,
    iter_skill_dirs,
    parse_skill_md,
    validate_skill_md,
    validate_skill_name,
)

log = logging.getLogger(__name__)

# Staging tree lives under a dotfile dir so iter_skill_dirs (which
# skips dotfile dirs) treats it as invisible — no risk of staged
# skills leaking into the system-prompt index.
SHADOW_DIR_NAME = ".shadow"


# --------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------


@dataclass(frozen=True)
class StageResult:
    """Outcome of one staging write.

    Carries the staged path so the dispatcher can record it in the
    audit shadow file (the user sees both "we wanted to do X" and
    "the staged file is here").
    """

    ok: bool
    message: str
    staged_path: Path | None = None
    diff: str | None = None  # populated for S1 patches
    extra: dict | None = None


@dataclass(frozen=True)
class StagedSkill:
    """One skill in the staging tree, surfaced by list_staged_skills."""

    name: str
    relative_dir: Path  # relative to <workspace>/skills/.shadow/
    staged_dir: Path    # absolute
    live_dir: Path | None  # absolute; None when the live skill doesn't exist (S3)
    has_skill_md: bool
    support_files: tuple[Path, ...] = ()  # absolute paths of staged support files


@dataclass
class FlipResult:
    """Outcome of flipping one skill from staging into the live tree."""

    ok: bool
    skill_name: str
    message: str
    files_copied: list[str] = field(default_factory=list)
    is_new_skill: bool = False  # True for S3 flips


# --------------------------------------------------------------------
# Path helpers
# --------------------------------------------------------------------


def shadow_skills_root(workspace: Path) -> Path:
    """``<workspace>/skills/.shadow/`` (created on demand).

    This is the staging root. Subdirs mirror the live tree layout:
    a category dir ``<cat>/<name>/`` for categorised skills, or
    just ``<name>/`` for uncategorised. Support files live under
    ``<cat>/<name>/references/`` etc., same as live.
    """
    root = skills_dir(workspace) / SHADOW_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _staged_skill_dir(workspace: Path, skill_name: str, category: str | None) -> Path:
    """Resolve the per-skill staging directory.

    For S1/S2 against an existing skill, callers should use
    ``_resolve_staged_skill_dir`` instead — it mirrors the live
    skill's actual category placement rather than defaulting.
    """
    base = shadow_skills_root(workspace)
    if category:
        return base / category / skill_name
    return base / skill_name


def _resolve_staged_skill_dir(workspace: Path, skill_name: str) -> Path:
    """For S1/S2 (skill must exist live): mirror the live skill's
    actual on-disk path under ``.shadow/``.

    Falls back to the uncategorised location (``.shadow/<name>/``)
    when the live skill isn't found — but callers should validate
    existence themselves before calling this.
    """
    live_dir = _find_skill_dir(skills_dir(workspace), skill_name)
    if live_dir is None:
        return shadow_skills_root(workspace) / skill_name
    rel = live_dir.relative_to(skills_dir(workspace))
    return shadow_skills_root(workspace) / rel


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via temp+rename.

    Mirrors the helper in ``core/skills.py`` so a partial write
    can never leave a half-formed SKILL.md or support file.
    """
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


def _read_skill_md_for_patch(workspace: Path, skill_name: str) -> str | None:
    """Return the most current SKILL.md body for ``skill_name``.

    If the skill is staged (a previous patch already lives in
    ``.shadow/``), return the staged version so successive patches
    accumulate. Otherwise return the live version. Returns None if
    neither exists.
    """
    staged = _resolve_staged_skill_dir(workspace, skill_name) / "SKILL.md"
    if staged.exists():
        try:
            return staged.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("Could not read staged SKILL.md %s: %s", staged, exc)
    live_dir = _find_skill_dir(skills_dir(workspace), skill_name)
    if live_dir is None:
        return None
    try:
        return (live_dir / "SKILL.md").read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Could not read live SKILL.md for %s: %s", skill_name, exc)
        return None


# --------------------------------------------------------------------
# S1: stage a patch against an existing skill
# --------------------------------------------------------------------


def stage_skill_patch(
    workspace: Path,
    skill_name: str,
    patch_old: str,
    patch_new: str,
) -> StageResult:
    """Apply an S1 patch in the staging tree.

    Reads the most-current SKILL.md (staged version if present, else
    live), runs the same fuzzy whitespace-tolerant replace as
    ``patch_skill`` in core/skills.py, validates the result, and
    writes both the patched SKILL.md and a unified-diff sidecar to
    the staging tree.

    Defenses:
      - Refuses pinned skills (defense-in-depth — the prompt also
        steers the LLM away via the ``(pinned, read-only)`` marker
        in the skill index).
      - Refuses if the live skill doesn't exist (S1 requires an
        existing target — the LLM should pick S3 for new skills).
      - Refuses if the patch produces invalid SKILL.md (frontmatter
        broken, body empty, name field changed).
      - Refuses if the patch_old_string isn't found verbatim.
    """
    name_err = validate_skill_name(skill_name)
    if name_err:
        return StageResult(False, name_err)
    if PinStore(skills_dir(workspace)).is_pinned(skill_name):
        return StageResult(
            False,
            f"Skill '{skill_name}' is pinned — S1 patch refused. "
            f"The LLM should not have proposed a patch against a "
            f"pinned skill (the index marks it read-only).",
        )
    live_dir = _find_skill_dir(skills_dir(workspace), skill_name)
    if live_dir is None:
        return StageResult(
            False,
            f"No live skill named '{skill_name}'. S1 requires an "
            f"existing target; pick S3 for new skills.",
        )
    if not patch_old or not patch_new:
        return StageResult(False, "patch_old_string and patch_new_string are required")
    current = _read_skill_md_for_patch(workspace, skill_name)
    if current is None:
        return StageResult(False, f"Could not read SKILL.md for '{skill_name}'")

    new_content, replacements = _fuzzy_replace(
        current, patch_old, patch_new, replace_all=False
    )
    if replacements == 0:
        return StageResult(
            False,
            f"patch_old_string not found in '{skill_name}'. The LLM "
            f"must quote text that exists verbatim in the current "
            f"SKILL.md (or the staged version if a prior patch is "
            f"already in flight).",
        )
    if replacements > 1:
        return StageResult(
            False,
            f"patch_old_string matched {replacements} times in "
            f"'{skill_name}'. The LLM must pick a uniquely-matching "
            f"snippet.",
        )

    err = validate_skill_md(new_content)
    if err:
        return StageResult(False, f"patched content invalid: {err}")
    parsed = parse_skill_md(new_content)
    if parsed is None or parsed.name != skill_name:
        return StageResult(
            False,
            f"patch broke frontmatter — name must remain '{skill_name}'",
        )

    staged_dir = _resolve_staged_skill_dir(workspace, skill_name)
    staged_skill_md = staged_dir / "SKILL.md"
    diff_path = staged_dir / "SKILL.md.diff"
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"live/{skill_name}/SKILL.md",
            tofile=f"staged/{skill_name}/SKILL.md",
        )
    )
    try:
        _atomic_write_text(staged_skill_md, new_content)
        _atomic_write_text(diff_path, diff)
    except OSError as exc:
        return StageResult(False, f"staging write failed: {exc}")
    return StageResult(
        True,
        f"Staged S1 patch for '{skill_name}' at {staged_skill_md}",
        staged_path=staged_skill_md,
        diff=diff,
        extra={"replacements": replacements, "diff_path": str(diff_path)},
    )


# --------------------------------------------------------------------
# S2: stage a support file under an existing skill
# --------------------------------------------------------------------


def stage_support_file(
    workspace: Path,
    skill_name: str,
    rel_path: str,
    content: str,
) -> StageResult:
    """Stage an S2 support file under an existing skill.

    Validation mirrors ``write_supporting_file`` in core/skills.py:
    rel_path must be under references/, templates/, or scripts/, no
    absolute paths, no parent-dir traversal. The skill must exist
    in the live tree (S2 extends an existing umbrella; new skills
    are S3).
    """
    name_err = validate_skill_name(skill_name)
    if name_err:
        return StageResult(False, name_err)
    if PinStore(skills_dir(workspace)).is_pinned(skill_name):
        return StageResult(
            False,
            f"Skill '{skill_name}' is pinned — S2 support file refused.",
        )
    live_dir = _find_skill_dir(skills_dir(workspace), skill_name)
    if live_dir is None:
        return StageResult(
            False,
            f"No live skill named '{skill_name}'. S2 requires an "
            f"existing umbrella; pick S3 for new skills.",
        )
    rel = Path(rel_path)
    if rel.is_absolute() or ".." in rel.parts:
        return StageResult(False, "support_file_path must be a relative path")
    if not rel.parts or rel.parts[0] not in ALLOWED_SUBDIRS:
        return StageResult(
            False,
            f"support_file_path must live under one of {sorted(ALLOWED_SUBDIRS)}",
        )
    if not content or not content.strip():
        return StageResult(False, "support_file_content is empty")

    staged_dir = _resolve_staged_skill_dir(workspace, skill_name)
    target = staged_dir / rel
    try:
        _atomic_write_text(target, content)
    except OSError as exc:
        return StageResult(False, f"staging write failed: {exc}")
    return StageResult(
        True,
        f"Staged S2 support file at {target}",
        staged_path=target,
    )


# --------------------------------------------------------------------
# S3: stage a brand-new umbrella skill
# --------------------------------------------------------------------


def stage_new_skill(
    workspace: Path,
    skill_name: str,
    body: str,
    category: str | None = None,
) -> StageResult:
    """Stage an S3 new-umbrella skill.

    Validation:
      - Name must be valid kebab-case (per core/skills.py).
      - Body must validate as SKILL.md (frontmatter + body).
      - Frontmatter ``name`` must match ``skill_name``.
      - No live-tree collision (existing active skill of same name).
      - No archive collision (archived skill of same name — restore
        from archive first or pick a different name, mirroring
        ``create_skill`` semantics).
      - No staging-tree collision (another S3 already staged with
        the same name; user must flip or remove first).
    """
    name_err = validate_skill_name(skill_name)
    if name_err:
        return StageResult(False, name_err)
    if category is not None:
        cat_err = validate_skill_name(category)
        if cat_err:
            return StageResult(False, f"category: {cat_err}")
    err = validate_skill_md(body)
    if err:
        return StageResult(False, err)
    parsed = parse_skill_md(body)
    if parsed is None or parsed.name != skill_name:
        return StageResult(
            False,
            f"frontmatter name must match the skill name '{skill_name}'",
        )

    live_root = skills_dir(workspace)
    if _find_skill_dir(live_root, skill_name) is not None:
        return StageResult(
            False,
            f"a live skill named '{skill_name}' already exists — S3 "
            f"refused. The LLM should fall back to S1 (patch) or S2 "
            f"(support file) against the existing skill.",
        )
    archived = live_root / ARCHIVE_DIR_NAME / skill_name
    if archived.exists():
        return StageResult(
            False,
            f"a skill named '{skill_name}' is in the archive. "
            f"Restore via /curator restore or pick a different name.",
        )

    staged_dir = _staged_skill_dir(workspace, skill_name, category)
    if staged_dir.exists():
        return StageResult(
            False,
            f"a staged skill named '{skill_name}' already exists at "
            f"{staged_dir}. Flip or remove it first via vexis-skill "
            f"flip-shadow before staging another with the same name.",
        )

    staged_skill_md = staged_dir / "SKILL.md"
    try:
        _atomic_write_text(staged_skill_md, body)
    except OSError as exc:
        return StageResult(False, f"staging write failed: {exc}")
    return StageResult(
        True,
        f"Staged S3 new skill '{skill_name}' at {staged_skill_md}",
        staged_path=staged_skill_md,
        extra={"category": category},
    )


# --------------------------------------------------------------------
# Inspection: list what's currently staged
# --------------------------------------------------------------------


def list_staged_skills(workspace: Path) -> list[StagedSkill]:
    """Enumerate every staged skill under ``.shadow/``.

    Used by the Telegram audit command and by ``vexis-skill
    flip-shadow --all``. Returns one entry per staged-skill
    directory (the directory containing a SKILL.md OR any support
    file). Sorted by name for deterministic output.
    """
    root = shadow_skills_root(workspace)
    if not root.exists():
        return []
    out: list[StagedSkill] = []
    seen: set[Path] = set()
    for skill_md in root.rglob("SKILL.md"):
        skill_dir = skill_md.parent
        if skill_dir in seen:
            continue
        seen.add(skill_dir)
        out.append(_build_staged_skill(workspace, skill_dir, root))
    # Also pick up skill dirs with support files but no staged SKILL.md
    # (S2 against an existing skill — we add references/foo.md but
    # don't restage SKILL.md itself).
    for support_dir_name in ALLOWED_SUBDIRS:
        for support_root in root.rglob(support_dir_name):
            if not support_root.is_dir():
                continue
            skill_dir = support_root.parent
            if skill_dir in seen or skill_dir == root:
                continue
            seen.add(skill_dir)
            out.append(_build_staged_skill(workspace, skill_dir, root))
    out.sort(key=lambda s: s.name)
    return out


def _build_staged_skill(
    workspace: Path, staged_dir: Path, shadow_root: Path
) -> StagedSkill:
    rel = staged_dir.relative_to(shadow_root)
    skill_name = staged_dir.name  # last segment of the staged path
    has_skill_md = (staged_dir / "SKILL.md").exists()
    support_files: list[Path] = []
    for sub in ALLOWED_SUBDIRS:
        sub_dir = staged_dir / sub
        if sub_dir.is_dir():
            for f in sorted(sub_dir.rglob("*")):
                if f.is_file():
                    support_files.append(f)
    live_root = skills_dir(workspace)
    live_dir = _find_skill_dir(live_root, skill_name)
    return StagedSkill(
        name=skill_name,
        relative_dir=rel,
        staged_dir=staged_dir,
        live_dir=live_dir,
        has_skill_md=has_skill_md,
        support_files=tuple(support_files),
    )


# --------------------------------------------------------------------
# Flip: move staged content into the live tree
# --------------------------------------------------------------------


def flip_shadow_to_live(
    workspace: Path,
    *,
    only_skill: str | None = None,
) -> list[FlipResult]:
    """Move staged content from ``.shadow/`` into the live tree.

    If ``only_skill`` is given, flip just that skill. Otherwise flip
    all staged skills.

    Per-skill atomicity: each staged file (SKILL.md, support files)
    is moved via ``os.replace``, which is atomic. A crash mid-flip
    leaves the live tree in either pre-flip state (if no files
    copied yet for that skill) or partial state (some files copied);
    re-running the flip is idempotent.

    For S3 (new skill): creates the live skill dir, copies SKILL.md
    + any staged support files, calls ``UsageStore.initialize``.
    For S1/S2 (mods to existing): copies SKILL.md (overwriting live)
    and/or support files into the live skill dir, calls
    ``UsageStore.bump_patch``.

    The staged dir is removed only after a successful flip — a
    failed flip leaves staging untouched so the user can re-attempt.
    """
    staged = list_staged_skills(workspace)
    if only_skill is not None:
        staged = [s for s in staged if s.name == only_skill]
        if not staged:
            return [FlipResult(False, only_skill, f"no staged skill named '{only_skill}'")]
    if not staged:
        return []
    live_root = skills_dir(workspace)
    pins = PinStore(live_root)
    usage = UsageStore(live_root)
    results: list[FlipResult] = []
    for skill in staged:
        if pins.is_pinned(skill.name):
            results.append(FlipResult(
                False, skill.name,
                f"refusing to flip pinned skill '{skill.name}' — "
                f"unpin first if you really want to overwrite it",
            ))
            continue
        try:
            files_copied = _flip_one(workspace, skill, live_root)
        except OSError as exc:
            results.append(FlipResult(
                False, skill.name, f"flip failed mid-copy: {exc}",
            ))
            continue
        if skill.live_dir is None:
            usage.initialize(skill.name)
            results.append(FlipResult(
                True, skill.name,
                f"flipped new skill '{skill.name}' live ({len(files_copied)} files)",
                files_copied=files_copied,
                is_new_skill=True,
            ))
        else:
            usage.bump_patch(skill.name)
            results.append(FlipResult(
                True, skill.name,
                f"flipped patch/support for '{skill.name}' live "
                f"({len(files_copied)} files)",
                files_copied=files_copied,
            ))
        # Cleanup: remove the staged dir (rmtree handles the case
        # where category dirs become empty after the file move).
        try:
            shutil.rmtree(skill.staged_dir)
            _prune_empty_parents_under(skill.staged_dir, shadow_skills_root(workspace))
        except OSError as exc:
            log.warning("Could not clean staging dir %s: %s", skill.staged_dir, exc)
    return results


def _flip_one(
    workspace: Path,
    skill: StagedSkill,
    live_root: Path,
) -> list[str]:
    """Copy every staged file for ``skill`` into the live tree.

    Returns the list of relative paths copied. Raises OSError on
    failure (caller wraps into FlipResult).
    """
    files_copied: list[str] = []
    if skill.live_dir is None:
        # S3 path: live dir doesn't exist yet. Mirror the staged
        # path under the live root (same category placement).
        live_dir = live_root / skill.relative_dir
        live_dir.mkdir(parents=True, exist_ok=True)
    else:
        live_dir = skill.live_dir

    if skill.has_skill_md:
        src = skill.staged_dir / "SKILL.md"
        dst = live_dir / "SKILL.md"
        _atomic_replace(src, dst)
        files_copied.append("SKILL.md")
    for support_path in skill.support_files:
        rel_to_skill = support_path.relative_to(skill.staged_dir)
        dst = live_dir / rel_to_skill
        _atomic_replace(support_path, dst)
        files_copied.append(str(rel_to_skill))
    return files_copied


def _atomic_replace(src: Path, dst: Path) -> None:
    """Atomically replace ``dst`` with ``src``'s contents.

    Same-filesystem rename when possible (atomic), else copy + unlink
    fallback. The dst's parent directory is ensured before the move.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dst)
    except OSError:
        # Cross-filesystem fallback (rare in our setup but safe).
        shutil.copy2(src, dst)
        src.unlink()


def _prune_empty_parents_under(path: Path, stop_at: Path) -> None:
    """Walk up from ``path``'s parent removing empty dirs until we
    reach ``stop_at``. Quietly stops when a dir isn't empty.

    Used after a per-skill flip cleanup: if removing
    ``.shadow/<cat>/<name>/`` leaves ``.shadow/<cat>/`` empty,
    that empty category dir gets cleaned too — but ``.shadow/``
    itself stays put.
    """
    try:
        stop = stop_at.resolve()
    except OSError:
        return
    current = path.parent
    while True:
        try:
            here = current.resolve()
        except OSError:
            return
        if here == stop:
            return
        try:
            current.rmdir()  # raises if not empty
        except OSError:
            return
        current = current.parent


__all__ = [
    "SHADOW_DIR_NAME",
    "StageResult",
    "StagedSkill",
    "FlipResult",
    "shadow_skills_root",
    "stage_skill_patch",
    "stage_support_file",
    "stage_new_skill",
    "list_staged_skills",
    "flip_shadow_to_live",
]
