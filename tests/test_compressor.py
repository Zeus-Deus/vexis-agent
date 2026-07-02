"""Tests for the conversation compressor (Issue #11).

Coverage matches the acceptance criteria in Issue #11:

  - Trigger fires when token estimate crosses threshold.
  - Trigger does NOT fire below threshold.
  - Token estimate includes system prompt + tool schemas
    (regression on Hermes-agent's v0.13.0 bug).
  - Synthetic summary message starts with SUMMARY_PREFIX, not
    any recursion-guard prefix (curator / goal_judge / kanban).
  - The last K turns are preserved byte-for-byte.
  - Iterative compression: a second compression of an already-
    summarised session folds the previous summary into the new
    one rather than discarding it.
  - Goal-judge and learning curator can still read a compressed
    transcript via ``brain.iter_messages()`` (parity test —
    invariant).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from vexis_agent.core.brain.base import AuxResult
from vexis_agent.core.brain.claude_code import (
    ClaudeCodeBrain,
    _build_synthetic_summary_jsonl_line,
    _parse_jsonl_for_compression,
    _rewrite_jsonl_with_summary,
)
from vexis_agent.core.brain.compressor import (
    DEFAULT_PROTECT_LAST_N_TURNS,
    DEFAULT_TURN_THRESHOLD,
    SUMMARY_PREFIX,
    CompressionInputs,
    build_first_compaction_prompt,
    build_iterative_compaction_prompt,
    estimate_transcript_tokens,
    extract_summary_body,
    is_summary_message,
    plan_replacement,
    serialize_messages_for_summary,
    should_compress,
    wrap_with_summary_prefix,
)
from vexis_agent.core.goal_judge import GOAL_JUDGE_PROMPT_PREFIX
from vexis_agent.core.kanban.constants import KANBAN_WORKER_PREFIX
from vexis_agent.core.learning_review import CURATOR_REVIEW_PROMPT_PREFIX
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore
from vexis_agent.core.transcripts import (
    claude_session_jsonl_dir,
    iter_messages,
)


# ──────────────────────────────────────────────────────────────────
# SUMMARY_PREFIX recursion-guard invariant
# ──────────────────────────────────────────────────────────────────


def test_summary_prefix_does_not_overlap_recursion_guard_prefixes() -> None:
    """A compressed foreground transcript must still pass the
    curator's content-prefix filter.

    The recursion guard in ``core.transcripts.list_eligible_sessions``
    skips sessions whose first user-turn starts with any of the
    aux-prompt prefixes. SUMMARY_PREFIX must NOT start with any of
    them (and conversely none of them may start with
    SUMMARY_PREFIX) or compressed sessions become invisible to the
    learning curator.
    """
    for guard in (
        CURATOR_REVIEW_PROMPT_PREFIX,
        GOAL_JUDGE_PROMPT_PREFIX,
        KANBAN_WORKER_PREFIX,
    ):
        assert not SUMMARY_PREFIX.startswith(guard), (
            f"SUMMARY_PREFIX accidentally overlaps recursion-guard "
            f"prefix {guard!r} — compressed sessions would become "
            f"invisible to the learning curator. Re-word "
            f"SUMMARY_PREFIX to start with something distinct."
        )
        assert not guard.startswith(SUMMARY_PREFIX), (
            f"recursion-guard prefix {guard!r} accidentally starts "
            f"with SUMMARY_PREFIX — compressed sessions would "
            f"silently become curator-owned."
        )


def test_summary_prefix_distinctive_opening_token() -> None:
    """The opening bracket-prefix is what makes SUMMARY_PREFIX a
    durable signature for the iterative-summary detector."""
    assert SUMMARY_PREFIX.startswith("[SUMMARY OF PRIOR CONVERSATION")


def test_is_summary_message_and_extract_summary_body() -> None:
    wrapped = wrap_with_summary_prefix("## Active Task\nDo X")
    assert is_summary_message(wrapped)
    body = extract_summary_body(wrapped)
    assert body.startswith("## Active Task")
    assert "Do X" in body
    # Non-summary text passes through unchanged.
    assert not is_summary_message("hi there")
    assert extract_summary_body("hi there") == "hi there"


# ──────────────────────────────────────────────────────────────────
# Trigger logic
# ──────────────────────────────────────────────────────────────────


def test_trigger_fires_when_token_estimate_crosses_threshold() -> None:
    """Build a message big enough to cross an 80% × 200k cap."""
    big_text = "x" * (200_000 * 4)  # ~200k tokens (char/4) — well past 80%
    decision = should_compress(
        CompressionInputs(
            messages=[("user", big_text)],
            system_prompt="",
            tool_schemas_text="",
            context_window_tokens=200_000,
            threshold_ratio=0.80,
            threshold_turns=40,
        )
    )
    assert decision.compress is True
    assert "token estimate" in decision.reason


def test_trigger_does_not_fire_below_threshold() -> None:
    decision = should_compress(
        CompressionInputs(
            messages=[("user", "hello"), ("assistant", "hi")],
            system_prompt="small system prompt",
            tool_schemas_text="",
            context_window_tokens=200_000,
            threshold_ratio=0.80,
            threshold_turns=40,
        )
    )
    assert decision.compress is False
    assert "below thresholds" in decision.reason


def test_trigger_fires_when_turn_count_exceeds_threshold() -> None:
    """Many small turns trigger compression even when the token
    estimate is far below the cap."""
    msgs = []
    for i in range(50):
        msgs.append(("user", f"q{i}"))
        msgs.append(("assistant", f"a{i}"))
    decision = should_compress(
        CompressionInputs(
            messages=msgs,
            threshold_turns=40,
        )
    )
    assert decision.compress is True
    assert "turn count" in decision.reason


def test_token_estimate_includes_system_prompt_and_tool_schemas() -> None:
    """Hermes-v0.13.0 regression: the trigger MUST count system
    prompt + tool schemas. A near-zero conversation with a huge
    system prompt is over-cap and should fire."""
    huge_system_prompt = "S" * (200_000 * 4)  # ~200k tokens
    decision = should_compress(
        CompressionInputs(
            messages=[("user", "hi")],
            system_prompt=huge_system_prompt,
            tool_schemas_text="",
            context_window_tokens=200_000,
            threshold_ratio=0.80,
            threshold_turns=10_000,  # turn trigger off
        )
    )
    assert decision.compress is True

    # And the same with tool schemas — system prompt empty, tool
    # schemas huge.
    huge_schemas = "T" * (200_000 * 4)
    decision2 = should_compress(
        CompressionInputs(
            messages=[("user", "hi")],
            system_prompt="",
            tool_schemas_text=huge_schemas,
            context_window_tokens=200_000,
            threshold_ratio=0.80,
            threshold_turns=10_000,
        )
    )
    assert decision2.compress is True


def test_estimate_transcript_tokens_sums_all_inputs() -> None:
    """Spot-check the estimator: the answer is monotonic in each
    of system_prompt, tool_schemas, and messages."""
    base = CompressionInputs(messages=[("user", "x" * 40)])
    tokens_base = estimate_transcript_tokens(base)

    with_system = CompressionInputs(
        messages=[("user", "x" * 40)],
        system_prompt="y" * 40,
    )
    assert estimate_transcript_tokens(with_system) > tokens_base

    with_schemas = CompressionInputs(
        messages=[("user", "x" * 40)],
        tool_schemas_text="z" * 40,
    )
    assert estimate_transcript_tokens(with_schemas) > tokens_base


# ──────────────────────────────────────────────────────────────────
# Replacement plan
# ──────────────────────────────────────────────────────────────────


def test_plan_replacement_keeps_last_n_turns() -> None:
    """The protected tail is the last K turns; everything before
    them goes into messages_to_summarise."""
    msgs = [("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(30)]
    plan = plan_replacement(msgs, protect_last_n_turns=10)
    assert len(plan.protected_tail) == 10
    assert plan.protected_tail == msgs[-10:]
    assert len(plan.messages_to_summarise) == 20
    assert plan.previous_summary is None
    assert plan.protected_tail_indices == list(range(20, 30))


def test_plan_replacement_detects_iterative_summary() -> None:
    """When the first message already starts with SUMMARY_PREFIX,
    the plan extracts the previous summary body and leaves it
    out of messages_to_summarise."""
    first = wrap_with_summary_prefix("## Active Task\nrefactor auth module")
    msgs = [("user", first)]
    msgs.extend(
        ("user" if i % 2 == 0 else "assistant", f"new{i}")
        for i in range(30)
    )
    plan = plan_replacement(msgs, protect_last_n_turns=10)
    assert plan.previous_summary is not None
    assert "Active Task" in plan.previous_summary
    assert "refactor auth module" in plan.previous_summary
    # messages_to_summarise must NOT carry the previous summary —
    # it would double-count in the new prompt.
    for role, text in plan.messages_to_summarise:
        assert not text.startswith(SUMMARY_PREFIX)
    # Protected tail is still the last 10 messages of the new turns.
    assert len(plan.protected_tail) == 10


def test_plan_replacement_handles_short_transcript() -> None:
    """A short transcript (fewer messages than protect_last_n_turns)
    yields an empty messages_to_summarise — the caller will then
    bail rather than firing a useless summariser."""
    msgs = [("user", "hi"), ("assistant", "hello")]
    plan = plan_replacement(msgs, protect_last_n_turns=10)
    assert plan.messages_to_summarise == []
    assert plan.protected_tail == msgs


# ──────────────────────────────────────────────────────────────────
# Prompt templates
# ──────────────────────────────────────────────────────────────────


def test_first_compaction_prompt_contains_template_structure() -> None:
    """The first-compaction prompt embeds the full structured
    template so the summariser knows every section to fill."""
    prompt = build_first_compaction_prompt("[user]\nhi\n\n[assistant]\nhello")
    for section in (
        "## Active Task",
        "## Goal",
        "## Completed Actions",
        "## Active State",
        "## Pending User Asks",
        "## Critical Context",
    ):
        assert section in prompt
    # Defensive: NEVER include API keys instruction must be present
    # so a leaked-credentials transcript doesn't echo them out.
    assert "[REDACTED]" in prompt


def test_iterative_compaction_prompt_carries_previous_summary() -> None:
    prev = "## Active Task\nrefactor auth module\n\n## Goal\n…"
    new_block = "[user]\nNew turn\n\n[assistant]\nAck"
    prompt = build_iterative_compaction_prompt(prev, new_block)
    assert "PREVIOUS SUMMARY:" in prompt
    assert "refactor auth module" in prompt
    assert "NEW TURNS TO INCORPORATE:" in prompt
    assert "New turn" in prompt
    # The update-rather-than-from-scratch instruction must be in
    # there — without it the summariser produces a stand-alone
    # summary that drops the iterative history.
    assert "PRESERVE" in prompt
    assert "Active Task" in prompt


def test_serialize_messages_for_summary_skips_empty_text() -> None:
    serialised = serialize_messages_for_summary(
        [
            ("user", "hello"),
            ("assistant", ""),  # empty (tool-only) turn — dropped
            ("user", "again"),
        ]
    )
    assert "[user]\nhello" in serialised
    assert "[user]\nagain" in serialised
    # The empty assistant turn produces nothing.
    assert "[assistant]\n\n" not in serialised


# ──────────────────────────────────────────────────────────────────
# JSONL rewrite helpers (claude-code)
# ──────────────────────────────────────────────────────────────────


def _make_test_jsonl(path: Path, *, n_turns: int) -> list[str]:
    """Write a synthetic claude-code-shaped JSONL with ``n_turns``
    user+assistant pairs. Returns the raw lines for byte-for-byte
    comparison.
    """
    lines: list[str] = []
    # Preamble: permission-mode + initial file-history-snapshot.
    lines.append(json.dumps({"type": "permission-mode", "permissionMode": "auto"}))
    lines.append(json.dumps({"type": "file-history-snapshot", "snapshot": {}}))

    for i in range(n_turns):
        user = {
            "parentUuid": None if i == 0 else f"asst-{i-1}",
            "isSidechain": False,
            "promptId": f"u{i}",
            "type": "user",
            "message": {"role": "user", "content": f"user message {i}"},
            "uuid": f"user-{i}",
            "timestamp": f"2026-05-{(i % 27)+1:02d}T12:00:00.000Z",
        }
        lines.append(json.dumps(user))
        asst = {
            "parentUuid": f"user-{i}",
            "isSidechain": False,
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": f"assistant reply {i}"}],
            },
            "uuid": f"asst-{i}",
            "timestamp": f"2026-05-{(i % 27)+1:02d}T12:00:01.000Z",
        }
        lines.append(json.dumps(asst))
    # Epilogue: stop_hook_summary at the very end.
    lines.append(json.dumps({"type": "stop_hook_summary", "ok": True}))

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def test_parse_jsonl_for_compression_splits_segments(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    _make_test_jsonl(jsonl, n_turns=5)

    result = _parse_jsonl_for_compression(jsonl)
    assert result is not None
    preamble, records, epilogue = result
    # Two preamble lines (permission-mode + file-history-snapshot).
    assert len(preamble) == 2
    assert "permission-mode" in preamble[0]
    # 5 user + 5 assistant = 10 conversational records.
    assert len(records) == 10
    assert records[0].role == "user"
    assert records[0].text == "user message 0"
    assert records[1].role == "assistant"
    assert records[1].text == "assistant reply 0"
    # Epilogue: stop_hook_summary.
    assert len(epilogue) == 1
    assert "stop_hook_summary" in epilogue[0]


def test_synthetic_summary_jsonl_line_shape(tmp_path: Path) -> None:
    """The synthetic user-turn we insert must look like a normal
    claude-code user line so claude-code reads it on resume."""
    body = wrap_with_summary_prefix("## Active Task\nrefactor auth module")
    line = _build_synthetic_summary_jsonl_line(
        session_id="00000000-0000-0000-0000-000000000001",
        workspace=tmp_path,
        summary_text=body,
    )
    obj = json.loads(line)
    assert obj["type"] == "user"
    assert obj["isSidechain"] is False
    assert obj["sessionId"] == "00000000-0000-0000-0000-000000000001"
    assert obj["message"]["role"] == "user"
    content = obj["message"]["content"]
    assert isinstance(content, str)
    assert content.startswith(SUMMARY_PREFIX)


def test_rewrite_preserves_last_k_turns_byte_for_byte(tmp_path: Path) -> None:
    """The acceptance criterion: after compression the last K
    JSONL lines (modulo preamble/epilogue) are byte-identical to
    the original."""
    jsonl = tmp_path / "session.jsonl"
    original_lines = _make_test_jsonl(jsonl, n_turns=20)

    result = _parse_jsonl_for_compression(jsonl)
    assert result is not None
    preamble, records, epilogue = result

    # Take the last K=10 message records (= K user+assistant
    # records, which is K conversational turns starting from
    # message index 30).
    plan = plan_replacement(
        [(r.role, r.text) for r in records],
        protect_last_n_turns=10,
    )

    _rewrite_jsonl_with_summary(
        jsonl_path=jsonl,
        session_id="ses-xyz",
        preamble_lines=preamble,
        synthetic_user_text=wrap_with_summary_prefix("summary body here"),
        message_records=records,
        protected_tail_indices=plan.protected_tail_indices,
        epilogue_lines=epilogue,
        workspace=tmp_path,
    )

    rewritten = jsonl.read_text(encoding="utf-8").splitlines()
    # Preamble preserved verbatim.
    assert rewritten[:2] == original_lines[:2]
    # First conversational line is now the synthetic summary.
    summary_obj = json.loads(rewritten[2])
    assert summary_obj["type"] == "user"
    assert summary_obj["message"]["content"].startswith(SUMMARY_PREFIX)
    # Protected tail: the last 10 message records'
    # original lines, byte-for-byte.
    expected_tail_lines = [
        original_lines[records[i].line_index]
        for i in plan.protected_tail_indices
    ]
    tail_lines = rewritten[3 : 3 + len(expected_tail_lines)]
    assert tail_lines == expected_tail_lines
    # Epilogue preserved at the end.
    assert rewritten[-1] == original_lines[-1]


# ──────────────────────────────────────────────────────────────────
# End-to-end: claude-code's compress_if_needed
# ──────────────────────────────────────────────────────────────────


def _make_brain_with_session(tmp_path: Path) -> tuple[ClaudeCodeBrain, str]:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    session = SessionStore(tmp_path / "sessions.json")
    brain = ClaudeCodeBrain(
        workspace=workspace, session=session, running_tasks=RunningTasks(),
    )
    session_id = session.get()
    return brain, session_id


def _place_session_jsonl(
    brain: ClaudeCodeBrain, session_id: str, *, n_turns: int,
) -> Path:
    target_dir = claude_session_jsonl_dir(brain._workspace)
    target_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = target_dir / f"{session_id}.jsonl"
    _make_test_jsonl(jsonl_path, n_turns=n_turns)
    return jsonl_path


def test_compress_if_needed_no_op_when_below_threshold(
    tmp_path: Path,
) -> None:
    brain, session_id = _make_brain_with_session(tmp_path)
    _place_session_jsonl(brain, session_id, n_turns=5)

    # Should not even call spawn_aux because the trigger says no.
    with patch.object(brain, "spawn_aux", new=AsyncMock()) as mock_aux:
        result = asyncio.run(brain.compress_if_needed(session_id))
    assert result is False
    mock_aux.assert_not_called()


def test_compress_if_needed_rewrites_long_session(tmp_path: Path) -> None:
    brain, session_id = _make_brain_with_session(tmp_path)
    jsonl_path = _place_session_jsonl(
        brain, session_id, n_turns=DEFAULT_TURN_THRESHOLD + 5,
    )
    original = jsonl_path.read_text(encoding="utf-8")
    original_line_count = len(original.splitlines())

    fake_summary = (
        "## Active Task\nUser is asking for refactor of auth module\n\n"
        "## Completed Actions\n1. Read config.py — found bug at line 45"
    )
    with patch.object(
        brain, "spawn_aux",
        new=AsyncMock(return_value=AuxResult(
            stdout=fake_summary, stderr="", returncode=0,
        )),
    ) as mock_aux:
        result = asyncio.run(brain.compress_if_needed(session_id))

    assert result is True
    mock_aux.assert_called_once()
    # Aux call: must be text-only (defense in depth) and tagged.
    call_kwargs = mock_aux.call_args.kwargs
    assert call_kwargs["allowed_tools"] == []
    assert call_kwargs["env_overrides"] == {"VEXIS_COMPRESSOR": "1"}
    assert call_kwargs["subsystem"] == "compressor"

    rewritten = jsonl_path.read_text(encoding="utf-8").splitlines()
    # Strictly shorter than the original — the older turns are gone.
    assert len(rewritten) < original_line_count

    # Find the synthetic summary user-turn: it's the first user
    # conversational line in the rewritten transcript.
    summary_line = None
    for line in rewritten:
        obj = json.loads(line) if line.strip().startswith("{") else None
        if obj and obj.get("type") == "user" and isinstance(
            obj.get("message", {}).get("content"), str
        ):
            summary_line = obj
            break
    assert summary_line is not None
    content = summary_line["message"]["content"]
    assert content.startswith(SUMMARY_PREFIX)
    assert "refactor of auth module" in content
    # Recursion-guard invariant: the synthetic message DOES NOT
    # start with any of the recursion-guard prefixes.
    assert not content.startswith(CURATOR_REVIEW_PROMPT_PREFIX)
    assert not content.startswith(GOAL_JUDGE_PROMPT_PREFIX)
    assert not content.startswith(KANBAN_WORKER_PREFIX)


def test_compress_passes_reasoning_level_from_dict_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #50: a dict-shaped ``models.subsystems.compressor`` with a
    ``reasoning`` key flows through to the summariser spawn as
    ``reasoning_level``. Both ``subsystem_tier`` and
    ``subsystem_reasoning`` read the same on-disk config, so pointing
    ``_read_raw`` at an in-memory config exercises the real parse."""
    brain, session_id = _make_brain_with_session(tmp_path)
    _place_session_jsonl(
        brain, session_id, n_turns=DEFAULT_TURN_THRESHOLD + 5,
    )

    from vexis_agent.core import yaml_config

    monkeypatch.setattr(
        yaml_config, "_read_raw",
        lambda: {
            "models": {
                "subsystems": {
                    "compressor": {"model": "small", "reasoning": "low"},
                },
            },
        },
    )

    with patch.object(
        brain, "spawn_aux",
        new=AsyncMock(return_value=AuxResult(
            stdout="## Active Task\nx", stderr="", returncode=0,
        )),
    ) as mock_aux:
        assert asyncio.run(brain.compress_if_needed(session_id)) is True

    kwargs = mock_aux.call_args.kwargs
    assert kwargs["reasoning_level"] == "low"
    # The model half of the dict still resolves as the tier.
    assert kwargs["model_tier"] == "small"


def test_compress_reasoning_level_none_when_plain_string_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain-string compressor config keeps today's behaviour: no
    reasoning flag flows to the spawn (``None`` = defer to CLI
    default)."""
    brain, session_id = _make_brain_with_session(tmp_path)
    _place_session_jsonl(
        brain, session_id, n_turns=DEFAULT_TURN_THRESHOLD + 5,
    )

    from vexis_agent.core import yaml_config

    monkeypatch.setattr(
        yaml_config, "_read_raw",
        lambda: {"models": {"subsystems": {"compressor": "small"}}},
    )

    with patch.object(
        brain, "spawn_aux",
        new=AsyncMock(return_value=AuxResult(
            stdout="## Active Task\nx", stderr="", returncode=0,
        )),
    ) as mock_aux:
        assert asyncio.run(brain.compress_if_needed(session_id)) is True

    kwargs = mock_aux.call_args.kwargs
    assert kwargs["reasoning_level"] is None
    assert kwargs["model_tier"] == "small"


def test_compress_if_needed_iterative_folds_previous_summary(
    tmp_path: Path,
) -> None:
    """Run compress once on a long session, then bulk up the
    session past threshold again and assert the second compression
    routes through the iterative-update prompt (i.e. preserves the
    previous summary)."""
    brain, session_id = _make_brain_with_session(tmp_path)
    jsonl_path = _place_session_jsonl(
        brain, session_id, n_turns=DEFAULT_TURN_THRESHOLD + 5,
    )

    first_summary = "## Active Task\nFirst-pass goal\n\n## Goal\nrefactor X"

    with patch.object(
        brain, "spawn_aux",
        new=AsyncMock(return_value=AuxResult(
            stdout=first_summary, stderr="", returncode=0,
        )),
    ):
        assert asyncio.run(brain.compress_if_needed(session_id)) is True

    # Append fresh turns to push past threshold again.
    with jsonl_path.open("a", encoding="utf-8") as fh:
        for i in range(DEFAULT_TURN_THRESHOLD + 2):
            fh.write(json.dumps({
                "parentUuid": None,
                "isSidechain": False,
                "type": "user",
                "message": {"role": "user", "content": f"new user {i}"},
                "uuid": f"new-u-{i}",
                "timestamp": "2026-05-15T12:00:00.000Z",
            }) + "\n")
            fh.write(json.dumps({
                "parentUuid": None,
                "isSidechain": False,
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"new asst {i}"}],
                },
                "uuid": f"new-a-{i}",
                "timestamp": "2026-05-15T12:00:01.000Z",
            }) + "\n")

    second_prompt_captured: list[str] = []

    async def _capture_spawn(prompt: str, **kwargs):
        second_prompt_captured.append(prompt)
        return AuxResult(
            stdout="## Active Task\nSecond-pass updated", stderr="",
            returncode=0,
        )

    with patch.object(brain, "spawn_aux", new=_capture_spawn):
        assert asyncio.run(brain.compress_if_needed(session_id)) is True

    assert second_prompt_captured, "second compression didn't spawn the summariser"
    second_prompt = second_prompt_captured[0]
    # Iterative-update prompt MUST embed the previous summary
    # under PREVIOUS SUMMARY: header.
    assert "PREVIOUS SUMMARY:" in second_prompt
    assert "First-pass goal" in second_prompt


def test_compressed_transcript_iterable_by_iter_messages(
    tmp_path: Path,
) -> None:
    """After compression the curator/judges read transcripts via
    ``brain.iter_messages`` — assert the compressed JSONL is still
    walkable through that pipe and that the synthetic summary
    user-turn appears as a user message."""
    brain, session_id = _make_brain_with_session(tmp_path)
    jsonl_path = _place_session_jsonl(
        brain, session_id, n_turns=DEFAULT_TURN_THRESHOLD + 5,
    )

    fake_summary = "## Active Task\nReadable via iter_messages"
    with patch.object(
        brain, "spawn_aux",
        new=AsyncMock(return_value=AuxResult(
            stdout=fake_summary, stderr="", returncode=0,
        )),
    ):
        assert asyncio.run(brain.compress_if_needed(session_id)) is True

    # ``iter_messages`` over the JSONL must yield the synthetic
    # summary message as a user role.
    messages = list(iter_messages(jsonl_path))
    assert messages, "iter_messages returned nothing after compression"
    first_user = next((m for m in messages if m.role == "user"), None)
    assert first_user is not None
    assert first_user.text.startswith(SUMMARY_PREFIX)
    # The protected tail messages must still be readable too.
    assistant_msgs = [m for m in messages if m.role == "assistant"]
    assert assistant_msgs, "no protected assistant messages survived"


def test_compress_failure_returns_false_does_not_raise(
    tmp_path: Path,
) -> None:
    """Best-effort guarantee: a summariser failure must NOT raise."""
    brain, session_id = _make_brain_with_session(tmp_path)
    _place_session_jsonl(
        brain, session_id, n_turns=DEFAULT_TURN_THRESHOLD + 5,
    )

    # spawn_aux raises — compress_if_needed must catch.
    with patch.object(
        brain, "spawn_aux",
        new=AsyncMock(side_effect=RuntimeError("network down")),
    ):
        result = asyncio.run(brain.compress_if_needed(session_id))
    assert result is False


def test_compress_disabled_via_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """compression.enabled: false short-circuits everything."""
    brain, session_id = _make_brain_with_session(tmp_path)
    _place_session_jsonl(
        brain, session_id, n_turns=DEFAULT_TURN_THRESHOLD + 5,
    )

    # The brain's compress_if_needed reads via a `from yaml_config
    # import compression_enabled` at function-call time, so patching
    # the source module works.
    from vexis_agent.core import yaml_config

    monkeypatch.setattr(yaml_config, "compression_enabled", lambda: False)

    with patch.object(brain, "spawn_aux", new=AsyncMock()) as mock_aux:
        result = asyncio.run(brain.compress_if_needed(session_id))
    assert result is False
    mock_aux.assert_not_called()
