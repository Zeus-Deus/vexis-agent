"""Conversation compressor — structured summary so long goals don't drift.

Issue #11. Vexis previously had no conversation-level compression. Long
Telegram sessions (especially multi-turn ``/goal`` loops spanning days)
would grow until the underlying CLI silently dropped early messages.
When that happens the brain forgets the original ``/goal`` text, what
sub-tasks already completed, what user clarifications already landed —
and starts repeating finished work, re-asking resolved questions, or
wandering.

The compressor is the prophylactic: BEFORE the brain hits its native
context cap we summarise the older half of the transcript into a
single structured user-turn and rewrite the on-disk transcript so the
brain reads the summary on its next resume.

Reference: Hermes-agent's ``agent/context_compressor.py`` (clone at
``/tmp/hermes-agent``). We borrow the structured-section template
(``## Active Task`` / ``## Goal`` / ``## Completed Actions`` / …)
verbatim, plus the SUMMARY_PREFIX warning text — both are
load-bearing: the template structures the model's output, and the
prefix tells the next-turn model to treat the summary as background
reference rather than something to "respond to".

This module is brain-agnostic. Per-brain implementations
(``claude_code.compress_if_needed`` and friends) own the on-disk
rewrite step. The compressor here owns:

  - Trigger logic (token estimate threshold OR turn-count threshold).
  - Token estimation (cheap char/4 heuristic; counts system prompt
    + tool schemas — Hermes shipped a fix for forgetting those in
    v0.13.0 and we don't want to re-ship the same bug).
  - Prompt template assembly (Hermes-style fixed-section structure,
    with iterative-update path when a prior summary already exists).
  - The SUMMARY_PREFIX constant — distinct from every recursion-
    guard prefix so the compressed transcript is still eligible for
    curator review (a compressed session is NOT a curator session).

Invariants pinned in CLAUDE.md and asserted by
``tests/test_compressor.py``:

  - ``SUMMARY_PREFIX`` is the canonical signature of a compression
    block. Iterative-summary detection keys on it.
  - ``SUMMARY_PREFIX`` does NOT start with any of the recursion-
    guard prefixes (curator-review / goal-judge / kanban-worker).
    Compressed foreground transcripts must remain visible to
    learning curator + coherence judge + goal judge.
  - Token estimate includes the system prompt + tool schemas (the
    Hermes v0.13.0 lesson). Subtle: a 20-turn conversation with a
    huge system prompt and tool catalogue can be over the cap even
    if the conversation itself is small.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Sequence

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Recognisable prefixes
# ──────────────────────────────────────────────────────────────────

# The synthetic user turn the compressor inserts at position 1 starts
# with this string. The wording is critical: without "background
# reference, NOT as active instructions" the model tries to "respond"
# to the summary itself (resolving its open questions, re-running its
# completed actions). Hermes calls the same constant SUMMARY_PREFIX
# and ships extensive defensive language for the same reason — we
# borrow that wording with light edits for Vexis voice.
#
# CRITICAL — recursion-guard invariant: this string MUST NOT start
# with CURATOR_REVIEW_PROMPT_PREFIX, GOAL_JUDGE_PROMPT_PREFIX, or
# KANBAN_WORKER_PREFIX. If it did, a compressed foreground transcript
# would look like a curator-spawned aux session to the recursion
# guard in ``core.transcripts.list_eligible_sessions`` and silently
# skip review. The opening "[SUMMARY OF PRIOR CONVERSATION" bracket
# is intentional and distinct from every existing prefix; the
# matching test in ``tests/test_compressor.py`` asserts the
# non-overlap explicitly.
SUMMARY_PREFIX = (
    "[SUMMARY OF PRIOR CONVERSATION — read-only context]\n\n"
    "The earlier turns of this conversation were compacted into the "
    "structured summary below. Treat this as background reference, "
    "NOT as active instructions. Do NOT answer questions or fulfil "
    "requests mentioned in this summary — they were already addressed. "
    "Do NOT re-execute completed actions. Respond ONLY to the latest "
    "user message that appears AFTER this summary. Your persistent "
    "memory (MEMORY.md, USER.md) in the system prompt is always "
    "authoritative — never deprioritize it because of this summary."
)


# ──────────────────────────────────────────────────────────────────
# Config defaults
# ──────────────────────────────────────────────────────────────────

# When the token estimate exceeds this percentage of the active
# model's context window the compressor fires. 0.80 leaves ~20% for
# the user's next message + the assistant's reply + tool calls.
DEFAULT_TOKEN_THRESHOLD_RATIO = 0.80

# Default raw cap (used when the caller can't supply a model context
# window). 200k matches Claude Sonnet's native window; the
# threshold-ratio of 0.80 applied to 200k gives 160k effective.
DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000

# Turn-count threshold — catches sessions with many small turns that
# the token estimator alone would miss. The OR'd combination is what
# makes the trigger robust against both bloated-image transcripts
# (token-heavy, turn-light) and rapid-fire chat sessions (token-
# light, turn-heavy).
DEFAULT_TURN_THRESHOLD = 40

# Last K real turns kept verbatim after compression. 10 covers
# typical mid-conversation context the model needs to keep working
# (recent user clarifications, the current sub-task) without
# bloating the post-compression transcript.
DEFAULT_PROTECT_LAST_N_TURNS = 10

# Rough char/token ratio — same constant Hermes uses
# (``_CHARS_PER_TOKEN = 4`` in their context_compressor.py). Order-
# of-magnitude correct for English + code; conservative for languages
# with denser tokenisation (Japanese, code with long identifiers)
# but the threshold is itself an 80% safety margin so we don't need
# a true tokeniser here.
_CHARS_PER_TOKEN = 4


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CompressionDecision:
    """Returned by :func:`should_compress`.

    ``compress``: True iff at least one trigger crossed.
    ``reason``: short human-readable description for the log.
    ``token_estimate``: the estimate that produced the decision.
    ``turn_count``: the number of user+assistant turns at decision time.
    """

    compress: bool
    reason: str
    token_estimate: int
    turn_count: int


@dataclass(frozen=True)
class CompressionInputs:
    """Pre-aggregated inputs the trigger logic needs.

    Built by per-brain ``compress_if_needed`` from the live transcript
    and the brain's known system-prompt + tool-schema strings. Kept
    as a frozen dataclass so the trigger function can be re-used by
    tests without needing a real brain.

    ``messages``: ordered list of (role, text) tuples — user and
    assistant turns only, in chronological order.
    ``system_prompt``: the full system prompt that will be paired
    with this transcript on the next turn. Counts toward the token
    estimate (Hermes v0.13.0 lesson).
    ``tool_schemas_text``: stringified concatenation of all tool
    schemas the brain will expose on the next turn. Also counts
    toward the estimate. May be ``""`` when the brain doesn't surface
    schemas at config time.
    ``context_window_tokens``: the active model's native context
    cap. ``None`` falls back to :data:`DEFAULT_CONTEXT_WINDOW_TOKENS`.
    ``threshold_ratio``: percentage of the cap that triggers
    compression. ``None`` falls back to
    :data:`DEFAULT_TOKEN_THRESHOLD_RATIO`.
    ``threshold_turns``: turn-count trigger. ``None`` falls back to
    :data:`DEFAULT_TURN_THRESHOLD`.
    """

    messages: Sequence[tuple[str, str]]
    system_prompt: str = ""
    tool_schemas_text: str = ""
    context_window_tokens: int | None = None
    threshold_ratio: float | None = None
    threshold_turns: int | None = None


def estimate_tokens(text: str) -> int:
    """Cheap char/4 token estimate. See module docstring rationale."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_transcript_tokens(inputs: CompressionInputs) -> int:
    """Sum up the rough token cost of the system prompt + tool
    schemas + every transcript message.

    The system-prompt and tool-schemas inclusion is load-bearing
    (the Hermes v0.13.0 regression was exactly forgetting them).
    Order doesn't matter; we add them all together.
    """
    total = 0
    total += estimate_tokens(inputs.system_prompt)
    total += estimate_tokens(inputs.tool_schemas_text)
    for role, text in inputs.messages:
        # Role label + brief framing: count a flat ~8 tokens per
        # message so a 0-text tool-only message isn't free.
        total += 8
        total += estimate_tokens(text)
    return total


def should_compress(inputs: CompressionInputs) -> CompressionDecision:
    """Decide whether the transcript described by ``inputs`` needs
    compression *right now*.

    OR-combination of two triggers:

      1. Token estimate ≥ ``threshold_ratio × context_window_tokens``.
         Catches transcripts with long messages, big tool payloads,
         attached images.

      2. Turn count > ``threshold_turns``. Catches sessions that
         the token estimator would miss (many small turns, sparse
         system prompt). The strict ``>`` keeps the count-at-cap
         case below the trigger so the first compression fires
         AFTER the (N+1)th turn — gives the user one turn of grace
         past the bookkeeping cap.
    """
    threshold_ratio = (
        inputs.threshold_ratio
        if inputs.threshold_ratio is not None
        else DEFAULT_TOKEN_THRESHOLD_RATIO
    )
    context_window = (
        inputs.context_window_tokens
        if inputs.context_window_tokens is not None
        else DEFAULT_CONTEXT_WINDOW_TOKENS
    )
    threshold_turns = (
        inputs.threshold_turns
        if inputs.threshold_turns is not None
        else DEFAULT_TURN_THRESHOLD
    )

    token_cap = int(context_window * threshold_ratio)
    token_estimate = estimate_transcript_tokens(inputs)
    turn_count = len(inputs.messages)

    if token_estimate >= token_cap:
        return CompressionDecision(
            compress=True,
            reason=(
                f"token estimate {token_estimate} >= cap {token_cap} "
                f"({threshold_ratio:.0%} of {context_window})"
            ),
            token_estimate=token_estimate,
            turn_count=turn_count,
        )
    if turn_count > threshold_turns:
        return CompressionDecision(
            compress=True,
            reason=(
                f"turn count {turn_count} > threshold {threshold_turns}"
            ),
            token_estimate=token_estimate,
            turn_count=turn_count,
        )
    return CompressionDecision(
        compress=False,
        reason=(
            f"below thresholds (tokens={token_estimate}/{token_cap}, "
            f"turns={turn_count}/{threshold_turns})"
        ),
        token_estimate=token_estimate,
        turn_count=turn_count,
    )


# ──────────────────────────────────────────────────────────────────
# Iterative-summary detection
# ──────────────────────────────────────────────────────────────────


def is_summary_message(text: str) -> bool:
    """True iff ``text`` starts with :data:`SUMMARY_PREFIX`.

    Used by per-brain implementations to detect an already-summarised
    session and route to the iterative-update prompt instead of the
    first-compaction prompt.
    """
    return bool(text) and text.startswith(SUMMARY_PREFIX)


def extract_summary_body(text: str) -> str:
    """Strip the :data:`SUMMARY_PREFIX` and any surrounding whitespace
    from a synthetic summary message body. Returns the structured
    summary block alone so the iterative-update prompt can embed it
    verbatim.

    Returns the original text unchanged if no prefix is detected —
    defensive against the iterative path being called on a
    non-summary message (shouldn't happen but the cost is zero)."""
    if not is_summary_message(text):
        return text
    body = text[len(SUMMARY_PREFIX):]
    return body.strip()


# ──────────────────────────────────────────────────────────────────
# Prompt template — Hermes-style structured sections
# ──────────────────────────────────────────────────────────────────

_TEMPLATE_SECTIONS = """\
## Active Task
[The single most important field. Copy the user's most recent
unfulfilled request verbatim. If multiple were requested and only
some are done, list only the ones NOT yet completed. Continuation
should pick up exactly here. If none, write "None."]

## Goal
[What the user is trying to accomplish overall — quote the original
/goal text or task body verbatim when available.]

## Constraints & Preferences
[User-stated constraints, coding style, important decisions.]

## Completed Actions
[Numbered list of concrete actions taken. Format each as:
N. ACTION target — outcome [tool: name]
Be specific: file paths, commands, line numbers, results.]

## Active State
[Current working state — branch, modified files, test status,
running processes, environment details that matter.]

## In Progress
[Work currently underway when compression fired.]

## Blocked
[Blockers, errors, or unresolved issues. Include exact error
messages.]

## Resolved Questions
[Q/A pairs the user already answered — include the answer so it
is NOT repeated.]

## Pending User Asks
[Questions or requests from the user that have NOT been answered
yet. If none, write "None."]

## Critical Context
[Specific values, error messages, configuration details that would
be lost without explicit preservation. NEVER include API keys,
tokens, passwords, or credentials — write [REDACTED] instead.]

Write the summary body only. Be concrete: include file paths,
command outputs, error messages, line numbers, and specific values.
Avoid vague descriptions like "made some changes".
"""


_PREAMBLE = (
    "You are a summarization agent creating a context checkpoint for "
    "the conversation below. Produce ONLY the structured summary — "
    "no greeting, no preamble, no commentary. Write in the same "
    "language the user was using. NEVER include API keys, tokens, "
    "passwords, secrets, or credentials in the summary — replace any "
    "that appear with [REDACTED]."
)


def build_first_compaction_prompt(transcript_text: str) -> str:
    """Compose the prompt for the FIRST compression of a session.

    ``transcript_text`` is the serialised conversation (one
    ``user:``/``assistant:`` block per turn, in chronological order)
    that the per-brain caller built from :meth:`Brain.iter_messages`.
    """
    return (
        f"{_PREAMBLE}\n\n"
        "Create a structured checkpoint summary for the conversation "
        "after the earlier turns are compacted. The summary must "
        "preserve enough detail for the assistant to continue without "
        "re-reading the original turns.\n\n"
        "TURNS TO SUMMARIZE:\n"
        f"{transcript_text}\n\n"
        "Use this exact structure:\n\n"
        f"{_TEMPLATE_SECTIONS}"
    )


def build_iterative_compaction_prompt(
    previous_summary: str,
    new_transcript_text: str,
) -> str:
    """Compose the prompt for an iterative compression — a session
    that was ALREADY compressed once and has now crossed the
    threshold again.

    ``previous_summary`` is the body of the prior summary block
    (already stripped of :data:`SUMMARY_PREFIX` by
    :func:`extract_summary_body`). ``new_transcript_text`` is the
    serialised turns that have arrived since the last compression.
    """
    return (
        f"{_PREAMBLE}\n\n"
        "You are updating an existing context-compaction summary. A "
        "previous compaction produced the summary below; new "
        "conversation turns have occurred since then and need to be "
        "incorporated.\n\n"
        "PREVIOUS SUMMARY:\n"
        f"{previous_summary}\n\n"
        "NEW TURNS TO INCORPORATE:\n"
        f"{new_transcript_text}\n\n"
        "Update the summary using the exact structure below. PRESERVE "
        "all existing information that is still relevant. ADD new "
        "completed actions to the list (continue numbering). Move "
        "items from 'In Progress' to 'Completed Actions' when done. "
        "Move answered questions to 'Resolved Questions'. Update "
        "'Active State' to reflect current state. Remove information "
        "only if clearly obsolete. CRITICAL: Update 'Active Task' to "
        "reflect the user's most recent unfulfilled request — this "
        "is the most important field for task continuity.\n\n"
        f"{_TEMPLATE_SECTIONS}"
    )


def serialize_messages_for_summary(
    messages: Iterable[tuple[str, str]],
) -> str:
    """Render ``(role, text)`` pairs as a flat block the summariser
    can read. One ``[role]\\ntext`` stanza per message, blank line
    between. Skips empty-text messages so a stream of tool-only
    assistant turns doesn't pad the prompt with whitespace.

    Truncates per-message text at 4000 chars to keep the summariser
    prompt bounded; the tail of an extremely long user paste is less
    informative than the head, and the structured summary is the
    point of compression in the first place.
    """
    lines: list[str] = []
    for role, text in messages:
        clean = (text or "").strip()
        if not clean:
            continue
        if len(clean) > 4000:
            clean = clean[:4000] + "… [truncated for summariser]"
        lines.append(f"[{role}]\n{clean}")
    return "\n\n".join(lines)


def wrap_with_summary_prefix(summary_body: str) -> str:
    """Compose the synthetic user turn the brain inserts at position
    1 of the rewritten transcript. Pairs the SUMMARY_PREFIX warning
    text with the structured summary body produced by the
    summariser."""
    body = (summary_body or "").strip()
    return f"{SUMMARY_PREFIX}\n\n{body}" if body else SUMMARY_PREFIX


# ──────────────────────────────────────────────────────────────────
# Replacement-plan helper
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReplacementPlan:
    """Describes how to split an existing transcript into the parts
    that survive compression.

    ``messages_to_summarise``: the older messages (role, text) that
    are folded into the summary.
    ``protected_tail``: the recent messages kept verbatim. Same
    ``(role, text)`` shape so the caller can match against its
    on-disk store.
    ``previous_summary``: extracted body of an earlier summary
    when this is an iterative compression; ``None`` for the first
    compaction.
    """

    messages_to_summarise: list[tuple[str, str]]
    protected_tail: list[tuple[str, str]]
    previous_summary: str | None = None
    # Indices into the ORIGINAL ``messages`` sequence for each kept
    # segment. Used by per-brain implementations to copy verbatim
    # lines from the existing JSONL/SQL store instead of having to
    # serialise from the abstract (role, text) shape — this is what
    # keeps the "last K turns are preserved byte-for-byte"
    # invariant honest.
    protected_tail_indices: list[int] = field(default_factory=list)


def plan_replacement(
    messages: Sequence[tuple[str, str]],
    *,
    protect_last_n_turns: int = DEFAULT_PROTECT_LAST_N_TURNS,
) -> ReplacementPlan:
    """Split a transcript into (to-summarise, protected-tail) plus
    iterative-summary detection.

    Iterative path: when the FIRST message in ``messages`` is itself
    a SUMMARY_PREFIX-wrapped summary turn, we strip it out of the
    summary inputs and pass the body to the caller as
    ``previous_summary``. The remaining messages (everything between
    the prior summary and the protected tail) become
    ``messages_to_summarise``.

    First-compaction path: ``previous_summary`` is None;
    ``messages_to_summarise`` is everything before the tail.

    ``protect_last_n_turns`` is clamped to the available message
    count. A transcript with 5 messages and K=10 will yield empty
    ``messages_to_summarise`` and an unchanged tail — the caller
    should re-check the trigger (no work to do).
    """
    msgs = list(messages)
    previous_summary: str | None = None
    start_idx = 0

    if msgs and msgs[0][0] == "user" and is_summary_message(msgs[0][1]):
        previous_summary = extract_summary_body(msgs[0][1])
        start_idx = 1

    middle = msgs[start_idx:]
    if protect_last_n_turns < 0:
        protect_last_n_turns = 0
    if protect_last_n_turns >= len(middle):
        # Nothing to summarise — all messages are in the protected
        # tail. Caller decides whether to bail (typical) or proceed
        # with an empty summary (degenerate).
        protected = middle
        to_summarise: list[tuple[str, str]] = []
        protected_indices = list(range(start_idx, start_idx + len(middle)))
    else:
        split = len(middle) - protect_last_n_turns
        to_summarise = list(middle[:split])
        protected = list(middle[split:])
        protected_indices = list(
            range(start_idx + split, start_idx + len(middle))
        )

    return ReplacementPlan(
        messages_to_summarise=list(to_summarise),
        protected_tail=list(protected),
        previous_summary=previous_summary,
        protected_tail_indices=protected_indices,
    )


# ──────────────────────────────────────────────────────────────────
# Public API summary
# ──────────────────────────────────────────────────────────────────

__all__ = [
    "DEFAULT_CONTEXT_WINDOW_TOKENS",
    "DEFAULT_PROTECT_LAST_N_TURNS",
    "DEFAULT_TOKEN_THRESHOLD_RATIO",
    "DEFAULT_TURN_THRESHOLD",
    "CompressionDecision",
    "CompressionInputs",
    "ReplacementPlan",
    "SUMMARY_PREFIX",
    "build_first_compaction_prompt",
    "build_iterative_compaction_prompt",
    "estimate_tokens",
    "estimate_transcript_tokens",
    "extract_summary_body",
    "is_summary_message",
    "plan_replacement",
    "serialize_messages_for_summary",
    "should_compress",
    "wrap_with_summary_prefix",
]
