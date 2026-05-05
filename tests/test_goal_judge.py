"""Tests for ``core/goal_judge.py`` — fail-OPEN auxiliary judge."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

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
# spawn-callable helper (mirrors tests/test_coherence_judge.py:_spawn_returning)
# ──────────────────────────────────────────────────────────────────


def _spawn_returning(stdout: str, *, returncode: int = 0, stderr: str = ""):
    """Build a fake spawn callable that returns a CompletedProcess and
    captures argv/env so tests can assert what was sent."""
    captured: dict[str, Any] = {}

    def spawn(argv, env):
        captured["argv"] = argv
        captured["env"] = env
        return subprocess.CompletedProcess(
            args=argv,
            returncode=returncode,
            stdout=stdout.encode(),
            stderr=stderr.encode(),
        )

    return spawn, captured


# ──────────────────────────────────────────────────────────────────
# _parse_judge_response — verdict shapes
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
# judge_goal — short-circuit paths (no subprocess spawned)
# ──────────────────────────────────────────────────────────────────


def test_empty_goal_returns_skipped(tmp_path: Path) -> None:
    """The pre-spawn short-circuit. The manager guards against calling
    with empty goal text in practice; this is defense-in-depth."""
    verdict, reason = judge_goal(tmp_path, "", "agent reply")
    assert verdict == "skipped"
    assert reason == "empty goal"

    verdict, reason = judge_goal(tmp_path, "   ", "agent reply")
    assert verdict == "skipped"


def test_empty_response_returns_continue_without_spawn(tmp_path: Path) -> None:
    """An empty assistant reply returns ('continue', ...) WITHOUT
    spawning the judge — there's nothing to evaluate. Distinct from
    'skipped': the brain turn that produced the empty reply still
    counted, so the manager increments the budget."""
    spawn_called = []

    def fake_spawn(argv, env):
        spawn_called.append(True)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")

    verdict, reason = judge_goal(tmp_path, "ship the thing", "", spawn=fake_spawn)
    assert verdict == "continue"
    assert "empty response" in reason
    assert not spawn_called


# ──────────────────────────────────────────────────────────────────
# judge_goal — full pipeline with mocked spawn
# ──────────────────────────────────────────────────────────────────


def test_judge_says_done(tmp_path: Path) -> None:
    spawn, _ = _spawn_returning('{"done": true, "reason": "shipped"}')
    verdict, reason = judge_goal(tmp_path, "ship", "I shipped it.", spawn=spawn)
    assert verdict == "done"
    assert reason == "shipped"


def test_judge_says_continue(tmp_path: Path) -> None:
    spawn, _ = _spawn_returning('{"done": false, "reason": "halfway"}')
    verdict, reason = judge_goal(tmp_path, "ship", "made progress", spawn=spawn)
    assert verdict == "continue"
    assert reason == "halfway"


def test_unachievable_per_prompt_maps_to_done(tmp_path: Path) -> None:
    """The system prompt explicitly tells the judge to return DONE
    when the goal is unachievable / blocked / needs user input. We
    mock a 'done' response with a block-reason to lock in that the
    parser doesn't filter it out — the prompt-shape contract is what
    enforces the policy, the parser stays neutral."""
    spawn, _ = _spawn_returning(
        '{"done": true, "reason": "needs API key from user"}'
    )
    verdict, reason = judge_goal(
        tmp_path, "ship", "I cannot proceed without an API key.", spawn=spawn
    )
    assert verdict == "done"
    assert "API key" in reason


# ──────────────────────────────────────────────────────────────────
# Fail-OPEN semantics — every error path returns 'continue'
# ──────────────────────────────────────────────────────────────────


def test_subprocess_timeout_returns_continue(tmp_path: Path) -> None:
    def spawn(argv, env):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=GOAL_JUDGE_TIMEOUT_SECONDS)

    verdict, reason = judge_goal(tmp_path, "goal", "reply", spawn=spawn)
    assert verdict == "continue"
    assert "timed out" in reason


def test_subprocess_oserror_returns_continue(tmp_path: Path) -> None:
    """``claude`` binary missing or env-issue → fail-open."""
    def spawn(argv, env):
        raise FileNotFoundError("claude not on PATH")

    verdict, reason = judge_goal(tmp_path, "goal", "reply", spawn=spawn)
    assert verdict == "continue"
    assert "spawn failed" in reason


def test_nonzero_exit_returns_continue(tmp_path: Path) -> None:
    spawn, _ = _spawn_returning(
        "", returncode=2, stderr="rate limited"
    )
    verdict, reason = judge_goal(tmp_path, "goal", "reply", spawn=spawn)
    assert verdict == "continue"
    assert "exited 2" in reason
    assert "rate limited" in reason


def test_malformed_response_returns_continue(tmp_path: Path) -> None:
    """Non-zero return is a hard fail; non-JSON in stdout maps the
    judge to 'continue' via fail-open in the parser."""
    spawn, _ = _spawn_returning("the model rambled but never emitted JSON")
    verdict, reason = judge_goal(tmp_path, "goal", "reply", spawn=spawn)
    assert verdict == "continue"
    # reason is the parser's error string, surfaced by the judge.
    assert reason


# ──────────────────────────────────────────────────────────────────
# Spawn shape — env var, argv, prompt prefix invariant
# ──────────────────────────────────────────────────────────────────


def test_spawn_sets_goal_judge_env_var(tmp_path: Path) -> None:
    """``VEXIS_GOAL_JUDGE=1`` must be present in the spawned env so
    audit logs and any downstream filter can attribute the JSONL to
    the goal subsystem."""
    spawn, captured = _spawn_returning('{"done": false, "reason": "x"}')
    judge_goal(tmp_path, "goal", "reply", spawn=spawn)
    assert captured["env"][GOAL_JUDGE_ENV_VAR] == "1"


def test_spawn_argv_includes_claude_p_and_prompt(tmp_path: Path) -> None:
    """Sanity on the argv shape: starts with claude -p, ends with the
    rendered prompt as a single positional. Mirrors
    ``test_run_coherence_judge_success_path``'s assertion shape."""
    spawn, captured = _spawn_returning('{"done": false, "reason": "x"}')
    judge_goal(tmp_path, "ship the thing", "I worked on it", spawn=spawn)
    argv = captured["argv"]
    assert argv[0] == "claude"
    assert argv[1] == "-p"
    # Last positional is the rendered prompt.
    assert "ship the thing" in argv[-1]
    assert argv[-1].startswith(GOAL_JUDGE_PROMPT_PREFIX)


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
