"""Kanban dispatcher + worker spawn tests.

Pinned behaviours:

  * ``build_worker_prompt`` carries KANBAN_WORKER_PREFIX as the first
    line so the learning curator's recursion guard skips worker
    sessions.
  * ``dispatch_once`` claims ready tasks, spawns workers up to
    ``max_concurrent``, releases stale claims, promotes todo→ready.
  * Unresolvable lane → task is skipped (logged), not crashed on.
  * KanbanController.start/stop lifecycle is idempotent.
  * Worker outcomes:
      - returncode=0 + status flipped to done by MCP → run done +
        consecutive_failures reset.
      - returncode=0 + task still in_progress → run failed, claim
        released, failure counter bumped.
      - returncode≠0 → run failed, failure counter bumped.
      - BrainTimeoutError → run timed_out, failure counter bumped.
      - BrainModelNotFoundError → run spawn_failed, task auto-blocked.
      - failure_limit reached → task auto-blocked (status=blocked).
  * Aux call carries correct env_overrides (VEXIS_KANBAN_TASK_ID etc),
    cwd, allow_tools=True, subsystem="kanban_worker".

Async test pattern follows the project's ``asyncio.run(scenario())``
convention — pytest-asyncio is NOT a dev dependency. Each async test
defines an inner ``scenario`` coroutine and calls ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vexis_agent.core.brain.base import (
    AuxResult,
    BrainError,
    BrainModelNotFoundError,
    BrainTimeoutError,
)
from vexis_agent.core.brain.null import BrainNull
from vexis_agent.core.kanban.constants import (
    ENV_VAR_KANBAN,
    ENV_VAR_KANBAN_CLAIM_LOCK,
    ENV_VAR_KANBAN_LANE,
    ENV_VAR_KANBAN_TASK_ID,
    KANBAN_WORKER_PREFIX,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_READY,
    STATUS_TODO,
    STATUS_TRIAGE,
)
from vexis_agent.core.kanban.db import KanbanStore
from vexis_agent.core.kanban.dispatcher import (
    KanbanController,
    build_worker_prompt,
    dispatch_once,
)
from vexis_agent.core.kanban.lanes import (
    DEFAULT_LANES,
    LaneSpec,
)


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path):
    s = KanbanStore(tmp_path / "kanban.db")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _isolate_yaml_config(monkeypatch, tmp_path):
    """Same shim as test_kanban_lanes.py — yaml_config has a captured
    reference to vexis_dir() that conftest's monkeypatch doesn't reach."""
    private_root = tmp_path / "_vexis_isolated"
    private_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.vexis_dir", lambda: private_root,
    )
    yield


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _spawn_recorder():
    """Spawn fn that records the call without scheduling a real task.
    Reset via the returned ``calls`` list — append/clear in tests."""
    calls: list[tuple] = []

    def spawn_fn(task, lane, claim_lock, run_id, max_runtime):
        calls.append((task, lane, claim_lock, run_id, max_runtime))
        return None

    return spawn_fn, calls


async def _drain(ctrl: KanbanController) -> None:
    """Wait for all in-flight spawn tasks to complete (or error out)."""
    if ctrl._in_flight:
        await asyncio.gather(*list(ctrl._in_flight), return_exceptions=True)


def _write_config(tmp_path: Path, body: str) -> None:
    """Write ``~/.vexis/config.yaml`` to the location the autouse
    ``_isolate_yaml_config`` fixture points ``vexis_dir()`` at."""
    cfg = tmp_path / "_vexis_isolated" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body, encoding="utf-8")


# ──────────────────────────────────────────────────────────────────
# build_worker_prompt
# ──────────────────────────────────────────────────────────────────


def test_worker_prompt_starts_with_recursion_guard_prefix(store):
    """First line MUST be KANBAN_WORKER_PREFIX so the learning curator
    skips this session. Recursion-guard invariant in CLAUDE.md."""
    task = store.create_task(title="x", body="b")
    lane = DEFAULT_LANES["default"]
    prompt = build_worker_prompt(task, lane)
    assert prompt.startswith(KANBAN_WORKER_PREFIX)


def test_worker_prompt_contains_lane_system_prompt(store):
    task = store.create_task(title="x")
    lane = LaneSpec(name="custom", system_prompt="DISTINCTIVE_PROMPT_MARKER")
    prompt = build_worker_prompt(task, lane)
    assert "DISTINCTIVE_PROMPT_MARKER" in prompt


def test_worker_prompt_contains_task_id_title_body(store):
    task = store.create_task(title="title-marker", body="body-marker-line")
    prompt = build_worker_prompt(task, DEFAULT_LANES["default"])
    assert task.id in prompt
    assert "title-marker" in prompt
    assert "body-marker-line" in prompt


def test_worker_prompt_handles_empty_body(store):
    task = store.create_task(title="just-title")
    prompt = build_worker_prompt(task, DEFAULT_LANES["default"])
    assert "just-title" in prompt


def test_worker_prompt_uses_vexis_kanban_cli_handshake(store):
    """The completion handshake is the real `vexis-kanban` CLI keyed on
    $VEXIS_KANBAN_TASK_ID — NOT a phantom `kanban_complete` tool. A
    worker that took the old wording literally never declared done and
    got retried into an auto-block (the v0.11 background-goal break)."""
    task = store.create_task(title="x", body="b")
    prompt = build_worker_prompt(task, DEFAULT_LANES["implementation"])
    # Exact completion command, with the env var the dispatcher sets.
    assert 'vexis-kanban complete "$VEXIS_KANBAN_TASK_ID"' in prompt
    assert 'vexis-kanban block "$VEXIS_KANBAN_TASK_ID"' in prompt
    # It explicitly disclaims the nonexistent tool so a literal-minded
    # worker doesn't hunt for it.
    assert "no `kanban_complete` tool" in prompt


# ──────────────────────────────────────────────────────────────────
# dispatch_once — pure function tests
# ──────────────────────────────────────────────────────────────────


def test_dispatch_once_empty_board_noops(store):
    spawn_fn, calls = _spawn_recorder()
    res = dispatch_once(
        store, max_concurrent=2, claim_ttl_seconds=60,
        default_max_runtime=300, spawn_fn=spawn_fn,
    )
    assert res.claimed == 0
    assert res.spawned == 0
    assert res.promoted == 0
    assert res.stale_released == 0
    assert calls == []


def test_dispatch_once_promotes_root_todo(store):
    """A todo with no parents gets promoted to ready and (in the same
    tick) claimed + spawned."""
    spawn_fn, calls = _spawn_recorder()
    t = store.create_task(title="x", status=STATUS_TODO)
    res = dispatch_once(
        store, max_concurrent=2, claim_ttl_seconds=60,
        default_max_runtime=300, spawn_fn=spawn_fn,
    )
    assert res.promoted == 1
    after = store.require_task(t.id)
    assert after.status == STATUS_IN_PROGRESS
    assert res.claimed == 1


def test_dispatch_once_keeps_triage_alone(store):
    """A task in triage is NOT promoted (only todo→ready promotes)."""
    spawn_fn, _ = _spawn_recorder()
    t = store.create_task(title="x", status=STATUS_TRIAGE)
    res = dispatch_once(
        store, max_concurrent=2, claim_ttl_seconds=60,
        default_max_runtime=300, spawn_fn=spawn_fn,
    )
    assert res.promoted == 0
    assert store.require_task(t.id).status == STATUS_TRIAGE


def test_dispatch_once_blocks_child_until_parents_done(store):
    """Parent in triage, child in todo with parent as dep. Child must
    NOT promote until parent reaches done."""
    spawn_fn, _ = _spawn_recorder()
    p = store.create_task(title="parent", status=STATUS_TRIAGE)
    c = store.create_task(title="child", parents=[p.id])  # auto-todo
    res = dispatch_once(
        store, max_concurrent=2, claim_ttl_seconds=60,
        default_max_runtime=300, spawn_fn=spawn_fn,
    )
    # Neither parent (still triage) nor child (parent not done) promotes.
    assert res.promoted == 0
    assert store.require_task(c.id).status == STATUS_TODO


def test_dispatch_once_promotes_child_when_parent_done(store):
    spawn_fn, _ = _spawn_recorder()
    p = store.create_task(title="parent")
    c = store.create_task(title="child", parents=[p.id])
    store.update_task(p.id, status=STATUS_DONE)
    res = dispatch_once(
        store, max_concurrent=2, claim_ttl_seconds=60,
        default_max_runtime=300, spawn_fn=spawn_fn,
    )
    assert c.id in [store.require_task(c.id).id]  # tautology, just confirm reachable
    assert res.promoted == 1
    assert store.require_task(c.id).status == STATUS_IN_PROGRESS


def test_dispatch_once_max_concurrent_cap(store):
    spawn_fn, _ = _spawn_recorder()
    for i in range(5):
        store.create_task(title=f"t{i}", status=STATUS_READY)
    res = dispatch_once(
        store, max_concurrent=2, claim_ttl_seconds=60,
        default_max_runtime=300, spawn_fn=spawn_fn,
    )
    assert res.claimed == 2
    assert res.skipped_at_cap is True


def test_dispatch_once_releases_stale_claim_and_reclaims(store):
    spawn_fn, _ = _spawn_recorder()
    t = store.create_task(title="x", status=STATUS_READY)
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=60)
    # Force expiry.
    store._conn.execute(
        "UPDATE tasks SET claim_expires = 0 WHERE id = ?", (t.id,),
    )
    res = dispatch_once(
        store, max_concurrent=2, claim_ttl_seconds=60,
        default_max_runtime=300, spawn_fn=spawn_fn,
    )
    assert res.stale_released == 1
    after = store.require_task(t.id)
    # Released then re-claimed in same tick.
    assert after.status == STATUS_IN_PROGRESS


def test_dispatch_once_unresolvable_lane_skipped(store):
    spawn_fn, calls = _spawn_recorder()
    # Lane doesn't exist anywhere.
    store.create_task(
        title="ghost", status=STATUS_READY, lane="lane-that-does-not-exist",
    )
    res = dispatch_once(
        store, max_concurrent=2, claim_ttl_seconds=60,
        default_max_runtime=300, spawn_fn=spawn_fn,
    )
    assert res.claimed == 0
    assert calls == []


def test_dispatch_once_calls_spawn_with_resolved_lane(store):
    spawn_fn, calls = _spawn_recorder()
    store.create_task(title="x", status=STATUS_READY, lane="research")
    dispatch_once(
        store, max_concurrent=2, claim_ttl_seconds=60,
        default_max_runtime=300, spawn_fn=spawn_fn,
    )
    assert len(calls) == 1
    task, lane, claim_lock, run_id, max_runtime = calls[0]
    assert lane.name == "research"
    assert claim_lock != ""
    assert isinstance(run_id, int) and run_id > 0


# ──────────────────────────────────────────────────────────────────
# KanbanController lifecycle
# ──────────────────────────────────────────────────────────────────


def test_controller_start_stop_idempotent(store, workspace):
    async def scenario():
        brain = BrainNull()
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        loop = asyncio.get_running_loop()
        ctrl.start(loop)
        ctrl.start(loop)  # idempotent
        await ctrl.stop()
        await ctrl.stop()  # idempotent
    asyncio.run(scenario())


def test_controller_tick_runs(store, workspace):
    async def scenario():
        brain = BrainNull()
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        res = await ctrl.tick()
        assert res.claimed == 0
        assert res.spawned == 0
    asyncio.run(scenario())


# ──────────────────────────────────────────────────────────────────
# Worker outcomes
# ──────────────────────────────────────────────────────────────────


def test_worker_returncode_0_with_done_status_finalises_complete(
    store, workspace, monkeypatch,
):
    """The worker called kanban_complete via MCP during the spawn, so
    when spawn_aux returns the task is already in ``done``. The
    controller trusts that and marks the run done."""
    t = store.create_task(title="x", status=STATUS_READY, lane="default")
    aux = AuxResult(stdout="worker output", stderr="", returncode=0)
    brain = BrainNull(aux_results=[aux])
    real_spawn = brain.spawn_aux

    async def patched_spawn(prompt, **kwargs):
        # Simulate kanban_complete MCP tool flipping status mid-spawn.
        store.update_task(t.id, status=STATUS_DONE)
        return await real_spawn(prompt, **kwargs)

    monkeypatch.setattr(brain, "spawn_aux", patched_spawn)

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    after = store.require_task(t.id)
    assert after.status == STATUS_DONE
    assert after.consecutive_failures == 0
    runs = store.list_runs(t.id)
    assert len(runs) == 1
    assert runs[0].outcome == "completed"


def test_worker_silent_exit_treated_as_gave_up(store, workspace):
    """Worker exited cleanly but never called kanban_complete or
    kanban_block. Release back to ready, bump failure counter."""
    t = store.create_task(title="x", status=STATUS_READY, lane="default")
    brain = BrainNull(
        aux_results=[AuxResult(stdout="silent", stderr="", returncode=0)],
    )

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    after = store.require_task(t.id)
    assert after.status == STATUS_READY
    assert after.consecutive_failures == 1
    runs = store.list_runs(t.id)
    assert runs[0].outcome == "gave_up"


def test_worker_nonzero_returncode_failed(store, workspace):
    t = store.create_task(title="x", status=STATUS_READY, lane="default")
    brain = BrainNull(
        aux_results=[AuxResult(stdout="stuff", stderr="err", returncode=1)],
    )

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    after = store.require_task(t.id)
    assert after.status == STATUS_READY
    assert after.consecutive_failures == 1
    runs = store.list_runs(t.id)
    assert runs[0].outcome == "failed"


def test_worker_brain_timeout(store, workspace):
    t = store.create_task(title="x", status=STATUS_READY, lane="default")
    brain = BrainNull()
    brain.next_aux_raises(BrainTimeoutError("subprocess timed out"))

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    after = store.require_task(t.id)
    assert after.consecutive_failures == 1
    runs = store.list_runs(t.id)
    assert runs[0].outcome == "timed_out"


def test_worker_model_not_found_auto_blocks(store, workspace):
    t = store.create_task(title="x", status=STATUS_READY, lane="default")
    brain = BrainNull()
    brain.next_aux_raises(BrainModelNotFoundError(
        subsystem="kanban_worker",
        model_id="bogus-model",
        brain_kind="claude-code",
        suggested_fix="Run /model set kanban_worker <valid-id>",
    ))

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    after = store.require_task(t.id)
    assert after.status == STATUS_BLOCKED
    runs = store.list_runs(t.id)
    assert runs[0].outcome == "spawn_failed"


def test_worker_brain_error_generic(store, workspace):
    t = store.create_task(title="x", status=STATUS_READY, lane="default")
    brain = BrainNull()
    brain.next_aux_raises(BrainError("generic spawn error"))

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    after = store.require_task(t.id)
    assert after.consecutive_failures == 1
    runs = store.list_runs(t.id)
    assert runs[0].outcome == "spawn_failed"


def test_failure_limit_auto_blocks(store, workspace):
    """Three consecutive failures (DEFAULT_FAILURE_LIMIT=3) → task auto-blocks."""
    t = store.create_task(title="x", status=STATUS_READY, lane="default")
    brain = BrainNull(aux_results=[
        AuxResult(stdout="", stderr="err1", returncode=1),
        AuxResult(stdout="", stderr="err2", returncode=1),
        AuxResult(stdout="", stderr="err3", returncode=1),
    ])

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        for _ in range(3):
            await ctrl.tick()
            await _drain(ctrl)
    asyncio.run(scenario())

    after = store.require_task(t.id)
    assert after.status == STATUS_BLOCKED
    assert after.consecutive_failures >= 3


# ──────────────────────────────────────────────────────────────────
# Aux call shape
# ──────────────────────────────────────────────────────────────────


def test_spawn_aux_carries_kanban_env_vars(store, workspace):
    t = store.create_task(title="x", status=STATUS_READY, lane="research")
    brain = BrainNull(
        aux_results=[AuxResult(stdout="ok", stderr="", returncode=0)],
    )

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    records = brain.aux_call_records()
    assert len(records) == 1
    env = records[0]["env_overrides"]
    assert env is not None
    assert env.get(ENV_VAR_KANBAN) == "1"
    assert env.get(ENV_VAR_KANBAN_TASK_ID) == t.id
    assert env.get(ENV_VAR_KANBAN_LANE) == "research"
    # The claim lock the worker would use to heartbeat itself — non-empty
    # so a long single-tool-call worker can extend its own claim.
    assert env.get(ENV_VAR_KANBAN_CLAIM_LOCK)


def test_claim_keepalive_heartbeats_in_flight_task(store, workspace, monkeypatch):
    """While a worker runs, the dispatcher heartbeats its claim so a run
    longer than the claim TTL isn't reclaimed mid-flight (which would
    bump failures AND spawn a duplicate worker). We drive
    ``_claim_keepalive`` directly with an instant clock and assert it
    actually beats the store."""
    import vexis_agent.core.kanban.dispatcher as disp

    t = store.create_task(title="x", status=STATUS_READY, lane="research")
    store.claim_task(t.id, claim_lock="L1", ttl_seconds=100)

    # Spy on the store heartbeat so the assertion is clock-independent.
    hb_calls: list[tuple] = []
    real_hb = store.heartbeat

    def spy_heartbeat(task_id, *, claim_lock, ttl_seconds):
        hb_calls.append((task_id, claim_lock, ttl_seconds))
        return real_hb(task_id, claim_lock=claim_lock, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(store, "heartbeat", spy_heartbeat)

    # Beat immediately instead of sleeping ttl//2, and stop after two beats.
    real_sleep = asyncio.sleep
    beats = {"n": 0}

    async def fast_sleep(_seconds):
        beats["n"] += 1
        if beats["n"] > 2:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(disp.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(disp, "kanban_claim_ttl_seconds", lambda: 100)

    async def scenario():
        ctrl = KanbanController(store=store, brain=BrainNull(), workspace=workspace)
        try:
            await ctrl._claim_keepalive(t.id, "L1")
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())

    # Two beats landed on the store with the right task id, claim lock,
    # and TTL — the claim is being kept alive under the worker.
    assert len(hb_calls) >= 2
    assert hb_calls[0] == (t.id, "L1", 100)
    assert store.get_task(t.id).claim_expires is not None


def test_spawn_aux_allow_tools_true_and_subsystem(store, workspace):
    store.create_task(title="x", status=STATUS_READY, lane="default")
    brain = BrainNull(
        aux_results=[AuxResult(stdout="ok", stderr="", returncode=0)],
    )

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    records = brain.aux_call_records()
    assert records[0]["allow_tools"] is True
    assert records[0]["subsystem"] == "kanban_worker"


def test_spawn_aux_carries_lane_tier(store, workspace):
    """Lane.tier flows through to brain.spawn_aux as model_tier."""
    store.create_task(
        title="x", status=STATUS_READY, lane="implementation",
    )
    brain = BrainNull(
        aux_results=[AuxResult(stdout="ok", stderr="", returncode=0)],
    )

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    records = brain.aux_call_records()
    # implementation lane defaults to ``large`` tier.
    assert records[0]["model_tier"] == "large"
    # Built-in lanes pin no effort — reasoning defers to the CLI default.
    assert records[0]["reasoning_level"] is None


def test_spawn_aux_carries_lane_reasoning(store, workspace, tmp_path):
    """Issue #50: a lane's ``reasoning`` knob flows through to
    ``brain.spawn_aux`` as ``reasoning_level`` the same way ``tier``
    flows through as ``model_tier``."""
    _write_config(tmp_path, """
kanban:
  lanes:
    implementation:
      tier: large
      reasoning: low
    """)
    store.create_task(
        title="x", status=STATUS_READY, lane="implementation",
    )
    brain = BrainNull(
        aux_results=[AuxResult(stdout="ok", stderr="", returncode=0)],
    )

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    records = brain.aux_call_records()
    assert records[0]["model_tier"] == "large"
    assert records[0]["reasoning_level"] == "low"


def test_event_hook_invoked_on_spawn(store, workspace):
    store.create_task(title="x", status=STATUS_READY, lane="default")
    brain = BrainNull(
        aux_results=[AuxResult(stdout="ok", stderr="", returncode=0)],
    )
    fired: list[str] = []

    async def hook():
        fired.append("kick")

    async def scenario():
        ctrl = KanbanController(
            store=store, brain=brain, workspace=workspace,
            event_hook=hook,
        )
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    # Hook fires on tick-end (claim/spawn) and on spawn-finish.
    assert len(fired) >= 1


# ──────────────────────────────────────────────────────────────────
# Cancellation
# ──────────────────────────────────────────────────────────────────


def test_controller_stop_cancels_in_flight_spawn(store, workspace):
    store.create_task(title="x", status=STATUS_READY, lane="default")

    class SlowBrain(BrainNull):
        async def spawn_aux(self, prompt, **kwargs):
            await asyncio.sleep(60)
            return AuxResult(stdout="", stderr="", returncode=0)

    brain = SlowBrain()

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        assert ctrl.in_flight_count() == 1
        await ctrl.stop()
        assert ctrl.in_flight_count() == 0
    asyncio.run(scenario())


# ──────────────────────────────────────────────────────────────────
# Per-task max_retries override
# ──────────────────────────────────────────────────────────────────


def test_per_task_max_retries_overrides_global_limit(store, workspace):
    """Task with max_retries=1 auto-blocks on the first failure even
    though global failure_limit is 3."""
    t = store.create_task(
        title="x", status=STATUS_READY, lane="default", max_retries=1,
    )
    brain = BrainNull(
        aux_results=[AuxResult(stdout="", stderr="err", returncode=1)],
    )

    async def scenario():
        ctrl = KanbanController(store=store, brain=brain, workspace=workspace)
        ctrl._loop = asyncio.get_running_loop()
        await ctrl.tick()
        await _drain(ctrl)
    asyncio.run(scenario())

    after = store.require_task(t.id)
    assert after.status == STATUS_BLOCKED
    assert after.consecutive_failures == 1
