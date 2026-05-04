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


def _read_people(path: Path) -> list[Person]:
    if not path.exists():
        return []
    return parse_relationships_file(path.read_text(encoding="utf-8"))


def _write_people(path: Path, people: list[Person], *, kind: FileKind) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_relationships_file(people, kind=kind), encoding="utf-8")


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
