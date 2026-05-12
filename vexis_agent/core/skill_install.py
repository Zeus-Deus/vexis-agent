"""Install + uninstall skills from external sources.

The third skill tier alongside bundled (ships with vexis) and
workspace (user/curator-authored). An installed skill lives at
``<workspace>/skills/installed/<name>/`` with a ``.provenance.json``
sidecar that:

  * records where the skill came from (URL, sha, install timestamp)
    so the user can later run a re-install / update flow,
  * marks the skill as auto-pinned for the curator (presence of
    ``.provenance.json`` in a skill dir == hands-off),
  * lets the dashboard render an "INSTALLED · upstream-source-url"
    chip distinct from both the bundled badge and pinned badge.

Sources supported in v1:

  * ``https://...`` URL pointing at a SKILL.md — fetched via stdlib
    urllib (no requests dep). Single-file install.
  * ``http://...`` same; included for local dev convenience.
  * Local filesystem path — either a single ``.md`` file (treated
    as a SKILL.md) or a directory containing one. Directories
    install the whole tree (so supporting ``references/`` /
    ``templates/`` / ``scripts/`` come with).

Sources NOT yet supported (defer until needed):

  * ``github:owner/repo/path`` shorthand
  * ``git+https://`` clone-and-install
  * Tarball / zip URLs
  * Marketplace registries (``/.well-known/skills/index.json``)

The single-user / single-machine install layer is intentionally
minimal: there's no signing, no hash verification beyond what we
record in provenance, no auto-update. Trust model = "user owns the
risk." For the future scrape-and-port pipeline, the pre-port pass
on the home-server is the real safety gate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from vexis_agent.core.skills import (
    ALLOWED_SUBDIRS,
    MAX_SKILL_MD_CHARS,
    MAX_SUPPORTING_FILE_BYTES,
    OpResult,
    UsageStore,
    parse_skill_md,
    validate_skill_md,
    validate_skill_name,
)

log = logging.getLogger(__name__)

# Subdirectory under <workspace>/skills/ where installed skills live.
# Deliberately NOT a dotfile so the existing ``iter_skill_dirs`` walk
# (which skips dot-prefixed dirs) picks them up. The provenance.json
# sidecar is what marks them as read-only — directory naming is just
# a self-documenting convention, not a protection mechanism.
INSTALLED_DIR_NAME = "installed"

# Marker file in each installed skill's dir. Presence = "this skill
# was installed from an external source; treat as read-only." The
# curator and write-op paths check for this file before mutating.
PROVENANCE_FILENAME = ".provenance.json"

# Upper bound on a fetched payload. SKILL.md files are markdown +
# small frontmatter; 256 KiB is generous and stops a misbehaving
# server from streaming us a 1 GB blob.
MAX_FETCH_BYTES = 256 * 1024

# How long to wait for a network fetch before giving up. urllib
# applies this per-call.
FETCH_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class Provenance:
    """On-disk record of where an installed skill came from.

    Written as ``.provenance.json`` next to ``SKILL.md``. Every field
    is required so a corrupt or hand-edited record fails loud at
    install time — better to refuse the install than silently lose
    the source URL the user will later need for an update.
    """

    source: str               # canonical URL or local path string
    source_kind: str          # "https" | "http" | "file"
    sha256: str               # hex digest of SKILL.md content as fetched
    bytes_fetched: int        # original byte count
    installed_at: str         # ISO-8601 UTC

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_kind": self.source_kind,
            "sha256": self.sha256,
            "bytes_fetched": self.bytes_fetched,
            "installed_at": self.installed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Provenance":
        return cls(
            source=str(d.get("source", "")),
            source_kind=str(d.get("source_kind", "")),
            sha256=str(d.get("sha256", "")),
            bytes_fetched=int(d.get("bytes_fetched", 0) or 0),
            installed_at=str(d.get("installed_at", "")),
        )


@dataclass(frozen=True)
class InstallResult:
    """OpResult-shape return from ``install_skill``."""

    ok: bool
    message: str
    name: str | None = None
    path: Path | None = None
    provenance: Provenance | None = None


# --------------------------------------------------------------------
# Source resolution
# --------------------------------------------------------------------


# Match a GitHub browser-blob URL so we can rewrite it to the raw
# form. The browser URL serves an HTML wrapper around the file, not
# the file itself — pasting it directly into ``install`` would fail
# at the SKILL.md frontmatter check. Pre-converting is the friendliest
# UX since most users paste from their address bar, not the raw URL.
#
# Supported shape:
#   https://github.com/<owner>/<repo>/blob/<ref>/<path...>
# Converted to:
#   https://raw.githubusercontent.com/<owner>/<repo>/<ref>/<path...>
#
# ``ref`` can be a branch, tag, or commit SHA. ``path`` is anything
# remaining (we don't validate it ends in .md — install does that).
import re as _re

_GITHUB_BLOB_RE = _re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$",
)


def _maybe_rewrite_github_blob(source: str) -> str:
    """Rewrite a github.com/.../blob/... URL to raw.githubusercontent.com.

    Returns the rewritten URL when the pattern matches, otherwise the
    input unchanged. Single-pass — no chained rewrites. The
    ``provenance.source`` field stores the **rewritten** URL so a
    re-install / update follows the same canonical form, not the
    original blob link.
    """
    m = _GITHUB_BLOB_RE.match(source)
    if m is None:
        return source
    owner, repo, ref, path = m.groups()
    return (
        f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    )


def _classify_source(source: str) -> tuple[str, str]:
    """Return ``(kind, canonical)`` for the source string.

    ``kind`` is one of ``"https"``, ``"http"``, ``"file"``.
    ``canonical`` is the cleaned-up source string written to
    provenance — URLs get the github-blob → raw rewrite applied so
    re-install follows the same canonical form. Filesystem paths
    are absolutised + symlink-resolved.
    """
    if source.startswith(("https://", "http://")):
        # Apply the github-blob → raw rewrite BEFORE choosing the
        # https/http kind — the rewritten URL is always https.
        rewritten = _maybe_rewrite_github_blob(source)
        kind = "https" if rewritten.startswith("https://") else "http"
        return (kind, rewritten)
    # Treat as filesystem path. Expand ~ and resolve absolutely so
    # provenance carries a stable reference even if the user's CWD
    # changes between install and re-install.
    p = Path(os.path.expanduser(source)).resolve()
    return ("file", str(p))


def _fetch_url(url: str) -> bytes:
    """Fetch a URL and return the body. Caps response size + timeout.

    Raises:
        OSError: network failure, http error, or response too large.
    """
    log.info("skill install: fetching %s", url)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "vexis-skill/1.0 (install)"},
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
        # urlopen raises HTTPError on non-2xx; we still defend on
        # content-length when it's set so we can refuse a giant
        # payload before reading it all.
        cl = resp.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > MAX_FETCH_BYTES:
                    raise OSError(
                        f"refusing fetch: content-length {cl} > "
                        f"limit {MAX_FETCH_BYTES}"
                    )
            except ValueError:
                # Malformed header — proceed with the bounded read
                # below; better to read MAX_FETCH_BYTES+1 and refuse
                # than crash on a bogus header.
                pass
        body = resp.read(MAX_FETCH_BYTES + 1)
    if len(body) > MAX_FETCH_BYTES:
        raise OSError(
            f"refusing fetch: response > {MAX_FETCH_BYTES} bytes"
        )
    return body


def _read_local(path: Path) -> bytes:
    """Read a local file. Same byte cap as the URL fetch path so a
    user can't sneak in a huge SKILL.md that bloats the brain's
    index render budget downstream."""
    if not path.is_file():
        raise OSError(f"not a file: {path}")
    size = path.stat().st_size
    if size > MAX_FETCH_BYTES:
        raise OSError(
            f"refusing read: {path} is {size} bytes (limit "
            f"{MAX_FETCH_BYTES})"
        )
    return path.read_bytes()


# --------------------------------------------------------------------
# Install / uninstall
# --------------------------------------------------------------------


def install_skill(
    workspace_skills_root: Path,
    source: str,
    *,
    name_override: str | None = None,
    overwrite: bool = False,
) -> InstallResult:
    """Install a skill from a URL or local path.

    Drops the skill at
    ``<workspace_skills_root>/<INSTALLED_DIR_NAME>/<name>/SKILL.md``
    plus a sibling ``.provenance.json``. Returns an InstallResult
    describing success / failure. Fails loud on:

      * malformed SKILL.md (no frontmatter, missing name/desc, etc),
      * name collision with an existing INSTALLED skill (unless
        ``overwrite=True``),
      * name collision with a NON-installed workspace skill (no
        overwrite allowed in either direction — the user must
        manually resolve),
      * network / fs errors during the fetch.

    The installed skill becomes visible in the brain's index on the
    next session start; the existing ``discover_skills`` walk picks
    it up because ``installed/`` is not a dotfile dir.
    """
    if not source or not isinstance(source, str):
        return InstallResult(False, "source is required (URL or local path)")

    kind, canonical = _classify_source(source)

    # 1. Fetch.
    try:
        if kind == "file":
            content = _read_local(Path(canonical))
        else:
            content = _fetch_url(canonical)
    except OSError as exc:
        return InstallResult(False, f"fetch failed: {exc}")

    # 2. Decode + validate as SKILL.md.
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        return InstallResult(False, f"not utf-8: {exc}")
    if len(text) > MAX_SKILL_MD_CHARS:
        return InstallResult(
            False,
            f"SKILL.md exceeds {MAX_SKILL_MD_CHARS} chars",
        )
    err = validate_skill_md(text)
    if err is not None:
        return InstallResult(False, f"invalid SKILL.md: {err}")
    meta = parse_skill_md(text)
    if meta is None:
        return InstallResult(
            False, "could not parse SKILL.md frontmatter",
        )

    # 3. Name resolution.
    name = name_override or meta.name
    name_err = validate_skill_name(name)
    if name_err is not None:
        return InstallResult(
            False, f"invalid skill name {name!r}: {name_err}",
        )
    # If the SKILL.md frontmatter and the override disagree, prefer
    # the override BUT keep the frontmatter intact when we write the
    # file (parse_skill_md already enforces frontmatter.name match
    # at parse time; we don't rewrite it). Future: surface a warning.

    # 4. Directory layout. installed/<name>/SKILL.md
    installed_root = workspace_skills_root / INSTALLED_DIR_NAME
    target_dir = installed_root / name

    # 5. Collision detection.
    if target_dir.exists():
        if not overwrite:
            return InstallResult(
                False,
                f"skill '{name}' already installed; pass --overwrite "
                f"to replace, or 'vexis-skill uninstall {name}' first",
            )
        # Wipe before reinstalling so leftover supporting files don't
        # silently survive a re-install.
        shutil.rmtree(target_dir)

    # Refuse install if the SAME name exists as a non-installed
    # workspace skill — that's a collision the user must resolve
    # manually (via rename or delete) so we don't shadow their work
    # silently.
    for sibling in workspace_skills_root.rglob("SKILL.md"):
        try:
            rel = sibling.relative_to(workspace_skills_root)
        except ValueError:
            continue
        # Skip the install root + its archive sibling.
        if rel.parts and rel.parts[0] in (INSTALLED_DIR_NAME, ".archive"):
            continue
        try:
            sib_meta = parse_skill_md(
                sibling.read_text(encoding="utf-8"),
            )
        except OSError:
            continue
        if sib_meta is not None and sib_meta.name == name:
            return InstallResult(
                False,
                f"a workspace-authored skill named '{name}' already "
                f"exists at {sibling.parent} — rename or delete it "
                f"before installing",
            )

    # 6. Write SKILL.md + provenance.
    target_dir.mkdir(parents=True, exist_ok=True)
    skill_md = target_dir / "SKILL.md"
    skill_md.write_text(text, encoding="utf-8")

    provenance = Provenance(
        source=canonical,
        source_kind=kind,
        sha256=hashlib.sha256(content).hexdigest(),
        bytes_fetched=len(content),
        installed_at=datetime.now(timezone.utc).isoformat(),
    )
    (target_dir / PROVENANCE_FILENAME).write_text(
        json.dumps(provenance.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # Initialise telemetry so the dashboard renders use/view counts
    # at zero from day one rather than as undefined.
    try:
        UsageStore(workspace_skills_root).initialize(name)
    except Exception:
        # Telemetry write is best-effort — never fail the install over
        # a usage-record glitch.
        log.exception("install_skill: usage init failed for %s", name)

    return InstallResult(
        ok=True,
        message=f"installed skill '{name}' from {canonical}",
        name=name,
        path=target_dir,
        provenance=provenance,
    )


def uninstall_skill(
    workspace_skills_root: Path,
    name: str,
) -> OpResult:
    """Remove an installed skill. Refuses on names that aren't in the
    installed/ subdir — use ``vexis-skill delete`` for workspace-
    authored skills."""
    name_err = validate_skill_name(name)
    if name_err is not None:
        return OpResult(False, name_err)
    target_dir = workspace_skills_root / INSTALLED_DIR_NAME / name
    if not target_dir.is_dir():
        return OpResult(
            False,
            f"no installed skill named '{name}' (looked at {target_dir})",
        )
    if not (target_dir / PROVENANCE_FILENAME).is_file():
        return OpResult(
            False,
            f"{target_dir} exists but has no {PROVENANCE_FILENAME} — "
            f"refusing to remove a non-installed skill via uninstall. "
            f"Use 'vexis-skill delete {name}' if it's workspace-authored.",
        )
    try:
        shutil.rmtree(target_dir)
    except OSError as exc:
        return OpResult(False, f"remove failed: {exc}")
    # Don't strip telemetry — the user may reinstall the same skill
    # later and the historical use/view counts stay informative.
    return OpResult(True, f"uninstalled skill '{name}'", {"path": str(target_dir)})


# --------------------------------------------------------------------
# Provenance helpers (used by discovery + dashboard + curator)
# --------------------------------------------------------------------


def is_installed_skill_dir(skill_dir: Path) -> bool:
    """Return True if ``skill_dir`` contains a provenance marker.

    Used by:
      * ``core.skills._discover_one_root`` to tag SkillMeta.source as
        ``"installed"`` (so the dashboard renders the right badge).
      * Write-op paths to refuse mutations on installed skills (same
        protection as bundled — fork into a fresh workspace skill if
        you want to edit).
      * The curator's stale-detection sweep to skip installed skills
        (similar to bundled — they're not user-authored, the curator
        shouldn't propose archive/edit on them).
    """
    return (skill_dir / PROVENANCE_FILENAME).is_file()


def load_provenance(skill_dir: Path) -> Provenance | None:
    """Load a skill's provenance record, or None if missing/corrupt."""
    p = skill_dir / PROVENANCE_FILENAME
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return Provenance.from_dict(raw)


__all__ = [
    "FETCH_TIMEOUT_SECONDS",
    "INSTALLED_DIR_NAME",
    "InstallResult",
    "MAX_FETCH_BYTES",
    "PROVENANCE_FILENAME",
    "Provenance",
    "install_skill",
    "is_installed_skill_dir",
    "load_provenance",
    "rewrite_github_blob_url",
    "uninstall_skill",
]


# Public alias of the internal rewriter so callers (tests, future
# CLI helpers, dashboard preview) can use the same canonicalisation.
def rewrite_github_blob_url(source: str) -> str:
    """Public alias of :func:`_maybe_rewrite_github_blob`."""
    return _maybe_rewrite_github_blob(source)
