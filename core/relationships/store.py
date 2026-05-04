"""Parser, serializer, and file ops for RELATIONSHIPS.md and
RELATIONSHIPS-SHADOW.md.

File shape (research doc §3.2 + §3.4):

    # RELATIONSHIPS.md

    > <intro paragraph, kept verbatim across rewrites>

    ## Sarah (work)
    ```yaml
    slug: sarah-work
    display_name: Sarah Chen
    relationship: coworker
    qualifier: work
    last_confirmed: 2026-05-04
    source_session: 0a1b2c3d
    ```
    - [confirmed 2026-05-04 sess:0a1b2c3d] Tech lead on the Vexis
      rollout team.
    - [confirmed 2026-04-18 sess:b5fa9911] Prefers async standups
      over live ones.

    ## Sarah (sister)
    ```yaml
    slug: sarah-sister
    ...
    ```
    - [confirmed 2026-05-04 sess:0a1b2c3d] Allergic to peanuts.

The shadow file uses the SAME shape with two extra YAML fields
(``pending: true`` and ``staged_at``) and ``[staged …]``
provenance pins instead of ``[confirmed …]``. Same parser,
different serialization paths.

Token-gated writes go through ``RelationshipsStore.add`` which
delegates threat-scanning + appending. Plain ``MemoryStore.add(
target="relationships", …)`` is REFUSED at the entry point —
relationships writes require an explicit ``ConsentToken`` and
the wrong-API-surface PermissionError is the signal that the
caller should be using ``RelationshipsStore`` instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Literal

import yaml

FileKind = Literal["live", "shadow"]

LIVE_FILE_NAME = "RELATIONSHIPS.md"
SHADOW_FILE_NAME = "RELATIONSHIPS-SHADOW.md"
ARCHIVE_FILE_NAME = "RELATIONSHIPS-ARCHIVE.md"

LIVE_HEADER = "# RELATIONSHIPS.md\n"
LIVE_INTRO = (
    "> Personal facts about specific people the user has explicitly\n"
    "> consented to remember. Edit or delete via the dashboard\n"
    "> Relationships panel, or via Telegram: \"forget …\" / \"update\n"
    "> what you know about …\".\n"
)

SHADOW_HEADER = "# RELATIONSHIPS-SHADOW.md\n"
SHADOW_INTRO = (
    "> Pending relationships entries staged by the trigger\n"
    "> detector. Promoted to RELATIONSHIPS.md on the next curator\n"
    "> tick after coherence + sensitive-pattern checks pass.\n"
)


# --------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    """One bulleted fact under a person H2.

    ``confirmed_date`` is ISO ``YYYY-MM-DD``. ``source_session_short``
    is the first 8 chars of the source session UUID (for human
    readability — full UUID lives in the H2's YAML frontmatter).

    ``staged`` distinguishes shadow-file pins (``[staged …]``) from
    live-file pins (``[confirmed …]``). ``superseded_by_date`` /
    ``superseded_by_session`` mark a fact as historical (Day 3
    SUPERSEDE feature; reserved for forward-compat now).
    """

    text: str
    confirmed_date: str
    source_session_short: str
    staged: bool = False
    superseded_by_date: str | None = None
    superseded_by_session: str | None = None

    def render(self) -> str:
        verb = "staged" if self.staged else "confirmed"
        prefix = f"[{verb} {self.confirmed_date} sess:{self.source_session_short}]"
        if self.superseded_by_date and self.superseded_by_session:
            sup = (
                f"[superseded {self.superseded_by_date} "
                f"by sess:{self.superseded_by_session}] "
            )
        else:
            sup = ""
        return f"- {sup}{prefix} {self.text}"


@dataclass(frozen=True)
class Person:
    """One H2 section in a relationships file.

    ``coherence_block`` is a sticky shadow flag set when promotion
    has been blocked by a coherence-related condition. Values:

      - ``"missing_transcript"``: source turn JSONL not findable
        (e.g. synthetic Telegram session_uuid in Day 2 — full
        brain-session-UUID handoff is Day 3 scope). Entry stays
        in shadow; promotion is deterministically refused without
        invoking the judge subprocess.
      - ``"incoherent"``: judge returned INCOHERENT (reserved for
        the case where the entry stays in shadow rather than being
        dropped — Day 2 currently leaves this implicit and just
        records a drop event, but the field is here for forward-
        compat with the dashboard's "blocked-pending review" view).
      - ``None``: no block.
    """

    slug: str
    display_name: str
    relationship: str
    qualifier: str | None
    last_confirmed: str
    source_session: str
    facts: tuple[Fact, ...] = field(default_factory=tuple)
    pending: bool = False
    staged_at: str | None = None
    source_turn_index: int | None = None
    coherence_block: str | None = None

    def heading(self) -> str:
        if self.qualifier:
            return f"## {self.display_name} ({self.qualifier})"
        return f"## {self.display_name}"

    def render(self) -> str:
        yaml_payload: dict = {
            "slug": self.slug,
            "display_name": self.display_name,
            "relationship": self.relationship,
            "qualifier": self.qualifier,
            "last_confirmed": self.last_confirmed,
            "source_session": self.source_session,
        }
        if self.pending:
            yaml_payload["pending"] = True
            if self.staged_at:
                yaml_payload["staged_at"] = self.staged_at
            if self.source_turn_index is not None:
                yaml_payload["source_turn_index"] = self.source_turn_index
            if self.coherence_block:
                yaml_payload["coherence_block"] = self.coherence_block
        yaml_text = yaml.safe_dump(
            yaml_payload, sort_keys=False, allow_unicode=True
        ).rstrip("\n")
        lines = [
            self.heading(),
            "```yaml",
            yaml_text,
            "```",
        ]
        for fact in self.facts:
            lines.append(fact.render())
        return "\n".join(lines)


# --------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------


_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_FENCE_OPEN_RE = re.compile(r"^```(?:yaml)?\s*$")
_FENCE_CLOSE_RE = re.compile(r"^```\s*$")
_FACT_RE = re.compile(
    r"^-\s+"
    r"(?:\[superseded\s+(?P<sup_date>\d{4}-\d{2}-\d{2})\s+"
    r"by\s+sess:(?P<sup_sess>[A-Za-z0-9_-]+)\]\s+)?"
    r"\[(?P<verb>confirmed|staged)\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
    r"sess:(?P<sess>[A-Za-z0-9_-]+)\]\s+"
    r"(?P<text>.+)$"
)


def parse_relationships_file(text: str) -> list[Person]:
    """Parse a RELATIONSHIPS.md or RELATIONSHIPS-SHADOW.md body
    into an ordered list of Person dataclasses.

    Tolerant: silently skips H2 sections whose YAML block is
    malformed (logs would be added at integration time). Returns
    empty list on empty / non-existent file content.
    """
    if not text:
        return []
    people: list[Person] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = _HEADING_RE.match(lines[i])
        if not m:
            i += 1
            continue
        heading = m.group(1)
        i += 1
        # Expect a fenced YAML block immediately after the heading,
        # possibly preceded by blank lines.
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines) or not _FENCE_OPEN_RE.match(lines[i]):
            continue
        i += 1
        yaml_lines: list[str] = []
        while i < len(lines) and not _FENCE_CLOSE_RE.match(lines[i]):
            yaml_lines.append(lines[i])
            i += 1
        if i >= len(lines):
            break
        i += 1  # consume closing fence
        try:
            payload = yaml.safe_load("\n".join(yaml_lines)) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(payload, dict):
            continue
        slug = str(payload.get("slug") or "").strip()
        display_name = str(payload.get("display_name") or "").strip()
        relationship = str(payload.get("relationship") or "").strip()
        if not slug or not display_name:
            continue
        qualifier_raw = payload.get("qualifier")
        qualifier = (
            str(qualifier_raw).strip()
            if qualifier_raw not in (None, "", "null")
            else None
        )
        # Collect facts until next H2 or end of file.
        facts: list[Fact] = []
        while i < len(lines) and not _HEADING_RE.match(lines[i]):
            fm = _FACT_RE.match(lines[i].strip())
            if fm:
                facts.append(
                    Fact(
                        text=fm.group("text"),
                        confirmed_date=fm.group("date"),
                        source_session_short=fm.group("sess"),
                        staged=(fm.group("verb") == "staged"),
                        superseded_by_date=fm.group("sup_date"),
                        superseded_by_session=fm.group("sup_sess"),
                    )
                )
            i += 1
        # Heading is informational only — slug is canonical. Heading
        # text mismatch is tolerated (mechanical rewrites may fall
        # behind YAML edits briefly).
        del heading
        people.append(
            Person(
                slug=slug,
                display_name=display_name,
                relationship=relationship,
                qualifier=qualifier,
                last_confirmed=str(payload.get("last_confirmed") or "").strip(),
                source_session=str(payload.get("source_session") or "").strip(),
                facts=tuple(facts),
                pending=bool(payload.get("pending") or False),
                staged_at=(
                    str(payload.get("staged_at")).strip()
                    if payload.get("staged_at") not in (None, "")
                    else None
                ),
                source_turn_index=(
                    int(payload.get("source_turn_index"))
                    if isinstance(payload.get("source_turn_index"), int)
                    else None
                ),
                coherence_block=(
                    str(payload.get("coherence_block")).strip()
                    if payload.get("coherence_block") not in (None, "")
                    else None
                ),
            )
        )
    return people


def serialize_relationships_file(
    people: Iterable[Person], *, kind: FileKind
) -> str:
    """Render people back to a markdown file body."""
    if kind == "live":
        header, intro = LIVE_HEADER, LIVE_INTRO
    else:
        header, intro = SHADOW_HEADER, SHADOW_INTRO
    parts = [header, "", intro]
    for person in people:
        parts.append("")
        parts.append(person.render())
    return "\n".join(parts).rstrip() + "\n"


# --------------------------------------------------------------------
# File-level operations + RelationshipsStore
# --------------------------------------------------------------------


def relationships_live_path(workspace: Path) -> Path:
    return workspace / LIVE_FILE_NAME


def relationships_shadow_path(workspace: Path) -> Path:
    return workspace / SHADOW_FILE_NAME


def relationships_archive_path(workspace: Path) -> Path:
    return workspace / ARCHIVE_FILE_NAME


def _read_people(path: Path) -> list[Person]:
    if not path.exists():
        return []
    return parse_relationships_file(path.read_text(encoding="utf-8"))


def _write_people(path: Path, people: list[Person], *, kind: FileKind) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_relationships_file(people, kind=kind), encoding="utf-8")


# --------------------------------------------------------------------
# Archive file — RELATIONSHIPS-ARCHIVE.md
# --------------------------------------------------------------------
#
# 3a is append-only on the archive. Each removed Person becomes a
# block headed by a `## REMOVED <ISO-date>` separator immediately
# preceding the original H2. Restore (3b) will read these back.
#
# Layout:
#
#     # RELATIONSHIPS-ARCHIVE.md
#
#     > Removed Person blocks. Each entry has a `## REMOVED <date>`
#     > marker preceding the original H2 + YAML + facts.
#
#     ## REMOVED 2026-05-04
#     ## Sarah (work)
#     ```yaml
#     slug: sarah-work
#     ...
#     ```
#     - [confirmed 2026-04-18 sess:b5fa9911] Tech lead.
#
# `## REMOVED ...` and `## <Name>` are both H2; the parser
# distinguishes by prefix. We render archive entries by hand
# (rather than re-using ``serialize_relationships_file``) because
# 3a needs the REMOVED separator and the block is the verbatim
# Person markdown — same renderer the live file uses.

ARCHIVE_HEADER = "# RELATIONSHIPS-ARCHIVE.md\n"
ARCHIVE_INTRO = (
    "> Removed Person blocks. Each entry is preceded by a\n"
    "> `## REMOVED <ISO-date>` marker; the block below it is the\n"
    "> verbatim H2 + YAML + facts that lived in RELATIONSHIPS.md.\n"
    "> 3b's `/relationships restore <slug>` reads from this file.\n"
)


def _initialize_archive_if_needed(path: Path) -> None:
    """Create the archive file with header+intro if it doesn't exist.

    Idempotent: if the file already exists with any content, leaves
    it alone. The header lines are stable so future restore code
    can skip them.
    """
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ARCHIVE_HEADER + "\n" + ARCHIVE_INTRO, encoding="utf-8")


def append_archive_block(
    path: Path, *, person: Person, removed_date: str
) -> None:
    """Append a REMOVED Person block to the archive file.

    Writes via ``<path>.tmp`` + atomic rename so a crash mid-write
    leaves either the previous archive or the new one, never a
    truncated mix. Caller is the synchronous DELETE flow in
    ``RelationshipsStore.delete_live``.
    """
    _append_archive_block_raw(
        path,
        block=f"## REMOVED {removed_date}\n{person.render()}\n",
    )


def _append_archive_block_raw(path: Path, *, block: str) -> None:
    """Append a pre-rendered block to the archive file.

    The block is responsible for its own H2 header (``## REMOVED ...``,
    ``## SUPERSEDED ...``, ``## DISAMBIGUATED ...``) and trailing
    newline. We init the file if missing, then atomic-write tmp +
    rename so an interrupted append leaves the previous archive
    intact.
    """
    _initialize_archive_if_needed(path)
    body = path.read_text(encoding="utf-8")
    if not body.endswith("\n"):
        body += "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body + "\n" + block, encoding="utf-8")
    tmp.replace(path)


def append_supersede_archive_block(
    path: Path,
    *,
    old_person: Person,
    superseded_date: str,
    new_session_short: str,
) -> None:
    """Append the OLD Person block to the archive under a
    ``## SUPERSEDED <date>`` separator, with each old fact carrying
    a ``[superseded YYYY-MM-DD by sess:<short>]`` provenance line.

    Caller is the synchronous SUPERSEDE flow. The new (replacement)
    facts are NOT written here — they go into the live file.
    """
    annotated_facts = tuple(
        replace(
            f,
            superseded_by_date=superseded_date,
            superseded_by_session=new_session_short,
        )
        for f in old_person.facts
    )
    annotated = replace(old_person, facts=annotated_facts)
    _append_archive_block_raw(
        path,
        block=f"## SUPERSEDED {superseded_date}\n{annotated.render()}\n",
    )


def append_disambiguation_archive_line(
    path: Path,
    *,
    disambiguated_date: str,
    old_slug: str,
    new_slug: str,
) -> None:
    """Append a ``## DISAMBIGUATED <date>`` block with a single
    provenance line per scoping doc §3.5: a previously-unqualified
    slug got back-edited to a qualified slug because a second
    person with the same first name appeared. The line lives in
    the archive ONLY — the live file just shows the renamed slug.
    """
    line = (
        f'- [disambiguated {disambiguated_date} from "{old_slug}" '
        f'to "{new_slug}"]'
    )
    _append_archive_block_raw(
        path,
        block=f"## DISAMBIGUATED {disambiguated_date}\n{line}\n",
    )


@dataclass(frozen=True)
class StoreResult:
    """Return type for RelationshipsStore mutations.

    ``ok`` is True on success, False on threat-scan refusal or
    other recoverable error. ``message`` is human-readable; never
    None. ``person_slug`` is the slug touched.
    """

    ok: bool
    message: str
    person_slug: str | None = None


@dataclass(frozen=True)
class AddLiveResult:
    """Return type for ``RelationshipsStore.add_live`` (v3c approval).

    Distinct from ``StoreResult`` because the approve path needs
    to surface structured failure reasons to the dashboard's
    409-modal flow (§5.1 patch). Fields beyond ``ok`` are populated
    only for the missing-qualifier collision case.
    """

    ok: bool
    person_slug: str | None = None
    merged: bool = False
    blocked_by: str | None = None  # "sensitive-pattern" / "missing_existing_qualifier" / "back-edit-failed"
    detail: str = ""
    # Populated when blocked_by="missing_existing_qualifier":
    existing_slug: str | None = None
    existing_facts: tuple[str, ...] = ()
    existing_qualifier_candidates: tuple[str, ...] = ()
    proposed_qualifier: str | None = None


def format_relationships_for_system_prompt(workspace: Path) -> str:
    """Render RELATIONSHIPS.md for inclusion in the brain's system
    prompt (v3c, Day 4a — patch to §2.2 of the research doc).

    Returns the live file's content verbatim when present, prefixed
    by a one-line section header so the brain knows what it's
    looking at. Empty string when the file is absent or empty —
    the caller drops empty blocks in ``build_system_prompt``.

    Reads ONLY the live file at ``relationships_live_path``. Does
    NOT read ``RELATIONSHIPS-SHADOW.md`` (in-flight v3b explicit
    stages), ``RELATIONSHIPS-ARCHIVE.md`` (deletion / supersede
    history), or ``.vexis/relationships-candidates.json`` (v3c
    silent queue). Brain isolation for the candidates file is
    enforced by ``tests/test_brain_isolation.py``.
    """
    live_path = relationships_live_path(workspace)
    if not live_path.exists():
        return ""
    try:
        body = live_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    body = body.strip()
    if not body:
        return ""
    return body


class RelationshipsStore:
    """Token-gated writes to RELATIONSHIPS.md / RELATIONSHIPS-SHADOW.md.

    Every write requires a verified ``ConsentToken`` passed
    explicitly to ``stage`` / ``promote``. The store ALSO re-runs
    the verify (defense in depth) — even though the curator caller
    typically verifies upstream, a future second caller (Day 3
    edit/delete, dashboard PATCH, any later code) cannot silently
    bypass the token check by skipping the curator. The verify is
    centralized in ``core.relationships.consent.verify_for_promotion``
    and raises ``ConsentError`` (a ``PermissionError`` subclass) on
    any violation; the store never catches.

    The store holds the file-shape invariants (slug uniqueness
    within a file, per-fact provenance pins, shadow vs live
    serialization differences, the coherence_block flag).
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._live_path = relationships_live_path(workspace)
        self._shadow_path = relationships_shadow_path(workspace)
        self._archive_path = relationships_archive_path(workspace)

    @property
    def archive_path(self) -> Path:
        return self._archive_path

    # ----- read paths -----

    def list_live(self) -> list[Person]:
        return _read_people(self._live_path)

    def list_shadow(self) -> list[Person]:
        return _read_people(self._shadow_path)

    def get_live(self, slug: str) -> Person | None:
        for p in self.list_live():
            if p.slug == slug:
                return p
        return None

    def get_shadow(self, slug: str) -> Person | None:
        for p in self.list_shadow():
            if p.slug == slug:
                return p
        return None

    def has_live_fact(self, slug: str, fact_id: str) -> bool:
        """Dedup check used by v3c silent extractor: return True iff
        ``slug`` exists in live AND ``fact_id`` matches one of its
        live facts. Same fact-id scheme as
        ``core.relationships.consent._fact_id`` (sha256 of stripped
        text, 16-hex truncation). Avoids re-queueing facts the user
        has already approved."""
        from core.relationships.consent import _fact_id
        match = self.get_live(slug)
        if match is None:
            return False
        return any(_fact_id(f.text) == fact_id for f in match.facts)

    # ----- write paths (token verification enforced HERE) -----

    def stage(self, person: Person, *, token) -> StoreResult:
        """Append/replace a person in the SHADOW file.

        Verifies the ConsentToken against the staged person's slug
        and fact set. Sets ``pending=True`` if not already.
        """
        # Lazy import to avoid a circular dep (consent imports
        # nothing from store, but a future change might).
        from core.relationships.consent import verify_for_promotion
        verify_for_promotion(
            token,
            person_slug=person.slug,
            facts=[f.text for f in person.facts],
        )
        if not person.pending:
            person = replace(person, pending=True)
        people = self.list_shadow()
        people = [p for p in people if p.slug != person.slug] + [person]
        _write_people(self._shadow_path, people, kind="shadow")
        return StoreResult(
            ok=True,
            message=f"staged {len(person.facts)} fact(s) for {person.slug}",
            person_slug=person.slug,
        )

    def update_shadow_flag(
        self, slug: str, *, coherence_block: str | None
    ) -> StoreResult:
        """Set or clear the ``coherence_block`` field on a shadow
        entry without touching its facts. NOT a write of new
        relationships content — token check does not apply (this
        only mutates a curator-owned diagnostic flag, never user
        data). Used by ``RelationshipsCurator._promote_one`` to
        record a missing-transcript or incoherent block."""
        people = self.list_shadow()
        match = next((p for p in people if p.slug == slug), None)
        if match is None:
            return StoreResult(
                ok=False,
                message=f"no shadow entry for slug={slug!r}",
                person_slug=slug,
            )
        updated = replace(match, coherence_block=coherence_block)
        people = [p for p in people if p.slug != slug] + [updated]
        _write_people(self._shadow_path, people, kind="shadow")
        return StoreResult(
            ok=True,
            message=f"updated coherence_block={coherence_block!r} for {slug}",
            person_slug=slug,
        )

    def promote(self, slug: str, *, token) -> StoreResult:
        """Move a SHADOW person into LIVE.

        Verifies the ConsentToken against the shadow person's slug
        and fact set BEFORE writing. Removes ``pending`` flag;
        rewrites provenance pins from ``staged`` to ``confirmed``.
        """
        from core.relationships.consent import verify_for_promotion
        shadow_people = self.list_shadow()
        match = next((p for p in shadow_people if p.slug == slug), None)
        if match is None:
            return StoreResult(
                ok=False,
                message=f"no shadow entry for slug={slug!r}",
                person_slug=slug,
            )
        # Defense-in-depth: the curator already verified upstream,
        # but the store cannot trust its caller to do so. Verify
        # again here against the shadow's own fact set.
        verify_for_promotion(
            token,
            person_slug=slug,
            facts=[f.text for f in match.facts],
        )
        promoted_facts = tuple(
            replace(f, staged=False) for f in match.facts
        )
        promoted = replace(
            match,
            pending=False,
            staged_at=None,
            source_turn_index=None,
            facts=promoted_facts,
        )
        # Merge into live: replace slug if exists, otherwise append.
        live_people = self.list_live()
        existing = next((p for p in live_people if p.slug == slug), None)
        if existing is not None:
            merged_facts = existing.facts + promoted.facts
            promoted = replace(
                promoted,
                facts=merged_facts,
                # Preserve the original first-introduction qualifier
                # if it differs (Day 3 disambiguation back-edit).
                qualifier=promoted.qualifier or existing.qualifier,
            )
            live_people = [p for p in live_people if p.slug != slug]
        live_people.append(promoted)
        _write_people(self._live_path, live_people, kind="live")
        # Drop the shadow entry.
        new_shadow = [p for p in shadow_people if p.slug != slug]
        _write_people(self._shadow_path, new_shadow, kind="shadow")
        return StoreResult(
            ok=True,
            message=f"promoted {len(promoted.facts)} new fact(s) for {slug}",
            person_slug=slug,
        )

    def drop_shadow(self, slug: str, *, reason: str) -> StoreResult:
        """Remove a SHADOW person without promoting (for restart-
        recovery drops, coherence blocks, sensitive blocks).
        """
        shadow_people = self.list_shadow()
        if not any(p.slug == slug for p in shadow_people):
            return StoreResult(
                ok=False,
                message=f"no shadow entry for slug={slug!r}",
                person_slug=slug,
            )
        new_shadow = [p for p in shadow_people if p.slug != slug]
        _write_people(self._shadow_path, new_shadow, kind="shadow")
        return StoreResult(
            ok=True,
            message=f"dropped shadow entry for {slug}: {reason}",
            person_slug=slug,
        )

    # ----- synchronous DELETE (3a) -----

    def delete_live(
        self, slug: str, *, token, removed_date: str
    ) -> StoreResult:
        """Atomically archive then remove a Person from the live file.

        Verifies the ConsentToken with ``expected_action="delete"``,
        appends the Person's verbatim block to RELATIONSHIPS-ARCHIVE.md
        (under a ``## REMOVED <removed_date>`` separator), and rewrites
        RELATIONSHIPS.md without the block.

        Atomicity: archive write happens first via tmp+rename, then
        live rewrite via tmp+rename. A crash between the two leaves
        the archive with a REMOVED-block AND the live file still
        carrying the same slug. Recovery path:

            if slug in archive (with REMOVED marker) and slug in live:
                resume the live rewrite (re-call delete_live with the
                same token reissued by recovery — 3a does not auto-
                recover; surfaced in REPORT.md and the user can re-run
                "forget Sarah" to converge). 3b will harden recovery.

        Returns ``ok=False`` with a non-error reason when no live
        entry exists for the slug — caller distinguishes this from
        a token failure (which raises ConsentError).
        """
        from core.relationships.consent import verify_for_promotion
        verify_for_promotion(
            token, person_slug=slug, facts=[], expected_action="delete",
        )
        live_people = self.list_live()
        match = next((p for p in live_people if p.slug == slug), None)
        if match is None:
            return StoreResult(
                ok=False,
                message=f"no live entry for slug={slug!r}",
                person_slug=slug,
            )
        # Step 1: append to archive (atomic via tmp+rename inside).
        append_archive_block(
            self._archive_path, person=match, removed_date=removed_date,
        )
        # Step 2: rewrite live without the block (atomic).
        self._atomic_rewrite_live(
            [p for p in live_people if p.slug != slug]
        )
        return StoreResult(
            ok=True,
            message=f"deleted {slug} (archived to {self._archive_path.name})",
            person_slug=slug,
        )

    def _atomic_rewrite_live(self, people: list[Person]) -> None:
        """Write the live file via ``.tmp + replace``. Centralised so
        the SUPERSEDE / DELETE / rename / restore paths share one
        atomic-rename implementation."""
        live_tmp = self._live_path.with_suffix(self._live_path.suffix + ".tmp")
        live_tmp.write_text(
            serialize_relationships_file(people, kind="live"),
            encoding="utf-8",
        )
        live_tmp.replace(self._live_path)

    # ----- v3c approval-time write to live -----

    def add_live(
        self, person: Person, *, token
    ) -> "AddLiveResult":
        """Promote an approved candidate to RELATIONSHIPS.md.

        Verifies the ConsentToken with ``expected_action="approve"``
        and that fact_ids cover ``person.facts``. Re-runs the
        sensitive-pattern scan with ``target_file="relationships"``
        — the third-party check is now suspended on the verified
        token, but the medical/legal/financial/etc. set still fires.
        A sensitive hit refuses with ``blocked_by="sensitive-pattern"``;
        token NOT consumed (caller decides whether to retry).

        Slug-collision handling:

        - No existing live entry for the slug → atomic append.
        - Existing live entry has a YAML qualifier that DIFFERS
          from ``person.qualifier`` → fire ``rename_live_slug`` to
          rename the existing bare slug to its qualified form,
          then append the new entry as the qualified slug. Caller
          (curator) is expected to derive ``person.slug`` to the
          qualified form already (matching v3b's
          ``_resolve_add_slug`` semantics).
        - Existing live entry has NO YAML qualifier AND
          ``person.qualifier`` is non-null → return
          ``blocked_by="missing_existing_qualifier"`` with the
          existing facts so the dashboard modal can render.
        - Existing live entry slug exactly matches and has the same
          qualifier (or both null) → merge: append the new facts
          to the existing block.
        """
        from core.relationships.consent import verify_for_promotion
        from core.learning_review import _scan_lesson_for_sensitive_content

        verify_for_promotion(
            token,
            person_slug=person.slug,
            facts=[f.text for f in person.facts],
            expected_action="approve",
        )
        # Step 1: sensitive-pattern scan with the token-verified
        # target_file. Joined fact text so a multi-fact promotion
        # that hides one bad fact in benign ones still trips.
        joined = "; ".join(f.text for f in person.facts)
        hit = _scan_lesson_for_sensitive_content(
            joined,
            scope=f"relationships:{person.slug}",
            target_file="relationships",
        )
        if hit:
            return AddLiveResult(
                ok=False,
                blocked_by="sensitive-pattern",
                detail=f"scanner-hit: {hit}",
                person_slug=person.slug,
            )
        # Step 2: slug-collision handling. v3c approval routes
        # qualified slugs already; bare-slug collisions only
        # happen when an existing live entry's YAML qualifier
        # introduces ambiguity.
        live_people = self.list_live()
        existing = next(
            (p for p in live_people if p.slug == person.slug), None
        )
        if existing is not None:
            # Same slug — merge facts (preserve existing qualifier
            # if the approval didn't supply one).
            from dataclasses import replace as _dc_replace
            merged_facts = existing.facts + person.facts
            merged = _dc_replace(
                existing,
                facts=merged_facts,
                last_confirmed=person.last_confirmed,
                source_session=person.source_session,
                qualifier=existing.qualifier or person.qualifier,
            )
            new_live = [p for p in live_people if p.slug != person.slug] + [merged]
            self._atomic_rewrite_live(new_live)
            return AddLiveResult(
                ok=True,
                merged=True,
                person_slug=person.slug,
                detail=f"merged {len(person.facts)} fact(s) into existing {person.slug}",
            )
        # No same-slug match. Bare-slug collision case: the user's
        # approval supplied a qualifier (so person.slug is qualified
        # like ``sarah-coworker``) but a bare ``sarah`` exists in
        # live. Detect by stripping the qualifier suffix.
        if person.qualifier:
            from core.relationships.triggers import derive_slug
            base_slug = derive_slug(person.display_name)
            if base_slug != person.slug:
                bare_match = next(
                    (p for p in live_people if p.slug == base_slug),
                    None,
                )
                if bare_match is not None:
                    if bare_match.qualifier is None:
                        # Missing-qualifier modal case (§5.1 patch).
                        return AddLiveResult(
                            ok=False,
                            blocked_by="missing_existing_qualifier",
                            person_slug=person.slug,
                            existing_slug=base_slug,
                            existing_facts=tuple(
                                f.text for f in bare_match.facts
                            ),
                            existing_qualifier_candidates=(),
                            proposed_qualifier=person.qualifier,
                            detail=(
                                f"existing live entry {base_slug!r} has "
                                f"no qualifier; resolve via dashboard"
                            ),
                        )
                    # Existing has a qualifier — back-edit it to the
                    # qualified form first, then append the new entry.
                    rename_res = self.rename_live_slug(
                        old_slug=base_slug,
                        new_slug=f"{base_slug}-{bare_match.qualifier}",
                        new_qualifier=bare_match.qualifier,
                        disambiguated_date=person.last_confirmed,
                    )
                    if not rename_res.ok:
                        return AddLiveResult(
                            ok=False,
                            blocked_by="back-edit-failed",
                            person_slug=person.slug,
                            detail=rename_res.message,
                        )
                    live_people = self.list_live()
        # Plain append.
        new_live = list(live_people) + [person]
        self._atomic_rewrite_live(new_live)
        return AddLiveResult(
            ok=True,
            person_slug=person.slug,
            detail=f"added {len(person.facts)} fact(s) for {person.slug}",
        )

    # ----- synchronous SUPERSEDE (3b) -----

    def supersede_live(
        self,
        slug: str,
        *,
        token,
        new_facts: list[str],
        new_session_uuid: str,
        new_session_short: str,
        superseded_date: str,
    ) -> StoreResult:
        """Atomically archive the OLD facts then rewrite the live
        Person with the NEW fact set under the same H2.

        Verifies the ConsentToken with ``expected_action="supersede"``
        AND that the token's fact_ids cover ``new_facts`` (so a
        tampered runtime can't slip in extra facts before the live
        rewrite lands). YAML preserved verbatim except
        ``last_confirmed`` is bumped to ``superseded_date`` and
        ``source_session`` is updated to ``new_session_uuid`` (the
        live entry's "most recent confirmation" pin moves to the
        SUPERSEDE turn).

        Atomicity: archive append first, then live rewrite. A crash
        between the two leaves both the OLD facts (in the archive
        under SUPERSEDED) AND the OLD facts in the live file. Same
        recovery contract as DELETE — user re-runs "update Sarah" to
        converge.

        ``ok=False`` when no live entry matches the slug. Token
        check raises ConsentError on failure.
        """
        from core.relationships.consent import verify_for_promotion
        verify_for_promotion(
            token,
            person_slug=slug,
            facts=new_facts,
            expected_action="supersede",
        )
        live_people = self.list_live()
        match = next((p for p in live_people if p.slug == slug), None)
        if match is None:
            return StoreResult(
                ok=False,
                message=f"no live entry for slug={slug!r}",
                person_slug=slug,
            )
        # Step 1: archive the OLD block with SUPERSEDED marker and
        # per-fact `[superseded ... by sess:...]` provenance lines.
        append_supersede_archive_block(
            self._archive_path,
            old_person=match,
            superseded_date=superseded_date,
            new_session_short=new_session_short,
        )
        # Step 2: build the new live block — same heading + slug +
        # qualifier, updated last_confirmed + source_session, NEW
        # facts as `[confirmed ...]` pins.
        new_fact_objs = tuple(
            Fact(
                text=t,
                confirmed_date=superseded_date,
                source_session_short=new_session_short,
                staged=False,
            )
            for t in new_facts
        )
        new_person = replace(
            match,
            last_confirmed=superseded_date,
            source_session=new_session_uuid,
            facts=new_fact_objs,
        )
        new_live = [p for p in live_people if p.slug != slug] + [new_person]
        self._atomic_rewrite_live(new_live)
        return StoreResult(
            ok=True,
            message=(
                f"superseded {slug} ({len(new_facts)} new fact(s); "
                f"old archived)"
            ),
            person_slug=slug,
        )

    # ----- 3b disambiguation back-edit -----

    def rename_live_slug(
        self,
        *,
        old_slug: str,
        new_slug: str,
        new_qualifier: str | None,
        disambiguated_date: str,
    ) -> StoreResult:
        """Rename a live Person from ``old_slug`` to ``new_slug``,
        appending a ``## DISAMBIGUATED <date>`` provenance entry to
        the archive.

        Caller is the disambiguation back-edit flow when a second
        person with the same first name appears and the existing
        bare-slug entry needs to be renamed to its qualified form.
        Both writes are atomic (.tmp + replace), archive-first.

        ``new_qualifier`` overrides the existing entry's YAML
        qualifier (passed by caller because the back-edit decision
        was made from that field). The H2 heading is recomputed
        from ``display_name`` + ``new_qualifier``.

        No token requirement — the back-edit is curator-driven on
        an already-consented entry; the rename does not introduce
        new content. Defense-in-depth: the live file rewrite is
        atomic so a tampered intermediate state can't leak.

        ``ok=False`` when ``old_slug`` doesn't exist or
        ``new_slug`` would collide with another live entry.
        """
        live_people = self.list_live()
        match = next((p for p in live_people if p.slug == old_slug), None)
        if match is None:
            return StoreResult(
                ok=False,
                message=f"no live entry for slug={old_slug!r}",
                person_slug=old_slug,
            )
        if any(p.slug == new_slug for p in live_people if p.slug != old_slug):
            return StoreResult(
                ok=False,
                message=(
                    f"rename target slug={new_slug!r} already exists in live"
                ),
                person_slug=old_slug,
            )
        # Step 1: archive the disambiguation provenance line.
        append_disambiguation_archive_line(
            self._archive_path,
            disambiguated_date=disambiguated_date,
            old_slug=old_slug,
            new_slug=new_slug,
        )
        # Step 2: rewrite live with the renamed entry.
        renamed = replace(
            match,
            slug=new_slug,
            qualifier=new_qualifier,
        )
        new_live = [p for p in live_people if p.slug != old_slug] + [renamed]
        self._atomic_rewrite_live(new_live)
        return StoreResult(
            ok=True,
            message=f"renamed {old_slug!r} → {new_slug!r}",
            person_slug=new_slug,
        )

    # ----- 3b restore from archive -----

    def restore_from_archive(self, slug: str) -> StoreResult:
        """Restore the most-recent ``## REMOVED`` block for ``slug``
        from RELATIONSHIPS-ARCHIVE.md back into RELATIONSHIPS.md,
        and remove the REMOVED block from the archive.

        Token-free path — caller is the user-initiated
        ``/learning relationships-restore`` slash command. No
        classifier verdict, no consent ambiguity.

        ``ok=False`` shapes:
        - ``"slug-already-live"``: ``slug`` exists in the live file.
          Restore would overwrite; user must /forget first.
        - ``"no-archive"``: archive file doesn't exist.
        - ``"no-removed-block"``: archive has no REMOVED block for
          ``slug``.

        On success: live rewrite (.tmp + replace), archive rewrite
        (.tmp + replace), archive-first. Multiple REMOVED blocks
        for the same slug → restore the LAST one (most recent),
        leave older REMOVED blocks alone (deleted-restored-deleted
        history is preserved for audit).
        """
        live_people = self.list_live()
        if any(p.slug == slug for p in live_people):
            return StoreResult(
                ok=False,
                message="slug-already-live",
                person_slug=slug,
            )
        if not self._archive_path.exists():
            return StoreResult(
                ok=False,
                message="no-archive",
                person_slug=slug,
            )
        archive_text = self._archive_path.read_text(encoding="utf-8")
        block_idx, block = _find_last_removed_block_for_slug(
            archive_text, slug,
        )
        if block_idx is None or block is None:
            return StoreResult(
                ok=False,
                message="no-removed-block",
                person_slug=slug,
            )
        # Parse the embedded Person from the block (the block is the
        # markdown body that lived in the live file). We strip the
        # leading `## REMOVED <date>` line and parse the rest as a
        # one-Person file.
        person_md = _strip_removed_header(block)
        people = parse_relationships_file(person_md)
        if not people:
            return StoreResult(
                ok=False,
                message="archive-block-unparseable",
                person_slug=slug,
            )
        restored = people[0]
        # Step 1: rewrite the archive without the restored REMOVED block.
        new_archive_text = _archive_with_block_removed(
            archive_text, block_idx, block,
        )
        archive_tmp = self._archive_path.with_suffix(
            self._archive_path.suffix + ".tmp"
        )
        archive_tmp.write_text(new_archive_text, encoding="utf-8")
        archive_tmp.replace(self._archive_path)
        # Step 2: rewrite live with the restored entry appended.
        live_people.append(restored)
        self._atomic_rewrite_live(live_people)
        return StoreResult(
            ok=True,
            message=f"restored {slug} from archive",
            person_slug=slug,
        )


# --------------------------------------------------------------------
# Archive parsing helpers (3b restore path)
# --------------------------------------------------------------------


_REMOVED_HEADER_RE = re.compile(
    r"^##\s+REMOVED\s+(\d{4}-\d{2}-\d{2})\s*$",
    re.MULTILINE,
)
# Marker H2s the archive uses to separate top-level entries.
# A REMOVED/SUPERSEDED/DISAMBIGUATED block extends from its marker
# H2 up to (but not including) the next marker H2 or EOF — including
# any embedded Person H2s within (the Person heading is data, not a
# new top-level block).
_ARCHIVE_TOPLEVEL_H2_RE = re.compile(
    r"^##\s+(?:REMOVED|SUPERSEDED|DISAMBIGUATED)\s+\d{4}-\d{2}-\d{2}\s*$",
    re.MULTILINE,
)


def _find_last_removed_block_for_slug(
    archive_text: str, slug: str
) -> tuple[int | None, str | None]:
    """Locate the most recent ``## REMOVED <date>`` block whose
    embedded YAML carries ``slug:`` for the requested slug.

    Returns ``(start_offset, block_text)`` so the caller can splice
    the block out of the archive. Returns ``(None, None)`` if no
    block matches. ``block_text`` includes the leading
    ``## REMOVED <date>\n`` header and the body up to (but not
    including) the next top-level archive H2 (REMOVED / SUPERSEDED /
    DISAMBIGUATED) or EOF.
    """
    boundaries = [
        m.start() for m in _ARCHIVE_TOPLEVEL_H2_RE.finditer(archive_text)
    ]
    if not boundaries:
        return None, None
    boundaries.append(len(archive_text))
    last_match: tuple[int, str] | None = None
    for i, start in enumerate(boundaries[:-1]):
        end = boundaries[i + 1]
        section = archive_text[start:end]
        first_newline = section.find("\n")
        header_line = section[:first_newline] if first_newline >= 0 else section
        if not _REMOVED_HEADER_RE.match(header_line):
            continue
        body = section[first_newline + 1:] if first_newline >= 0 else ""
        people = parse_relationships_file(body)
        if any(p.slug == slug for p in people):
            last_match = (start, section)
    if last_match is None:
        return None, None
    return last_match


def _strip_removed_header(block: str) -> str:
    """Drop the leading ``## REMOVED <date>\n`` line from a block
    so what remains parses as a one-Person markdown body."""
    first_newline = block.find("\n")
    if first_newline < 0:
        return ""
    return block[first_newline + 1:]


def _archive_with_block_removed(
    archive_text: str, block_start: int, block_text: str
) -> str:
    """Splice ``block_text`` out of ``archive_text`` starting at
    ``block_start``. Preserves preceding header/intro and any
    later REMOVED blocks intact. Trims any orphan blank lines that
    would otherwise stack after the splice."""
    before = archive_text[:block_start]
    after = archive_text[block_start + len(block_text):]
    # Trim any orphan blank lines at the splice seam: rstrip the
    # preceding text down to one trailing newline, lstrip leading
    # blank lines from the following text.
    before = before.rstrip() + "\n"
    after = after.lstrip("\n")
    if after:
        # Leave one blank line between intro/header and the next H2.
        return before + "\n" + after
    return before
