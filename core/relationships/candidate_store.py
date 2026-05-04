"""Silent-extraction candidate queue (v3c Day 4a).

Per ``.plans/relationships-v3c-research.md``:

- Disk shape (§2.1): JSON at
  ``<workspace>/.vexis/relationships-candidates.json``, keyed
  by ``person_slug`` then by ``fact_id``. Each fact carries an
  occurrence array (one per (session_uuid, turn_index) sighting).
  Slug-level metadata: display_name, qualifier_candidates,
  strongest_cue_seen, first/last_seen, approved_at, rejected_at.
  Fact-level metadata: text, first/last_seen, occurrences,
  approved_at, rejected_at.
- Tiered eligibility (§3.4): strong qualifier cues
  (mom/dad/partner/sibling/child/etc.) → eligible after 1
  session; soft + weak cues → ≥2 distinct session_uuids in
  30 days. Strength is per-OBSERVATION (§3.5), tracked on the
  slug as ``strongest_cue_seen``.
- Rejection state machine (§3.6): per-fact tombstone
  (``facts.<id>.rejected_at``) silently drops re-extractions
  of that exact ``fact_id``; per-slug tombstone
  (``slug.rejected_at``) silently drops everything for that
  slug. ``restore_rejected`` clears either level. Tombstones
  are sticky across observations — the user explicitly clicks
  restore to undo.
- File-handling locking (mirrors ``core.user_candidates``):
  fcntl.flock sidecar lock + tmp + rename + fsync.

This module is brain-INVISIBLE by design. The brain's
``build_system_prompt`` does not read this file or its parsed
form, and ``tests/test_brain_isolation.py`` enforces that
property under any fixture state.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CANDIDATES_FILENAME = "relationships-candidates.json"

# Tiered eligibility defaults (mirroring core.user_candidates):
DEFAULT_RECURRENCE_THRESHOLD = 2
DEFAULT_RECURRENCE_WINDOW = timedelta(days=30)
DEFAULT_STALE_WINDOW = timedelta(days=30)

# Per-fact occurrence cap, FIFO eviction by seen_at.
MAX_OCCURRENCES_PER_FACT = 20

# Strong qualifier cues: relationships whose mere mention implies
# real persistence in the user's life. One observation is enough
# to pass the eligibility gate. Lowercase match after stripping.
STRONG_QUALIFIER_CUES = frozenset({
    "mom", "mother", "dad", "father",
    "wife", "husband", "spouse", "partner",
    "girlfriend", "boyfriend",
    "fiance", "fiancee", "fiancé", "fiancée",
    "son", "daughter", "child", "kid",
    "sister", "brother", "sibling",
    "grandmother", "grandfather", "grandma", "grandpa",
})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_qualifier(qualifier: str | None) -> str | None:
    if not qualifier:
        return None
    cleaned = qualifier.strip().lower()
    return cleaned or None


def _qualifier_strength(qualifier: str | None) -> str:
    """Return ``"strong" | "soft" | "weak"`` for ``qualifier``.

    Strong → in ``STRONG_QUALIFIER_CUES``. Soft → present but not
    strong (friend, coworker, boss, etc.). Weak → None / empty /
    pronoun-like ("guy", "person", "someone").
    """
    norm = _normalize_qualifier(qualifier)
    if norm is None:
        return "weak"
    if norm in STRONG_QUALIFIER_CUES:
        return "strong"
    if norm in {"guy", "person", "someone", "dude"}:
        return "weak"
    return "soft"


def candidates_path(workspace: Path) -> Path:
    return workspace / ".vexis" / CANDIDATES_FILENAME


# --------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------


@dataclass
class CandidateOccurrence:
    """One sighting of a fact under a slug, in one session turn."""

    session_uuid: str
    turn_index: int
    seen_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_uuid": self.session_uuid,
            "turn_index": self.turn_index,
            "seen_at": _iso(self.seen_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateOccurrence | None":
        seen_at = _parse_iso(data.get("seen_at"))
        if seen_at is None:
            return None
        session_uuid = data.get("session_uuid")
        turn_raw = data.get("turn_index")
        if not isinstance(session_uuid, str) or not session_uuid:
            return None
        if not isinstance(turn_raw, int) or turn_raw < 1:
            return None
        return cls(session_uuid=session_uuid, turn_index=turn_raw, seen_at=seen_at)


@dataclass
class CandidateFact:
    """One fact under a slug with its observation history and
    optional approval / rejection tombstones."""

    fact_id: str
    text: str
    first_seen: datetime
    last_seen: datetime
    occurrences: list[CandidateOccurrence] = field(default_factory=list)
    approved_at: datetime | None = None
    rejected_at: datetime | None = None

    def distinct_session_uuids_within(
        self, window: timedelta, *, now: datetime
    ) -> set[str]:
        cutoff = now - window
        return {o.session_uuid for o in self.occurrences if o.seen_at >= cutoff}

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "first_seen": _iso(self.first_seen),
            "last_seen": _iso(self.last_seen),
            "occurrences": [o.to_dict() for o in self.occurrences],
            "approved_at": _iso(self.approved_at) if self.approved_at else None,
            "rejected_at": _iso(self.rejected_at) if self.rejected_at else None,
        }

    @classmethod
    def from_dict(cls, fact_id: str, data: dict[str, Any]) -> "CandidateFact | None":
        first_seen = _parse_iso(data.get("first_seen"))
        last_seen = _parse_iso(data.get("last_seen"))
        text = data.get("text")
        if not isinstance(text, str) or not text or first_seen is None or last_seen is None:
            return None
        raw_occ = data.get("occurrences") or []
        occurrences: list[CandidateOccurrence] = []
        if isinstance(raw_occ, list):
            for o in raw_occ:
                if isinstance(o, dict):
                    parsed = CandidateOccurrence.from_dict(o)
                    if parsed is not None:
                        occurrences.append(parsed)
        return cls(
            fact_id=fact_id,
            text=text,
            first_seen=first_seen,
            last_seen=last_seen,
            occurrences=occurrences,
            approved_at=_parse_iso(data.get("approved_at")),
            rejected_at=_parse_iso(data.get("rejected_at")),
        )


@dataclass
class Candidate:
    """One person-slug entry with all its facts and slug-level state."""

    slug: str
    display_name: str
    first_seen: datetime
    last_seen: datetime
    qualifier_candidates: list[str] = field(default_factory=list)
    strongest_cue_seen: str = "weak"  # "weak" | "soft" | "strong"
    facts: dict[str, CandidateFact] = field(default_factory=dict)
    approved_at: datetime | None = None
    rejected_at: datetime | None = None

    def distinct_session_uuids_within(
        self, window: timedelta, *, now: datetime
    ) -> set[str]:
        """Across all NON-REJECTED facts, distinct session UUIDs
        within ``window``. Rejected facts don't count toward
        eligibility."""
        cutoff = now - window
        seen: set[str] = set()
        for fact in self.facts.values():
            if fact.rejected_at is not None:
                continue
            for o in fact.occurrences:
                if o.seen_at >= cutoff:
                    seen.add(o.session_uuid)
        return seen

    def active_facts(self) -> list[CandidateFact]:
        return [f for f in self.facts.values() if f.rejected_at is None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "first_seen": _iso(self.first_seen),
            "last_seen": _iso(self.last_seen),
            "qualifier_candidates": list(self.qualifier_candidates),
            "strongest_cue_seen": self.strongest_cue_seen,
            "facts": {fid: f.to_dict() for fid, f in self.facts.items()},
            "approved_at": _iso(self.approved_at) if self.approved_at else None,
            "rejected_at": _iso(self.rejected_at) if self.rejected_at else None,
        }

    @classmethod
    def from_dict(cls, slug: str, data: dict[str, Any]) -> "Candidate | None":
        first_seen = _parse_iso(data.get("first_seen"))
        last_seen = _parse_iso(data.get("last_seen"))
        display_name = data.get("display_name")
        if (
            not isinstance(display_name, str)
            or not display_name
            or first_seen is None
            or last_seen is None
        ):
            return None
        quals_raw = data.get("qualifier_candidates") or []
        quals = [str(q) for q in quals_raw if isinstance(q, str) and q.strip()]
        strongest = data.get("strongest_cue_seen", "weak")
        if strongest not in ("strong", "soft", "weak"):
            strongest = "weak"
        raw_facts = data.get("facts") or {}
        facts: dict[str, CandidateFact] = {}
        if isinstance(raw_facts, dict):
            for fid, fdata in raw_facts.items():
                if isinstance(fid, str) and isinstance(fdata, dict):
                    parsed = CandidateFact.from_dict(fid, fdata)
                    if parsed is not None:
                        facts[fid] = parsed
        return cls(
            slug=slug,
            display_name=display_name,
            first_seen=first_seen,
            last_seen=last_seen,
            qualifier_candidates=quals,
            strongest_cue_seen=strongest,
            facts=facts,
            approved_at=_parse_iso(data.get("approved_at")),
            rejected_at=_parse_iso(data.get("rejected_at")),
        )


# --------------------------------------------------------------------
# View dataclasses for slash-command / dashboard consumers
# --------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateView:
    """Read-only projection of a Candidate for the slash-command and
    dashboard list endpoints. Excludes rejected facts unless the
    caller explicitly opts in via ``include_rejected``."""

    slug: str
    display_name: str
    qualifier: str | None
    qualifier_candidates: tuple[str, ...]
    strongest_cue_seen: str
    session_count: int
    fact_count: int
    eligible: bool
    facts: tuple["FactView", ...]
    first_seen: datetime
    last_seen: datetime
    approved_at: datetime | None
    rejected_at: datetime | None


@dataclass(frozen=True)
class FactView:
    """Read-only projection of a CandidateFact."""

    fact_id: str
    text: str
    occurrence_count: int
    first_seen: datetime
    last_seen: datetime
    rejected_at: datetime | None


# --------------------------------------------------------------------
# Store
# --------------------------------------------------------------------


class RelationshipsCandidateStore:
    """Disk-backed silent-extraction candidate queue.

    Mirrors ``core.user_candidates.UserCandidateStore``'s locking
    model (fcntl.flock sidecar + tmp + rename + fsync) so the
    daemon thread, the curator-extractor subprocess (no, this
    runs in-process — the lock just defends against future
    multi-process callers), and the dashboard's API mutations all
    see consistent state.

    The store holds no in-memory state between calls — every
    mutation re-reads from disk under the lock, applies the
    change, writes back. Reads do not lock; atomic rename
    guarantees readers see either old or new state, never a tear.
    """

    def __init__(
        self,
        path: Path,
        *,
        recurrence_threshold: int = DEFAULT_RECURRENCE_THRESHOLD,
        recurrence_window: timedelta = DEFAULT_RECURRENCE_WINDOW,
        stale_window: timedelta = DEFAULT_STALE_WINDOW,
    ) -> None:
        self._path = path
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._recurrence_threshold = recurrence_threshold
        self._recurrence_window = recurrence_window
        self._stale_window = stale_window

    @property
    def path(self) -> Path:
        return self._path

    # ---------- io ----------

    def load(self) -> dict[str, Candidate]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning(
                "%s corrupt; treating as empty",
                self._path.name,
            )
            return {}
        by_slug = data.get("by_slug") if isinstance(data, dict) else None
        if not isinstance(by_slug, dict):
            return {}
        out: dict[str, Candidate] = {}
        for slug, payload in by_slug.items():
            if isinstance(slug, str) and isinstance(payload, dict):
                parsed = Candidate.from_dict(slug, payload)
                if parsed is not None:
                    out[slug] = parsed
        return out

    def _save(self, by_slug: dict[str, Candidate]) -> None:
        payload = {
            "by_slug": {slug: c.to_dict() for slug, c in by_slug.items()},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    # ---------- public API ----------

    def add_observation(
        self,
        *,
        slug: str,
        display_name: str,
        qualifier: str | None,
        fact_text: str,
        session_uuid: str,
        turn_index: int,
        seen_at: datetime | None = None,
    ) -> Candidate | None:
        """Record one (slug, fact) sighting in one session turn.

        Returns the post-mutation Candidate, or None when the
        observation was dropped due to a rejection tombstone (per
        §3.6: rejected slugs and rejected fact_ids drop silently
        on re-extraction). Tombstones never auto-clear; the user
        must explicitly call ``restore_rejected``.
        """
        from core.relationships.consent import _fact_id

        when = seen_at or _utc_now()
        fact_id = _fact_id(fact_text)
        records = self.load()
        candidate = records.get(slug)
        # §3.6: slug-level tombstone short-circuits everything
        # (no observation recorded, no fact entry created).
        if candidate is not None and candidate.rejected_at is not None:
            log.debug(
                "candidate_store: dropping observation for "
                "rejected slug %s", slug,
            )
            return None
        # §3.6: fact-level tombstone short-circuits the fact write
        # (slug stays open for OTHER facts in the same emission).
        if (
            candidate is not None
            and fact_id in candidate.facts
            and candidate.facts[fact_id].rejected_at is not None
        ):
            log.debug(
                "candidate_store: dropping observation for "
                "rejected fact %s under slug %s", fact_id, slug,
            )
            return None
        # New slug.
        if candidate is None:
            candidate = Candidate(
                slug=slug,
                display_name=display_name,
                first_seen=when,
                last_seen=when,
                qualifier_candidates=[],
                strongest_cue_seen="weak",
                facts={},
            )
            records[slug] = candidate
        # Slug-level updates.
        candidate.last_seen = when
        norm_qual = _normalize_qualifier(qualifier)
        if norm_qual and norm_qual not in {
            q.lower() for q in candidate.qualifier_candidates
        }:
            candidate.qualifier_candidates.append(norm_qual)
        # §3.5: strongest cue seen across observations wins.
        new_strength = _qualifier_strength(qualifier)
        candidate.strongest_cue_seen = _max_strength(
            candidate.strongest_cue_seen, new_strength,
        )
        # Fact entry.
        fact = candidate.facts.get(fact_id)
        if fact is None:
            fact = CandidateFact(
                fact_id=fact_id,
                text=fact_text,
                first_seen=when,
                last_seen=when,
                occurrences=[],
            )
            candidate.facts[fact_id] = fact
        fact.last_seen = when
        fact.occurrences.append(
            CandidateOccurrence(
                session_uuid=session_uuid,
                turn_index=turn_index,
                seen_at=when,
            )
        )
        # Hard cap with FIFO eviction by seen_at — same defense
        # as core.user_candidates.MAX_OCCURRENCES_PER_CLAIM.
        if len(fact.occurrences) > MAX_OCCURRENCES_PER_FACT:
            overflow = len(fact.occurrences) - MAX_OCCURRENCES_PER_FACT
            fact.occurrences.sort(key=lambda o: o.seen_at)
            fact.occurrences = fact.occurrences[overflow:]
        self._save(records)
        return candidate

    def get(self, slug: str) -> Candidate | None:
        return self.load().get(slug)

    def eligible_for_promotion(
        self, slug: str, *, now: datetime | None = None
    ) -> bool:
        """Tiered eligibility per §3.4. Strong cue → 1 session.
        Soft / weak → ≥``recurrence_threshold`` distinct session
        UUIDs in ``recurrence_window``. Rejected slugs and
        already-approved slugs are never eligible."""
        candidate = self.get(slug)
        if candidate is None:
            return False
        if candidate.rejected_at is not None:
            return False
        if candidate.approved_at is not None:
            return False
        if not candidate.active_facts():
            return False
        if now is None:
            now = _utc_now()
        if candidate.strongest_cue_seen == "strong":
            distinct = candidate.distinct_session_uuids_within(
                self._recurrence_window, now=now,
            )
            return len(distinct) >= 1
        distinct = candidate.distinct_session_uuids_within(
            self._recurrence_window, now=now,
        )
        return len(distinct) >= self._recurrence_threshold

    def mark_approved(
        self,
        slug: str,
        fact_ids: list[str] | None = None,
        *,
        now: datetime | None = None,
    ) -> Candidate | None:
        """Stamp ``approved_at`` on the slug and on each fact in
        ``fact_ids``. ``fact_ids=None`` means "all currently active
        (non-rejected) facts."""
        when = now or _utc_now()
        records = self.load()
        candidate = records.get(slug)
        if candidate is None:
            return None
        targets = (
            list(fact_ids)
            if fact_ids is not None
            else [f.fact_id for f in candidate.active_facts()]
        )
        for fid in targets:
            fact = candidate.facts.get(fid)
            if fact is None or fact.rejected_at is not None:
                continue
            fact.approved_at = when
        candidate.approved_at = when
        self._save(records)
        return candidate

    def mark_rejected(
        self,
        slug: str,
        fact_ids: list[str] | None = None,
        *,
        now: datetime | None = None,
    ) -> Candidate | None:
        """Tombstone the slug or specific facts under the slug.

        - ``fact_ids=None`` → set ``slug.rejected_at``. All facts
          under the slug become ineligible for promotion. Future
          extractions for the slug drop silently.
        - ``fact_ids=[ids...]`` → set ``facts.<id>.rejected_at``
          for each. Other facts under the slug stay open.
        """
        when = now or _utc_now()
        records = self.load()
        candidate = records.get(slug)
        if candidate is None:
            return None
        if fact_ids is None:
            candidate.rejected_at = when
        else:
            for fid in fact_ids:
                fact = candidate.facts.get(fid)
                if fact is None:
                    continue
                fact.rejected_at = when
        self._save(records)
        return candidate

    def restore_rejected(
        self,
        slug: str,
        fact_ids: list[str] | None = None,
    ) -> Candidate | None:
        """Inverse of ``mark_rejected``. Clears the tombstone(s);
        eligibility is re-evaluated against existing observations
        on the next ``eligible_for_promotion`` call."""
        records = self.load()
        candidate = records.get(slug)
        if candidate is None:
            return None
        if fact_ids is None:
            candidate.rejected_at = None
        else:
            for fid in fact_ids:
                fact = candidate.facts.get(fid)
                if fact is None:
                    continue
                fact.rejected_at = None
        self._save(records)
        return candidate

    def delete_slug(self, slug: str) -> bool:
        """Hard-remove a slug entry. Used by the approve flow when
        the candidate is fully promoted to live (no facts remaining
        in the queue). Returns True if the slug was present."""
        records = self.load()
        if slug not in records:
            return False
        del records[slug]
        self._save(records)
        return True

    def expire_stale(
        self, *, now: datetime | None = None
    ) -> int:
        """Drop unapproved + unrejected entries whose ``last_seen``
        is older than ``stale_window``. Approved / rejected entries
        are retained for audit. Returns the count removed."""
        when = now or _utc_now()
        cutoff = when - self._stale_window
        records = self.load()
        to_remove = [
            slug for slug, c in records.items()
            if c.approved_at is None
            and c.rejected_at is None
            and c.last_seen < cutoff
        ]
        if not to_remove:
            return 0
        for slug in to_remove:
            del records[slug]
        self._save(records)
        return len(to_remove)

    # ---------- read views ----------

    def list_eligible(
        self, *, now: datetime | None = None
    ) -> list[CandidateView]:
        """Eligible slugs (per §3.4 gate), excluding approved /
        rejected. Sorted by ``last_seen`` desc so the freshest
        candidates surface first in the slash-command list."""
        when = now or _utc_now()
        records = self.load()
        out: list[CandidateView] = []
        for slug, c in records.items():
            if c.approved_at is not None:
                continue
            if c.rejected_at is not None:
                continue
            if not self.eligible_for_promotion(slug, now=when):
                continue
            out.append(self._make_view(c, eligible=True, now=when))
        out.sort(key=lambda v: v.last_seen, reverse=True)
        return out

    def list_all(
        self, *, include_rejected: bool = False, now: datetime | None = None
    ) -> list[CandidateView]:
        """Every slug, with eligibility computed. Excludes rejected
        slugs unless ``include_rejected``. Approved slugs are
        always included (audit surface)."""
        when = now or _utc_now()
        records = self.load()
        out: list[CandidateView] = []
        for slug, c in records.items():
            if c.rejected_at is not None and not include_rejected:
                continue
            eligible = self.eligible_for_promotion(slug, now=when)
            out.append(self._make_view(c, eligible=eligible, now=when))
        out.sort(key=lambda v: v.last_seen, reverse=True)
        return out

    def _make_view(
        self, c: Candidate, *, eligible: bool, now: datetime
    ) -> CandidateView:
        facts_view = tuple(
            FactView(
                fact_id=f.fact_id,
                text=f.text,
                occurrence_count=len(f.occurrences),
                first_seen=f.first_seen,
                last_seen=f.last_seen,
                rejected_at=f.rejected_at,
            )
            for f in c.facts.values()
            if f.rejected_at is None
        )
        session_count = len(
            c.distinct_session_uuids_within(
                self._recurrence_window, now=now,
            )
        )
        # Pick the most-recently-observed qualifier as the
        # "primary" qualifier for the view; full list still
        # available in qualifier_candidates.
        primary_qualifier = c.qualifier_candidates[-1] if c.qualifier_candidates else None
        return CandidateView(
            slug=c.slug,
            display_name=c.display_name,
            qualifier=primary_qualifier,
            qualifier_candidates=tuple(c.qualifier_candidates),
            strongest_cue_seen=c.strongest_cue_seen,
            session_count=session_count,
            fact_count=len(facts_view),
            eligible=eligible,
            facts=facts_view,
            first_seen=c.first_seen,
            last_seen=c.last_seen,
            approved_at=c.approved_at,
            rejected_at=c.rejected_at,
        )


_STRENGTH_RANK = {"weak": 0, "soft": 1, "strong": 2}


def _max_strength(a: str, b: str) -> str:
    return a if _STRENGTH_RANK.get(a, 0) >= _STRENGTH_RANK.get(b, 0) else b


__all__ = [
    "CANDIDATES_FILENAME",
    "DEFAULT_RECURRENCE_THRESHOLD",
    "DEFAULT_RECURRENCE_WINDOW",
    "DEFAULT_STALE_WINDOW",
    "MAX_OCCURRENCES_PER_FACT",
    "STRONG_QUALIFIER_CUES",
    "Candidate",
    "CandidateFact",
    "CandidateOccurrence",
    "CandidateView",
    "FactView",
    "RelationshipsCandidateStore",
    "candidates_path",
]
