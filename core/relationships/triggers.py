"""Consent-trigger detector for the RELATIONSHIPS.md curator.

Day 2 (path (a) per research doc §3.1 amendment 2): canonical
regex hits are a CHEAP GATE, not a parse. On any triggered turn
(regex hit OR regex miss with a named third party present), the
sonnet classifier always runs to extract ``person_slug``,
``qualifier``, and ``facts``. Token mint at §3.4 requires those
fields, so the classifier is the canonical extractor and there
is no canonical-only path that bypasses it.

Production flow (``detect`` with ``skip_classifier=False``):

  role-gate → quoted-content strip → regex gate → classifier
  always runs IF (regex hit OR named third party present)
  → if classifier verdict ∈ {ADD, DELETE, SUPERSEDE} ∧
    confidence ≥ 0.75 → return TriggerVerdict with person/facts.

Dryrun + regex-matrix tests flow (``skip_classifier=True``):

  same preamble → regex gate → return the regex match as the
  trigger verdict (with empty person/facts). Cheap. No subprocess.

# TODO Day 3: AMBIGUOUS → clarification flow needs a per-user
# "pending disambiguation" state so the next user turn is
# recognized as a continuation ("she's the one from work")
# rather than a fresh non-trigger. Day 2's detector is stateless.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Literal

log = logging.getLogger(__name__)

from core.brain.base import (
    BrainAuthRequired,
    BrainError,
    BrainNotInstalled,
    BrainTimeoutError,
)

if TYPE_CHECKING:
    from core.brain.base import Brain

from core.identity_threat import check_named_third_party
from core.relationships.quoted import strip_quoted_blocks
from core.yaml_config import subsystem_reasoning, subsystem_tier

CLASSIFIER_TIMEOUT_SECONDS = 12.0
CLASSIFIER_CONFIDENCE_THRESHOLD = 0.75
RELATIONSHIPS_CLASSIFIER_ENV_VAR = "VEXIS_RELATIONSHIPS_CLASSIFIER"

Verdict = Literal["ADD", "DELETE", "SUPERSEDE", "NONE"]


@dataclass(frozen=True)
class TriggerVerdict:
    """Result of one detect() call.

    ``matched_pattern_id`` is the canonical regex ID (``"ADD-1"`` …
    ``"SUP-1"``), the literal ``"classifier"`` if only the
    fallback classifier produced the trigger, or ``None`` for NONE
    results. When BOTH a regex matched AND the classifier ran (the
    common Day 2 path), this records the regex id (the regex is
    what gated the classifier).
    """

    verdict: Verdict
    person_name: str | None = None
    qualifier: str | None = None
    facts: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    matched_pattern_id: str | None = None


_NONE = TriggerVerdict(verdict="NONE", confidence=0.0)


class _ErrorCounter:
    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def incr(self, label: str) -> None:
        self._counts[label] += 1

    def get(self, label: str) -> int:
        return self._counts[label]

    def reset(self) -> None:
        self._counts.clear()


classifier_errors = _ErrorCounter()


# ---------- Canonical regex matrix (research doc §3.1 amendment 2) ----------

_ADD_1 = re.compile(
    r"^\s*(?:please\s+)?remember\s+(?:that\s+)?(?:my\s+)?\w+",
    re.IGNORECASE,
)
_ADD_2 = re.compile(
    r"^\s*(?:please\s+)?save\s+(?:this|that)\s*[:,]?",
    re.IGNORECASE,
)
_ADD_3 = re.compile(
    r"^\s*(?:fyi|for\s+future\s+reference|for\s+the\s+record)\s*[:,]",
    re.IGNORECASE,
)
_ADD_4 = re.compile(
    # \b after the verb keeps "noteworthy" / "address" out;
    # [\s:,]+ covers "note: my brother…" (zero whitespace before
    # the colon — research doc §4 C-P5).
    r"^\s*(?:add|note|store)\b[\s:,]+(?:to\s+)?(?:relationships|profile|notes)?\s*[:,]?",
    re.IGNORECASE,
)
_DEL_1 = re.compile(
    r"^\s*(?:please\s+)?forget\s+(?:about\s+)?(?:my\s+)?\w+",
    re.IGNORECASE,
)
_DEL_2 = re.compile(
    # Synced to research doc §3.1 amendment 2: "that\s+thing" not
    # bare "that" — "delete that thing about my brother" is the
    # natural phrasing.
    r"^\s*(?:delete|remove|drop)\s+(?:that\s+thing|the\s+thing|everything)\s+about\s+\w+",
    re.IGNORECASE,
)
_SUP_1 = re.compile(
    r"^\s*(?:update|correct|fix)\s+(?:that|what\s+you\s+know)\s+about\s+\w+",
    re.IGNORECASE,
)

_REGEX_MATRIX: tuple[tuple[str, re.Pattern[str], Verdict], ...] = (
    ("ADD-1", _ADD_1, "ADD"),
    ("ADD-2", _ADD_2, "ADD"),
    ("ADD-3", _ADD_3, "ADD"),
    ("ADD-4", _ADD_4, "ADD"),
    ("DEL-1", _DEL_1, "DELETE"),
    ("DEL-2", _DEL_2, "DELETE"),
    ("SUP-1", _SUP_1, "SUPERSEDE"),
)


def _regex_pass(text: str) -> TriggerVerdict:
    """Symmetric paranoid wrapper around the canonical regex pass.

    Fail-open mirrors the classifier side: any exception → NONE,
    error counted.
    """
    try:
        for pattern_id, pattern, verdict in _REGEX_MATRIX:
            if pattern.match(text):
                return TriggerVerdict(
                    verdict=verdict,
                    confidence=1.0,
                    matched_pattern_id=pattern_id,
                )
        return _NONE
    except Exception as exc:
        log.warning("relationships.regex_error: %s", exc, exc_info=True)
        classifier_errors.incr("regex")
        return _NONE


# --------------------------------------------------------------------
# Classifier — real claude -p call, with injectable spawn hook for
# tests. Pattern mirrors core/coherence_judge.py and
# core/learning_review.py.
# --------------------------------------------------------------------


_CLASSIFIER_PROMPT_TEMPLATE = """You are a strict consent-trigger classifier for a personal-assistant memory system. Your job is to decide whether a single user message contains an EXPLICIT, UNAMBIGUOUS instruction to remember (ADD), forget (DELETE), or update (SUPERSEDE) facts about a SPECIFIC NAMED THIRD-PARTY PERSON.

You DO NOT infer consent. You DO NOT generalize. You output JSON only.

Output schema (strict JSON, no surrounding prose, no markdown fences):
{{
  "verdict":    "ADD" | "DELETE" | "SUPERSEDE" | "NONE",
  "person":     "<name as written, or null>",
  "qualifier":  "<relationship qualifier like friend / coworker / mom, or null>",
  "facts":      ["<one canonical phrasing per atomic fact>"],
  "confidence": 0.0
}}

Hard rules:
- VERDICT must be NONE unless the message is an explicit user instruction to remember / forget / update facts about a named person.
- ADD requires at least one extractable fact. Emit one entry per atomic claim — DO NOT concatenate ("likes mystery novels and is allergic to peanuts" → two entries).
- DELETE / SUPERSEDE may have an empty facts list.
- person field is the FIRST name as written (e.g. "Sarah" not "my sister Sarah").
- qualifier is the relationship word ("sister", "coworker") if present, else null.
- confidence must be ≥ 0.75 to be acted on; if you are unsure, output verdict NONE.
- If the message names a third party but is NOT an explicit instruction (e.g. "Sarah and I went to dinner"), output verdict NONE.
- Quoted / roleplay content has already been stripped before you see this message; if anything looks like a quote, treat it conservatively.

User turn (session_uuid={session_uuid}, turn_index={turn_index}):
<<<USER_TURN>>>
{user_turn}
<<<END_USER_TURN>>>

Output the JSON object now, nothing else.
"""


def _parse_classifier_output(stdout: str) -> TriggerVerdict:
    """Parse the classifier's JSON response into a TriggerVerdict.

    Tolerant: if the classifier wraps JSON in a markdown fence or
    leading prose, locate the first ``{`` and last ``}`` and try
    to parse the substring. Returns NONE on any parse failure.
    """
    s = stdout.strip()
    if not s:
        return _NONE
    # Locate JSON object boundaries.
    i = s.find("{")
    j = s.rfind("}")
    if i < 0 or j <= i:
        return _NONE
    try:
        obj = json.loads(s[i : j + 1])
    except json.JSONDecodeError:
        return _NONE
    if not isinstance(obj, dict):
        return _NONE
    verdict_raw = str(obj.get("verdict") or "NONE").strip().upper()
    if verdict_raw not in ("ADD", "DELETE", "SUPERSEDE", "NONE"):
        return _NONE
    if verdict_raw == "NONE":
        return _NONE
    person_raw = obj.get("person")
    person = str(person_raw).strip() if person_raw not in (None, "", "null") else None
    qualifier_raw = obj.get("qualifier")
    qualifier = (
        str(qualifier_raw).strip()
        if qualifier_raw not in (None, "", "null")
        else None
    )
    facts_raw = obj.get("facts") or []
    if not isinstance(facts_raw, list):
        return _NONE
    facts = tuple(str(f).strip() for f in facts_raw if str(f).strip())
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if verdict_raw == "ADD" and not facts:
        # Schema requires ≥1 fact for ADD; refuse this output.
        return _NONE
    return TriggerVerdict(
        verdict=verdict_raw,  # type: ignore[arg-type]
        person_name=person,
        qualifier=qualifier,
        facts=facts,
        confidence=confidence,
        matched_pattern_id="classifier",
    )


async def _classifier_call(
    text: str,
    *,
    session_uuid: str,
    turn_index: int,
    workspace: Path | None = None,
    brain: "Brain",
) -> TriggerVerdict:
    """Spawn the classifier through ``Brain.spawn_aux``, parse the
    JSON output, return the verdict.

    Wrapped by ``detect()`` in ``asyncio.wait_for(...,
    timeout=CLASSIFIER_TIMEOUT_SECONDS)`` and a broad ``except
    Exception:`` that returns NONE on any failure (transport,
    spawn, exit-nonzero, parse). Phase B routes through
    ``brain.spawn_aux`` instead of an inline ``subprocess.run``;
    the brain abstraction owns argv composition and tier
    resolution.
    """
    if workspace is None:
        workspace = Path.cwd()
    prompt = _CLASSIFIER_PROMPT_TEMPLATE.format(
        session_uuid=session_uuid,
        turn_index=turn_index,
        user_turn=text,
    )

    try:
        result = await brain.spawn_aux(
            prompt,
            model_tier=subsystem_tier("relationships_classifier"),
            reasoning_level=subsystem_reasoning("relationships_classifier"),
            timeout_seconds=CLASSIFIER_TIMEOUT_SECONDS,
            env_overrides={RELATIONSHIPS_CLASSIFIER_ENV_VAR: "1"},
            cwd=workspace,
            subsystem="relationships_classifier",
        )
    except BrainTimeoutError:
        log.warning("relationships.classifier_subprocess_timeout")
        classifier_errors.incr("subprocess_timeout")
        return _NONE
    except (BrainNotInstalled, BrainAuthRequired) as exc:
        log.warning("relationships.classifier_spawn_failed: %s", exc)
        classifier_errors.incr("spawn_failed")
        return _NONE
    except BrainError as exc:
        log.warning("relationships.classifier_spawn_failed: %s", exc)
        classifier_errors.incr("spawn_failed")
        return _NONE

    if result.returncode != 0:
        log.warning(
            "relationships.classifier_nonzero_exit code=%s body=%s",
            result.returncode,
            (result.stderr or result.stdout)[:300],
        )
        classifier_errors.incr("nonzero_exit")
        return _NONE
    return _parse_classifier_output(result.stdout)


# --------------------------------------------------------------------
# Public detect()
# --------------------------------------------------------------------


async def detect(
    text: str,
    *,
    role: Literal["user"],
    session_uuid: str,
    turn_index: int,
    skip_classifier: bool = False,
    workspace: Path | None = None,
    classifier_call: (
        Callable[..., Awaitable[TriggerVerdict]] | None
    ) = None,
    brain: "Brain | None" = None,
) -> TriggerVerdict:
    """Run the consent-trigger detector against one user turn.

    Production path (``skip_classifier=False``): regex matrix is a
    cheap gate; on any regex hit OR a regex miss where the turn
    names a third party, the sonnet classifier runs and is the
    canonical extractor for ``person_name`` / ``qualifier`` /
    ``facts``. Verdict is the classifier's verdict; regex info is
    preserved in ``matched_pattern_id`` only when the regex hit
    (otherwise ``"classifier"``).

    Cheap path (``skip_classifier=True``): used by the dryrun CLI
    and the regex-matrix tests. Returns the regex match verbatim
    with empty person/facts, or NONE if no regex matched. No
    subprocess fired.

    Hard role-gate at the top: any role other than ``"user"`` is
    short-circuited to NONE before any pattern or classifier work.

    Quoted content (markdown blockquotes, fenced code blocks,
    inline backtick spans) is stripped from ``text`` BEFORE regex
    and BEFORE classifier prompt assembly.

    Empty / whitespace-only input → NONE.

    Failure mode: classifier timeout, transport error,
    rate-limit, or unparseable response → NONE with
    confidence=0.0, error counted in ``classifier_errors``. The
    synchronous Telegram → brain path must NEVER block on
    detector flakiness.
    """
    if role != "user":
        return _NONE
    if not text or not text.strip():
        return _NONE
    stripped = strip_quoted_blocks(text)
    if not stripped.strip():
        return _NONE

    regex_result = _regex_pass(stripped)

    if skip_classifier:
        return regex_result

    has_third_party = bool(check_named_third_party(stripped))
    if regex_result.verdict == "NONE" and not has_third_party:
        return _NONE

    # When a custom ``classifier_call`` is supplied (test seam), it
    # takes precedence and may not need a brain. The default
    # ``_classifier_call`` requires one — fall back to a BrainNull
    # if the caller passed None and we're using the default, so
    # tests that don't pass brain still work (the BrainNull will
    # raise on exhaustion if anything actually reaches the spawn,
    # surfacing the missing brain rather than silently returning).
    call = classifier_call
    if call is None:
        from core.brain.null import BrainNull
        _b = brain or BrainNull()
        async def call(text_, *, session_uuid, turn_index, workspace):
            return await _classifier_call(
                text_,
                session_uuid=session_uuid,
                turn_index=turn_index,
                workspace=workspace,
                brain=_b,
            )
    try:
        verdict = await asyncio.wait_for(
            call(
                stripped,
                session_uuid=session_uuid,
                turn_index=turn_index,
                workspace=workspace,
            ),
            timeout=CLASSIFIER_TIMEOUT_SECONDS + 2.0,
        )
    except TimeoutError:
        log.warning(
            "relationships.classifier_timeout sess=%s turn=%s",
            session_uuid, turn_index,
        )
        classifier_errors.incr("timeout")
        return _NONE
    except Exception as exc:
        log.warning(
            "relationships.classifier_error sess=%s turn=%s: %s",
            session_uuid, turn_index, exc, exc_info=True,
        )
        classifier_errors.incr("error")
        return _NONE

    if verdict.verdict == "NONE":
        return _NONE
    if verdict.confidence < CLASSIFIER_CONFIDENCE_THRESHOLD:
        return _NONE
    if verdict.verdict == "ADD" and not verdict.facts:
        return _NONE

    # Preserve the regex pattern_id when both fired (the regex is
    # what gated the classifier; useful for dryrun annotation).
    if regex_result.verdict != "NONE":
        verdict = TriggerVerdict(
            verdict=verdict.verdict,
            person_name=verdict.person_name,
            qualifier=verdict.qualifier,
            facts=verdict.facts,
            confidence=verdict.confidence,
            matched_pattern_id=regex_result.matched_pattern_id,
        )
    return verdict


# --------------------------------------------------------------------
# Slug derivation (Day 2 minimum; Day 3 adds disambiguation).
# --------------------------------------------------------------------


_SLUG_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def derive_slug(person_name: str) -> str:
    """Produce a lowercase-kebab slug from a person's name.

    Day 2 spec: identity-only slug ("Sarah" → "sarah"). 3b layers
    disambiguation on top via ``derive_slug_with_disambiguation``
    when a collision exists.
    """
    cleaned = _SLUG_NORMALIZE_RE.sub("-", person_name.lower()).strip("-")
    return cleaned or "unknown"


def derive_slug_with_disambiguation(
    person_name: str, qualifier: str | None
) -> str:
    """Produce ``"{base}-{qual_slug}"`` when a qualifier is present,
    else ``"{base}"``.

    Used by 3b's curator when an utterance carries a qualifier and
    the curator needs to either route the write to the qualified
    slug (collision-free case) or trigger a back-edit on an
    existing bare slug (disambiguation case). Identical kebab
    normalisation to ``derive_slug`` so ``"sarah"`` + ``"co worker"``
    yields ``"sarah-co-worker"``.
    """
    base = derive_slug(person_name)
    if not qualifier:
        return base
    qual = _SLUG_NORMALIZE_RE.sub("-", qualifier.lower()).strip("-")
    if not qual:
        return base
    return f"{base}-{qual}"
