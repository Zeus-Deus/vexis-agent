"""Day 1 tests for core/coherence_judge.

Coverage matches the §7 Day 1 checkpoint, updated for Phase B of the
brain abstraction:
  - run_coherence_judge: success path with BrainNull seam (replaces
    the pre-Phase-B spawn-callable seam), INCOHERENT shortcut on
    missing evidence, degraded mode (empty messages), timeout /
    spawn-error / non-zero-exit / parse-failure → NEAR_MISS_REVIEW
    with reason=other.

Argv-shape and env-override invariants now live in
``tests/test_brain_contract.py`` against ``ClaudeCodeBrain.spawn_aux``;
this file asserts on ``brain.aux_call_records()`` for the judge's
contract with the brain (prompt content, env-override key, tier name).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from vexis_agent.core import coherence_judge as cj
from vexis_agent.core.brain.base import (
    AuxResult,
    BrainError,
    BrainNotInstalled,
    BrainTimeoutError,
)
from vexis_agent.core.brain.null import BrainNull
from vexis_agent.core.coherence_judge import (
    COHERENCE_JUDGE_ENV_VAR,
    CoherenceVerdict,
    WINDOW_MAX_CHARS,
    _build_judge_prompt,
    _extract_verdict,
    _find_window_bounds,
    _render_transcript_window,
    _validate_verdict_dict,
    find_evidence_message_index,
    run_coherence_judge,
)
from vexis_agent.core.transcripts import TranscriptMessage


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def _msg(
    role: str,
    text: str,
    *,
    ts: str = "2026-05-02T10:00:00Z",
    uuid: str = "m1",
) -> TranscriptMessage:
    return TranscriptMessage(
        role=role,
        text=text,
        timestamp=datetime.fromisoformat(ts.replace("Z", "+00:00")),
        uuid=uuid,
        tool_calls=(),
        raw={},
    )


def _brain_returning(stdout: str, *, returncode: int = 0, stderr: str = "") -> BrainNull:
    """Build a BrainNull pre-loaded with one AuxResult — the judge
    consumes one ``spawn_aux`` per call. Phase B replacement for the
    old ``_spawn_returning`` helper."""
    return BrainNull(
        aux_results=[
            AuxResult(stdout=stdout, stderr=stderr, returncode=returncode)
        ]
    )


@pytest.fixture(autouse=True)
def isolated_yaml_config(monkeypatch, tmp_path):
    """Phase B: tier resolution reads ``~/.vexis/config.yaml``. Tests
    must not see the user's real config — otherwise the
    ``models.coherence_judge`` legacy raw-string would override the
    default tier and confuse assertions on ``model_tier``. Point the
    config path at a fresh tmp file for every test."""
    from vexis_agent.core import yaml_config

    cfg_dir = tmp_path / "vexis-config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(yaml_config, "_config_path", lambda: cfg_dir / "config.yaml")
    yield


# --------------------------------------------------------------------
# find_evidence_message_index
# --------------------------------------------------------------------


def test_find_evidence_message_index_hit():
    msgs = [
        _msg("assistant", "I'll filter by current time"),
        _msg("user", "yeah some of these aren't playing anymore"),
        _msg("assistant", "got it"),
    ]
    assert find_evidence_message_index(msgs, "aren't playing anymore") == 1


def test_find_evidence_message_index_returns_first_hit():
    """Multiple user messages contain the evidence — return the first."""
    msgs = [
        _msg("user", "thing happens", uuid="m1"),
        _msg("assistant", "ok"),
        _msg("user", "thing happens again", uuid="m2"),
    ]
    assert find_evidence_message_index(msgs, "thing happens") == 0


def test_find_evidence_message_index_ignores_assistant_text():
    """v2's verbatim-evidence check requires evidence to be in a
    USER message; the judge inherits this — assistant text doesn't
    count even if it contains the string."""
    msgs = [
        _msg("assistant", "you said 'pizza tonight' — got it"),
        _msg("user", "yes"),
    ]
    assert find_evidence_message_index(msgs, "pizza tonight") == -1


def test_find_evidence_message_index_empty_evidence():
    msgs = [_msg("user", "anything")]
    assert find_evidence_message_index(msgs, "") == -1


def test_find_evidence_message_index_no_messages():
    assert find_evidence_message_index([], "anything") == -1


# --------------------------------------------------------------------
# _find_window_bounds
# --------------------------------------------------------------------


def test_find_window_bounds_full_5_turn_window():
    """Build alternating user/assistant turns and verify ±5 user-
    turn bounds with the evidence in the middle."""
    # 11 user turns total, evidence at index 10 (the 6th user turn)
    msgs: list[TranscriptMessage] = []
    for i in range(11):
        msgs.append(_msg("user", f"u{i}", uuid=f"u{i}"))
        msgs.append(_msg("assistant", f"a{i}", uuid=f"a{i}"))
    # Evidence at the 6th user turn (index 10)
    evidence_index = 10  # u5 (0-indexed)
    start, end = _find_window_bounds(msgs, evidence_index, turns_each_side=5)
    # Should include 5 user turns before (u0-u4) and 5 after (u6-u10).
    # u0 is at index 0; u10 is at index 20.
    assert start == 0
    assert end == 20


def test_find_window_bounds_clamped_at_start():
    """Evidence near the start: window clamps at index 0."""
    msgs = [
        _msg("user", "u0"),
        _msg("assistant", "a0"),
        _msg("user", "u1_evidence"),
        _msg("assistant", "a1"),
        _msg("user", "u2"),
    ]
    start, end = _find_window_bounds(msgs, 2, turns_each_side=5)
    assert start == 0
    assert end == 4


def test_find_window_bounds_clamped_at_end():
    msgs = [
        _msg("user", "u0"),
        _msg("user", "u1_evidence"),
    ]
    start, end = _find_window_bounds(msgs, 1, turns_each_side=5)
    assert start == 0
    assert end == 1


def test_find_window_bounds_single_message():
    msgs = [_msg("user", "only one")]
    start, end = _find_window_bounds(msgs, 0, turns_each_side=5)
    assert start == 0
    assert end == 0


def test_find_window_bounds_out_of_range_returns_index():
    """Defensive: out-of-range index returns a degenerate (idx, idx)
    pair rather than raising."""
    msgs = [_msg("user", "x")]
    start, end = _find_window_bounds(msgs, 99, turns_each_side=5)
    assert start == 99
    assert end == 99


def test_find_window_bounds_two_turn_fallback():
    """The fallback window is ±2 user turns — verify the count."""
    msgs: list[TranscriptMessage] = []
    for i in range(7):
        msgs.append(_msg("user", f"u{i}", uuid=f"u{i}"))
        msgs.append(_msg("assistant", f"a{i}", uuid=f"a{i}"))
    # Evidence at index 8 (u4, the 5th user turn — middle of the array)
    start, end = _find_window_bounds(msgs, 8, turns_each_side=2)
    # Should include u2, u3, u4 (evidence), u5, u6 → 2 before, 2 after.
    # u2 at index 4; u6 at index 12.
    assert start == 4
    assert end == 12


# --------------------------------------------------------------------
# _render_transcript_window
# --------------------------------------------------------------------


def test_render_transcript_window_default():
    msgs = [
        _msg("user", "first"),
        _msg("assistant", "ok"),
        _msg("user", "evidence here"),
        _msg("assistant", "got it"),
        _msg("user", "after"),
    ]
    out = _render_transcript_window(msgs, 2)
    assert "evidence here" in out
    assert "first" in out
    assert "after" in out
    # Renders via _format_transcript so role headers should appear
    assert "USER" in out
    assert "ASSISTANT" in out


def test_render_transcript_window_out_of_bounds_returns_empty():
    msgs = [_msg("user", "x")]
    assert _render_transcript_window(msgs, 99) == ""
    assert _render_transcript_window(msgs, -1) == ""


def test_render_transcript_window_fallback_on_cap():
    """A long transcript window that exceeds max_chars at ±5 falls
    back to ±2. Verified by comparing the rendered length at ±5
    (no cap) vs ±5-with-cap-that-forces-fallback — the latter must
    be strictly smaller (the fallback actually triggered)."""
    msgs: list[TranscriptMessage] = []
    for i in range(11):
        msgs.append(_msg("user", f"u{i} " + "x" * 200, uuid=f"u{i}"))
        msgs.append(_msg("assistant", f"a{i} " + "y" * 200, uuid=f"a{i}"))
    # Evidence at u5 (index 10).
    full = _render_transcript_window(
        msgs, 10, max_chars=10**9, turns_each_side=5, fallback_turns=2,
    )
    capped = _render_transcript_window(
        msgs, 10, max_chars=2000, turns_each_side=5, fallback_turns=2,
    )
    assert len(capped) < len(full)
    # Evidence message body still present after fallback
    assert "u5" in capped


def test_render_transcript_window_truncates_evidence_body_when_still_over():
    """When even ±2 exceeds the cap, truncate the evidence body but
    retain the evidence quote intact."""
    big_user_text = (
        "before " + ("x" * 8000) + " EVIDENCE_QUOTE " + ("y" * 8000)
    )
    msgs = [
        _msg("user", big_user_text),
    ]
    out = _render_transcript_window(
        msgs,
        0,
        max_chars=5000,
        turns_each_side=5,
        fallback_turns=2,
        evidence_text="EVIDENCE_QUOTE",
    )
    # Evidence quote must be retained intact
    assert "EVIDENCE_QUOTE" in out
    # Truncation marker must appear (the "x"*8000 prefix should be
    # truncated since 8000 > EVIDENCE_BODY_TRUNCATE_PREFIX=4000)
    assert "[truncated]" in out


def test_render_transcript_window_no_truncation_when_fits():
    """When the default ±5 window fits, no truncation marker."""
    msgs = [
        _msg("user", "short evidence"),
    ]
    out = _render_transcript_window(msgs, 0, evidence_text="short evidence")
    assert "short evidence" in out
    assert "[truncated]" not in out


# --------------------------------------------------------------------
# _build_judge_prompt
# --------------------------------------------------------------------


def test_build_judge_prompt_standard_shape():
    lesson = {
        "class": "PROCEDURAL",
        "tier": "S3",
        "lesson": "Always do X when Y.",
        "scope": "Y-related tasks",
        "evidence": "do X please",
    }
    out = _build_judge_prompt(
        lesson, transcript_window="### USER (...)\nstuff\n"
    )
    assert "PROCEDURAL" in out
    assert "Tier: S3" in out
    assert "Always do X when Y." in out
    assert "Y-related tasks" in out
    assert "do X please" in out
    assert "### USER" in out
    # The output contract instructions land in the prompt
    assert "COHERENT" in out
    assert "INCOHERENT" in out
    assert "NEAR_MISS_REVIEW" in out


def test_build_judge_prompt_with_target_body():
    lesson = {
        "class": "PROCEDURAL",
        "tier": "S1",
        "lesson": "lesson",
        "scope": "scope",
        "evidence": "ev",
    }
    out = _build_judge_prompt(
        lesson,
        transcript_window="window",
        target_body="## Existing skill body\n\nstep 1...",
    )
    assert "Target the lesson would write to" in out
    assert "step 1" in out


def test_build_judge_prompt_omits_target_block_when_none():
    lesson = {
        "class": "SITUATIONAL",
        "lesson": "lesson",
        "scope": "scope",
        "evidence": "ev",
    }
    out = _build_judge_prompt(lesson, transcript_window="window")
    assert "Target the lesson would write to" not in out


def test_build_judge_prompt_omits_tier_for_non_procedural():
    """IDENTITY/SITUATIONAL don't carry tier; the prompt should not
    have a ``Tier:`` line for them."""
    lesson = {
        "class": "IDENTITY",
        "lesson": "User prefers terse responses.",
        "scope": "communication",
        "evidence": "stop padding",
    }
    out = _build_judge_prompt(lesson, transcript_window="window")
    assert "Tier:" not in out
    assert "IDENTITY" in out


def test_build_judge_prompt_degraded_mode():
    lesson = {
        "class": "PROCEDURAL",
        "tier": "S3",
        "lesson": "lesson",
        "scope": "scope",
        "evidence": "ev",
    }
    out = _build_judge_prompt(
        lesson, transcript_window="", degraded=True
    )
    assert "transcript window unavailable" in out
    assert "lean toward NEAR_MISS_REVIEW" in out


# --------------------------------------------------------------------
# _extract_verdict / _validate_verdict_dict
# --------------------------------------------------------------------


def test_extract_verdict_bare_json_coherent():
    out = _extract_verdict('{"verdict": "COHERENT", "reason": null, "explanation": null}')
    assert out is not None
    assert out.verdict == "COHERENT"
    assert out.reason is None
    assert out.explanation is None


def test_extract_verdict_bare_json_incoherent():
    out = _extract_verdict(
        '{"verdict": "INCOHERENT", "reason": "mismatched-attribution", '
        '"explanation": "evidence is about Tailscale; lesson is about Python"}'
    )
    assert out is not None
    assert out.verdict == "INCOHERENT"
    assert out.reason == "mismatched-attribution"
    assert "Tailscale" in out.explanation


def test_extract_verdict_near_miss():
    out = _extract_verdict(
        '{"verdict": "NEAR_MISS_REVIEW", "reason": "narrow-one-shot", '
        '"explanation": "evidence is one tactical exchange"}'
    )
    assert out is not None
    assert out.verdict == "NEAR_MISS_REVIEW"
    assert out.reason == "narrow-one-shot"


def test_extract_verdict_code_fenced():
    out = _extract_verdict(
        '```json\n{"verdict": "COHERENT", "reason": null, "explanation": null}\n```'
    )
    assert out is not None
    assert out.verdict == "COHERENT"


def test_extract_verdict_object_in_prose():
    """Defensive — model occasionally pads with prose despite the
    'JSON only' instruction. Verify we still extract."""
    raw = (
        "Looking at this, I think:\n"
        '{"verdict": "INCOHERENT", "reason": "hallucinated-inference", '
        '"explanation": "claim is not in the evidence"}\n'
        "That's my judgment."
    )
    out = _extract_verdict(raw)
    assert out is not None
    assert out.verdict == "INCOHERENT"


def test_extract_verdict_returns_none_on_malformed():
    assert _extract_verdict("just prose") is None
    assert _extract_verdict("") is None
    assert _extract_verdict("{not json") is None


def test_extract_verdict_returns_none_on_unknown_verdict():
    assert _extract_verdict(
        '{"verdict": "MAYBE", "reason": null, "explanation": "..."}'
    ) is None


def test_extract_verdict_returns_none_on_incoherent_missing_reason():
    """INCOHERENT requires a valid reason — schema violation."""
    assert _extract_verdict(
        '{"verdict": "INCOHERENT", "reason": null, "explanation": "x"}'
    ) is None
    assert _extract_verdict(
        '{"verdict": "INCOHERENT", "reason": "made-up", "explanation": "x"}'
    ) is None


def test_extract_verdict_returns_none_on_missing_explanation():
    """NEAR_MISS_REVIEW and INCOHERENT require explanation."""
    assert _extract_verdict(
        '{"verdict": "NEAR_MISS_REVIEW", "reason": "other", "explanation": ""}'
    ) is None
    assert _extract_verdict(
        '{"verdict": "INCOHERENT", "reason": "other", "explanation": null}'
    ) is None


def test_extract_verdict_near_miss_with_unknown_reason_coerces_to_none():
    """An INCOHERENT with unknown reason is rejected (above), but a
    NEAR_MISS_REVIEW with unknown reason should keep the verdict and
    drop the reason — the verdict is still actionable."""
    out = _extract_verdict(
        '{"verdict": "NEAR_MISS_REVIEW", "reason": "made-up", '
        '"explanation": "thin grounding"}'
    )
    assert out is not None
    assert out.verdict == "NEAR_MISS_REVIEW"
    assert out.reason is None
    assert "thin" in out.explanation


def test_extract_verdict_returns_none_on_non_string_input():
    assert _extract_verdict(None) is None  # type: ignore[arg-type]
    assert _extract_verdict(123) is None  # type: ignore[arg-type]


def test_validate_verdict_dict_accepts_all_known_reasons():
    for reason in cj._VALID_REASONS:
        out = _validate_verdict_dict({
            "verdict": "INCOHERENT",
            "reason": reason,
            "explanation": "x",
        })
        assert out is not None, f"reason {reason} should be accepted"
        assert out.reason == reason


# --------------------------------------------------------------------
# run_coherence_judge — full pipeline with BrainNull seam
# --------------------------------------------------------------------


def test_run_coherence_judge_success_path(tmp_path):
    msgs = [
        _msg("user", "first"),
        _msg("assistant", "ok"),
        _msg("user", "actual evidence here"),
    ]
    lesson = {
        "class": "PROCEDURAL",
        "tier": "S3",
        "lesson": "When evidence happens, do thing.",
        "scope": "evidence-related tasks",
        "evidence": "actual evidence here",
    }
    brain = _brain_returning(
        '{"verdict": "COHERENT", "reason": null, "explanation": null}'
    )
    out = run_coherence_judge(tmp_path, lesson, msgs, brain)

    assert out.verdict == "COHERENT"
    assert out.degraded is False
    record = brain.aux_call_records()[0]
    # Recursion-guard env set so the curator won't re-review the
    # judge's own session JSONL.
    assert record["env_overrides"] == {COHERENCE_JUDGE_ENV_VAR: "1"}
    # Tier defaults to "small" per DEFAULT_SUBSYSTEM_TIERS.
    assert record["model_tier"] == "small"
    # Evidence reaches the prompt.
    assert "actual evidence here" in record["prompt"]
    # cwd routed correctly (recursion-guard JSONL placement).
    assert record["cwd"] == tmp_path


def test_run_coherence_judge_returns_incoherent_on_missing_evidence(tmp_path):
    """v2 verifier should have caught this; if it didn't, judge
    flags INCOHERENT(hallucinated-inference) without spawning."""
    msgs = [_msg("user", "different content")]
    lesson = {
        "class": "PROCEDURAL",
        "tier": "S3",
        "lesson": "x",
        "scope": "y",
        "evidence": "this string is not in the transcript",
    }
    # Brain shouldn't be called; an empty BrainNull would return a
    # default empty AuxResult, but the judge short-circuits before.
    brain = BrainNull()
    out = run_coherence_judge(tmp_path, lesson, msgs, brain)
    assert out.verdict == "INCOHERENT"
    assert out.reason == "hallucinated-inference"
    assert "not found" in out.explanation
    assert brain.aux_calls() == []  # never spawned


def test_run_coherence_judge_degraded_mode_empty_messages(tmp_path):
    """Manual /learning coherence-audit on a v1-era live entry gets
    no transcript window. The judge runs anyway with degraded=True."""
    lesson = {
        "class": "PROCEDURAL",
        "tier": "S3",
        "lesson": "x",
        "scope": "y",
        "evidence": "ev",
    }
    brain = _brain_returning(
        '{"verdict": "NEAR_MISS_REVIEW", "reason": "other", '
        '"explanation": "thin grounding without transcript"}'
    )
    out = run_coherence_judge(tmp_path, lesson, [], brain)

    assert out.verdict == "NEAR_MISS_REVIEW"
    assert out.degraded is True
    # Prompt mentions the degraded-mode note.
    record = brain.aux_call_records()[0]
    assert "transcript window unavailable" in record["prompt"]


def test_run_coherence_judge_timeout_returns_near_miss(tmp_path):
    msgs = [_msg("user", "evidence here")]
    lesson = {
        "class": "PROCEDURAL", "tier": "S3",
        "lesson": "x", "scope": "y", "evidence": "evidence here",
    }
    brain = BrainNull()
    brain.next_aux_raises(BrainTimeoutError("aux timed out"))
    out = run_coherence_judge(tmp_path, lesson, msgs, brain)
    assert out.verdict == "NEAR_MISS_REVIEW"
    assert out.reason == "other"
    assert "timed out" in out.explanation


def test_run_coherence_judge_spawn_failure_returns_near_miss(tmp_path):
    msgs = [_msg("user", "evidence here")]
    lesson = {
        "class": "PROCEDURAL", "tier": "S3",
        "lesson": "x", "scope": "y", "evidence": "evidence here",
    }
    brain = BrainNull()
    brain.next_aux_raises(BrainNotInstalled("claude binary missing"))
    out = run_coherence_judge(tmp_path, lesson, msgs, brain)
    assert out.verdict == "NEAR_MISS_REVIEW"
    assert "spawn failed" in out.explanation


def test_run_coherence_judge_brain_error_returns_near_miss(tmp_path):
    """Catch-all for OSError-shaped failures inside spawn_aux."""
    msgs = [_msg("user", "evidence here")]
    lesson = {
        "class": "PROCEDURAL", "tier": "S3",
        "lesson": "x", "scope": "y", "evidence": "evidence here",
    }
    brain = BrainNull()
    brain.next_aux_raises(BrainError("opaque subprocess failure"))
    out = run_coherence_judge(tmp_path, lesson, msgs, brain)
    assert out.verdict == "NEAR_MISS_REVIEW"
    assert "spawn failed" in out.explanation


def test_run_coherence_judge_nonzero_exit_returns_near_miss(tmp_path):
    msgs = [_msg("user", "evidence here")]
    lesson = {
        "class": "PROCEDURAL", "tier": "S3",
        "lesson": "x", "scope": "y", "evidence": "evidence here",
    }
    brain = _brain_returning("oops", returncode=1, stderr="claude failed")
    out = run_coherence_judge(tmp_path, lesson, msgs, brain)
    assert out.verdict == "NEAR_MISS_REVIEW"
    assert "exited 1" in out.explanation


def test_run_coherence_judge_malformed_response_returns_near_miss(tmp_path):
    msgs = [_msg("user", "evidence here")]
    lesson = {
        "class": "PROCEDURAL", "tier": "S3",
        "lesson": "x", "scope": "y", "evidence": "evidence here",
    }
    brain = _brain_returning("complete prose with no json")
    out = run_coherence_judge(tmp_path, lesson, msgs, brain)
    assert out.verdict == "NEAR_MISS_REVIEW"
    assert out.reason == "other"
    assert "malformed" in out.explanation


def test_run_coherence_judge_passes_target_body_to_prompt(tmp_path):
    msgs = [_msg("user", "evidence here")]
    lesson = {
        "class": "PROCEDURAL", "tier": "S1",
        "lesson": "x", "scope": "y", "evidence": "evidence here",
    }
    brain = _brain_returning(
        '{"verdict": "COHERENT", "reason": null, "explanation": null}'
    )
    run_coherence_judge(
        tmp_path, lesson, msgs, brain,
        target_body="## Existing skill body\n\ndo Y when Z\n",
    )
    prompt = brain.aux_call_records()[0]["prompt"]
    assert "Existing skill body" in prompt
    assert "do Y when Z" in prompt


# --------------------------------------------------------------------
# CoherenceVerdict factory ergonomics
# --------------------------------------------------------------------


def test_coherence_verdict_factories():
    coh = CoherenceVerdict.coherent()
    assert coh.verdict == "COHERENT"
    assert coh.reason is None
    assert coh.explanation is None
    assert coh.degraded is False

    nm = CoherenceVerdict.near_miss(reason="other", explanation="thin")
    assert nm.verdict == "NEAR_MISS_REVIEW"
    assert nm.reason == "other"
    assert nm.explanation == "thin"

    inc = CoherenceVerdict.incoherent(
        reason="mismatched-attribution", explanation="topic mismatch"
    )
    assert inc.verdict == "INCOHERENT"
    assert inc.reason == "mismatched-attribution"
