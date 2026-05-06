"""Phase C Day 5 — real-binary OpenCode smoke tests.

Opt-in via ``pytest -m brain_smoke_opencode``. Default suite skips
these (they spawn a real ``opencode run`` subprocess, hit the
configured model provider's API, and depend on the user's auth
state). Run intentionally before flipping ``brain.kind`` to
``opencode`` and after every Day-5+ change to OpenCodeBrain.

What's covered

- **Foreground happy path**: real ``opencode run`` against
  ``brain.respond`` returns a non-empty string within the
  per-test timeout. Verifies argv shape + JSON event parsing on
  the real binary, not just our test fakes.
- **Process kill (cancel mid-turn)**: spawn a long-running turn
  and call ``RunningTasks.cancel`` mid-stream; assert the
  subprocess + every child is reaped within the 5 s
  ``_kill_group`` ceiling. Pins the ``start_new_session=True`` +
  ``os.killpg(SIGTERM)`` + 5 s grace + ``SIGKILL`` fallback
  primitive against real opencode child topology.
- **Session resume + harvest**: first call harvests an id, second
  call passes ``--session <id>`` and the conversation continues
  with shared context. Verified by asking the model to recall
  something said on turn 1.

What's NOT covered

- MCP tool firing. The Day-5 dogfood checklist (manual) covers
  this — automated MCP coverage requires a known-stable MCP
  server + a model deterministic enough to invoke it. Punt to
  Day 7 when ``docs/brains.md`` documents the manual flow.
- Auth-failure surface. ``opencode auth list`` is part of
  ``healthcheck`` (already smoke-tested in
  ``test_brain_opencode_scaffold.py``); a real auth-revoked
  scenario is too disruptive to automate.

Cost note

Each test spawns one or two real ``opencode run`` calls. A
short prompt against a tiny model is the cheapest plausible
shape (a haiku-tier or local model). The tests don't assert on
content semantics — only that a reply is produced, that
cancellation kills the process, and that resume threads
context. Approximate cost ceiling: a few cents per full smoke
run, dominated by the resume test's two turns.

Design citation: ``.plans/brain-abstraction-research.md`` §5 Day 5
"Process kill, error handling, edge cases, dogfood".
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import psutil
import pytest

from core.brain.base import BrainCancelled
from core.brain.opencode import OpenCodeBrain
from core.running_tasks import RunningTasks
from core.sessions import SessionStore


pytestmark = pytest.mark.brain_smoke_opencode


# ──────────────────────────────────────────────────────────────────
# Skip if opencode binary missing
# ──────────────────────────────────────────────────────────────────


def _opencode_available() -> bool:
    return shutil.which("opencode") is not None


pytestmark = [
    pytest.mark.brain_smoke_opencode,
    pytest.mark.skipif(
        not _opencode_available(),
        reason="opencode binary not on PATH",
    ),
]


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws-smoke"
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
) -> OpenCodeBrain:
    return OpenCodeBrain(
        workspace=workspace,
        session=session_store,
        running_tasks=running_tasks,
    )


# ──────────────────────────────────────────────────────────────────
# Foreground happy path
# ──────────────────────────────────────────────────────────────────


def test_foreground_turn_produces_reply(brain: OpenCodeBrain):
    """Spawn a real ``opencode run`` and verify a non-empty reply
    arrives within the test timeout. Smokes the full ``respond``
    path: spawn → JSON event stream parse → text concatenation →
    sessionID harvest → mark initialised."""
    # 60 s timeout — plenty for a one-line reply on the smallest
    # model the user has configured. Tests run ``-m
    # brain_smoke_opencode`` are explicitly opt-in so this isn't
    # a default-CI cost.
    async def _run():
        return await asyncio.wait_for(
            brain.respond("Reply with the single word: pong", chat_id=999),
            timeout=60.0,
        )

    reply = asyncio.run(_run())
    assert isinstance(reply, str)
    assert reply.strip() != "", f"empty reply from real opencode run: {reply!r}"


# ──────────────────────────────────────────────────────────────────
# Session resume — harvest + share context across turns
# ──────────────────────────────────────────────────────────────────


def test_session_resume_threads_context(
    brain: OpenCodeBrain, session_store: SessionStore,
):
    """Two real turns. Turn 1 establishes a fact; turn 2 resumes
    via the harvested session id and asks for it back. The model
    must remember — proves the ``--session <id>`` resume path
    actually plumbs prior context through opencode."""
    sentinel = "borogove-7421"

    async def _run_two_turns():
        await asyncio.wait_for(
            brain.respond(
                f"Remember the secret word for this turn: {sentinel}. "
                f"Reply: 'noted'.",
                chat_id=1001,
            ),
            timeout=90.0,
        )
        # The harvested session id should have landed in the store.
        token_after_t1 = session_store.get()
        assert session_store.is_initialized(), (
            "first call did not flip initialised=True"
        )
        assert token_after_t1.startswith("ses_"), (
            f"session token doesn't look like an opencode id: {token_after_t1!r}"
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
    """All descendants of pid (including pid). Returns [] if pid
    is already gone."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return []
    out = [pid]
    for child in parent.children(recursive=True):
        out.append(child.pid)
    return out


def test_cancel_mid_turn_reaps_process_group(
    brain: OpenCodeBrain,
    running_tasks: RunningTasks,
):
    """Spawn a real long-running turn, cancel mid-stream, verify
    every PID in the process group is gone within the
    ``_kill_group`` 5 s ceiling.

    This is the §7 dogfood checklist's "/cancel mid-turn" step,
    automated to the extent we can. Pins
    ``start_new_session=True`` at spawn (which puts opencode +
    its children — model SDK, tool subprocesses — under one
    process group so ``os.killpg`` reaches all of them).
    """
    chat_id = 2002
    # Use a prompt that forces opencode to spend a few seconds
    # streaming so we have a window to /cancel mid-turn. A long
    # prompt + a request for a long structured reply works without
    # needing any specific tool.
    prompt = (
        "Slowly count from 1 to 200 in plain English, one number per line. "
        "Take your time."
    )

    async def _spawn_and_cancel():
        respond_task = asyncio.create_task(
            brain.respond(prompt, chat_id=chat_id)
        )
        # Wait until respond has actually spawned the subprocess.
        # ``RunningTasks.is_running(chat_id)`` flips True once
        # ``respond`` reserves the slot AND attaches the proc.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if running_tasks.is_running(chat_id):
                break
            await asyncio.sleep(0.05)
        else:
            respond_task.cancel()
            pytest.fail("opencode never started running within 10s")

        # Snapshot the PG before cancel so we can verify reaping.
        slot_pid = None
        state = running_tasks._chats.get(chat_id)
        if state is not None and state.slot is not None and state.slot.proc is not None:
            slot_pid = state.slot.proc.pid
        assert slot_pid is not None, "no proc registered post-spawn"

        pids_before = _proc_tree(slot_pid)
        # /cancel — flips slot.cancelled=True, then SIGTERMs the PG.
        cancelled = await running_tasks.cancel(chat_id, grace_seconds=5.0)
        assert cancelled, "running_tasks.cancel returned False"

        # respond should now resolve as BrainCancelled (or
        # BrainError if the proc died very fast).
        with pytest.raises((BrainCancelled, Exception)):
            await asyncio.wait_for(respond_task, timeout=10.0)

        return slot_pid, pids_before

    slot_pid, pids_before = asyncio.run(_spawn_and_cancel())

    # Verify reaping: within 6s of the cancel, no PID from the
    # original PG should still be alive. _kill_group's grace is
    # 5s + a SIGKILL; we give a 1s buffer.
    deadline = time.monotonic() + 6.0
    survivors: list[int] = []
    while time.monotonic() < deadline:
        survivors = [
            pid for pid in pids_before
            if _pid_alive(pid)
        ]
        if not survivors:
            return
        time.sleep(0.1)
    pytest.fail(
        f"PG kill failed: survivors after 6s = {survivors} "
        f"(slot_pid={slot_pid}, original_pg={pids_before})"
    )


def _pid_alive(pid: int) -> bool:
    """True if pid is alive AND not a zombie. psutil treats zombies
    as 'alive' under is_running() — defend against that."""
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
