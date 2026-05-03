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
def _detect(text: str, *, role: str = "user", uuid: str = "s-1", turn: int = 1) -> TriggerVerdict:
    # The detector's role parameter is typed Literal["user"]. Tests
    # that intentionally pass other roles use a type-ignore to
    # signal the deliberate violation; the runtime gate is what we
    # actually exercise.
    return asyncio.run(
        detect(
            text,
            role=role,  # type: ignore[arg-type]
            session_uuid=uuid,
            turn_index=turn,
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
        controller = lc.LearningController(workspace=workspace)
        seen_texts: list[str] = []

        async def _spy(text, *, role, session_uuid, turn_index):
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


def test_fail_open_classifier_raises():
    async def _boom(text, *, session_uuid, turn_index):
        raise RuntimeError("classifier transport error")

    # Use a text that does NOT match any canonical regex so the
    # classifier path is exercised.
    text = "tell me a joke about pythons"
    with patch.object(triggers, "_classifier_call", _boom):
        v = _detect(text)
    assert v.verdict == "NONE"
    assert classifier_errors.get("error") == 1


def test_fail_open_classifier_times_out():
    async def _slow(text, *, session_uuid, turn_index):
        await asyncio.sleep(10)
        return TriggerVerdict(verdict="ADD", confidence=1.0)

    text = "tell me a story please"
    with (
        patch.object(triggers, "_classifier_call", _slow),
        patch.object(triggers, "CLASSIFIER_TIMEOUT_SECONDS", 0.05),
    ):
        v = _detect(text)
    assert v.verdict == "NONE"
    assert classifier_errors.get("timeout") == 1


def test_classifier_below_threshold_is_dropped():
    async def _low(text, *, session_uuid, turn_index):
        return TriggerVerdict(
            verdict="ADD",
            confidence=CLASSIFIER_CONFIDENCE_THRESHOLD - 0.01,
            matched_pattern_id="classifier",
        )

    text = "tell me a joke about pythons"
    with patch.object(triggers, "_classifier_call", _low):
        v = _detect(text)
    assert v.verdict == "NONE"


def test_classifier_at_threshold_passes_through():
    async def _high(text, *, session_uuid, turn_index):
        return TriggerVerdict(
            verdict="ADD",
            confidence=CLASSIFIER_CONFIDENCE_THRESHOLD,
            person_name="Sarah",
            facts=("likes mystery novels",),
            matched_pattern_id="classifier",
        )

    text = "tell me a joke about pythons"
    with patch.object(triggers, "_classifier_call", _high):
        v = _detect(text)
    assert v.verdict == "ADD"
    assert v.matched_pattern_id == "classifier"


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
