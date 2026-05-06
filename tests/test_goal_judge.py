"""Tests for ``core/goal_judge.py`` — fail-OPEN auxiliary judge.

Phase B refactor: the judge now spawns via ``Brain.spawn_aux`` instead
of building argv + calling ``subprocess.run`` directly. Tests inject
a ``BrainNull`` pre-loaded with canned ``AuxResult`` values (or
configured to raise) instead of the old ``spawn=fake_callable`` seam.

The argv-shape and env-override invariants are pinned in
``tests/test_brain_contract.py`` against ``ClaudeCodeBrain.spawn_aux``;
this file just verifies the judge's *contract* with the brain
(prompt shape, env-override key, tier name, fail-open mapping of
brain-level errors to ``"continue"`` verdict).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.brain.base import (
    AuxResult,
    BrainError,
    BrainNotInstalled,
    BrainTimeoutError,
)
from core.brain.null import BrainNull
from core.goal_judge import (
    GOAL_JUDGE_ENV_VAR,
    GOAL_JUDGE_PROMPT_PREFIX,
    GOAL_JUDGE_TIMEOUT_SECONDS,
    JUDGE_SYSTEM_PROMPT,
    _parse_judge_response,
    _render_prompt,
    judge_goal,
)


# ──────────────────────────────────────────────────────────────────
# Helpers — BrainNull pre-loaded with one AuxResult
# ──────────────────────────────────────────────────────────────────


def _brain_returning(stdout: str, *, returncode: int = 0, stderr: str = "") -> BrainNull:
    """BrainNull pre-loaded with a single AuxResult — the judge
    consumes one ``spawn_aux`` per call. Mirrors the old
    ``_spawn_returning`` helper but on the brain abstraction."""
    return BrainNull(
        aux_results=[
            AuxResult(stdout=stdout, stderr=stderr, returncode=returncode)
        ]
    )


def _judge(workspace: Path, goal: str, reply: str, brain: BrainNull):
    """Sync wrapper around the now-async ``judge_goal`` so test
    bodies stay synchronous (codebase convention — see the docstring
    on ``tests/test_brain_cancel.py``)."""
    return asyncio.run(judge_goal(workspace, goal, reply, brain))


# ──────────────────────────────────────────────────────────────────
# _parse_judge_response — verdict shapes (unchanged from pre-Phase-B)
# ──────────────────────────────────────────────────────────────────


def test_parse_clean_json_done():
    done, reason = _parse_judge_response('{"done": true, "reason": "achieved"}')
    assert done is True
    assert reason == "achieved"


def test_parse_clean_json_continue():
    done, reason = _parse_judge_response('{"done": false, "reason": "more work"}')
    assert done is False
    assert reason == "more work"


def test_parse_fence_wrapped_json():
    """```json ... ``` is the most common wrapper we see in practice."""
    raw = '```json\n{"done": true, "reason": "shipped"}\n```'
    done, reason = _parse_judge_response(raw)
    assert done is True
    assert reason == "shipped"


def test_parse_json_embedded_in_prose():
    """Models occasionally prefix reasoning before the JSON object;
    the embedded-object regex picks it out."""
    raw = (
        'Looking at the response, the goal seems satisfied. '
        'Verdict: {"done": true, "reason": "deliverable produced"}'
    )
    done, reason = _parse_judge_response(raw)
    assert done is True
    assert reason == "deliverable produced"


def test_parse_stringified_done_values():
    """'true', 'yes', 'done', '1' all map to True (case-insensitive)."""
    for s in ("true", "yes", "done", "1", "TRUE", "Yes"):
        done, _ = _parse_judge_response(f'{{"done": "{s}", "reason": "r"}}')
        assert done is True, f"expected True for {s!r}"
    for s in ("false", "no", "0", "not yet"):
        done, _ = _parse_judge_response(f'{{"done": "{s}", "reason": "r"}}')
        assert done is False, f"expected False for {s!r}"


def test_parse_malformed_json_fails_open():
    """Non-JSON returns ``(False, <error>)`` so judge_goal can map to
    verdict='continue' — the budget is the backstop."""
    done, reason = _parse_judge_response("this is not json at all")
    assert done is False
    assert reason  # non-empty error message


def test_parse_empty_response():
    done, reason = _parse_judge_response("")
    assert done is False
    assert reason == "judge returned empty response"


# ──────────────────────────────────────────────────────────────────
# judge_goal — short-circuit paths (no spawn_aux call)
# ──────────────────────────────────────────────────────────────────


def test_empty_goal_returns_skipped(tmp_path: Path) -> None:
    """The pre-spawn short-circuit. The manager guards against calling
    with empty goal text in practice; this is defense-in-depth."""
    brain = BrainNull()
    verdict, reason = _judge(tmp_path, "", "agent reply", brain)
    assert verdict == "skipped"
    assert reason == "empty goal"
    # Confirm we never called spawn_aux for empty input.
    assert brain.aux_calls() == []

    verdict, _ = _judge(tmp_path, "   ", "agent reply", brain)
    assert verdict == "skipped"
    assert brain.aux_calls() == []


def test_empty_response_returns_continue_without_spawn(tmp_path: Path) -> None:
    """An empty assistant reply returns ('continue', ...) WITHOUT
    spawning the judge — there's nothing to evaluate. Distinct from
    'skipped': the brain turn that produced the empty reply still
    counted, so the manager increments the budget."""
    brain = BrainNull()
    verdict, reason = _judge(tmp_path, "ship the thing", "", brain)
    assert verdict == "continue"
    assert "empty response" in reason
    assert brain.aux_calls() == []  # never spawned


# ──────────────────────────────────────────────────────────────────
# judge_goal — full pipeline with mocked brain
# ──────────────────────────────────────────────────────────────────


def test_judge_says_done(tmp_path: Path) -> None:
    brain = _brain_returning('{"done": true, "reason": "shipped"}')
    verdict, reason = _judge(tmp_path, "ship", "I shipped it.", brain)
    assert verdict == "done"
    assert reason == "shipped"


def test_judge_says_continue(tmp_path: Path) -> None:
    brain = _brain_returning('{"done": false, "reason": "halfway"}')
    verdict, reason = _judge(tmp_path, "ship", "made progress", brain)
    assert verdict == "continue"
    assert reason == "halfway"


def test_unachievable_per_prompt_maps_to_done(tmp_path: Path) -> None:
    """The system prompt explicitly tells the judge to return DONE
    when the goal is unachievable / blocked / needs user input. We
    mock a 'done' response with a block-reason to lock in that the
    parser doesn't filter it out — the prompt-shape contract is what
    enforces the policy, the parser stays neutral."""
    brain = _brain_returning(
        '{"done": true, "reason": "needs API key from user"}'
    )
    verdict, reason = _judge(
        tmp_path, "ship", "I cannot proceed without an API key.", brain
    )
    assert verdict == "done"
    assert "API key" in reason


# ──────────────────────────────────────────────────────────────────
# Fail-OPEN semantics — every error path returns 'continue'
# ──────────────────────────────────────────────────────────────────


def test_brain_timeout_returns_continue(tmp_path: Path) -> None:
    """``BrainTimeoutError`` from ``spawn_aux`` → verdict='continue'.
    Pre-Phase-B this caught ``subprocess.TimeoutExpired``; the
    semantic is unchanged, only the exception type differs."""
    brain = BrainNull()
    brain.next_aux_raises(BrainTimeoutError("aux timed out"))
    verdict, reason = _judge(tmp_path, "goal", "reply", brain)
    assert verdict == "continue"
    assert "timed out" in reason


def test_brain_not_installed_returns_continue(tmp_path: Path) -> None:
    """``BrainNotInstalled`` (binary missing) → verdict='continue'.
    Pre-Phase-B this caught ``OSError``/``FileNotFoundError``."""
    brain = BrainNull()
    brain.next_aux_raises(BrainNotInstalled("claude not on PATH"))
    verdict, reason = _judge(tmp_path, "goal", "reply", brain)
    assert verdict == "continue"
    assert "spawn failed" in reason


def test_brain_error_returns_continue(tmp_path: Path) -> None:
    """Any other ``BrainError`` from ``spawn_aux`` → verdict='continue'.
    Catch-all for OSError-shaped failures other than missing-binary."""
    brain = BrainNull()
    brain.next_aux_raises(BrainError("opaque subprocess failure"))
    verdict, reason = _judge(tmp_path, "goal", "reply", brain)
    assert verdict == "continue"
    assert "spawn failed" in reason


def test_nonzero_exit_returns_continue(tmp_path: Path) -> None:
    brain = _brain_returning("", returncode=2, stderr="rate limited")
    verdict, reason = _judge(tmp_path, "goal", "reply", brain)
    assert verdict == "continue"
    assert "exited 2" in reason
    assert "rate limited" in reason


def test_malformed_response_returns_continue(tmp_path: Path) -> None:
    """Non-zero return is a hard fail; non-JSON in stdout maps the
    judge to 'continue' via fail-open in the parser."""
    brain = _brain_returning("the model rambled but never emitted JSON")
    verdict, reason = _judge(tmp_path, "goal", "reply", brain)
    assert verdict == "continue"
    assert reason  # parser's error string


# ──────────────────────────────────────────────────────────────────
# spawn_aux contract — env_overrides, model_tier, prompt shape
# ──────────────────────────────────────────────────────────────────


def test_spawn_aux_passes_goal_judge_env_var(tmp_path: Path) -> None:
    """``VEXIS_GOAL_JUDGE=1`` must be in ``env_overrides`` so the
    spawned brain process can self-identify in audit logs and
    downstream filters can attribute the JSONL to the goal subsystem.
    Asserts on the brain-call record, not the OS env."""
    brain = _brain_returning('{"done": false, "reason": "x"}')
    _judge(tmp_path, "goal", "reply", brain)
    record = brain.aux_call_records()[0]
    assert record["env_overrides"] == {GOAL_JUDGE_ENV_VAR: "1"}


def test_spawn_aux_uses_goal_judge_tier(tmp_path: Path) -> None:
    """The judge passes ``model_tier=subsystem_tier("goal_judge")``,
    which defaults to ``"large"``. Brain.spawn_aux is responsible for
    resolving the tier to a native model id — pinned in
    test_model_tiers.py and test_brain_contract.py."""
    brain = _brain_returning('{"done": false, "reason": "x"}')
    _judge(tmp_path, "goal", "reply", brain)
    record = brain.aux_call_records()[0]
    # Default goal_judge tier per DEFAULT_SUBSYSTEM_TIERS — "large".
    assert record["model_tier"] == "large"


def test_spawn_aux_prompt_starts_with_filter_prefix(tmp_path: Path) -> None:
    """The brain-call records show the full prompt; verify it begins
    with ``GOAL_JUDGE_PROMPT_PREFIX`` (the curator's recursion-guard
    invariant)."""
    brain = _brain_returning('{"done": false, "reason": "x"}')
    _judge(tmp_path, "ship the thing", "I worked on it", brain)
    prompt = brain.aux_call_records()[0]["prompt"]
    assert prompt.startswith(GOAL_JUDGE_PROMPT_PREFIX)
    assert "ship the thing" in prompt


def test_spawn_aux_passes_workspace_as_cwd(tmp_path: Path) -> None:
    """``cwd`` defaults to brain.workspace, but the judge passes its
    own ``workspace`` parameter explicitly so a multi-workspace
    deployment routes correctly. Pin that the judge sets cwd
    rather than letting it default."""
    brain = _brain_returning('{"done": false, "reason": "x"}')
    _judge(tmp_path, "goal", "reply", brain)
    record = brain.aux_call_records()[0]
    assert record["cwd"] == tmp_path


def test_spawn_aux_uses_goal_judge_timeout(tmp_path: Path) -> None:
    """The judge's hard wall is ``GOAL_JUDGE_TIMEOUT_SECONDS``, not the
    brain's default 60 s. Verify it's passed through."""
    brain = _brain_returning('{"done": false, "reason": "x"}')
    _judge(tmp_path, "goal", "reply", brain)
    record = brain.aux_call_records()[0]
    assert record["timeout_seconds"] == GOAL_JUDGE_TIMEOUT_SECONDS


def test_spawn_aux_does_not_allow_tools(tmp_path: Path) -> None:
    """The judge emits text-only verdicts; if the model unexpectedly
    tries a tool the call should fail loud rather than silently use
    one. ``allow_tools=False`` is the default but pin it explicitly."""
    brain = _brain_returning('{"done": false, "reason": "x"}')
    _judge(tmp_path, "goal", "reply", brain)
    record = brain.aux_call_records()[0]
    assert record["allow_tools"] is False


# ──────────────────────────────────────────────────────────────────
# Render invariants (unchanged from pre-Phase-B; brain-agnostic)
# ──────────────────────────────────────────────────────────────────


def test_render_prompt_starts_with_filter_prefix() -> None:
    """**Load-bearing invariant** for the curator's content-prefix
    filter. The curator's ``_is_curator_owned`` check looks at the
    first user message in a JSONL and accepts ``GOAL_JUDGE_PROMPT_PREFIX``
    as a signal to exclude the JSONL from review eligibility. If the
    rendered prompt drifts away from the prefix, every goal judgment
    silently becomes a future curator review subject. This test
    catches that drift."""
    rendered = _render_prompt("ship", "reply")
    assert rendered.startswith(GOAL_JUDGE_PROMPT_PREFIX)
    # And the system prompt itself is what defines that prefix.
    assert JUDGE_SYSTEM_PROMPT.startswith(GOAL_JUDGE_PROMPT_PREFIX)


def test_render_prompt_truncates_long_inputs() -> None:
    """Goal capped at 2000 chars, response at 4000 — prevent runaway
    prompt sizes from inflating judge cost unboundedly."""
    long_goal = "g" * 5000
    long_resp = "r" * 10000
    rendered = _render_prompt(long_goal, long_resp)
    # Truncation marker present somewhere in the prompt.
    assert "[truncated]" in rendered
    # The rendered prompt is bounded — system prompt + user template
    # overhead is ~1 KB; goal max 2000, response max 4000; so total
    # < ~7500 chars even with header overhead.
    assert len(rendered) < 7500
