"""Silent third-party fact extractor (v3c Day 4a).

Per ``.plans/relationships-v3c-research.md``:

- Runs at lesson-curator tick time against each session that
  passes triage. Sequential after the lesson reviewer (sharing
  the loaded transcript). Failures isolated — extractor errors
  don't undermine the lesson reviewer's success.
- Default model: haiku (§4.1 patch — see
  ``core.yaml_config.model_relationships_extractor``). The
  extraction task is structurally simpler than the lesson
  reviewer (no class taxonomy), and haiku handles fixed-schema
  JSON output reliably.
- Per-fact extraction-time scan with ``target_file="user"`` —
  fires the medical/legal/financial set, the USER-md additions,
  AND the named-third-party scanner (the v3b
  ``target_file="relationships"`` bypass requires a verified
  ConsentToken; no token exists yet at extraction time).
  Sensitive hits drop the fact silently (no queue write, no
  user surface).
- Dedup against live RELATIONSHIPS.md before queuing. Exact
  ``fact_id`` match (sha256 of stripped text, 16-hex truncation
  — same scheme v3b uses) drops the observation silently.

The extractor mints NO ConsentTokens. Tokens are minted at
approval time by the slash-command / dashboard surface (see
``core.relationships.curator`` for the post-approval flow).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from core.brain.base import (
    BrainAuthRequired,
    BrainError,
    BrainNotInstalled,
    BrainTimeoutError,
)

if TYPE_CHECKING:
    from core.brain.base import Brain

from core.relationships.candidate_store import (
    RelationshipsCandidateStore,
)
from core.relationships.consent import _fact_id
from core.relationships.store import RelationshipsStore
from core.relationships.triggers import derive_slug
from core.transcripts import TranscriptMessage
from core.yaml_config import subsystem_tier

log = logging.getLogger(__name__)

# Hard ceiling on a single extractor ``claude -p`` invocation.
# Bumped 30 → 60 in v3c Day 5 because the Day 4c eval surfaced
# cold-start latencies that exceed 30s on first-invocation in a
# fresh shell (claude CLI auth + model warm-up). 60s still bails
# on genuine hangs (haiku rarely takes more than ~10s on a
# transcript that fits the MAX_USER_TURNS_PER_EXTRACTION cap),
# while absorbing the cold-start variance that produced flaky
# eval failures unrelated to extraction quality.
EXTRACTOR_TIMEOUT_SECONDS = 60.0
EXTRACTOR_ENV_VAR = "VEXIS_RELATIONSHIPS_EXTRACTOR"

# Cap on the number of user-role turns the extractor sees per
# session. Long sessions get tail-truncated — the extractor's job
# is "what are the durable third-party facts in this session,"
# which is robust to losing the very early turns of a long
# session and very expensive when haiku has to reason over a
# multi-thousand-line transcript.
MAX_USER_TURNS_PER_EXTRACTION = 80


# --------------------------------------------------------------------
# Result dataclasses
# --------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedFact:
    """One (slug, fact) pair the extractor proposes for queueing.

    ``confidence`` mirrors v3b's TriggerVerdict shape; the
    extractor's calling site applies the threshold (default 0.6
    — looser than v3b's 0.75 because silent extraction errs
    toward "queue it, let the user reject" rather than v3b's
    "drop unless explicit").
    """

    person: str
    qualifier: str | None
    fact: str
    confidence: float


@dataclass
class ExtractionResult:
    """Outcome of one extractor pass over one session."""

    session_uuid: str
    facts_emitted: int = 0
    facts_dropped_sensitive: int = 0
    facts_dropped_dedup: int = 0
    facts_queued: int = 0
    error: str | None = None
    raw_response: str = ""
    parsed: list[ExtractedFact] = field(default_factory=list)


# --------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------


_EXTRACTOR_PROMPT_TEMPLATE = """You are a silent relationship-fact extractor for a personal-assistant memory system. Your job is to read one user's session transcript and emit any third-party relationship facts that surface in natural conversation.

You DO NOT require explicit user instructions to remember. You DO NOT require a particular phrasing. You extract third-party facts that appear in normal conversation — the user mentioning their coworker, their mom, their friend, etc., along with durable facts about that person.

You output JSON only.

Output schema (strict JSON, no surrounding prose, no markdown fences):
{{
  "extractions": [
    {{
      "person":     "<first name OR a relational referent like 'mom' / 'dad' / 'partner'>",
      "qualifier":  "<relationship word like friend / coworker / mom, or null>",
      "fact":       "<one canonical phrasing of one atomic fact about the person>",
      "confidence": 0.0
    }}
  ]
}}

Hard rules:
- Extract THIRD parties only. Facts about the user themself go to a different system; ignore them here.
- One emission per atomic fact. "Sarah is my coworker and lives in Berlin" → two emissions, one with qualifier=coworker fact="lives in Berlin", another with qualifier=coworker fact="works with the user."
- The fact field describes the THIRD PARTY, not the user's relationship to them. "Sarah is my coworker" → fact="works with the user as a coworker"; the qualifier field carries "coworker" separately.
- Skip mere mentions without facts ("had lunch with Marco today" → no extraction).
- Skip user-self-claims ("I had lunch with Marco" with no fact about Marco → no extraction; "Marco told me he uses Vim" → emit person=Marco fact="uses Vim").
- person must be the referent the user uses. A first name is preferred ("Sarah", "Marco"). When the user's only referent is a relational term ("my mom", "my dad", "my partner", "my brother"), use the lowercase relational term as the person value AND set qualifier to the same word — these are real third-party people the user identifies by role, not pronouns to skip. Genuine pronouns ("she", "he", "they") with no antecedent → skip.
- qualifier is the relationship word ("sister", "coworker", "mom", "friend") if present anywhere in the conversation about that person; null otherwise.
- confidence ≥ 0.5 to be acted on. Below that, omit the extraction.
- Output empty extractions array if no facts surface.

Transcript (session_uuid={session_uuid}, {turn_count} user turn(s)):
<<<TRANSCRIPT>>>
{transcript}
<<<END_TRANSCRIPT>>>

Output the JSON object now, nothing else.
"""


def _format_transcript_for_extractor(messages: list[TranscriptMessage]) -> tuple[str, int]:
    """Render the transcript as a flat user-only stream so the
    extractor's reasoning is grounded in user utterances only.

    The lesson reviewer uses a fuller transcript shape (assistant
    turns + tool calls); the extractor doesn't need that — it's
    looking for facts the USER stated. Tail-truncate to
    ``MAX_USER_TURNS_PER_EXTRACTION`` so long sessions don't
    blow the context budget.
    """
    user_msgs = [m for m in messages if m.role == "user"]
    if len(user_msgs) > MAX_USER_TURNS_PER_EXTRACTION:
        user_msgs = user_msgs[-MAX_USER_TURNS_PER_EXTRACTION:]
    lines: list[str] = []
    for i, msg in enumerate(user_msgs, start=1):
        text = msg.text.strip()
        if not text:
            continue
        # Truncate per-turn to keep one verbose paste from
        # dominating the context window. 1500 chars per turn is
        # generous; a real conversational turn is well under.
        if len(text) > 1500:
            text = text[:1500] + "…"
        lines.append(f"[turn {i}] {text}")
    return "\n".join(lines), len(user_msgs)


# --------------------------------------------------------------------
# Subprocess + parser
# --------------------------------------------------------------------


def _parse_extractor_output(stdout: str) -> list[ExtractedFact]:
    """Parse the extractor's JSON. Tolerant: locates the first
    `{` and last `}` and tries that substring. Returns an empty
    list on any parse failure (treated as "extractor returned
    nothing")."""
    s = stdout.strip()
    if not s:
        return []
    i = s.find("{")
    j = s.rfind("}")
    if i < 0 or j <= i:
        return []
    try:
        obj = json.loads(s[i : j + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict):
        return []
    raw_extractions = obj.get("extractions") or []
    if not isinstance(raw_extractions, list):
        return []
    out: list[ExtractedFact] = []
    for entry in raw_extractions:
        if not isinstance(entry, dict):
            continue
        person = entry.get("person")
        fact = entry.get("fact")
        if not isinstance(person, str) or not person.strip():
            continue
        if not isinstance(fact, str) or not fact.strip():
            continue
        qualifier_raw = entry.get("qualifier")
        qualifier = (
            str(qualifier_raw).strip()
            if qualifier_raw not in (None, "", "null")
            else None
        )
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < 0.5:
            continue
        out.append(
            ExtractedFact(
                person=person.strip(),
                qualifier=qualifier,
                fact=fact.strip(),
                confidence=confidence,
            )
        )
    return out


# --------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------


async def extract_relationships(
    messages: list[TranscriptMessage],
    session_uuid: str,
    *,
    workspace: Path,
    candidate_store: RelationshipsCandidateStore,
    brain: "Brain",
    relationships_store: RelationshipsStore | None = None,
    sensitive_scan: Callable[..., str | None] | None = None,
) -> ExtractionResult:
    """Run one extractor pass over ``messages`` and queue any
    surviving facts to ``candidate_store``.

    Phase B: spawns via ``Brain.spawn_aux`` instead of an inline
    ``subprocess.run`` + ``asyncio.to_thread`` shim. ``brain`` is
    the aux-spawn surface. The ``sensitive_scan`` test seam stays
    for the threat-scanner call (orthogonal to the spawn).
    """
    result = ExtractionResult(session_uuid=session_uuid)
    transcript_text, user_turn_count = _format_transcript_for_extractor(messages)
    if not transcript_text:
        return result
    prompt = _EXTRACTOR_PROMPT_TEMPLATE.format(
        session_uuid=session_uuid,
        turn_count=user_turn_count,
        transcript=transcript_text,
    )

    stdout = ""
    err: str | None = None
    try:
        aux = await brain.spawn_aux(
            prompt,
            model_tier=subsystem_tier("relationships_extractor"),
            timeout_seconds=EXTRACTOR_TIMEOUT_SECONDS,
            env_overrides={EXTRACTOR_ENV_VAR: "1"},
            cwd=workspace,
            subsystem="relationships_extractor",
        )
        stdout = aux.stdout
        if aux.returncode != 0:
            err = (
                f"claude -p exited {aux.returncode}: "
                f"{(aux.stderr or aux.stdout).strip()[:300]}"
            )
    except BrainTimeoutError:
        err = f"timed out after {EXTRACTOR_TIMEOUT_SECONDS}s"
    except (BrainNotInstalled, BrainAuthRequired) as exc:
        err = f"spawn failed: {exc}"
    except BrainError as exc:
        err = f"spawn failed: {exc}"

    result.raw_response = stdout
    if err:
        result.error = err
        log.warning(
            "relationships extractor error for sess %s: %s",
            session_uuid, err,
        )
        return result
    parsed = _parse_extractor_output(stdout)
    result.parsed = list(parsed)
    result.facts_emitted = len(parsed)
    if not parsed:
        return result

    scan = sensitive_scan or _default_sensitive_scan
    rel_store = relationships_store or RelationshipsStore(workspace)

    for extracted in parsed:
        slug = derive_slug(extracted.person)
        # Sensitive-pattern scan at extraction time: target_file="user"
        # so the third-party scanner FIRES (no token in hand). A hit
        # drops the fact silently — no queue write, no user surface.
        scan_hit = scan(
            extracted.fact,
            scope=f"relationships_candidate:{slug}",
            target_file="user",
        )
        if scan_hit:
            result.facts_dropped_sensitive += 1
            log.info(
                "extractor dropped sensitive fact at extract time "
                "(scope=%s, slug=%s)",
                scan_hit, slug,
            )
            continue
        # Dedup against live: if slug is already in live AND the
        # exact fact_id is present in live's facts, drop silently.
        fact_id = _fact_id(extracted.fact)
        if rel_store.has_live_fact(slug, fact_id):
            result.facts_dropped_dedup += 1
            log.debug(
                "extractor dropped already-live fact (slug=%s, "
                "fact_id=%s)", slug, fact_id,
            )
            continue
        # Queue it.
        candidate_store.add_observation(
            slug=slug,
            display_name=extracted.person,
            qualifier=extracted.qualifier,
            fact_text=extracted.fact,
            session_uuid=session_uuid,
            turn_index=user_turn_count,
        )
        result.facts_queued += 1
    return result


def _default_sensitive_scan(text: str, *, scope: str, target_file: str) -> str | None:
    """Lazy-import wrapper around
    ``core.learning_review._scan_lesson_for_sensitive_content``.
    Lazy because that module is heavy and the extractor only
    pulls it in at runtime (test paths can pass their own scan
    via the ``sensitive_scan`` kwarg)."""
    from core.learning_review import _scan_lesson_for_sensitive_content
    return _scan_lesson_for_sensitive_content(
        text, scope=scope, target_file=target_file,
    )


__all__ = [
    "EXTRACTOR_ENV_VAR",
    "EXTRACTOR_TIMEOUT_SECONDS",
    "MAX_USER_TURNS_PER_EXTRACTION",
    "ExtractedFact",
    "ExtractionResult",
    "extract_relationships",
]
