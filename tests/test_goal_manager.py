"""Tests for ``core/goal_manager.py`` — the per-session GoalManager."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from core.brain.null import BrainNull
from core.goal_judge import judge_goal as _real_judge_goal  # noqa: F401  # ensure module is loadable
from core.goal_manager import (
    CONTINUATION_PROMPT_TEMPLATE,
    GoalAlreadyActiveError,
    GoalManager,
)
from core.goal_state import GoalState, GoalStateStore, TerminalGoalError


# ──────────────────────────────────────────────────────────────────
# Phase B helpers — evaluate_after_turn is now async
# ──────────────────────────────────────────────────────────────────


def _evaluate_sync(mgr: GoalManager, last_response: str) -> dict[str, Any]:
    """Sync wrapper around ``GoalManager.evaluate_after_turn`` for
    test bodies that stay synchronous (codebase convention). Passes
    a placeholder ``BrainNull`` because every test that calls this
    has the judge patched to a fixed return value via ``_patch_judge``
    — the brain reference is never actually used."""
    return asyncio.run(mgr.evaluate_after_turn(last_response, BrainNull()))


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> GoalStateStore:
    return GoalStateStore(tmp_path / "goals.json")


def _mgr(store: GoalStateStore, session_uuid: str = "sid", *, max_turns: int = 20) -> GoalManager:
    return GoalManager(
        session_uuid=session_uuid,
        workspace=Path("/tmp"),
        store=store,
        default_max_turns=max_turns,
    )


# ──────────────────────────────────────────────────────────────────
# set / pause / resume / clear lifecycle
# ──────────────────────────────────────────────────────────────────


def test_set_creates_active_goal(store: GoalStateStore) -> None:
    mgr = _mgr(store, max_turns=5)
    state = mgr.set("port the goal command")
    assert state.goal == "port the goal command"
    assert state.status == "active"
    assert state.max_turns == 5
    assert state.turns_used == 0
    assert state.created_at is not None
    assert mgr.is_active()
    assert mgr.has_goal()
    assert "port the goal command" in mgr.status_line()
    assert "active" in mgr.status_line()


def test_set_rejects_empty_text(store: GoalStateStore) -> None:
    mgr = _mgr(store)
    with pytest.raises(ValueError):
        mgr.set("")
    with pytest.raises(ValueError):
        mgr.set("   \n  ")
    assert mgr.state is None


def test_set_rejects_when_already_active(store: GoalStateStore) -> None:
    """A goal already active or paused for the session blocks a new
    /goal <text>. Hermes' equivalent rejects via the slash handler;
    we surface a typed exception so the transport renders the right
    string."""
    mgr = _mgr(store)
    mgr.set("first")
    with pytest.raises(GoalAlreadyActiveError):
        mgr.set("second")
    # Even when paused — the user must /goal clear first.
    mgr.pause()
    with pytest.raises(GoalAlreadyActiveError):
        mgr.set("third")


def test_set_replaces_done_or_cleared_record(store: GoalStateStore) -> None:
    """Once a goal is done or cleared, the user can /goal <text> a
    new one without typing /goal clear first."""
    mgr = _mgr(store)
    mgr.set("first")
    mgr.mark_done("delivered")
    # mark_done leaves status=done; has_goal is False so set() is allowed.
    assert not mgr.has_goal()
    state = mgr.set("second")
    assert state.goal == "second"
    assert state.status == "active"


def test_pause_writes_state_and_renders_status(store: GoalStateStore) -> None:
    mgr = _mgr(store)
    mgr.set("port goal")
    state = mgr.pause(reason="user-paused")
    assert state is not None
    assert state.status == "paused"
    assert state.paused_reason == "user-paused"
    assert "paused" in mgr.status_line()
    assert "user-paused" in mgr.status_line()
    # has_goal is True (paused records are still goals); is_active is False.
    assert mgr.has_goal()
    assert not mgr.is_active()


def test_pause_with_no_goal_returns_none(store: GoalStateStore) -> None:
    mgr = _mgr(store)
    assert mgr.pause() is None


def test_resume_resets_turns_used(store: GoalStateStore) -> None:
    """The §4 invariant: resume zeros the budget so a goal paused at
    20/20 can run another 20 without manual config edits."""
    mgr = _mgr(store, max_turns=5)
    mgr.set("port goal")
    # Simulate prior turns burning budget.
    state = mgr.state
    assert state is not None
    state.turns_used = 3
    store.save("sid", state)
    # Re-bind manager to flush the in-memory state.
    mgr = _mgr(store, max_turns=5)
    mgr.pause(reason="budget exhausted (3/5)")

    resumed = mgr.resume()
    assert resumed is not None
    assert resumed.status == "active"
    assert resumed.turns_used == 0  # ← the invariant
    assert resumed.paused_reason is None


def test_clear_marks_status_and_drops_in_memory(store: GoalStateStore) -> None:
    mgr = _mgr(store)
    mgr.set("port goal")
    mgr.clear()
    # Status persisted on disk for audit, but the manager treats
    # itself as goal-less.
    assert mgr.state is None
    assert not mgr.has_goal()
    assert "No active goal" in mgr.status_line()
    # On disk, the cleared record is still there.
    raw = store.load("sid")
    assert raw is not None
    assert raw.status == "cleared"


def test_mark_done_writes_status(store: GoalStateStore) -> None:
    mgr = _mgr(store)
    mgr.set("ship")
    mgr.mark_done("shipped")
    assert mgr.state is not None
    assert mgr.state.status == "done"
    assert mgr.state.last_verdict == "done"
    assert mgr.state.last_reason == "shipped"


# ──────────────────────────────────────────────────────────────────
# Persistence across managers (Hermes' load-bearing invariant)
# ──────────────────────────────────────────────────────────────────


def test_persistence_across_managers(store: GoalStateStore) -> None:
    """Two managers on the same session UUID see each other's writes
    via the store. This is what makes daemon restart and the per-call
    manager-rebuild pattern in `_on_goal` / `_run_goal_hook` work."""
    m1 = _mgr(store)
    m1.set("first goal")

    m2 = _mgr(store)
    assert m2.state is not None
    assert m2.state.goal == "first goal"
    assert m2.is_active()

    m2.pause(reason="m2 paused it")
    m3 = _mgr(store)
    assert m3.state is not None
    assert m3.state.status == "paused"
    assert m3.state.paused_reason == "m2 paused it"


# ──────────────────────────────────────────────────────────────────
# evaluate_after_turn outcomes
# ──────────────────────────────────────────────────────────────────


def _patch_judge(verdict: str, reason: str):
    """Patch ``core.goal_manager.judge_goal`` (the import the manager
    uses) to return a fixed verdict. Local helper because every
    evaluate test needs it. Phase B: ``judge_goal`` is async, so we
    use ``AsyncMock`` to make ``await judge_goal(...)`` resolve to
    the fixed tuple."""
    return mock.patch(
        "core.goal_manager.judge_goal",
        new=mock.AsyncMock(return_value=(verdict, reason)),
    )


def test_evaluate_done_marks_goal_done(store: GoalStateStore) -> None:
    mgr = _mgr(store)
    mgr.set("ship")
    with _patch_judge("done", "delivered"):
        decision = _evaluate_sync(mgr, "I shipped it.")
    assert decision["verdict"] == "done"
    assert decision["should_continue"] is False
    assert decision["continuation_prompt"] is None
    assert "Goal achieved" in decision["message"]
    assert mgr.state is not None
    assert mgr.state.status == "done"
    assert mgr.state.turns_used == 1


def test_evaluate_continue_under_budget_emits_continuation(
    store: GoalStateStore,
) -> None:
    mgr = _mgr(store, max_turns=5)
    mgr.set("port the goal command")
    with _patch_judge("continue", "made progress"):
        decision = _evaluate_sync(mgr, "started Day 2 work")
    assert decision["verdict"] == "continue"
    assert decision["should_continue"] is True
    assert decision["continuation_prompt"] is not None
    assert "port the goal command" in decision["continuation_prompt"]
    assert "Continuing toward goal" in decision["message"]
    assert mgr.state is not None
    assert mgr.state.status == "active"
    assert mgr.state.turns_used == 1


def test_evaluate_budget_exhaustion_auto_pauses(store: GoalStateStore) -> None:
    """When ``turns_used`` reaches ``max_turns``, the manager flips to
    paused with a budget-exhausted reason and stops emitting
    continuations."""
    mgr = _mgr(store, max_turns=2)
    mgr.set("hard goal")
    with _patch_judge("continue", "not yet"):
        d1 = _evaluate_sync(mgr, "step 1")
        assert d1["should_continue"] is True
        assert mgr.state is not None
        assert mgr.state.turns_used == 1
        assert mgr.state.status == "active"

        d2 = _evaluate_sync(mgr, "step 2")
        # turns_used hits max_turns after this call.
        assert d2["should_continue"] is False
        assert d2["continuation_prompt"] is None
        assert mgr.state.status == "paused"
        assert mgr.state.turns_used == 2
        assert "budget" in (mgr.state.paused_reason or "").lower()
        assert "paused" in d2["message"].lower()


def test_evaluate_inactive_when_no_goal(store: GoalStateStore) -> None:
    mgr = _mgr(store)
    decision = _evaluate_sync(mgr, "anything")
    assert decision["verdict"] == "inactive"
    assert decision["should_continue"] is False


def test_evaluate_inactive_when_paused(store: GoalStateStore) -> None:
    mgr = _mgr(store)
    mgr.set("goal")
    mgr.pause()
    decision = _evaluate_sync(mgr, "anything")
    assert decision["verdict"] == "inactive"
    assert decision["should_continue"] is False
    # Turn count not incremented when goal isn't active.
    assert mgr.state is not None
    assert mgr.state.turns_used == 0


def test_evaluate_skipped_folded_into_continue(store: GoalStateStore) -> None:
    """The §3 invariant: when judge_goal returns ('skipped', ...) for
    any reason, the manager treats it as continue — the brain turn
    that preceded the call already consumed budget, so the count
    increments and a continuation is enqueued."""
    mgr = _mgr(store, max_turns=5)
    mgr.set("g")
    with _patch_judge("skipped", "empty goal"):
        decision = _evaluate_sync(mgr, "reply")
    assert decision["should_continue"] is True
    assert decision["continuation_prompt"] is not None
    assert mgr.state is not None
    assert mgr.state.turns_used == 1
    # The verdict is recorded as "skipped" for forensics.
    assert mgr.state.last_verdict == "skipped"


# ──────────────────────────────────────────────────────────────────
# Continuation prompt cache invariant
# ──────────────────────────────────────────────────────────────────


def test_continuation_prompt_contains_goal_text(store: GoalStateStore) -> None:
    mgr = _mgr(store)
    mgr.set("port goal command to vexis")
    prompt = mgr.next_continuation_prompt()
    assert prompt is not None
    assert "port goal command to vexis" in prompt


def test_continuation_prompt_no_system_prompt_leak(store: GoalStateStore) -> None:
    """**Load-bearing prompt-cache invariant** from §3 / §5 of the
    research doc. The continuation prompt is fed to the brain as a
    plain user-role message; if it ever contained system-prompt-shaped
    content (``"You are"`` framings or our ``[SYSTEM CONTEXT]``
    notifier header), Anthropic prompt caching would invalidate and
    cost would spike. This test catches that drift."""
    mgr = _mgr(store)
    mgr.set("port goal command")
    prompt = mgr.next_continuation_prompt()
    assert prompt is not None
    assert "You are" not in prompt
    assert "[SYSTEM CONTEXT]" not in prompt
    # And the template constant itself doesn't leak — guards the
    # static side too in case a future test stub mocks the manager.
    assert "You are" not in CONTINUATION_PROMPT_TEMPLATE
    assert "[SYSTEM CONTEXT]" not in CONTINUATION_PROMPT_TEMPLATE


def test_continuation_prompt_none_when_inactive(store: GoalStateStore) -> None:
    mgr = _mgr(store)
    assert mgr.next_continuation_prompt() is None
    mgr.set("g")
    mgr.pause()
    assert mgr.next_continuation_prompt() is None


# ──────────────────────────────────────────────────────────────────
# Day 5.5 — terminal verdicts win against concurrent pause / resume
# ──────────────────────────────────────────────────────────────────


def test_pause_after_done_raises_terminal(store: GoalStateStore) -> None:
    """A pause on a goal whose disk state is already ``done`` raises
    :class:`TerminalGoalError` instead of silently overwriting the
    terminal status with ``paused``."""
    mgr = _mgr(store)
    mgr.set("g")
    mgr.mark_done("delivered")  # disk: done

    # Build a fresh manager whose in-memory state was loaded BEFORE
    # the done write — simulates the race where the dashboard pause
    # request started while the goal was still active.
    racing = GoalManager(
        session_uuid="sid", workspace=Path("/tmp"), store=store
    )
    # Force-set a stale in-memory active state so pause() doesn't
    # short-circuit on `_state is None`. Disk is still done.
    racing._state = GoalState(goal="g", status="active", turns_used=2)

    with pytest.raises(TerminalGoalError) as exc_info:
        racing.pause(reason="user-paused")
    assert exc_info.value.status == "done"
    assert exc_info.value.session_uuid == "sid"

    # Disk untouched — still done with last_verdict=done.
    final = store.load("sid")
    assert final is not None
    assert final.status == "done"
    assert final.last_verdict == "done"
    assert final.paused_reason is None


def test_resume_after_done_raises_terminal(store: GoalStateStore) -> None:
    """Resume on a done goal raises :class:`TerminalGoalError` —
    a finished goal cannot be revived."""
    mgr = _mgr(store)
    mgr.set("g")
    mgr.mark_done("delivered")

    racing = GoalManager(
        session_uuid="sid", workspace=Path("/tmp"), store=store
    )
    racing._state = GoalState(goal="g", status="paused", turns_used=2)

    with pytest.raises(TerminalGoalError) as exc_info:
        racing.resume()
    assert exc_info.value.status == "done"

    final = store.load("sid")
    assert final is not None
    assert final.status == "done"


def test_pause_after_cleared_raises_terminal(store: GoalStateStore) -> None:
    """Cleared is the other terminal state. Pause raises rather
    than overwriting."""
    mgr = _mgr(store)
    mgr.set("g")
    mgr.clear()

    racing = GoalManager(
        session_uuid="sid", workspace=Path("/tmp"), store=store
    )
    racing._state = GoalState(goal="g", status="active")

    with pytest.raises(TerminalGoalError) as exc_info:
        racing.pause()
    assert exc_info.value.status == "cleared"

    final = store.load("sid")
    assert final is not None
    assert final.status == "cleared"


def test_resume_after_cleared_raises_terminal(store: GoalStateStore) -> None:
    mgr = _mgr(store)
    mgr.set("g")
    mgr.clear()

    racing = GoalManager(
        session_uuid="sid", workspace=Path("/tmp"), store=store
    )
    racing._state = GoalState(goal="g", status="paused")

    with pytest.raises(TerminalGoalError) as exc_info:
        racing.resume()
    assert exc_info.value.status == "cleared"


def test_pause_loses_to_concurrent_done_verdict(
    store: GoalStateStore,
) -> None:
    """End-to-end shape of the bug Day 5.5 fixes.

    Setup: GoalManager loaded ACTIVE state at __init__. Then a
    concurrent writer (simulating the goal hook's evaluate_after_turn
    save) flips disk to done. The manager's in-memory state is now
    stale — it still says active.

    With the Day 5.5 fix, ``mgr.pause()`` reloads disk under fcntl
    lock, sees done, raises :class:`TerminalGoalError`. Disk stays
    done. Without the fix (pre-Day-5.5), the pause would have
    blindly written its in-memory paused status over the done write
    — silent state corruption.
    """
    # Goal active on disk.
    mgr = GoalManager(
        session_uuid="sid", workspace=Path("/tmp"), store=store
    )
    mgr.set("ship the thing")
    # mgr's in-memory state: status=active, turns_used=0.

    # Concurrent writer flips disk to done. Manager has no idea.
    disk_state = store.load("sid")
    assert disk_state is not None
    disk_state.status = "done"
    disk_state.turns_used = 1
    disk_state.last_verdict = "done"
    disk_state.last_reason = "shipped"
    store.save("sid", disk_state)

    # Manager's in-memory state still claims active — the stale view.
    assert mgr._state is not None
    assert mgr._state.status == "active"

    # Pause MUST raise rather than corrupt disk.
    with pytest.raises(TerminalGoalError) as exc_info:
        mgr.pause(reason="user-paused")
    assert exc_info.value.status == "done"

    # Disk authoritative state preserved.
    final = store.load("sid")
    assert final is not None
    assert final.status == "done"
    assert final.last_verdict == "done"
    assert final.last_reason == "shipped"
    # Critically, paused_reason is NOT set — the pause's mutation
    # was rejected before any write happened.
    assert final.paused_reason is None


def test_resume_loses_to_concurrent_done_verdict(
    store: GoalStateStore,
) -> None:
    """Mirror of the pause race for resume."""
    mgr = GoalManager(
        session_uuid="sid", workspace=Path("/tmp"), store=store
    )
    mgr.set("g")
    state = mgr.state
    assert state is not None
    state.status = "paused"
    state.turns_used = 5
    store.save("sid", state)
    # Refresh manager so its in-memory matches disk (paused).
    mgr = GoalManager(
        session_uuid="sid", workspace=Path("/tmp"), store=store
    )
    assert mgr._state is not None and mgr._state.status == "paused"

    # Concurrent writer flips disk to done.
    state.status = "done"
    state.last_verdict = "done"
    state.last_reason = "concurrent finish"
    store.save("sid", state)

    with pytest.raises(TerminalGoalError) as exc_info:
        mgr.resume()
    assert exc_info.value.status == "done"

    final = store.load("sid")
    assert final is not None
    assert final.status == "done"
    # Resume's reset of turns_used to 0 was rejected — disk count
    # preserved at the value the concurrent writer left it.
    assert final.turns_used == 5


def test_pause_reload_picks_up_disk_changes(
    store: GoalStateStore,
) -> None:
    """The reload-under-lock isn't only for terminal protection —
    it also means non-terminal disk changes are preserved across
    a pause. E.g., another writer bumped ``turns_used`` between
    this manager's __init__ and our pause; the bumped count
    survives the pause write."""
    mgr = GoalManager(
        session_uuid="sid", workspace=Path("/tmp"), store=store
    )
    mgr.set("g")
    # Concurrent writer bumps turns_used (non-terminal change).
    disk = store.load("sid")
    assert disk is not None
    disk.turns_used = 7
    disk.last_verdict = "continue"
    disk.last_reason = "more work"
    store.save("sid", disk)
    # mgr's in-memory still has turns_used=0 from set().

    new_state = mgr.pause(reason="user-paused")
    assert new_state is not None
    assert new_state.status == "paused"
    # turns_used preserved from disk (NOT clobbered to mgr's stale 0).
    assert new_state.turns_used == 7
    assert new_state.last_verdict == "continue"
    assert new_state.paused_reason == "user-paused"


def test_continuation_prompt_starts_with_verbatim_prefix(
    store: GoalStateStore,
) -> None:
    """Pin the §3 verbatim prefix as a downstream contract.

    Coupled with :func:`test_continuation_prompt_no_system_prompt_leak`
    (no system-prompt-shaped strings) and
    :func:`test_continuation_prompt_contains_goal_text` (goal text
    present), this locks in the §3 template structurally. The prefix
    is what the user sees in the "Picking up:" preview when the
    drain processes a continuation, so any drift here would change
    user-visible behaviour silently.
    """
    mgr = _mgr(store)
    mgr.set("port goal command to vexis")
    prompt = mgr.next_continuation_prompt()
    assert prompt is not None
    assert prompt.startswith("[Continuing toward your standing goal]"), (
        f"continuation prompt drift: expected verbatim §3 prefix, got "
        f"{prompt[:80]!r}"
    )
    # And the template constant itself starts with the prefix —
    # guards the static side too.
    assert CONTINUATION_PROMPT_TEMPLATE.startswith(
        "[Continuing toward your standing goal]"
    )
