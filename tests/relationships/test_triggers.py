"""Day 1 tests for the relationships trigger detector.

Covers the canonical regex matrix (one positive + one near-miss
negative per regex), the hard role-gate (research doc C-R2 — both
the literal-type drift assertion and the missed-call-site drift
assertion), the quoted-content cases (C-Q1/C-Q2/C-Q3), the
fail-open wrapper, and empty input.

No file writes anywhere — Day 1 detector is pure observation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from core.relationships import triggers
from core.relationships.triggers import (
    CLASSIFIER_CONFIDENCE_THRESHOLD,
    TriggerVerdict,
    classifier_errors,
    detect,
)


# Helper: drive an async detector call from a sync test, mirroring
# the existing convention in tests/test_learning_curator.py.
#
# ``skip_classifier`` defaults to True because the bulk of these
# tests exercise the regex matrix and the role-gate / quoted-content
# preamble — they don't want to spawn a real claude -p classifier
# call (or even mock one). Dedicated classifier-pipeline tests pass
# their own ``classifier_call=`` stub.
def _detect(
    text: str,
    *,
    role: str = "user",
    uuid: str = "s-1",
    turn: int = 1,
    skip_classifier: bool = True,
    classifier_call=None,
) -> TriggerVerdict:
    return asyncio.run(
        detect(
            text,
            role=role,  # type: ignore[arg-type]
            session_uuid=uuid,
            turn_index=turn,
            skip_classifier=skip_classifier,
            classifier_call=classifier_call,
        )
    )


@pytest.fixture(autouse=True)
def _reset_error_counter():
    classifier_errors.reset()
    yield
    classifier_errors.reset()


# --------------------------------------------------------------------
# Canonical regex matrix — one positive + one near-miss negative each.
# Negatives are deliberately close to the pattern but miss a structural
# requirement, so they exercise the regex's anchors / boundaries
# rather than just being random unrelated text.
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expect_id,expect_verdict",
    [
        ("remember that my girlfriend Sarah likes mystery novels", "ADD-1", "ADD"),
        ("Please remember Sarah's birthday", "ADD-1", "ADD"),
        ("save this: Sarah's birthday is May 12", "ADD-2", "ADD"),
        ("Save that, Sarah is allergic to peanuts", "ADD-2", "ADD"),
        ("fyi: Marco is allergic to peanuts", "ADD-3", "ADD"),
        ("for future reference: Sarah prefers async", "ADD-3", "ADD"),
        ("note: my brother lives in Lisbon", "ADD-4", "ADD"),
        ("add to relationships: Marco moved to Berlin", "ADD-4", "ADD"),
        ("forget Sarah", "DEL-1", "DELETE"),
        ("Please forget my brother", "DEL-1", "DELETE"),
        ("delete the thing about my brother", "DEL-2", "DELETE"),
        ("remove everything about Marco", "DEL-2", "DELETE"),
        ("update what you know about Marco", "SUP-1", "SUPERSEDE"),
        ("correct that about Sarah", "SUP-1", "SUPERSEDE"),
    ],
)
def test_regex_positive(text: str, expect_id: str, expect_verdict: str):
    v = _detect(text)
    assert v.verdict == expect_verdict
    assert v.matched_pattern_id == expect_id
    assert v.confidence == 1.0


@pytest.mark.parametrize(
    "text,reason",
    [
        # ADD-1 needs "remember" then a word; "remember" alone is not
        # followed by anything to remember.
        ("remember", "ADD-1: trailing word required"),
        # ADD-2 needs "save this/that"; "save my work" doesn't match.
        ("save my work", "ADD-2: 'this'/'that' required after save"),
        # ADD-3 needs the colon/comma after "fyi"; bare "fyi marco" doesn't.
        ("fyi marco is here", "ADD-3: punctuation required after fyi"),
        # ADD-4 needs the leading verb at the start; mid-sentence
        # "add" doesn't anchor.
        ("can you add Marco to the list", "ADD-4: anchored at start"),
        # DEL-1 needs "forget" then a word.
        ("forget", "DEL-1: trailing word required"),
        # DEL-2 needs "delete/remove/drop ... about <word>".
        ("delete the file", "DEL-2: 'about <word>' required"),
        # SUP-1 needs "update/correct/fix" then "that"/"what you know"
        # then "about <word>".
        ("update Sarah", "SUP-1: 'that'/'what you know about' required"),
        # Plain conversation that names a third party — must NOT
        # trigger (the v2 reject path still catches it downstream).
        ("Sarah and I went to dinner last night", "no trigger keyword"),
    ],
)
def test_regex_near_miss(text: str, reason: str):
    v = _detect(text)
    assert v.verdict == "NONE", f"{reason}: unexpectedly fired ({v.matched_pattern_id})"
    assert v.matched_pattern_id is None


# --------------------------------------------------------------------
# Role gate — research doc C-R2.
# Two assertions in one test (per research doc): (a) the literal-
# type-drift case where someone passes role="assistant" directly
# returns NONE short-circuit, and (b) the missed-call-site drift
# case where the dispatch path must not invoke detect over
# assistant-role text.
# --------------------------------------------------------------------


def test_role_gate_short_circuits_assistant_role():
    """C-R2 part (a): literal-type drift.

    A future caller that ignores the Literal["user"] type hint and
    passes role="assistant" must still get NONE — the runtime gate
    is what enforces the contract, not the type hint alone.
    """
    text = "I'll remember that Sarah likes mystery novels."
    v = _detect(text, role="assistant")
    assert v.verdict == "NONE"
    assert v.matched_pattern_id is None
    assert v.confidence == 0.0


def test_role_gate_dispatch_path_does_not_invoke_detect_over_replies():
    """C-R2 part (b): missed-call-site drift.

    Day 1 spec: detect() is invoked ONLY from the
    /learning relationships-dryrun CLI handler, and that handler
    must filter to ``msg.role == "user"`` BEFORE calling detect.
    Mock the detector and walk a synthetic JSONL containing one
    assistant-role line that would otherwise trigger ADD-1.
    Assert the mock was never called over that line.
    """
    import core.learning_curator as lc
    from core.transcripts import claude_session_jsonl_dir

    workspace = Path("/tmp/vexis-test-roles")
    pdir = claude_session_jsonl_dir(workspace)
    pdir.mkdir(parents=True, exist_ok=True)
    jsonl = pdir / "role-gate-test.jsonl"
    lines = [
        json.dumps({
            "type": "user",
            "uuid": "u-1",
            "timestamp": "2026-05-03T12:00:00Z",
            "message": {"role": "user", "content": "hello world"},
        }),
        json.dumps({
            "type": "assistant",
            "uuid": "a-1",
            "timestamp": "2026-05-03T12:00:01Z",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": "I'll remember that Sarah likes mystery novels.",
                }],
            },
        }),
    ]
    jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        # Phase C Day 5: dryrun routes session enumeration +
        # transcript reads through the brain abstraction. Default
        # is BrainNull (returns empty); we need a real
        # ClaudeCodeBrain so it picks up the seeded JSONL via
        # ``core.transcripts.iter_session_metas``.
        from core.brain.claude_code import ClaudeCodeBrain
        from core.running_tasks import RunningTasks
        from core.sessions import SessionStore
        workspace.mkdir(parents=True, exist_ok=True)
        sess = SessionStore(workspace / "sessions.json")
        cc_brain = ClaudeCodeBrain(
            workspace=workspace, session=sess, running_tasks=RunningTasks(),
        )
        controller = lc.LearningController(workspace=workspace, brain=cc_brain)
        seen_texts: list[str] = []

        async def _spy(text, **kwargs):
            seen_texts.append(text)
            return TriggerVerdict(verdict="NONE")

        with patch.object(lc, "relationships_detect", _spy):
            out = asyncio.run(controller._relationships_dryrun_text(last_n=5))

        assert "hello world" in seen_texts
        for t in seen_texts:
            assert "I'll remember that Sarah" not in t, (
                f"detector was called over assistant-role text: {t!r}"
            )
        assert "scanned 1 session" in out
    finally:
        jsonl.unlink(missing_ok=True)
        try:
            pdir.rmdir()
        except OSError:
            pass


# --------------------------------------------------------------------
# Quoted / roleplay protection — research doc C-Q1, C-Q2, C-Q3.
# Day 1's strip_quoted_blocks is wired in; these assert it actually
# protects the matched cases from the trigger surface.
# --------------------------------------------------------------------


def test_quoted_c_q1_translation_request_with_quoted_trigger():
    """C-Q1: 'translate this to French: "remember that …"' must NOT trigger.

    The quoted-content stripper removes the inline backtick/quote
    block before the regex pass. (The stripper only handles
    backticks + blockquotes + fences — straight quotes don't
    trigger stripping. So we use a backtick span here as the
    realistic 'quoted' marker.)
    """
    text = (
        "translate this to French: "
        "`remember that my girlfriend Sarah likes mystery novels`"
    )
    v = _detect(text)
    assert v.verdict == "NONE"


def test_quoted_c_q2_blockquote_in_script():
    """C-Q2: a markdown blockquote containing the trigger phrase
    inside a script-writing context must NOT trigger."""
    text = (
        "in the script I'm writing, character A says:\n"
        "> remember that Marco hates mornings"
    )
    v = _detect(text)
    assert v.verdict == "NONE"


def test_quoted_c_q3_inline_backtick_about_delete_verbs():
    """C-Q3: inline backtick spans are stripped — a meta-discussion
    about DELETE statements must NOT trigger."""
    text = (
        "what's the difference between `forget my sister` and "
        "`delete my sister` as DELETE statements?"
    )
    v = _detect(text)
    assert v.verdict == "NONE"


# --------------------------------------------------------------------
# Fail-open wrapper — research doc §3.1.
# Classifier raises → detect() returns NONE, error counted, no
# exception propagates. The synchronous Telegram → brain path
# must NEVER block on detector flakiness.
# --------------------------------------------------------------------


# --- Day 2: full pipeline (regex gates, classifier always parses) ---
# These exercise the classifier injection. Use ADD-1-matching text
# so the regex gates the classifier in. The classifier_call=
# injection short-circuits the real claude -p subprocess.


def test_fail_open_classifier_raises():
    async def _boom(text, **kwargs):
        raise RuntimeError("classifier transport error")

    text = "remember that my girlfriend Sarah likes mystery novels"
    v = _detect(text, skip_classifier=False, classifier_call=_boom)
    assert v.verdict == "NONE"
    assert classifier_errors.get("error") == 1


def test_fail_open_classifier_times_out():
    async def _slow(text, **kwargs):
        await asyncio.sleep(10)
        return TriggerVerdict(verdict="ADD", confidence=1.0)

    text = "remember that my girlfriend Sarah likes mystery novels"
    with patch.object(triggers, "CLASSIFIER_TIMEOUT_SECONDS", 0.05):
        v = _detect(text, skip_classifier=False, classifier_call=_slow)
    assert v.verdict == "NONE"
    assert classifier_errors.get("timeout") == 1


def test_classifier_below_threshold_is_dropped():
    async def _low(text, **kwargs):
        return TriggerVerdict(
            verdict="ADD",
            confidence=CLASSIFIER_CONFIDENCE_THRESHOLD - 0.01,
            person_name="Sarah",
            facts=("likes mystery novels",),
            matched_pattern_id="classifier",
        )

    text = "remember that my girlfriend Sarah likes mystery novels"
    v = _detect(text, skip_classifier=False, classifier_call=_low)
    assert v.verdict == "NONE"


def test_classifier_at_threshold_passes_through():
    async def _high(text, **kwargs):
        return TriggerVerdict(
            verdict="ADD",
            confidence=CLASSIFIER_CONFIDENCE_THRESHOLD,
            person_name="Sarah",
            qualifier="girlfriend",
            facts=("likes mystery novels",),
            matched_pattern_id="classifier",
        )

    text = "remember that my girlfriend Sarah likes mystery novels"
    v = _detect(text, skip_classifier=False, classifier_call=_high)
    assert v.verdict == "ADD"
    assert v.person_name == "Sarah"
    assert v.qualifier == "girlfriend"
    assert v.facts == ("likes mystery novels",)
    # Regex hit takes precedence in matched_pattern_id when both fire.
    assert v.matched_pattern_id == "ADD-1"


def test_full_pipeline_regex_miss_no_third_party_does_not_invoke_classifier():
    """Path (a) gate: regex miss + no third party named → classifier
    never runs (cost-bound). Asserts the gate by counting calls."""
    call_count = 0

    async def _spy(text, **kwargs):
        nonlocal call_count
        call_count += 1
        return TriggerVerdict(verdict="ADD", confidence=1.0,
                              person_name="X", facts=("y",))

    v = _detect("just a normal message about nothing in particular",
                skip_classifier=False, classifier_call=_spy)
    assert v.verdict == "NONE"
    assert call_count == 0


def test_full_pipeline_regex_miss_third_party_present_invokes_classifier():
    """Path (a) gate: regex miss BUT third party named → classifier
    runs (the v3.4 fallback case)."""
    call_count = 0

    async def _spy(text, **kwargs):
        nonlocal call_count
        call_count += 1
        return TriggerVerdict(
            verdict="ADD",
            confidence=0.9,
            person_name="Sarah",
            qualifier="coworker",
            facts=("prefers async",),
            matched_pattern_id="classifier",
        )

    # "Sarah on the team prefers async" matches THIRD_PARTY_SUBJECT_RE.
    v = _detect("Sarah on the team prefers async standups",
                skip_classifier=False, classifier_call=_spy)
    assert call_count == 1
    assert v.verdict == "ADD"
    assert v.matched_pattern_id == "classifier"


def test_full_pipeline_classifier_returns_no_facts_for_add_drops():
    """Schema rule: ADD requires ≥1 fact. Classifier returning ADD
    with empty facts list collapses to NONE."""
    async def _empty(text, **kwargs):
        return TriggerVerdict(
            verdict="ADD", confidence=0.95,
            person_name="Sarah", facts=(),
        )

    text = "remember that Sarah is my friend"
    v = _detect(text, skip_classifier=False, classifier_call=_empty)
    assert v.verdict == "NONE"


# --------------------------------------------------------------------
# Empty / whitespace input.
# --------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", " ", "\n\n", "\t  \n"])
def test_empty_or_whitespace_input(text: str):
    v = _detect(text)
    assert v.verdict == "NONE"
    assert v.matched_pattern_id is None


def test_only_quoted_content_strips_to_empty():
    text = "```\nremember that Sarah likes mystery novels\n```"
    v = _detect(text)
    assert v.verdict == "NONE"
