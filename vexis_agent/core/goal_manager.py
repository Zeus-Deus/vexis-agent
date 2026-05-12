"""Per-session orchestration for the /goal command.

A :class:`GoalManager` binds to one Claude session UUID and owns the
state-machine transitions for a standing goal: set / pause / resume /
clear / mark_done / evaluate_after_turn / next_continuation_prompt.
Persistence flows through :class:`core.goal_state.GoalStateStore`;
the auxiliary judge call is :func:`core.goal_judge.judge_goal`.

Mirrors upstream goal-loop prior-art 1:1 with two adaptations:

  * Persistence is `GoalStateStore` (single-file fcntl + atomic
    rename) instead of the upstream ``SessionDB.state_meta`` SQLite table.
  * The judge is a `claude -p` subprocess call (Vexis' aux model
    pattern) instead of an OpenAI-compatible client.

The transport layer (`transports/telegram.py:_on_goal` and the goal
hook in `_drain_chat`) holds the manager; this module is pure
orchestration with no I/O outside the store and judge calls.

Design invariants (from `.plans/goal-command-research.md`):

  * `set` rejects empty goal text — `judge_goal` would short-circuit
    to verdict=skipped, but we catch it earlier so the user gets a
    proper error message rather than a silent no-op.
  * `evaluate_after_turn` increments ``turns_used`` BEFORE asking the
    judge — both real user turns and goal continuations consume
    budget (matches upstream; documented in §1 of the research doc as
    a surprise worth flagging).
  * The verdict ``"skipped"`` is folded into the continue branch
    (turn IS counted, continuation IS enqueued). The brain turn that
    preceded the call already consumed budget; pretending it didn't
    would be wrong.
  * `resume` resets ``turns_used`` to 0 — the user's intent in
    typing /goal resume is "give me another budget", not "let it
    run one more turn before re-pausing".
  * `next_continuation_prompt` returns a plain user-role message that
    must NOT contain any system-prompt-shaped content (no "You are",
    no "[SYSTEM CONTEXT]") so Anthropic prompt caching stays intact.
    The test ``test_continuation_prompt_no_system_prompt_leak`` pins
    this.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vexis_agent.core.goal_judge import judge_goal
from vexis_agent.core.goal_state import (
    DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES,
    DEFAULT_MAX_TURNS,
    GoalState,
    GoalStateStore,
    TerminalGoalError,
)

if TYPE_CHECKING:
    from vexis_agent.core.brain.base import Brain

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Continuation prompt template — verbatim from upstream
# per `.plans/goal-command-research.md` §3.
# ──────────────────────────────────────────────────────────────────

CONTINUATION_PROMPT_TEMPLATE = (
    "[Continuing toward your standing goal]\n"
    "Goal: {goal}\n\n"
    "Continue working toward this goal. Take the next concrete step. "
    "If you believe the goal is complete, state so explicitly and stop. "
    "If you are blocked and need input from the user, say so clearly and stop."
)


# Status-line glyphs lifted from upstream — the actual user-facing
# strings live in :meth:`GoalManager.status_line` so a future skin
# (ASCII-only deployments) can rewrite them without touching the
# state machine.


# ──────────────────────────────────────────────────────────────────
# GoalManager
# ──────────────────────────────────────────────────────────────────


class GoalAlreadyActiveError(Exception):
    """Raised by :meth:`GoalManager.set` when a goal is already
    active or paused for this session. The /goal command surface
    catches this to render the "Goal already active" reject string
    rather than letting the user stomp the existing goal silently."""


class GoalManager:
    """Per-session goal state + continuation decisions.

    The Telegram transport instantiates one ``GoalManager`` per goal
    operation, all sharing the same workspace and ``GoalStateStore``.
    State is loaded on construction (so two managers on the same
    session see each other's writes via the store) and re-persisted
    after every mutation.

    The transport never holds a manager across multiple turns — it
    builds one per `_on_goal` call and per `_run_goal_hook` call.
    That keeps the lifetime tied to a single coordination boundary
    (a slash command, a drain iteration) and avoids stale in-memory
    state across `/clear`-induced session UUID rotations.
    """

    def __init__(
        self,
        *,
        session_uuid: str,
        workspace: Path,
        store: GoalStateStore,
        default_max_turns: int = DEFAULT_MAX_TURNS,
    ) -> None:
        self._session_uuid = session_uuid
        self._workspace = workspace
        self._store = store
        self._default_max_turns = max(1, int(default_max_turns or DEFAULT_MAX_TURNS))
        self._state: GoalState | None = store.load(session_uuid)

    # ----- introspection ---------------------------------------------

    @property
    def session_uuid(self) -> str:
        return self._session_uuid

    @property
    def state(self) -> GoalState | None:
        return self._state

    def reload(self) -> GoalState | None:
        """Re-read state from the store and replace the in-memory copy.

        The post-turn hook calls this between
        :meth:`evaluate_after_turn` returning and the continuation
        enqueue: a concurrent /goal pause / /goal clear / cancel
        auto-pause may have flipped status to paused or cleared
        during the judge call's async window. A fresh read catches
        that and lets the caller bail out before adding a stale
        continuation to the queue.
        """
        self._state = self._store.load(self._session_uuid)
        return self._state

    def is_active(self) -> bool:
        """True iff a goal is set AND the loop should fire on the
        next turn. ``cleared`` and ``done`` count as no goal here;
        ``paused`` stops the loop without removing the record."""
        return self._state is not None and self._state.status == "active"

    def has_goal(self) -> bool:
        """True iff a goal record exists in either active or paused
        state. ``cleared`` and ``done`` are absences from the user's
        perspective even though the row is still on disk."""
        return self._state is not None and self._state.status in ("active", "paused")

    def status_line(self) -> str:
        """One-line user-visible summary suitable for /goal status."""
        s = self._state
        if s is None or s.status == "cleared":
            return "No active goal. Set one with /goal <text>."
        budget = f"{s.turns_used}/{s.max_turns} turns"
        if s.status == "active":
            return f"⊙ Goal (active, {budget}): {s.goal}"
        if s.status == "paused":
            extra = f" — {s.paused_reason}" if s.paused_reason else ""
            return f"⏸ Goal (paused, {budget}{extra}): {s.goal}"
        if s.status == "done":
            return f"✓ Goal done ({budget}): {s.goal}"
        return f"Goal ({s.status}, {budget}): {s.goal}"

    # ----- mutation ---------------------------------------------------

    def set(self, goal: str, *, max_turns: int | None = None) -> GoalState:
        """Start a new standing goal.

        Raises :class:`ValueError` on empty goal text and
        :class:`GoalAlreadyActiveError` when a goal is currently
        ``active`` or ``paused`` for this session — the /goal command
        surface catches both to render the right reject string. A
        ``done`` or ``cleared`` row from a previous goal is replaced.
        """
        cleaned = (goal or "").strip()
        if not cleaned:
            raise ValueError("goal text is empty")
        if self.has_goal():
            raise GoalAlreadyActiveError(
                "a goal is already active or paused for this session"
            )
        budget = max(1, int(max_turns)) if max_turns else self._default_max_turns
        now = datetime.now(timezone.utc)
        state = GoalState(
            goal=cleaned,
            status="active",
            turns_used=0,
            max_turns=budget,
            created_at=now,
            last_turn_at=None,
        )
        self._state = state
        self._store.save(self._session_uuid, state)
        return state

    def pause(self, *, reason: str = "user-paused") -> GoalState | None:
        """Flip status → paused. No-op when no goal is set.

        ``reason`` is shown in the status line (e.g. ``"user-paused"``,
        ``"user-cancelled"`` from the /cancel auto-pause path,
        ``"turn budget exhausted (N/M)"`` from
        :meth:`evaluate_after_turn`). The in-flight brain turn
        (if any) is NOT interrupted — pause is "soft", taking effect
        on the next drain iteration when the goal hook reads
        ``status=paused`` and exits early.

        Reload-under-lock via :meth:`GoalStateStore.update_atomic`:
        if the disk state has flipped to terminal (``done`` /
        ``cleared``) since this manager was constructed, raises
        :class:`TerminalGoalError`. Caller surfaces as 409 (dashboard)
        or "Goal already done" (Telegram) — pause cannot revive a
        finished goal.
        """
        if self._state is None:
            return None

        def _apply(current: GoalState) -> GoalState:
            current.status = "paused"
            current.paused_reason = reason
            return current

        try:
            new_state = self._store.update_atomic(
                self._session_uuid, _apply, refuse_terminal=True,
            )
        except KeyError:
            # Row was deleted between our __init__ load and now.
            # Treat as no-goal — same posture as the original
            # ``self._state is None`` short-circuit.
            return None
        # TerminalGoalError propagates up to the caller (transport
        # surface translates to 409 / Telegram reply).
        self._state = new_state
        return self._state

    def resume(self) -> GoalState | None:
        """Flip status → active and reset ``turns_used`` to 0.

        The reset is deliberate — without it, a goal paused at the
        budget ceiling would re-pause after a single turn on resume
        (upstream does the same; `.plans/goal-command-research.md` §4
        adopts the behaviour). No-op when no goal is set.

        Same reload-under-lock contract as :meth:`pause`: raises
        :class:`TerminalGoalError` if disk state is ``done`` or
        ``cleared`` at lock-acquire time. A done goal cannot be
        resumed — it's finished.
        """
        if self._state is None:
            return None

        def _apply(current: GoalState) -> GoalState:
            current.status = "active"
            current.paused_reason = None
            current.turns_used = 0
            return current

        try:
            new_state = self._store.update_atomic(
                self._session_uuid, _apply, refuse_terminal=True,
            )
        except KeyError:
            return None
        self._state = new_state
        return self._state

    def clear(self) -> None:
        """Mark the goal cleared. Record retained for audit/restart.

        ``has_goal`` and ``is_active`` return False afterwards;
        ``status_line`` renders "No active goal".
        """
        if self._state is None:
            return
        if self._state.status == "cleared":
            return
        self._state.status = "cleared"
        self._store.save(self._session_uuid, self._state)
        # Drop the in-memory reference so subsequent introspection
        # treats the manager as goal-less. The on-disk row stays.
        self._state = None

    def mark_done(self, reason: str) -> None:
        """Force status → done with a reason. Used by the manager
        itself from :meth:`evaluate_after_turn`; exposed for tests
        and any future explicit /goal done command."""
        if self._state is None:
            return
        self._state.status = "done"
        self._state.last_verdict = "done"
        self._state.last_reason = reason
        self._store.save(self._session_uuid, self._state)

    # ----- the post-turn entry point ---------------------------------

    async def evaluate_after_turn(
        self, last_response: str, brain: "Brain"
    ) -> dict[str, Any]:
        """Run the judge, update state, return a decision dict.

        Called by the goal hook in ``transports/telegram.py:_drain_chat``
        after every brain turn in a chat with an active goal. The
        return is consumed directly by the hook to drive the next
        action (send status, optionally enqueue continuation).

        Decision keys:

          * ``status``: current goal status after update
          * ``should_continue``: bool — caller should enqueue the
            continuation prompt
          * ``continuation_prompt``: str | None
          * ``verdict``: ``"done"`` | ``"continue"`` | ``"inactive"``
          * ``reason``: str
          * ``message``: user-visible one-liner for the chat (one of
            the three forms documented in §3 of the research doc)

        ``"skipped"`` from :func:`judge_goal` is folded into
        ``"continue"`` here — the brain turn that preceded the judge
        already consumed budget, so we keep accounting consistent.

        ``brain`` is the aux-spawn surface — Phase B threads it from
        the transport (telegram's ``_run_goal_hook``) through to
        :func:`judge_goal`. Stored as a parameter rather than on the
        manager so the dashboard's read-only ``GoalManager`` doesn't
        need a brain reference.
        """
        state = self._state
        if state is None or state.status != "active":
            return {
                "status": state.status if state else None,
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "inactive",
                "reason": "no active goal",
                "message": "",
            }

        # Count the brain turn that just finished — this is what
        # consumed budget, regardless of whether the judge says done
        # or continue or skipped (defensive). Mirrors the upstream pattern
        #.
        state.turns_used += 1
        state.last_turn_at = datetime.now(timezone.utc)

        verdict, reason, parse_failed = await judge_goal(
            self._workspace, state.goal, last_response, brain
        )
        # Cache the cited verdict on disk so /goal status can show it
        # without re-running the judge. ``"skipped"`` is recorded as-is
        # for forensics even though we fold it into the continue path
        # for budget/queue accounting.
        state.last_verdict = verdict if verdict in ("done", "continue", "skipped") else None
        state.last_reason = reason

        # Track consecutive judge parse failures. Reset on any usable
        # reply, including transport / spawn errors (parse_failed=False)
        # so a flaky brain doesn't trip the auto-pause meant for bad
        # judge models. Mirrors the upstream pattern
        #. The reset happens before the
        # done branch so a "done" verdict that follows a stretch of
        # parse failures doesn't leave a stale counter on the row.
        if parse_failed:
            state.consecutive_parse_failures += 1
        else:
            state.consecutive_parse_failures = 0

        if verdict == "done":
            state.status = "done"
            self._store.save(self._session_uuid, state)
            return {
                "status": "done",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "done",
                "reason": reason,
                "message": f"✓ Goal achieved: {reason}",
            }

        # Auto-pause when the judge model can't produce the expected
        # JSON verdict N turns in a row. Points the user at the
        # ``models.subsystems.goal_judge`` knob so they can route this
        # side task to a stricter tier (or model id) that follows the
        # contract. Without this guard, a misconfigured ``goal_judge``
        # tier (small/tiny, or a non-strict-JSON model) would burn the
        # entire turn budget producing identical "judge reply was not
        # JSON" log lines before the budget backstop fires.
        #
        # Checked BEFORE the budget backstop on purpose: when both
        # would fire on the same turn, the parse-failure message is
        # the actionable one (config fix), while the budget message
        # would just say "20/20 turns used" without explaining why the
        # judge never agreed.
        if state.consecutive_parse_failures >= DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES:
            state.status = "paused"
            state.paused_reason = (
                f"judge model returned unparseable output "
                f"{state.consecutive_parse_failures} turns in a row"
            )
            self._store.save(self._session_uuid, state)
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — the judge model "
                    f"({state.consecutive_parse_failures} turns) isn't "
                    "returning the required JSON verdict. Route the "
                    "judge to a stricter tier in ~/.vexis/config.yaml:\n"
                    "  models:\n"
                    "    subsystems:\n"
                    "      goal_judge: large\n"
                    "Then /goal resume to continue."
                ),
            }

        # Budget exhausted — auto-pause with a paused_reason that
        # /goal status surfaces verbatim.
        if state.turns_used >= state.max_turns:
            state.status = "paused"
            state.paused_reason = (
                f"turn budget exhausted ({state.turns_used}/{state.max_turns})"
            )
            self._store.save(self._session_uuid, state)
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — {state.turns_used}/{state.max_turns} "
                    "turns used. /goal resume to keep going, /goal clear to stop."
                ),
            }

        # Continue (or fold skipped → continue). Persist and emit
        # the continuation prompt for the hook to enqueue.
        self._store.save(self._session_uuid, state)
        return {
            "status": "active",
            "should_continue": True,
            "continuation_prompt": self.next_continuation_prompt(),
            "verdict": "continue",
            "reason": reason,
            "message": (
                f"↻ Continuing toward goal "
                f"({state.turns_used}/{state.max_turns}): {reason}"
            ),
        }

    def next_continuation_prompt(self) -> str | None:
        """Return the user-role message to feed back as the next turn,
        or ``None`` when no goal is active.

        **Cache invariant.** The returned string must NOT contain any
        system-prompt-shaped content. The unit test
        ``test_continuation_prompt_no_system_prompt_leak`` enforces
        this; see `.plans/goal-command-research.md` §5.
        """
        if not self._state or self._state.status != "active":
            return None
        return CONTINUATION_PROMPT_TEMPLATE.format(goal=self._state.goal)


__all__ = [
    "CONTINUATION_PROMPT_TEMPLATE",
    "GoalAlreadyActiveError",
    "GoalManager",
    # Re-exported from vexis_agent.core.goal_state so callers that already import
    # from goal_manager can catch this without learning a second module.
    "TerminalGoalError",
]
