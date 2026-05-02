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
    """Build a fake spawn callable that returns a CompletedProcess
    with the given stdout/return code. Captures argv/env so tests can
    assert what was sent."""
    captured: dict[str, Any] = {}

    def spawn(argv, env):
        captured["argv"] = argv
        captured["env"] = env
        cp = subprocess.CompletedProcess(
            args=argv, returncode=returncode,
            stdout=stdout.encode(), stderr=stderr.encode(),
        )
        return cp

    return spawn, captured


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


def test_validate_lesson_full_pass():
    cand = {
        "lesson": "L" * 50,
        "evidence": "filter to upcoming items only please",
        "scope": "time-bound listings",
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is True
    assert reason == ""


def test_validate_lesson_missing_lesson():
    cand = {"evidence": "x", "scope": "y"}
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "lesson" in reason


def test_validate_lesson_oversize_lesson():
    cand = {
        "lesson": "x" * 281,
        "evidence": "filter to upcoming items only please",
        "scope": "y",
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "exceeds" in reason


def test_validate_lesson_evidence_not_in_session():
    cand = {
        "lesson": "valid lesson",
        "evidence": "phrase that never appeared in any user message",
        "scope": "x",
    }
    ok, reason = _validate_lesson(cand, _ok_msgs(), max_chars=280)
    assert ok is False
    assert "verbatim" in reason


def test_validate_lesson_non_dict():
    ok, reason = _validate_lesson("not a dict", _ok_msgs(), max_chars=280)  # type: ignore[arg-type]
    assert ok is False


# --------------------------------------------------------------------
# run_review — success and failure paths via mocked spawn
# --------------------------------------------------------------------


def test_run_review_happy_path_one_lesson():
    msgs = [
        _msg("user", "list movies tonight"),
        _msg("assistant", "[movies including past ones]"),
        _msg("user", "you included past showings, filter to upcoming"),
        _msg("assistant", "fixed"),
    ]
    response = (
        '[{"lesson": "When listing time-bound options, filter to '
        'entries still ahead of the current time.", '
        '"evidence": "you included past showings, filter to upcoming", '
        '"scope": "time-bound listings"}]'
    )
    spawn, captured = _spawn_returning(response)
    output = run_review(Path("/tmp"), _meta(), msgs, spawn=spawn)

    assert output.error is None
    assert output.nothing_to_save is False
    assert len(output.parsed_lessons) == 1
    assert len(output.verified_lessons) == 1
    assert len(output.rejected) == 0
    assert "When listing" in output.verified_lessons[0]["lesson"]

    # Subprocess argv looks right:
    assert captured["argv"][0] == "claude"
    assert captured["argv"][1] == "-p"
    # And the recursion env is set in the child env:
    assert captured["env"][RECURSION_ENV_VAR] == "1"
    # No --resume: review session must stay isolated:
    assert "--resume" not in captured["argv"]


def test_run_review_nothing_to_save():
    msgs = [_msg("user", "hi"), _msg("assistant", "hello")]
    spawn, _ = _spawn_returning("Nothing to save.")
    output = run_review(Path("/tmp"), _meta(), msgs, spawn=spawn)
    assert output.nothing_to_save is True
    assert output.verified_lessons == []
    assert output.error is None


def test_run_review_evidence_verification_rejects():
    """Model invents a verbatim quote that isn't in the transcript."""
    msgs = [_msg("user", "one real user message")]
    response = (
        '[{"lesson": "L", '
        '"evidence": "this string never appears in any user message", '
        '"scope": "S"}]'
    )
    spawn, _ = _spawn_returning(response)
    output = run_review(Path("/tmp"), _meta(), msgs, spawn=spawn)
    assert len(output.parsed_lessons) == 1
    assert len(output.verified_lessons) == 0
    assert len(output.rejected) == 1
    assert "verbatim" in output.rejected[0][1]


def test_run_review_caps_at_max_entries(monkeypatch):
    monkeypatch.setattr(lr, "learning_max_entries_per_session", lambda: 2)
    msgs = [_msg("user", "user phrase 1"), _msg("user", "user phrase 2"),
            _msg("user", "user phrase 3")]
    response = (
        '['
        '{"lesson": "A", "evidence": "user phrase 1", "scope": "X"},'
        '{"lesson": "B", "evidence": "user phrase 2", "scope": "Y"},'
        '{"lesson": "C", "evidence": "user phrase 3", "scope": "Z"}'
        ']'
    )
    spawn, _ = _spawn_returning(response)
    output = run_review(Path("/tmp"), _meta(), msgs, spawn=spawn)
    assert len(output.verified_lessons) == 2
    # The third one ends up rejected with the cap reason:
    assert len(output.rejected) == 1
    assert "cap" in output.rejected[0][1]


def test_run_review_subprocess_nonzero_exit():
    spawn, _ = _spawn_returning("oops", returncode=1, stderr="claude failed")
    output = run_review(Path("/tmp"), _meta(), [_msg("user", "hi")], spawn=spawn)
    assert output.error is not None
    assert "exited 1" in output.error


def test_run_review_unparseable_response():
    spawn, _ = _spawn_returning("complete garbage with no JSON")
    output = run_review(Path("/tmp"), _meta(), [_msg("user", "hi")], spawn=spawn)
    assert output.error is not None
    assert "could not parse" in output.error


def test_run_review_timeout():
    def spawn(argv, env):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)
    output = run_review(Path("/tmp"), _meta(), [_msg("user", "hi")], spawn=spawn)
    assert output.error is not None
    assert "timed out" in output.error


def test_run_review_spawn_oserror():
    def spawn(argv, env):
        raise FileNotFoundError("claude not on PATH")
    output = run_review(Path("/tmp"), _meta(), [_msg("user", "hi")], spawn=spawn)
    assert output.error is not None
    assert "spawn failed" in output.error


def test_run_review_logs_large_transcript_warning(caplog):
    """Audit catch (Day 2 user-flagged): if the transcript is large
    we MUST log a warning, never silently truncate."""
    big_text = "x" * (LARGE_TRANSCRIPT_WARN_CHARS + 100)
    msgs = [_msg("user", big_text)]
    spawn, _ = _spawn_returning("Nothing to save.")
    with caplog.at_level(logging.WARNING):
        run_review(Path("/tmp"), _meta(), msgs, spawn=spawn)
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


def test_run_review_declines_oversized_transcript():
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

    output = run_review(Path("/tmp"), _meta(), msgs, spawn=spawn)

    assert output.declined_too_large is True
    assert output.error is None
    assert output.nothing_to_save is False
    assert output.raw_response == ""
    assert output.transcript_chars > LEARNING_TRANSCRIPT_DECLINE_CHARS
    # And the LLM was never called:
    assert spawn_called["called"] is False


def test_run_review_just_below_decline_threshold_runs(monkeypatch):
    """Right at the threshold, we still send the transcript."""
    from core.learning_review import LEARNING_TRANSCRIPT_DECLINE_CHARS
    # Aim for transcript chars below the threshold. The formatter
    # adds overhead so we shoot well under to avoid flake.
    text = "y" * (LEARNING_TRANSCRIPT_DECLINE_CHARS - 5_000)
    msgs = [_msg("user", text)]
    spawn, _ = _spawn_returning("Nothing to save.")
    output = run_review(Path("/tmp"), _meta(), msgs, spawn=spawn)
    assert output.declined_too_large is False
    assert output.nothing_to_save is True
