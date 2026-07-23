"""Real-binary Codex smoke tests.

Opt-in via ``pytest -m brain_smoke_codex``. The default suite skips
these (they spawn a real ``codex exec`` subprocess, hit the
configured provider's API, and depend on the user's ``codex login``
state). Run intentionally before flipping ``brain.kind`` to ``codex``.

Covered: a foreground reply, session resume threading context, and
``/cancel`` reaping the process group. NOT covered: MCP tool firing
(manual dogfood) and auth-failure surface (too disruptive to
automate — ``codex login status`` is smoke-tested in the scaffold).

Design lock: ``.plans/codex-brain-research.md`` §3.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import psutil
import pytest

from vexis_agent.core.brain.base import BrainCancelled
from vexis_agent.core.brain.codex import CodexBrain
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore


def _codex_available() -> bool:
    return shutil.which("codex") is not None


pytestmark = [
    pytest.mark.brain_smoke_codex,
    pytest.mark.skipif(
        not _codex_available(), reason="codex binary not on PATH",
    ),
]


@pytest.fixture(autouse=True)
def _use_real_codex_home(monkeypatch: pytest.MonkeyPatch):
    """Undo the suite-wide ``_isolate_codex_home`` redirect: smoke
    tests drive the real binary against the user's real ``~/.codex``
    (auth state, model cache). Module-level autouse runs after the
    conftest autouse, so the delenv wins."""
    monkeypatch.delenv("CODEX_HOME", raising=False)


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
) -> CodexBrain:
    return CodexBrain(
        workspace=workspace,
        session=session_store,
        running_tasks=running_tasks,
    )


def test_foreground_turn_produces_reply(brain: CodexBrain):
    async def _run():
        return await asyncio.wait_for(
            brain.respond("Reply with the single word: pong", chat_id=999),
            timeout=90.0,
        )

    reply = asyncio.run(_run())
    assert isinstance(reply, str)
    assert reply.strip() != "", f"empty reply from real codex exec: {reply!r}"


def test_session_resume_threads_context(
    brain: CodexBrain, session_store: SessionStore,
):
    sentinel = "borogove-7421"

    async def _run_two_turns():
        await asyncio.wait_for(
            brain.respond(
                f"Remember the secret word for this turn: {sentinel}. "
                f"Reply: 'noted'.",
                chat_id=1001,
            ),
            timeout=120.0,
        )
        assert session_store.is_initialized(), (
            "first call did not flip initialised=True"
        )
        reply2 = await asyncio.wait_for(
            brain.respond(
                "What was the secret word I told you? Reply with just the word.",
                chat_id=1001,
            ),
            timeout=120.0,
        )
        return reply2

    reply2 = asyncio.run(_run_two_turns())
    assert sentinel in reply2.lower() or sentinel in reply2, (
        f"resume didn't carry context. Reply: {reply2!r}"
    )


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


def _proc_tree(pid: int) -> list[int]:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return []
    out = [pid]
    for child in parent.children(recursive=True):
        out.append(child.pid)
    return out


def test_cancel_mid_turn_reaps_process_group(
    brain: CodexBrain, running_tasks: RunningTasks,
):
    chat_id = 2002
    prompt = (
        "Slowly count from 1 to 200 in plain English, one number per line. "
        "Take your time."
    )

    async def _spawn_and_cancel():
        respond_task = asyncio.create_task(
            brain.respond(prompt, chat_id=chat_id)
        )
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if running_tasks.is_running(chat_id):
                break
            await asyncio.sleep(0.05)
        else:
            respond_task.cancel()
            pytest.fail("codex never started running within 10s")

        state = running_tasks._chats.get(chat_id)
        slot_pid = None
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
