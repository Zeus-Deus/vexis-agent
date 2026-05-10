"""Phase C Day 8 — real-binary claude-code smoke tests.

Opt-in via ``pytest -m brain_smoke``. Default suite skips these
(they spawn a real ``claude -p`` subprocess and hit Anthropic's
API). Mirror of ``tests/test_brain_opencode_smoke.py`` so both
brains have parity coverage at the smoke level.

What's covered

- **Foreground happy path**: real ``claude -p`` against
  ``brain.respond`` returns a non-empty reply. Smokes the full
  ``respond`` path on the real binary, not just our mocks.
- **Session resume + threading**: turn 1 plants a sentinel,
  turn 2 resumes via the stored session UUID and asks for it
  back. Pins ``--resume <uuid>`` actually plumbing prior context
  through claude-code (not just argv shape).
- **Process kill (cancel mid-turn)**: spawn a long-running turn,
  ``RunningTasks.cancel`` mid-stream, assert every PID in the
  original PG is reaped within the 5 s ``_kill_group`` ceiling.
  Pins ``start_new_session=True`` at spawn + the killpg primitive
  against real claude-code child topology.

What's NOT covered

- MCP tool firing — covered by the dogfood checklist's manual
  step #4. Same rationale as the opencode smoke: deterministic
  MCP coverage requires a known-stable server + a model that
  always invokes it, which is too much harness for a smoke
  marker.
- Auth-failure surface — covered by the cross-brain contract
  exception-hierarchy test plus the install script's PATH-check
  test. Real-auth-revoked is too disruptive to automate.

Cost

Two real ``claude -p`` calls per resume test + one per
foreground test + one cancellable per kill test ≈ pennies per
full smoke run.

Design citation: ``.plans/brain-abstraction-research.md`` §5 Day 8
"Final cross-brain test run".
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import psutil
import pytest

from vexis_agent.core.brain.base import BrainCancelled
from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore


def _claude_available() -> bool:
    return shutil.which("claude") is not None


pytestmark = [
    pytest.mark.brain_smoke,
    pytest.mark.skipif(
        not _claude_available(),
        reason="claude binary not on PATH",
    ),
]


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws-cc-smoke"
    ws.mkdir()
    return ws


@pytest.fixture
def session_store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions.json")


@pytest.fixture
def running_tasks() -> RunningTasks:
    return RunningTasks()


@pytest.fixture
def brain(
    workspace: Path,
    session_store: SessionStore,
    running_tasks: RunningTasks,
) -> ClaudeCodeBrain:
    return ClaudeCodeBrain(
        workspace=workspace,
        session=session_store,
        running_tasks=running_tasks,
    )


# ──────────────────────────────────────────────────────────────────
# Foreground happy path
# ──────────────────────────────────────────────────────────────────


def test_foreground_turn_produces_reply(brain: ClaudeCodeBrain):
    """Spawn a real ``claude -p`` and verify a non-empty reply
    arrives within the test timeout."""
    async def _run():
        return await asyncio.wait_for(
            brain.respond("Reply with the single word: pong", chat_id=999),
            timeout=60.0,
        )

    reply = asyncio.run(_run())
    assert isinstance(reply, str)
    assert reply.strip() != "", f"empty reply from real claude -p: {reply!r}"


# ──────────────────────────────────────────────────────────────────
# Session resume — share context across turns
# ──────────────────────────────────────────────────────────────────


def test_session_resume_threads_context(
    brain: ClaudeCodeBrain, session_store: SessionStore,
):
    """Two real turns. Turn 1 establishes a fact; turn 2 resumes
    via the stored session UUID and asks for it back. The model
    must remember — proves the resume path actually plumbs prior
    context through claude-code."""
    sentinel = "borogove-cc-7421"

    async def _run_two_turns():
        await asyncio.wait_for(
            brain.respond(
                f"Remember the secret word for this turn: {sentinel}. "
                f"Reply: 'noted'.",
                chat_id=1001,
            ),
            timeout=90.0,
        )
        # Session token was minted at SessionStore init; resume
        # path uses it on the next call.
        token_after_t1 = session_store.get()
        assert session_store.is_initialized(), (
            "first call did not flip initialised=True"
        )
        # claude-code's session ids are UUIDs — sanity-check the shape.
        assert "-" in token_after_t1, (
            f"session token doesn't look like a UUID: {token_after_t1!r}"
        )

        reply2 = await asyncio.wait_for(
            brain.respond(
                "What was the secret word I told you? Reply with just the word.",
                chat_id=1001,
            ),
            timeout=90.0,
        )
        return reply2

    reply2 = asyncio.run(_run_two_turns())
    assert sentinel in reply2.lower() or sentinel in reply2, (
        f"second-turn reply doesn't contain the sentinel — context "
        f"didn't carry across resume. Reply: {reply2!r}"
    )


# ──────────────────────────────────────────────────────────────────
# Process kill — /cancel mid-turn reaps the whole process group
# ──────────────────────────────────────────────────────────────────


def _proc_tree(pid: int) -> list[int]:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return []
    out = [pid]
    for child in parent.children(recursive=True):
        out.append(child.pid)
    return out


def _pid_alive(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    try:
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
    except psutil.NoSuchProcess:
        return False
    return True


def test_cancel_mid_turn_reaps_process_group(
    brain: ClaudeCodeBrain,
    running_tasks: RunningTasks,
):
    """Spawn a real long-running turn, cancel mid-stream, verify
    every PID in the process group is gone within the
    ``_kill_group`` 5 s ceiling. Mirrors the opencode smoke's
    cancel test — same primitive, same assertions, different
    binary."""
    chat_id = 2002
    prompt = (
        "Slowly count from 1 to 200 in plain English, one number per line. "
        "Take your time."
    )

    async def _spawn_and_cancel():
        respond_task = asyncio.create_task(
            brain.respond(prompt, chat_id=chat_id)
        )
        # Wait until respond has actually spawned the subprocess.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if running_tasks.is_running(chat_id):
                break
            await asyncio.sleep(0.05)
        else:
            respond_task.cancel()
            pytest.fail("claude -p never started running within 10s")

        slot_pid = None
        state = running_tasks._chats.get(chat_id)
        if state is not None and state.slot is not None and state.slot.proc is not None:
            slot_pid = state.slot.proc.pid
        assert slot_pid is not None, "no proc registered post-spawn"

        pids_before = _proc_tree(slot_pid)
        cancelled = await running_tasks.cancel(chat_id, grace_seconds=5.0)
        assert cancelled, "running_tasks.cancel returned False"

        with pytest.raises((BrainCancelled, Exception)):
            await asyncio.wait_for(respond_task, timeout=10.0)

        return slot_pid, pids_before

    slot_pid, pids_before = asyncio.run(_spawn_and_cancel())

    deadline = time.monotonic() + 6.0
    survivors: list[int] = []
    while time.monotonic() < deadline:
        survivors = [pid for pid in pids_before if _pid_alive(pid)]
        if not survivors:
            return
        time.sleep(0.1)
    pytest.fail(
        f"PG kill failed: survivors after 6s = {survivors} "
        f"(slot_pid={slot_pid}, original_pg={pids_before})"
    )
