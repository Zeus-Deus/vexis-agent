"""Day 2 tests for core/learning_review.py.

Coverage:
  - _format_transcript: text + tool_use rendered, tool_result-only
    messages dropped, tool_input long-string truncation.
  - _extract_lessons: bare JSON array, JSON in code fence, single
    object → list[1], "Nothing to save." (case-insensitive), trailing
    prose around JSON, malformed → None.
  - _verify_evidence: matches user message, ignores assistant message,
    rejects empty / not-found.
  - _validate_lesson: full-pass, missing fields, oversize, evidence
    fail, non-dict candidate.
  - run_review: success path with mocked spawn, error paths
    (timeout, non-zero exit, parse failure), max-N cap, large-transcript
    warning logged.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from core import learning_review as lr
from core.learning_review import (
    LARGE_TRANSCRIPT_WARN_CHARS,
    RECURSION_ENV_VAR,
    ReviewOutput,
    _build_review_prompt,
    _extract_lessons,
    _format_transcript,
    _validate_lesson,
    _verify_evidence,
    run_review,
)
from core.transcripts import SessionMeta, TranscriptMessage


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def _msg(
    role: str,
    text: str,
    *,
    tool_calls: tuple = (),
    ts: str = "2026-05-02T10:00:00Z",
    uuid: str = "m1",
) -> TranscriptMessage:
    return TranscriptMessage(
        role=role,
        text=text,
        timestamp=datetime.fromisoformat(ts.replace("Z", "+00:00")),
        uuid=uuid,
        tool_calls=tool_calls,
        raw={},
    )


def _meta(uuid: str = "session-1", path: Path | None = None) -> SessionMeta:
    return SessionMeta(
        session_uuid=uuid,
        jsonl_path=path or Path(f"/tmp/{uuid}.jsonl"),
        last_message_timestamp=datetime(2026, 5, 2, 10, 0, 0, tzinfo=timezone.utc),
        message_count_estimate=2,
    )


def _spawn_returning(stdout: str, *, returncode: int = 0, stderr: str = ""):
    """Phase B: build a BrainNull pre-loaded with one AuxResult.
    Returns (brain, brain) — the second value preserves the
    ``spawn, captured = _spawn_returning(...)`` 2-tuple shape so
    existing call sites unpacking ``spawn, captured`` keep working;
    the second element is the same brain (use
    ``captured.aux_call_records()`` to inspect what was sent).
    """
    from core.brain.base import AuxResult
    from core.brain.null import BrainNull

    brain = BrainNull(
        aux_results=[
            AuxResult(stdout=stdout, stderr=stderr, returncode=returncode)
        ]
    )
    return brain, brain


@pytest.fixture(autouse=True)
def _isolated_yaml_config(monkeypatch, tmp_path):
    """Phase B: tier resolution reads ``~/.vexis/config.yaml``. Tests
    must not see the user's real config — otherwise legacy
    ``models.learning_triage: haiku`` raw-string keys override the
    default tier and confuse assertions on ``model_tier``. Point the
    config path at ``tmp_path / "config.yaml"`` so tests that
    explicitly write a config to the same location (like
    ``test_triage_yaml_config_overrides``) interoperate cleanly."""
    from core import yaml_config

    monkeypatch.setattr(
        yaml_config, "_config_path", lambda: tmp_path / "config.yaml"
    )


@pytest.fixture(autouse=True)
def _triage_disabled_by_default(monkeypatch):
    """Most pre-existing tests in this module were written before the
    two-tier triage gate landed and assume a single subprocess spawn
    (the full review). Default-disable triage here so those assertions
    still hold; the tests that exercise triage explicitly re-enable it
    via monkeypatch and provide a multi-call spawn."""
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: False)


# --------------------------------------------------------------------
# _format_transcript
# --------------------------------------------------------------------


def test_format_transcript_basic():
    msgs = [
        _msg("user", "list movies tonight", ts="2026-05-02T10:00:00Z"),
        _msg("assistant", "here are tonight's movies",
             ts="2026-05-02T10:00:05Z"),
    ]
    out = _format_transcript(msgs)
    assert "### USER (2026-05-02T10:00:00Z)" in out
    assert "list movies tonight" in out
    assert "### ASSISTANT (2026-05-02T10:00:05Z)" in out
    assert "here are tonight's movies" in out


def test_format_transcript_skips_tool_result_only_messages():
    """User messages that consist purely of a tool_result block come
    back from iter_messages with empty text + empty tool_calls; those
    must be skipped so the transcript reads cleanly."""
    msgs = [
        _msg("user", "first user input"),
        _msg("user", "", tool_calls=()),  # synthetic empty (tool_result-only)
        _msg("assistant", "response"),
    ]
    out = _format_transcript(msgs)
    assert "first user input" in out
    assert "response" in out
    # No header for the empty message:
    assert out.count("### USER") == 1


def test_format_transcript_renders_tool_calls_inline():
    msgs = [
        _msg("assistant", "checking", tool_calls=(
            {"id": "tu1", "name": "Bash", "input": {"cmd": "ls"}},
        )),
    ]
    out = _format_transcript(msgs)
    assert "checking" in out
    assert "[tool: Bash(" in out
    assert "ls" in out


def test_format_transcript_truncates_huge_tool_inputs():
    """A 10K-char tool input should not bloat the transcript."""
    msgs = [
        _msg("assistant", "running", tool_calls=(
            {"id": "tu1", "name": "Bash", "input": {"cmd": "x" * 10_000}},
        )),
    ]
    out = _format_transcript(msgs)
    assert len(out) < 1000  # truncation kicks in


# --------------------------------------------------------------------
# _extract_lessons
# --------------------------------------------------------------------


def test_extract_nothing_to_save_literal():
    assert _extract_lessons("Nothing to save.") == "nothing-to-save"
    assert _extract_lessons("nothing to save") == "nothing-to-save"
    assert _extract_lessons("  Nothing to save.  ") == "nothing-to-save"


def test_extract_bare_json_array():
    text = '[{"lesson": "L", "evidence": "E", "scope": "S"}]'
    result = _extract_lessons(text)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["lesson"] == "L"


def test_extract_json_in_code_fence():
    text = '```json\n[{"lesson": "L", "evidence": "E", "scope": "S"}]\n```'
    result = _extract_lessons(text)
    assert isinstance(result, list)
    assert result[0]["lesson"] == "L"


def test_extract_unfenced_code_block():
    """Sometimes the model emits ``` without 'json'."""
    text = '```\n[{"lesson": "L", "evidence": "E", "scope": "S"}]\n```'
    result = _extract_lessons(text)
    assert isinstance(result, list)


def test_extract_single_object_wraps_to_list():
    text = '{"lesson": "L", "evidence": "E", "scope": "S"}'
    result = _extract_lessons(text)
    assert isinstance(result, list)
    assert len(result) == 1


def test_extract_with_leading_prose():
    text = (
        'Here is what I found:\n'
        '[{"lesson": "L", "evidence": "E", "scope": "S"}]'
    )
    result = _extract_lessons(text)
    assert isinstance(result, list)
    assert len(result) == 1


def test_extract_two_lessons():
    text = (
        '[{"lesson": "L1", "evidence": "E1", "scope": "S1"},'
        ' {"lesson": "L2", "evidence": "E2", "scope": "S2"}]'
    )
    result = _extract_lessons(text)
    assert isinstance(result, list)
    assert len(result) == 2


def test_extract_malformed_returns_none():
    assert _extract_lessons("garbage") is None
    assert _extract_lessons("") is None
    assert _extract_lessons(None) is None  # type: ignore[arg-type]


def test_extract_filters_non_dict_array_elements():
    text = '[{"lesson": "L", "evidence": "E", "scope": "S"}, "extra", 42]'
    result = _extract_lessons(text)
    assert isinstance(result, list)
    assert len(result) == 1


# --------------------------------------------------------------------
# _verify_evidence
# --------------------------------------------------------------------


def test_verify_evidence_match_in_user_message():
    msgs = [
        _msg("user", "you listed past events, filter to upcoming"),
        _msg("assistant", "got it"),
    ]
    assert _verify_evidence("filter to upcoming", msgs) is True


def test_verify_evidence_substring_within_user_message():
    msgs = [_msg("user", "really long message... target phrase ...more")]
    assert _verify_evidence("target phrase", msgs) is True


def test_verify_evidence_rejects_assistant_only():
    """LLM says 'evidence' is from assistant — should reject."""
    msgs = [
        _msg("user", "go"),
        _msg("assistant", "here is a phrase claude wrote"),
    ]
    assert _verify_evidence("phrase claude wrote", msgs) is False


def test_verify_evidence_rejects_empty():
    msgs = [_msg("user", "anything")]
    assert _verify_evidence("", msgs) is False


def test_verify_evidence_rejects_not_found():
    msgs = [_msg("user", "actual text")]
    assert _verify_evidence("paraphrased text", msgs) is False


# --------------------------------------------------------------------
# _validate_lesson
# --------------------------------------------------------------------


def _ok_msgs() -> list[TranscriptMessage]:
    return [_msg("user", "filter to upcoming items only please")]


def _ok_situational() -> dict:
    """A v2-shape SITUATIONAL candidate that should pass cleanly.

    Used as the baseline for validator tests that aren't exercising
    procedural-tier shape (those build their own PROCEDURAL/S{1,2,3}
    candidates). SITUATIONAL is the simplest valid v2 shape — no tier,
    no target — so it isolates field-level checks.
    """
    return {
        "class": "SITUATIONAL",
        "lesson": "L" * 50,
        "evidence": "filter to upcoming items only please",
        "scope": "time-bound listings",
    }


def test_validate_lesson_full_pass():
    ok, reason = _validate_lesson(_ok_situational(), _ok_msgs(), max_chars=280)
    assert ok is True
    assert reason == ""


def test_validate_lesson_missing_lesson():
    cand = {"class": "SITUATIONAL", "evidence": "x", "scope": "y"}
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "lesson" in reason


def test_validate_lesson_oversize_lesson():
    cand = _ok_situational()
    cand["lesson"] = "x" * 281
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "exceeds" in reason


def test_validate_lesson_evidence_not_in_session():
    cand = _ok_situational()
    cand["evidence"] = "phrase that never appeared in any user message"
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "verbatim" in reason


def test_validate_lesson_non_dict():
    ok, reason = _validate_lesson("not a dict", _ok_msgs(), max_chars=280)  # type: ignore[arg-type]
    assert ok is False


# --------------------------------------------------------------------
# Day 1 v2: classification + tier + target shape validation
# --------------------------------------------------------------------


def test_validate_lesson_missing_class_rejected():
    """v2 makes class REQUIRED. v1-shape candidates without it must
    fail cleanly so we don't silently mix v1/v2 outputs."""
    cand = _ok_situational()
    del cand["class"]
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "class" in reason


def test_validate_lesson_invalid_class_rejected():
    cand = _ok_situational()
    cand["class"] = "MIXED"  # not one of the four enum values
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "class" in reason


def test_validate_lesson_volatile_dropped():
    """VOLATILE candidates are dropped — the prompt forbids them but
    if the LLM emits one anyway we reject defensively."""
    cand = _ok_situational()
    cand["class"] = "VOLATILE"
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "VOLATILE" in reason


def test_validate_lesson_identity_no_tier_no_target():
    """IDENTITY candidates do not carry tier/target; if either appears,
    it indicates the LLM mis-classified or mis-shaped its output."""
    cand = _ok_situational()
    cand["class"] = "IDENTITY"
    cand["tier"] = "S1"  # bogus — IDENTITY shouldn't carry a tier
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "tier" in reason or "target" in reason


# --------------------------------------------------------------------
# Day 3: IDENTITY target.user_claim_alias shape validation
# --------------------------------------------------------------------


def test_validate_identity_no_target_passes():
    """IDENTITY without target = fresh claim insertion."""
    cand = _ok_situational()
    cand["class"] = "IDENTITY"
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is True, reason


def test_validate_identity_with_alias_target_passes():
    """IDENTITY with target = {user_claim_alias: "..."} is the
    Day 3 alias path."""
    cand = _ok_situational()
    cand["class"] = "IDENTITY"
    cand["target"] = {"user_claim_alias": "User prefers concise answers."}
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is True, reason


def test_validate_identity_target_with_extra_keys_rejected():
    """target may only carry user_claim_alias for IDENTITY — extras
    indicate the LLM mistakenly produced a procedural shape."""
    cand = _ok_situational()
    cand["class"] = "IDENTITY"
    cand["target"] = {
        "user_claim_alias": "User prefers concise answers.",
        "skill_name": "stray",  # not allowed for IDENTITY
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "extra keys" in reason.lower() or "skill_name" in reason


def test_validate_identity_target_empty_alias_rejected():
    cand = _ok_situational()
    cand["class"] = "IDENTITY"
    cand["target"] = {"user_claim_alias": "   "}
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False


def test_validate_identity_target_non_dict_rejected():
    cand = _ok_situational()
    cand["class"] = "IDENTITY"
    cand["target"] = "not a dict"
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False


def test_validate_situational_target_still_rejected():
    """SITUATIONAL still must not carry tier or target — only
    PROCEDURAL and IDENTITY can carry them."""
    cand = _ok_situational()
    cand["target"] = {"user_claim_alias": "x"}  # SITUATIONAL by default
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False


# --------------------------------------------------------------------
# Day 3: extended threat scanner (USER.md target)
# --------------------------------------------------------------------


def test_scanner_user_target_catches_religion():
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "User is a Christian and prays daily.",
        "religion",
        target_file="user",
    )
    assert pid is not None
    assert pid.startswith("user:religion")


def test_scanner_user_target_catches_politics():
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "User leans conservative on most issues.",
        "political views",
        target_file="user",
    )
    assert pid is not None
    assert pid.startswith("user:politics")


def test_scanner_user_target_catches_sexuality():
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "User is bisexual and uses they/them pronouns.",
        "identity",
        target_file="user",
    )
    assert pid is not None
    assert pid.startswith("user:sexuality")


def test_scanner_user_target_catches_named_third_party():
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "User's girlfriend Sarah prefers Italian food.",
        "preferences",
        target_file="user",
    )
    assert pid is not None
    assert pid.startswith("user:named-third-party")


# --------------------------------------------------------------------
# Day 3.5: adversarial coverage for the named-third-party scanner.
# This is the LOAD-BEARING safety check — it must work for every
# adversarial case the user listed plus a few extras that came up
# during design.
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,text",
    [
        ("possessive-relational",
         "User's wife Sarah prefers terse answers"),
        ("subject-on-team",
         "Sarah on the team uses Vim"),
        ("interaction-with-the-named",
         "User had a meeting with the Sarah Team Lead"),
        ("user-transitive-mentioned",
         "User mentioned Sarah in passing"),
        ("user-transitive-emailed",
         "User emailed Sarah yesterday about the proposal"),
        ("user-married-to",
         "User is married to David and they live in Berlin"),
        ("subject-action-verb",
         "Sarah said the deploy succeeded"),
        ("interaction-call",
         "User had a call with Marcus about contract terms"),
        ("possessive-tech-lead",
         "User's tech lead Maria prefers code reviews early"),
        ("possessive-team-lead",
         "User's team lead Diego runs tight standups"),
        ("interaction-chat-with",
         "User chatted with Olivia about the rollout"),
        ("interaction-spoke-to",
         "User spoke to Henry on Tuesday"),
    ],
)
def test_named_third_party_rejects(label, text):
    """Every adversarial case the user named PLUS a handful of
    grammatical variants get rejected. These are the load-bearing
    safety checks for USER.md — failure here means a real third
    party could be promoted into the user's identity profile."""
    from core.learning_review import _check_named_third_party
    result = _check_named_third_party(text)
    assert result == "user:named-third-party", (
        f"adversarial case {label!r} should reject but didn't: {text!r}"
    )


@pytest.mark.parametrize(
    "label,text",
    [
        ("self-named",
         "User is named John"),
        ("works-for-org",
         "User works for Anthropic"),
        ("uses-tech",
         "User uses Linux on a Hetzner box behind Tailscale"),
        ("self-preference",
         "User prefers terse responses for direct factual questions"),
        ("self-language",
         "User works in Python and TypeScript primarily"),
        ("org-as-subject",
         "Anthropic releases Claude updates monthly"),
        ("weekday-token",
         "User asks for status updates on Monday mornings"),
        ("month-token",
         "User starts new projects every January"),
        ("product-as-subject-via-org",
         "Postgres handles the workload fine for User"),
        ("vexis-self-reference",
         "Vexis writes to MEMORY.md when User adds notes"),
    ],
)
def test_named_third_party_allows(label, text):
    """Cases that look superficially like third-party mentions but
    are actually self-reference, organizations, technologies, or
    weekday/month tokens. The allowlist post-filter must let these
    through — false positives erode trust in the scanner."""
    from core.learning_review import _check_named_third_party
    result = _check_named_third_party(text)
    assert result is None, (
        f"non-third-party case {label!r} false-positive'd: "
        f"{text!r} → {result!r}"
    )


def test_named_third_party_allowlist_design_decision():
    """Document the allowlist design: weekdays, months, common orgs,
    and known technologies are explicitly NOT person names. The list
    is intentionally short — false positives in the allowlist are
    cheap (a real Anthropic/Linux mention slips through) compared to
    false negatives (a real third party gets immortalized in
    USER.md). Add to the allowlist only when a real false positive
    is observed in production."""
    from core.learning_review import _NON_PERSON_CAPITALIZED
    # Sanity: the most-common false-positive sources are present.
    assert "Anthropic" in _NON_PERSON_CAPITALIZED
    assert "Linux" in _NON_PERSON_CAPITALIZED
    assert "Hetzner" in _NON_PERSON_CAPITALIZED
    assert "Monday" in _NON_PERSON_CAPITALIZED
    assert "Vim" in _NON_PERSON_CAPITALIZED
    # Self-reference tokens:
    assert "User" in _NON_PERSON_CAPITALIZED
    assert "Vexis" in _NON_PERSON_CAPITALIZED


def test_named_third_party_decision_user_mentioned_sarah():
    """Documented decision for the ambiguous case the user flagged:
    'User mentioned Sarah in passing' → REJECTED.

    Reasoning: any explicit third-party name in an IDENTITY lesson
    is high-risk. The verb 'mentioned' is transitive with Sarah as
    the named object — even if benign in spirit, immortalizing the
    name in USER.md is harder to undo than dropping the candidate.
    The LLM will produce another non-naming candidate next session
    if this preference is real.
    """
    from core.learning_review import _check_named_third_party
    assert _check_named_third_party("User mentioned Sarah in passing") == \
        "user:named-third-party"


def test_named_third_party_via_scanner_target_file_user():
    """End-to-end: an adversarial case routed through the full
    scanner with target_file=user must reject."""
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "Sarah on the team uses Vim",
        "team workflow notes",
        target_file="user",
    )
    assert pid is not None
    assert pid.startswith("user:named-third-party")


def test_named_third_party_via_scanner_target_file_memory_does_not_fire():
    """target_file=memory does NOT run the third-party scanner —
    that's reserved for IDENTITY classifications routing to
    USER.md. SITUATIONAL/PROCEDURAL candidates can name third
    parties in their lesson text without rejection (the LLM steers
    correctly via prompt classification)."""
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "Sarah on the team uses Vim",
        "team workflow notes",
        target_file="memory",
    )
    assert pid is None


def test_scanner_user_target_catches_self_harm():
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "User has been suicidal in the past.",
        "wellbeing",
        target_file="user",
    )
    assert pid is not None
    assert pid.startswith("user:self-harm")


def test_scanner_user_target_catches_mental_health():
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "User struggles with depression.",
        "wellbeing",
        target_file="user",
    )
    assert pid is not None
    assert pid.startswith("user:mental-health")


def test_scanner_memory_target_skips_user_md_patterns():
    """USER.md-specific patterns must NOT fire for SITUATIONAL/MEMORY
    candidates — those go through the base scanner only. (Religion/
    politics in MEMORY.md is questionable but defensible — not the
    learning curator's job to police.)"""
    from core.learning_review import _scan_lesson_for_sensitive_content
    # This would fire for target_file="user" — confirm it does NOT
    # fire for the default memory target.
    pid = _scan_lesson_for_sensitive_content(
        "User is a Christian and prays daily.",
        "religion",
        target_file="memory",
    )
    assert pid is None


def test_scanner_user_target_still_runs_base_set():
    """USER.md scanner is layered ON TOP OF the base set — medical/
    legal/financial patterns still fire when target_file=user."""
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "Recommend antibiotic dosage based on user weight.",
        "medical",
        target_file="user",
    )
    assert pid is not None
    assert pid.startswith("medical")


def test_validate_identity_lesson_with_named_third_party_rejected():
    """End-to-end: an IDENTITY lesson naming a third party gets
    rejected at validate time via the USER.md scanner stack."""
    msgs = [_msg("user", "yeah my girlfriend Sarah likes pasta")]
    cand = {
        "class": "IDENTITY",
        "lesson": "User's girlfriend Sarah prefers Italian food.",
        "evidence": "yeah my girlfriend Sarah likes pasta",
        "scope": "personal preferences",
    }
    ok, reason = _validate_lesson(cand, msgs, max_chars=280)
    assert ok is False
    assert "sensitive" in reason.lower()
    assert "named-third-party" in reason.lower()


def test_validate_situational_lesson_with_third_party_passes():
    """Same lesson under SITUATIONAL classification does NOT fire
    the USER.md scanner — wouldn't normally happen because the LLM
    classifies correctly, but the scanner-target split is by class
    not by lesson content."""
    msgs = [_msg("user", "yeah my girlfriend Sarah likes pasta")]
    cand = {
        "class": "SITUATIONAL",
        "lesson": "User's girlfriend Sarah prefers Italian food.",
        "evidence": "yeah my girlfriend Sarah likes pasta",
        "scope": "x",
    }
    ok, reason = _validate_lesson(cand, msgs, max_chars=280)
    # Note: this passes — the SITUATIONAL scanner doesn't include the
    # USER.md-specific patterns. The defense for this content type is
    # the LLM classifying correctly (which the prompt steers toward).
    assert ok is True, reason


# --------------------------------------------------------------------
# Day 3: USER candidate queue renderer + prompt section
# --------------------------------------------------------------------


def test_render_user_candidate_queue_empty(monkeypatch, tmp_path):
    """When the queue file is empty/missing, render an explicit
    empty-state placeholder."""
    from core.learning_review import _render_user_candidate_queue
    queue_file = tmp_path / "user_candidates.json"
    monkeypatch.setattr(
        "core.learning_review.user_candidates_path", lambda: queue_file
    )
    out = _render_user_candidate_queue()
    assert "no pending or promoted USER claims yet" in out


def test_render_user_candidate_queue_lists_pending_and_promoted(monkeypatch, tmp_path):
    """The queue rendering shows both pending and promoted claims with
    their session counts and a [promoted] marker for the latter."""
    from core.learning_review import _render_user_candidate_queue
    from core.user_candidates import UserCandidateStore
    queue_file = tmp_path / "user_candidates.json"
    monkeypatch.setattr(
        "core.learning_review.user_candidates_path", lambda: queue_file
    )
    store = UserCandidateStore(queue_file)
    store.add_occurrence("Pending claim.", "sess-1", "ev")
    store.add_occurrence("Promoted claim.", "sess-1", "ev")
    store.add_occurrence("Promoted claim.", "sess-2", "ev")
    store.mark_promoted("Promoted claim.")
    out = _render_user_candidate_queue()
    assert '"Pending claim."' in out
    assert '"Promoted claim."' in out
    # Pending shows 1 session, promoted shows 2:
    assert "1 session(s)" in out
    assert "2 session(s)" in out
    assert "[promoted]" in out


def test_build_review_prompt_includes_user_queue_section():
    """The Day 3 prompt section must be present in the rendered
    prompt so the LLM knows about the alias path."""
    from core.learning_review import _build_review_prompt
    prompt = _build_review_prompt(
        "transcript",
        user_queue_text="1. \"Existing claim.\" (1 session(s))",
    )
    assert "USER candidate queue" in prompt
    assert "alias path for IDENTITY claims" in prompt
    assert "user_claim_alias" in prompt
    assert "1. \"Existing claim.\" (1 session(s))" in prompt


def test_build_review_prompt_v2_day3_sections_present():
    """Sanity check: all three v2 context-block markers get rendered."""
    from core.learning_review import _build_review_prompt
    prompt = _build_review_prompt("x")
    assert "<skill-index>" in prompt
    assert "<existing-memory>" in prompt
    assert "<user-candidates>" in prompt
    # And no markers leaked through:
    assert "{{SKILL_INDEX}}" not in prompt
    assert "{{EXISTING_MEMORY}}" not in prompt
    assert "{{USER_CANDIDATE_QUEUE}}" not in prompt


def test_validate_lesson_procedural_s1_full_pass():
    cand = {
        "class": "PROCEDURAL",
        "lesson": "When listing time-bound options, filter ahead of now.",
        "evidence": "filter to upcoming items only please",
        "scope": "time-bound listings",
        "tier": "S1",
        "target": {
            "skill_name": "communication-style",
            "patch_old_string": "## Some heading\nexisting text",
            "patch_new_string": "## Some heading\nupdated text",
        },
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is True, f"expected pass; got {reason!r}"


def test_validate_lesson_procedural_s1_missing_patch_strings():
    cand = {
        "class": "PROCEDURAL",
        "lesson": "L",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
        "tier": "S1",
        "target": {"skill_name": "some-skill"},  # missing patch_*
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "patch_old_string" in reason or "patch_new_string" in reason


def test_validate_lesson_procedural_s2_full_pass():
    cand = {
        "class": "PROCEDURAL",
        "lesson": "When working with bge-m3 on Dutch corpora, query in Dutch.",
        "evidence": "filter to upcoming items only please",
        "scope": "multilingual RAG",
        "tier": "S2",
        "target": {
            "skill_name": "multilingual-rag",
            "support_file_path": "references/dutch-bge-m3.md",
            "support_file_content": "# Dutch + bge-m3 notes\n…",
        },
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is True, f"expected pass; got {reason!r}"


def test_validate_lesson_procedural_s2_invalid_subdir():
    cand = {
        "class": "PROCEDURAL",
        "lesson": "L",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
        "tier": "S2",
        "target": {
            "skill_name": "some-skill",
            "support_file_path": "secrets/leak.md",  # bad subdir
            "support_file_content": "x",
        },
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "references/" in reason or "templates/" in reason or "scripts/" in reason


def test_validate_lesson_procedural_s3_full_pass():
    cand = {
        "class": "PROCEDURAL",
        "lesson": "When listing time-bound options, filter ahead of now.",
        "evidence": "filter to upcoming items only please",
        "scope": "time-bound listings",
        "tier": "S3",
        "target": {
            "skill_name": "time-bound-listings",
            "new_skill_body": (
                "---\n"
                "name: time-bound-listings\n"
                "description: Filter time-bound options.\n"
                "origin: learning-curator\n"
                "---\n\n"
                "# Body\n"
            ),
        },
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is True, f"expected pass; got {reason!r}"


def test_validate_lesson_procedural_s3_missing_origin_tag():
    """S3 must include origin: learning-curator in frontmatter so the
    audit trail is preserved (per §3.7 #3 of the v2 research doc)."""
    cand = {
        "class": "PROCEDURAL",
        "lesson": "L",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
        "tier": "S3",
        "target": {
            "skill_name": "new-skill",
            "new_skill_body": (
                "---\nname: new-skill\ndescription: x.\n---\n\nBody\n"
                # missing 'origin: learning-curator'
            ),
        },
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "origin" in reason


def test_validate_lesson_procedural_invalid_tier():
    cand = {
        "class": "PROCEDURAL",
        "lesson": "L",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
        "tier": "S4",  # not a valid tier
        "target": {"skill_name": "x"},
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "tier" in reason


def test_validate_lesson_procedural_missing_target():
    cand = {
        "class": "PROCEDURAL",
        "lesson": "L",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
        "tier": "S3",
        # missing target entirely
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "target" in reason


# --------------------------------------------------------------------
# run_review — success and failure paths via mocked spawn
# --------------------------------------------------------------------


def test_run_review_happy_path_one_lesson(tmp_path):
    msgs = [
        _msg("user", "list movies tonight"),
        _msg("assistant", "[movies including past ones]"),
        _msg("user", "you included past showings, filter to upcoming"),
        _msg("assistant", "fixed"),
    ]
    # v2 candidate: PROCEDURAL → S3 (no existing skills in tmp_path,
    # so the LLM picks S3). Workspace is tmp_path so the skill
    # discovery + memory dedup look at empty trees.
    response = (
        '[{'
        '"class": "PROCEDURAL", '
        '"lesson": "When listing time-bound options, filter to '
        'entries still ahead of the current time.", '
        '"evidence": "you included past showings, filter to upcoming", '
        '"scope": "time-bound listings", '
        '"tier": "S3", '
        '"target": {'
        '"skill_name": "time-bound-listings", '
        '"new_skill_body": "---\\nname: time-bound-listings\\n'
        'description: Filter time-bound options.\\n'
        'origin: learning-curator\\n---\\n\\nBody\\n"'
        '}'
        '}]'
    )
    spawn, captured = _spawn_returning(response)
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)

    assert output.error is None
    assert output.nothing_to_save is False
    assert len(output.parsed_lessons) == 1
    assert len(output.verified_lessons) == 1
    assert len(output.rejected) == 0
    assert "When listing" in output.verified_lessons[0]["lesson"]
    assert output.verified_lessons[0]["class"] == "PROCEDURAL"
    assert output.verified_lessons[0]["tier"] == "S3"

    # Phase B: assertions move from argv-shape to brain-call-record
    # shape. ``captured`` is the same BrainNull instance (the helper
    # returns a 2-tuple of (brain, brain) for diff hygiene).
    record = captured.aux_call_records()[0]
    # Recursion env propagated via env_overrides:
    assert record["env_overrides"] == {RECURSION_ENV_VAR: "1"}
    # Tier (default ``small`` per DEFAULT_SUBSYSTEM_TIERS) — review
    # is small-tier; the brain resolves it to a native model id:
    assert record["model_tier"] == "small"
    # The v2 prompt context blocks are present in the prompt:
    prompt = record["prompt"]
    assert "<skill-index>" in prompt
    assert "<existing-memory>" in prompt
    assert "Classification — required before output" in prompt
    # cwd routed to the workspace (recursion-guard JSONL placement):
    assert record["cwd"] == tmp_path


def test_run_review_nothing_to_save(tmp_path):
    msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
    spawn, _ = _spawn_returning("Nothing to save.")
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)
    assert output.nothing_to_save is True
    assert output.verified_lessons == []
    assert output.error is None


def test_run_review_evidence_verification_rejects(tmp_path):
    """Model invents a verbatim quote that isn't in the transcript."""
    msgs = [_msg("user", "one real user message")]
    response = (
        '[{"class": "SITUATIONAL", "lesson": "L", '
        '"evidence": "this string never appears in any user message", '
        '"scope": "S"}]'
    )
    spawn, _ = _spawn_returning(response)
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)
    assert len(output.parsed_lessons) == 1
    assert len(output.verified_lessons) == 0
    assert len(output.rejected) == 1
    assert "verbatim" in output.rejected[0][1]


def test_run_review_caps_at_max_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "learning_max_entries_per_session", lambda: 2)
    msgs = [_msg("user", "user phrase 1"), _msg("user", "user phrase 2"),
            _msg("user", "user phrase 3")]
    response = (
        '['
        '{"class": "SITUATIONAL", "lesson": "A", "evidence": "user phrase 1", "scope": "X"},'
        '{"class": "SITUATIONAL", "lesson": "B", "evidence": "user phrase 2", "scope": "Y"},'
        '{"class": "SITUATIONAL", "lesson": "C", "evidence": "user phrase 3", "scope": "Z"}'
        ']'
    )
    spawn, _ = _spawn_returning(response)
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)
    assert len(output.verified_lessons) == 2
    # The third one ends up rejected with the cap reason:
    assert len(output.rejected) == 1
    assert "cap" in output.rejected[0][1]


def test_run_review_max_1_s3_per_session(tmp_path, monkeypatch):
    """Per §3.7 #7 of the v2 research: at most one S3 (new-umbrella)
    lesson per reviewed session — the second is rejected with a
    cap reason. Other tier slots remain available."""
    monkeypatch.setattr(lr, "learning_max_entries_per_session", lambda: 2)
    msgs = [_msg("user", "user phrase 1"), _msg("user", "user phrase 2")]
    response = (
        '['
        '{"class": "PROCEDURAL", "lesson": "A", "evidence": "user phrase 1", '
        ' "scope": "X", "tier": "S3", '
        ' "target": {"skill_name": "skill-one", "new_skill_body": '
        '   "---\\nname: skill-one\\ndescription: D.\\norigin: learning-curator\\n---\\nB"}},'
        '{"class": "PROCEDURAL", "lesson": "B", "evidence": "user phrase 2", '
        ' "scope": "Y", "tier": "S3", '
        ' "target": {"skill_name": "skill-two", "new_skill_body": '
        '   "---\\nname: skill-two\\ndescription: D.\\norigin: learning-curator\\n---\\nB"}}'
        ']'
    )
    spawn, _ = _spawn_returning(response)
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)
    assert len(output.verified_lessons) == 1
    assert output.verified_lessons[0]["target"]["skill_name"] == "skill-one"
    assert len(output.rejected) == 1
    assert "S3-create cap" in output.rejected[0][1]


def test_run_review_subprocess_nonzero_exit(tmp_path):
    spawn, _ = _spawn_returning("oops", returncode=1, stderr="claude failed")
    output = run_review(tmp_path, _meta(), [_msg("user", "hi")], brain=spawn)
    assert output.error is not None
    assert "exited 1" in output.error


def test_run_review_unparseable_response(tmp_path):
    spawn, _ = _spawn_returning("complete garbage with no JSON")
    output = run_review(tmp_path, _meta(), [_msg("user", "hi")], brain=spawn)
    assert output.error is not None
    assert "could not parse" in output.error


def test_run_review_timeout(tmp_path):
    from core.brain.base import BrainTimeoutError
    from core.brain.null import BrainNull

    brain = BrainNull()
    brain.next_aux_raises(BrainTimeoutError("review timed out"))
    output = run_review(tmp_path, _meta(), [_msg("user", "hi")], brain=brain)
    assert output.error is not None
    assert "timed out" in output.error


def test_run_review_spawn_oserror(tmp_path):
    from core.brain.base import BrainNotInstalled
    from core.brain.null import BrainNull

    brain = BrainNull()
    brain.next_aux_raises(BrainNotInstalled("claude not on PATH"))
    output = run_review(tmp_path, _meta(), [_msg("user", "hi")], brain=brain)
    assert output.error is not None
    assert "spawn failed" in output.error


def test_run_review_logs_large_transcript_warning(tmp_path, caplog):
    """Audit catch (Day 2 user-flagged): if the transcript is large
    we MUST log a warning, never silently truncate."""
    big_text = "x" * (LARGE_TRANSCRIPT_WARN_CHARS + 100)
    msgs = [_msg("user", big_text)]
    spawn, _ = _spawn_returning("Nothing to save.")
    with caplog.at_level(logging.WARNING):
        run_review(tmp_path, _meta(), msgs, brain=spawn)
    matched = [r for r in caplog.records
               if "large transcript" in r.getMessage()]
    assert len(matched) == 1, f"expected one warning, got {len(matched)}"
    # Warning includes the actual transcript char count (which is the
    # body length plus the markdown-rendering overhead from the
    # formatter, not the raw user-text length):
    assert "Sending without truncation" in matched[0].getMessage()
    msg = matched[0].getMessage()
    # Pull out the "(N chars / M ...)" number.
    import re as _re
    m = _re.search(r"\((\d+) chars", msg)
    assert m is not None
    assert int(m.group(1)) > LARGE_TRANSCRIPT_WARN_CHARS


# --------------------------------------------------------------------
# _build_review_prompt
# --------------------------------------------------------------------


def test_build_review_prompt_includes_transcript_section():
    transcript = "### USER (...)\nhi\n"
    prompt = _build_review_prompt(transcript)
    assert "Conversation transcript" in prompt
    assert transcript in prompt
    # Hard rules carried over correctly:
    assert "evidence" in prompt
    assert "Nothing to save" in prompt
    assert "medical" in prompt and "legal" in prompt and "financial" in prompt


def test_build_review_prompt_includes_max_2_justification():
    """Carryover: prompt must explain why max-2 (not 1, not 3)."""
    prompt = _build_review_prompt("transcript")
    assert "max 2" in prompt.lower() or "max-2" in prompt.lower() or "Why max 2" in prompt


def test_build_review_prompt_includes_anti_pattern_pairs():
    prompt = _build_review_prompt("t")
    assert "BAD:" in prompt
    assert "GOOD:" in prompt


# --------------------------------------------------------------------
# Day 3: sensitive-content scanner (medical/legal/financial)
# --------------------------------------------------------------------


def test_sensitive_scan_clean_lesson_passes():
    from core.learning_review import _scan_lesson_for_sensitive_content
    assert _scan_lesson_for_sensitive_content(
        "filter time-bound options by current time",
        "scheduled listings",
    ) is None


def test_sensitive_scan_catches_medical_dosage():
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "User takes 500mg of antibiotic per day; remember dosage",
        "medical reminders",
    )
    assert pid is not None
    assert pid.startswith("medical")


def test_sensitive_scan_catches_legal_advice():
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "When user asks about contracts, give legal advice with caveats",
        "contract review",
    )
    assert pid is not None
    assert pid.startswith("legal")


def test_sensitive_scan_catches_financial_advice():
    from core.learning_review import _scan_lesson_for_sensitive_content
    pid = _scan_lesson_for_sensitive_content(
        "User prefers buy/sell signals on tech stocks every Monday",
        "investment advice timing",
    )
    assert pid is not None
    assert pid.startswith("financial")


def test_sensitive_scan_evidence_not_scanned():
    """Defense-in-depth design choice: the scanner only sees the
    rendered lesson + scope, NOT the evidence string. The user may
    legitimately quote 'my doctor prescribed' as evidence without
    that triggering a reject."""
    msgs = [_msg("user", "my doctor prescribed me an antibiotic for the infection")]
    cand = {
        "class": "SITUATIONAL",
        "lesson": "When the user describes a routine doctor visit, "
                  "follow up with whether they need help logging it.",
        "evidence": "my doctor prescribed me an antibiotic for the infection",
        "scope": "personal health admin reminders",
    }
    # Lesson + scope are clean. Evidence has 'prescribed' and
    # 'antibiotic' but those don't get scanned.
    ok, reason = _validate_lesson(cand, msgs, max_chars=280)
    assert ok is True, f"expected pass, got rejection: {reason}"


def test_validate_lesson_rejects_sensitive_content():
    msgs = [_msg("user", "what dosage should I take")]
    cand = {
        "class": "SITUATIONAL",
        "lesson": "Recommend antibiotic dosage based on user weight",
        "evidence": "what dosage should I take",
        "scope": "medical advice for user",
    }
    ok, reason = _validate_lesson(cand, msgs, max_chars=280)
    assert ok is False
    assert "sensitive" in reason.lower()


# --------------------------------------------------------------------
# Day 3: large-transcript decline
# --------------------------------------------------------------------


def test_run_review_declines_oversized_transcript(tmp_path):
    """At >200K chars we don't even spawn the LLM; we set
    declined_too_large and return. This advances last_reviewed_at
    via the controller's success path."""
    from core.learning_review import LEARNING_TRANSCRIPT_DECLINE_CHARS
    big_text = "x" * (LEARNING_TRANSCRIPT_DECLINE_CHARS + 100)
    msgs = [_msg("user", big_text)]
    spawn_called = {"called": False}

    def spawn(argv, env):
        spawn_called["called"] = True
        cp = subprocess.CompletedProcess(args=argv, returncode=0,
                                          stdout=b'Nothing to save.',
                                          stderr=b'')
        return cp

    output = run_review(tmp_path, _meta(), msgs, brain=spawn)

    assert output.declined_too_large is True
    assert output.error is None
    assert output.nothing_to_save is False
    assert output.raw_response == ""
    assert output.transcript_chars > LEARNING_TRANSCRIPT_DECLINE_CHARS
    # And the LLM was never called:
    assert spawn_called["called"] is False


def test_run_review_just_below_decline_threshold_runs(tmp_path):
    """Right at the threshold, we still send the transcript."""
    from core.learning_review import LEARNING_TRANSCRIPT_DECLINE_CHARS
    # Aim for transcript chars below the threshold. The formatter
    # adds overhead so we shoot well under to avoid flake.
    text = "y" * (LEARNING_TRANSCRIPT_DECLINE_CHARS - 5_000)
    msgs = [_msg("user", text)]
    spawn, _ = _spawn_returning("Nothing to save.")
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)
    assert output.declined_too_large is False
    assert output.nothing_to_save is True


# --------------------------------------------------------------------
# Day 1 v2: skill index + existing memory rendering
# --------------------------------------------------------------------


def test_render_skill_index_empty_tree(tmp_path):
    """When no skills exist, the index renders an explicit S3-fallback
    hint rather than an empty block."""
    from core.learning_review import _render_skill_index
    out = _render_skill_index(tmp_path / "skills")
    assert "no skills exist yet" in out
    assert "S3" in out


def test_render_skill_index_lists_active_skills(tmp_path):
    """A populated tree renders one bullet per skill with the 1-line
    description from frontmatter."""
    from core.learning_review import _render_skill_index
    skills_root = tmp_path / "skills"
    (skills_root / "alpha-skill").mkdir(parents=True)
    (skills_root / "alpha-skill" / "SKILL.md").write_text(
        "---\nname: alpha-skill\ndescription: First test skill.\n---\n\nBody\n",
        encoding="utf-8",
    )
    (skills_root / "beta-skill").mkdir(parents=True)
    (skills_root / "beta-skill" / "SKILL.md").write_text(
        "---\nname: beta-skill\ndescription: Second test skill.\n---\n\nBody\n",
        encoding="utf-8",
    )
    out = _render_skill_index(skills_root)
    assert "alpha-skill" in out
    assert "beta-skill" in out
    assert "First test skill." in out


def test_render_skill_index_marks_pinned_read_only(tmp_path):
    """Pinned skills must carry the (pinned, read-only) suffix so the
    LLM doesn't propose S1/S2 against them. They must STILL appear in
    the index so S3 collisions are avoided."""
    from core.learning_review import _render_skill_index
    from core.skills import PinStore
    skills_root = tmp_path / "skills"
    (skills_root / "free-skill").mkdir(parents=True)
    (skills_root / "free-skill" / "SKILL.md").write_text(
        "---\nname: free-skill\ndescription: D.\n---\n\nB\n",
        encoding="utf-8",
    )
    (skills_root / "pinned-skill").mkdir(parents=True)
    (skills_root / "pinned-skill" / "SKILL.md").write_text(
        "---\nname: pinned-skill\ndescription: D.\n---\n\nB\n",
        encoding="utf-8",
    )
    PinStore(skills_root).pin("pinned-skill")
    out = _render_skill_index(skills_root)
    # Both must appear (S3 collision avoidance):
    assert "free-skill" in out
    assert "pinned-skill" in out
    # Only pinned gets the marker:
    pinned_line = [ln for ln in out.split("\n") if "pinned-skill" in ln][0]
    free_line = [ln for ln in out.split("\n") if "free-skill" in ln and "pinned-skill" not in ln][0]
    assert "(pinned, read-only)" in pinned_line
    assert "(pinned, read-only)" not in free_line


def test_build_review_prompt_explains_pinned_marker():
    """The prompt must explain what (pinned, read-only) means so the
    LLM doesn't propose patches against pinned skills."""
    from core.learning_review import _build_review_prompt
    prompt = _build_review_prompt("transcript")
    assert "(pinned, read-only)" in prompt
    # The rule against patching pinned (text wraps over multiple
    # lines in the constant; check the unambiguous fragments):
    assert "cannot be patched" in prompt
    assert "Do NOT propose" in prompt
    # Whitespace-tolerant check that S1/S2 against pinned is forbidden:
    import re
    assert re.search(r"propose\s+S1\s+or\s+S2\s+against\s+a\s+pinned\s+skill", prompt)


def test_render_existing_memory_empty_files(tmp_path):
    from core.learning_review import _render_existing_memory
    out = _render_existing_memory(tmp_path)
    assert out == "(no existing entries)"


def test_render_existing_memory_combines_live_and_shadow(tmp_path):
    """The dedup view spans both MEMORY.md and MEMORY-SHADOW.md so the
    LLM can avoid duplicating either."""
    from core.learning_review import _render_existing_memory
    memdir = tmp_path / "memories"
    memdir.mkdir()
    (memdir / "MEMORY.md").write_text(
        "live entry one\n§\nlive entry two\n", encoding="utf-8",
    )
    (memdir / "MEMORY-SHADOW.md").write_text(
        "[learned 2026-05-02] shadow entry\n  Scope: x\n  Evidence: y\n",
        encoding="utf-8",
    )
    out = _render_existing_memory(tmp_path)
    assert "live entry one" in out
    assert "live entry two" in out
    assert "[learned 2026-05-02] shadow entry" in out


def test_check_evidence_overlap_substring_hit():
    from core.learning_review import _check_evidence_overlap
    existing = ["[learned 2026-05-02] X\n  Evidence: filter to upcoming items only please"]
    hit, idx = _check_evidence_overlap("filter to upcoming items only please", existing)
    assert hit is True
    assert idx == 1


def test_check_evidence_overlap_reverse_substring_hit():
    """Bidirectional: if a new long candidate quote contains an old
    short evidence string, that's still a hit."""
    from core.learning_review import _check_evidence_overlap
    existing = ["short stored entry"]
    # New candidate evidence is a longer message that contains the old entry verbatim.
    hit, idx = _check_evidence_overlap(
        "verbose user message that includes short stored entry as a fragment",
        existing,
    )
    assert hit is True
    assert idx == 1


def test_check_evidence_overlap_miss():
    from core.learning_review import _check_evidence_overlap
    existing = ["completely unrelated content"]
    hit, idx = _check_evidence_overlap("the new candidate evidence", existing)
    assert hit is False
    assert idx is None


def test_check_evidence_overlap_empty_inputs():
    from core.learning_review import _check_evidence_overlap
    assert _check_evidence_overlap("", ["x"]) == (False, None)
    assert _check_evidence_overlap("x", []) == (False, None)


def test_run_review_situational_dedup_skip(tmp_path):
    """SITUATIONAL candidate whose evidence overlaps an existing
    MEMORY.md entry is dropped with a dedup reason — not silently
    duplicated."""
    memdir = tmp_path / "memories"
    memdir.mkdir()
    (memdir / "MEMORY.md").write_text(
        "[learned 2026-05-02] User runs Vexis on Hetzner VPS at 203.0.113.42\n"
        "  Scope: env\n"
        "  Evidence: yeah this is still on the Hetzner box at 203.0.113.42",
        encoding="utf-8",
    )
    msgs = [_msg("user", "yeah this is still on the Hetzner box at 203.0.113.42")]
    response = (
        '[{"class": "SITUATIONAL", '
        '"lesson": "User runs Vexis on Hetzner VPS at 203.0.113.42.", '
        '"evidence": "yeah this is still on the Hetzner box at 203.0.113.42", '
        '"scope": "environment"}]'
    )
    spawn, _ = _spawn_returning(response)
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)
    assert len(output.parsed_lessons) == 1
    assert len(output.verified_lessons) == 0
    assert len(output.rejected) == 1
    assert "deduped" in output.rejected[0][1]


def test_build_review_prompt_substitutes_skill_index_and_memory():
    from core.learning_review import _build_review_prompt
    prompt = _build_review_prompt(
        "transcript here",
        skill_index_text="- skill-foo: foo skill",
        existing_memory_text="1. existing entry",
    )
    assert "- skill-foo: foo skill" in prompt
    assert "1. existing entry" in prompt
    # Markers must not leak through:
    assert "{{SKILL_INDEX}}" not in prompt
    assert "{{EXISTING_MEMORY}}" not in prompt


def test_build_review_prompt_v2_sections_present():
    """The new v2 prompt sections must be present in the rendered
    prompt — these guard against accidental section removal."""
    from core.learning_review import _build_review_prompt
    prompt = _build_review_prompt("x")
    assert "Classification — required before output" in prompt
    assert "PROCEDURAL" in prompt and "IDENTITY" in prompt
    assert "SITUATIONAL" in prompt and "VOLATILE" in prompt
    assert "Procedural lessons → skill tier order" in prompt
    assert "S1." in prompt and "S2." in prompt and "S3." in prompt
    assert "Existing skills you can patch" in prompt
    assert "Existing memory entries — avoid duplicates" in prompt
    assert "origin: learning-curator" in prompt
    assert "max-1" in prompt or "at MOST one S3" in prompt


# --------------------------------------------------------------------
# Two-tier triage: cheap haiku skim before sonnet
# --------------------------------------------------------------------


def _spawn_sequence(*responses):
    """Phase B: build a BrainNull pre-loaded with a sequence of
    AuxResults, one per spawn_aux call. Each entry is either a
    ``(stdout, returncode, stderr)`` tuple or a plain string treated
    as stdout/exit-0. Returns ``(brain, calls)`` where ``calls`` is
    a list-shim that exposes each spawn's ``{argv, env}`` view by
    reading ``brain.aux_call_records()`` lazily — pre-Phase-B tests
    asserted on ``calls[i]["env"][RECURSION_ENV_VAR]``; the shim
    translates that to ``aux_call_records()[i]["env_overrides"]``."""
    from core.brain.base import AuxResult
    from core.brain.null import BrainNull

    aux_results = []
    for item in responses:
        if isinstance(item, str):
            aux_results.append(AuxResult(stdout=item, stderr="", returncode=0))
        else:
            stdout, returncode, stderr = item
            aux_results.append(
                AuxResult(stdout=stdout, stderr=stderr, returncode=returncode)
            )
    brain = BrainNull(aux_results=aux_results)

    class _CallsShim:
        """List-like view over ``brain.aux_call_records()`` that
        exposes each call as ``{argv, env}`` for backward compat
        with the pre-Phase-B test assertions. The "argv" key is a
        synthetic claude-shaped argv built from the recorded prompt
        + tier so tests can still grep for ``--model``."""

        def __getitem__(self, i):
            r = brain.aux_call_records()[i]
            tier = r["model_tier"]
            argv = ["claude", "-p"]
            if tier:
                argv += ["--model", tier]
            argv.append(r["prompt"])
            return {
                "argv": argv,
                "env": dict(r["env_overrides"] or {}),
            }

        def __len__(self):
            return len(brain.aux_call_records())

    return brain, _CallsShim()


def test_parse_triage_response_yes_no_garbage():
    from core.learning_review import _parse_triage_response
    assert _parse_triage_response("YES") is True
    assert _parse_triage_response(" yes ") is True
    assert _parse_triage_response("Yes.") is True
    assert _parse_triage_response("YES — looks like a correction") is True
    assert _parse_triage_response("NO") is False
    assert _parse_triage_response("no") is False
    assert _parse_triage_response("No.") is False
    assert _parse_triage_response("maybe") is None
    assert _parse_triage_response("") is None
    assert _parse_triage_response("  ") is None
    assert _parse_triage_response(None) is None  # type: ignore[arg-type]


def test_build_triage_prompt_has_yes_no_question():
    from core.learning_review import _build_triage_prompt
    out = _build_triage_prompt("transcript body here")
    assert "YES" in out and "NO" in out
    assert "transcript body here" in out
    # Triage prompt is intentionally LIGHT — must not carry the full
    # review's heavy context blocks (skill index / existing memory /
    # USER queue). That's the whole point.
    assert "<skill-index>" not in out
    assert "<existing-memory>" not in out
    assert "<user-candidates>" not in out


def test_triage_no_skips_sonnet(tmp_path, monkeypatch):
    """Triage returns NO → sonnet is never called → output marks
    nothing-to-save with triage_skipped=True. Exactly one subprocess
    spawn (the triage call)."""
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
    spawn, calls = _spawn_sequence("NO")
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)

    assert len(calls) == 1
    assert output.nothing_to_save is True
    assert output.triage_skipped is True
    assert output.triage_result == "NO"
    assert output.error is None
    assert output.verified_lessons == []
    assert output.parsed_lessons == []


def test_triage_yes_runs_sonnet(tmp_path, monkeypatch):
    """Triage returns YES → sonnet runs → output reflects sonnet's
    response. Two spawns total."""
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    msgs = [_msg("user", "list movies tonight"),
            _msg("assistant", "[movies including past ones]"),
            _msg("user", "you included past showings, filter to upcoming"),
            _msg("assistant", "fixed")]
    sonnet_response = (
        '[{'
        '"class": "PROCEDURAL", '
        '"lesson": "When listing time-bound options, filter to '
        'entries still ahead of the current time.", '
        '"evidence": "you included past showings, filter to upcoming", '
        '"scope": "time-bound listings", '
        '"tier": "S3", '
        '"target": {'
        '"skill_name": "time-bound-listings", '
        '"new_skill_body": "---\\nname: time-bound-listings\\n'
        'description: Filter time-bound options.\\n'
        'origin: learning-curator\\n---\\n\\nBody\\n"'
        '}'
        '}]'
    )
    spawn, calls = _spawn_sequence("YES", sonnet_response)
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)

    assert len(calls) == 2
    assert output.error is None
    assert output.nothing_to_save is False
    assert output.triage_skipped is False
    assert output.triage_result == "YES"
    assert len(output.verified_lessons) == 1


def test_triage_disabled_skips_triage(tmp_path, monkeypatch):
    """With learning.triage_enabled: false the triage call is bypassed
    entirely — only one spawn (the full sonnet review)."""
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: False)
    msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
    spawn, calls = _spawn_sequence("Nothing to save.")
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)

    assert len(calls) == 1
    assert output.nothing_to_save is True
    assert output.triage_skipped is False
    assert output.triage_result == "DISABLED"


def test_triage_garbage_output_fails_open(tmp_path, monkeypatch):
    """Triage returns unparseable output → fall through to sonnet
    (fail-open). triage_result records FAIL_OPEN for the audit
    trail."""
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
    spawn, calls = _spawn_sequence("maybe", "Nothing to save.")
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)

    assert len(calls) == 2
    assert output.triage_result == "FAIL_OPEN"
    # Sonnet ran and said nothing-to-save, so output reflects that —
    # but triage_skipped is False (we didn't skip on triage's word).
    assert output.nothing_to_save is True
    assert output.triage_skipped is False


def test_triage_empty_output_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
    spawn, calls = _spawn_sequence("", "Nothing to save.")
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)

    assert len(calls) == 2
    assert output.triage_result == "FAIL_OPEN"
    assert output.nothing_to_save is True
    assert output.triage_skipped is False


def test_triage_rate_limit_propagates(tmp_path, monkeypatch):
    """Triage spawn returns rate-limit-shaped error → output.error is
    set so the curator's tick-abort path triggers. Sonnet is NOT
    called."""
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
    spawn, calls = _spawn_sequence(
        ("", 1, "anthropic: hit your limit, try again later")
    )
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)

    assert len(calls) == 1
    assert output.error is not None
    assert "hit your limit" in output.error
    assert output.triage_result == "RATE_LIMITED"
    assert output.nothing_to_save is False
    assert output.triage_skipped is False
    # Curator's _is_rate_limit_error must fire on this same string:
    from core.learning_curator import _is_rate_limit_error
    assert _is_rate_limit_error(f"error: {output.error}") is True


def test_triage_rate_limit_other_marker(tmp_path, monkeypatch):
    """Verify a couple of the other rate-limit markers also propagate
    (rate-limit, 429, usage limit)."""
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    msgs = [_msg("user", "hi")]
    for marker in ("usage limit reached", "rate limit hit", "HTTP 429 returned"):
        spawn, calls = _spawn_sequence(("", 1, marker))
        output = run_review(tmp_path, _meta(), msgs, brain=spawn)
        assert len(calls) == 1
        assert output.error is not None
        assert output.triage_result == "RATE_LIMITED"


def test_triage_spawn_oserror_fails_open(tmp_path, monkeypatch):
    """Triage subprocess raises BrainError (e.g. transient OSError
    inside the brain spawn) → fall through to sonnet."""
    from core.brain.base import AuxResult, BrainError
    from core.brain.null import BrainNull

    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    msgs = [_msg("user", "hi")]
    brain = BrainNull(
        aux_results=[
            # First call (triage) raises via next_aux_raises; second
            # call (sonnet) consumes this AuxResult.
            AuxResult(stdout="Nothing to save.", stderr="", returncode=0),
        ]
    )
    brain.next_aux_raises(BrainError("transient"))

    output = run_review(tmp_path, _meta(), msgs, brain=brain)
    assert len(brain.aux_call_records()) == 2  # triage + sonnet
    assert output.triage_result == "ERROR"
    assert output.error is None
    assert output.nothing_to_save is True
    assert output.triage_skipped is False


def test_triage_spawn_timeout_fails_open(tmp_path, monkeypatch):
    from core.brain.base import AuxResult, BrainTimeoutError
    from core.brain.null import BrainNull

    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    msgs = [_msg("user", "hi")]
    brain = BrainNull(
        aux_results=[
            AuxResult(stdout="Nothing to save.", stderr="", returncode=0),
        ]
    )
    brain.next_aux_raises(BrainTimeoutError("triage timed out"))

    output = run_review(tmp_path, _meta(), msgs, brain=brain)
    assert len(brain.aux_call_records()) == 2
    assert output.triage_result == "ERROR"


def test_triage_nonzero_exit_non_rate_limit_fails_open(tmp_path, monkeypatch):
    """Non-zero exit that does NOT look like rate-limit fails open
    rather than propagating as a review error — flaky triage shouldn't
    burn the per-session retry budget."""
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    msgs = [_msg("user", "hi")]
    spawn, calls = _spawn_sequence(
        ("", 1, "some unrelated stderr noise"),
        "Nothing to save.",
    )
    output = run_review(tmp_path, _meta(), msgs, brain=spawn)

    assert len(calls) == 2
    assert output.triage_result == "ERROR"
    assert output.error is None
    assert output.nothing_to_save is True


def test_triage_uses_recursion_env_var(tmp_path, monkeypatch):
    """Both triage and sonnet must pass VEXIS_LEARNING_REVIEW=1 in
    env_overrides so nested spawn-detection works for either call.
    Phase B: assert via brain.aux_call_records() rather than the
    pre-Phase-B argv ``env`` dict."""
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    msgs = [_msg("user", "hi")]
    brain, _ = _spawn_sequence("YES", "Nothing to save.")
    run_review(tmp_path, _meta(), msgs, brain=brain)

    records = brain.aux_call_records()
    assert records[0]["env_overrides"] == {RECURSION_ENV_VAR: "1"}
    assert records[1]["env_overrides"] == {RECURSION_ENV_VAR: "1"}


def test_triage_uses_triage_tier(tmp_path, monkeypatch):
    """Phase B: triage and review pass DIFFERENT abstract tiers to
    spawn_aux. Default subsystem map: ``learning_triage`` → ``tiny``
    (cheap), ``learning_review`` → ``small``. The brain implementation
    resolves tiers to native model ids at spawn time (claude-code:
    tiny→haiku, small→haiku per DEFAULT_TIER_MAP_CLAUDE_CODE), but
    the tier-distinction is what the test asserts."""
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    msgs = [_msg("user", "hi")]
    brain, _ = _spawn_sequence("YES", "Nothing to save.")
    run_review(tmp_path, _meta(), msgs, brain=brain)

    records = brain.aux_call_records()
    # Default subsystem tiers per core/yaml_config.py:DEFAULT_SUBSYSTEM_TIERS.
    assert records[0]["model_tier"] == "tiny"   # learning_triage
    assert records[1]["model_tier"] == "small"  # learning_review


def test_triage_skipped_for_oversized_transcript(tmp_path, monkeypatch):
    """The size-decline gate fires BEFORE triage, so an oversized
    transcript spawns nothing — triage_result stays None."""
    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)
    from core.learning_review import LEARNING_TRANSCRIPT_DECLINE_CHARS
    big_text = "x" * (LEARNING_TRANSCRIPT_DECLINE_CHARS + 100)
    msgs = [_msg("user", big_text)]

    def spawn(argv, env):
        raise AssertionError("no subprocess should fire when declined")

    output = run_review(tmp_path, _meta(), msgs, brain=spawn)
    assert output.declined_too_large is True
    assert output.triage_result is None
    assert output.triage_skipped is False


def test_triage_yaml_config_default_haiku(tmp_path):
    """yaml_config.model_learning_triage defaults to 'haiku' when no
    config file exists. yaml_config.learning_triage_enabled defaults
    to True."""
    from unittest import mock
    from core import yaml_config

    def fake_vexis_dir() -> Path:
        return tmp_path  # empty dir → no config.yaml → defaults

    with mock.patch("core.yaml_config.vexis_dir", side_effect=fake_vexis_dir):
        assert yaml_config.model_learning_triage() == "haiku"
        assert yaml_config.learning_triage_enabled() is True


def test_triage_yaml_config_overrides(tmp_path):
    """Config file can override both the model and the feature gate."""
    from unittest import mock
    from core import yaml_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models:\n"
        "  learning_triage: sonnet\n"
        "learning:\n"
        "  triage_enabled: false\n",
        encoding="utf-8",
    )

    def fake_vexis_dir() -> Path:
        return tmp_path

    with mock.patch("core.yaml_config.vexis_dir", side_effect=fake_vexis_dir):
        assert yaml_config.model_learning_triage() == "sonnet"
        assert yaml_config.learning_triage_enabled() is False
