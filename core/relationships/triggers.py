"""Consent-trigger detector for the RELATIONSHIPS.md curator.

Day 1: regex matrix + classifier-stub wiring + fail-open wrapper +
hard role-gate. No file writes anywhere. The detector is invoked
only from the dryrun CLI (``/learning relationships-dryrun``) and
from tests; the live Telegram path wiring lands Day 2.

Design source: ``.plans/relationships-md-research.md`` §3.1 and
§3.4. Specifically:

- Hybrid detector: canonical regex fast-path → sonnet classifier
  on ambiguity. Day 1 ships the regex matrix verbatim from §3.1
  and stubs the classifier (always returns NONE). The classifier
  prompt + temperature=0 wiring lands Day 2.
- Hard role-gate: ``role != "user"`` short-circuits to NONE
  before any pattern runs. The literal-type annotation plus the
  runtime check is the spec — both are required so a future
  refactor can't silently bypass either layer.
- Fail-open wrapper: classifier timeout/error/rate-limit returns
  NONE with confidence=0.0, logs at WARNING, increments a
  process-local error counter. The user's turn must NEVER block on
  detector flakiness — this is on the synchronous Telegram → brain
  path.

# TODO Day 2: replace ``_classifier_call`` stub with the real
# sonnet call at temperature=0. Temperature=0 is REQUIRED because
# the restart-recovery procedure in §3.4 ("Bounded restart-
# recovery procedure") re-runs the classifier against the stored
# source turn and compares verdicts. Any nondeterminism produces
# spurious "verdict mismatch → drop" events that lose legitimate
# pending entries.

# TODO Day 3: AMBIGUOUS → clarification flow needs a per-user
# "pending disambiguation" state so the next user turn is
# recognized as a continuation ("she's the one from work") rather
# than a fresh non-trigger. Day 1's detector is stateless.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

log = logging.getLogger(__name__)

from core.relationships.quoted import strip_quoted_blocks

CLASSIFIER_TIMEOUT_SECONDS = 3.0
CLASSIFIER_CONFIDENCE_THRESHOLD = 0.75

Verdict = Literal["ADD", "DELETE", "SUPERSEDE", "NONE"]


@dataclass(frozen=True)
class TriggerVerdict:
    """Result of one detect() call.

    ``matched_pattern_id`` is the canonical regex ID (``"ADD-1"`` …
    ``"SUP-1"``), the literal ``"classifier"`` if the fallback
    classifier produced the verdict, or ``None`` for NONE results.
    Day 1 leaves ``person_name`` / ``qualifier`` / ``facts`` empty
    because the canonical regexes don't extract them — the
    classifier (Day 2) is the source of those fields.
    """

    verdict: Verdict
    person_name: str | None = None
    qualifier: str | None = None
    facts: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    matched_pattern_id: str | None = None


_NONE = TriggerVerdict(verdict="NONE", confidence=0.0)


# Process-local error counter. Wrapped in a small helper class so a
# test can read + reset without touching the module's namespace.
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


# ---------- Canonical regex matrix (research doc §3.1) ----------
#
# All seven compiled at import time. Each ``re.IGNORECASE``. Each
# anchored at start-of-string (``^``) with optional leading
# whitespace — the user's turn is the WHOLE message text, not an
# arbitrary span inside a longer document. If the user buries a
# trigger phrase mid-paragraph, that's classifier territory.

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
    # Deviation from research doc §3.1's literal table: the table
    # uses ``\s+`` after the verb, but the §4 C-P5 fixture
    # ("note: my brother lives in Lisbon") requires zero whitespace
    # between the verb and the colon. ``[\s:,]+`` covers both
    # "note: ..." and "add this:" while ``\b`` after the verb
    # alternation keeps "noteworthy" / "address" from matching.
    r"^\s*(?:add|note|store)\b[\s:,]+(?:to\s+)?(?:relationships|profile|notes)?\s*[:,]?",
    re.IGNORECASE,
)
_DEL_1 = re.compile(
    r"^\s*(?:please\s+)?forget\s+(?:about\s+)?(?:my\s+)?\w+",
    re.IGNORECASE,
)
_DEL_2 = re.compile(
    r"^\s*(?:delete|remove|drop)\s+(?:that|the\s+thing|everything)\s+about\s+\w+",
    re.IGNORECASE,
)
_SUP_1 = re.compile(
    r"^\s*(?:update|correct|fix)\s+(?:that|what\s+you\s+know)\s+about\s+\w+",
    re.IGNORECASE,
)

# Ordered: ADD before DEL before SUP, since the matrix has no
# overlapping prefixes between groups; first match wins.
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

    Regex compilation can't fail at this point (compiled at import
    time and any failure would have surfaced at module load), but
    ``Pattern.match`` itself is in CPython and could in principle
    raise (e.g. on absurd input). Fail-open mirrors the classifier
    side: any exception → NONE, error counted.
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


async def _classifier_call(
    text: str,
    *,
    session_uuid: str,
    turn_index: int,
) -> TriggerVerdict:
    """Day 1 stub. Always returns NONE.

    Day 2: replace with a real sonnet ``claude -p`` call at
    temperature=0 with the 4-output schema:

        verdict: ADD | DELETE | SUPERSEDE | NONE
        person:  <name as written, or null>
        fact:    <one-sentence canonical fact, or null>
        confidence: 0.0..1.0

    Promote to a trigger only when verdict != NONE and confidence
    >= CLASSIFIER_CONFIDENCE_THRESHOLD. The session_uuid + turn_index
    arguments will be threaded into the prompt so the classifier
    sees the surrounding context window.
    """
    del text, session_uuid, turn_index  # Day 1: unused by stub
    return _NONE


async def detect(
    text: str,
    *,
    role: Literal["user"],
    session_uuid: str,
    turn_index: int,
) -> TriggerVerdict:
    """Run the consent-trigger detector against one user turn.

    Returns the regex verdict if any canonical pattern matches.
    Otherwise falls through to the classifier (stubbed in Day 1).
    Hard role-gate at the top: any role other than ``"user"`` is
    short-circuited to NONE before any pattern or classifier work.

    Quoted content (markdown blockquotes, fenced code blocks,
    inline backtick spans) is stripped from ``text`` BEFORE regex
    and BEFORE classifier prompt assembly so the user can quote a
    trigger phrase without firing one (research doc C-Q1..C-Q3).

    Empty / whitespace-only input returns NONE.

    Failure mode (research doc §3.1): classifier timeout, transport
    error, rate-limit, or unparseable response → NONE with
    confidence=0.0, error logged at WARNING and counted in
    ``classifier_errors``. The synchronous Telegram → brain path
    must NEVER block on detector flakiness.
    """
    # Hard role-gate. The literal-type annotation documents the
    # expected caller contract; the runtime check is what actually
    # enforces it. Both are required — a future refactor that
    # weakens the type annotation must not silently bypass the
    # gate, and a future caller that ignores the type hint must
    # not silently sneak past either.
    if role != "user":
        return _NONE

    if not text or not text.strip():
        return _NONE

    stripped = strip_quoted_blocks(text)
    if not stripped.strip():
        return _NONE

    regex_result = _regex_pass(stripped)
    if regex_result.verdict != "NONE":
        return regex_result

    # Fallback to classifier. Wrapped in wait_for + broad except so
    # any transport flakiness fails open. Day 1's stub returns NONE
    # synchronously and won't time out, but the wrapper is wired
    # now so Day 2 can swap in the real call without re-architecting.
    try:
        verdict = await asyncio.wait_for(
            _classifier_call(
                stripped,
                session_uuid=session_uuid,
                turn_index=turn_index,
            ),
            timeout=CLASSIFIER_TIMEOUT_SECONDS,
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
    return verdict
