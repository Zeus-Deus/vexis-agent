"""Auxiliary `claude -p` judge for the /goal command.

After every brain turn in a chat with an active standing goal, the
goal hook calls :func:`judge_goal` with the goal text and the
assistant's most recent reply. The judge — sonnet by default, override
via ``models.goal_judge`` in ``~/.vexis/config.yaml`` — emits a single
JSON object ``{"done": <bool>, "reason": "<one-sentence>"}``. The
parser is robust to the same fence/embedded-prose shapes the
coherence judge tolerates (``core.coherence_judge``).

Failure posture is **fail-OPEN**: any subprocess error, timeout,
parse failure, or schema violation collapses to
``("continue", "<reason>")``. A flaky judge must never wedge progress
— the per-goal turn budget (default 20, see
``core.goal_state.DEFAULT_MAX_TURNS``) is the real backstop.

This is the inverse posture from :mod:`core.coherence_judge`, which
is fail-LOUD (judge errors surface as ``NEAR_MISS_REVIEW`` annotations
on lessons). Different surface, different recovery; we do not try
to share a single judge framework — see the §2 audit in
``.plans/goal-command-research.md`` for the rationale.

Recursion guard: every judge call spawns a ``claude -p`` subprocess
that creates its own session JSONL in the workspace projects
directory. ``GOAL_JUDGE_PROMPT_PREFIX`` below is the recognisable
opening line of every judge prompt; the curator's content-prefix
filter at :func:`core.transcripts._is_curator_owned` recognises it
and excludes goal-judge JSONLs from review eligibility. Without this,
every goal judgment would later get reviewed for lessons.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from core.brain.base import (
    Brain,
    BrainAuthRequired,
    BrainError,
    BrainNotInstalled,
    BrainTimeoutError,
)
from core.yaml_config import subsystem_reasoning, subsystem_tier

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────

# Hard wall on a single judge call. Sonnet typically returns in a
# few seconds for a ~6 KB prompt; 30 s is comfortable headroom.
# Mirrors Hermes' `goals.py:DEFAULT_JUDGE_TIMEOUT = 30.0`.
GOAL_JUDGE_TIMEOUT_SECONDS = 30

# Truncation caps from Hermes (`goals.py:48-49,304-307`). The goal is
# usually a short sentence; the assistant response can be arbitrarily
# long, so we trim it before sending to the judge.
_GOAL_MAX_CHARS = 2_000
_RESPONSE_MAX_CHARS = 4_000

# Recursion-guard env var. Set on the spawned `claude -p` subprocess
# so any code path that consults the environment knows it's a
# goal-judge spawn. Distinct from ``RECURSION_ENV_VAR`` and
# ``COHERENCE_JUDGE_ENV_VAR`` so audit logs can disambiguate the
# subsystem that produced any given child JSONL.
GOAL_JUDGE_ENV_VAR = "VEXIS_GOAL_JUDGE"


# ──────────────────────────────────────────────────────────────────
# Prompts (verbatim per `.plans/goal-command-research.md` §3)
# ──────────────────────────────────────────────────────────────────

JUDGE_SYSTEM_PROMPT = (
    "You are a strict judge evaluating whether an autonomous agent has "
    "achieved a user's stated goal. You receive the goal text and the "
    "agent's most recent response. Your only job is to decide whether "
    "the goal is fully satisfied based on that response.\n\n"
    "A goal is DONE only when:\n"
    "- The response explicitly confirms the goal was completed, OR\n"
    "- The response clearly shows the final deliverable was produced, OR\n"
    "- The response explains the goal is unachievable / blocked / needs "
    "user input (treat this as DONE with reason describing the block).\n\n"
    "Otherwise the goal is NOT done — CONTINUE.\n\n"
    "Reply ONLY with a single JSON object on one line:\n"
    '{"done": true|false, "reason": "<one-sentence rationale>"}'
)

# First-line signature of every rendered judge prompt. Used by the
# curator's content-prefix filter
# (``core/transcripts.py:_is_curator_owned``) to recognise and
# exclude goal-judge JSONLs from eligibility. The unit test
# ``test_goal_judge_prompt_invariant`` asserts that
# :func:`_render_prompt` actually starts with this prefix, so a future
# prompt edit surfaces a test failure rather than a silent filter
# regression.
GOAL_JUDGE_PROMPT_PREFIX = (
    "You are a strict judge evaluating whether an autonomous agent"
)


_JUDGE_USER_PROMPT_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Agent's most recent response:\n{response}\n\n"
    "Is the goal satisfied?"
)


# ──────────────────────────────────────────────────────────────────
# JSON extraction (lifted from core.coherence_judge — duplicate
# rather than generalise; see module docstring for rationale)
# ──────────────────────────────────────────────────────────────────


# Code-fence stripper. Matches ```json\n...\n``` or ```\n...\n```.
# Mirrors :data:`core.coherence_judge._FENCE_RE`.
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.+?)\n?```", re.DOTALL)

# JSON-object embedded in arbitrary prose. Looser than
# :data:`core.coherence_judge._VERDICT_OBJ_RE` because the goal
# judge's verdict object has different keys. Non-greedy so two
# objects in one response don't get merged.
_DONE_OBJ_RE = re.compile(r"\{[^{}]*\"done\"[^{}]*\}", re.DOTALL)


def _try_parse_object(text: str) -> dict | None:
    """Parse a JSON string into a dict, or return None on failure.

    Mirrors :func:`core.coherence_judge._try_parse_object`.
    """
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "… [truncated]"


# ──────────────────────────────────────────────────────────────────
# Verdict parser
# ──────────────────────────────────────────────────────────────────


def _parse_judge_response(raw: str) -> tuple[bool, str, bool]:
    """Parse the judge reply into ``(done, reason, parse_failed)``.

    Tolerates: clean JSON, fence-wrapped JSON, JSON embedded in
    prose, stringified booleans (``"true"``/``"yes"``/``"done"``/``"1"``
    map to ``True``; everything else to ``False``).

    Fail-open: any parse/schema failure returns
    ``(False, "<error>", True)`` — :func:`judge_goal` then maps the
    boolean to ``verdict="continue"`` and propagates the
    ``parse_failed`` flag so the manager can auto-pause after N
    consecutive parse failures (see
    :data:`core.goal_state.DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES`).

    ``parse_failed=True`` flags the cases the auto-pause guard exists
    to catch — empty body, non-JSON prose, malformed JSON. A
    successful parse returns ``parse_failed=False`` regardless of
    the verdict (a clean ``"continue"`` doesn't burn the budget the
    way a stream of garbage replies would).
    """
    if not raw:
        return False, "judge returned empty response", True

    body = raw.strip()
    fence = _FENCE_RE.search(body)
    if fence:
        body = fence.group(1).strip()

    parsed = _try_parse_object(body)
    if parsed is None:
        match = _DONE_OBJ_RE.search(body)
        if match:
            parsed = _try_parse_object(match.group(0))
    if not isinstance(parsed, dict):
        return False, f"judge reply was not JSON: {_truncate(raw, 200)!r}", True

    done_val = parsed.get("done")
    if isinstance(done_val, str):
        done = done_val.strip().lower() in ("true", "yes", "1", "done")
    else:
        done = bool(done_val)
    reason_val = parsed.get("reason")
    reason = str(reason_val).strip() if reason_val is not None else ""
    if not reason:
        reason = "no reason provided"
    return done, reason, False


# ──────────────────────────────────────────────────────────────────
# Subprocess runner
# ──────────────────────────────────────────────────────────────────


def _render_prompt(goal: str, last_response: str) -> str:
    """Compose the full prompt sent to ``claude -p``.

    System block + blank line + user block. ``claude -p`` ignores
    ``system`` role unless ``--append-system-prompt`` is passed, so we
    fold the instruction header into a labeled section at the head of
    the message — same pattern :func:`core.coherence_judge._build_judge_prompt`
    uses.

    The first character of the result must equal the first character of
    :data:`GOAL_JUDGE_PROMPT_PREFIX` so the curator's content-prefix
    filter recognises the resulting JSONL. The unit test
    ``test_goal_judge_prompt_invariant`` enforces this.
    """
    user_section = _JUDGE_USER_PROMPT_TEMPLATE.format(
        goal=_truncate(goal, _GOAL_MAX_CHARS),
        response=_truncate(last_response, _RESPONSE_MAX_CHARS),
    )
    return f"{JUDGE_SYSTEM_PROMPT}\n\n{user_section}"


async def judge_goal(
    workspace: Path,
    goal: str,
    last_response: str,
    brain: Brain,
) -> tuple[str, str, bool]:
    """Ask the auxiliary judge whether ``goal`` is satisfied by ``last_response``.

    Returns ``(verdict, reason, parse_failed)`` where verdict is one of:

      * ``"done"`` — judge confirmed the goal is satisfied (or that
        it's unachievable/blocked, which the prompt explicitly maps
        to DONE).
      * ``"continue"`` — judge said keep going, OR a fail-open
        fallback from any subprocess / parse / schema error. The
        turn budget and the consecutive-parse-failures auto-pause are
        the backstops.
      * ``"skipped"`` — pre-spawn short-circuit. Only one condition
        produces it: empty goal text. The brain turn that preceded
        this call still happened, so the manager folds skipped into
        ``continue`` for budget accounting.

    ``parse_failed`` is True only when the judge call **succeeded but
    its output was unusable** — empty stdout, non-JSON prose, or
    schema-shaped JSON missing the ``done`` key. Spawn errors,
    timeouts, and non-zero exits return ``parse_failed=False`` because
    those are transient (network / auth / rate-limit shapes) and a
    flaky transport must not trip the auto-pause meant for bad judge
    models. The empty-goal and empty-response short-circuits also
    return ``False`` for the same reason — they aren't model output.

    The manager (:meth:`core.goal_manager.GoalManager.evaluate_after_turn`)
    increments ``state.consecutive_parse_failures`` on True and resets
    to 0 on False; when the counter hits
    :data:`core.goal_state.DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES` it
    auto-pauses with a config-pointer message.

    ``workspace`` is passed as ``cwd`` to ``brain.spawn_aux`` so
    spawned JSONLs land in the workspace's transcript directory
    (where the curator's recursion guard scans). ``brain`` is the
    aux-spawn surface — Phase B routes this site through
    :meth:`Brain.spawn_aux` instead of building argv + calling
    ``subprocess.run`` directly.

    Async because :meth:`Brain.spawn_aux` is async; the post-turn
    hook in ``transports/telegram.py:_run_goal_hook`` already runs
    in the event loop, so the caller awaits directly without a
    ``to_thread`` wrapper. The fail-open semantics for timeouts
    and spawn errors are unchanged from pre-Phase-B.
    """
    if not goal.strip():
        return "skipped", "empty goal", False
    if not last_response.strip():
        # The brain turn produced no substantive reply. Almost
        # certainly not done yet — count the turn (caller does that)
        # and continue. Don't spawn the judge for nothing.
        return "continue", "empty response (nothing to evaluate)", False

    prompt = _render_prompt(goal, last_response)

    try:
        result = await brain.spawn_aux(
            prompt,
            model_tier=subsystem_tier("goal_judge"),
            reasoning_level=subsystem_reasoning("goal_judge"),
            timeout_seconds=GOAL_JUDGE_TIMEOUT_SECONDS,
            env_overrides={GOAL_JUDGE_ENV_VAR: "1"},
            cwd=workspace,
            subsystem="goal_judge",
        )
    except BrainTimeoutError:
        # Transient transport failure — does NOT count as a parse
        # failure. A flaky network shouldn't trip the auto-pause meant
        # for bad judge models.
        return (
            "continue",
            f"judge timed out after {GOAL_JUDGE_TIMEOUT_SECONDS}s",
            False,
        )
    except (BrainNotInstalled, BrainAuthRequired) as exc:
        return "continue", f"judge spawn failed: {exc}", False
    except BrainError as exc:
        return "continue", f"judge spawn failed: {exc}", False

    if result.returncode != 0:
        # Non-zero exit (rate limit, auth blip, etc.) is also transient.
        body = (result.stderr or result.stdout).strip()
        return (
            "continue",
            f"judge exited {result.returncode}: {body[:300]}",
            False,
        )

    done, reason, parse_failed = _parse_judge_response(result.stdout)
    verdict = "done" if done else "continue"
    log.info(
        "goal judge: verdict=%s reason=%s parse_failed=%s",
        verdict,
        _truncate(reason, 120),
        parse_failed,
    )
    return verdict, reason, parse_failed


__all__ = [
    "GOAL_JUDGE_ENV_VAR",
    "GOAL_JUDGE_PROMPT_PREFIX",
    "GOAL_JUDGE_TIMEOUT_SECONDS",
    "JUDGE_SYSTEM_PROMPT",
    "judge_goal",
    # Internal helpers exported for direct unit testing.
    "_DONE_OBJ_RE",
    "_FENCE_RE",
    "_parse_judge_response",
    "_render_prompt",
    "_truncate",
    "_try_parse_object",
]
