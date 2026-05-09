"""Per-chat pending-disambiguation state for the AMBIGUOUS verdict.

The relationships hook fires for the user's first AMBIGUOUS turn,
records what the original verdict was, what the candidate slugs
were, and what session_uuid/turn_index pinned the consent. The
hook for the user's NEXT turn on the same chat checks this store
first — if a non-expired entry is present, it merges the original
text with the new turn and re-runs classification to resolve.

5-minute TTL. Persisted to ``<workspace>/.vexis/relationships-pending.json``
so a daemon restart between the two turns can still recover.
Atomic via ``.tmp + replace``.

This module owns:

- ``PendingEntry`` dataclass.
- ``PendingDisambiguationStore`` — the persistence + TTL surface.

This module does NOT own:

- The decision-making about WHEN to write a pending entry — that
  lives in ``RelationshipsCurator._process_ambiguous`` because it
  needs the live store and classifier in scope.
- The merge-and-reclassify logic — also in the curator.

The 3-strike unresolved cap (per scoping doc §5 R8) lives in the
``ambiguity_count`` field; the curator increments it and decides
when to drop. This module just stores the count.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


PENDING_FILENAME = "relationships-pending.json"
PENDING_TTL = timedelta(minutes=5)
MAX_AMBIGUITY_REPROMPTS = 3


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class PendingEntry:
    """One pending disambiguation, keyed by ``chat_id`` in the
    enclosing store.

    ``original_verdict`` is one of "ADD" / "DELETE" / "SUPERSEDE"
    — the verdict the classifier emitted before the curator's slug
    resolution discovered the ambiguity. ``original_text`` is the
    user's verbatim utterance (used for the merge re-classification
    on the next turn). ``candidate_slugs`` is informational —
    rendered into the AMBIGUOUS reply.

    ``session_uuid`` / ``turn_index`` pin the *original* turn
    because that's the consent locus: when the user disambiguates,
    the token still has to claim it covers the original turn, not
    the disambiguation reply (which is itself a non-trigger turn
    most of the time).

    ``ambiguity_count`` is the number of times the curator has
    emitted an AMBIGUOUS reply for this entry. Starts at 1 (the
    first AMBIGUOUS reply). Resolved-on-next-turn → 0 increments.
    Still-ambiguous next-turn → 2. Cap at
    ``MAX_AMBIGUITY_REPROMPTS``.
    """

    chat_id: int
    expires_at: datetime
    original_verdict: str
    original_text: str
    candidate_slugs: tuple[str, ...]
    session_uuid: str
    turn_index: int
    ambiguity_count: int = 1

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or _utc_now()) >= self.expires_at

    def to_json(self) -> dict:
        return {
            "expires_at": _iso(self.expires_at),
            "original_verdict": self.original_verdict,
            "original_text": self.original_text,
            "candidate_slugs": list(self.candidate_slugs),
            "session_uuid": self.session_uuid,
            "turn_index": self.turn_index,
            "ambiguity_count": self.ambiguity_count,
        }

    @classmethod
    def from_json(cls, chat_id: int, payload: dict) -> "PendingEntry | None":
        expires = _parse_iso(payload.get("expires_at"))
        if expires is None:
            return None
        original_verdict = payload.get("original_verdict")
        if original_verdict not in ("ADD", "DELETE", "SUPERSEDE"):
            return None
        original_text = payload.get("original_text")
        if not isinstance(original_text, str) or not original_text:
            return None
        slugs_raw = payload.get("candidate_slugs") or []
        if not isinstance(slugs_raw, list):
            return None
        slugs = tuple(str(s) for s in slugs_raw if s)
        session_uuid = payload.get("session_uuid")
        if not isinstance(session_uuid, str) or not session_uuid:
            return None
        turn_index_raw = payload.get("turn_index")
        if not isinstance(turn_index_raw, int) or turn_index_raw < 1:
            return None
        amb_count = payload.get("ambiguity_count", 1)
        if not isinstance(amb_count, int) or amb_count < 1:
            amb_count = 1
        return cls(
            chat_id=chat_id,
            expires_at=expires,
            original_verdict=original_verdict,
            original_text=original_text,
            candidate_slugs=slugs,
            session_uuid=session_uuid,
            turn_index=turn_index_raw,
            ambiguity_count=amb_count,
        )


@dataclass
class PendingDisambiguationStore:
    """JSON-backed map of ``chat_id`` (str-keyed on disk) → ``PendingEntry``.

    Keys serialised as strings because JSON object keys must be
    strings; the in-memory dict uses ``int`` for ergonomics.
    Atomic writes via ``.tmp + replace`` so an interrupted save
    leaves either the old map or the new one — never a truncated
    mix.

    ``ttl`` is configurable for tests but defaults to 5 minutes
    per the scoping doc.
    """

    workspace: Path
    ttl: timedelta = PENDING_TTL
    _entries: dict[int, PendingEntry] = field(default_factory=dict)
    _loaded: bool = False

    @property
    def path(self) -> Path:
        return self.workspace / ".vexis" / PENDING_FILENAME

    def load(self) -> None:
        """Idempotent — call once at startup. Subsequent calls
        no-op. Reads ``relationships-pending.json``, drops expired
        entries silently, populates the in-memory map. A corrupt
        file logs a warning and starts empty (the user will lose
        any pending disambiguations from the previous session,
        but corruption shouldn't block startup)."""
        if self._loaded:
            return
        self._loaded = True
        path = self.path
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(
                "relationships-pending.json corrupt or unreadable: %s; "
                "starting empty",
                exc,
            )
            return
        if not isinstance(raw, dict):
            log.warning(
                "relationships-pending.json not an object; starting empty"
            )
            return
        now = _utc_now()
        for key, payload in raw.items():
            try:
                chat_id = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            entry = PendingEntry.from_json(chat_id, payload)
            if entry is None:
                continue
            if entry.is_expired(now):
                continue
            self._entries[chat_id] = entry

    def get(self, chat_id: int) -> PendingEntry | None:
        """Return the live (non-expired) entry for ``chat_id``, or
        None. Expired entries are GC'd here so callers don't have
        to re-check freshness."""
        self.load()
        entry = self._entries.get(chat_id)
        if entry is None:
            return None
        if entry.is_expired():
            self._entries.pop(chat_id, None)
            self._save()
            return None
        return entry

    def put(
        self,
        *,
        chat_id: int,
        original_verdict: str,
        original_text: str,
        candidate_slugs: list[str],
        session_uuid: str,
        turn_index: int,
        ambiguity_count: int = 1,
    ) -> PendingEntry:
        """Create or replace the entry for ``chat_id``. TTL is
        refreshed to ``now + ttl`` on every put (so a still-
        ambiguous re-prompt extends the window)."""
        self.load()
        entry = PendingEntry(
            chat_id=chat_id,
            expires_at=_utc_now() + self.ttl,
            original_verdict=original_verdict,
            original_text=original_text,
            candidate_slugs=tuple(candidate_slugs),
            session_uuid=session_uuid,
            turn_index=turn_index,
            ambiguity_count=ambiguity_count,
        )
        self._entries[chat_id] = entry
        self._save()
        return entry

    def bump_ambiguity(self, chat_id: int) -> PendingEntry | None:
        """Increment the entry's ``ambiguity_count`` and refresh
        its TTL. Returns the updated entry, or None if no entry
        exists. Caller (curator) decides whether the bumped count
        has hit ``MAX_AMBIGUITY_REPROMPTS`` and the entry should be
        dropped."""
        existing = self.get(chat_id)
        if existing is None:
            return None
        return self.put(
            chat_id=chat_id,
            original_verdict=existing.original_verdict,
            original_text=existing.original_text,
            candidate_slugs=list(existing.candidate_slugs),
            session_uuid=existing.session_uuid,
            turn_index=existing.turn_index,
            ambiguity_count=existing.ambiguity_count + 1,
        )

    def consume(self, chat_id: int) -> PendingEntry | None:
        """Remove and return the entry for ``chat_id``. Used when
        the curator successfully resolves a disambiguation OR when
        it gives up (TTL expiry, 3-strikes, unrelated message)."""
        self.load()
        entry = self._entries.pop(chat_id, None)
        if entry is not None:
            self._save()
        return entry

    def all(self) -> list[PendingEntry]:
        """Test/audit accessor — returns the live (non-expired)
        entries. Order undefined; iterate at your own pace."""
        self.load()
        now = _utc_now()
        live = [e for e in self._entries.values() if not e.is_expired(now)]
        # Garbage-collect expired entries on observation.
        if len(live) != len(self._entries):
            self._entries = {e.chat_id: e for e in live}
            self._save()
        return live

    def _save(self) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            str(chat_id): entry.to_json()
            for chat_id, entry in self._entries.items()
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
