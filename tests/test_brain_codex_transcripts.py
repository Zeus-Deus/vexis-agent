"""``CodexBrain`` rollout-JSONL transcript reader.

Exercises ``iter_session_metas``, ``iter_messages``, and
``is_brain_owned_session`` against hand-built rollout JSONLs under a
tmp sessions dir (pointed at via ``set_codex_sessions_dir_override``).

Rollout layout mirrored from the codex-brain research probe
(codex-cli 0.145.0):

    $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl
      line 0: {"type":"session_meta","payload":{"id","timestamp","cwd",...}}
      {"type":"event_msg","payload":{"type":"user_message","message":...}}
      {"type":"event_msg","payload":{"type":"agent_message","message":...}}
      (other line types skipped by the reader)

Design lock: ``.plans/codex-brain-research.md`` §2.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vexis_agent.core.brain.codex import (
    CodexBrain,
    set_codex_sessions_dir_override,
)
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore


# ──────────────────────────────────────────────────────────────────
# Fixtures + rollout builder
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def brain(workspace: Path, tmp_path: Path) -> CodexBrain:
    return CodexBrain(
        workspace=workspace,
        session=SessionStore(tmp_path / "sessions.json"),
        running_tasks=RunningTasks(),
    )


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    """Override the codex sessions root for the test's duration."""
    root = tmp_path / "codex-sessions"
    root.mkdir(parents=True, exist_ok=True)
    set_codex_sessions_dir_override(root)
    yield root
    set_codex_sessions_dir_override(None)


def _write_rollout(
    sessions_dir: Path,
    *,
    sid: str,
    cwd: str,
    timestamp: str,
    messages: list[tuple[str, str]] | None = None,
    extra_lines: list[dict] | None = None,
    raw_lines: list[str] | None = None,
) -> Path:
    """Build one rollout JSONL. ``messages`` is a list of (role, text);
    role ``user`` → ``user_message``, ``assistant`` → ``agent_message``."""
    day_dir = sessions_dir / "2026" / "07" / "23"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"rollout-{timestamp}-{sid}.jsonl"
    lines: list[str] = [
        json.dumps({
            "type": "session_meta",
            "payload": {"id": sid, "timestamp": timestamp, "cwd": cwd},
        })
    ]
    for role, text in (messages or []):
        subtype = "user_message" if role == "user" else "agent_message"
        lines.append(json.dumps({
            "type": "event_msg",
            "payload": {"type": subtype, "message": text},
        }))
    for extra in (extra_lines or []):
        lines.append(json.dumps(extra))
    for raw in (raw_lines or []):
        lines.append(raw)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ──────────────────────────────────────────────────────────────────
# iter_session_metas
# ──────────────────────────────────────────────────────────────────


def test_iter_session_metas_empty_when_dir_missing(brain: CodexBrain):
    """The autouse conftest fixture points the reader at a nonexistent
    dir — the curator scan must return empty, not crash."""
    assert list(brain.iter_session_metas()) == []


def test_iter_session_metas_filters_by_cwd(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="s-in-1", cwd=ws,
        timestamp="2026-07-23T10:00:00Z",
        messages=[("user", "hi")],
    )
    _write_rollout(
        sessions_dir, sid="s-in-2", cwd=ws,
        timestamp="2026-07-23T11:00:00Z",
        messages=[("user", "yo")],
    )
    _write_rollout(
        sessions_dir, sid="s-other", cwd="/some/other/workspace",
        timestamp="2026-07-23T12:00:00Z",
        messages=[("user", "elsewhere")],
    )
    metas = list(brain.iter_session_metas())
    assert {m.session_uuid for m in metas} == {"s-in-1", "s-in-2"}
    # jsonl_path=None signals "route reads through iter_messages".
    assert all(m.jsonl_path is None for m in metas)


def test_iter_session_metas_newest_first(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="old", cwd=ws,
        timestamp="2026-07-23T08:00:00Z", messages=[("user", "a")],
    )
    _write_rollout(
        sessions_dir, sid="new", cwd=ws,
        timestamp="2026-07-23T20:00:00Z", messages=[("user", "b")],
    )
    _write_rollout(
        sessions_dir, sid="mid", cwd=ws,
        timestamp="2026-07-23T14:00:00Z", messages=[("user", "c")],
    )
    sids = [m.session_uuid for m in brain.iter_session_metas()]
    assert sids == ["new", "mid", "old"]


def test_iter_session_metas_counts_messages(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="busy", cwd=ws,
        timestamp="2026-07-23T10:00:00Z",
        messages=[("user", "1"), ("assistant", "2"), ("user", "3")],
    )
    _write_rollout(
        sessions_dir, sid="empty", cwd=ws,
        timestamp="2026-07-23T09:00:00Z",
        messages=[],
    )
    metas = {m.session_uuid: m for m in brain.iter_session_metas()}
    assert metas["busy"].message_count_estimate == 3
    assert metas["empty"].message_count_estimate == 0


def test_iter_session_metas_carries_timestamp(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="s", cwd=ws,
        timestamp="2026-07-23T10:00:00Z", messages=[("user", "hi")],
    )
    meta = next(iter(brain.iter_session_metas()))
    assert meta.last_message_timestamp == datetime(
        2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc
    )


# ──────────────────────────────────────────────────────────────────
# iter_messages
# ──────────────────────────────────────────────────────────────────


def test_iter_messages_unknown_session_empty(
    brain: CodexBrain, sessions_dir: Path
):
    assert list(brain.iter_messages("does-not-exist")) == []


def test_iter_messages_roles_and_text(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="s", cwd=ws,
        timestamp="2026-07-23T10:00:00Z",
        messages=[("user", "hi vexis"), ("assistant", "hi back")],
    )
    msgs = list(brain.iter_messages("s"))
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert [m.text for m in msgs] == ["hi vexis", "hi back"]


def test_iter_messages_skips_non_conversational_lines(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    """``response_item`` / ``turn_context`` / other event_msg subtypes
    are not conversational turns and must not surface."""
    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="s", cwd=ws,
        timestamp="2026-07-23T10:00:00Z",
        messages=[("user", "real turn")],
        extra_lines=[
            {"type": "response_item", "payload": {"foo": "bar"}},
            {"type": "turn_context", "payload": {}},
            {"type": "event_msg", "payload": {"type": "token_count"}},
        ],
    )
    msgs = list(brain.iter_messages("s"))
    assert [m.text for m in msgs] == ["real turn"]


def test_iter_messages_skips_corrupt_lines(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="s", cwd=ws,
        timestamp="2026-07-23T10:00:00Z",
        messages=[("user", "good one")],
        raw_lines=["this is not json {{{"],
    )
    msgs = list(brain.iter_messages("s"))
    assert [m.text for m in msgs] == ["good one"]


# ──────────────────────────────────────────────────────────────────
# is_brain_owned_session
# ──────────────────────────────────────────────────────────────────


def test_is_brain_owned_session_curator_prefix(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    from vexis_agent.core.learning_review import CURATOR_REVIEW_PROMPT_PREFIX

    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="curator", cwd=ws,
        timestamp="2026-07-23T10:00:00Z",
        messages=[("user", CURATOR_REVIEW_PROMPT_PREFIX + "...")],
    )
    assert brain.is_brain_owned_session("curator") is True


def test_is_brain_owned_session_goal_judge_prefix(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    from vexis_agent.core.goal_judge import GOAL_JUDGE_PROMPT_PREFIX

    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="gj", cwd=ws,
        timestamp="2026-07-23T10:00:00Z",
        messages=[("user", GOAL_JUDGE_PROMPT_PREFIX + "...")],
    )
    assert brain.is_brain_owned_session("gj") is True


def test_is_brain_owned_session_kanban_prefix(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    from vexis_agent.core.kanban.constants import KANBAN_WORKER_PREFIX

    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="kb", cwd=ws,
        timestamp="2026-07-23T10:00:00Z",
        messages=[("user", KANBAN_WORKER_PREFIX + "...")],
    )
    assert brain.is_brain_owned_session("kb") is True


def test_is_brain_owned_session_false_for_real_user(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="real", cwd=ws,
        timestamp="2026-07-23T10:00:00Z",
        messages=[("user", "hey can you check the log file?")],
    )
    assert brain.is_brain_owned_session("real") is False


def test_is_brain_owned_session_walks_past_assistant_first(
    brain: CodexBrain, workspace: Path, sessions_dir: Path
):
    """If an assistant turn precedes the first user turn, the check
    walks past it to the first user message."""
    from vexis_agent.core.learning_review import CURATOR_REVIEW_PROMPT_PREFIX

    ws = str(workspace.resolve())
    _write_rollout(
        sessions_dir, sid="forked", cwd=ws,
        timestamp="2026-07-23T10:00:00Z",
        messages=[
            ("assistant", "context from fork"),
            ("user", CURATOR_REVIEW_PROMPT_PREFIX + "..."),
        ],
    )
    assert brain.is_brain_owned_session("forked") is True
