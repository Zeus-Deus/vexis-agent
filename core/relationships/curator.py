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
from typing import Awaitable, Callable

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
from core.relationships.store import (
    Fact,
    Person,
    RelationshipsStore,
    StoreResult,
)
from core.relationships.triggers import (
    TriggerVerdict,
    derive_slug,
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

    ``staged`` is True when an ADD trigger fired and a Person was
    written to RELATIONSHIPS-SHADOW.md. ``reply_text`` is the
    staged-acknowledge text the transport should send to the user
    (None when no trigger fired — message proceeds to brain
    unchanged).
    """

    staged: bool
    reply_text: str | None
    person_slug: str | None = None
    fact_count: int = 0
    verdict: str = "NONE"


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
    ) -> None:
        self._workspace = workspace
        self._store = store or RelationshipsStore(workspace)
        self._tokens = pending_tokens or PendingTokens()
        # Test seams. Production passes None for both.
        self._classifier_call = classifier_call
        self._coherence_judge = coherence_judge or run_coherence_judge
        self._drop_events: list[_DropEvent] = []
        self._has_recovered = False

    # ----- properties for telemetry / tests -----

    @property
    def store(self) -> RelationshipsStore:
        return self._store

    @property
    def tokens(self) -> PendingTokens:
        return self._tokens

    def drain_drop_events(self) -> list[_DropEvent]:
        events = list(self._drop_events)
        self._drop_events.clear()
        return events

    # ----- entry point 1: turn-level -----

    async def process_user_turn(
        self,
        text: str,
        *,
        session_uuid: str,
        turn_index: int,
    ) -> TurnLevelResult:
        """Detect → mint → stage → reply. Called from
        ``transports/telegram.py``'s ``_dispatch_to_brain``.
        """
        verdict = await relationships_detect(
            text,
            role="user",
            session_uuid=session_uuid,
            turn_index=turn_index,
            workspace=self._workspace,
            classifier_call=self._classifier_call,
        )
        if verdict.verdict == "NONE":
            return TurnLevelResult(staged=False, reply_text=None)

        if verdict.verdict in ("DELETE", "SUPERSEDE"):
            # TODO Day 3: synchronous delete + supersede flow per
            # research doc §3.3. Day 2's scope is ADD only.
            raise NotImplementedError(
                f"verdict {verdict.verdict} not yet wired (Day 3 scope)"
            )

        # ADD path.
        if not verdict.person_name or not verdict.facts:
            log.warning(
                "relationships.process_user_turn: ADD verdict missing "
                "person_name or facts (sess=%s turn=%s)",
                session_uuid, turn_index,
            )
            return TurnLevelResult(staged=False, reply_text=None)

        slug = derive_slug(verdict.person_name)
        token = mint(
            session_uuid=session_uuid,
            turn_index=turn_index,
            classifier_verdict="ADD",
            person_slug=slug,
            facts=list(verdict.facts),
        )
        self._tokens.add(token)
        person = _person_from_trigger(
            verdict=verdict,
            session_uuid=session_uuid,
            turn_index=turn_index,
        )
        self._store.stage(person, token=token)
        n = len(verdict.facts)
        word = "fact" if n == 1 else "facts"
        reply = (
            f"Got it — I've staged {n} {word} about "
            f"{verdict.person_name} for the relationships file. "
            f"It'll land after the next audit pass."
        )
        return TurnLevelResult(
            staged=True,
            reply_text=reply,
            person_slug=slug,
            fact_count=n,
            verdict="ADD",
        )

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
                self._workspace, synthetic_lesson, messages,
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
