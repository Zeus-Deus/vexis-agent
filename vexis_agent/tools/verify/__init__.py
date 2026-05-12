"""Post-claim verification for build-and-test tasks.

``vexis-verify run <task-id> --checks checks.yaml`` re-enters the
sandbox the agent worked in, runs each check, and emits a JSON
pass/fail summary. ``vexis-bg`` uses this to gate task completion:
if any check fails, the task is bounced back to the agent's queue
with the failure summary as the next-turn observation, so the
loop can iterate.

This is the analogue of the upstream's ``compute_reward()`` hook in
``environments/tool_context.py``, adapted to Vexis's per-task
sandbox pattern.
"""

from .checks import (
    Check,
    CheckResult,
    CheckSpec,
    VerifyOutcome,
    load_checks,
    run_checks,
)

__all__ = [
    "Check",
    "CheckSpec",
    "CheckResult",
    "VerifyOutcome",
    "load_checks",
    "run_checks",
]
