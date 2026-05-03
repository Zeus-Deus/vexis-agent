"""v3a coherence judge — second-line check on promoted lesson↔evidence pairs.

This module is invoked AFTER v2's ``_validate_lesson`` clears a
candidate (verbatim evidence + threat scanner + class enum). v3a is
the second line: a typed LLM judgment on whether the lesson's text
actually generalizes the user's signal in the cited evidence.

The failure modes (per ``.plans/coherence-curator-research.md`` §1.4):

  * **mismatched-attribution** — real evidence quote, real claim, no
    semantic link between them. v2's verbatim-evidence check passes
    because the string is in the transcript; v3a catches the
    mis-pairing.
  * **narrow-one-shot** — real evidence from one tactical exchange,
    lesson scope claims a class-level rule far broader than the
    evidence supports.
  * **hallucinated-inference** — real evidence, claim plausibly
    inferred from elsewhere in the session, no support in the cited
    evidence (sarcasm taken literally; rhetorical questions parsed
    as commands).
  * **scope-overflow** — lesson body is well-evidenced but the scope
    field overreaches.
  * **wrong-target-file** — lesson grounded but routed to the wrong
    file (catches a v2 routing miscall while we're at it).

Pipeline::

    verified_lesson + meta + messages
        ─► find_evidence_message_index
        ─► _render_transcript_window  (canonical §3.2 rule)
        ─► _build_judge_prompt        (§4.4 prompt template)
        ─► claude -p (Sonnet by default, model_coherence_judge())
        ─► _extract_verdict           (typed JSON parser + verifier)
        ─► CoherenceVerdict(verdict, reason, explanation, degraded)

Verdict triage (per ``.plans/coherence-curator-research.md`` §3.2):

  * COHERENT          — silent; entry proceeds normally
  * NEAR_MISS_REVIEW  — annotate; entry proceeds with a soft flag
  * INCOHERENT        — annotate with reason; entry proceeds with a
                        hard flag (still goes to shadow tree, never
                        auto-deletes — v3a is advisory-only per §3.6)

Failure modes (per §5.2): a malformed judge response, a
``claude -p`` timeout, or a non-zero exit all collapse to
NEAR_MISS_REVIEW with ``reason=other`` and a diagnostic explanation.
Fail-loud rather than silent-pass: if the judge can't make up its
mind, surface the entry for human review.

Day 1 scope: this module + tests, no integration with
``_write_verified``. Day 2 wires the call into the dispatcher.
Day 3 adds eval grades.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from core.transcripts import TranscriptMessage
from core.yaml_config import (
    model_coherence_judge,
    resolve_model_flag,
)

log = logging.getLogger(__name__)

# Hard wall on a single judge call. Sonnet typically returns in a few
# seconds for a ~10 KB prompt; 60 s is comfortable headroom for
# burstier latency without letting a hung subprocess block the tick.
COHERENCE_JUDGE_TIMEOUT_SECONDS = 60

# Canonical window rule (binding — see ``.plans/coherence-curator-research.md``
# §3.2). Default ±5 user turns around the evidence message; fall back
# to ±2 if rendered length exceeds max_chars; if still over, truncate
# the evidence message body but always retain the evidence quote intact.
WINDOW_TURNS_DEFAULT = 5
WINDOW_TURNS_FALLBACK = 2
WINDOW_MAX_CHARS = 10_000
EVIDENCE_BODY_TRUNCATE_PREFIX = 4_000
EVIDENCE_BODY_TRUNCATE_SUFFIX = 1_000

# Recursion guard env var. The judge spawns its own ``claude -p``;
# the spawned process inherits this so the learning curator's
# eligibility filter knows to ignore the judge's session JSONL on
# the next tick. Distinct from ``RECURSION_ENV_VAR`` in
# ``core/learning_review.py`` so audit logs can tell which subsystem
# spawned what.
COHERENCE_JUDGE_ENV_VAR = "VEXIS_COHERENCE_JUDGE"


# Verdict + reason enums. Kept in sync with the prompt's Output
# contract — any change to one must mirror in the other.
_VALID_VERDICTS: frozenset[str] = frozenset({
    "COHERENT", "NEAR_MISS_REVIEW", "INCOHERENT",
})

_VALID_REASONS: frozenset[str] = frozenset({
    "mismatched-attribution",
    "narrow-one-shot",
    "hallucinated-inference",
    "scope-overflow",
    "wrong-target-file",
    "other",
})


@dataclass(frozen=True)
class CoherenceVerdict:
    """Typed output from one judge call.

    ``reason`` and ``explanation`` are required when verdict !=
    COHERENT and None on COHERENT. ``degraded`` is True when the
    judge ran without a transcript window (manual audit on a v1-era
    live entry whose source-session linkage is lost — see §5.5).
    """

    verdict: str
    reason: str | None
    explanation: str | None
    degraded: bool = False

    @classmethod
    def coherent(cls) -> "CoherenceVerdict":
        return cls(verdict="COHERENT", reason=None, explanation=None)

    @classmethod
    def near_miss(cls, reason: str | None, explanation: str) -> "CoherenceVerdict":
        return cls(
            verdict="NEAR_MISS_REVIEW",
            reason=reason,
            explanation=explanation,
        )

    @classmethod
    def incoherent(cls, reason: str, explanation: str) -> "CoherenceVerdict":
        return cls(
            verdict="INCOHERENT",
            reason=reason,
            explanation=explanation,
        )


SpawnFn = Callable[[list[str], dict[str, str]], subprocess.CompletedProcess]


# --------------------------------------------------------------------
# Evidence message location
# --------------------------------------------------------------------


def find_evidence_message_index(
    messages: list[TranscriptMessage], evidence: str
) -> int:
    """Return the index of the first user message containing
    ``evidence`` verbatim. Returns -1 on miss.

    Mirrors v2's ``_verify_evidence`` (``learning_review.py:828-846``)
    but returns the index instead of just a bool. Day 2's hook will
    call this to know where to window. The verbatim search is exact
    substring — same posture as the v2 check (no whitespace
    normalization).
    """
    if not evidence:
        return -1
    for i, msg in enumerate(messages):
        if msg.role != "user":
            continue
        if evidence in msg.text:
            return i
    return -1


# --------------------------------------------------------------------
# Transcript window rendering (canonical §3.2 rule)
# --------------------------------------------------------------------


def _find_window_bounds(
    messages: list[TranscriptMessage],
    evidence_index: int,
    turns_each_side: int,
) -> tuple[int, int]:
    """Return (start, end) such that ``messages[start:end+1]`` covers
    ±``turns_each_side`` user turns around ``evidence_index``.

    Boundaries are anchored on the Nth user turn either side of the
    evidence (or the list extent if fewer user turns exist). Assistant
    turns *between* the two boundary user turns ride along
    automatically by virtue of being inside the slice. Trailing
    assistant turns AFTER the forward boundary user turn are NOT
    included — symmetric with the backward direction, which doesn't
    pick up leading assistant turns BEFORE the backward boundary
    user turn.

    Out-of-range ``evidence_index`` returns a degenerate (idx, idx)
    pair rather than raising — the caller has already passed
    user-supplied data through validation, but defense-in-depth.
    """
    if not (0 <= evidence_index < len(messages)):
        return evidence_index, evidence_index

    # Backward: find the index of the Nth user turn before evidence.
    # If fewer than N user turns exist, the slice starts at 0.
    start = 0
    seen = 0
    for i in range(evidence_index - 1, -1, -1):
        if messages[i].role == "user":
            seen += 1
            if seen == turns_each_side:
                start = i
                break

    # Forward: find the index of the Nth user turn after evidence.
    # If fewer than N user turns exist, the slice ends at the last
    # message (which is typically what we want — include whatever
    # remains of the session).
    end = len(messages) - 1
    seen = 0
    for i in range(evidence_index + 1, len(messages)):
        if messages[i].role == "user":
            seen += 1
            if seen == turns_each_side:
                end = i
                break

    return start, end


def _truncate_evidence_body(
    messages: list[TranscriptMessage],
    evidence_index: int,
    evidence_text: str,
) -> list[TranscriptMessage]:
    """Return a new messages list with the evidence message's body
    truncated to PREFIX + ``...[truncated]...`` + SUFFIX, but with
    ``evidence_text`` preserved intact.

    The evidence quote is the spine of the judgment; truncating it
    would defeat the whole point. We keep PREFIX chars before it and
    SUFFIX chars after it. If evidence_text isn't found in the body
    (shouldn't happen — v2 verifier caught this earlier; defensive),
    we fall back to a generic prefix/suffix truncation of the raw body.
    """
    msg = messages[evidence_index]
    body = msg.text
    threshold = EVIDENCE_BODY_TRUNCATE_PREFIX + EVIDENCE_BODY_TRUNCATE_SUFFIX
    if len(body) <= threshold:
        return list(messages)
    out = list(messages)
    if not evidence_text or evidence_text not in body:
        new_body = (
            body[:EVIDENCE_BODY_TRUNCATE_PREFIX]
            + " ...[truncated]... "
            + body[-EVIDENCE_BODY_TRUNCATE_SUFFIX:]
        )
        out[evidence_index] = replace(msg, text=new_body)
        return out
    pos = body.index(evidence_text)
    before = body[:pos]
    after = body[pos + len(evidence_text):]
    if len(before) > EVIDENCE_BODY_TRUNCATE_PREFIX:
        before = "...[truncated]... " + before[-EVIDENCE_BODY_TRUNCATE_PREFIX:]
    if len(after) > EVIDENCE_BODY_TRUNCATE_SUFFIX:
        after = after[:EVIDENCE_BODY_TRUNCATE_SUFFIX] + " ...[truncated]..."
    new_body = before + evidence_text + after
    out[evidence_index] = replace(msg, text=new_body)
    return out


def _slice_and_render(
    messages: list[TranscriptMessage],
    evidence_index: int,
    turns_each_side: int,
) -> str:
    """Slice ±``turns_each_side`` user turns and render via the v2
    transcript formatter so the judge sees the same shape v2 saw."""
    # Imported here to avoid an import-time cycle: learning_review
    # already imports yaml_config helpers, and Day 2 will have
    # learning_curator import both modules. Local import keeps the
    # graph clean.
    from core.learning_review import _format_transcript

    start, end = _find_window_bounds(
        messages, evidence_index, turns_each_side
    )
    return _format_transcript(messages[start:end + 1])


def _render_transcript_window(
    messages: list[TranscriptMessage],
    evidence_message_index: int,
    *,
    turns_each_side: int = WINDOW_TURNS_DEFAULT,
    fallback_turns: int = WINDOW_TURNS_FALLBACK,
    max_chars: int = WINDOW_MAX_CHARS,
    evidence_text: str = "",
) -> str:
    """Render the transcript chunk around ``evidence_message_index``.

    Implements the §3.2 canonical rule (binding — single source of
    truth, also referenced by §5.3):

      1. **Default**: ±``turns_each_side`` user turns around the
         evidence message (with intervening assistant turns), rendered
         via ``_format_transcript``.
      2. **If rendered length > ``max_chars``**: fall back to
         ±``fallback_turns`` user turns.
      3. **If still > ``max_chars``**: keep the fallback window but
         truncate the evidence message body (keeping ``evidence_text``
         intact). The evidence quote is the spine of the judgment and
         is never truncated; only the surrounding body is.

    Returns "" when ``evidence_message_index`` is out of bounds —
    callers treat that as "degraded mode" and set ``degraded=True``
    on the verdict.
    """
    if not (0 <= evidence_message_index < len(messages)):
        return ""
    rendered = _slice_and_render(
        messages, evidence_message_index, turns_each_side
    )
    if len(rendered) <= max_chars:
        return rendered
    rendered = _slice_and_render(
        messages, evidence_message_index, fallback_turns
    )
    if len(rendered) <= max_chars:
        return rendered
    truncated = _truncate_evidence_body(
        messages, evidence_message_index, evidence_text
    )
    return _slice_and_render(
        truncated, evidence_message_index, fallback_turns
    )


# --------------------------------------------------------------------
# Judge prompt assembly (§4.4)
# --------------------------------------------------------------------


_JUDGE_PROMPT_TEMPLATE = """\
You are a coherence judge for the Vexis learning curator. Your job
is to decide whether a proposed lesson is properly grounded in the
user-message evidence cited from the source session.

You will be shown:
  1. The proposed lesson (rule, scope, class, tier, target).
  2. The cited evidence (a verbatim user quote).
  3. The transcript window around the cited evidence ({turns_window}
     user turns either side, with intervening assistant turns).
  4. (When applicable) the existing skill body or memory entry
     this lesson would write to.

Your verdict is one of:

  COHERENT — the lesson generalizes the user's actual signal in
    the transcript, the evidence directly motivates the rule, and
    the scope is proportional to what the evidence supports.

  NEAR_MISS_REVIEW — the lesson is plausibly grounded but the
    grounding is thin: the evidence supports it weakly, the scope
    is somewhat broader than the evidence justifies, or the
    transcript context is ambiguous. The user should review.

  INCOHERENT — the lesson is not grounded in the cited evidence.
    Pick a reason:
      - mismatched-attribution: evidence is verbatim and real, but
        about a different topic than the lesson.
      - narrow-one-shot: evidence is one tactical exchange; lesson
        scope claims a class-level rule far beyond it.
      - hallucinated-inference: lesson claims something the
        evidence does not contain or support (sarcasm or rhetorical
        questions taken literally; inference back-attached to an
        unrelated quote).
      - scope-overflow: lesson body is well-evidenced; scope field
        overreaches what the evidence justifies.
      - wrong-target-file: lesson is grounded but routed to the
        wrong file (a procedural rule sent to MEMORY.md, an
        identity claim sent to skills, etc.).
      - other: explain in the explanation field.

Output ONLY a JSON object on a single line. No prose, no fences:

  {{"verdict": "...", "reason": "..." | null, "explanation": "..."}}

Hard rules:
  - If the evidence string does not appear in the transcript at
    all, return INCOHERENT with reason=hallucinated-inference.
  - If the lesson is technically true but the transcript shows
    no signal that should have prompted it, return INCOHERENT
    with reason=mismatched-attribution.
  - If the user message is sarcastic or rhetorical (e.g. "oh sure
    just hardcode the password — what could go wrong") and the
    lesson takes the surface text literally as a request, return
    INCOHERENT (mismatched-attribution OR hallucinated-inference,
    whichever fits — the verifier accepts either).
  - If the user message uses non-standard English (pidgin, code-
    switching, dense jargon, in-jokes) but the meaning is clear in
    context AND the lesson correctly captures that meaning,
    return COHERENT. Do not flag legitimate lessons just because
    the surface text looks irregular.
  - When in doubt between COHERENT and NEAR_MISS_REVIEW, prefer
    NEAR_MISS_REVIEW. A flag the user dismisses is cheaper than
    an unflagged miss.
  - When in doubt between NEAR_MISS_REVIEW and INCOHERENT, prefer
    NEAR_MISS_REVIEW. INCOHERENT is the strong signal; reserve
    it for cases where the misalignment is unambiguous.
  - The ``explanation`` field must be ≤300 chars and explain what
    specifically does or doesn't align. Required for
    NEAR_MISS_REVIEW and INCOHERENT; ignored for COHERENT.

## Proposed lesson

Class: {class_}
{tier_line}Lesson: {lesson_text}
Scope: {scope}
Evidence (verbatim user quote): {evidence}{target_block}

## Transcript window around the evidence

{transcript_section}
"""


def _build_judge_prompt(
    lesson: dict,
    *,
    transcript_window: str,
    target_body: str | None = None,
    degraded: bool = False,
    turns_window: int = WINDOW_TURNS_DEFAULT,
) -> str:
    """Compose the judge's user prompt.

    ``lesson`` is the v2-shape verified lesson dict (class, lesson,
    evidence, scope, optional tier+target). ``target_body`` is the
    rendered text of the target file/entry the lesson would write to,
    when known (S1 patches: existing SKILL.md; S3: ``new_skill_body``;
    SITUATIONAL: rendered MEMORY entry; IDENTITY: queued claim text).
    Pass None when the target isn't readily available — the judge
    proceeds without it.

    ``degraded=True`` swaps the transcript section for a note that the
    source-session JSONL is unavailable so the judge knows it's
    operating on lesson + evidence shape alone (manual audit on
    v1-era live entries — see §5.5).
    """
    class_ = lesson.get("class", "?")
    tier = lesson.get("tier")
    tier_line = f"Tier: {tier}\n" if tier else ""
    lesson_text = lesson.get("lesson", "")
    scope = lesson.get("scope", "")
    evidence = lesson.get("evidence", "")
    target_block = ""
    if target_body:
        target_block = (
            "\n\n## Target the lesson would write to\n\n" + target_body
        )
    if degraded:
        transcript_section = (
            "(transcript window unavailable — source session JSONL "
            "rotated or unlinked. Judge based on lesson and evidence "
            "shape alone; lean toward NEAR_MISS_REVIEW when the "
            "evidence is too thin to support the lesson without "
            "transcript context.)"
        )
    else:
        transcript_section = transcript_window or "(empty window)"
    return _JUDGE_PROMPT_TEMPLATE.format(
        turns_window=turns_window,
        class_=class_,
        tier_line=tier_line,
        lesson_text=lesson_text,
        scope=scope,
        evidence=evidence,
        target_block=target_block,
        transcript_section=transcript_section,
    )


# --------------------------------------------------------------------
# Verdict extraction (parse + verify the judge's output)
# --------------------------------------------------------------------


# Code fence: matches ```json\n...\n``` or ```\n...\n```. Non-greedy
# body so multiple fences in one response don't merge. Same shape as
# v2's ``_FENCE_RE`` (``learning_review.py:752``).
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.+?)\n?```", re.DOTALL)

# Find a {"verdict": ...} object inside arbitrary prose. Defensive —
# the prompt asks for JSON only, but the model occasionally pads.
_VERDICT_OBJ_RE = re.compile(
    r"\{[^{}]*\"verdict\"[^{}]*\}", re.DOTALL
)


def _try_parse_object(text: str) -> dict | None:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _validate_verdict_dict(obj: dict) -> CoherenceVerdict | None:
    """Validate the parsed dict matches the verdict schema.

    Returns None on schema violation (caller treats as malformed →
    NEAR_MISS_REVIEW with reason=other). Tolerates an unknown reason
    on NEAR_MISS_REVIEW (coerces to None) since the verdict itself is
    still actionable; an unknown reason on INCOHERENT is a hard fail
    because the audit annotation requires the reason name.
    """
    verdict = obj.get("verdict")
    reason = obj.get("reason")
    explanation = obj.get("explanation")
    if not isinstance(verdict, str) or verdict not in _VALID_VERDICTS:
        return None
    if verdict == "COHERENT":
        return CoherenceVerdict.coherent()
    if not isinstance(explanation, str) or not explanation.strip():
        return None
    if verdict == "INCOHERENT":
        if not isinstance(reason, str) or reason not in _VALID_REASONS:
            return None
    if reason is not None and (
        not isinstance(reason, str) or reason not in _VALID_REASONS
    ):
        # NEAR_MISS_REVIEW with an unknown reason — keep the verdict,
        # drop the reason. The user still gets the explanation.
        reason = None
    return CoherenceVerdict(
        verdict=verdict,
        reason=reason,
        explanation=explanation.strip(),
    )


def _extract_verdict(raw_response: str) -> CoherenceVerdict | None:
    """Parse the judge's response into a CoherenceVerdict.

    Robust to: code fences (```json...```), single-line JSON, a JSON
    object embedded in prose. Returns None on parse failure or
    schema violation; ``run_coherence_judge`` treats None as
    "judge output malformed" and falls back to NEAR_MISS_REVIEW per
    §5.2 (fail-loud).
    """
    if not isinstance(raw_response, str):
        return None
    body = raw_response.strip()
    if not body:
        return None
    fence = _FENCE_RE.search(body)
    if fence:
        body = fence.group(1).strip()
    parsed = _try_parse_object(body)
    if parsed is None:
        match = _VERDICT_OBJ_RE.search(body)
        if match:
            parsed = _try_parse_object(match.group(0))
    if parsed is None:
        return None
    return _validate_verdict_dict(parsed)


# --------------------------------------------------------------------
# Subprocess runner
# --------------------------------------------------------------------


def run_coherence_judge(
    workspace: Path,
    lesson: dict,
    messages: list[TranscriptMessage],
    *,
    target_body: str | None = None,
    spawn: SpawnFn | None = None,
) -> CoherenceVerdict:
    """Spawn ``claude -p``, render the prompt, parse the verdict.

    Returns a CoherenceVerdict in all cases — never raises. Failure
    paths (timeout, non-zero exit, parse failure) collapse to
    NEAR_MISS_REVIEW with ``reason=other`` and a diagnostic
    explanation, fail-loud per §5.2.

    Two degraded conditions:
      1. ``messages`` empty → manual audit on a v1-era entry. The
         judge runs on lesson + evidence shape alone with
         ``degraded=True``.
      2. ``messages`` non-empty but evidence not found → judge
         shortcuts to INCOHERENT(hallucinated-inference). v2's
         verifier should have caught this; defense-in-depth.

    ``target_body`` is the optional rendered target the lesson would
    write to (existing SKILL.md, MEMORY entry, etc.). Pass None when
    not available; the judge proceeds without that input.

    ``spawn`` is a test seam — production passes None and we shell
    out via ``subprocess.run``. Mirrors the shape of v2's
    ``run_review`` (``learning_review.py:1149``).
    """
    evidence = str(lesson.get("evidence", ""))

    if not messages:
        evidence_index = -1
        transcript_window = ""
        degraded = True
    else:
        evidence_index = find_evidence_message_index(messages, evidence)
        if evidence_index < 0:
            return CoherenceVerdict.incoherent(
                reason="hallucinated-inference",
                explanation=(
                    f"evidence string {evidence[:80]!r} not found "
                    f"verbatim in any user message of this session"
                ),
            )
        transcript_window = _render_transcript_window(
            messages,
            evidence_index,
            evidence_text=evidence,
        )
        degraded = False

    prompt = _build_judge_prompt(
        lesson,
        transcript_window=transcript_window,
        target_body=target_body,
        degraded=degraded,
    )
    argv = [
        "claude",
        "-p",
        *resolve_model_flag(model_coherence_judge()),
        prompt,
    ]
    env = {**os.environ, COHERENCE_JUDGE_ENV_VAR: "1"}

    try:
        if spawn is not None:
            cp = spawn(argv, env)
        else:
            cp = subprocess.run(
                argv,
                env=env,
                cwd=str(workspace),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=COHERENCE_JUDGE_TIMEOUT_SECONDS,
            )
    except subprocess.TimeoutExpired:
        return _maybe_degrade(
            CoherenceVerdict.near_miss(
                reason="other",
                explanation=(
                    f"judge timed out after "
                    f"{COHERENCE_JUDGE_TIMEOUT_SECONDS}s"
                ),
            ),
            degraded=degraded,
        )
    except (OSError, FileNotFoundError) as exc:
        return _maybe_degrade(
            CoherenceVerdict.near_miss(
                reason="other",
                explanation=f"judge spawn failed: {exc}",
            ),
            degraded=degraded,
        )

    stdout = (
        cp.stdout.decode("utf-8", errors="replace")
        if isinstance(cp.stdout, bytes)
        else cp.stdout or ""
    )
    if cp.returncode != 0:
        stderr = (
            cp.stderr.decode("utf-8", errors="replace")
            if isinstance(cp.stderr, bytes)
            else cp.stderr or ""
        )
        body = (stderr or stdout).strip()
        return _maybe_degrade(
            CoherenceVerdict.near_miss(
                reason="other",
                explanation=(
                    f"judge exited {cp.returncode}: {body[:300]}"
                ),
            ),
            degraded=degraded,
        )

    verdict = _extract_verdict(stdout)
    if verdict is None:
        return _maybe_degrade(
            CoherenceVerdict.near_miss(
                reason="other",
                explanation=(
                    f"judge output malformed (could not parse verdict "
                    f"JSON): {stdout.strip()[:300]}"
                ),
            ),
            degraded=degraded,
        )
    return _maybe_degrade(verdict, degraded=degraded)


def _maybe_degrade(
    verdict: CoherenceVerdict, *, degraded: bool
) -> CoherenceVerdict:
    """Stamp ``degraded=True`` onto a verdict when the judge ran
    without a transcript window. Pure helper to keep the runner's
    return paths clean."""
    if not degraded or verdict.degraded:
        return verdict
    return CoherenceVerdict(
        verdict=verdict.verdict,
        reason=verdict.reason,
        explanation=verdict.explanation,
        degraded=True,
    )


__all__ = [
    "COHERENCE_JUDGE_ENV_VAR",
    "COHERENCE_JUDGE_TIMEOUT_SECONDS",
    "WINDOW_TURNS_DEFAULT",
    "WINDOW_TURNS_FALLBACK",
    "WINDOW_MAX_CHARS",
    "CoherenceVerdict",
    "SpawnFn",
    "find_evidence_message_index",
    "run_coherence_judge",
    # Internal helpers exported for direct unit testing:
    "_JUDGE_PROMPT_TEMPLATE",
    "_VALID_VERDICTS",
    "_VALID_REASONS",
    "_render_transcript_window",
    "_find_window_bounds",
    "_truncate_evidence_body",
    "_build_judge_prompt",
    "_extract_verdict",
    "_validate_verdict_dict",
]
