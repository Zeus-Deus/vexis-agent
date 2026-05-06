"""RelationshipsCurator — three entry points (turn-level write,
tick-level promote, restart-recovery).

Per research doc §3.4 (with §3.1 amendment-2 path commitment):

- **Turn-level** (``process_user_turn``): wired from
  ``transports/telegram.py``'s ``_dispatch_to_brain``. Runs the
  trigger detector under the role-gate; on positive ADD, mints
  one ConsentToken for ``(session_uuid, turn_index, person_slug)``
  covering N facts, writes the staged Person into
  RELATIONSHIPS-SHADOW.md with ``pending: true``, returns a
  staged-acknowledge reply that the transport sends to the user.
  DELETE / SUPERSEDE land Day 3 — Day 2 raises NotImplementedError
  with a TODO marker for those branches.

- **Tick-level** (``tick_promote_pending``): hooked into the
  existing learning-curator tick. For each ``pending: true`` block
  in shadow:

    1. Token-presence check: the in-memory PendingTokens must hold
       a token matching ``(session_uuid, turn_index, person_slug)``.
       Missing → drop with REPORT.md surface.
    2. Coherence judge with ``scope="relationships"``. INCOHERENT
       BLOCKS promotion (entry stays in shadow, marked for
       dashboard inspection).
    3. Sensitive-pattern scan with ``target_file="relationships"``.
       Hit BLOCKS promotion.
    4. On all-clear: ``store.promote(slug)`` moves the entry into
       RELATIONSHIPS.md, drops the shadow block, consumes the token.

- **Restart-recovery** (``recover_after_restart``): once at daemon
  startup, before the first tick. For every ``pending: true``
  block in shadow, re-run the trigger classifier (temperature=0)
  against the stored source turn loaded from
  ``~/.claude/projects/<encoded-workspace>/<session_uuid>.jsonl``.
  Verdict + person_slug match → re-mint the token. Mismatch or
  source turn missing → drop the entry, surface in REPORT.md.
  Cost-bounded: typically ≤5 sonnet calls per restart (one per
  pending entry from the previous tick window).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from core.brain.base import Brain

from core.coherence_judge import (
    CoherenceVerdict,
    run_coherence_judge,
)
from core.learning_review import _scan_lesson_for_sensitive_content
from core.relationships.consent import (
    ConsentError,
    ConsentToken,
    PendingTokens,
    derive_fact_ids,
    mint,
    verify_for_promotion,
)
from core.relationships.pending import (
    MAX_AMBIGUITY_REPROMPTS,
    PendingDisambiguationStore,
    PendingEntry,
)
from core.relationships.store import (
    Fact,
    Person,
    RelationshipsStore,
    StoreResult,
)
from core.relationships.triggers import (
    TriggerVerdict,
    derive_slug,
    derive_slug_with_disambiguation,
    detect as relationships_detect,
)
from core.transcripts import (
    TranscriptMessage,
    claude_session_jsonl_dir,
    iter_messages,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------
# Result dataclasses
# --------------------------------------------------------------------


@dataclass(frozen=True)
class TurnLevelResult:
    """Outcome of one ``process_user_turn`` call.

    Field meanings:

    - ``staged``: True when an ADD trigger fired and a Person was
      written to RELATIONSHIPS-SHADOW.md.
    - ``deleted``: True when a DELETE fired and a live entry was
      archived + removed.
    - ``superseded`` (3b): True when SUPERSEDE fired and the live
      Person's facts were replaced (old archived).
    - ``ambiguous`` (3b): True when the slug couldn't be resolved
      and a pending-disambiguation entry was written. The hook
      reply prompts the user to clarify.
    - ``matched``: True when the verdict's slug resolved to a live
      entry (DELETE/SUPERSEDE) or staged successfully (ADD); False
      for the friendly no-op replies.
    - ``blocked_by`` (3b): For SUPERSEDE: ``"sensitive-pattern"``
      or ``"coherence"`` when the pre-write checks refused.
    - ``reply_text``: The text the transport should send to the
      user. None when no trigger fired (message proceeds to brain
      unchanged) or when an ambiguous-drop happened silently.
    - ``verdict``: Echo of the classifier's verdict label
      (or ``"AMBIGUOUS"`` when the curator couldn't pin a slug).
    """

    staged: bool
    reply_text: str | None
    person_slug: str | None = None
    fact_count: int = 0
    verdict: str = "NONE"
    deleted: bool = False
    superseded: bool = False
    ambiguous: bool = False
    matched: bool = False
    blocked_by: str | None = None


@dataclass(frozen=True)
class PromoteResult:
    """Outcome of one tick-promote attempt for one shadow entry."""

    person_slug: str
    promoted: bool
    blocked_by: str | None = None  # "missing-token" / "coherence" / "sensitive" / None
    detail: str = ""


@dataclass(frozen=True)
class RecoveryResult:
    """Outcome of one restart-recovery attempt for one shadow entry."""

    person_slug: str
    re_minted: bool
    dropped_reason: str | None = None  # "verdict-mismatch" / "source-missing" / None


@dataclass(frozen=True)
class ApproveCandidateResult:
    """v3c outcome of one ``approve_candidate`` call."""

    ok: bool
    reply_text: str
    slug: str
    blocked_by: str | None = None  # "sensitive-pattern" / "missing_existing_qualifier" / "not-in-queue" / "slug-rejected" / "no-active-facts" / store-error
    detail: str = ""
    # Populated when blocked_by="missing_existing_qualifier"
    # (mirrors AddLiveResult so the dashboard can render the modal):
    existing_slug: str | None = None
    existing_facts: tuple[str, ...] = ()
    existing_qualifier_candidates: tuple[str, ...] = ()
    proposed_qualifier: str | None = None


@dataclass(frozen=True)
class RejectCandidateResult:
    """v3c outcome of one ``reject_candidate`` call."""

    ok: bool
    reply_text: str
    slug: str


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_date(dt: datetime) -> str:
    return dt.date().isoformat()


def _short_session(session_uuid: str) -> str:
    return session_uuid[:8] if len(session_uuid) >= 8 else session_uuid


def _load_source_turn(
    workspace: Path, session_uuid: str, turn_index: int
) -> tuple[TranscriptMessage | None, list[TranscriptMessage]]:
    """Return (the user turn at ``turn_index``, full message list).

    ``turn_index`` is 1-indexed across user-role messages only
    (mirrors how the dryrun and turn-level entry count). On
    file-not-found or no matching turn, returns (None, []).
    """
    pdir = claude_session_jsonl_dir(workspace)
    jsonl = pdir / f"{session_uuid}.jsonl"
    if not jsonl.exists():
        return None, []
    messages = list(iter_messages(jsonl))
    user_count = 0
    for msg in messages:
        if msg.role == "user":
            user_count += 1
            if user_count == turn_index:
                return msg, messages
    return None, messages


def _person_from_trigger(
    *,
    verdict: TriggerVerdict,
    session_uuid: str,
    turn_index: int,
) -> Person:
    """Construct the staged Person for a triggered ADD turn."""
    name = verdict.person_name or "Unknown"
    slug = derive_slug(name)
    today = _iso_date(_utc_now())
    short = _short_session(session_uuid)
    facts = tuple(
        Fact(
            text=f,
            confirmed_date=today,
            source_session_short=short,
            staged=True,
        )
        for f in verdict.facts
    )
    return Person(
        slug=slug,
        display_name=name,
        relationship=verdict.qualifier or "(unspecified)",
        qualifier=verdict.qualifier,
        last_confirmed=today,
        source_session=session_uuid,
        facts=facts,
        pending=True,
        staged_at=_utc_now().isoformat(),
        source_turn_index=turn_index,
    )


# --------------------------------------------------------------------
# RelationshipsCurator
# --------------------------------------------------------------------


@dataclass
class _DropEvent:
    """One row for the per-tick REPORT.md surface."""

    person_slug: str
    reason: str
    detail: str


class RelationshipsCurator:
    """Three-entry-point curator for RELATIONSHIPS.md.

    One instance per LearningController. Holds the in-memory
    PendingTokens registry — daemon restart drops it, recovery is
    handled by ``recover_after_restart``.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        store: RelationshipsStore | None = None,
        pending_tokens: PendingTokens | None = None,
        classifier_call: (
            Callable[..., Awaitable[TriggerVerdict]] | None
        ) = None,
        coherence_judge: (
            Callable[..., CoherenceVerdict] | None
        ) = None,
        pending_disambiguation: PendingDisambiguationStore | None = None,
        candidate_store: "RelationshipsCandidateStore | None" = None,
        brain: "Brain | None" = None,
    ) -> None:
        # Phase B: brain is the aux-spawn surface. Threaded into the
        # extractor and the coherence judge. Tests that don't reach
        # the spawn path can leave it None — we fall back to a
        # ``BrainNull()`` so the cached callable still has something
        # to invoke when a downstream code path inadvertently asks
        # for a real spawn.
        from core.brain.null import BrainNull

        self._brain: "Brain" = brain or BrainNull()
        self._workspace = workspace
        self._store = store or RelationshipsStore(workspace)
        self._tokens = pending_tokens or PendingTokens()
        # Test seams. Production passes None for both.
        self._classifier_call = classifier_call
        self._coherence_judge = coherence_judge or run_coherence_judge
        self._drop_events: list[_DropEvent] = []
        self._has_recovered = False
        # v3b Day 3b: per-chat pending-disambiguation state. Persisted
        # to ``<workspace>/.vexis/relationships-pending.json`` with a
        # 5-min TTL so a daemon restart between two turns can still
        # recover. Loaded lazily on first access (no I/O on init for
        # tests that never use it).
        self._pending_disambig = pending_disambiguation or PendingDisambiguationStore(
            workspace=workspace,
        )
        # v3c Day 4a: silent-extraction candidate queue. Owned by
        # the curator so the extractor (curator-driven) and the
        # approval surface (slash-command + dashboard) share one
        # disk-backed store under one fcntl lock.
        from core.relationships.candidate_store import (
            RelationshipsCandidateStore,
            candidates_path,
        )
        self._candidate_store = candidate_store or RelationshipsCandidateStore(
            candidates_path(workspace),
        )
        # v3b Day 3a-3b + v3c Day 4a: per-curator counters surfaced
        # in REPORT.md and consumed by the dashboard's telemetry
        # hook. Reset only on process restart.
        self._counters: dict[str, int] = {
            # 3a
            "add_staged": 0,
            "delete_executed": 0,
            "delete_missing": 0,
            "cursor_collision": 0,
            "hook_errors": 0,
            # 3b
            "supersede_executed": 0,
            "supersede_missing": 0,
            "supersede_blocked_sensitive": 0,
            "supersede_blocked_coherence": 0,
            "ambiguous_emitted": 0,
            "ambiguous_resolved": 0,
            "ambiguous_dropped_unresolved": 0,
            "ambiguous_dropped_unrelated": 0,
            "disambiguation_back_edit": 0,
            "restore_executed": 0,
            "restore_missing": 0,
            "restore_collision": 0,
            # 4a (silent extraction)
            "extractor_runs": 0,
            "extractor_errors": 0,
            "extractor_facts_emitted": 0,
            "extractor_facts_dropped_sensitive": 0,
            "extractor_facts_dropped_dedup": 0,
            "candidates_queued": 0,
            "candidates_eligible": 0,
            "candidates_approved": 0,
            "candidates_rejected": 0,
            "candidates_expired": 0,
            "approve_blocked_sensitive": 0,
            "approve_blocked_missing_qualifier": 0,
        }

    # ----- properties for telemetry / tests -----

    @property
    def store(self) -> RelationshipsStore:
        return self._store

    @property
    def tokens(self) -> PendingTokens:
        return self._tokens

    @property
    def candidate_store(self) -> "RelationshipsCandidateStore":
        return self._candidate_store

    def drain_drop_events(self) -> list[_DropEvent]:
        events = list(self._drop_events)
        self._drop_events.clear()
        return events

    @property
    def counters(self) -> dict[str, int]:
        """Snapshot of per-curator telemetry counters. Live dict —
        callers should not mutate."""
        return self._counters

    def increment_counter(self, name: str, by: int = 1) -> None:
        """Bump a counter by ``by``. Unknown names are ignored
        (forwards-compat for transport-side increments like
        ``cursor_collision`` / ``hook_errors`` that originate
        outside the curator)."""
        if name in self._counters:
            self._counters[name] += by

    # ----- entry point 1: turn-level -----

    async def process_user_turn(
        self,
        text: str,
        *,
        session_uuid: str,
        turn_index: int,
        chat_id: int | None = None,
    ) -> TurnLevelResult:
        """Detect → resolve slug → mint → stage / archive → reply.

        ``chat_id`` is used by 3b's pending-disambiguation flow:
        when present and the chat has a non-expired pending
        AMBIGUOUS entry, the curator merges the original turn
        with this turn's text and re-runs classification before
        falling through to fresh detection. Day 2/3a tests pass
        ``chat_id=None`` and bypass the pending flow entirely.
        """
        # 3b: try to resolve a pending disambiguation BEFORE
        # classifying this turn fresh. The pending flow uses the
        # ORIGINAL turn's session_uuid/turn_index as the consent
        # locus, so passing this turn's session/turn_index here
        # is just for fall-through (when no pending applies or it
        # gives up).
        if chat_id is not None:
            pending = self._pending_disambig.get(chat_id)
            if pending is not None:
                resolved = await self._resolve_pending(
                    pending=pending, follow_up_text=text, chat_id=chat_id,
                )
                if resolved is not None:
                    return resolved

        verdict = await relationships_detect(
            text,
            role="user",
            session_uuid=session_uuid,
            turn_index=turn_index,
            workspace=self._workspace,
            classifier_call=self._classifier_call,
        )
        return await self._dispatch_verdict(
            verdict=verdict,
            session_uuid=session_uuid,
            turn_index=turn_index,
            original_text=text,
            chat_id=chat_id,
        )

    async def _dispatch_verdict(
        self,
        *,
        verdict: TriggerVerdict,
        session_uuid: str,
        turn_index: int,
        original_text: str,
        chat_id: int | None,
    ) -> TurnLevelResult:
        """Route a fresh classifier verdict to its branch. Shared by
        ``process_user_turn`` (fresh path) and ``_resolve_pending``
        (re-classification of the merged original+follow-up text).
        """
        if verdict.verdict == "NONE":
            return TurnLevelResult(staged=False, reply_text=None)

        if verdict.verdict == "SUPERSEDE":
            return await self._process_supersede(
                verdict=verdict,
                session_uuid=session_uuid,
                turn_index=turn_index,
                original_text=original_text,
                chat_id=chat_id,
            )

        if verdict.verdict == "DELETE":
            return self._process_delete(
                verdict=verdict,
                session_uuid=session_uuid,
                turn_index=turn_index,
                original_text=original_text,
                chat_id=chat_id,
            )

        return self._process_add(
            verdict=verdict,
            session_uuid=session_uuid,
            turn_index=turn_index,
            original_text=original_text,
            chat_id=chat_id,
        )

    def _process_add(
        self,
        *,
        verdict: TriggerVerdict,
        session_uuid: str,
        turn_index: int,
        original_text: str,
        chat_id: int | None,
    ) -> TurnLevelResult:
        if not verdict.person_name or not verdict.facts:
            log.warning(
                "relationships.process_user_turn: ADD verdict missing "
                "person_name or facts (sess=%s turn=%s)",
                session_uuid, turn_index,
            )
            return TurnLevelResult(staged=False, reply_text=None)

        # 3b: slug resolution with disambiguation back-edit.
        # If utterance carries a qualifier and a bare-slug live
        # entry exists with a *different* qualifier in YAML, we
        # back-edit the bare-slug entry to its qualified form
        # before staging the new entry.
        resolution = self._resolve_add_slug(verdict)
        if resolution.kind == "ambiguous":
            return self._emit_ambiguous(
                original_verdict="ADD",
                original_text=original_text,
                candidate_slugs=resolution.candidate_slugs,
                session_uuid=session_uuid,
                turn_index=turn_index,
                person_name=verdict.person_name,
                chat_id=chat_id,
            )
        if resolution.kind == "back-edit":
            self._perform_back_edit(
                old_slug=resolution.back_edit_old_slug or "",
                new_slug=resolution.back_edit_new_slug or "",
                new_qualifier=resolution.back_edit_new_qualifier,
            )

        slug = resolution.slug
        token = mint(
            session_uuid=session_uuid,
            turn_index=turn_index,
            classifier_verdict="ADD",
            person_slug=slug,
            facts=list(verdict.facts),
            action="add",
        )
        self._tokens.add(token)
        person = _person_from_trigger(
            verdict=verdict,
            session_uuid=session_uuid,
            turn_index=turn_index,
        )
        # Override the staged Person's slug with the resolved one
        # so the qualified form ("sarah-coworker") lands in shadow,
        # not the bare "sarah".
        if person.slug != slug:
            from dataclasses import replace as _dc_replace
            person = _dc_replace(person, slug=slug)
        self._store.stage(person, token=token)
        n = len(verdict.facts)
        word = "fact" if n == 1 else "facts"
        reply = (
            f"Got it — I've staged {n} {word} about "
            f"{verdict.person_name} for the relationships file. "
            f"It'll land after the next audit pass."
        )
        self._counters["add_staged"] += 1
        return TurnLevelResult(
            staged=True,
            reply_text=reply,
            person_slug=slug,
            fact_count=n,
            verdict="ADD",
            matched=True,
        )

    def _process_delete(
        self,
        *,
        verdict: TriggerVerdict,
        session_uuid: str,
        turn_index: int,
        original_text: str = "",
        chat_id: int | None = None,
    ) -> TurnLevelResult:
        """Synchronous DELETE — looks up slug in RELATIONSHIPS.md,
        mints a delete-action token, archives the H2 block, removes
        it from live. Missing slug → friendly no-op (no token, no
        write, no archive).

        3b: when ``utterance qualifier == None`` AND multiple
        slug variants exist for the bare base ("forget Sarah" with
        both ``sarah-friend`` and ``sarah-coworker`` in live), emits
        AMBIGUOUS instead of guessing. With a qualifier in
        utterance, the qualified slug is tried first; if missing
        but the bare slug exists, falls through to the bare slug.

        DELETE skips the shadow / tick-promote pipeline entirely.
        It does NOT invoke the coherence judge (no claim to ground
        — we're removing information, not asserting it) and does
        NOT invoke the sensitive-pattern scanner (deletion can't
        introduce new sensitive content).
        """
        if not verdict.person_name:
            log.warning(
                "relationships.process_user_turn: DELETE verdict missing "
                "person_name (sess=%s turn=%s)",
                session_uuid, turn_index,
            )
            return TurnLevelResult(
                staged=False, reply_text=None, verdict="DELETE",
            )
        resolution = self._resolve_existing_slug(verdict)
        if resolution.kind == "ambiguous":
            return self._emit_ambiguous(
                original_verdict="DELETE",
                original_text=original_text,
                candidate_slugs=resolution.candidate_slugs,
                session_uuid=session_uuid,
                turn_index=turn_index,
                person_name=verdict.person_name,
                chat_id=chat_id,
            )
        slug = resolution.slug
        live_match = self._store.get_live(slug)
        if live_match is None:
            self._counters["delete_missing"] += 1
            return TurnLevelResult(
                staged=False,
                deleted=False,
                matched=False,
                reply_text=(
                    f"I don't have anything on {verdict.person_name} to forget."
                ),
                person_slug=slug,
                verdict="DELETE",
            )
        token = mint(
            session_uuid=session_uuid,
            turn_index=turn_index,
            classifier_verdict="DELETE",
            person_slug=slug,
            facts=[],
            action="delete",
        )
        # Track the token in PendingTokens for symmetry with ADD —
        # DELETE is synchronous so the token isn't strictly needed
        # post-execution, but the registry record helps audit ("a
        # delete fired at sess X turn N for slug Y") and keeps the
        # mint surface uniform. Consume immediately on success.
        self._tokens.add(token)
        removed_date = _iso_date(_utc_now())
        result = self._store.delete_live(
            slug, token=token, removed_date=removed_date,
        )
        if not result.ok:
            # delete_live already verified the token; an ok=False
            # here means the slug was found at lookup-time but
            # vanished by store-time (raced with another call). Drop
            # the token, surface as missing.
            self._tokens.consume(
                session_uuid=session_uuid,
                turn_index=turn_index,
                person_slug=slug,
            )
            self._counters["delete_missing"] += 1
            return TurnLevelResult(
                staged=False,
                deleted=False,
                matched=False,
                reply_text=(
                    f"I don't have anything on {verdict.person_name} to forget."
                ),
                person_slug=slug,
                verdict="DELETE",
            )
        # Consume the token now that the live + archive write succeeded.
        self._tokens.consume(
            session_uuid=session_uuid,
            turn_index=turn_index,
            person_slug=slug,
        )
        self._counters["delete_executed"] += 1
        return TurnLevelResult(
            staged=False,
            deleted=True,
            matched=True,
            reply_text=(
                f"Forgot what I had on {verdict.person_name}. "
                f"Archived for restore."
            ),
            person_slug=slug,
            verdict="DELETE",
        )

    # ----- entry point 1b (3b): SUPERSEDE -----

    async def _process_supersede(
        self,
        *,
        verdict: TriggerVerdict,
        session_uuid: str,
        turn_index: int,
        original_text: str,
        chat_id: int | None,
    ) -> TurnLevelResult:
        """Synchronous SUPERSEDE — replaces the fact set for an
        existing live Person. Sensitive scanner + coherence judge
        run BEFORE the live rewrite; either INCOHERENT or a
        sensitive-pattern hit refuses with no mutation.

        The classifier extracts the new fact set; the live entry's
        H2 + YAML frontmatter is preserved (with ``last_confirmed``
        bumped and ``source_session`` updated to this turn). Old
        facts are archived under a ``## SUPERSEDED <date>`` block
        with per-fact ``[superseded ... by sess:...]`` provenance.
        """
        if not verdict.person_name or not verdict.facts:
            log.warning(
                "relationships.process_user_turn: SUPERSEDE verdict missing "
                "person_name or facts (sess=%s turn=%s)",
                session_uuid, turn_index,
            )
            return TurnLevelResult(
                staged=False, reply_text=None, verdict="SUPERSEDE",
            )
        resolution = self._resolve_existing_slug(verdict)
        if resolution.kind == "ambiguous":
            return self._emit_ambiguous(
                original_verdict="SUPERSEDE",
                original_text=original_text,
                candidate_slugs=resolution.candidate_slugs,
                session_uuid=session_uuid,
                turn_index=turn_index,
                person_name=verdict.person_name,
                chat_id=chat_id,
            )
        slug = resolution.slug
        live_match = self._store.get_live(slug)
        if live_match is None:
            self._counters["supersede_missing"] += 1
            return TurnLevelResult(
                staged=False,
                superseded=False,
                matched=False,
                reply_text=(
                    f"I don't have anything on {verdict.person_name} to update."
                ),
                person_slug=slug,
                verdict="SUPERSEDE",
            )
        # Sensitive-pattern scan over the JOINED new facts so a
        # multi-fact replacement that buries one bad fact in two
        # benign ones still trips the gate.
        joined = "; ".join(verdict.facts)
        hit = _scan_lesson_for_sensitive_content(
            joined,
            scope=f"relationships:{slug}",
            target_file="relationships",
        )
        if hit:
            self._counters["supersede_blocked_sensitive"] += 1
            return TurnLevelResult(
                staged=False,
                superseded=False,
                matched=True,
                blocked_by="sensitive-pattern",
                reply_text=(
                    f"Couldn't update {verdict.person_name} — "
                    f"that includes content I can't store."
                ),
                person_slug=slug,
                verdict="SUPERSEDE",
            )
        # Coherence judge over the new facts vs the source turn.
        # Same bar as ADD's tick-promote — INCOHERENT blocks the
        # write because we're about to overwrite live state.
        verdict_judge = self._run_supersede_judge(
            slug=slug,
            new_facts=list(verdict.facts),
            session_uuid=session_uuid,
            turn_index=turn_index,
        )
        if verdict_judge is not None and verdict_judge.verdict == "INCOHERENT":
            self._counters["supersede_blocked_coherence"] += 1
            return TurnLevelResult(
                staged=False,
                superseded=False,
                matched=True,
                blocked_by="coherence",
                reply_text=(
                    f"Couldn't update {verdict.person_name} — "
                    f"that doesn't match what was just said."
                ),
                person_slug=slug,
                verdict="SUPERSEDE",
            )
        # Mint, write, consume.
        token = mint(
            session_uuid=session_uuid,
            turn_index=turn_index,
            classifier_verdict="SUPERSEDE",
            person_slug=slug,
            facts=list(verdict.facts),
            action="supersede",
        )
        self._tokens.add(token)
        today = _iso_date(_utc_now())
        store_res = self._store.supersede_live(
            slug,
            token=token,
            new_facts=list(verdict.facts),
            new_session_uuid=session_uuid,
            new_session_short=_short_session(session_uuid),
            superseded_date=today,
        )
        if not store_res.ok:
            self._tokens.consume(
                session_uuid=session_uuid,
                turn_index=turn_index,
                person_slug=slug,
            )
            self._counters["supersede_missing"] += 1
            return TurnLevelResult(
                staged=False,
                superseded=False,
                matched=False,
                reply_text=(
                    f"I don't have anything on {verdict.person_name} to update."
                ),
                person_slug=slug,
                verdict="SUPERSEDE",
            )
        self._tokens.consume(
            session_uuid=session_uuid,
            turn_index=turn_index,
            person_slug=slug,
        )
        self._counters["supersede_executed"] += 1
        return TurnLevelResult(
            staged=False,
            superseded=True,
            matched=True,
            reply_text=(
                f"Updated {verdict.person_name} — replaced the old facts "
                f"with what you just said. Old version archived for restore."
            ),
            person_slug=slug,
            verdict="SUPERSEDE",
        )

    def _run_supersede_judge(
        self,
        *,
        slug: str,
        new_facts: list[str],
        session_uuid: str,
        turn_index: int,
    ) -> CoherenceVerdict | None:
        """Run the coherence judge over the new facts vs the
        source turn. Returns None when the source turn isn't
        loadable (treated as advisory-skip — SUPERSEDE proceeds).
        """
        source_msg, messages = _load_source_turn(
            self._workspace, session_uuid, turn_index,
        )
        if source_msg is None:
            log.info(
                "relationships.supersede: source turn unloadable "
                "(sess=%s turn=%s); skipping judge",
                session_uuid, turn_index,
            )
            return None
        synthetic_lesson = {
            "class": "RELATIONSHIPS",
            "lesson": "; ".join(new_facts),
            "evidence": source_msg.text,
            "scope": "relationships",
        }
        try:
            return self._coherence_judge(
                self._workspace, synthetic_lesson, messages, self._brain,
            )
        except Exception as exc:
            log.warning(
                "relationships.supersede.coherence_judge_unexpected_error: %s",
                exc, exc_info=True,
            )
            return None

    # ----- 3b slug resolution (ADD with disambiguation, DELETE/SUPERSEDE matching) -----

    @dataclass(frozen=True)
    class _SlugResolution:
        kind: str  # "single" | "ambiguous" | "back-edit"
        slug: str
        candidate_slugs: tuple[str, ...] = ()
        back_edit_old_slug: str | None = None
        back_edit_new_slug: str | None = None
        back_edit_new_qualifier: str | None = None

    def _resolve_add_slug(
        self, verdict: TriggerVerdict
    ) -> "_SlugResolution":
        """Resolve the ADD-write slug, deciding whether a
        disambiguation back-edit is needed.

        Per scoping doc §3.5: qualifier-in-utterance only routes
        to a qualified slug when a collision exists. Day 2/3a
        shape preserved when no collision is detected — the
        utterance qualifier still lands in YAML (via
        ``Person.qualifier``), but the slug stays bare so existing
        callers (and Day 2 tests) see ``"sarah"``.

        Cases (with `verdict.qualifier` as the utterance qualifier):

        1. Qualifier present, qualified slug already in live
           (``sarah-coworker`` exists): use the qualified slug.
           Re-ADD on the same person.
        2. Qualifier present, bare slug exists with a YAML qualifier
           DIFFERENT from utterance: back-edit ``sarah`` →
           ``sarah-{old-qual}`` AND stage new ``sarah-{new-qual}``.
        3. Qualifier present, bare slug exists with the SAME YAML
           qualifier as utterance: re-ADD on the bare slug (it IS
           this person; no need to rename).
        4. Qualifier present, bare slug exists WITHOUT a YAML
           qualifier: AMBIGUOUS — we can't auto-name the existing
           entry's qualified form. User must clarify.
        5. Qualifier absent: stage bare slug.
        6. Qualifier present + no collision at all: stage bare slug
           (Day 2/3a shape — qualifier lands in YAML, not in slug).
        """
        base_slug = derive_slug(verdict.person_name)
        if not verdict.qualifier:
            return RelationshipsCurator._SlugResolution(
                kind="single", slug=base_slug,
            )
        qualified_slug = derive_slug_with_disambiguation(
            verdict.person_name, verdict.qualifier,
        )
        live = self._store.list_live()
        # Case 1: qualified slug already exists — re-ADD on it.
        if any(p.slug == qualified_slug for p in live):
            return RelationshipsCurator._SlugResolution(
                kind="single", slug=qualified_slug,
            )
        bare_match = next((p for p in live if p.slug == base_slug), None)
        if bare_match is None:
            # Case 6: no collision — Day 2/3a shape (bare slug;
            # YAML carries the qualifier).
            return RelationshipsCurator._SlugResolution(
                kind="single", slug=base_slug,
            )
        existing_qualifier = bare_match.qualifier
        # Case 4: bare exists but no YAML qualifier — AMBIGUOUS.
        if not existing_qualifier:
            return RelationshipsCurator._SlugResolution(
                kind="ambiguous",
                slug=qualified_slug,
                candidate_slugs=(base_slug,),
            )
        # Case 3: same qualifier — re-ADD on the bare slug.
        if existing_qualifier.strip().lower() == verdict.qualifier.strip().lower():
            return RelationshipsCurator._SlugResolution(
                kind="single", slug=base_slug,
            )
        # Case 2: bare has DIFFERENT YAML qualifier — back-edit
        # the bare slug to its qualified form before staging the
        # new entry.
        existing_qualified = derive_slug_with_disambiguation(
            bare_match.display_name, existing_qualifier,
        )
        return RelationshipsCurator._SlugResolution(
            kind="back-edit",
            slug=qualified_slug,
            back_edit_old_slug=base_slug,
            back_edit_new_slug=existing_qualified,
            back_edit_new_qualifier=existing_qualifier,
        )

    def _resolve_existing_slug(
        self, verdict: TriggerVerdict
    ) -> "_SlugResolution":
        """Resolve the slug for DELETE/SUPERSEDE.

        Cases:

        1. Qualifier present + qualified slug exists: use it.
        2. Qualifier present + qualified slug missing + bare slug
           exists: AMBIGUOUS (utterance says "forget my coworker
           Sarah" but live has ``sarah`` not ``sarah-coworker`` —
           we can't tell if the bare slug IS the coworker).
        3. Qualifier absent + multiple ``base*`` slugs in live
           (``sarah-friend`` + ``sarah-coworker``): AMBIGUOUS.
        4. Qualifier absent + exactly one match: use it.
        5. No match anywhere: return base_slug (caller's missing-
           slug branch will take the friendly no-op path).
        """
        base_slug = derive_slug(verdict.person_name)
        live = self._store.list_live()
        live_slugs = [p.slug for p in live]
        if verdict.qualifier:
            qualified_slug = derive_slug_with_disambiguation(
                verdict.person_name, verdict.qualifier,
            )
            if qualified_slug in live_slugs:
                return RelationshipsCurator._SlugResolution(
                    kind="single", slug=qualified_slug,
                )
            if base_slug in live_slugs:
                return RelationshipsCurator._SlugResolution(
                    kind="ambiguous",
                    slug=qualified_slug,
                    candidate_slugs=(base_slug,),
                )
            return RelationshipsCurator._SlugResolution(
                kind="single", slug=qualified_slug,
            )
        # No qualifier in utterance — find variants on the base.
        variants = [
            s for s in live_slugs
            if s == base_slug or s.startswith(base_slug + "-")
        ]
        if len(variants) > 1:
            return RelationshipsCurator._SlugResolution(
                kind="ambiguous",
                slug=base_slug,
                candidate_slugs=tuple(variants),
            )
        if len(variants) == 1:
            return RelationshipsCurator._SlugResolution(
                kind="single", slug=variants[0],
            )
        return RelationshipsCurator._SlugResolution(
            kind="single", slug=base_slug,
        )

    def _perform_back_edit(
        self,
        *,
        old_slug: str,
        new_slug: str,
        new_qualifier: str | None,
    ) -> None:
        """Rename ``old_slug`` to ``new_slug`` in the live file and
        record a ``[disambiguated ...]`` provenance line in the
        archive. Best-effort — failures are logged but don't block
        the ADD itself (the new entry can still be staged under
        the qualified slug; the live file just keeps the bare
        ``sarah`` until manual cleanup)."""
        try:
            res = self._store.rename_live_slug(
                old_slug=old_slug,
                new_slug=new_slug,
                new_qualifier=new_qualifier,
                disambiguated_date=_iso_date(_utc_now()),
            )
            if res.ok:
                self._counters["disambiguation_back_edit"] += 1
            else:
                log.warning(
                    "relationships.back_edit refused: %s", res.message,
                )
        except Exception:
            log.exception("relationships.back_edit raised")

    # ----- 3b AMBIGUOUS pending-disambiguation -----

    def _emit_ambiguous(
        self,
        *,
        original_verdict: str,
        original_text: str,
        candidate_slugs: tuple[str, ...],
        session_uuid: str,
        turn_index: int,
        person_name: str,
        chat_id: int | None,
    ) -> TurnLevelResult:
        """Write a pending-disambiguation entry and return the
        AMBIGUOUS reply for the transport to send.

        When ``chat_id`` is None (test or non-Telegram path), no
        pending entry is written — we just return a result with
        ``ambiguous=True`` and the reply, which the caller may
        ignore. This keeps the curator API usable without a
        Telegram chat scope for unit tests.
        """
        candidates_text = self._format_candidate_choices(
            person_name, candidate_slugs,
        )
        reply = (
            f"Which {person_name}? You've told me about "
            f"{candidates_text}."
        )
        if chat_id is not None:
            self._pending_disambig.put(
                chat_id=chat_id,
                original_verdict=original_verdict,
                original_text=original_text,
                candidate_slugs=list(candidate_slugs),
                session_uuid=session_uuid,
                turn_index=turn_index,
            )
        self._counters["ambiguous_emitted"] += 1
        return TurnLevelResult(
            staged=False,
            ambiguous=True,
            matched=False,
            reply_text=reply,
            verdict="AMBIGUOUS",
        )

    def _format_candidate_choices(
        self, person_name: str, candidate_slugs: tuple[str, ...]
    ) -> str:
        """Render "Sarah (work) and Sarah (sister)" from the live
        entries matching the candidate slugs. Falls back to bare
        slug names if a candidate isn't in live anymore."""
        live = self._store.list_live()
        labels: list[str] = []
        for slug in candidate_slugs:
            match = next((p for p in live if p.slug == slug), None)
            if match is None:
                labels.append(f"{person_name} ({slug})")
                continue
            qual = match.qualifier or slug
            labels.append(f"{match.display_name} ({qual})")
        if not labels:
            labels = [person_name]
        if len(labels) == 1:
            return labels[0]
        if len(labels) == 2:
            return f"{labels[0]} and {labels[1]}"
        return ", ".join(labels[:-1]) + f", and {labels[-1]}"

    async def _resolve_pending(
        self,
        *,
        pending: PendingEntry,
        follow_up_text: str,
        chat_id: int,
    ) -> TurnLevelResult | None:
        """Try to resolve a pending disambiguation by merging the
        original turn's text with this follow-up turn's text and
        re-running classification.

        Outcomes:

        - Merge classifies clearly (qualified person+qualifier or
          a single ``sarah-*`` slug match): consume the pending
          entry, dispatch the original verdict using the original
          turn's session_uuid/turn_index. Return the dispatch's
          result.
        - Merge still AMBIGUOUS or the user's reply doesn't help:
          bump the pending entry's count. If count exceeds
          ``MAX_AMBIGUITY_REPROMPTS``, drop silently with a logged
          warning. Otherwise re-emit the AMBIGUOUS reply (TTL
          refreshed by ``put`` inside ``bump_ambiguity``).
        - Merge classifies as NONE (the user said something
          unrelated): drop the pending entry with a logged warning.
          Return None so the normal hook flow runs against
          ``follow_up_text`` alone.
        """
        merged = f"{pending.original_text} {follow_up_text}".strip()
        merge_verdict = await relationships_detect(
            merged,
            role="user",
            session_uuid=pending.session_uuid,
            turn_index=pending.turn_index,
            workspace=self._workspace,
            classifier_call=self._classifier_call,
        )
        if merge_verdict.verdict == "NONE":
            # Unrelated message — drop pending silently, fall through.
            self._pending_disambig.consume(chat_id)
            self._counters["ambiguous_dropped_unrelated"] += 1
            log.info(
                "relationships.ambiguous: pending entry for chat %s "
                "dropped after unrelated follow-up",
                chat_id,
            )
            return None

        # Build a hypothetical resolution from the merge classifier's
        # output. If the merge picked up a qualifier we couldn't see
        # the first time, that's the disambiguator.
        if merge_verdict.verdict == pending.original_verdict:
            # Try resolution against the LIVE store. If still
            # ambiguous → bump-or-drop. If clear → fire the original
            # verdict using the pending entry's session_uuid/turn.
            resolution = (
                self._resolve_add_slug(merge_verdict)
                if merge_verdict.verdict == "ADD"
                else self._resolve_existing_slug(merge_verdict)
            )
            if resolution.kind != "ambiguous":
                self._pending_disambig.consume(chat_id)
                self._counters["ambiguous_resolved"] += 1
                # Re-emit through _dispatch_verdict using the ORIGINAL
                # turn's locus. The merged verdict carries the right
                # person/qualifier/facts; we just need the consent
                # locus to match the moment the user originally
                # gave consent.
                return await self._dispatch_verdict(
                    verdict=merge_verdict,
                    session_uuid=pending.session_uuid,
                    turn_index=pending.turn_index,
                    original_text=pending.original_text,
                    chat_id=None,  # suppress nested pending creation
                )

        # Still ambiguous — bump or drop.
        bumped = self._pending_disambig.bump_ambiguity(chat_id)
        if bumped is None:
            return None
        if bumped.ambiguity_count > MAX_AMBIGUITY_REPROMPTS:
            self._pending_disambig.consume(chat_id)
            self._counters["ambiguous_dropped_unresolved"] += 1
            log.warning(
                "relationships.ambiguous: pending entry for chat %s "
                "dropped after %d unresolved prompts",
                chat_id, bumped.ambiguity_count,
            )
            return TurnLevelResult(
                staged=False, reply_text=None, verdict="NONE",
            )
        candidates_text = self._format_candidate_choices(
            self._person_name_from_slugs(bumped.candidate_slugs),
            bumped.candidate_slugs,
        )
        reply = (
            f"Still not sure which one — "
            f"{candidates_text}?"
        )
        return TurnLevelResult(
            staged=False,
            ambiguous=True,
            matched=False,
            reply_text=reply,
            verdict="AMBIGUOUS",
        )

    def _person_name_from_slugs(
        self, candidate_slugs: tuple[str, ...]
    ) -> str:
        """Best-effort recovery of the person's first name from the
        live entries matching the candidate slugs (since the pending
        entry stores only slugs)."""
        live = self._store.list_live()
        for slug in candidate_slugs:
            match = next((p for p in live if p.slug == slug), None)
            if match is not None:
                return match.display_name
        # Fall back to the slug's first segment.
        if candidate_slugs:
            first = candidate_slugs[0].split("-")[0]
            return first.capitalize()
        return "them"

    # ----- entry point 3 (3b): user-initiated restore -----

    def restore(self, slug: str) -> TurnLevelResult:
        """Reverse a deletion. Token-free — caller is the
        ``/learning relationships-restore <slug>`` slash command,
        which is auth-gated by the Telegram allow-list.

        ``ok=False`` shapes from the store map to:
        - ``"slug-already-live"`` → restore_collision counter +
          "<Name> is already in your relationships..." reply.
        - ``"no-archive"`` / ``"no-removed-block"`` →
          restore_missing counter + "Nothing to restore..." reply.
        - any other store error → restore_missing + same reply.
        """
        res = self._store.restore_from_archive(slug)
        if res.ok:
            self._counters["restore_executed"] += 1
            display = self._display_for_slug_after_restore(slug)
            return TurnLevelResult(
                staged=False,
                matched=True,
                reply_text=f"Restored {display} from archive.",
                person_slug=slug,
                verdict="RESTORE",
            )
        if res.message == "slug-already-live":
            self._counters["restore_collision"] += 1
            display = self._display_for_slug_after_restore(slug)
            return TurnLevelResult(
                staged=False,
                matched=False,
                reply_text=(
                    f"{display} is already in your relationships — "
                    f"restore would overwrite. "
                    f"Run /learning relationships-forget {slug} first."
                ),
                person_slug=slug,
                verdict="RESTORE",
            )
        # Either no-archive, no-removed-block, or unparseable —
        # all collapse to the missing path.
        self._counters["restore_missing"] += 1
        return TurnLevelResult(
            staged=False,
            matched=False,
            reply_text=f"Nothing to restore for {slug}.",
            person_slug=slug,
            verdict="RESTORE",
        )

    # ----- v3c silent-queue entry points -----

    def approve_candidate(
        self,
        slug: str,
        *,
        fact_ids: list[str] | None = None,
        qualifier: str | None = None,
        token_session_uuid: str = "approve",
        token_turn_index: int = 1,
    ) -> "ApproveCandidateResult":
        """Promote queued candidate facts to live RELATIONSHIPS.md.

        Token semantics: the approval click IS the consent moment;
        the token's ``session_uuid`` defaults to the literal
        ``"approve"`` to disambiguate from extraction-time session
        UUIDs in audit logs. Caller (slash-command / dashboard)
        may override to attach a more informative attribution.

        ``fact_ids=None`` approves every active (non-rejected,
        non-yet-approved) fact under the slug. ``qualifier``
        overrides the candidate's current qualifier guess; when
        both are non-null the slug becomes
        ``derive_slug_with_disambiguation(display_name, qualifier)``
        for the live write.
        """
        from core.relationships.candidate_store import CandidateView
        from core.relationships.consent import mint as _mint
        from core.relationships.triggers import (
            derive_slug_with_disambiguation,
        )

        candidate = self._candidate_store.get(slug)
        if candidate is None:
            return ApproveCandidateResult(
                ok=False,
                blocked_by="not-in-queue",
                reply_text=f"No candidate in the queue for {slug}.",
                slug=slug,
            )
        if candidate.rejected_at is not None:
            return ApproveCandidateResult(
                ok=False,
                blocked_by="slug-rejected",
                reply_text=(
                    f"Candidate {slug} is rejected. "
                    f"Restore it first via the dashboard."
                ),
                slug=slug,
            )
        # Resolve the fact set + qualifier.
        active = candidate.active_facts()
        if fact_ids is not None:
            id_set = set(fact_ids)
            active = [f for f in active if f.fact_id in id_set]
        if not active:
            return ApproveCandidateResult(
                ok=False,
                blocked_by="no-active-facts",
                reply_text=f"No facts to approve for {slug}.",
                slug=slug,
            )
        chosen_qualifier = (
            qualifier if qualifier is not None
            else (
                candidate.qualifier_candidates[-1]
                if candidate.qualifier_candidates
                else None
            )
        )
        target_slug = (
            derive_slug_with_disambiguation(
                candidate.display_name, chosen_qualifier,
            )
            if chosen_qualifier
            else slug
        )
        today = _iso_date(_utc_now())
        short = _short_session(token_session_uuid)
        person = Person(
            slug=target_slug,
            display_name=candidate.display_name,
            relationship=chosen_qualifier or "(unspecified)",
            qualifier=chosen_qualifier,
            last_confirmed=today,
            source_session=token_session_uuid,
            facts=tuple(
                Fact(
                    text=f.text,
                    confirmed_date=today,
                    source_session_short=short,
                    staged=False,
                )
                for f in active
            ),
        )
        token = _mint(
            session_uuid=token_session_uuid,
            turn_index=token_turn_index,
            classifier_verdict="ADD",
            person_slug=target_slug,
            facts=[f.text for f in active],
            action="approve",
        )
        add_res = self._store.add_live(person, token=token)
        if not add_res.ok:
            if add_res.blocked_by == "sensitive-pattern":
                self._counters["approve_blocked_sensitive"] += 1
                return ApproveCandidateResult(
                    ok=False,
                    blocked_by="sensitive-pattern",
                    reply_text=(
                        f"Couldn't approve {candidate.display_name} — "
                        f"that includes content I can't store."
                    ),
                    slug=target_slug,
                    detail=add_res.detail,
                )
            if add_res.blocked_by == "missing_existing_qualifier":
                self._counters["approve_blocked_missing_qualifier"] += 1
                return ApproveCandidateResult(
                    ok=False,
                    blocked_by="missing_existing_qualifier",
                    reply_text=(
                        f"Couldn't approve {candidate.display_name} — "
                        f"existing entry {add_res.existing_slug!r} has "
                        f"no qualifier. Resolve via dashboard."
                    ),
                    slug=target_slug,
                    detail=add_res.detail,
                    existing_slug=add_res.existing_slug,
                    existing_facts=add_res.existing_facts,
                    existing_qualifier_candidates=add_res.existing_qualifier_candidates,
                    proposed_qualifier=add_res.proposed_qualifier,
                )
            return ApproveCandidateResult(
                ok=False,
                blocked_by=add_res.blocked_by or "store-error",
                reply_text=(
                    f"Couldn't approve {candidate.display_name}: "
                    f"{add_res.detail or add_res.blocked_by}."
                ),
                slug=target_slug,
                detail=add_res.detail,
            )
        # Mark approved in the queue. If every fact under the slug
        # is now approved, drop the candidate entirely.
        self._candidate_store.mark_approved(
            slug, fact_ids=[f.fact_id for f in active],
        )
        post = self._candidate_store.get(slug)
        if post is not None:
            remaining = [
                f for f in post.facts.values()
                if f.approved_at is None and f.rejected_at is None
            ]
            if not remaining:
                self._candidate_store.delete_slug(slug)
        self._counters["candidates_approved"] += 1
        n = len(active)
        word = "fact" if n == 1 else "facts"
        return ApproveCandidateResult(
            ok=True,
            blocked_by=None,
            reply_text=(
                f"Approved {n} {word} for {candidate.display_name}. "
                f"Saved to RELATIONSHIPS.md."
            ),
            slug=target_slug,
            detail=add_res.detail,
        )

    def reject_candidate(
        self,
        slug: str,
        *,
        fact_ids: list[str] | None = None,
    ) -> "RejectCandidateResult":
        """Tombstone a slug or its specific facts. Per §3.6.

        ``fact_ids=None`` rejects the entire slug (all current and
        future facts under it drop silently). With ``fact_ids``,
        only those facts are tombstoned.
        """
        candidate = self._candidate_store.get(slug)
        if candidate is None:
            return RejectCandidateResult(
                ok=False,
                reply_text=f"No candidate in the queue for {slug}.",
                slug=slug,
            )
        self._candidate_store.mark_rejected(slug, fact_ids=fact_ids)
        self._counters["candidates_rejected"] += 1
        if fact_ids is None:
            return RejectCandidateResult(
                ok=True,
                reply_text=(
                    f"Rejected {candidate.display_name}. "
                    f"Future mentions will drop silently."
                ),
                slug=slug,
            )
        n = len(fact_ids)
        word = "fact" if n == 1 else "facts"
        return RejectCandidateResult(
            ok=True,
            reply_text=(
                f"Rejected {n} {word} for {candidate.display_name}."
            ),
            slug=slug,
        )

    def list_pending_candidates(self) -> list:
        """List eligible + below-threshold candidates for the
        ``/learning relationships-pending`` slash command."""
        # `list_all` returns active + approved; we filter to
        # not-yet-approved, not-rejected.
        return [
            v for v in self._candidate_store.list_all()
            if v.approved_at is None and v.rejected_at is None
        ]

    def _display_for_slug_after_restore(self, slug: str) -> str:
        """Find the live entry's display_name for ``slug`` (after
        the restore has landed), or fall back to the slug itself.
        """
        match = self._store.get_live(slug)
        if match is not None:
            return match.display_name
        return slug

    # ----- entry point 2: tick-level promote -----

    def tick_promote_pending(self) -> list[PromoteResult]:
        """For every pending shadow entry: token-check, coherence,
        sensitive scan, promote. Returns one PromoteResult per
        pending entry attempted.
        """
        results: list[PromoteResult] = []
        for person in self._store.list_shadow():
            if not person.pending:
                continue
            results.append(self._promote_one(person))
        return results

    def _promote_one(self, person: Person) -> PromoteResult:
        slug = person.slug
        # 1. Token presence + slug + fact_ids match.
        token = self._tokens.get(
            session_uuid=person.source_session,
            turn_index=person.source_turn_index or 0,
            person_slug=slug,
        )
        try:
            verify_for_promotion(
                token,
                person_slug=slug,
                facts=[f.text for f in person.facts],
            )
        except ConsentError as exc:
            self._record_drop(slug, "missing-token", str(exc))
            # Don't drop the shadow entry yet — restart-recovery
            # may yet re-mint it on the next daemon-startup cycle.
            # Block this tick only.
            return PromoteResult(
                person_slug=slug,
                promoted=False,
                blocked_by="missing-token",
                detail=str(exc),
            )

        # 2. Coherence judge with scope="relationships".
        source_msg, messages = _load_source_turn(
            self._workspace,
            person.source_session,
            person.source_turn_index or 0,
        )
        # Day 2 completion guard: if the source turn isn't loadable
        # we MUST NOT spawn the judge. run_coherence_judge() with an
        # empty messages list silently degrades to a no-window
        # subprocess call — that's vacuous on a relationships-scope
        # claim AND wastes a sonnet call per pending entry per tick.
        # Block deterministically; mark the shadow entry so the
        # dashboard / REPORT.md surface it; entry stays in shadow
        # for a future tick (Day 3's brain-session-UUID handoff
        # will make these source turns loadable again).
        if source_msg is None:
            self._store.update_shadow_flag(
                slug, coherence_block="missing_transcript",
            )
            self._record_drop(
                slug, "coherence-missing-transcript",
                f"source JSONL not found for "
                f"sess={person.source_session} "
                f"turn={person.source_turn_index}",
            )
            return PromoteResult(
                person_slug=slug,
                promoted=False,
                blocked_by="missing-transcript",
                detail=(
                    f"source turn unloadable "
                    f"(sess={person.source_session}, "
                    f"turn={person.source_turn_index}); judge skipped"
                ),
            )
        evidence = source_msg.text
        # Concatenate facts as the lesson body — the judge sees
        # "did the user's turn justify these claims?"
        lesson_body = "; ".join(f.text for f in person.facts)
        synthetic_lesson = {
            "class": "RELATIONSHIPS",
            "lesson": lesson_body,
            "evidence": evidence,
            "scope": "relationships",
        }
        try:
            verdict = self._coherence_judge(
                self._workspace, synthetic_lesson, messages, self._brain,
            )
        except Exception as exc:  # judge is supposed to never raise
            log.warning(
                "relationships.coherence_judge_unexpected_error: %s",
                exc, exc_info=True,
            )
            verdict = CoherenceVerdict.near_miss(
                reason="other",
                explanation=f"judge raised: {exc}",
            )
        if verdict.verdict == "INCOHERENT":
            self._record_drop(
                slug, "coherence-incoherent",
                f"{verdict.reason}: {verdict.explanation}",
            )
            return PromoteResult(
                person_slug=slug,
                promoted=False,
                blocked_by="coherence",
                detail=f"INCOHERENT: {verdict.reason}",
            )
        # NEAR_MISS_REVIEW is advisory — promotion proceeds. Logged
        # but not blocking, mirroring the lesson-curator policy.

        # 3. Sensitive-pattern scan with target_file="relationships".
        # Run against each fact's text; first hit blocks.
        for fact in person.facts:
            hit = _scan_lesson_for_sensitive_content(
                fact.text,
                scope=f"relationships:{slug}",
                target_file="relationships",
            )
            if hit:
                self._record_drop(
                    slug, "sensitive-pattern",
                    f"fact rejected by scanner: {hit}",
                )
                return PromoteResult(
                    person_slug=slug,
                    promoted=False,
                    blocked_by="sensitive",
                    detail=f"scanner-hit: {hit}",
                )

        # 4. Promote. The store re-verifies the token (defense in
        # depth) — passes here since the curator already verified
        # at step 1 and we re-use the same in-memory token.
        promote_res: StoreResult = self._store.promote(slug, token=token)
        if not promote_res.ok:
            return PromoteResult(
                person_slug=slug,
                promoted=False,
                blocked_by="store-error",
                detail=promote_res.message,
            )
        # Consume the token only after successful promotion.
        self._tokens.consume(
            session_uuid=person.source_session,
            turn_index=person.source_turn_index or 0,
            person_slug=slug,
        )
        return PromoteResult(
            person_slug=slug,
            promoted=True,
            detail=promote_res.message,
        )

    def _record_drop(self, slug: str, reason: str, detail: str) -> None:
        self._drop_events.append(
            _DropEvent(person_slug=slug, reason=reason, detail=detail)
        )

    # ----- entry point 3: restart-recovery -----

    async def recover_after_restart(self) -> list[RecoveryResult]:
        """One-shot: re-mint tokens for all pending shadow entries
        by re-running the classifier against the stored source
        turn. Idempotent — safe to call once per daemon startup.
        """
        if self._has_recovered:
            return []
        self._has_recovered = True
        results: list[RecoveryResult] = []
        for person in self._store.list_shadow():
            if not person.pending:
                continue
            results.append(await self._recover_one(person))
        return results

    async def _recover_one(self, person: Person) -> RecoveryResult:
        slug = person.slug
        source_msg, _messages = _load_source_turn(
            self._workspace,
            person.source_session,
            person.source_turn_index or 0,
        )
        if source_msg is None:
            self._store.drop_shadow(slug, reason="source-turn-missing")
            self._record_drop(
                slug, "recovery-source-missing",
                f"sess={person.source_session} turn={person.source_turn_index}",
            )
            return RecoveryResult(
                person_slug=slug,
                re_minted=False,
                dropped_reason="source-missing",
            )

        verdict = await relationships_detect(
            source_msg.text,
            role="user",
            session_uuid=person.source_session,
            turn_index=person.source_turn_index or 0,
            workspace=self._workspace,
            classifier_call=self._classifier_call,
        )
        if verdict.verdict != "ADD":
            self._store.drop_shadow(
                slug,
                reason=f"verdict-mismatch: classifier returned {verdict.verdict}",
            )
            self._record_drop(
                slug, "recovery-verdict-mismatch",
                f"got={verdict.verdict} expected=ADD",
            )
            return RecoveryResult(
                person_slug=slug,
                re_minted=False,
                dropped_reason="verdict-mismatch",
            )
        # Verify the re-derived fact_ids match the shadow's stored facts.
        shadow_fact_ids = derive_fact_ids([f.text for f in person.facts])
        new_fact_ids = derive_fact_ids(list(verdict.facts))
        if set(shadow_fact_ids) - set(new_fact_ids):
            self._store.drop_shadow(
                slug, reason="fact_ids-mismatch on recovery",
            )
            self._record_drop(
                slug, "recovery-fact-ids-mismatch",
                f"shadow_facts={len(shadow_fact_ids)} re-extracted={len(new_fact_ids)}",
            )
            return RecoveryResult(
                person_slug=slug,
                re_minted=False,
                dropped_reason="verdict-mismatch",
            )
        # Re-mint with the original shadow's fact texts (the source
        # of truth for what was staged), not the re-extracted ones.
        token = mint(
            session_uuid=person.source_session,
            turn_index=person.source_turn_index or 0,
            classifier_verdict="ADD",
            person_slug=slug,
            facts=[f.text for f in person.facts],
        )
        self._tokens.add(token)
        return RecoveryResult(person_slug=slug, re_minted=True)
