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

from core.memory import ENTRY_DELIMITER as _MEMORY_ENTRY_DELIMITER
from core.paths import memories_dir, skills_dir, user_candidates_path
from core.skills import PinStore, discover_skills
from core.transcripts import SessionMeta, TranscriptMessage
from core.user_candidates import UserCandidateStore
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


# Placeholders for the contextual sections rendered in by
# _build_review_prompt. Double-curlies disambiguate from the JSON
# example object's single-curly braces in the prompt body.
_SKILL_INDEX_MARKER = "{{SKILL_INDEX}}"
_EXISTING_MEMORY_MARKER = "{{EXISTING_MEMORY}}"
_USER_QUEUE_MARKER = "{{USER_CANDIDATE_QUEUE}}"

_LEARNING_REVIEW_PROMPT = """\
You are reviewing a finished Vexis session for memorable lessons to
promote into Vexis's long-term knowledge. Your output is read by an
automated verifier that will reject malformed, unevidenced, or
mis-classified output. Verified lessons are routed by the dispatcher
to one of: an existing skill (patch), a support file under an
existing skill, a brand-new umbrella skill, the USER profile, or
the MEMORY notes file. Each target is injected into every future
session's system prompt — a bad write affects every future task; a
missed write affects this one.

## What counts as a lesson

A lesson is a GENERAL rule or fact, derived from a SPECIFIC user
signal in this session, that would help a future session do its
job better.

Strong signals (any one warrants a lesson candidate):
  - The user explicitly corrected something Vexis did. ("no, do it
    this way", "stop X", "don't Y", "filter out Z").
  - The user said "remember this" or "from now on" or "when X happens
    do Y" — durable instruction.
  - The user expressed a workflow preference applicable to a class of
    tasks ("when I ask for a list, default to the top 5").
  - The user revealed a durable identity / preference fact about
    themselves ("I prefer terse responses", "I work in Python").
  - The user surfaced a durable environmental fact ("the box is at
    203.0.113.42 behind Tailscale").

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

## Classification — required before output

Before emitting your output, classify the lesson into ONE of:

  PROCEDURAL — A general RULE about HOW to do a class of task.
    Phrasing test: starts with "when X" or "before Y" or "after Z";
    describes an action or check the agent should take.
    Example: "When listing time-bound options, filter to entries
    still ahead of the current time."
    Routes to: a SKILL (patch existing, add support file, or create
    new umbrella).

  IDENTITY — A durable fact about WHO the user is or HOW they want
    Vexis to behave that is NOT conditional on a specific task.
    Phrasing test: starts with "user is …" or "user prefers …" or
    "user works in …"; persists across sessions and topics.
    Example: "User prefers concise responses with no preamble."
    Routes to: USER.md (the user-profile file).

  SITUATIONAL — A factual claim about the user's environment,
    tools, constraints, or current setup that is NOT procedural and
    NOT identity. Examples: server addresses, daemon names, hardware
    quirks, persistent service URLs.
    Example: "User runs Vexis on Hetzner VPS at 203.0.113.42."
    Routes to: MEMORY.md (the agent notes file).

  VOLATILE — A fact about what the user is doing RIGHT NOW that
    will not outlast the week. DO NOT PROMOTE.
    Example: "User is currently debugging WhatsApp ingest pipeline
    today."
    Routes to: nowhere — drop with class=VOLATILE.

Hard rules for classification:
  - If the lesson would fire across multiple distinct future tasks,
    it is PROCEDURAL.
  - If the lesson is about the user's preferred response style,
    formatting, tone, communication, or boundaries, it is IDENTITY.
  - If the lesson would expire within a week, it is VOLATILE — drop.
  - When in doubt between PROCEDURAL and IDENTITY: PROCEDURAL wins.
    Skills compose; identity facts cumulate, and we'd rather
    over-promote skills than over-promote identity.

## Procedural lessons → skill tier order

If `class` = PROCEDURAL, pick the earliest tier that fits:

  S1. UPDATE AN EXISTING SKILL via vexis-skill patch.
      For each skill in the index below, ask: "does this lesson
      extend, refine, or correct what this skill says?" If yes,
      patch that skill. The patch should ADD a labeled subsection
      or pitfall, not replace existing text wholesale.
      Output requires `target.skill_name`, `target.patch_old_string`,
      `target.patch_new_string`.

  S2. ADD A SUPPORT FILE under an existing skill via
      vexis-skill write-file. Use this when the lesson is
      session-specific detail (an error transcript, a reproduction
      recipe, a domain-specific reference) that belongs under an
      umbrella but doesn't change the umbrella's top-level
      instructions. Place under references/, templates/, or
      scripts/ as appropriate.
      Output requires `target.skill_name`, `target.support_file_path`,
      `target.support_file_content`.

  S3. CREATE A NEW CLASS-LEVEL UMBRELLA SKILL via vexis-skill
      create. Only when no existing skill covers the class.
      The name MUST be at the class level — describe the class
      of task, not the session artifact. The name MUST NOT be a
      specific error string, a feature codename, a library name
      alone, a date, or "fix-X / debug-Y / audit-Z-today". If
      the proposed name only makes sense for today's task, it's
      wrong — fall back to S1 or S2.

      The new skill MUST include `origin: learning-curator` in
      its YAML frontmatter so the audit trail is preserved.
      Output requires `target.skill_name`, `target.new_skill_body`,
      and optionally `target.new_skill_category`.

Hard cap: at MOST one S3 lesson per session (the verifier rejects
the second). The other slot must be S1, S2, IDENTITY, SITUATIONAL,
or "Nothing to save." S3 is the highest-blast-radius write and
self-rate-limits accordingly.

## Existing skills you can patch (S1) or extend with support files (S2)

The following skills already exist. For each, ask whether the new
lesson belongs INSIDE one of them. Quote the skill name verbatim in
your output's `target.skill_name` field.

<skill-index>
""" + _SKILL_INDEX_MARKER + """
</skill-index>

If the lesson would meaningfully fit inside one of these, return
tier S1 or S2 and identify the skill by name. If you are splitting
hairs to make it fit, that's a signal it doesn't — prefer S3 (new
umbrella) in that case, but ONLY if the lesson is truly class-level
(see anti-narrowing rules above).

Skills marked ``(pinned, read-only)`` cannot be patched or extended
with support files — the user has frozen them. Do NOT propose S1 or
S2 against a pinned skill; the verifier will reject. They are still
listed so you can avoid naming a new S3 umbrella that collides with
a pinned name.

When the index above is empty, S1 and S2 are not available — pick
S3 for any PROCEDURAL lesson.

## Existing memory entries — avoid duplicates

The following entries are already in MEMORY.md (and/or the shadow
file). If your candidate lesson restates one of them — same rule,
similar phrasing — return "Nothing to save." for that candidate
rather than proposing a duplicate. The verifier also runs an exact
substring check on `evidence` against these entries and will reject
verbatim duplicates regardless.

<existing-memory>
""" + _EXISTING_MEMORY_MARKER + """
</existing-memory>

## USER candidate queue — alias path for IDENTITY claims

The following IDENTITY claims are accumulating toward USER.md
promotion. Each gets promoted only when it has been observed in
≥2 distinct sessions within a 30-day window. Items marked
``[promoted]`` are already in USER.md and listed here for
reference (don't propose duplicates of those either).

<user-candidates>
""" + _USER_QUEUE_MARKER + """
</user-candidates>

If your IDENTITY candidate restates one of these queue claims under
different phrasing — same identity fact, different words — return
``target.user_claim_alias`` set to the existing claim's text
verbatim. The dispatcher will record this session as another
occurrence of the existing claim instead of creating a new queue
entry; that's how the threshold gets crossed across sessions.

If your IDENTITY candidate is a genuinely new claim (no existing
queue entry covers it), omit ``target`` entirely. The dispatcher
creates a new queue entry on first observation.

## Output contract

Emit either the literal string

    Nothing to save.

OR a JSON array of lesson objects (max 2). Each object:

    {
      "class":    "PROCEDURAL" | "IDENTITY" | "SITUATIONAL" | "VOLATILE",
      "lesson":   "<≤400 chars, target ~250-300, the rule or fact>",
      "evidence": "<verbatim user message that triggered this — must
                   appear word-for-word in the session>",
      "scope":    "<one-line description of what class of tasks/contexts
                   this applies to>",
      "tier":     "<for PROCEDURAL only: 'S1' | 'S2' | 'S3'>",
      "target": {
        "skill_name":            "<S1/S2: existing skill; S3: new name>",
        "patch_old_string":      "<S1 only: verbatim text in the existing SKILL.md to replace>",
        "patch_new_string":      "<S1 only: replacement text>",
        "support_file_path":     "<S2 only: e.g. references/dutch-rag.md>",
        "support_file_content":  "<S2 only: the file contents>",
        "new_skill_body":        "<S3 only: full SKILL.md including YAML frontmatter with origin: learning-curator>",
        "new_skill_category":    "<S3 only: optional category, omit if uncategorised>"
      }
    }

For IDENTITY:
  - omit `tier` entirely.
  - omit `target` for a fresh claim (no queue entry covers it).
  - include `target = {"user_claim_alias": "<existing claim text
    verbatim>"}` if your candidate restates an existing queue
    claim. This deduplicates within the queue.

For SITUATIONAL, omit both `tier` and `target`.
For VOLATILE, do not emit a candidate at all — return "Nothing to
save." instead.

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
2. ``lesson`` must be ≤400 chars (verifier-enforced). Aim for
   250-300 chars in normal cases — that's enough room for one
   rule plus a parenthetical example or scope qualifier. The 400
   ceiling is a hard guard against manifesto-length lessons; if
   you're approaching it you're over-generalizing or trying to
   pack two rules into one entry. Split into two lessons or
   narrow the scope instead.
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
7. ``class`` is REQUIRED. The verifier rejects candidates without
   a valid class enum value.
8. For PROCEDURAL: ``tier`` is REQUIRED and must be S1, S2, or S3.
   ``target`` must carry the fields named in the output contract
   for that tier. The verifier rejects shape mismatches.
9. For S1: ``target.patch_old_string`` is the verbatim text to
   replace. The verifier checks that the existing SKILL.md actually
   contains that string. If you don't know the SKILL.md body
   verbatim, choose S2 or S3 instead.
10. For S3: the new skill name MUST NOT collide with an existing
    skill name shown in the skill index. The verifier rejects
    collisions and you should fall back to S1 or S2.
11. Do not propose duplicates of entries already shown in the
    "Existing memory entries" section. Return "Nothing to save."
    for that candidate.
12. For IDENTITY: if your claim restates an existing queue entry
    under different phrasing, set ``target.user_claim_alias`` to
    the existing claim text verbatim. Do not create a fresh queue
    entry for an alias of an existing one — the threshold is per-
    claim, and aliasing is what lets two paraphrased observations
    cross it.
13. For IDENTITY: do not propose claims about religion, politics,
    sexuality, named third parties (girlfriend's name, etc.), or
    self-harm / mental-health disclosures. The verifier rejects
    these — USER.md is read aloud to the model in every session
    and these categories should not be auto-promoted from a single
    observation. The user can hand-add to USER.md via the memory
    tool if they want them there.
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


# --------------------------------------------------------------------
# Skill-index and existing-memory rendering for the v2 prompt
# --------------------------------------------------------------------


# Cap to keep the skill index from blowing the prompt budget. With
# ~10-30 active skills the cap is moot; only fires when the tree
# grows past the v2 design's expected scale.
_MAX_SKILL_INDEX_CHARS = 6000

# Cap on existing-memory rendering. MEMORY.md is capped at 2200 chars
# and MEMORY-SHADOW.md is currently ~12k; we render up to this many
# chars combined and truncate with a marker if more.
_MAX_EXISTING_MEMORY_CHARS = 8000

# Cap on USER candidate queue rendering. The queue is an unbounded
# accumulator (size depends on user activity); 4000 chars holds
# roughly 25 short claims with metadata. Truncation is a marker so
# the LLM knows there's more it can't see.
_MAX_USER_QUEUE_CHARS = 4000


def _render_skill_index(skills_root: Path) -> str:
    """Render the active-skills list for the prompt's skill-index slot.

    Format: one line per skill, ``- name: description`` with an
    optional ``(pinned, read-only)`` suffix on pinned skills.
    The LLM uses this to:
      - pick S1/S2 targets (cannot patch pinned),
      - avoid S3 name collisions (pinned names are still listed
        precisely so the LLM doesn't propose a colliding new
        umbrella),
      - decide when S3 is required (none of the existing skills fit).

    Skips archived skills (``iter_skill_dirs`` already excludes
    ``.archive``). Returns an explicit empty-state message when no
    skills exist so the LLM understands S3 is the only option.
    """
    try:
        metas = discover_skills(skills_root)
    except OSError as exc:
        log.warning("Could not enumerate skills at %s: %s", skills_root, exc)
        return "(skill index unavailable — proceed without S1/S2)"
    if not metas:
        return "(no skills exist yet — pick S3 for any procedural lesson)"
    try:
        pinned = set(PinStore(skills_root).list())
    except OSError as exc:
        log.warning("Could not load pin store at %s: %s", skills_root, exc)
        pinned = set()
    lines: list[str] = []
    used = 0
    for meta in metas:
        # Truncate descriptions to keep the index legible. The full
        # description is in the SKILL.md frontmatter if the LLM needs
        # it (and the model can see the body via skill_view in tier-2
        # write mode).
        desc = meta.description
        if len(desc) > 200:
            desc = desc[:197] + "..."
        suffix = " (pinned, read-only)" if meta.name in pinned else ""
        line = f"- {meta.name}: {desc}{suffix}"
        if used + len(line) + 1 > _MAX_SKILL_INDEX_CHARS:
            lines.append(f"... (and {len(metas) - len(lines)} more skills, truncated)")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _render_existing_memory(workspace: Path) -> str:
    """Render the existing-memory bullet list for the prompt's dedup slot.

    Reads BOTH MEMORY.md and MEMORY-SHADOW.md so the LLM can see
    everything that's effectively in the user's memory pool (the
    shadow file is what the curator has staged but not yet flipped
    live). Entries from both files are concatenated and deduped by
    exact text match.

    Returns ``"(no existing entries)"`` when both files are empty so
    the LLM understands that and doesn't hallucinate constraints.
    """
    entries = _load_existing_memory_entries(workspace)
    if not entries:
        return "(no existing entries)"
    lines: list[str] = []
    used = 0
    for i, entry in enumerate(entries, start=1):
        # Render each entry as a single bullet with a 1-line preview.
        # The full text is on disk if the LLM ever needs it; for
        # dedup judgment, the first line + lesson is what matters.
        first_line = entry.split("\n", 1)[0]
        preview = first_line if len(first_line) <= 240 else first_line[:237] + "..."
        bullet = f"{i}. {preview}"
        if used + len(bullet) + 1 > _MAX_EXISTING_MEMORY_CHARS:
            lines.append(f"... (and {len(entries) - len(lines)} more entries, truncated)")
            break
        lines.append(bullet)
        used += len(bullet) + 1
    return "\n".join(lines)


def _load_existing_memory_entries(workspace: Path) -> list[str]:
    """Return all distinct ``§``-delimited entries across MEMORY.md
    and MEMORY-SHADOW.md. First-seen wins for dedup."""
    seen: dict[str, None] = {}
    for name in ("MEMORY.md", "MEMORY-SHADOW.md"):
        path = memories_dir(workspace) / name
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.debug("Could not read %s for dedup: %s", path, exc)
            continue
        for chunk in raw.split(_MEMORY_ENTRY_DELIMITER):
            stripped = chunk.strip()
            if stripped:
                seen.setdefault(stripped, None)
    return list(seen.keys())


def _render_user_candidate_queue() -> str:
    """Render the USER.md candidate queue for the prompt's queue slot.

    Format: numbered bullets with the claim text, occurrence count,
    and a [promoted] marker for already-promoted entries. The LLM
    uses this to decide whether to alias an IDENTITY candidate to
    an existing claim or to create a fresh queue entry.

    Returns ``"(no pending or promoted USER claims yet)"`` when the
    queue is empty so the LLM doesn't misread an empty block as a
    skip signal.

    The queue file is at ``~/.vexis/learning/user_candidates.json``
    — the SAME location the dispatcher writes to. Reads don't lock
    (atomic rename means readers see either old or new state, never
    a tear) so this rendering is safe to call inline in run_review
    without coordinating with concurrent writes.
    """
    try:
        store = UserCandidateStore(user_candidates_path())
        candidates = store.list_all()
    except OSError as exc:
        log.warning("Could not read user candidate queue: %s", exc)
        return "(USER queue unavailable — proceed without alias context)"
    if not candidates:
        return "(no pending or promoted USER claims yet)"
    lines: list[str] = []
    used = 0
    for i, c in enumerate(candidates, start=1):
        # Show distinct-session count as the "progress toward threshold"
        # signal, not raw occurrence count (which can over-count when
        # a single session emitted the claim multiple times).
        distinct = len(c.distinct_session_uuids())
        marker = " [promoted]" if c.promoted_to_user_md else ""
        # Truncate long claims to keep the queue legible.
        claim_preview = c.claim if len(c.claim) <= 240 else c.claim[:237] + "..."
        line = f"{i}. \"{claim_preview}\" ({distinct} session(s)){marker}"
        if used + len(line) + 1 > _MAX_USER_QUEUE_CHARS:
            lines.append(
                f"... (and {len(candidates) - len(lines)} more queue entries, truncated)"
            )
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _check_evidence_overlap(
    candidate_evidence: str, existing_entries: list[str]
) -> tuple[bool, int | None]:
    """Exact substring dedup gate.

    Returns ``(True, index)`` if ``candidate_evidence`` is a substring
    of some existing entry's text OR an existing entry's text contains
    the candidate evidence verbatim. The index is 1-based to match the
    bullet numbering in ``_render_existing_memory`` (so error messages
    match what the LLM saw). Returns ``(False, None)`` if no overlap.

    Cheap in-process check — no LLM call. The semantic gate is
    delegated to the LLM via ``_render_existing_memory`` in the
    prompt; this is the belt-and-suspenders backup.
    """
    if not candidate_evidence:
        return False, None
    needle = candidate_evidence.strip()
    if not needle:
        return False, None
    for i, entry in enumerate(existing_entries, start=1):
        # Bidirectional substring: catches both
        # (a) the new candidate quotes a fragment of an old entry, and
        # (b) an old entry quoted a fragment of what's now a longer
        #     candidate quote.
        if needle in entry or entry in needle:
            return True, i
    return False, None


def _build_review_prompt(
    transcript_text: str,
    *,
    skill_index_text: str = "",
    existing_memory_text: str = "",
    user_queue_text: str = "",
) -> str:
    """Assemble the user-input string for ``claude -p``.

    The prompt + transcript is sent as a single user message. We don't
    use ``--system-prompt`` overrides — claude's default system prompt
    plus our prompt-as-instruction works well enough that the curator
    uses the same pattern (see ``core/curator.py``).

    Context blocks substituted into placeholder markers:
      - ``skill_index_text``      → ``{{SKILL_INDEX}}``
      - ``existing_memory_text``  → ``{{EXISTING_MEMORY}}``
      - ``user_queue_text``       → ``{{USER_CANDIDATE_QUEUE}}``  (Day 3)

    When any block is omitted (e.g. tests that only check prompt
    structure), an empty-state placeholder is rendered in its place so
    the prompt still parses.
    """
    skill_text = skill_index_text or "(skill index not provided)"
    memory_text = existing_memory_text or "(existing memory not provided)"
    queue_text = user_queue_text or "(USER candidate queue not provided)"
    body = (
        _LEARNING_REVIEW_PROMPT
        .replace(_SKILL_INDEX_MARKER, skill_text)
        .replace(_EXISTING_MEMORY_MARKER, memory_text)
        .replace(_USER_QUEUE_MARKER, queue_text)
    )
    return f"{body}\n\n## Conversation transcript\n\n{transcript_text}"


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


# Valid v2 enum values. Centralised so the validator and tests stay
# in lock-step.
_VALID_CLASSES: frozenset[str] = frozenset({
    "PROCEDURAL", "IDENTITY", "SITUATIONAL", "VOLATILE",
})
_VALID_TIERS: frozenset[str] = frozenset({"S1", "S2", "S3"})


def _validate_target_shape(
    class_: str, tier: str | None, target: dict | None
) -> str | None:
    """Validate that the ``target`` dict carries the right fields for
    the chosen tier. Returns a reason string on rejection, None on OK.

    Day 1 scope: shape-only validation. Day 2 adds the existence/
    collision checks (does the named skill actually exist for S1/S2,
    is the new name free for S3, does the patch_old_string actually
    appear in the existing SKILL.md). Day 3 adds the IDENTITY alias
    target shape — IDENTITY may carry an optional
    ``target.user_claim_alias`` pointing to an existing queue claim.
    """
    if class_ == "IDENTITY":
        # IDENTITY may carry NO target (fresh claim) OR a target
        # with EXACTLY ``user_claim_alias`` (and nothing else, so
        # we don't accidentally accept procedural-shaped fields).
        if tier is not None:
            return f"class=IDENTITY must not include 'tier' (PROCEDURAL-only)"
        if target is None:
            return None
        if not isinstance(target, dict):
            return "IDENTITY 'target' must be a JSON object when present"
        alias = target.get("user_claim_alias")
        if not isinstance(alias, str) or not alias.strip():
            return (
                "IDENTITY 'target' must contain a non-empty "
                "'user_claim_alias' string when present"
            )
        # Reject any sibling keys — keeps the contract tight.
        extra_keys = set(target.keys()) - {"user_claim_alias"}
        if extra_keys:
            return (
                f"IDENTITY 'target' may only contain 'user_claim_alias'; "
                f"got extra keys: {sorted(extra_keys)}"
            )
        return None
    if class_ == "SITUATIONAL":
        # SITUATIONAL never carries tier or target.
        if tier is not None or target is not None:
            return (
                f"class=SITUATIONAL should not include 'tier' or 'target' "
                f"(those are PROCEDURAL/IDENTITY-only)"
            )
        return None
    if class_ != "PROCEDURAL":
        # Defensive — VOLATILE is dropped at the top of validate_lesson;
        # any other unknown class falls through to here.
        if tier is not None or target is not None:
            return (
                f"class={class_} should not include 'tier' or 'target' "
                f"(those are PROCEDURAL-only)"
            )
        return None
    # PROCEDURAL: tier + target required, with shape varying by tier.
    if tier not in _VALID_TIERS:
        return f"PROCEDURAL requires tier in {sorted(_VALID_TIERS)}; got {tier!r}"
    if not isinstance(target, dict):
        return "PROCEDURAL requires 'target' as a JSON object"
    skill_name = target.get("skill_name")
    if not isinstance(skill_name, str) or not skill_name.strip():
        return f"tier {tier} requires 'target.skill_name' as a non-empty string"
    if tier == "S1":
        for key in ("patch_old_string", "patch_new_string"):
            v = target.get(key)
            if not isinstance(v, str) or not v.strip():
                return f"tier S1 requires 'target.{key}' as a non-empty string"
    elif tier == "S2":
        path = target.get("support_file_path")
        content = target.get("support_file_content")
        if not isinstance(path, str) or not path.strip():
            return "tier S2 requires 'target.support_file_path' as a non-empty string"
        if "/" not in path:
            return (
                "tier S2 'target.support_file_path' must include a "
                "subdir prefix (references/, templates/, or scripts/)"
            )
        first_part = path.split("/", 1)[0]
        if first_part not in {"references", "templates", "scripts"}:
            return (
                f"tier S2 'target.support_file_path' must start with "
                f"references/, templates/, or scripts/; got {first_part!r}"
            )
        if not isinstance(content, str) or not content.strip():
            return "tier S2 requires 'target.support_file_content' as a non-empty string"
    elif tier == "S3":
        body = target.get("new_skill_body")
        if not isinstance(body, str) or not body.strip():
            return "tier S3 requires 'target.new_skill_body' as a non-empty string"
        if "origin: learning-curator" not in body:
            return (
                "tier S3 'target.new_skill_body' must include "
                "'origin: learning-curator' in its YAML frontmatter"
            )
        category = target.get("new_skill_category")
        if category is not None and (
            not isinstance(category, str) or not category.strip()
        ):
            return (
                "tier S3 'target.new_skill_category' must be a non-empty "
                "string when provided (omit the field for uncategorised)"
            )
    return None


def _validate_lesson(
    candidate: dict,
    messages: list[TranscriptMessage],
    *,
    max_chars: int,
) -> tuple[bool, str]:
    """Per-candidate validation. Returns (ok, reason).

    Order matters: cheapest checks first (presence, length, enum,
    target-shape, scanner, then verifier) so a malformed batch
    doesn't burn the transcript scan on every entry.

    v2 additions:
      - ``class`` is required and must be one of the four enums.
      - ``tier`` and ``target`` are required when class=PROCEDURAL,
        with shape varying by tier (S1/S2/S3).
      - VOLATILE candidates are rejected with a fixed reason — the
        prompt instructs the LLM not to emit them, but defense-in-
        depth: if it does, we drop on the floor.
    """
    if not isinstance(candidate, dict):
        return False, "candidate is not a JSON object"
    lesson = candidate.get("lesson")
    evidence = candidate.get("evidence")
    scope = candidate.get("scope")
    class_ = candidate.get("class")
    tier = candidate.get("tier")
    target = candidate.get("target")
    if not isinstance(class_, str) or class_ not in _VALID_CLASSES:
        return False, (
            f"missing or invalid 'class' (must be one of "
            f"{sorted(_VALID_CLASSES)})"
        )
    if class_ == "VOLATILE":
        return False, (
            "class=VOLATILE candidates are dropped — the prompt "
            "instructs not to emit these"
        )
    if not isinstance(lesson, str) or not lesson.strip():
        return False, "missing or empty 'lesson'"
    if not isinstance(evidence, str) or not evidence.strip():
        return False, "missing or empty 'evidence'"
    if not isinstance(scope, str) or not scope.strip():
        return False, "missing or empty 'scope'"
    if len(lesson) > max_chars:
        return False, f"lesson exceeds {max_chars} chars"
    target_err = _validate_target_shape(class_, tier, target)
    if target_err:
        return False, target_err
    # Day 3: IDENTITY claims route to USER.md and run an extended
    # threat-scanner stack (religion/politics, sexuality, named
    # third parties, self-harm/mental-health) on top of the base
    # medical/legal/financial set. PROCEDURAL/SITUATIONAL stay on
    # the base set.
    scanner_target = "user" if class_ == "IDENTITY" else "memory"
    sensitive = _scan_lesson_for_sensitive_content(
        lesson, scope, target_file=scanner_target
    )
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


# Day 3 (v2 §3.4): patterns specifically for IDENTITY claims that
# would land in USER.md. Layered ON TOP OF the base patterns above —
# IDENTITY candidates run both sets, MEMORY/SITUATIONAL candidates
# run only the base set. The bar for USER.md is higher because
# identity content is re-spoken in every future session forever and
# the user almost certainly didn't intend to immortalize it from a
# single observation.
#
# Conservative posture: false positives are cheap (drop the
# candidate; the LLM tries again next session), false negatives
# embed identity claims the user can't easily un-remember.
_USER_MD_THREAT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Religion / faith
    (re.compile(
        r"\buser\s+(?:is|identifies\s+as)\s+(?:a\s+|an\s+)?"
        r"(christian|catholic|protestant|muslim|islamic|jewish|hindu|"
        r"buddhist|sikh|atheist|agnostic|pagan|mormon|jehovah)",
        re.I,
    ), "user:religion"),
    (re.compile(r"\buser\s+(?:practices?|believes?\s+in|follows?)\s+\w+ism\b", re.I),
     "user:religion"),
    (re.compile(r"\buser\s+(?:prays?|attends?\s+(?:church|mosque|synagogue|temple))",
                re.I),
     "user:religion"),
    # Politics / ideology
    (re.compile(
        r"\buser\s+(?:is|votes?|voted|leans?|identifies\s+as)\s+(?:a\s+|an\s+)?"
        r"(conservative|liberal|democrat|republican|leftist|right[\s-]?wing|"
        r"left[\s-]?wing|progressive|libertarian|socialist|communist|fascist|"
        r"maga|woke)",
        re.I,
    ), "user:politics"),
    (re.compile(r"\buser\s+supports?\s+(?:the\s+)?(\w+\s+)?(party|candidate)\b", re.I),
     "user:politics"),
    # Sexuality / orientation / gender identity
    (re.compile(
        r"\buser\s+(?:is|identifies\s+as)\s+(?:a\s+|an\s+)?"
        r"(gay|lesbian|bisexual|bi|straight|heterosexual|homosexual|"
        r"asexual|ace|pansexual|queer|trans(?:gender)?|nonbinary|"
        r"non-binary|enby|cis(?:gender)?)",
        re.I,
    ), "user:sexuality"),
    (re.compile(r"\buser'?s?\s+(?:sexual|romantic)\s+(?:orientation|preference)\b",
                re.I),
     "user:sexuality"),
    (re.compile(r"\buser'?s?\s+(?:preferred\s+)?pronouns?\b", re.I),
     "user:sexuality"),
    # NB: Named-third-party patterns are NOT in this tuple — that
    # check needs an allowlist post-filter (so "Anthropic uses X"
    # doesn't false-positive as a person). It lives in the dedicated
    # ``_check_named_third_party`` function below; the scanner calls
    # both this tuple AND that function when target_file="user".
    #
    # Self-harm / mental-health disclosure. Hard reject — these are
    # never appropriate for an automated system to immortalize, and
    # carry crisis-context that the model should not parrot.
    (re.compile(r"\b(suicidal|suicide|self[\s-]?harm)\b", re.I),
     "user:self-harm"),
    (re.compile(
        r"\buser\s+(?:struggles?|deals?|copes?|battles?|fights?)\s+with\s+"
        r"(depression|anxiety|ptsd|trauma|addiction|alcoholism|"
        r"bipolar|schizophrenia|eating\s+disorder|ocd|adhd)",
        re.I,
    ), "user:mental-health"),
    (re.compile(
        r"\buser\s+(?:is\s+)?(?:in\s+therapy|seeing\s+a\s+therapist|"
        r"on\s+(?:antidepressants|ssris|adhd\s+medication|lithium))",
        re.I,
    ), "user:mental-health"),
    (re.compile(r"\buser'?s?\s+mental\s+health\b", re.I),
     "user:mental-health"),
)


# --------------------------------------------------------------------
# Named-third-party check (Day 3.5 refactor)
# --------------------------------------------------------------------
#
# The named-third-party scanner is the load-bearing safety check for
# USER.md writes — third parties haven't consented to be in Vexis's
# system prompt. The simple-regex-tuple model the other USER.md
# patterns use isn't enough here because we need:
#   1. Multiple patterns covering different syntactic shapes
#      (possessive, transitive verb, subject position, interaction).
#   2. An ALLOWLIST post-filter so capitalized non-person words
#      (Anthropic, Linux, Hetzner, weekday names) don't trigger
#      false-positive rejections.
#
# Adversarial cases this implementation handles (see
# tests/test_learning_review.py::test_named_third_party_*):
#   "User's wife Sarah prefers terse answers"      → REJECTED (A)
#   "Sarah on the team uses Vim"                   → REJECTED (C)
#   "User had a meeting with the Sarah Team Lead"  → REJECTED (D)
#   "User mentioned Sarah in passing"              → REJECTED (E)
#   "User is named John"                           → ALLOWED (self)
#   "User works for Anthropic"                     → ALLOWED (org)


# Capitalized non-person tokens that look like names but aren't —
# orgs, products, technologies, places, weekday/month names, and
# self-reference. The post-filter skips matches whose captured name
# is in this set so e.g. "User uses Linux" doesn't get flagged.
#
# Conservatism dial: this list filters FALSE POSITIVES out of the
# scanner. It's intentionally short so we err toward over-rejecting
# (drop the candidate; the LLM produces another next session) rather
# than under-rejecting (immortalize a third-party fact in USER.md).
# Add tokens here only when a real false positive is observed.
_NON_PERSON_CAPITALIZED: frozenset[str] = frozenset({
    # Self-reference
    "User", "Vexis",
    # Common sentence-start / topic-head words. Day 4 eval surfaced
    # these as false positives — "When asked" and "Code reviews"
    # both matched Pattern C and rejected legit IDENTITY/PROCEDURAL
    # lessons. Add the common English sentence starters and a few
    # frequent topic-noun-as-subject words so the scanner doesn't
    # flag e.g. "When asked a question, give a short answer" as
    # naming a third party.
    "When", "If", "Before", "After", "During", "While", "Until",
    "For", "To", "From", "With", "Without", "Once", "Whenever",
    "Always", "Never", "Sometimes", "Often", "Usually", "Default",
    "Use", "Avoid", "Skip", "Don", "Do", "Both", "Either",
    "Then", "Otherwise", "Note", "Tip", "Warning", "Important",
    "How", "What", "Why", "Where", "Who", "Whose", "Which",
    "Code", "Tests", "Test", "Build", "PR", "API", "CI", "CD",
    "URL", "JSON", "YAML", "HTTP", "HTTPS", "TCP", "UDP",
    "Memory", "Skills", "Skill", "Session", "Sessions", "Tasks",
    # Common AI / dev orgs
    "Claude", "Anthropic", "OpenAI", "Google", "Microsoft", "Apple",
    "Amazon", "Meta", "Nvidia", "Intel",
    # Hosting / infra
    "Hetzner", "Cloudflare", "Tailscale", "Wireguard", "AWS", "Azure",
    # Apps
    "Telegram", "Slack", "Discord", "GitHub", "GitLab", "Bitbucket",
    "Notion", "Linear", "Jira", "Figma", "Zoom",
    # OS / desktop
    "Linux", "Hyprland", "Wayland", "Arch", "Ubuntu", "Debian",
    "Fedora", "MacOS", "Windows", "Omarchy", "Gnome",
    # Languages / runtimes
    "Python", "TypeScript", "JavaScript", "Rust", "Java", "Kotlin",
    "Swift", "Ruby", "Elixir", "Bun", "Deno", "Node",
    # Data / tools
    "Postgres", "PostgreSQL", "MySQL", "SQLite", "Redis", "Docker",
    "Kubernetes", "Terraform", "Ansible", "Nix", "Vim", "Emacs",
    "VSCode", "Neovim",
    # Calendar
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
})


# IMPORTANT: every verb / role alternation below must end with ``\b``
# (closing word boundary) so a partial-prefix match like "use" inside
# "user" can't trigger the scanner. Day 4 eval surfaced this as a
# false positive: Pattern C matched "The use" inside "The user reads"
# because ``uses?`` (without trailing ``\b``) matched the "use"
# prefix of "user". Word boundaries fix this.

# Pattern A: explicit relational possessive — "user's <role> <Name>".
# Roles include family, work, professional. Case-insensitive on the
# preamble; the captured name comes through with original casing
# because re.I makes [A-Z] match lowercase too — so the post-filter
# does an explicit ``name[0].isupper()`` check.
_THIRD_PARTY_POSSESSIVE_RE = re.compile(
    r"\buser'?s?\s+"
    r"(?:wife|husband|spouse|partner|girlfriend|boyfriend|fianc[eé]e?|"
    r"son|daughter|child|kid|sister|brother|mother|mom|father|dad|"
    r"parent|grandmother|grandfather|cousin|aunt|uncle|niece|nephew|"
    r"friend|colleague|coworker|teammate|teammates|"
    r"boss|manager|report|reports|client|customer|"
    r"team\s+lead|tech\s+lead|lead|"
    r"therapist|doctor|dentist|trainer)\b"
    r"(?:\s+(?:is\s+|named\s+|called\s+))?\s+([A-Z][a-z]+)\b",
    re.I,
)

# Pattern B: relationship verbs about the user — "user is married to
# <Name>" / "user is dating <Name>". Distinct from "user is named X"
# (self-reference, no third party).
_THIRD_PARTY_RELATION_RE = re.compile(
    r"\buser\s+(?:is\s+)?(?:married|engaged|divorced|dating|seeing|"
    r"interviewing|hired)\b\s+(?:to\s+|with\s+)?([A-Z][a-z]+)\b",
    re.I,
)

# Pattern C: third-party-as-subject — "<Name> + person-verb". Catches
# "Sarah uses Vim" / "Sarah on the team prefers …". The "on the team"
# alternative is included because it's a common shape: "<Name> on
# the team <verb>". Case-sensitive on the name capture (no re.I)
# so that lowercase verbs at sentence-start don't false-positive.
# Trailing ``\b`` after the verb group prevents prefix matches like
# "use" inside "user" (Day 4 fix).
_THIRD_PARTY_SUBJECT_RE = re.compile(
    r"\b([A-Z][a-z]+)\s+"
    r"(?:on\s+(?:the|our|the\s+\w+)\s+team\s+\w+|"
    r"uses?|used|prefers?|preferred|likes?|liked|loves?|loved|"
    r"hates?|hated|said|says|told|tells|asked|asks|answered|answers|"
    r"wants|wanted|thinks|thought|believes?|believed|"
    r"works?|worked|knows?|knew|"
    r"mentions?|mentioned|texted|emailed|called|messaged|"
    r"sent|gave|gives|made|makes|wrote|writes)\b"
)

# Pattern D: interaction with a named third party — "(meeting|call|
# chat|message) with [the] <Name>". The optional "the" catches
# "meeting with the Sarah Team Lead" — odd grammar but catches the
# shape where the LLM appends a role after the name.
_THIRD_PARTY_INTERACTION_RE = re.compile(
    r"\b(?:meet(?:ing|s|ings)?|call(?:s|ed|ing)?|chat(?:s|ted|ting)?|"
    r"spoke|talked|talking|messaged|emailed|texted|"
    r"discuss(?:ed|ing|ion)?|met)\b\s+(?:with|to)\s+(?:the\s+)?([A-Z][a-z]+)\b"
)

# Pattern E: user-as-subject + transitive verb + named object. Catches
# "User mentioned Sarah" / "User emailed Sarah". Distinct from "user
# is married to Sarah" (handled by B) and "user is named John"
# (allowed: "named" / "is" don't appear here as the verb).
_THIRD_PARTY_TRANSITIVE_RE = re.compile(
    r"\buser\s+(?:mentioned|met|emailed|texted|messaged|called|"
    r"introduced|saw|spoke\s+with|spoke\s+to|talked\s+to|talked\s+with|"
    r"asked|told)\b\s+(?:to\s+)?([A-Z][a-z]+)\b",
    re.I,
)

_THIRD_PARTY_PATTERNS: tuple[re.Pattern[str], ...] = (
    _THIRD_PARTY_POSSESSIVE_RE,
    _THIRD_PARTY_RELATION_RE,
    _THIRD_PARTY_SUBJECT_RE,
    _THIRD_PARTY_INTERACTION_RE,
    _THIRD_PARTY_TRANSITIVE_RE,
)


def _check_named_third_party(text: str) -> str | None:
    """Return ``"user:named-third-party"`` if ``text`` mentions an
    identifiable third-party human by name; None otherwise.

    Five-pattern scanner with allowlist post-filter:
      A. "user's <role> <Name>"        — possessive relational
      B. "user is married to <Name>"   — user's own relational verb
      C. "<Name> + verb"               — third-party as subject
      D. "(meeting|call) with <Name>"  — interaction
      E. "user mentioned <Name>"       — user-as-subject transitive

    Each match's captured group is checked against
    ``_NON_PERSON_CAPITALIZED`` — orgs, products, technologies,
    weekdays — so "User uses Linux" / "User works for Anthropic"
    don't false-positive. Only when the captured token is
    capitalized AND not in the allowlist do we reject.

    The function deliberately uses ``finditer`` so a sentence with
    a benign Anthropic mention plus a real name (e.g. "User uses
    Anthropic and Sarah likes Linux") still rejects on the Sarah
    match.
    """
    for pat in _THIRD_PARTY_PATTERNS:
        for m in pat.finditer(text):
            if not m.lastindex:
                continue
            name = m.group(1)
            if not name:
                continue
            # Must start with an uppercase letter in the source —
            # protects against re.I lowering "uses" or similar verbs
            # being captured by a permissive pattern.
            if not name[0].isupper():
                continue
            if name in _NON_PERSON_CAPITALIZED:
                continue
            return "user:named-third-party"
    return None


def _scan_lesson_for_sensitive_content(
    lesson: str,
    scope: str,
    *,
    target_file: str = "memory",
) -> str | None:
    """Return a pattern id if ``lesson`` or ``scope`` matches a sensitive
    pattern; None otherwise.

    Evidence is intentionally NOT scanned — that's verbatim user text
    and the user may quote clinical/legal/financial vocabulary
    without the lesson itself being unsafe. The danger is in lessons
    that get re-spoken by the model in every future session, not in
    the user's own words being archived as evidence.

    ``target_file`` selects which scanner stack runs:
      - ``"memory"`` (default): the base medical/legal/financial set.
        Used for SITUATIONAL → MEMORY.md and the verifier's first
        pass on every candidate.
      - ``"user"``: the base set PLUS the USER.md-specific patterns
        (religion, politics, sexuality, self-harm, mental health) AND
        the named-third-party scanner. Used for IDENTITY
        classifications whose route is the USER.md candidate queue.
    """
    target = f"{lesson}\n{scope}"
    for pattern, pid in _LEARNING_THREAT_PATTERNS:
        if pattern.search(target):
            return pid
    if target_file == "user":
        for pattern, pid in _USER_MD_THREAT_PATTERNS:
            if pattern.search(target):
                return pid
        # Named-third-party check has its own scanner with allowlist
        # post-filter — runs after the simpler tuple-based patterns.
        third_party = _check_named_third_party(target)
        if third_party:
            return third_party
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

    # v2 context blocks: render the skill index + existing memory +
    # USER candidate queue and substitute into the prompt's
    # placeholder slots. The skill index gives the LLM the S1/S2
    # candidate list and lets it avoid S3 name collisions; the
    # existing memory gives it the semantic dedup context; the
    # candidate queue (Day 3) gives it the alias-vs-fresh signal for
    # IDENTITY classifications. The in-process exact-evidence gate
    # runs after parse, on each verified candidate.
    skill_index_text = _render_skill_index(skills_dir(workspace))
    existing_memory_text = _render_existing_memory(workspace)
    existing_memory_entries = _load_existing_memory_entries(workspace)
    user_queue_text = _render_user_candidate_queue()

    prompt = _build_review_prompt(
        transcript,
        skill_index_text=skill_index_text,
        existing_memory_text=existing_memory_text,
        user_queue_text=user_queue_text,
    )
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
    s3_count = 0
    for cand in parsed[:max_entries]:
        ok, reason = _validate_lesson(cand, messages, max_chars=max_chars)
        if not ok:
            output.rejected.append((cand, reason))
            continue
        # Per-session S3 cap: only one new-umbrella write allowed.
        # See `.plans/learning-curator-v2-research.md` §3.7 #7 for
        # the proportionality-of-blast-radius rationale. Caught here
        # rather than in _validate_lesson because the cap is a
        # cross-candidate concern, not a per-candidate shape check.
        if (
            isinstance(cand, dict)
            and cand.get("class") == "PROCEDURAL"
            and cand.get("tier") == "S3"
        ):
            if s3_count >= 1:
                output.rejected.append(
                    (cand, "exceeded max-1 S3-create cap per session")
                )
                continue
            s3_count += 1
        # Memory dedup: SITUATIONAL candidates (the residual MEMORY.md
        # path) get exact-evidence-overlap checked against the union
        # of MEMORY.md and MEMORY-SHADOW.md. PROCEDURAL routes to
        # skills (different store, different dedup), IDENTITY routes
        # to USER.md (handled in Day 3 with the candidate queue).
        if isinstance(cand, dict) and cand.get("class") == "SITUATIONAL":
            evidence = cand.get("evidence", "")
            hit, idx = _check_evidence_overlap(evidence, existing_memory_entries)
            if hit:
                output.rejected.append(
                    (cand, f"deduped: evidence overlap with existing memory entry #{idx}")
                )
                continue
        output.verified_lessons.append(cand)
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
    "_SKILL_INDEX_MARKER",
    "_EXISTING_MEMORY_MARKER",
    "_USER_QUEUE_MARKER",
    "_VALID_CLASSES",
    "_VALID_TIERS",
    "_USER_MD_THREAT_PATTERNS",
    "_THIRD_PARTY_PATTERNS",
    "_NON_PERSON_CAPITALIZED",
    "_check_named_third_party",
    "_format_transcript",
    "_extract_lessons",
    "_verify_evidence",
    "_validate_lesson",
    "_validate_target_shape",
    "_build_review_prompt",
    "_render_skill_index",
    "_render_existing_memory",
    "_render_user_candidate_queue",
    "_load_existing_memory_entries",
    "_check_evidence_overlap",
    "_scan_lesson_for_sensitive_content",
]
