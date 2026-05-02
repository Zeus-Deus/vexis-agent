"""LLM-backed review of one session: prompt, parser, verifier, runner.

This module owns everything between "we have a transcript" and "we
have validated lesson candidates ready to write". The controller in
``core/learning_curator.py`` calls ``run_review`` and decides where
to put the verified output (MEMORY-SHADOW.md vs MEMORY.md).

Pipeline:

    transcript ─► _build_prompt ─► claude -p ─► _extract_lessons
                                                       │
                                                       ▼
                                               _validate_lesson  ──► verified
                                               (per candidate)        rejected

Hard rules enforced here, not in the prompt alone:
  - Verbatim evidence MUST appear in some user message of this
    session. Hallucinated quotes are rejected even if the LLM is
    confident.
  - Each lesson capped at ``learning_max_entry_chars()`` chars.
  - At most ``learning_max_entries_per_session()`` candidates per
    session — anything beyond is rejected as "exceeded cap".

The full review prompt lives in ``_LEARNING_REVIEW_PROMPT`` below.
It's a string constant — keep it here so prompt changes show up in
git history without ambiguity about which version was running.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.transcripts import SessionMeta, TranscriptMessage
from core.yaml_config import (
    learning_max_entries_per_session,
    learning_max_entry_chars,
)

log = logging.getLogger(__name__)

# Hard wall on a single review fork. claude -p with a large transcript
# can take a minute or so; 5 min is comfortable headroom while still
# catching genuine hangs.
LEARNING_REVIEW_TIMEOUT_SECONDS = 5 * 60

# Soft warning threshold: above this, we still send the transcript
# but log a warning so context-limit issues are visible. We do NOT
# silently truncate — by design, surfacing the size was the Day 2
# response to "transcript too big".
LARGE_TRANSCRIPT_WARN_CHARS = 100_000

# Hard decline threshold: above this we don't even try the LLM call.
# 200K formatted-transcript chars is well above the warn line,
# comfortably below typical Claude context limits, and leaves
# headroom for the prompt (~3K) and response. Rationale per
# Day 3 spec: "decline-with-reason for v1; track skip rate, revisit
# if >10% of sessions get skipped after a week."
LEARNING_TRANSCRIPT_DECLINE_CHARS = 200_000

# Env var the recursion guard checks for. Mirrors VEXIS_CURATOR=1 in
# core/curator.py — the spawned claude -p inherits this and any code
# that checks for it (including the LearningController) refuses to
# spawn another review.
RECURSION_ENV_VAR = "VEXIS_LEARNING_REVIEW"


_LEARNING_REVIEW_PROMPT = """\
You are reviewing a finished Vexis session for memorable lessons to
save into long-term memory. Your output is read by an automated
verifier that will reject malformed or unevidenced output, then
written to MEMORY.md (which is injected into every future session's
system prompt). A bad write affects every future task; a missed write
affects this one.

## What counts as a lesson

A lesson is a GENERAL rule, derived from a SPECIFIC user signal in
this session, that would help a future session do its job better.

Strong signals (any one warrants a lesson candidate):
  - The user explicitly corrected something Vexis did. ("no, do it
    this way", "stop X", "don't Y", "filter out Z").
  - The user said "remember this" or "from now on" or "when X happens
    do Y" — durable instruction.
  - The user expressed a workflow preference applicable to a class of
    tasks ("when I ask for a list, default to the top 5").

Weak signals (NOT lessons by themselves):
  - Vexis self-corrected mid-session without user input. The fix
    will live in the conversation; no new rule.
  - One-off frustration with no specific corrective content.
  - Praise. ("nice", "good", "thanks").
  - Speculation about what the user might want.

## What a lesson must do

A lesson must GENERALIZE — it must apply to a class of future tasks,
not just the exact task in this session. The class should be NARROW,
not vacuous. "Be helpful" is too broad. "Always filter movie listings
by current time" is too narrow. The right level is "When listing
time-bound options, filter to entries still ahead of the current
time."

The test: would this rule, applied to a different domain in the same
class, still produce sensible behavior? If yes, the level of
abstraction is right. If no, narrow it.

## Anti-patterns — DO NOT write entries like these

These are real failure modes. Each pair shows the bad version Vexis
might write if rushed, and the right rewrite.

  BAD:  "User likes movies that haven't started yet."
  GOOD: "When listing time-bound options (movie showings, event
        slots, deadlines), filter to those still ahead of the current
        time unless the user explicitly asks for past entries."
  Reason: bad version is domain-bound; good version generalizes to a
  class.

  BAD:  "Always filter by current time."
  GOOD: Same as above.
  Reason: vacuous version misfires — applied to "list all files
  modified this year" it would silently drop January.

  BAD:  "User got frustrated when Vexis was verbose, so be terse."
  GOOD: "When the user asks for a specific value or status, return
        only the value, not also adjacent context — they will ask
        for more if they want it."
  Reason: "be terse" is a vibe, not a rule. Good version is testable:
  did the response contain only what was asked, or extras?

  BAD:  "User said 'fuck this' so they're stressed today."
  GOOD: Don't write. Mood is not a lesson.
  Reason: emotion isn't durable. Tomorrow's session is a new mood.

  BAD:  "Vexis should be more careful with file deletions."
  GOOD: "Before any rm/delete operation on user files, restate the
        path and ask for explicit y/n unless the user prefixed the
        request with 'yes' / 'go ahead' / 'no need to confirm'."
  Reason: vague principle vs executable check.

## Output contract

Emit either the literal string

    Nothing to save.

OR a JSON array of lesson objects (max 2). Each object:

    {
      "lesson":   "<≤280 chars, the rule>",
      "evidence": "<verbatim user message that triggered this — must
                   appear word-for-word in the session>",
      "scope":    "<one-line description of what class of tasks this
                   applies to>"
    }

Wrap even a single lesson in a JSON array: [{...}].

Why max 2: 1 forces dropping a real second-strongest lesson when two
distinct strong signals appear in the same session; 3 invites filler
weak enough to be net-negative across all future sessions. 2 is the
smallest cap that survives the "two strong, distinct signals" case
without rewarding noise.

Do NOT call any tools. Do NOT include any text outside the JSON or
the literal "Nothing to save." string. Your entire response is one
of those two things.

## Hard rules

1. ``evidence`` must be a verbatim quote of a user message in this
   session. The verifier will reject any lesson whose evidence is
   not found.
2. ``lesson`` must be ≤280 chars. Manifesto-length lessons are
   over-generalized.
3. If both strong-signal and weak-signal candidates exist, emit only
   strong-signal lessons.
4. If you cannot quote verbatim user evidence, "Nothing to save." is
   the right answer.
5. Do not emit a lesson about the user's identity, religion,
   politics, sexual life, named third parties, or specific medical /
   legal / financial advice received in the session. Memory is read
   aloud to the model in every session — sensitive content gets
   re-spoken forever, and medical/legal/financial recall in
   particular risks the model parroting bad guidance back as if it
   were durable rule.
6. Do not emit a lesson about a one-off bug or temporary
   environmental issue. ("X was broken today" is not a rule.)
"""


# --------------------------------------------------------------------
# Output structure
# --------------------------------------------------------------------


@dataclass
class ReviewOutput:
    """Structured outcome of one review fork.

    Used by the controller to decide what to write where, and to
    populate per-tick REPORT.md. Carries everything the audit
    surface needs: the raw response, the parsed candidates, the
    verified subset, the rejected ones with reasons, any spawn
    error, and (Day 3) the ``declined_too_large`` flag for
    transcripts past the hard threshold. Transcript size is captured
    so the per-tick report can surface "we sent N chars / M messages"
    without re-reading the JSONL.

    ``declined_too_large`` is mutually exclusive with the other
    success/error paths — if the flag is True, the LLM was never
    called and ``raw_response`` is empty.
    """

    raw_response: str = ""
    parsed_lessons: list[dict] = field(default_factory=list)
    verified_lessons: list[dict] = field(default_factory=list)
    rejected: list[tuple[dict, str]] = field(default_factory=list)
    nothing_to_save: bool = False
    declined_too_large: bool = False
    error: str | None = None
    transcript_chars: int = 0
    transcript_messages: int = 0


# --------------------------------------------------------------------
# Transcript formatting
# --------------------------------------------------------------------


# Tool call inputs can be huge (e.g. a Bash heredoc). Truncate the
# inline summary so the transcript stays the right size for review.
# 200 chars is enough to recognize the call ("Bash(cat <<EOF\n...")
# without dragging the whole heredoc into context.
_TOOL_INPUT_PREVIEW_CHARS = 200


def _format_tool_call(tc: dict) -> str:
    name = tc.get("name") or "?"
    raw_input = tc.get("input")
    if raw_input is None:
        rendered = ""
    elif isinstance(raw_input, str):
        rendered = raw_input
    else:
        try:
            rendered = json.dumps(raw_input, ensure_ascii=False)
        except (TypeError, ValueError):
            rendered = str(raw_input)
    if len(rendered) > _TOOL_INPUT_PREVIEW_CHARS:
        rendered = rendered[:_TOOL_INPUT_PREVIEW_CHARS] + "..."
    return f"[tool: {name}({rendered})]"


def _format_transcript(messages: list[TranscriptMessage]) -> str:
    """Render messages as markdown for the review prompt.

    Drops messages whose only payload is a tool_result (they show up
    as empty text + empty tool_calls because ``_flatten_content`` only
    extracts text and tool_use). The assistant side already shows the
    tool call inline; the result is mostly noise for the review and
    can be enormous (file dumps, snapshot output, etc.). If we ever
    decide tool results matter for review quality, this is the place
    to add them back with aggressive truncation.
    """
    lines: list[str] = []
    for msg in messages:
        if not msg.text and not msg.tool_calls:
            continue
        ts = msg.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f"### {msg.role.upper()} ({ts})")
        if msg.text:
            lines.append(msg.text)
        for tc in msg.tool_calls:
            lines.append(_format_tool_call(tc))
        lines.append("")  # blank line between messages
    return "\n".join(lines).rstrip() + "\n"


def _build_review_prompt(transcript_text: str) -> str:
    """Assemble the user-input string for ``claude -p``.

    The prompt + transcript is sent as a single user message. We don't
    use ``--system-prompt`` overrides — claude's default system prompt
    plus our prompt-as-instruction works well enough that the curator
    uses the same pattern (see ``core/curator.py``).
    """
    return f"{_LEARNING_REVIEW_PROMPT}\n\n## Conversation transcript\n\n{transcript_text}"


# --------------------------------------------------------------------
# Output extraction (parse claude's response)
# --------------------------------------------------------------------


# "Nothing to save." with optional trailing/leading whitespace and an
# optional period. Case-insensitive because the model occasionally
# Title-Cases.
_NOTHING_RE = re.compile(r"^\s*nothing\s+to\s+save\.?\s*$", re.IGNORECASE)

# Code fence: matches ```json\n...\n``` or ```\n...\n```. Non-greedy
# body so multiple fences in one response don't merge.
_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.+?)\n```", re.DOTALL)


def _extract_lessons(text: str) -> str | list[dict] | None:
    """Parse claude's response into a structured form.

    Returns:
      - the literal string ``"nothing-to-save"`` if the response is
        the canonical no-write signal,
      - a list of dicts (possibly empty) when JSON parses cleanly,
      - ``None`` if neither shape matches (controller surfaces this
        as a parse-failure outcome — better noisy than silent).

    Robust to: code fences (```json ... ```), single-object responses
    (wraps into a list), trailing prose after the JSON (greedy
    bracket-matching), leading prose before the JSON.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if _NOTHING_RE.match(stripped):
        return "nothing-to-save"

    # 1. Strip code fences first.
    body = stripped
    fence_match = _FENCE_RE.search(body)
    if fence_match:
        body = fence_match.group(1).strip()

    # 2. Try the whole body as JSON.
    parsed = _try_parse(body)
    if parsed is not None:
        return parsed

    # 3. Look for a JSON array inside (greedy outer brackets).
    arr_match = re.search(r"\[.*\]", body, re.DOTALL)
    if arr_match:
        parsed = _try_parse(arr_match.group(0))
        if parsed is not None:
            return parsed

    # 4. Look for a single JSON object containing "lesson".
    obj_match = re.search(
        r"\{[^{}]*\"lesson\"[^{}]*\}", body, re.DOTALL
    )
    if obj_match:
        parsed = _try_parse(obj_match.group(0))
        if parsed is not None:
            return parsed

    return None


def _try_parse(text: str) -> list[dict] | None:
    """Parse ``text`` as JSON and normalize to a list of dicts.

    A single object becomes ``[obj]``; an array stays as-is (with
    non-dict elements filtered). Returns None on json.JSONDecodeError
    so the caller falls through to the next extraction strategy.
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return None


# --------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------


def _verify_evidence(
    evidence: str, messages: list[TranscriptMessage]
) -> bool:
    """True iff ``evidence`` appears verbatim as a substring of some
    user message's text in this transcript.

    Exact substring match — no whitespace normalization. If the LLM
    paraphrases or trims punctuation, the verifier rejects. That's
    the right behavior: we'd rather drop a maybe-real lesson than
    promote a hallucinated quote into long-term memory.
    """
    if not evidence:
        return False
    for msg in messages:
        if msg.role != "user":
            continue
        if evidence in msg.text:
            return True
    return False


def _validate_lesson(
    candidate: dict,
    messages: list[TranscriptMessage],
    *,
    max_chars: int,
) -> tuple[bool, str]:
    """Per-candidate validation. Returns (ok, reason).

    Order matters: cheapest checks first (presence, length, scanner,
    then verifier) so a malformed batch doesn't burn the transcript
    scan on every entry.
    """
    if not isinstance(candidate, dict):
        return False, "candidate is not a JSON object"
    lesson = candidate.get("lesson")
    evidence = candidate.get("evidence")
    scope = candidate.get("scope")
    if not isinstance(lesson, str) or not lesson.strip():
        return False, "missing or empty 'lesson'"
    if not isinstance(evidence, str) or not evidence.strip():
        return False, "missing or empty 'evidence'"
    if not isinstance(scope, str) or not scope.strip():
        return False, "missing or empty 'scope'"
    if len(lesson) > max_chars:
        return False, f"lesson exceeds {max_chars} chars"
    sensitive = _scan_lesson_for_sensitive_content(lesson, scope)
    if sensitive:
        return False, f"sensitive-content match: {sensitive}"
    if not _verify_evidence(evidence, messages):
        return False, "evidence not found verbatim in any user message"
    return True, ""


# --------------------------------------------------------------------
# Sensitive-content scanner (defense-in-depth for Hard Rule 5)
# --------------------------------------------------------------------


# Hard Rule 5 in the prompt forbids medical / legal / financial
# advice in lessons. The prompt is the first line; this scanner is
# the second. We match against the rendered lesson AND scope, but
# NOT against ``evidence`` — evidence is the verbatim user quote and
# the user may legitimately use clinical-sounding words ("my doctor
# said to take ibuprofen") without that being grounds to reject.
# We're trying to catch lessons that promote bad advice into every
# future session's system prompt, not user vocabulary.
#
# Patterns are intentionally conservative — false positives here are
# cheap (we drop a candidate; the LLM can produce another next time),
# false negatives are expensive (the entry lands in long-term memory).
_LEARNING_THREAT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Medical
    (re.compile(r"\b(prescription|prescribe|prescribed)\b", re.I),
     "medical:prescription"),
    (re.compile(r"\b(dosage|dose|mg/kg|mg\s*per\s*kg)\b", re.I),
     "medical:dosage"),
    (re.compile(r"\b(symptom|diagnos[ie]s|diagnose)\b", re.I),
     "medical:diagnosis"),
    (re.compile(r"\b(antibiotic|medication|allerg(?:y|ies|ic))\b", re.I),
     "medical:treatment"),
    (re.compile(r"\b(treat(?:ment)?\s+for|treatment\s+plan)\b", re.I),
     "medical:treatment"),
    # Legal
    (re.compile(r"\b(legal\s+advice|attorney(?:s|'s)?\s+advice)\b", re.I),
     "legal:advice"),
    (re.compile(r"\b(lawsuit|sue\s+|liabl[ey]|liability)\b", re.I),
     "legal:liability"),
    (re.compile(r"\b(jurisdiction|legal\s+counsel|contract\s+clause)\b", re.I),
     "legal:counsel"),
    # Financial
    (re.compile(r"\b(invest(?:ment|ing)?\s+(?:advice|strategy|plan))\b", re.I),
     "financial:advice"),
    (re.compile(r"\b(stock\s+pick|portfolio\s+allocation|trade\s+recommendation)\b", re.I),
     "financial:trading"),
    (re.compile(r"\b(tax\s+advice|tax\s+strategy|financial\s+advisor)\b", re.I),
     "financial:tax"),
    (re.compile(r"\b(buy\s+/\s+sell|when\s+to\s+(?:buy|sell)\s+(?:stocks?|crypto))\b", re.I),
     "financial:trading"),
)


def _scan_lesson_for_sensitive_content(lesson: str, scope: str) -> str | None:
    """Return a pattern id if ``lesson`` or ``scope`` matches a sensitive
    pattern; None otherwise.

    Evidence is intentionally NOT scanned — that's verbatim user text
    and the user may quote clinical/legal/financial vocabulary
    without the lesson itself being unsafe. The danger is in lessons
    that get re-spoken by the model in every future session, not in
    the user's own words being archived as evidence.
    """
    target = f"{lesson}\n{scope}"
    for pattern, pid in _LEARNING_THREAT_PATTERNS:
        if pattern.search(target):
            return pid
    return None


# --------------------------------------------------------------------
# Subprocess runner
# --------------------------------------------------------------------


SpawnFn = Callable[[list[str], dict[str, str]], subprocess.CompletedProcess]


def run_review(
    workspace: Path,
    meta: SessionMeta,
    messages: list[TranscriptMessage],
    *,
    spawn: SpawnFn | None = None,
) -> ReviewOutput:
    """Spawn ``claude -p`` for one session, parse, verify, classify.

    Does not write anywhere — that's the controller's job. Pure
    function modulo the subprocess; the ``spawn`` parameter exists
    for tests and is ``None`` in production (we shell out via
    ``subprocess.run``).

    Subprocess shape mirrors ``core/curator.py:_curator_subprocess_argv``:
    no ``--resume`` (the review must stay isolated from the user's
    session UUID), no ``--append-system-prompt``, env carries
    ``VEXIS_LEARNING_REVIEW=1`` for recursion-guard inheritance.
    """
    transcript = _format_transcript(messages)
    output = ReviewOutput(
        transcript_chars=len(transcript),
        transcript_messages=len([m for m in messages if m.text or m.tool_calls]),
    )

    # Hard decline: skip the LLM entirely if the transcript is past
    # the safety threshold. Returns a fully-formed ReviewOutput with
    # ``declined_too_large=True`` so the caller can advance
    # ``last_reviewed_at`` and avoid the cooldown loop. NOT an error
    # — declining a too-large transcript is a successful outcome
    # ("we chose not to review this") under the v1 strategy.
    if output.transcript_chars > LEARNING_TRANSCRIPT_DECLINE_CHARS:
        log.info(
            "Learning review for session %s: declining (%d chars > "
            "%d threshold). Marking session reviewed to break the "
            "cooldown loop. If this fires for >10%% of sessions over "
            "a week, revisit with truncation or summarization.",
            meta.session_uuid,
            output.transcript_chars,
            LEARNING_TRANSCRIPT_DECLINE_CHARS,
        )
        output.declined_too_large = True
        return output

    if output.transcript_chars > LARGE_TRANSCRIPT_WARN_CHARS:
        log.warning(
            "Learning review for session %s: large transcript "
            "(%d chars / %d non-empty messages). Sending without "
            "truncation; watch for context-limit errors in the "
            "subprocess output.",
            meta.session_uuid,
            output.transcript_chars,
            output.transcript_messages,
        )

    prompt = _build_review_prompt(transcript)
    argv = ["claude", "-p", prompt]
    env = {**os.environ, RECURSION_ENV_VAR: "1"}

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
                timeout=LEARNING_REVIEW_TIMEOUT_SECONDS,
            )
    except subprocess.TimeoutExpired:
        output.error = f"timed out after {LEARNING_REVIEW_TIMEOUT_SECONDS}s"
        return output
    except (OSError, FileNotFoundError) as exc:
        output.error = f"spawn failed: {exc}"
        return output

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
        # Truncate to keep reviewed.json readable; the full body lives
        # in the daemon log anyway.
        body = (stderr or stdout).strip()
        output.error = f"claude -p exited {cp.returncode}: {body[:500]}"
        return output

    output.raw_response = stdout.strip()

    parsed = _extract_lessons(output.raw_response)
    if parsed == "nothing-to-save":
        output.nothing_to_save = True
        return output
    if parsed is None or not isinstance(parsed, list):
        output.error = (
            "could not parse JSON or 'Nothing to save.' from response"
        )
        return output

    output.parsed_lessons = parsed

    max_entries = learning_max_entries_per_session()
    max_chars = learning_max_entry_chars()

    # First N candidates get validated; anything beyond is rejected
    # with the cap reason so the audit surface shows what got dropped.
    for cand in parsed[:max_entries]:
        ok, reason = _validate_lesson(cand, messages, max_chars=max_chars)
        if ok:
            output.verified_lessons.append(cand)
        else:
            output.rejected.append((cand, reason))
    for cand in parsed[max_entries:]:
        output.rejected.append((cand, f"exceeded max-{max_entries} cap"))

    return output


__all__ = [
    "LEARNING_REVIEW_TIMEOUT_SECONDS",
    "LARGE_TRANSCRIPT_WARN_CHARS",
    "LEARNING_TRANSCRIPT_DECLINE_CHARS",
    "RECURSION_ENV_VAR",
    "ReviewOutput",
    "SpawnFn",
    "run_review",
    # Internal helpers exported for direct unit testing:
    "_LEARNING_REVIEW_PROMPT",
    "_LEARNING_THREAT_PATTERNS",
    "_format_transcript",
    "_extract_lessons",
    "_verify_evidence",
    "_validate_lesson",
    "_build_review_prompt",
    "_scan_lesson_for_sensitive_content",
]
