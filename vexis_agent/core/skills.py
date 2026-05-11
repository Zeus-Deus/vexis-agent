"""Skills: procedural knowledge the agent can create, load, and modify.

Each skill is a directory ``<workspace>/skills/<category>/<name>/``
containing a SKILL.md with YAML frontmatter (``name``, ``description``)
plus optional ``references/``, ``templates/``, ``scripts/`` subdirs.

Skill index injection happens at session start: every SKILL.md is
parsed for its description, the ``<available_skills>`` block is
rendered, and the brain pulls a single skill body via ``view`` only
when it decides one is relevant. This keeps the always-on token tax
proportional to skill count, not skill content.

Telemetry (``.usage.json``) and pinning (``.pinned.json``) live as
sidecar JSON files alongside the skill tree. Both are intentionally
NOT locked — they're lossy/statistical state and atomic-rename is
enough. (MEMORY.md is locked because losing an entry is user-visible;
losing a counter bump is not. Two-tier consistency by design.)

CRITICAL CURATOR CONTRACT
-------------------------
The curator's spawned ``claude -p`` runs with ``VEXIS_CURATOR=1`` in
its environment. The skill CLI checks that env var and refuses
``delete`` and ``remove_file`` outright — code-enforced, not
prompt-enforced. The curator can still merge / patch / archive but
cannot ratchet itself into deletion. See ``tools/skills_cli.py``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml

log = logging.getLogger(__name__)

# Frontmatter contract: name + description required; description capped
# at 1024 chars to keep the always-on index modest.
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_MD_CHARS = 100_000
MAX_SUPPORTING_FILE_BYTES = 1024 * 1024  # 1 MiB

# Subdirs that may receive supporting files. Anything else is rejected
# at the CLI layer — this is the whitelist that prevents the skill tree
# from sprouting a `secrets/` or `node_modules/` by accident.
ALLOWED_SUBDIRS: frozenset[str] = frozenset({"references", "templates", "scripts"})

# Names of skill subdirs/files that are NOT skills (sidecar state,
# archive, backups, telemetry).
RESERVED_NAMES: frozenset[str] = frozenset(
    {".archive", ".curator_backups", ".usage.json", ".pinned.json"}
)

ARCHIVE_DIR_NAME = ".archive"
CURATOR_BACKUPS_DIR_NAME = ".curator_backups"
USAGE_JSON_NAME = ".usage.json"
PINNED_JSON_NAME = ".pinned.json"

# Skill states for the curator's deterministic phase.
STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


# --------------------------------------------------------------------
# Frontmatter parsing
# --------------------------------------------------------------------


@dataclass(frozen=True)
class SkillMeta:
    """Parsed SKILL.md metadata. ``body`` is the markdown after frontmatter."""

    name: str
    description: str
    body: str
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str] | None:
    """Return (frontmatter, body) or None on parse failure.

    A SKILL.md must start with ``---``, have a closing ``---`` line,
    and parse as a YAML mapping. Anything else returns None and is
    treated as a malformed skill (silently skipped at index-build
    time, hard error at create time).
    """
    if not content.startswith("---"):
        return None
    match = re.search(r"\n---\s*\n", content[3:])
    if not match:
        return None
    yaml_part = content[3 : match.start() + 3]
    body = content[match.end() + 3 :]
    try:
        parsed = yaml.safe_load(yaml_part)
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed, body


def parse_skill_md(content: str) -> SkillMeta | None:
    """Parse a SKILL.md string. Returns None if the file is malformed.

    Index discovery uses None to mean "skip silently" — a single broken
    SKILL.md should not blow up the whole session. Create-time
    validation does its own stricter check via ``validate_skill_md``.
    """
    parsed = _parse_frontmatter(content)
    if parsed is None:
        return None
    fm, body = parsed
    name = fm.get("name")
    description = fm.get("description")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(description, str) or not description.strip():
        return None
    return SkillMeta(
        name=name.strip(),
        description=description.strip(),
        body=body,
        raw_frontmatter=fm,
    )


def validate_skill_md(content: str) -> str | None:
    """Return a human-readable error if ``content`` is invalid; None if OK.

    Stricter than ``parse_skill_md`` — used at create/edit time so the
    model gets a specific reason to fix rather than a silent skip.
    """
    if not content.strip():
        return "Content cannot be empty."
    if not content.startswith("---"):
        return (
            "SKILL.md must start with YAML frontmatter (---). "
            "See existing skills for the format."
        )
    match = re.search(r"\n---\s*\n", content[3:])
    if not match:
        return "SKILL.md frontmatter is not closed. Add a '---' line below the YAML."
    yaml_part = content[3 : match.start() + 3]
    try:
        parsed = yaml.safe_load(yaml_part)
    except yaml.YAMLError as exc:
        return f"YAML frontmatter parse error: {exc}"
    if not isinstance(parsed, dict):
        return "Frontmatter must be a YAML mapping (key: value pairs)."
    if "name" not in parsed:
        return "Frontmatter must include 'name'."
    if "description" not in parsed:
        return "Frontmatter must include 'description'."
    desc = parsed["description"]
    if not isinstance(desc, str) or not desc.strip():
        return "Frontmatter 'description' must be a non-empty string."
    if len(desc) > MAX_DESCRIPTION_LENGTH:
        return f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters."
    body = content[match.end() + 3 :].strip()
    if not body:
        return (
            "SKILL.md body is empty — add procedural instructions or "
            "domain knowledge below the frontmatter."
        )
    if len(content) > MAX_SKILL_MD_CHARS:
        return f"SKILL.md exceeds {MAX_SKILL_MD_CHARS} characters."
    return None


def validate_skill_name(name: str) -> str | None:
    """Return an error message or None. Names are lowercase kebab-case."""
    if not isinstance(name, str) or not name:
        return "skill name is required"
    if not _NAME_RE.match(name):
        return (
            "skill name must be lowercase kebab-case, 2-64 chars, starting "
            "with a letter (e.g. 'cursor-debugging')"
        )
    return None


# --------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------


def iter_skill_dirs(skills_root: Path) -> Iterator[Path]:
    """Yield directories that contain a SKILL.md, recursively.

    Skips dotfile dirs and ``node_modules``. The archive directory is
    excluded so archived skills don't appear in the index — they're
    still on disk and recoverable via /curator restore, just invisible.
    """
    if not skills_root.exists():
        return
    for skill_md in skills_root.rglob("SKILL.md"):
        try:
            rel = skill_md.relative_to(skills_root)
        except ValueError:
            continue
        if any(
            part.startswith(".") or part == "node_modules" for part in rel.parts[:-1]
        ):
            continue
        yield skill_md.parent


def discover_skills(skills_root: Path) -> list[SkillMeta]:
    """Parse every SKILL.md under ``skills_root``. Malformed files are
    silently skipped (logged at debug). Returns metas sorted by name."""
    metas: list[SkillMeta] = []
    seen_names: set[str] = set()
    for skill_dir in iter_skill_dirs(skills_root):
        skill_md = skill_dir / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            log.debug("Skipping unreadable %s: %s", skill_md, exc)
            continue
        meta = parse_skill_md(content)
        if meta is None:
            log.debug("Skipping malformed skill at %s", skill_md)
            continue
        if meta.name in seen_names:
            # First-seen wins. Mirroring directory structure usually
            # makes this a non-issue, but a hand-edited duplicate
            # shouldn't surface as two separate index entries.
            log.warning(
                "Duplicate skill name '%s' at %s; ignoring", meta.name, skill_md
            )
            continue
        seen_names.add(meta.name)
        metas.append(meta)
    return sorted(metas, key=lambda m: m.name)


# --------------------------------------------------------------------
# Index injection
# --------------------------------------------------------------------


_INDEX_PREAMBLE = (
    "## Skills (mandatory)\n"
    "Before replying, scan the skills below. If a skill matches or is even "
    "partially relevant to your task, you MUST load it with skill_view "
    "(via `vexis-skill view <name>`) and follow its instructions. Err on "
    "the side of loading — better to have context you don't need than miss "
    "critical steps, pitfalls, or established workflows."
)


# Hermes-style in-session skill self-authoring guidance. Surfaced in
# EVERY session — even an empty workspace with zero skills — so a
# brand-new install can bootstrap its own skill library on the first
# non-trivial task. Mirrors NousResearch/hermes-agent's
# ``SKILLS_GUIDANCE`` constant in ``agent/prompt_builder.py:179-186``;
# adapted to vexis's CLI surface (``vexis-skill {create,patch}``) and
# the staging-tree review flow (writes land in ``skills/.shadow/`` and
# go live via ``vexis-skill flip-shadow``, so the brain doesn't need
# user approval to call ``create``/``patch`` — the gate is downstream).
#
# Three behaviours this block exists to drive:
#   1. End-of-task reflection — was this 5+ tool calls / a tricky
#      error / a workflow worth reusing? If yes, capture the shortcut
#      as a skill BEFORE declaring done.
#   2. Save the shortcut, not the discovery path — the body should be
#      the cheat sheet (the JS eval, the single curl, the one
#      dispatcher call), not the 20-click route that led there.
#   3. Patch-on-use — if a loaded skill is wrong/incomplete/outdated,
#      fix it with ``vexis-skill patch`` IMMEDIATELY, before
#      continuing the task. Unmaintained skills drift; drift is worse
#      than no skill.
_AUTHORING_GUIDANCE = (
    "## Skill authoring (mandatory)\n"
    "After a non-trivial task — ≥5 tool calls, a tricky error you "
    "had to work around, or any workflow you'd want to reuse — "
    "capture it as a skill with `vexis-skill create` BEFORE telling "
    "the user you're done. Skills are how you avoid re-discovering "
    "the same solution next session.\n"
    "\n"
    "Save the SHORTCUT, not the discovery path. If you tried 20 "
    "steps and then found a single `curl`, JS eval, or one-line "
    "dispatcher call that got the same result, the skill body is "
    "that shortcut — not the meandering route that led you to it. "
    "Future-you wants the cheat sheet, not the journal.\n"
    "\n"
    "When you load a skill via `vexis-skill view` and find it "
    "outdated, incomplete, or wrong, patch it with "
    "`vexis-skill patch` IMMEDIATELY — don't wait to be asked. "
    "Skills that aren't maintained become liabilities; drift is "
    "worse than no skill at all.\n"
    "\n"
    "Skill writes from this CLI land in the live tree and take "
    "effect on the user's NEXT session (the system-prompt index "
    "is frozen for the current session — same frozen-snapshot "
    "rule as memory). The user reviews the skill library through "
    "the dashboard Skills tab and can archive or pin anything you "
    "create that turns out to be a bad call. If you're unsure "
    "whether a workflow is reusable, lean toward creating — an "
    "unused skill costs ~one description line in the next session's "
    "index; a missing skill costs the full re-discovery."
)


def build_skill_authoring_block() -> str:
    """Hermes-style in-session skill authoring guidance.

    Always returns the same non-empty string — independent of
    workspace state. Both brain prompt builders inject this so the
    instruction is present even when the skills index is empty
    (chicken-and-egg: zero skills → no index → no nudge to ever
    create one).

    Pure-function on purpose: no filesystem access, no config read.
    Caching at the brain layer (per-session UUID) reuses the same
    bytes across turns, keeping Anthropic's prefix cache warm.
    """
    return _AUTHORING_GUIDANCE


def build_skills_index_block(skills_root: Path) -> str:
    """Render the ``## Skills (mandatory)`` block. Empty when no skills."""
    metas = discover_skills(skills_root)
    if not metas:
        return ""
    lines = ["<available_skills>"]
    for meta in metas:
        # Description is already capped at MAX_DESCRIPTION_LENGTH at
        # create time, but we re-truncate defensively in case a
        # hand-edit slipped a long description past validation.
        desc = meta.description
        if len(desc) > MAX_DESCRIPTION_LENGTH:
            desc = desc[: MAX_DESCRIPTION_LENGTH - 1] + "…"
        lines.append(f"- {meta.name}: {desc}")
    lines.append("</available_skills>")
    return f"{_INDEX_PREAMBLE}\n\n" + "\n".join(lines)


# --------------------------------------------------------------------
# Sidecar JSON helpers (telemetry + pinning)
# --------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not parse %s (%s); treating as empty", path, exc)
        return {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically. No lock — callers tolerate counter races."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------
# Telemetry (.usage.json)
# --------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_usage_record() -> dict[str, Any]:
    return {
        "use_count": 0,
        "view_count": 0,
        "last_used_at": None,
        "last_viewed_at": None,
        "patch_count": 0,
        "last_patched_at": None,
        "created_at": _utc_now_iso(),
        "state": STATE_ACTIVE,
        "archived_at": None,
    }


class UsageStore:
    """Wraps ``.usage.json``. All methods best-effort: telemetry must
    never break the agent's ability to run."""

    def __init__(self, skills_root: Path) -> None:
        self._path = skills_root / USAGE_JSON_NAME

    def load(self) -> dict[str, dict[str, Any]]:
        raw = _load_json(self._path)
        return raw if isinstance(raw, dict) else {}

    def save(self, data: dict[str, dict[str, Any]]) -> None:
        try:
            _atomic_write_json(self._path, data)
        except OSError as exc:
            log.debug("Failed to write %s: %s", self._path, exc, exc_info=True)

    def record(self, name: str) -> dict[str, Any]:
        """Live record for ``name``, creating the empty default if missing."""
        data = self.load()
        rec = data.get(name)
        if not isinstance(rec, dict):
            return _empty_usage_record()
        return rec

    def _mutate(self, name: str, mutator) -> None:
        data = self.load()
        rec = data.get(name)
        if not isinstance(rec, dict):
            rec = _empty_usage_record()
        mutator(rec)
        data[name] = rec
        self.save(data)

    def bump_view(self, name: str) -> None:
        def _m(r: dict[str, Any]) -> None:
            r["view_count"] = int(r.get("view_count", 0)) + 1
            r["last_viewed_at"] = _utc_now_iso()
            # Per Hermes: viewing == using. Keeps the curator's stale
            # timer correct without requiring two distinct call paths.
            r["use_count"] = int(r.get("use_count", 0)) + 1
            r["last_used_at"] = _utc_now_iso()

        self._mutate(name, _m)

    def bump_patch(self, name: str) -> None:
        def _m(r: dict[str, Any]) -> None:
            r["patch_count"] = int(r.get("patch_count", 0)) + 1
            r["last_patched_at"] = _utc_now_iso()
            # A patch is implicit use — the agent thought enough about
            # this skill to modify it.
            r["use_count"] = int(r.get("use_count", 0)) + 1
            r["last_used_at"] = _utc_now_iso()

        self._mutate(name, _m)

    def initialize(self, name: str) -> None:
        """Create an empty record for ``name`` if absent. Idempotent."""

        def _m(r: dict[str, Any]) -> None:
            r.setdefault("created_at", _utc_now_iso())
            r.setdefault("state", STATE_ACTIVE)

        self._mutate(name, _m)

    def set_state(self, name: str, state: str) -> None:
        def _m(r: dict[str, Any]) -> None:
            r["state"] = state
            if state == STATE_ARCHIVED:
                r["archived_at"] = _utc_now_iso()
            elif state == STATE_ACTIVE:
                r["archived_at"] = None

        self._mutate(name, _m)

    def forget(self, name: str) -> None:
        data = self.load()
        if name in data:
            del data[name]
            self.save(data)


# --------------------------------------------------------------------
# Pinning (.pinned.json)
# --------------------------------------------------------------------


class PinStore:
    """Owns ``.pinned.json``. Loaded fresh on every check — pinning
    operations are infrequent and a stale cache here would silently
    let the curator step on a freshly-pinned skill."""

    def __init__(self, skills_root: Path) -> None:
        self._path = skills_root / PINNED_JSON_NAME

    def list(self) -> list[str]:
        raw = _load_json(self._path)
        pinned = raw.get("pinned") if isinstance(raw, dict) else None
        return list(pinned) if isinstance(pinned, list) else []

    def is_pinned(self, name: str) -> bool:
        return name in self.list()

    def pin(self, name: str) -> bool:
        names = self.list()
        if name in names:
            return False
        names.append(name)
        names.sort()
        _atomic_write_json(self._path, {"pinned": names})
        return True

    def unpin(self, name: str) -> bool:
        names = self.list()
        if name not in names:
            return False
        names.remove(name)
        _atomic_write_json(self._path, {"pinned": names})
        return True


# --------------------------------------------------------------------
# Skill manage operations
# --------------------------------------------------------------------


@dataclass(frozen=True)
class OpResult:
    """Uniform success/error envelope for skill_manage operations."""

    ok: bool
    message: str
    extra: dict[str, Any] | None = None


def _find_skill_dir(skills_root: Path, name: str) -> Path | None:
    """Locate the directory whose SKILL.md has ``name`` in its frontmatter."""
    for skill_dir in iter_skill_dirs(skills_root):
        try:
            content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        except OSError:
            continue
        meta = parse_skill_md(content)
        if meta is not None and meta.name == name:
            return skill_dir
    return None


def view_skill(
    skills_root: Path, name: str, file_path: str | None = None
) -> OpResult:
    """Read SKILL.md or a supporting file. Bumps view+use telemetry on
    success. Per the design contract, viewing == using — the agent
    pulled this into context to act on it."""
    name_err = validate_skill_name(name)
    if name_err:
        return OpResult(False, name_err)
    skill_dir = _find_skill_dir(skills_root, name)
    if skill_dir is None:
        return OpResult(False, f"No skill named '{name}'.")

    if file_path is None:
        target = skill_dir / "SKILL.md"
    else:
        rel = Path(file_path)
        if rel.is_absolute() or ".." in rel.parts:
            return OpResult(
                False, "file_path must be a relative path within the skill dir"
            )
        if not rel.parts or rel.parts[0] not in ALLOWED_SUBDIRS:
            return OpResult(
                False,
                f"file_path must live under one of {sorted(ALLOWED_SUBDIRS)}.",
            )
        target = skill_dir / rel

    try:
        content = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return OpResult(False, f"file not found: {file_path or 'SKILL.md'}")
    except OSError as exc:
        return OpResult(False, f"read failed: {exc}")

    UsageStore(skills_root).bump_view(name)
    return OpResult(
        True,
        f"Read {file_path or 'SKILL.md'} for skill '{name}'.",
        {"name": name, "content": content, "path": str(target)},
    )


def create_skill(
    skills_root: Path,
    name: str,
    content: str,
    category: str | None = None,
) -> OpResult:
    """Create a new skill. Refuses on name collision (active OR archived)."""
    name_err = validate_skill_name(name)
    if name_err:
        return OpResult(False, name_err)
    err = validate_skill_md(content)
    if err:
        return OpResult(False, err)
    parsed = parse_skill_md(content)
    if parsed is None or parsed.name != name:
        # The frontmatter's name field must match the argument so we
        # don't end up with a directory called `foo` containing a
        # SKILL.md that says it's `bar`.
        return OpResult(
            False,
            f"frontmatter name must match the skill name '{name}'.",
        )

    if _find_skill_dir(skills_root, name) is not None:
        return OpResult(False, f"a skill named '{name}' already exists.")
    archived = skills_root / ARCHIVE_DIR_NAME / name
    if archived.exists():
        return OpResult(
            False,
            f"a skill named '{name}' is in the archive. Restore it via "
            f"/curator restore {name} or pick a different name.",
        )

    if category:
        cat_err = validate_skill_name(category)
        if cat_err:
            return OpResult(False, f"category: {cat_err}")
        skill_dir = skills_root / category / name
    else:
        skill_dir = skills_root / name

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return OpResult(False, f"directory {skill_dir} already exists.")

    skill_md = skill_dir / "SKILL.md"
    try:
        _atomic_write_text(skill_md, content)
    except OSError as exc:
        # Roll back the dir we just made if write failed.
        try:
            skill_dir.rmdir()
        except OSError:
            pass
        return OpResult(False, f"write failed: {exc}")

    UsageStore(skills_root).initialize(name)
    return OpResult(True, f"Created skill '{name}'.", {"path": str(skill_md)})


def edit_skill(skills_root: Path, name: str, content: str) -> OpResult:
    """Full SKILL.md rewrite. Validates the new content first."""
    name_err = validate_skill_name(name)
    if name_err:
        return OpResult(False, name_err)
    if PinStore(skills_root).is_pinned(name):
        return OpResult(
            False,
            f"Skill '{name}' is pinned. Ask the user to /unpin {name} first.",
        )
    err = validate_skill_md(content)
    if err:
        return OpResult(False, err)
    parsed = parse_skill_md(content)
    if parsed is None or parsed.name != name:
        return OpResult(
            False, f"frontmatter name must remain '{name}' across edits."
        )
    skill_dir = _find_skill_dir(skills_root, name)
    if skill_dir is None:
        return OpResult(False, f"No skill named '{name}'.")

    skill_md = skill_dir / "SKILL.md"
    try:
        _atomic_write_text(skill_md, content)
    except OSError as exc:
        return OpResult(False, f"write failed: {exc}")
    UsageStore(skills_root).bump_patch(name)
    return OpResult(True, f"Edited skill '{name}'.", {"path": str(skill_md)})


def patch_skill(
    skills_root: Path,
    name: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
) -> OpResult:
    """Targeted edit. Default requires unique match; ``replace_all``
    relaxes that. Whitespace-tolerant: the matcher first tries exact
    text, then a whitespace-collapsed pass to forgive indent drift."""
    name_err = validate_skill_name(name)
    if name_err:
        return OpResult(False, name_err)
    if PinStore(skills_root).is_pinned(name):
        return OpResult(
            False,
            f"Skill '{name}' is pinned. Ask the user to /unpin {name} first.",
        )
    if not old_string:
        return OpResult(False, "old_string is required")
    skill_dir = _find_skill_dir(skills_root, name)
    if skill_dir is None:
        return OpResult(False, f"No skill named '{name}'.")
    skill_md = skill_dir / "SKILL.md"
    try:
        original = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return OpResult(False, f"read failed: {exc}")

    new_content, replacements = _fuzzy_replace(
        original, old_string, new_string, replace_all=replace_all
    )
    if replacements == 0:
        return OpResult(
            False, f"old_string not found in skill '{name}'."
        )
    if replacements > 1 and not replace_all:
        return OpResult(
            False,
            f"old_string matched {replacements} times in '{name}'. "
            f"Pass --replace-all to apply to every match, or expand "
            f"old_string for uniqueness.",
        )
    err = validate_skill_md(new_content)
    if err:
        return OpResult(False, f"patched content invalid: {err}")
    parsed = parse_skill_md(new_content)
    if parsed is None or parsed.name != name:
        return OpResult(
            False,
            f"patch broke frontmatter — name must remain '{name}'.",
        )
    try:
        _atomic_write_text(skill_md, new_content)
    except OSError as exc:
        return OpResult(False, f"write failed: {exc}")
    UsageStore(skills_root).bump_patch(name)
    return OpResult(
        True,
        f"Patched skill '{name}' ({replacements} replacement"
        + ("s" if replacements != 1 else "")
        + ").",
        {"replacements": replacements, "path": str(skill_md)},
    )


def delete_skill(skills_root: Path, name: str) -> OpResult:
    """Hard delete. Refused for pinned skills."""
    name_err = validate_skill_name(name)
    if name_err:
        return OpResult(False, name_err)
    if PinStore(skills_root).is_pinned(name):
        return OpResult(
            False,
            f"Skill '{name}' is pinned. Ask the user to /unpin {name} first.",
        )
    skill_dir = _find_skill_dir(skills_root, name)
    if skill_dir is None:
        return OpResult(False, f"No skill named '{name}'.")
    parent = skill_dir.parent
    try:
        shutil.rmtree(skill_dir)
    except OSError as exc:
        return OpResult(False, f"delete failed: {exc}")
    # Cleanup empty category dir, but never the skills root itself.
    try:
        if parent != skills_root and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
    UsageStore(skills_root).forget(name)
    return OpResult(True, f"Deleted skill '{name}'.")


def write_supporting_file(
    skills_root: Path, name: str, file_path: str, file_content: str
) -> OpResult:
    """Add or overwrite a file under references/templates/scripts."""
    name_err = validate_skill_name(name)
    if name_err:
        return OpResult(False, name_err)
    if PinStore(skills_root).is_pinned(name):
        return OpResult(
            False,
            f"Skill '{name}' is pinned. Ask the user to /unpin {name} first.",
        )
    skill_dir = _find_skill_dir(skills_root, name)
    if skill_dir is None:
        return OpResult(False, f"No skill named '{name}'.")
    rel = Path(file_path)
    if rel.is_absolute() or ".." in rel.parts:
        return OpResult(False, "file_path must be a relative path.")
    if not rel.parts or rel.parts[0] not in ALLOWED_SUBDIRS:
        return OpResult(
            False, f"file_path must live under one of {sorted(ALLOWED_SUBDIRS)}."
        )
    encoded = file_content.encode("utf-8")
    if len(encoded) > MAX_SUPPORTING_FILE_BYTES:
        return OpResult(
            False,
            f"supporting file exceeds {MAX_SUPPORTING_FILE_BYTES} bytes.",
        )
    target = skill_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _atomic_write_text(target, file_content)
    except OSError as exc:
        return OpResult(False, f"write failed: {exc}")
    UsageStore(skills_root).bump_patch(name)
    return OpResult(
        True,
        f"Wrote {file_path} for skill '{name}'.",
        {"path": str(target)},
    )


def remove_supporting_file(
    skills_root: Path, name: str, file_path: str
) -> OpResult:
    """Remove a file under references/templates/scripts."""
    name_err = validate_skill_name(name)
    if name_err:
        return OpResult(False, name_err)
    if PinStore(skills_root).is_pinned(name):
        return OpResult(
            False,
            f"Skill '{name}' is pinned. Ask the user to /unpin {name} first.",
        )
    skill_dir = _find_skill_dir(skills_root, name)
    if skill_dir is None:
        return OpResult(False, f"No skill named '{name}'.")
    rel = Path(file_path)
    if rel.is_absolute() or ".." in rel.parts:
        return OpResult(False, "file_path must be a relative path.")
    if not rel.parts or rel.parts[0] not in ALLOWED_SUBDIRS:
        return OpResult(
            False, f"file_path must live under one of {sorted(ALLOWED_SUBDIRS)}."
        )
    target = skill_dir / rel
    if not target.exists():
        return OpResult(False, f"file not found: {file_path}")
    try:
        target.unlink()
    except OSError as exc:
        return OpResult(False, f"remove failed: {exc}")
    UsageStore(skills_root).bump_patch(name)
    return OpResult(True, f"Removed {file_path} from skill '{name}'.")


def archive_skill(skills_root: Path, name: str) -> OpResult:
    """Move a skill's directory into ``.archive/``. Refused for pinned."""
    name_err = validate_skill_name(name)
    if name_err:
        return OpResult(False, name_err)
    if PinStore(skills_root).is_pinned(name):
        return OpResult(False, f"Skill '{name}' is pinned; refusing to archive.")
    skill_dir = _find_skill_dir(skills_root, name)
    if skill_dir is None:
        return OpResult(False, f"No skill named '{name}'.")
    archive_root = skills_root / ARCHIVE_DIR_NAME
    archive_root.mkdir(parents=True, exist_ok=True)
    dest = archive_root / skill_dir.name
    if dest.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        dest = archive_root / f"{skill_dir.name}-{ts}"
    try:
        skill_dir.rename(dest)
    except OSError:
        try:
            shutil.move(str(skill_dir), str(dest))
        except OSError as exc:
            return OpResult(False, f"archive failed: {exc}")
    UsageStore(skills_root).set_state(name, STATE_ARCHIVED)
    parent = skill_dir.parent
    try:
        if parent != skills_root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
    return OpResult(True, f"Archived skill '{name}' to {dest}.", {"dest": str(dest)})


def restore_skill(skills_root: Path, name: str) -> OpResult:
    """Move a skill from ``.archive/`` back to the active tree."""
    name_err = validate_skill_name(name)
    if name_err:
        return OpResult(False, name_err)
    archive_root = skills_root / ARCHIVE_DIR_NAME
    if not archive_root.exists():
        return OpResult(False, "no archive exists yet.")
    # Match the archived dir by reading its SKILL.md frontmatter — we
    # can't trust the directory name because we may have suffixed it
    # with a timestamp during archive collision handling.
    candidate: Path | None = None
    for entry in archive_root.iterdir():
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = parse_skill_md(content)
        if meta is not None and meta.name == name:
            candidate = entry
            break
    if candidate is None:
        return OpResult(False, f"no archived skill named '{name}'.")
    if _find_skill_dir(skills_root, name) is not None:
        return OpResult(
            False, f"a skill named '{name}' already exists in the active tree."
        )
    dest = skills_root / candidate.name
    if dest.exists():
        return OpResult(False, f"destination {dest} already exists.")
    try:
        candidate.rename(dest)
    except OSError:
        try:
            shutil.move(str(candidate), str(dest))
        except OSError as exc:
            return OpResult(False, f"restore failed: {exc}")
    UsageStore(skills_root).set_state(name, STATE_ACTIVE)
    return OpResult(True, f"Restored skill '{name}'.", {"dest": str(dest)})


# --------------------------------------------------------------------
# Reporting helpers used by the curator
# --------------------------------------------------------------------


@dataclass(frozen=True)
class SkillReport:
    """Combined view of disk + telemetry, used by the curator."""

    name: str
    description: str
    state: str
    last_used_at: str | None
    created_at: str | None
    pinned: bool


def list_active_reports(skills_root: Path) -> list[SkillReport]:
    """One report per skill currently in the active tree."""
    pins = set(PinStore(skills_root).list())
    usage = UsageStore(skills_root).load()
    out: list[SkillReport] = []
    for meta in discover_skills(skills_root):
        rec = usage.get(meta.name) or _empty_usage_record()
        out.append(
            SkillReport(
                name=meta.name,
                description=meta.description,
                state=str(rec.get("state") or STATE_ACTIVE),
                last_used_at=rec.get("last_used_at"),
                created_at=rec.get("created_at"),
                pinned=meta.name in pins,
            )
        )
    return out


def archived_skill_names(skills_root: Path) -> list[str]:
    archive_root = skills_root / ARCHIVE_DIR_NAME
    if not archive_root.exists():
        return []
    names: list[str] = []
    for entry in archive_root.iterdir():
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = parse_skill_md(content)
        if meta is not None:
            names.append(meta.name)
    return sorted(names)


# --------------------------------------------------------------------
# Internals: fuzzy patch matcher and atomic text write
# --------------------------------------------------------------------


def _atomic_write_text(path: Path, content: str) -> None:
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


def _fuzzy_replace(
    text: str, old: str, new: str, *, replace_all: bool
) -> tuple[str, int]:
    """Replace ``old`` with ``new`` in ``text``. First tries exact match,
    then a whitespace-tolerant pass that collapses runs of whitespace
    in both the haystack and needle. Returns (new_text, count)."""
    if old in text:
        if replace_all:
            count = text.count(old)
            return text.replace(old, new), count
        # Single replacement
        return text.replace(old, new, 1), text.count(old)

    # Whitespace-tolerant fallback. We collapse runs of whitespace to
    # a single space in both sides for matching, but locate the actual
    # span in the original text to preserve the surrounding formatting.
    pattern = re.escape(old.strip())
    pattern = re.sub(r"\\\s+", r"\\s+", pattern)
    matches = list(re.finditer(pattern, text))
    if not matches:
        return text, 0
    if len(matches) > 1 and not replace_all:
        return text, len(matches)
    if replace_all:
        return re.sub(pattern, lambda _: new, text), len(matches)
    span = matches[0]
    return text[: span.start()] + new + text[span.end() :], 1
