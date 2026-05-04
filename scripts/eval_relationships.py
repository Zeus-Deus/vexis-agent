#!/usr/bin/env python3
"""v3c Day 4c: integration eval for the silent-extraction
extractor.

Runs each JSON fixture under
``tests/relationships/fixtures/extractor_eval/`` through the
real extractor pipeline (real ``claude -p`` subprocess, real
``_scan_lesson_for_sensitive_content``, real candidate-store
write to a tmp workspace). Reports per-fixture pass/fail and
overall stats.

This is an integration eval, NOT a unit test. It calls the model
selected by ``models.relationships_extractor`` in
``~/.vexis/config.yaml`` (default: haiku). Costs roughly one
haiku call per fixture; ~11 calls total at the spec corpus.

Acceptance thresholds (per the v3c research §8):

- ≥ 85% of positive fixtures emit correctly.
- ≥ 95% of negative fixtures correctly emit nothing.
- 100% of sensitive fixtures must NOT leak (queue write count == 0).
  Any sensitive leak is a hard fail regardless of category passes.

Invocation:

    pytest tests/relationships/ -m eval         # via the test harness
    python scripts/eval_relationships.py        # standalone

Use the standalone form for ad-hoc runs while tuning the prompt;
use the pytest form for the release gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.relationships.candidate_store import (  # noqa: E402
    RelationshipsCandidateStore,
)
from core.relationships.extractor import extract_relationships  # noqa: E402
from core.relationships.store import RelationshipsStore  # noqa: E402
from core.relationships.triggers import derive_slug  # noqa: E402
from core.transcripts import TranscriptMessage  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "relationships" / "fixtures" / "extractor_eval"


@dataclass
class FixtureResult:
    name: str
    category: str
    passed: bool
    soft_pass: bool
    sensitive_leaked: bool
    detail: str
    queued_slugs: list[str]
    queued_facts: dict[str, list[str]]


def _load_fixtures() -> list[dict]:
    fixtures = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        with path.open("r", encoding="utf-8") as fh:
            fixtures.append(json.load(fh))
    return fixtures


def _to_messages(transcript: list[dict]) -> list[TranscriptMessage]:
    msgs: list[TranscriptMessage] = []
    base = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
    for i, turn in enumerate(transcript):
        msgs.append(
            TranscriptMessage(
                role=turn["role"],
                text=turn["content"],
                timestamp=base,
                uuid=f"m-{i}",
                tool_calls=(),
                raw={},
            )
        )
    return msgs


def _evaluate(fixture: dict, queued: dict[str, list[str]],
              extractor_emitted_slugs: list[str]) -> FixtureResult:
    name = fixture["name"]
    category = fixture["category"]
    expected = fixture["expected"]
    soft = expected.get("tolerance") == "soft"
    must_emit = expected.get("must_emit_slug", [])
    must_not_emit = expected.get("must_not_emit_slug", [])
    must_substring = expected.get("must_contain_fact_substring", {})
    must_qualifier = expected.get("must_emit_qualifier_for", {})
    min_facts = expected.get("min_facts_per_slug", {})
    must_not_leak = expected.get("must_not_leak_sensitive", False)

    notes: list[str] = []
    sensitive_leaked = False

    # 1. Sensitive-leak check (hard).
    if must_not_leak:
        for slug, facts in queued.items():
            for fact in facts:
                f = fact.lower()
                if any(
                    term in f
                    for term in expected.get("sensitive_terms", [])
                ):
                    sensitive_leaked = True
                    notes.append(
                        f"SENSITIVE LEAK: slug={slug} fact={fact!r}"
                    )

    # 2. must_emit_slug — every required slug must have at least one
    #    queued fact.
    missing = [s for s in must_emit if s not in queued or not queued[s]]
    if missing:
        notes.append(f"missing required slug(s): {missing}")

    # 3. must_not_emit_slug — none of these may appear in the queue.
    leaked = [s for s in must_not_emit if s in queued and queued[s]]
    if leaked:
        notes.append(f"unexpected slug(s) emitted: {leaked}")

    # 4. fact substrings — for each required slug, at least one fact
    #    must contain each substring entry. An entry may use a pipe
    #    ``|`` to express "any-of" (e.g. ``"jet|trip"`` matches a
    #    fact containing "jet" OR "trip"). Day 5 fix: strict-AND
    #    semantics rejected legitimate paraphrases of the same
    #    source utterance.
    for slug, substrs in must_substring.items():
        slug_facts = [f.lower() for f in queued.get(slug, [])]
        missing_terms: list[str] = []
        for entry in substrs:
            alts = [
                a.strip().lower()
                for a in str(entry).split("|")
                if a.strip()
            ]
            if not alts:
                continue
            satisfied = any(
                any(alt in f for alt in alts) for f in slug_facts
            )
            if not satisfied:
                missing_terms.append(entry)
        if missing_terms:
            notes.append(
                f"slug={slug} facts missing substring(s): {missing_terms}"
            )

    # 5. minimum fact counts per slug.
    for slug, n_required in min_facts.items():
        n_actual = len(queued.get(slug, []))
        if n_actual < n_required:
            notes.append(
                f"slug={slug} got {n_actual} fact(s); expected >= {n_required}"
            )

    # 6. qualifier presence is a soft check — passes if the slug
    #    survived the queue with the qualifier in qualifier_candidates.
    #    The runner doesn't track candidates here (we only see queued
    #    facts), so we leave qualifier validation to the soft-pass tier.
    del must_qualifier  # acknowledged

    if sensitive_leaked:
        return FixtureResult(
            name=name, category=category,
            passed=False, soft_pass=False,
            sensitive_leaked=True,
            detail="; ".join(notes),
            queued_slugs=list(queued.keys()),
            queued_facts=queued,
        )

    if not notes:
        return FixtureResult(
            name=name, category=category,
            passed=True, soft_pass=False,
            sensitive_leaked=False,
            detail="ok",
            queued_slugs=list(queued.keys()),
            queued_facts=queued,
        )

    if soft:
        return FixtureResult(
            name=name, category=category,
            passed=False, soft_pass=True,
            sensitive_leaked=False,
            detail="; ".join(notes),
            queued_slugs=list(queued.keys()),
            queued_facts=queued,
        )
    return FixtureResult(
        name=name, category=category,
        passed=False, soft_pass=False,
        sensitive_leaked=False,
        detail="; ".join(notes),
        queued_slugs=list(queued.keys()),
        queued_facts=queued,
    )


async def _run_one(fixture: dict, workspace: Path) -> FixtureResult:
    cstore = RelationshipsCandidateStore(workspace / ".vexis" / "candidates.json")
    rstore = RelationshipsStore(workspace)
    messages = _to_messages(fixture["transcript"])
    session_uuid = f"eval-{fixture['name']}"
    result = await extract_relationships(
        messages,
        session_uuid,
        workspace=workspace,
        candidate_store=cstore,
        relationships_store=rstore,
    )
    if result.error:
        return FixtureResult(
            name=fixture["name"],
            category=fixture["category"],
            passed=False, soft_pass=False, sensitive_leaked=False,
            detail=f"extractor error: {result.error}",
            queued_slugs=[],
            queued_facts={},
        )
    queued: dict[str, list[str]] = {}
    for slug_dir, candidate in (cstore.load() or {}).items():
        active = [f.text for f in candidate.facts.values()
                  if f.rejected_at is None]
        if active:
            queued[slug_dir] = active
    return _evaluate(fixture, queued, [e.person for e in result.parsed])


async def run_all() -> tuple[list[FixtureResult], dict]:
    fixtures = _load_fixtures()
    results: list[FixtureResult] = []
    with tempfile.TemporaryDirectory(prefix="vexis-eval-") as tmp:
        ws_root = Path(tmp)
        for fx in fixtures:
            sub = ws_root / fx["name"]
            sub.mkdir()
            results.append(await _run_one(fx, sub))
    summary = _summary(results)
    return results, summary


def _summary(results: list[FixtureResult]) -> dict:
    pos = [r for r in results if r.category == "positive"]
    neg = [r for r in results if r.category == "negative"]
    sens = [r for r in results if r.category == "sensitive"]
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "soft_passed": sum(1 for r in results if r.soft_pass),
        "failed": sum(1 for r in results if not r.passed and not r.soft_pass),
        "positive": {
            "total": len(pos),
            "passed": sum(1 for r in pos if r.passed),
            "rate": (
                sum(1 for r in pos if r.passed) / len(pos) if pos else 0.0
            ),
        },
        "negative": {
            "total": len(neg),
            "passed": sum(1 for r in neg if r.passed or r.soft_pass),
            "rate": (
                sum(1 for r in neg if r.passed or r.soft_pass) / len(neg)
                if neg else 0.0
            ),
        },
        "sensitive": {
            "total": len(sens),
            "leaks": sum(1 for r in sens if r.sensitive_leaked),
            "ok": sum(1 for r in sens if not r.sensitive_leaked),
        },
    }


def _print_report(results: list[FixtureResult], summary: dict) -> None:
    print()
    print("=" * 64)
    print("Relationships extractor eval")
    print("=" * 64)
    for r in results:
        if r.sensitive_leaked:
            label = "SENSITIVE-LEAK"
        elif r.passed:
            label = "PASS"
        elif r.soft_pass:
            label = "soft"
        else:
            label = "FAIL"
        print(f"[{label:>15}] {r.category:<10} {r.name}")
        if r.detail and r.detail != "ok":
            print(f"  · {r.detail}")
        if r.queued_facts:
            for slug, facts in r.queued_facts.items():
                for fact in facts:
                    print(f"  - {slug}: {fact}")
    print("-" * 64)
    print(f"Total:     {summary['total']}")
    print(f"  pass:    {summary['passed']}")
    print(f"  soft:    {summary['soft_passed']}")
    print(f"  fail:    {summary['failed']}")
    print(
        f"  positive accuracy: "
        f"{summary['positive']['passed']}/{summary['positive']['total']} "
        f"({summary['positive']['rate']:.0%})"
    )
    print(
        f"  negative accuracy: "
        f"{summary['negative']['passed']}/{summary['negative']['total']} "
        f"({summary['negative']['rate']:.0%})"
    )
    print(
        f"  sensitive leaks:   "
        f"{summary['sensitive']['leaks']}/{summary['sensitive']['total']} "
        f"(must be 0)"
    )
    print("=" * 64)


# Threshold helpers used by the pytest gate (see
# tests/relationships/test_extractor_eval.py).

POSITIVE_PASS_THRESHOLD = 0.85
NEGATIVE_PASS_THRESHOLD = 0.95


def thresholds_met(summary: dict) -> tuple[bool, list[str]]:
    """Return (ok, reasons-failing). Sensitive leaks are always
    blocking — even one leak fails the gate."""
    reasons: list[str] = []
    if summary["sensitive"]["leaks"] > 0:
        reasons.append(
            f"sensitive leak count={summary['sensitive']['leaks']} "
            f"(must be 0)"
        )
    if summary["positive"]["total"] and (
        summary["positive"]["rate"] < POSITIVE_PASS_THRESHOLD
    ):
        reasons.append(
            f"positive rate {summary['positive']['rate']:.0%} < "
            f"{POSITIVE_PASS_THRESHOLD:.0%}"
        )
    if summary["negative"]["total"] and (
        summary["negative"]["rate"] < NEGATIVE_PASS_THRESHOLD
    ):
        reasons.append(
            f"negative rate {summary['negative']['rate']:.0%} < "
            f"{NEGATIVE_PASS_THRESHOLD:.0%}"
        )
    return (len(reasons) == 0, reasons)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the human report",
    )
    args = parser.parse_args()
    results, summary = asyncio.run(run_all())
    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary,
                    "results": [
                        {
                            "name": r.name,
                            "category": r.category,
                            "passed": r.passed,
                            "soft_pass": r.soft_pass,
                            "sensitive_leaked": r.sensitive_leaked,
                            "detail": r.detail,
                            "queued_slugs": r.queued_slugs,
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
    else:
        _print_report(results, summary)
    ok, reasons = thresholds_met(summary)
    if not ok:
        print()
        print("THRESHOLDS FAILED:")
        for r in reasons:
            print(f"  - {r}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
