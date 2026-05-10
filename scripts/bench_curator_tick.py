"""Curator tick benchmark for Phase C Day 8.

Measures the cost of one ``LearningController._run_once()`` call
against an empty workspace — i.e. all the bookkeeping the daemon
does per tick BEFORE any actual review subprocess fires.

Why this is the right benchmark for the brain-abstraction
overhead. Per-tick cost decomposes into:

  - ReviewedStore + SpawnedStore disk reads (constant)
  - Eligibility scan (``brain.iter_session_metas`` + filter +
    sort + ``brain.is_brain_owned_session`` per candidate)
  - Per-eligible-session review + post-review scan-diff
  - Per-tick REPORT.md + run.json write

The Phase A/B/C work all touched the eligibility scan path
(claude-code-shape glob → brain-routed) and the recursion-guard
scan-diff (file glob → ``brain.iter_session_metas``). Neither
adds material work for an empty workspace; the benchmark verifies
the overhead is within the §8 risk #7 budget of <5%.

§5 risk #7 baseline (pre-Phase-A, recorded Day 5): 4.40 ms/tick.

Usage:

    conda activate vexis-agent_env
    python scripts/bench_curator_tick.py [--iterations N]

Default 200 iterations, warmup 20.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

# Add the repo root to sys.path so we can import core.* even when
# the script is invoked directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(prog="bench-curator-tick")
    parser.add_argument(
        "--iterations", type=int, default=200,
        help="Sample count after warmup (default 200)",
    )
    parser.add_argument(
        "--warmup", type=int, default=20,
        help="Warmup iterations (default 20)",
    )
    args = parser.parse_args()

    # Lazy-import after sys.path is set.
    import tempfile
    from vexis_agent.core.brain.null import BrainNull
    from vexis_agent.core.learning_curator import LearningController

    with tempfile.TemporaryDirectory(prefix="vexis-bench-") as tmp:
        # Empty workspace + null brain — the simplest possible
        # tick path. Any per-tick fixed cost shows up here without
        # the noise of real review subprocesses.
        workspace = Path(tmp) / "ws"
        workspace.mkdir()
        (workspace / "memories").mkdir()

        controller = LearningController(
            workspace=workspace,
            brain=BrainNull(),
            review_fn=lambda ws, m: ("noop", None),
        )

        # Warmup — first few ticks pay one-time costs (file
        # creation, store init) that don't apply to steady-state.
        for _ in range(args.warmup):
            controller.run_now()

        samples_ns: list[int] = []
        for _ in range(args.iterations):
            t0 = time.perf_counter_ns()
            controller.run_now()
            samples_ns.append(time.perf_counter_ns() - t0)

    samples_ms = [s / 1_000_000 for s in samples_ns]
    samples_ms.sort()
    p50 = samples_ms[len(samples_ms) // 2]
    p95 = samples_ms[int(len(samples_ms) * 0.95)]
    mean = statistics.mean(samples_ms)
    stdev = statistics.stdev(samples_ms) if len(samples_ms) > 1 else 0.0

    print(f"Curator tick benchmark — {args.iterations} samples "
          f"(warmup {args.warmup})")
    print(f"  mean   = {mean:.3f} ms")
    print(f"  stdev  = {stdev:.3f} ms")
    print(f"  p50    = {p50:.3f} ms")
    print(f"  p95    = {p95:.3f} ms")
    print(f"  min    = {min(samples_ms):.3f} ms")
    print(f"  max    = {max(samples_ms):.3f} ms")

    # Phase A baseline (Day 5 measurement): 4.40 ms/tick.
    # §8 risk #7 budget: <5% overhead → <4.62 ms/tick ceiling.
    BASELINE_MS = 4.40
    BUDGET_PCT = 5.0
    ceiling_ms = BASELINE_MS * (1 + BUDGET_PCT / 100)
    delta_pct = (mean - BASELINE_MS) / BASELINE_MS * 100
    print()
    print(f"  Phase A baseline (Day 5): {BASELINE_MS:.3f} ms")
    print(f"  Budget ceiling (<{BUDGET_PCT:.0f}%): {ceiling_ms:.3f} ms")
    print(f"  Delta vs baseline: {delta_pct:+.2f}%")
    if mean <= ceiling_ms:
        print("  STATUS: within budget ✓")
        return 0
    print(f"  STATUS: over budget by {mean - ceiling_ms:.3f} ms ✗")
    return 1


if __name__ == "__main__":
    sys.exit(main())
