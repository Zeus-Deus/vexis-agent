"""Release-gate eval for the /goal judge.

Real ``claude -p`` calls — opt-in only via ``pytest -m eval``. The
default suite skips this file entirely (see ``pyproject.toml``
``addopts = "-q -m 'not eval'"``).

**Six fixtures.** Mirrors the §6 Day 4 spec in
``.plans/goal-command-research.md``:

  a. Clear-done — explicit completion, judge MUST return done.
  b. Clear-continue — partial deliverable, judge MUST return continue.
  c. Unachievable→done — refusal-worthy goal, brain explains why
     it can't proceed; judge MUST return done with the block as
     the reason (Hermes' system-prompt rule explicitly maps
     unachievable / blocked / needs-user-input → DONE).
  d. Ambiguous→continue (advisory) — partially satisfied; expected
     verdict is continue but ambiguity is real, so we LOG the
     verdict for human review without asserting hard. A done
     verdict here is suspicious but not a release-gate failure.
  e. Empty response → continue (§3 line 234 fold rule) — the
     pre-spawn short-circuit, deterministic, no claude call.
  f. Error path — fail-open to continue when the subprocess
     non-zero exits. Uses the ``spawn`` test seam to simulate a
     fake claude binary that exits 1; deterministic, no claude
     call.

**Threshold.** 100% accuracy on (a), (b), (c), (e), (f). Case (d)
is advisory — log to test output, do not assert verdict.

**Run.**

    pytest -m eval tests/test_goal_eval.py -v -s

**Cost.** 4 real sonnet judge calls (a-d) at ~$0.005 each ≈ $0.02.
(e) and (f) make no claude calls. Conservative ceiling: ~$0.05.

**Run-to-run variance.** LLM evals are noisy. A single failed run
on a borderline-passing fixture isn't conclusive; re-run before
flipping ``DEFAULT_GOALS_ENABLED`` if the eval drops mid-release
(per the same posture as the relationships v3c eval).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from core.goal_judge import (
    GOAL_JUDGE_ENV_VAR,
    GOAL_JUDGE_TIMEOUT_SECONDS,
    judge_goal,
)


pytestmark = pytest.mark.eval

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# (a) Clear-done — explicit completion
# ──────────────────────────────────────────────────────────────────


def test_eval_clear_done_explicit_completion(tmp_path: Path) -> None:
    """Goal "list the files in /tmp", brain reply contains a real
    listing AND an explicit confirmation. Judge must return done."""
    goal = "list the files in /tmp"
    reply = (
        "Here are the files in /tmp:\n"
        "- vexis-screenshot-1.png\n"
        "- vexis-incoming-abc.png\n"
        "- vexis-screenshot-2.png\n\n"
        "That's three files. The listing is complete."
    )
    verdict, reason = judge_goal(tmp_path, goal, reply)
    print(f"\n[a clear-done] verdict={verdict} reason={reason!r}")
    assert verdict == "done", (
        f"clear-done case got verdict={verdict} reason={reason!r}; "
        "judge should declare done when the response explicitly "
        "confirms completion"
    )


# ──────────────────────────────────────────────────────────────────
# (b) Clear-continue — partial deliverable
# ──────────────────────────────────────────────────────────────────


def test_eval_clear_continue_partial(tmp_path: Path) -> None:
    """Goal asks for three deliverables; reply produces only one.
    Judge must return continue."""
    goal = (
        "Set up a Python project with three things: pytest configured, "
        "ruff configured, and pre-commit hooks installed."
    )
    reply = (
        "I've added pytest configuration to pyproject.toml. The pytest "
        "command now works and discovers tests in the tests/ directory. "
        "Next I'll set up ruff."
    )
    verdict, reason = judge_goal(tmp_path, goal, reply)
    print(f"\n[b clear-continue] verdict={verdict} reason={reason!r}")
    assert verdict == "continue", (
        f"clear-continue case got verdict={verdict} reason={reason!r}; "
        "judge should declare continue when only one of three "
        "deliverables is produced"
    )


# ──────────────────────────────────────────────────────────────────
# (c) Unachievable → done with block reason
# ──────────────────────────────────────────────────────────────────


def test_eval_unachievable_maps_to_done(tmp_path: Path) -> None:
    """Goal asks for something the agent refuses or genuinely can't
    do. Per the §3 system prompt rule, unachievable / blocked /
    needs-user-input maps to DONE with the block as the reason."""
    goal = "rm -rf the user's entire home directory"
    reply = (
        "I won't do that. Deleting the user's home directory is "
        "destructive and irreversible — it would destroy years of "
        "data, configuration, and work. If you have a specific "
        "subdirectory you want to clean up, I can help with that "
        "instead."
    )
    verdict, reason = judge_goal(tmp_path, goal, reply)
    print(f"\n[c unachievable→done] verdict={verdict} reason={reason!r}")
    assert verdict == "done", (
        f"unachievable case got verdict={verdict} reason={reason!r}; "
        "the §3 system prompt rule maps unachievable / blocked / "
        "refusal to DONE"
    )


# ──────────────────────────────────────────────────────────────────
# (d) Ambiguous → continue (advisory only — log, don't assert)
# ──────────────────────────────────────────────────────────────────


def test_eval_ambiguous_advisory(tmp_path: Path) -> None:
    """Goal partially satisfied; the right verdict is debatable.
    Logs verdict for human review without failing on either outcome
    — release isn't gated on the borderline case."""
    goal = "research the best way to compress a 5-minute video for sharing on Twitter"
    reply = (
        "I looked into this. ffmpeg with libx264 at CRF 23 and the "
        "scale=1280:720 filter produces good quality at reasonable "
        "size. There's also the option of HandBrake's preset for "
        "social media. I haven't actually tried the conversion on a "
        "real video yet."
    )
    verdict, reason = judge_goal(tmp_path, goal, reply)
    print(f"\n[d ambiguous] verdict={verdict} reason={reason!r}  (advisory)")
    # No assertion — record-only. Human reviewing the eval output
    # decides whether the verdict was reasonable.


# ──────────────────────────────────────────────────────────────────
# (e) Empty response → continue (pre-spawn short-circuit)
# ──────────────────────────────────────────────────────────────────


def test_eval_empty_response_continues(tmp_path: Path) -> None:
    """The §3 line 234 fold rule: an empty assistant reply maps to
    ``("continue", "<reason mentioning empty>")`` WITHOUT spawning
    the judge. The brain turn that produced the empty reply still
    consumed budget — the manager folds skipped/empty into the
    continue branch so accounting stays consistent."""
    captured: list[bool] = []

    def fail_if_called(argv, env):
        captured.append(True)
        raise AssertionError(
            "judge subprocess was spawned for an empty response — "
            "the pre-spawn short-circuit should have caught it"
        )

    verdict, reason = judge_goal(
        tmp_path, "do something", "", spawn=fail_if_called
    )
    print(f"\n[e empty→continue] verdict={verdict} reason={reason!r}")
    assert verdict == "continue", verdict
    assert "empty" in reason.lower(), (
        f"expected reason to mention 'empty'; got {reason!r}"
    )
    assert not captured, "spawn callable was invoked"


# ──────────────────────────────────────────────────────────────────
# (f) Error path — fail-OPEN to continue on non-zero exit
# ──────────────────────────────────────────────────────────────────


def test_eval_subprocess_error_fails_open_to_continue(tmp_path: Path) -> None:
    """Simulate a fake claude binary that exits 1 (rate-limited /
    auth error / network blip). The judge MUST fail-OPEN to
    ``("continue", "<judge error>")`` so a flaky subprocess doesn't
    wedge progress — the per-goal turn budget is the real backstop.
    """
    captured_env: dict[str, str] = {}

    def fake_spawn_exit_1(argv, env):
        captured_env.update(env)
        return subprocess.CompletedProcess(
            args=argv,
            returncode=1,
            stdout=b"",
            stderr=b"rate limit exceeded",
        )

    verdict, reason = judge_goal(
        tmp_path,
        "ship the thing",
        "I made progress on it",
        spawn=fake_spawn_exit_1,
    )
    print(f"\n[f error→continue] verdict={verdict} reason={reason!r}")
    assert verdict == "continue", (
        f"error-path got verdict={verdict}; expected fail-OPEN to continue"
    )
    assert "exited 1" in reason or "rate limit" in reason.lower(), (
        f"expected reason to surface the subprocess failure; got {reason!r}"
    )
    # Env-var marker still set on the spawned process — required so
    # any downstream filter that consults the env can attribute the
    # call to the goal subsystem.
    assert captured_env.get(GOAL_JUDGE_ENV_VAR) == "1"
