"""Tests for core/transcripts.py — JSONL reading + eligibility filter.

Each test gets an isolated ``$HOME`` so ``claude_session_jsonl_dir``
points into a tmpdir-encoded path. The fixtures stage realistic
JSONL shapes — including the last-line-is-metadata case that the
audit caught.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vexis_agent.core import transcripts as t


# --------------------------------------------------------------------
# Fixtures + helpers
# --------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh ``$HOME`` per test so the fake Claude projects dir is
    rooted under tmp_path. ``Path.home`` is monkey-patched too because
    paths.py uses both in different paths."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    workspace = tmp_path / "vexis-workspace"
    workspace.mkdir()
    return workspace


def _project_dir_for(workspace: Path) -> Path:
    pdir = t.claude_session_jsonl_dir(workspace)
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")


def _user(uuid: str, ts: str, text: str, *, sidechain: bool = False) -> dict:
    obj: dict = {
        "type": "user",
        "uuid": uuid,
        "timestamp": ts,
        "message": {"role": "user", "content": text},
    }
    if sidechain:
        obj["isSidechain"] = True
    return obj


def _asst(uuid: str, ts: str, blocks: list[dict]) -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": ts,
        "message": {"role": "assistant", "content": blocks},
    }


# --------------------------------------------------------------------
# Encoding rule
# --------------------------------------------------------------------


def test_encoded_dir_replaces_slashes_only_for_simple_path():
    p = Path("/home/zeus/vexis-workspace")
    encoded = t.claude_session_jsonl_dir(p).name
    assert encoded == "-home-zeus-vexis-workspace"


def test_encoded_dir_replaces_dots_too_for_dotfile_parent():
    """Audit catch: ``/home/zeus/.codemux`` → ``-home-zeus--codemux``
    (the dot becomes a dash, producing a double-dash). Simple
    slash-replacement gets this wrong."""
    p = Path("/home/zeus/.codemux/worktrees/foo")
    encoded = t.claude_session_jsonl_dir(p).name
    assert encoded == "-home-zeus--codemux-worktrees-foo"


# --------------------------------------------------------------------
# iter_session_metas + last_message_timestamp
# --------------------------------------------------------------------


def test_iter_session_metas_empty_when_no_dir(workspace):
    # No projects dir at all — should yield nothing, not crash.
    assert list(t.iter_session_metas(workspace)) == []


def test_iter_session_metas_picks_up_jsonls(workspace):
    pdir = _project_dir_for(workspace)
    _write_jsonl(pdir / "abc.jsonl", [
        _user("u1", "2026-05-02T10:00:00Z", "hi"),
        _asst("a1", "2026-05-02T10:00:05Z", [{"type": "text", "text": "hello"}]),
    ])
    metas = list(t.iter_session_metas(workspace))
    assert len(metas) == 1
    assert metas[0].session_uuid == "abc"
    assert metas[0].last_message_timestamp is not None
    assert metas[0].last_message_timestamp.year == 2026


def test_last_message_timestamp_skips_trailing_metadata(workspace):
    """Audit catch: real Claude Code JSONLs often end with
    ``stop_hook_summary`` / ``last-prompt`` lines that don't carry a
    timestamp. The cheap probe must scan the tail and pick the max
    timestamp found, not just look at the literal final line."""
    pdir = _project_dir_for(workspace)
    _write_jsonl(pdir / "abc.jsonl", [
        _user("u1", "2026-05-02T10:00:00Z", "hi"),
        _asst("a1", "2026-05-02T10:00:05Z", [{"type": "text", "text": "hello"}]),
        # No timestamp on these — typical of stop_hook_summary / last-prompt:
        {"type": "system", "subtype": "stop_hook_summary", "hookCount": 0},
        {"type": "last-prompt", "lastPrompt": "..."},
    ])
    metas = list(t.iter_session_metas(workspace))
    last = metas[0].last_message_timestamp
    assert last is not None
    # The latest timestamp in the file is the assistant message at :05.
    assert last.minute == 0
    assert last.second == 5


def test_last_message_timestamp_handles_empty_file(workspace):
    pdir = _project_dir_for(workspace)
    (pdir / "empty.jsonl").write_text("", encoding="utf-8")
    metas = list(t.iter_session_metas(workspace))
    assert len(metas) == 1
    assert metas[0].last_message_timestamp is None


def test_last_message_timestamp_handles_corrupt_lines(workspace):
    """Lines that fail JSON parse get skipped silently; valid
    surrounding lines still produce a timestamp."""
    pdir = _project_dir_for(workspace)
    path = pdir / "corrupt.jsonl"
    path.write_text(
        "garbage line\n"
        + json.dumps(_user("u1", "2026-05-02T10:00:00Z", "hi")) + "\n"
        + "{\"unterminated json\n",
        encoding="utf-8",
    )
    metas = list(t.iter_session_metas(workspace))
    assert metas[0].last_message_timestamp is not None


# --------------------------------------------------------------------
# iter_messages
# --------------------------------------------------------------------


def test_iter_messages_filters_non_conversational(workspace):
    pdir = _project_dir_for(workspace)
    path = pdir / "abc.jsonl"
    _write_jsonl(path, [
        {"type": "permission-mode", "permissionMode": "auto"},
        _user("u1", "2026-05-02T10:00:00Z", "hi"),
        {"type": "attachment", "attachment": {}},
        _asst("a1", "2026-05-02T10:00:05Z", [{"type": "text", "text": "hello"}]),
        {"type": "system", "subtype": "stop_hook_summary"},
        {"type": "last-prompt", "lastPrompt": "..."},
    ])
    msgs = list(t.iter_messages(path))
    assert len(msgs) == 2
    assert msgs[0].role == "user" and msgs[0].text == "hi"
    assert msgs[1].role == "assistant" and msgs[1].text == "hello"


def test_iter_messages_extracts_tool_calls(workspace):
    pdir = _project_dir_for(workspace)
    path = pdir / "abc.jsonl"
    _write_jsonl(path, [
        _asst("a1", "2026-05-02T10:00:00Z", [
            {"type": "text", "text": "running"},
            {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"cmd": "ls"}},
        ]),
    ])
    msgs = list(t.iter_messages(path))
    assert len(msgs) == 1
    assert msgs[0].text == "running"
    assert len(msgs[0].tool_calls) == 1
    assert msgs[0].tool_calls[0]["name"] == "Bash"
    assert msgs[0].tool_calls[0]["input"] == {"cmd": "ls"}


def test_iter_messages_filters_sidechain(workspace):
    pdir = _project_dir_for(workspace)
    path = pdir / "abc.jsonl"
    _write_jsonl(path, [_user("u1", "2026-05-02T10:00:00Z", "subagent", sidechain=True)])
    assert list(t.iter_messages(path)) == []


def test_iter_messages_skips_messages_without_timestamp(workspace):
    pdir = _project_dir_for(workspace)
    path = pdir / "abc.jsonl"
    obj = _user("u1", "2026-05-02T10:00:00Z", "hi")
    del obj["timestamp"]
    _write_jsonl(path, [obj])
    assert list(t.iter_messages(path)) == []


def test_session_ended_at_returns_max_timestamp(workspace):
    pdir = _project_dir_for(workspace)
    path = pdir / "abc.jsonl"
    _write_jsonl(path, [
        _user("u1", "2026-05-02T10:00:00Z", "first"),
        _asst("a1", "2026-05-02T10:30:00Z", [{"type": "text", "text": "last"}]),
    ])
    end = t.session_ended_at(path)
    assert end is not None
    assert end.minute == 30


# --------------------------------------------------------------------
# list_eligible_sessions
# --------------------------------------------------------------------


def _now(year=2026, month=5, day=2, hour=11, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, 0, tzinfo=timezone.utc)


def test_list_eligible_skips_freshly_used(workspace):
    """Session whose last message is < 25 min old is in active use."""
    pdir = _project_dir_for(workspace)
    _write_jsonl(pdir / "fresh.jsonl", [
        _user("u1", "2026-05-02T10:50:00Z", "hi"),
    ])
    eligible = t.list_eligible_sessions(
        workspace=workspace,
        reviewed={},
        idle_threshold=timedelta(minutes=25),
        now=_now(hour=11, minute=0),  # only 10 min later
    )
    assert eligible == []


def test_list_eligible_passes_idle_gate(workspace):
    pdir = _project_dir_for(workspace)
    _write_jsonl(pdir / "abandoned.jsonl", [
        _user("u1", "2026-05-02T10:00:00Z", "hi"),
    ])
    eligible = t.list_eligible_sessions(
        workspace=workspace,
        reviewed={},
        idle_threshold=timedelta(minutes=25),
        now=_now(hour=11, minute=0),  # 60 min later
    )
    assert len(eligible) == 1
    assert eligible[0].session_uuid == "abandoned"


def test_list_eligible_skips_already_reviewed(workspace):
    pdir = _project_dir_for(workspace)
    _write_jsonl(pdir / "old.jsonl", [
        _user("u1", "2026-05-02T10:00:00Z", "hi"),
    ])
    reviewed = {"old": datetime(2026, 5, 2, 10, 0, 0, tzinfo=timezone.utc)}
    eligible = t.list_eligible_sessions(
        workspace=workspace,
        reviewed=reviewed,
        idle_threshold=timedelta(minutes=25),
        now=_now(hour=12, minute=0),
    )
    assert eligible == []


def test_list_eligible_re_eligible_after_resume(workspace):
    """Session was reviewed at 10:00; user then added a message at
    11:00. After the second idle period elapses, it's eligible
    again — this is the "resumed sessions" case the trigger model
    handles natively."""
    pdir = _project_dir_for(workspace)
    _write_jsonl(pdir / "resumed.jsonl", [
        _user("u1", "2026-05-02T10:00:00Z", "first"),
        _user("u2", "2026-05-02T11:00:00Z", "second"),
    ])
    reviewed = {"resumed": datetime(2026, 5, 2, 10, 0, 0, tzinfo=timezone.utc)}
    eligible = t.list_eligible_sessions(
        workspace=workspace,
        reviewed=reviewed,
        idle_threshold=timedelta(minutes=25),
        now=_now(hour=13, minute=0),
    )
    assert len(eligible) == 1


def test_list_eligible_excludes_spawned_uuids(workspace):
    """Recursion guard: sessions spawned by the curator's own review
    forks must never be picked up as candidates."""
    pdir = _project_dir_for(workspace)
    _write_jsonl(pdir / "ours.jsonl", [
        _user("u1", "2026-05-02T10:00:00Z", "review fork"),
    ])
    eligible = t.list_eligible_sessions(
        workspace=workspace,
        reviewed={},
        idle_threshold=timedelta(minutes=25),
        now=_now(hour=11, minute=0),
        spawned_by_curator={"ours"},
    )
    assert eligible == []


def test_list_eligible_orders_oldest_first(workspace):
    """Backlog ordering: oldest-last_message first so abandoned
    sessions get reviewed before fresher ones."""
    pdir = _project_dir_for(workspace)
    _write_jsonl(pdir / "newer.jsonl", [
        _user("u1", "2026-05-02T11:00:00Z", "newer"),
    ])
    _write_jsonl(pdir / "older.jsonl", [
        _user("u1", "2026-05-02T09:00:00Z", "older"),
    ])
    eligible = t.list_eligible_sessions(
        workspace=workspace,
        reviewed={},
        idle_threshold=timedelta(minutes=25),
        now=_now(hour=13, minute=0),
    )
    assert [m.session_uuid for m in eligible] == ["older", "newer"]


def test_list_eligible_drops_sessions_with_no_timestamp(workspace):
    pdir = _project_dir_for(workspace)
    (pdir / "empty.jsonl").write_text("", encoding="utf-8")
    eligible = t.list_eligible_sessions(
        workspace=workspace,
        reviewed={},
        idle_threshold=timedelta(minutes=25),
        now=_now(),
    )
    assert eligible == []
