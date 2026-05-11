"""Tests for the kanban tool action layer (vexis_agent/tools/kanban/api.py).

This is the unified surface Telegram, the dashboard, the CLI, and
the (future) MCP server wrap. Domain errors come back via the result
dict's ``error`` + ``kind`` keys; caller misuse raises ``ToolError``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.core.kanban.constants import (
    EVENT_BLOCKED,
    EVENT_COMPLETED,
    EVENT_HEARTBEAT,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_READY,
    STATUS_TODO,
    STATUS_TRIAGE,
)
from vexis_agent.core.kanban.db import KanbanStore
from vexis_agent.tools.kanban import api


@pytest.fixture
def store(tmp_path: Path):
    s = KanbanStore(tmp_path / "k.db")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _isolate_yaml_config(monkeypatch, tmp_path):
    private_root = tmp_path / "_vexis_isolated"
    private_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.vexis_dir", lambda: private_root,
    )
    yield


# ──────────────────────────────────────────────────────────────────
# Result shape
# ──────────────────────────────────────────────────────────────────


def test_ok_result_shape(store):
    r = api.create_task(store, title="hello")
    assert r["ok"] is True
    assert isinstance(r["data"], dict)
    assert r["data"]["title"] == "hello"


def test_err_result_shape(store):
    r = api.show_task(store, "missing")
    assert r["ok"] is False
    assert "error" in r
    assert r["kind"] == "TaskNotFoundError"


# ──────────────────────────────────────────────────────────────────
# create_task
# ──────────────────────────────────────────────────────────────────


def test_create_minimum(store):
    r = api.create_task(store, title="hello")
    assert r["ok"] is True
    assert r["data"]["status"] == STATUS_TRIAGE
    assert r["data"]["priority"] == 0
    assert r["data"]["created_by"] == "user"


def test_create_with_unknown_lane_returns_err(store):
    r = api.create_task(store, title="x", lane="nope")
    assert r["ok"] is False
    assert r["kind"] == "LaneNotFoundError"


def test_create_with_default_lane_works(store):
    r = api.create_task(store, title="x", lane="research")
    assert r["ok"] is True
    assert r["data"]["lane"] == "research"


def test_create_with_invalid_status_returns_err(store):
    r = api.create_task(store, title="x", status="bogus")
    assert r["ok"] is False
    assert r["kind"] == "InvalidStatusError"


def test_create_empty_title_raises_tool_error(store):
    with pytest.raises(api.ToolError):
        api.create_task(store, title="")


def test_create_with_parents_forces_todo(store):
    p = api.create_task(store, title="parent")
    c = api.create_task(
        store, title="child", parents=[p["data"]["id"]], status=STATUS_READY,
    )
    # Status forced to TODO because parents present.
    assert c["data"]["status"] == STATUS_TODO


def test_create_with_missing_parent_returns_err(store):
    r = api.create_task(store, title="orphan", parents=["ghost"])
    assert r["ok"] is False
    assert r["kind"] == "TaskNotFoundError"


# ──────────────────────────────────────────────────────────────────
# show_task / list_board / list_lanes_info / list_events / list_runs
# ──────────────────────────────────────────────────────────────────


def test_show_returns_full_detail(store):
    t = api.create_task(store, title="x", body="body")
    tid = t["data"]["id"]
    api.comment_on_task(store, tid, body="first comment")
    r = api.show_task(store, tid)
    assert r["ok"] is True
    assert r["data"]["task"]["id"] == tid
    assert r["data"]["parents"] == []
    assert r["data"]["children"] == []
    assert len(r["data"]["comments"]) == 1
    assert isinstance(r["data"]["events"], list)
    assert isinstance(r["data"]["runs"], list)


def test_show_missing_task(store):
    r = api.show_task(store, "nope")
    assert r["ok"] is False
    assert r["kind"] == "TaskNotFoundError"


def test_show_empty_task_id_raises_tool_error(store):
    with pytest.raises(api.ToolError):
        api.show_task(store, "")


def test_list_board_includes_summary_and_tasks(store):
    api.create_task(store, title="t1", status=STATUS_TODO)
    api.create_task(store, title="t2", status=STATUS_TODO)
    r = api.list_board(store)
    assert r["ok"] is True
    assert isinstance(r["data"]["summary"], dict)
    assert r["data"]["summary"][STATUS_TODO] == 2
    assert len(r["data"]["tasks"]) == 2


def test_list_board_filters_by_lane(store):
    api.create_task(store, title="r1", lane="research")
    api.create_task(store, title="i1", lane="implementation")
    r = api.list_board(store, lane="research")
    assert all(t["lane"] == "research" for t in r["data"]["tasks"])


def test_list_board_invalid_lane(store):
    r = api.list_board(store, lane="nope")
    assert r["ok"] is False
    assert r["kind"] == "LaneNotFoundError"


def test_list_board_invalid_status(store):
    r = api.list_board(store, status="bogus")
    assert r["ok"] is False
    assert r["kind"] == "InvalidStatusError"


def test_list_lanes_info(store):
    r = api.list_lanes_info(store)
    assert r["ok"] is True
    names = {lane["name"] for lane in r["data"]["lanes"]}
    # Default lanes always present.
    for d in ("research", "implementation", "review", "ops", "triage", "default"):
        assert d in names


def test_list_events_cursor(store):
    t = api.create_task(store, title="x")
    tid = t["data"]["id"]
    r1 = api.list_events(store, since=0)
    assert r1["ok"] is True
    cursor = r1["data"]["cursor"]
    # New comment generates a new event past the cursor.
    api.comment_on_task(store, tid, body="c1")
    r2 = api.list_events(store, since=cursor)
    assert len(r2["data"]["events"]) == 1
    assert r2["data"]["events"][0]["kind"] == "commented"


def test_list_runs_missing_task(store):
    r = api.list_runs(store, "nope")
    assert r["ok"] is False
    assert r["kind"] == "TaskNotFoundError"


def test_list_runs_empty_returns_ok(store):
    t = api.create_task(store, title="x")
    r = api.list_runs(store, t["data"]["id"])
    assert r["ok"] is True
    assert r["data"]["runs"] == []


# ──────────────────────────────────────────────────────────────────
# complete_task
# ──────────────────────────────────────────────────────────────────


def test_complete_task_flips_status_and_logs(store):
    t = api.create_task(store, title="x")
    tid = t["data"]["id"]
    r = api.complete_task(store, tid, summary="all done")
    assert r["ok"] is True
    assert r["data"]["status"] == STATUS_DONE
    # COMPLETED event present.
    events = store.list_events(tid)
    assert any(e.kind == EVENT_COMPLETED for e in events)
    # Summary landed as a comment.
    comments = store.list_comments(tid)
    assert any("all done" in c.body for c in comments)


def test_complete_missing_task(store):
    r = api.complete_task(store, "nope")
    assert r["ok"] is False
    assert r["kind"] == "TaskNotFoundError"


def test_complete_finalises_active_run(store):
    """If the task has a current_run_id, complete_task finalises that run."""
    t = api.create_task(store, title="x", status=STATUS_READY)
    tid = t["data"]["id"]
    store.claim_task(tid, claim_lock="L1", ttl_seconds=60)
    run_id = store.start_run(
        tid, lane=None, claim_lock="L1", ttl_seconds=60,
    )
    api.complete_task(store, tid, summary="ok")
    run = store.get_run(run_id)
    assert run is not None
    assert run.outcome == "completed"
    assert run.status == "done"


# ──────────────────────────────────────────────────────────────────
# block_task / unblock_task
# ──────────────────────────────────────────────────────────────────


def test_block_task_flips_status_with_reason(store):
    t = api.create_task(store, title="x")
    tid = t["data"]["id"]
    r = api.block_task(store, tid, reason="waiting on API key")
    assert r["ok"] is True
    assert r["data"]["status"] == STATUS_BLOCKED
    events = store.list_events(tid)
    assert any(
        e.kind == EVENT_BLOCKED and "API key" in (e.payload or {}).get("reason", "")
        for e in events
    )


def test_block_task_requires_reason(store):
    t = api.create_task(store, title="x")
    with pytest.raises(api.ToolError):
        api.block_task(store, t["data"]["id"], reason="")


def test_unblock_task(store):
    t = api.create_task(store, title="x")
    tid = t["data"]["id"]
    api.block_task(store, tid, reason="wait")
    r = api.unblock_task(store, tid)
    assert r["ok"] is True
    assert r["data"]["status"] == STATUS_READY


def test_unblock_non_blocked_task_returns_err(store):
    t = api.create_task(store, title="x")
    r = api.unblock_task(store, t["data"]["id"])
    assert r["ok"] is False
    assert r["kind"] == "InvalidStateError"


# ──────────────────────────────────────────────────────────────────
# comment_on_task
# ──────────────────────────────────────────────────────────────────


def test_comment_on_task(store):
    t = api.create_task(store, title="x")
    tid = t["data"]["id"]
    r = api.comment_on_task(store, tid, body="comment body")
    assert r["ok"] is True
    assert r["data"]["body"] == "comment body"


def test_comment_empty_body_raises_tool_error(store):
    t = api.create_task(store, title="x")
    with pytest.raises(api.ToolError):
        api.comment_on_task(store, t["data"]["id"], body="")


def test_comment_missing_task(store):
    r = api.comment_on_task(store, "nope", body="x")
    assert r["ok"] is False
    assert r["kind"] == "TaskNotFoundError"


# ──────────────────────────────────────────────────────────────────
# heartbeat_task
# ──────────────────────────────────────────────────────────────────


def test_heartbeat_succeeds_with_correct_lock(store):
    t = api.create_task(store, title="x", status=STATUS_READY)
    tid = t["data"]["id"]
    store.claim_task(tid, claim_lock="L1", ttl_seconds=60)
    r = api.heartbeat_task(store, tid, claim_lock="L1", progress="50%")
    assert r["ok"] is True
    events = store.list_events(tid)
    assert any(e.kind == EVENT_HEARTBEAT for e in events)


def test_heartbeat_wrong_lock_returns_claim_lost(store):
    t = api.create_task(store, title="x", status=STATUS_READY)
    tid = t["data"]["id"]
    store.claim_task(tid, claim_lock="L1", ttl_seconds=60)
    r = api.heartbeat_task(store, tid, claim_lock="WRONG")
    assert r["ok"] is False
    assert r["kind"] == "ClaimLost"


def test_heartbeat_missing_task(store):
    r = api.heartbeat_task(store, "nope", claim_lock="L1")
    assert r["ok"] is False
    assert r["kind"] == "TaskNotFoundError"


# ──────────────────────────────────────────────────────────────────
# archive / assign / link / unlink
# ──────────────────────────────────────────────────────────────────


def test_archive_task(store):
    t = api.create_task(store, title="x")
    tid = t["data"]["id"]
    r = api.archive_task(store, tid)
    assert r["ok"] is True
    # Hidden from default list.
    board = api.list_board(store)
    assert tid not in {t["id"] for t in board["data"]["tasks"]}


def test_archive_missing_task(store):
    r = api.archive_task(store, "nope")
    assert r["ok"] is False
    assert r["kind"] == "TaskNotFoundError"


def test_assign_lane_updates_lane(store):
    t = api.create_task(store, title="x")
    r = api.assign_lane(store, t["data"]["id"], lane="research")
    assert r["ok"] is True
    assert r["data"]["lane"] == "research"


def test_assign_lane_unknown(store):
    t = api.create_task(store, title="x")
    r = api.assign_lane(store, t["data"]["id"], lane="nope")
    assert r["ok"] is False
    assert r["kind"] == "LaneNotFoundError"


def test_assign_lane_clear_with_none(store):
    t = api.create_task(store, title="x", lane="research")
    r = api.assign_lane(store, t["data"]["id"], lane=None)
    assert r["ok"] is True
    assert r["data"]["lane"] is None


def test_add_and_remove_link(store):
    a = api.create_task(store, title="a")
    b = api.create_task(store, title="b")
    r = api.add_link(
        store, parent_id=a["data"]["id"], child_id=b["data"]["id"],
    )
    assert r["ok"] is True
    # Verify parent linkage.
    show = api.show_task(store, b["data"]["id"])
    assert a["data"]["id"] in show["data"]["parents"]
    # Remove.
    r = api.remove_link(
        store, parent_id=a["data"]["id"], child_id=b["data"]["id"],
    )
    assert r["ok"] is True
    show = api.show_task(store, b["data"]["id"])
    assert a["data"]["id"] not in show["data"]["parents"]


def test_add_link_missing_task(store):
    a = api.create_task(store, title="a")
    r = api.add_link(store, parent_id=a["data"]["id"], child_id="ghost")
    assert r["ok"] is False
    assert r["kind"] == "TaskNotFoundError"


def test_add_link_self_returns_err(store):
    a = api.create_task(store, title="a")
    r = api.add_link(
        store, parent_id=a["data"]["id"], child_id=a["data"]["id"],
    )
    assert r["ok"] is False
    assert r["kind"] == "KanbanError"


# ──────────────────────────────────────────────────────────────────
# JSON round-trip — every action returns JSON-serialisable
# ──────────────────────────────────────────────────────────────────


def test_all_results_json_serialisable(store):
    """Smoke test — happy paths only — verifying we never return
    non-serialisable values that would crash a dashboard route."""
    import json

    t = api.create_task(store, title="x")
    tid = t["data"]["id"]
    # Each result has to round-trip through json.dumps cleanly.
    json.dumps(t)
    json.dumps(api.show_task(store, tid))
    json.dumps(api.list_board(store))
    json.dumps(api.list_lanes_info(store))
    json.dumps(api.list_events(store))
    json.dumps(api.list_runs(store, tid))
    json.dumps(api.comment_on_task(store, tid, body="c"))
    json.dumps(api.assign_lane(store, tid, lane="ops"))
    json.dumps(api.block_task(store, tid, reason="x"))
    json.dumps(api.unblock_task(store, tid))
    json.dumps(api.complete_task(store, tid, summary="done"))
    json.dumps(api.archive_task(store, tid))
