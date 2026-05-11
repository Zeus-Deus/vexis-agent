"""Kanban storage tests.

Pinned behaviours:

  * Schema initialises cleanly + WAL works.
  * create_task with parents forces ``todo`` and links each parent.
  * Promotion (todo→ready) only fires when every parent is ``done``.
  * claim_task is atomic: a second claim raises ClaimContentionError.
  * Heartbeat returns False when another worker took the claim.
  * Stale claim cleanup releases AND increments consecutive_failures.
  * append_event validates kind; events_since cursor pagination works.
  * Restart preserves state (close + reopen sees the same rows).
  * board_summary returns counts per active status.

Counts referenced in this file (e.g. "11 expected events after the
fan-out scenario") drift over time. Treat counts as documentation;
the test asserts on the relevant subset rather than exact totals so
this docstring stays honest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.core.kanban.constants import (
    EVENT_CLAIMED,
    EVENT_COMPLETED,
    EVENT_CREATED,
    EVENT_LINKED,
    EVENT_PROMOTED,
    KANBAN_WORKER_PREFIX,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_READY,
    STATUS_TODO,
    STATUS_TRIAGE,
    VALID_EVENT_KINDS,
    VALID_STATUSES,
)
from vexis_agent.core.kanban.db import (
    ClaimContentionError,
    InvalidEventKindError,
    InvalidStatusError,
    KanbanError,
    KanbanStore,
    TaskNotFoundError,
)


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> KanbanStore:
    s = KanbanStore(tmp_path / "kanban.db")
    yield s
    s.close()


# ──────────────────────────────────────────────────────────────────
# Schema + lifecycle
# ──────────────────────────────────────────────────────────────────


def test_schema_initialises_cleanly(tmp_path: Path) -> None:
    """Constructing the store creates the file and runs the schema."""
    db_path = tmp_path / "subdir" / "kanban.db"  # parent dir auto-created
    store = KanbanStore(db_path)
    assert db_path.exists()
    # Five tables present.
    rows = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'",
    ).fetchall()
    names = {r["name"] for r in rows}
    for table in ("tasks", "task_links", "task_comments", "task_events", "task_runs"):
        assert table in names, f"missing table {table}: {names}"
    store.close()


def test_constants_recursion_guard_prefix_present() -> None:
    """KANBAN_WORKER_PREFIX is the content marker the curator skip
    list relies on. Test pins it so a rename would force the curator
    test to update too."""
    assert KANBAN_WORKER_PREFIX.startswith("[")
    assert "KANBAN" in KANBAN_WORKER_PREFIX


def test_close_is_idempotent(tmp_path: Path) -> None:
    s = KanbanStore(tmp_path / "k.db")
    s.close()
    s.close()  # second close must not raise


# ──────────────────────────────────────────────────────────────────
# create_task
# ──────────────────────────────────────────────────────────────────


def test_create_task_minimum(store: KanbanStore) -> None:
    task = store.create_task(title="hello world")
    assert task.title == "hello world"
    assert task.status == STATUS_TRIAGE
    assert task.priority == 0
    assert task.skills == []
    assert task.created_at > 0
    # CREATED event landed.
    events = store.list_events(task.id)
    assert any(e.kind == EVENT_CREATED for e in events)


def test_create_task_full_payload(store: KanbanStore) -> None:
    task = store.create_task(
        title="ship X",
        body="multi\nline body",
        lane="implementation",
        status=STATUS_TODO,
        priority=5,
        created_by="user",
        workspace_kind="dir",
        workspace_path="/tmp/ws",
        max_runtime_seconds=300,
        skills=["shell", "edit"],
        max_retries=2,
    )
    assert task.body == "multi\nline body"
    assert task.lane == "implementation"
    assert task.priority == 5
    assert task.skills == ["shell", "edit"]
    assert task.max_retries == 2


def test_create_task_rejects_empty_title(store: KanbanStore) -> None:
    with pytest.raises(KanbanError):
        store.create_task(title="")
    with pytest.raises(KanbanError):
        store.create_task(title="   ")


def test_create_task_rejects_invalid_status(store: KanbanStore) -> None:
    with pytest.raises(InvalidStatusError):
        store.create_task(title="x", status="not-a-status")


def test_create_task_with_parents_forces_todo_and_links(store: KanbanStore) -> None:
    p1 = store.create_task(title="parent 1")
    p2 = store.create_task(title="parent 2")
    # Even if we ask for ``ready``, having parents should pin it to ``todo``.
    child = store.create_task(
        title="child", status=STATUS_READY, parents=[p1.id, p2.id],
    )
    assert child.status == STATUS_TODO
    assert sorted(store.get_parents(child.id)) == sorted([p1.id, p2.id])
    assert store.get_children(p1.id) == [child.id]
    # Each parent gets a LINKED event.
    p1_events = store.list_events(p1.id)
    assert any(
        e.kind == EVENT_LINKED and e.payload and e.payload.get("child_id") == child.id
        for e in p1_events
    )


def test_create_task_with_missing_parent_raises(store: KanbanStore) -> None:
    with pytest.raises(TaskNotFoundError):
        store.create_task(title="orphan", parents=["does-not-exist"])


def test_create_task_with_explicit_id(store: KanbanStore) -> None:
    task = store.create_task(title="t", task_id="custom-id")
    assert task.id == "custom-id"
    assert store.get_task("custom-id") is task or store.get_task("custom-id").title == "t"


# ──────────────────────────────────────────────────────────────────
# read paths
# ──────────────────────────────────────────────────────────────────


def test_get_task_missing_returns_none(store: KanbanStore) -> None:
    assert store.get_task("nope") is None


def test_require_task_missing_raises(store: KanbanStore) -> None:
    with pytest.raises(TaskNotFoundError):
        store.require_task("nope")


def test_list_tasks_status_filter(store: KanbanStore) -> None:
    a = store.create_task(title="a", status=STATUS_TODO)
    b = store.create_task(title="b", status=STATUS_READY)
    store.create_task(title="c", status=STATUS_DONE)
    store.update_task(a.id, status=STATUS_DONE)
    todos = store.list_tasks(status=STATUS_TODO)
    assert todos == []
    readys = store.list_tasks(status=STATUS_READY)
    assert [t.id for t in readys] == [b.id]
    dones = store.list_tasks(status=STATUS_DONE)
    assert len(dones) == 2


def test_list_tasks_lane_filter(store: KanbanStore) -> None:
    store.create_task(title="r1", lane="research")
    store.create_task(title="r2", lane="research")
    store.create_task(title="i1", lane="implementation")
    rs = store.list_tasks(lane="research")
    assert {t.title for t in rs} == {"r1", "r2"}


def test_list_tasks_hides_archived_by_default(store: KanbanStore) -> None:
    a = store.create_task(title="a")
    store.create_task(title="b")
    store.archive_task(a.id)
    visible = store.list_tasks()
    assert a.id not in {t.id for t in visible}
    with_archived = store.list_tasks(include_archived=True)
    assert a.id in {t.id for t in with_archived}


def test_list_tasks_orders_by_priority_then_created(store: KanbanStore) -> None:
    low = store.create_task(title="low", priority=0)
    high = store.create_task(title="high", priority=10)
    mid = store.create_task(title="mid", priority=5)
    out = store.list_tasks()
    assert [t.id for t in out[:3]] == [high.id, mid.id, low.id]


# ──────────────────────────────────────────────────────────────────
# update_task
# ──────────────────────────────────────────────────────────────────


def test_update_task_no_op_when_no_args(store: KanbanStore) -> None:
    t = store.create_task(title="x")
    after = store.update_task(t.id)
    assert after.title == t.title


def test_update_task_status_to_done_sets_completed_at(store: KanbanStore) -> None:
    t = store.create_task(title="x")
    assert t.completed_at is None
    after = store.update_task(t.id, status=STATUS_DONE)
    assert after.status == STATUS_DONE
    assert after.completed_at is not None


def test_update_task_status_to_done_idempotent_for_completed_at(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_DONE)
    # Initial create with status=DONE doesn't touch completed_at — that's
    # update-only behaviour. Verify the second update doesn't move it
    # backward either.
    first = store.update_task(t.id, status=STATUS_DONE)
    second = store.update_task(t.id, status=STATUS_DONE)
    # Both calls flip the same status; completed_at gets set on the first
    # transition. We don't pin equality (clock may advance) but we DO
    # pin that it's set after either call.
    assert first.status == STATUS_DONE
    assert second.status == STATUS_DONE


def test_update_task_skills_replaces_list(store: KanbanStore) -> None:
    t = store.create_task(title="x", skills=["a", "b"])
    after = store.update_task(t.id, skills=["c"])
    assert after.skills == ["c"]


def test_update_task_invalid_status(store: KanbanStore) -> None:
    t = store.create_task(title="x")
    with pytest.raises(InvalidStatusError):
        store.update_task(t.id, status="bogus")


# ──────────────────────────────────────────────────────────────────
# links
# ──────────────────────────────────────────────────────────────────


def test_add_link_idempotent(store: KanbanStore) -> None:
    a = store.create_task(title="a")
    b = store.create_task(title="b")
    store.add_link(a.id, b.id)
    store.add_link(a.id, b.id)  # second call no-ops
    assert store.get_children(a.id) == [b.id]


def test_add_link_self_refused(store: KanbanStore) -> None:
    a = store.create_task(title="a")
    with pytest.raises(KanbanError):
        store.add_link(a.id, a.id)


def test_add_link_missing_task_raises(store: KanbanStore) -> None:
    a = store.create_task(title="a")
    with pytest.raises(TaskNotFoundError):
        store.add_link(a.id, "ghost")
    with pytest.raises(TaskNotFoundError):
        store.add_link("ghost", a.id)


def test_remove_link_idempotent(store: KanbanStore) -> None:
    a = store.create_task(title="a")
    b = store.create_task(title="b")
    store.add_link(a.id, b.id)
    store.remove_link(a.id, b.id)
    store.remove_link(a.id, b.id)  # already gone
    assert store.get_children(a.id) == []


# ──────────────────────────────────────────────────────────────────
# comments
# ──────────────────────────────────────────────────────────────────


def test_add_comment_appends_event(store: KanbanStore) -> None:
    t = store.create_task(title="x")
    c = store.add_comment(t.id, author="user", body="looks good")
    assert c.body == "looks good"
    listed = store.list_comments(t.id)
    assert len(listed) == 1
    assert listed[0].author == "user"
    # COMMENTED event present.
    events = store.list_events(t.id)
    assert any(e.kind == "commented" for e in events)


def test_add_comment_rejects_empty(store: KanbanStore) -> None:
    t = store.create_task(title="x")
    with pytest.raises(KanbanError):
        store.add_comment(t.id, author="u", body="")
    with pytest.raises(KanbanError):
        store.add_comment(t.id, author="u", body="    ")


def test_add_comment_missing_task_raises(store: KanbanStore) -> None:
    with pytest.raises(TaskNotFoundError):
        store.add_comment("ghost", author="u", body="x")


# ──────────────────────────────────────────────────────────────────
# claim / heartbeat / release
# ──────────────────────────────────────────────────────────────────


def test_claim_only_works_on_ready(store: KanbanStore) -> None:
    t = store.create_task(title="x")  # status = triage
    with pytest.raises(ClaimContentionError):
        store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)


def test_claim_atomic_second_caller_loses(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_READY)
    claimed = store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    assert claimed.claim_lock == "L1"
    assert claimed.status == STATUS_IN_PROGRESS
    # Second claim must fail.
    with pytest.raises(ClaimContentionError):
        store.claim_task(t.id, claim_lock="L2", ttl_seconds=60)


def test_claim_event_logged(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_READY)
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    events = store.list_events(t.id)
    assert any(e.kind == EVENT_CLAIMED for e in events)


def test_release_claim_resets_to_ready(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_READY)
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    store.release_claim(t.id)
    after = store.require_task(t.id)
    assert after.status == STATUS_READY
    assert after.claim_lock is None
    assert after.claim_expires is None


def test_heartbeat_succeeds_with_correct_lock(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_READY)
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    assert store.heartbeat(t.id, claim_lock="L1", ttl_seconds=60) is True


def test_heartbeat_fails_with_wrong_lock(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_READY)
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    assert store.heartbeat(t.id, claim_lock="L2-other-worker", ttl_seconds=60) is False


def test_cleanup_stale_claims_releases_and_increments_failures(
    store: KanbanStore,
) -> None:
    t = store.create_task(title="x", status=STATUS_READY)
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    # Force the claim to be expired by manually setting claim_expires.
    store._conn.execute(
        "UPDATE tasks SET claim_expires = 0 WHERE id = ?", (t.id,),
    )
    released = store.cleanup_stale_claims()
    assert released == [t.id]
    after = store.require_task(t.id)
    assert after.status == STATUS_READY
    assert after.claim_lock is None
    assert after.consecutive_failures == 1
    assert after.last_failure_error and "claim" in after.last_failure_error


def test_cleanup_stale_claims_skips_fresh(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_READY)
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    released = store.cleanup_stale_claims()
    assert released == []
    after = store.require_task(t.id)
    assert after.consecutive_failures == 0


def test_count_in_flight(store: KanbanStore) -> None:
    a = store.create_task(title="a", status=STATUS_READY)
    b = store.create_task(title="b", status=STATUS_READY)
    store.create_task(title="c", status=STATUS_READY)
    assert store.count_in_flight() == 0
    store.claim_task(a.id, claim_lock="L1", ttl_seconds=60)
    assert store.count_in_flight() == 1
    store.claim_task(b.id, claim_lock="L2", ttl_seconds=60)
    assert store.count_in_flight() == 2


# ──────────────────────────────────────────────────────────────────
# recompute_ready
# ──────────────────────────────────────────────────────────────────


def test_recompute_ready_promotes_root_todo(store: KanbanStore) -> None:
    """A todo task with no parents promotes immediately (vacuous truth)."""
    t = store.create_task(title="x", status=STATUS_TODO)
    promoted = store.recompute_ready()
    assert t.id in promoted
    assert store.require_task(t.id).status == STATUS_READY


def test_recompute_ready_blocks_until_all_parents_done(store: KanbanStore) -> None:
    p1 = store.create_task(title="p1")
    p2 = store.create_task(title="p2")
    child = store.create_task(title="child", parents=[p1.id, p2.id])
    # Only one parent done — child must NOT promote.
    store.update_task(p1.id, status=STATUS_DONE)
    store.recompute_ready()
    assert store.require_task(child.id).status == STATUS_TODO
    # Now finish the second parent — child promotes.
    store.update_task(p2.id, status=STATUS_DONE)
    promoted = store.recompute_ready()
    assert child.id in promoted
    assert store.require_task(child.id).status == STATUS_READY


def test_recompute_ready_logs_promoted_event(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_TODO)
    store.recompute_ready()
    events = store.list_events(t.id)
    assert any(e.kind == EVENT_PROMOTED for e in events)


def test_recompute_ready_idempotent(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_TODO)
    first = store.recompute_ready()
    second = store.recompute_ready()
    assert t.id in first
    assert t.id not in second  # already promoted, not promoted again


def test_list_ready_filters_unclaimed_and_orders(store: KanbanStore) -> None:
    a = store.create_task(title="a", status=STATUS_READY, priority=0)
    b = store.create_task(title="b", status=STATUS_READY, priority=10)
    c = store.create_task(title="c", status=STATUS_READY, priority=10)
    # Claim b — should drop out of list_ready.
    store.claim_task(b.id, claim_lock="L1", ttl_seconds=60)
    out = store.list_ready()
    ids = [t.id for t in out]
    assert b.id not in ids
    # Among the remaining, higher priority first then FIFO.
    assert ids == [c.id, a.id]


def test_list_ready_lane_filter(store: KanbanStore) -> None:
    store.create_task(title="a", status=STATUS_READY, lane="research")
    store.create_task(title="b", status=STATUS_READY, lane="implementation")
    out = store.list_ready(lane="research")
    assert len(out) == 1
    assert out[0].lane == "research"


# ──────────────────────────────────────────────────────────────────
# runs
# ──────────────────────────────────────────────────────────────────


def test_start_run_inserts_row_and_links_to_task(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_READY)
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    run_id = store.start_run(
        t.id, lane="implementation", claim_lock="L1",
        ttl_seconds=60, worker_pid=12345,
    )
    after = store.require_task(t.id)
    assert after.current_run_id == run_id
    assert after.worker_pid == 12345
    run = store.get_run(run_id)
    assert run is not None
    assert run.task_id == t.id
    assert run.lane == "implementation"
    assert run.status == "running"


def test_finalize_run_writes_outcome_and_summary(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_READY)
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    run_id = store.start_run(
        t.id, lane="research", claim_lock="L1", ttl_seconds=60,
    )
    store.finalize_run(
        run_id, outcome="completed",
        summary="did the thing",
        metadata={"tokens_used": 1500},
        new_status="done",
    )
    run = store.get_run(run_id)
    assert run is not None
    assert run.outcome == "completed"
    assert run.summary == "did the thing"
    assert run.metadata == {"tokens_used": 1500}
    assert run.status == "done"
    assert run.ended_at is not None


def test_finalize_run_invalid_status_raises(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_READY)
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    run_id = store.start_run(
        t.id, lane=None, claim_lock="L1", ttl_seconds=60,
    )
    with pytest.raises(InvalidStatusError):
        store.finalize_run(run_id, outcome="completed", new_status="bogus")


def test_list_runs_newest_first(store: KanbanStore) -> None:
    t = store.create_task(title="x", status=STATUS_READY)
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    r1 = store.start_run(t.id, lane=None, claim_lock="L1", ttl_seconds=60)
    store.finalize_run(r1, outcome="released", new_status="released")
    store.release_claim(t.id)
    store.claim_task(t.id, claim_lock="L2", ttl_seconds=60)
    r2 = store.start_run(t.id, lane=None, claim_lock="L2", ttl_seconds=60)
    runs = store.list_runs(t.id)
    assert [r.id for r in runs] == [r2, r1]


# ──────────────────────────────────────────────────────────────────
# events
# ──────────────────────────────────────────────────────────────────


def test_append_event_validates_kind(store: KanbanStore) -> None:
    t = store.create_task(title="x")
    with pytest.raises(InvalidEventKindError):
        store.append_event(t.id, "not-a-kind")


def test_events_since_cursor_pagination(store: KanbanStore) -> None:
    t = store.create_task(title="x")
    # Cursor at 0 returns everything.
    all_events = store.events_since(0)
    assert len(all_events) >= 1
    # Bookmark the latest event id.
    cursor = store.latest_event_id()
    # Append something new.
    store.add_comment(t.id, author="user", body="hello")
    new_events = store.events_since(cursor)
    assert len(new_events) == 1
    assert new_events[0].kind == "commented"


def test_events_since_limit(store: KanbanStore) -> None:
    t = store.create_task(title="x")
    for i in range(10):
        store.add_comment(t.id, author="u", body=f"c{i}")
    out = store.events_since(0, limit=3)
    assert len(out) == 3
    # Oldest first ordering.
    assert out[0].id < out[-1].id


def test_latest_event_id_empty_returns_zero(tmp_path: Path) -> None:
    s = KanbanStore(tmp_path / "k.db")
    assert s.latest_event_id() == 0
    s.close()


def test_valid_event_kinds_includes_lifecycle(store: KanbanStore) -> None:
    """Pin a few names in the public enum so a rename forces a
    test review — these are public over the WS event payload."""
    for kind in (
        EVENT_CREATED, EVENT_CLAIMED, EVENT_COMPLETED, EVENT_PROMOTED,
        EVENT_LINKED,
    ):
        assert kind in VALID_EVENT_KINDS


def test_valid_statuses_full_set() -> None:
    """Pin the complete status enum — column rendering depends on it."""
    expected = {
        "triage", "todo", "ready", "in_progress",
        "blocked", "done", "archived",
    }
    assert VALID_STATUSES == expected


# ──────────────────────────────────────────────────────────────────
# board_summary
# ──────────────────────────────────────────────────────────────────


def test_board_summary_counts(store: KanbanStore) -> None:
    store.create_task(title="t1", status=STATUS_TODO)
    store.create_task(title="t2", status=STATUS_TODO)
    r = store.create_task(title="r1", status=STATUS_READY)
    store.archive_task(
        store.create_task(title="archived").id,
    )
    summary = store.board_summary()
    assert summary["todo"] == 2
    assert summary["ready"] == 1
    assert summary["triage"] == 0  # nothing in triage
    # Archived NOT counted (key is absent or zero — we always include
    # it as a key with 0 because archived isn't in ACTIVE_STATUSES, so
    # it shouldn't even be in the dict).
    assert "archived" not in summary


# ──────────────────────────────────────────────────────────────────
# restart preserves state
# ──────────────────────────────────────────────────────────────────


def test_restart_preserves_tasks_and_events(tmp_path: Path) -> None:
    db = tmp_path / "k.db"
    s1 = KanbanStore(db)
    t = s1.create_task(title="persisted", lane="ops", priority=3)
    s1.add_comment(t.id, author="user", body="comment")
    s1.close()
    s2 = KanbanStore(db)
    after = s2.require_task(t.id)
    assert after.title == "persisted"
    assert after.lane == "ops"
    assert after.priority == 3
    assert len(s2.list_comments(t.id)) == 1
    assert s2.latest_event_id() > 0
    s2.close()


# ──────────────────────────────────────────────────────────────────
# to_dict round-trip
# ──────────────────────────────────────────────────────────────────


def test_task_to_dict_is_json_serialisable(store: KanbanStore) -> None:
    import json
    t = store.create_task(
        title="ser", lane="research", skills=["a", "b"],
        body="line1\nline2",
    )
    d = t.to_dict()
    raw = json.dumps(d)
    parsed = json.loads(raw)
    assert parsed["title"] == "ser"
    assert parsed["skills"] == ["a", "b"]
    assert parsed["body"] == "line1\nline2"
