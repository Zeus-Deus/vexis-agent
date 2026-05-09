"""Day 3 tests for the C1/C2/C3 grader in scripts/eval_learning.py.

Validates the grading rules without firing real claude -p calls:
fixture file is loadable, the per-fixture grader handles each
category correctly (known_bad / known_good / adversarial_near_miss),
and the report's passes_bar correctly enforces strict + hard-fail
guards.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vexis_agent.core.coherence_judge import CoherenceVerdict
from scripts.eval_learning import (
    COHERENCE_FIXTURES_PATH,
    CoherenceEvalReport,
    CoherenceFixtureResult,
    _grade_coherence_fixture,
)


# --------------------------------------------------------------------
# Fixture file loadability
# --------------------------------------------------------------------


def test_fixture_file_loads_and_has_expected_categories():
    raw = json.loads(COHERENCE_FIXTURES_PATH.read_text(encoding="utf-8"))
    fixtures = raw.get("fixtures") or []
    by_cat: dict[str, int] = {}
    for f in fixtures:
        by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1
    assert by_cat["known_bad"] == 3
    assert by_cat["known_good"] == 5
    assert by_cat["adversarial_near_miss"] == 3


def test_fixture_file_has_required_fields():
    raw = json.loads(COHERENCE_FIXTURES_PATH.read_text(encoding="utf-8"))
    for f in raw["fixtures"]:
        assert "id" in f
        assert "category" in f
        assert "expected_verdict" in f
        assert "lesson" in f
        assert "synthetic_transcript" in f
        # Lesson must carry the four core fields the judge consumes
        for key in ("class", "lesson", "scope", "evidence"):
            assert key in f["lesson"], f"fixture {f['id']} missing lesson.{key}"


def test_known_bad_fixtures_have_expected_reason_set():
    """Known-bad fixtures should declare which reasons are
    acceptable for the INCOHERENT verdict, so the grader doesn't
    over-reject when the judge picks a defensible-but-different
    reason from the catalog."""
    raw = json.loads(COHERENCE_FIXTURES_PATH.read_text(encoding="utf-8"))
    for f in raw["fixtures"]:
        if f["category"] == "known_bad":
            assert f.get("expected_reason_set"), (
                f"known_bad fixture {f['id']} should declare "
                f"expected_reason_set"
            )


def test_evidence_is_in_synthetic_transcript():
    """Each fixture's evidence string must appear verbatim in the
    message at evidence_message_index — same invariant
    find_evidence_message_index enforces."""
    raw = json.loads(COHERENCE_FIXTURES_PATH.read_text(encoding="utf-8"))
    for f in raw["fixtures"]:
        idx = f["evidence_message_index"]
        msg = f["synthetic_transcript"][idx]
        evidence = f["lesson"]["evidence"]
        assert evidence in msg[1], (
            f"fixture {f['id']}: evidence not in transcript[{idx}]"
        )
        assert msg[0] == "user", (
            f"fixture {f['id']}: evidence message must be a user turn"
        )


# --------------------------------------------------------------------
# _grade_coherence_fixture — per-fixture grading rules
# --------------------------------------------------------------------


def _bad_fixture(reason_set=("mismatched-attribution",)) -> dict:
    return {
        "id": "bad_x",
        "category": "known_bad",
        "expected_verdict": "INCOHERENT",
        "expected_reason_set": list(reason_set),
        "lesson": {},
    }


def _good_fixture() -> dict:
    return {
        "id": "good_x",
        "category": "known_good",
        "expected_verdict": "COHERENT",
        "lesson": {},
    }


def _nm_fixture(expected: str, reason_set: tuple = ()) -> dict:
    return {
        "id": "near_x",
        "category": "adversarial_near_miss",
        "expected_verdict": expected,
        "expected_reason_set": list(reason_set),
        "lesson": {},
    }


def test_grader_known_bad_pass_on_incoherent_with_expected_reason():
    fixture = _bad_fixture()
    verdict = CoherenceVerdict.incoherent(
        "mismatched-attribution", "topic mismatch"
    )
    passed, reason = _grade_coherence_fixture(fixture, verdict)
    assert passed is True
    assert "INCOHERENT" in reason


def test_grader_known_bad_fail_on_wrong_reason():
    fixture = _bad_fixture(reason_set=("mismatched-attribution",))
    verdict = CoherenceVerdict.incoherent(
        "scope-overflow", "scope too broad"
    )
    passed, reason = _grade_coherence_fixture(fixture, verdict)
    assert passed is False
    assert "not in expected set" in reason


def test_grader_known_bad_soft_pass_on_near_miss():
    fixture = _bad_fixture()
    verdict = CoherenceVerdict.near_miss("other", "thin grounding")
    passed, _reason = _grade_coherence_fixture(fixture, verdict)
    assert passed is True


def test_grader_known_bad_fail_on_coherent():
    fixture = _bad_fixture()
    verdict = CoherenceVerdict.coherent()
    passed, reason = _grade_coherence_fixture(fixture, verdict)
    assert passed is False
    assert "missed" in reason


def test_grader_known_good_pass_on_coherent():
    fixture = _good_fixture()
    verdict = CoherenceVerdict.coherent()
    passed, _ = _grade_coherence_fixture(fixture, verdict)
    assert passed is True


def test_grader_known_good_tolerates_near_miss():
    """C2 allows ≤1 NEAR_MISS_REVIEW degradation per §3.5."""
    fixture = _good_fixture()
    verdict = CoherenceVerdict.near_miss("other", "thin")
    passed, _ = _grade_coherence_fixture(fixture, verdict)
    assert passed is True


def test_grader_known_good_fail_on_incoherent():
    fixture = _good_fixture()
    verdict = CoherenceVerdict.incoherent(
        "mismatched-attribution", "wrong"
    )
    passed, reason = _grade_coherence_fixture(fixture, verdict)
    assert passed is False
    assert "false-positive" in reason


def test_grader_nm_exact_match_pass():
    fixture = _nm_fixture("INCOHERENT", reason_set=("mismatched-attribution",))
    verdict = CoherenceVerdict.incoherent(
        "mismatched-attribution", "x"
    )
    passed, _ = _grade_coherence_fixture(fixture, verdict)
    assert passed is True


def test_grader_nm_no_degradation_tolerated():
    """C3 grades exact-match: a NEAR_MISS_REVIEW where INCOHERENT
    was expected does not pass the per-fixture grade (the hard-fail
    guards live in CoherenceEvalReport.passes_bar separately)."""
    fixture = _nm_fixture("INCOHERENT", reason_set=("mismatched-attribution",))
    verdict = CoherenceVerdict.near_miss("other", "thin")
    passed, _ = _grade_coherence_fixture(fixture, verdict)
    assert passed is False


def test_grader_nm_coherent_expected_passes_on_coherent():
    fixture = _nm_fixture("COHERENT")
    verdict = CoherenceVerdict.coherent()
    passed, _ = _grade_coherence_fixture(fixture, verdict)
    assert passed is True


# --------------------------------------------------------------------
# CoherenceEvalReport.passes_bar — strict + hard-fail guards
# --------------------------------------------------------------------


def _result(fid: str, category: str, expected: str, actual_verdict: str,
            actual_reason: str | None = None,
            expected_reason_set: tuple = ()) -> CoherenceFixtureResult:
    """Build a synthetic CoherenceFixtureResult for passes_bar tests."""
    fixture_dict = {
        "id": fid, "category": category,
        "expected_verdict": expected,
        "expected_reason_set": list(expected_reason_set),
        "lesson": {},
    }
    if actual_verdict == "COHERENT":
        verdict = CoherenceVerdict.coherent()
    elif actual_verdict == "INCOHERENT":
        verdict = CoherenceVerdict.incoherent(
            actual_reason or "mismatched-attribution", "x"
        )
    else:
        verdict = CoherenceVerdict.near_miss(actual_reason, "x")
    passed, pass_reason = _grade_coherence_fixture(fixture_dict, verdict)
    return CoherenceFixtureResult(
        fixture_id=fid,
        category=category,
        expected_verdict=expected,
        expected_reason_set=list(expected_reason_set),
        actual_verdict=verdict.verdict,
        actual_reason=verdict.reason,
        actual_explanation=verdict.explanation or "",
        degraded=False,
        passed=passed,
        pass_reason=pass_reason,
    )


def test_report_passes_bar_when_all_grades_pass():
    fixtures = [
        _result(f"bad_{i}", "known_bad", "INCOHERENT", "INCOHERENT",
                "mismatched-attribution",
                ("mismatched-attribution",))
        for i in range(3)
    ] + [
        _result(f"good_{i}", "known_good", "COHERENT", "COHERENT")
        for i in range(5)
    ] + [
        _result("near_sarcasm", "adversarial_near_miss", "INCOHERENT",
                "INCOHERENT", "mismatched-attribution",
                ("mismatched-attribution",)),
        _result("near_pidgin", "adversarial_near_miss", "COHERENT",
                "COHERENT"),
        _result("near_just_for_now", "adversarial_near_miss", "COHERENT",
                "COHERENT"),
    ]
    report = CoherenceEvalReport(fixtures=fixtures)
    assert report.c1 == (3, 3)
    assert report.c2 == (5, 5)
    assert report.c3 == (3, 3)
    assert report.passes_bar is True


def test_report_fails_when_c1_misses_one_known_bad():
    fixtures = [
        _result("bad_a", "known_bad", "INCOHERENT", "INCOHERENT",
                "mismatched-attribution", ("mismatched-attribution",)),
        _result("bad_b", "known_bad", "INCOHERENT", "INCOHERENT",
                "mismatched-attribution", ("mismatched-attribution",)),
        _result("bad_c", "known_bad", "INCOHERENT", "COHERENT"),
    ] + [
        _result(f"good_{i}", "known_good", "COHERENT", "COHERENT")
        for i in range(5)
    ] + [
        _result("near_sarcasm", "adversarial_near_miss", "INCOHERENT",
                "INCOHERENT", "mismatched-attribution",
                ("mismatched-attribution",)),
        _result("near_pidgin", "adversarial_near_miss", "COHERENT",
                "COHERENT"),
        _result("near_just_for_now", "adversarial_near_miss", "COHERENT",
                "COHERENT"),
    ]
    report = CoherenceEvalReport(fixtures=fixtures)
    assert report.c1 == (2, 3)
    assert report.passes_bar is False


def test_report_hard_fails_when_sarcasm_passes_as_coherent():
    """Even if 2/3 C3 fixtures pass, a COHERENT verdict on the
    sarcasm fixture is a hard-fail (would let an actively bad
    lesson reach live)."""
    fixtures = [
        _result(f"bad_{i}", "known_bad", "INCOHERENT", "INCOHERENT",
                "mismatched-attribution", ("mismatched-attribution",))
        for i in range(3)
    ] + [
        _result(f"good_{i}", "known_good", "COHERENT", "COHERENT")
        for i in range(5)
    ] + [
        # Sarcasm passed as COHERENT — hard-fail
        _result("near_sarcasm", "adversarial_near_miss", "INCOHERENT",
                "COHERENT"),
        _result("near_pidgin", "adversarial_near_miss", "COHERENT",
                "COHERENT"),
        _result("near_just_for_now", "adversarial_near_miss", "COHERENT",
                "COHERENT"),
    ]
    report = CoherenceEvalReport(fixtures=fixtures)
    assert report.c3 == (2, 3)  # 2 of 3 pass the per-fixture grade
    assert report.passes_bar is False  # but hard-fail guard fires


def test_report_hard_fails_when_pidgin_flagged_incoherent():
    """A false-positive INCOHERENT on a COHERENT-expected NM is a
    hard-fail — flagging legitimate non-standard-English lessons
    would create flag fatigue."""
    fixtures = [
        _result(f"bad_{i}", "known_bad", "INCOHERENT", "INCOHERENT",
                "mismatched-attribution", ("mismatched-attribution",))
        for i in range(3)
    ] + [
        _result(f"good_{i}", "known_good", "COHERENT", "COHERENT")
        for i in range(5)
    ] + [
        _result("near_sarcasm", "adversarial_near_miss", "INCOHERENT",
                "INCOHERENT", "mismatched-attribution",
                ("mismatched-attribution",)),
        # Pidgin false-positive INCOHERENT — hard-fail
        _result("near_pidgin", "adversarial_near_miss", "COHERENT",
                "INCOHERENT", "scope-overflow"),
        _result("near_just_for_now", "adversarial_near_miss", "COHERENT",
                "COHERENT"),
    ]
    report = CoherenceEvalReport(fixtures=fixtures)
    assert report.passes_bar is False
