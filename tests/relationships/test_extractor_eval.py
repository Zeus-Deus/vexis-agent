"""v3c Day 4c: integration eval gate.

Runs the silent extractor against the fixture corpus at
``tests/relationships/fixtures/extractor_eval/`` using REAL
``claude -p`` subprocess calls (haiku-default per
``models.relationships_extractor`` in
``~/.vexis/config.yaml``).

Marked ``@pytest.mark.eval`` so it doesn't run on every CI
pass — opt in deliberately:

    pytest tests/relationships/ -m eval

The thresholds are defined in
``scripts/eval_relationships.py``:

- positive rate ≥ 85%
- negative rate ≥ 95%
- sensitive leaks == 0 (hard gate)

Any sensitive leak fails the gate regardless of the other
category rates.
"""

from __future__ import annotations

import asyncio

import pytest

from scripts.eval_relationships import run_all, thresholds_met


@pytest.mark.eval
def test_extractor_eval_meets_thresholds():
    results, summary = asyncio.run(run_all())
    ok, reasons = thresholds_met(summary)
    if not ok:
        msg = "extractor eval thresholds not met:\n  " + "\n  ".join(reasons)
        # Per-fixture context for the failure surface.
        for r in results:
            if r.sensitive_leaked or (not r.passed and not r.soft_pass):
                msg += f"\n  - {r.name} ({r.category}): {r.detail}"
        pytest.fail(msg)
