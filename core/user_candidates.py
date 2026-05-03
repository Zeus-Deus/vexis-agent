"""USER.md candidate queue: cross-session threshold for identity claims.

Day 3 of the v2 learning curator. IDENTITY classifications go through
this queue rather than landing in USER.md (or USER-SHADOW.md) on the
first observation. A claim only gets promoted when it has been
observed in **≥2 distinct sessions within a 30-day window** —
defending against one-shot mood / stress signals that look like
durable identity but aren't.

File layout
-----------
``~/.vexis/learning/user_candidates.json``::

    {
      "by_claim": {
        "User prefers terse responses for direct factual questions.": {
          "first_seen":          "2026-05-02T19:55:00Z",
          "last_seen":           "2026-05-03T14:20:00Z",
          "occurrences": [
            {"session_uuid": "abc-123",
             "evidence":     "I asked for the disk usage percentage…",
             "seen_at":      "2026-05-02T19:55:00Z"},
            {"session_uuid": "def-456",
             "evidence":     "stop wrapping yes/no in three paragraphs",
             "seen_at":      "2026-05-03T14:20:00Z"}
          ],
          "promoted_at":          "2026-05-03T14:20:01Z",
          "promoted_to_user_md":  true
        }
      }
    }

Locking
-------
Same model as ``reviewed.json`` (see ``core/learning_curator.py:148``):
sidecar ``.lock`` file with ``fcntl.flock``, atomic temp+rename
write, fsync. Reads do not lock — atomic rename means a reader
either sees the old file or the new file, never a tear.

Promotion contract
------------------
- ``add_occurrence(claim, session_uuid, evidence)`` records one
  observation. Multiple occurrences from the same ``session_uuid``
  count as ONE for the threshold (defends a single bug producing
  multiple emissions per session).
- ``eligible_for_promotion(claim, threshold=2, window=30d, now)``
  returns True iff the claim has ≥``threshold`` distinct
  ``session_uuid`` values within ``window`` of ``now`` AND has not
  already been promoted.
- ``mark_promoted(claim, now)`` sets ``promoted_at`` and
  ``promoted_to_user_md=True``. The claim stays in the file for
  audit but no longer counts as eligible.
- ``expire_stale(now, window=30d)`` removes unpromoted claims whose
  ``last_seen`` is older than ``window``. Promoted claims are
  retained indefinitely so audit can trace what was promoted when.
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

# Default threshold + window. Spec values from §3.4 of the v2
# research doc. Both can be overridden by callers (the queue itself
# stays policy-free; the dispatcher decides the threshold).
DEFAULT_PROMOTION_THRESHOLD = 2
DEFAULT_WINDOW = timedelta(days=30)

# Hard cap on occurrences per claim — defends against a buggy
# expire_stale + a chatty user combining to grow the queue
# unboundedly. When ``add_occurrence`` would push past this, the
# OLDEST occurrence is dropped FIFO so the audit trail keeps the
# most recent N — which is what matters for the eligibility window
# anyway (anything older than 30 days doesn't count for promotion).
#
# 20 is the v2 spec value. Rationale: the eligibility threshold is
# 2 distinct sessions; even a wildly chatty user emitting the same
# claim 5x per session would saturate the cap in 4 sessions, which
# is well past the 2-session bar. Anything beyond is audit padding
# that doesn't change promotion behavior.
MAX_OCCURRENCES_PER_CLAIM = 20


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


# --------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------


@dataclass
class UserCandidateOccurrence:
    """One observation of an IDENTITY claim from one session."""

    session_uuid: str
    evidence: str
    seen_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_uuid": self.session_uuid,
            "evidence": self.evidence,
            "seen_at": _iso(self.seen_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserCandidateOccurrence | None":
        seen_at = _parse_iso(data.get("seen_at"))
        if seen_at is None:
            return None
        session_uuid = data.get("session_uuid")
        evidence = data.get("evidence")
        if not isinstance(session_uuid, str) or not isinstance(evidence, str):
            return None
        return cls(session_uuid=session_uuid, evidence=evidence, seen_at=seen_at)


@dataclass
class UserCandidate:
    """One IDENTITY claim and every session that has observed it."""

    claim: str
    first_seen: datetime
    last_seen: datetime
    occurrences: list[UserCandidateOccurrence] = field(default_factory=list)
    promoted_at: datetime | None = None
    promoted_to_user_md: bool = False

    def distinct_session_uuids(self) -> set[str]:
        """De-duplicate session UUIDs so a single session producing
        multiple emissions of the same claim still counts as one."""
        return {o.session_uuid for o in self.occurrences}

    def distinct_session_uuids_within(
        self, window: timedelta, *, now: datetime
    ) -> set[str]:
        """Distinct UUIDs whose ``seen_at`` is within ``window`` of
        ``now``. Used by the eligibility check — old observations
        time out so a claim that was observed once 90 days ago plus
        once today is NOT eligible (it's effectively a new claim
        with one current observation)."""
        cutoff = now - window
        return {o.session_uuid for o in self.occurrences if o.seen_at >= cutoff}

    def to_dict(self) -> dict[str, Any]:
        return {
            "first_seen": _iso(self.first_seen),
            "last_seen": _iso(self.last_seen),
            "occurrences": [o.to_dict() for o in self.occurrences],
            "promoted_at": _iso(self.promoted_at) if self.promoted_at else None,
            "promoted_to_user_md": self.promoted_to_user_md,
        }

    @classmethod
    def from_dict(cls, claim: str, data: dict[str, Any]) -> "UserCandidate | None":
        first_seen = _parse_iso(data.get("first_seen"))
        last_seen = _parse_iso(data.get("last_seen"))
        if first_seen is None or last_seen is None:
            return None
        raw_occ = data.get("occurrences", [])
        occurrences: list[UserCandidateOccurrence] = []
        if isinstance(raw_occ, list):
            for o in raw_occ:
                if isinstance(o, dict):
                    parsed = UserCandidateOccurrence.from_dict(o)
                    if parsed is not None:
                        occurrences.append(parsed)
        return cls(
            claim=claim,
            first_seen=first_seen,
            last_seen=last_seen,
            occurrences=occurrences,
            promoted_at=_parse_iso(data.get("promoted_at")),
            promoted_to_user_md=bool(data.get("promoted_to_user_md", False)),
        )


# --------------------------------------------------------------------
# Store
# --------------------------------------------------------------------


class UserCandidateStore:
    """Owns ``user_candidates.json``. Mirrors ``ReviewedStore``
    locking model: sidecar ``.lock`` for write integrity via
    fcntl.flock + temp+rename + fsync. Reads do not lock.

    All mutating methods load fresh from disk under the lock,
    apply the change, and write back. The store holds no in-memory
    state between calls — concurrent processes (curator daemon,
    Telegram audit handler, dashboard) all see consistent state.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_suffix(path.suffix + ".lock")

    # ---------- io ----------

    def load(self) -> dict[str, UserCandidate]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning(
                "user_candidates.json corrupt at %s; treating as empty",
                self._path,
            )
            return {}
        by_claim = data.get("by_claim") if isinstance(data, dict) else None
        if not isinstance(by_claim, dict):
            return {}
        out: dict[str, UserCandidate] = {}
        for claim, payload in by_claim.items():
            if isinstance(claim, str) and isinstance(payload, dict):
                parsed = UserCandidate.from_dict(claim, payload)
                if parsed is not None:
                    out[claim] = parsed
        return out

    def _save(self, by_claim: dict[str, UserCandidate]) -> None:
        payload = {
            "by_claim": {claim: c.to_dict() for claim, c in by_claim.items()},
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

    # ---------- public api ----------

    def add_occurrence(
        self,
        claim: str,
        session_uuid: str,
        evidence: str,
        *,
        now: datetime | None = None,
    ) -> UserCandidate:
        """Record one observation of ``claim`` from ``session_uuid``.

        Creates the queue entry on first observation; appends to it
        on subsequent observations. Returns the post-mutation
        candidate so the caller can decide on promotion in the same
        critical section (avoiding a load-modify-load race).

        Multiple calls with the same ``session_uuid`` for the same
        claim STILL append (occurrences is an audit log, not a set);
        the dedup-for-threshold logic happens at eligibility-check
        time via ``distinct_session_uuids``. This keeps the audit
        trail honest — the user can see "the curator observed this
        claim twice in the same session" if it happens.
        """
        when = now or _utc_now()
        records = self.load()
        candidate = records.get(claim)
        if candidate is None:
            candidate = UserCandidate(
                claim=claim,
                first_seen=when,
                last_seen=when,
            )
            records[claim] = candidate
        candidate.occurrences.append(
            UserCandidateOccurrence(
                session_uuid=session_uuid,
                evidence=evidence,
                seen_at=when,
            )
        )
        # Hard cap: if the occurrences list exceeds
        # MAX_OCCURRENCES_PER_CLAIM, drop the OLDEST entries FIFO.
        # Eligibility uses ``distinct_session_uuids_within(window)``
        # which already only counts in-window observations, so
        # dropping ancient entries doesn't change promotion behavior
        # for current claims. This is the belt-and-suspenders
        # defense against a buggy expire_stale not running.
        if len(candidate.occurrences) > MAX_OCCURRENCES_PER_CLAIM:
            overflow = len(candidate.occurrences) - MAX_OCCURRENCES_PER_CLAIM
            log.info(
                "Trimming %d oldest occurrence(s) from claim %r "
                "(cap = %d)",
                overflow, claim, MAX_OCCURRENCES_PER_CLAIM,
            )
            # Sort by seen_at to ensure we drop the genuinely-oldest,
            # not just the first-appended (defensive against
            # out-of-order inserts from concurrent reviews).
            candidate.occurrences.sort(key=lambda o: o.seen_at)
            candidate.occurrences = candidate.occurrences[overflow:]
        candidate.last_seen = when
        self._save(records)
        return candidate

    def get(self, claim: str) -> UserCandidate | None:
        return self.load().get(claim)

    def list_all(self) -> list[UserCandidate]:
        """Sorted by first_seen ASC. For audit surfaces."""
        records = self.load()
        return sorted(records.values(), key=lambda c: c.first_seen)

    def list_pending(self, *, now: datetime | None = None) -> list[UserCandidate]:
        """Unpromoted claims, sorted by first_seen ASC. The
        Telegram audit surface uses this to show "claims accumulating
        toward the threshold"."""
        return [c for c in self.list_all() if not c.promoted_to_user_md]

    def list_promoted(self) -> list[UserCandidate]:
        """Already-promoted claims, sorted by promoted_at ASC."""
        records = self.load()
        promoted = [c for c in records.values() if c.promoted_to_user_md]
        return sorted(promoted, key=lambda c: c.promoted_at or c.last_seen)

    def eligible_for_promotion(
        self,
        claim: str,
        *,
        threshold: int = DEFAULT_PROMOTION_THRESHOLD,
        window: timedelta = DEFAULT_WINDOW,
        now: datetime | None = None,
    ) -> bool:
        """True iff ``claim`` has ≥``threshold`` distinct session
        UUIDs within ``window`` AND has not already been promoted.

        Distinct UUIDs only — the same session emitting the claim
        twice still counts as one. The window check uses ``seen_at``,
        not ``first_seen``, so an old single observation doesn't
        anchor a 30-day window that lets one new observation
        promote.
        """
        candidate = self.get(claim)
        if candidate is None:
            return False
        if candidate.promoted_to_user_md:
            return False
        if now is None:
            now = _utc_now()
        return len(candidate.distinct_session_uuids_within(window, now=now)) >= threshold

    def mark_promoted(
        self, claim: str, *, now: datetime | None = None
    ) -> UserCandidate | None:
        """Mark ``claim`` as promoted. Returns the post-mutation
        candidate, or None if the claim doesn't exist (defensive —
        the caller really should have just added an occurrence)."""
        when = now or _utc_now()
        records = self.load()
        candidate = records.get(claim)
        if candidate is None:
            return None
        candidate.promoted_at = when
        candidate.promoted_to_user_md = True
        self._save(records)
        return candidate

    def expire_stale(
        self,
        *,
        now: datetime | None = None,
        window: timedelta = DEFAULT_WINDOW,
    ) -> int:
        """Remove UNPROMOTED claims whose ``last_seen`` is older than
        ``window`` ago. Returns the count removed.

        Promoted claims are retained indefinitely so audit can trace
        when each USER.md entry was promoted from. The dispatcher
        calls this periodically (e.g. once per hour as a tick
        housekeeping step) so stale candidates don't accumulate.
        """
        when = now or _utc_now()
        cutoff = when - window
        records = self.load()
        to_remove = [
            claim
            for claim, c in records.items()
            if not c.promoted_to_user_md and c.last_seen < cutoff
        ]
        if not to_remove:
            return 0
        for claim in to_remove:
            del records[claim]
        self._save(records)
        return len(to_remove)


__all__ = [
    "DEFAULT_PROMOTION_THRESHOLD",
    "DEFAULT_WINDOW",
    "UserCandidate",
    "UserCandidateOccurrence",
    "UserCandidateStore",
]
