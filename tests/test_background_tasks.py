"""Tests for core/background_tasks.py.

The registry holds asyncio.subprocess.Process references but never
spawns real `claude -p`; we patch asyncio.create_subprocess_exec to
return a FakeProc whose lifecycle the test drives. os.killpg /
os.getpgid are routed via a fixture so SIGTERM/SIGKILL flips the
fake's exit state synchronously.

Tests follow the codebase convention of sync test functions calling
asyncio.run() rather than pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import json
import signal
from pathlib import Path
from typing import Any

import pytest

from core import background_tasks as bg_module
from core.background_tasks import (
    BackgroundTaskLimitReached,
    BackgroundTasks,
    InvalidTaskName,
    NameAlreadyInUse,
    TaskNotFound,
    TaskStatus,
)


class FakeProc:
    """Stand-in for asyncio.subprocess.Process.

    `wait()` blocks until `finish()` is called. We append signals to a
    list so tests can assert which escalation step ran.
    """

    def __init__(self, pid: int = 7777) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._exit = asyncio.Event()
        self.signals: list[int] = []
        self._launched_at_args: tuple[Any, ...] | None = None

    async def wait(self) -> int:
        await self._exit.wait()
        assert self.returncode is not None
        return self.returncode

    def finish(self, returncode: int = 0) -> None:
        if self.returncode is None:
            self.returncode = returncode
            self._exit.set()


@pytest.fixture
def patch_killpg(monkeypatch):
    procs: dict[int, FakeProc] = {}

    def _getpgid(pid: int) -> int:
        return pid

    def _killpg(pgid: int, sig: int) -> None:
        proc = procs.get(pgid)
        if proc is None:
            raise ProcessLookupError(pgid)
        proc.signals.append(sig)
        if sig in (signal.SIGTERM, signal.SIGKILL):
            proc.finish(-sig)

    def _kill_check(pid: int, sig: int) -> None:
        # `os.kill(pid, 0)` is used as a liveness probe — if the proc
        # has been finished, it should look gone.
        proc = procs.get(pid)
        if proc is None:
            raise ProcessLookupError(pid)
        if sig == 0 and proc.returncode is not None:
            raise ProcessLookupError(pid)

    monkeypatch.setattr(bg_module.os, "getpgid", _getpgid)
    monkeypatch.setattr(bg_module.os, "killpg", _killpg)
    monkeypatch.setattr(bg_module.os, "kill", _kill_check)
    return procs


def _build_bg(tmp_path: Path, max_concurrent: int = 3) -> BackgroundTasks:
    return BackgroundTasks(
        workspace=tmp_path,
        system_prompt_provider=lambda: "TEST-SOUL",
        max_concurrent=max_concurrent,
        log_dir=tmp_path / "logs",
        state_file=tmp_path / "bg-state.json",
    )


def _patch_spawn_factory(monkeypatch, factory):
    """Replace asyncio.create_subprocess_exec with the given factory."""
    monkeypatch.setattr(bg_module.asyncio, "create_subprocess_exec", factory)


def test_validate_name_accepts_kebab_case():
    BackgroundTasks.validate_name("fix-login-bug")
    BackgroundTasks.validate_name("abc")
    # 30-char boundary: must fit exactly.
    BackgroundTasks.validate_name("a" + "b" * 29)


def test_validate_name_rejects_bad_inputs():
    for bad in (
        "",
        "ab",  # too short
        "1leading-digit",  # leading digit
        "Has-Caps",
        "has_underscore",
        "has space",
        "../traversal",
        "a" * 31,  # too long
    ):
        with pytest.raises(InvalidTaskName):
            BackgroundTasks.validate_name(bad)


def test_spawn_validates_name(tmp_path):
    bg = _build_bg(tmp_path)

    async def scenario() -> None:
        with pytest.raises(InvalidTaskName):
            await bg.spawn(chat_id=1, name="BAD", prompt="x")

    asyncio.run(scenario())


def test_spawn_and_finish_round_trip(monkeypatch, tmp_path, patch_killpg):
    notifications: list[tuple[int, str]] = []

    async def notify(chat_id: int, text: str) -> None:
        notifications.append((chat_id, text))

    proc = FakeProc(pid=900)
    patch_killpg[900] = proc

    async def fake_spawn(*_argv, **_kwargs) -> FakeProc:
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)

    bg = _build_bg(tmp_path)
    bg.set_notify(notify)

    async def scenario() -> None:
        task = await bg.spawn(chat_id=42, name="fix-login-bug", prompt="go")
        assert task.status == TaskStatus.RUNNING
        assert task.pid == 900
        # Pretend claude exited cleanly.
        proc.finish(returncode=0)
        # Wait for the watcher to react.
        for _ in range(100):
            current = await bg.get("fix-login-bug")
            if current is not None and current.status == TaskStatus.FINISHED:
                break
            await asyncio.sleep(0.01)
        final = await bg.get("fix-login-bug")
        assert final is not None
        assert final.status == TaskStatus.FINISHED
        assert final.exit_code == 0

    asyncio.run(scenario())
    assert len(notifications) == 1
    chat_id, text = notifications[0]
    assert chat_id == 42
    assert "fix-login-bug" in text
    assert text.startswith("✅")


def test_spawn_failure_path_marks_failed_and_notifies(
    monkeypatch, tmp_path, patch_killpg
):
    notifications: list[tuple[int, str]] = []

    async def notify(chat_id: int, text: str) -> None:
        notifications.append((chat_id, text))

    proc = FakeProc(pid=901)
    patch_killpg[901] = proc

    async def fake_spawn(*_argv, **_kwargs) -> FakeProc:
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)

    bg = _build_bg(tmp_path)
    bg.set_notify(notify)

    async def scenario() -> None:
        await bg.spawn(chat_id=7, name="bad-task", prompt="go")
        proc.finish(returncode=2)
        for _ in range(100):
            t = await bg.get("bad-task")
            if t is not None and t.status == TaskStatus.FAILED:
                break
            await asyncio.sleep(0.01)
        final = await bg.get("bad-task")
        assert final is not None
        assert final.status == TaskStatus.FAILED
        assert final.exit_code == 2

    asyncio.run(scenario())
    assert notifications and notifications[0][1].startswith("❌")


def test_spawn_concurrent_limit(monkeypatch, tmp_path, patch_killpg):
    procs = [FakeProc(pid=1000 + i) for i in range(3)]
    for p in procs:
        patch_killpg[p.pid] = p
    queue = list(procs)

    async def fake_spawn(*_argv, **_kwargs) -> FakeProc:
        return queue.pop(0)

    _patch_spawn_factory(monkeypatch, fake_spawn)

    bg = _build_bg(tmp_path, max_concurrent=3)

    async def scenario() -> None:
        await bg.spawn(chat_id=1, name="task-one", prompt="a")
        await bg.spawn(chat_id=1, name="task-two", prompt="b")
        await bg.spawn(chat_id=1, name="task-three", prompt="c")
        with pytest.raises(BackgroundTaskLimitReached):
            await bg.spawn(chat_id=1, name="task-four", prompt="d")
        # Tear them down so test exits clean.
        for p in procs:
            p.finish(returncode=0)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())


def test_spawn_name_already_in_use(monkeypatch, tmp_path, patch_killpg):
    p = FakeProc(pid=1100)
    patch_killpg[p.pid] = p

    async def fake_spawn(*_argv, **_kwargs) -> FakeProc:
        return p

    _patch_spawn_factory(monkeypatch, fake_spawn)

    bg = _build_bg(tmp_path)

    async def scenario() -> None:
        await bg.spawn(chat_id=1, name="dup-name", prompt="a")
        with pytest.raises(NameAlreadyInUse):
            await bg.spawn(chat_id=1, name="dup-name", prompt="b")
        p.finish(returncode=0)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())


def test_cancel_running_task_signals_and_suppresses_notification(
    monkeypatch, tmp_path, patch_killpg
):
    notifications: list[tuple[int, str]] = []

    async def notify(chat_id: int, text: str) -> None:
        notifications.append((chat_id, text))

    proc = FakeProc(pid=1200)
    patch_killpg[1200] = proc

    async def fake_spawn(*_argv, **_kwargs) -> FakeProc:
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)

    bg = _build_bg(tmp_path)
    bg.set_notify(notify)

    async def scenario() -> None:
        await bg.spawn(chat_id=99, name="cancel-me", prompt="hang")
        cancelled = await bg.cancel("cancel-me")
        assert cancelled is True
        # Watcher must observe the SIGTERM exit and NOT fire a "finished"
        # notification — the cancel path is silent at the registry level.
        for _ in range(100):
            t = await bg.get("cancel-me")
            if t is not None and t.finished_at is not None:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert signal.SIGTERM in proc.signals
    # The watcher should not have queued a completion notification.
    assert notifications == []


def test_cancel_unknown_task_raises(tmp_path):
    bg = _build_bg(tmp_path)

    async def scenario() -> None:
        with pytest.raises(TaskNotFound):
            await bg.cancel("does-not-exist")

    asyncio.run(scenario())


def test_status_summary_includes_running_and_recent_finished(
    monkeypatch, tmp_path, patch_killpg
):
    procs = [FakeProc(pid=1300 + i) for i in range(2)]
    for p in procs:
        patch_killpg[p.pid] = p
    queue = list(procs)

    async def fake_spawn(*_argv, **_kwargs) -> FakeProc:
        return queue.pop(0)

    _patch_spawn_factory(monkeypatch, fake_spawn)

    bg = _build_bg(tmp_path)

    async def scenario() -> list[dict]:
        await bg.spawn(chat_id=1, name="one-running", prompt="a")
        await bg.spawn(chat_id=1, name="two-finished", prompt="b")
        procs[1].finish(returncode=0)
        for _ in range(100):
            t = await bg.get("two-finished")
            if t and t.status == TaskStatus.FINISHED:
                break
            await asyncio.sleep(0.01)
        summary = await bg.status_summary()
        procs[0].finish(returncode=0)
        await asyncio.sleep(0.05)
        return summary

    summary = asyncio.run(scenario())
    names = {row["name"]: row["status"] for row in summary}
    assert names["one-running"] == "running"
    assert names["two-finished"] == "finished"


def test_tail_log_returns_last_n_lines(tmp_path):
    bg = _build_bg(tmp_path)

    async def scenario() -> str:
        # Plant a fake task entry + log file.
        log_path = bg._log_dir / "peek.log"  # type: ignore[attr-defined]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(f"line-{i}" for i in range(100)))
        from datetime import datetime, timezone

        bg._tasks["peek"] = bg_module.BackgroundTask(  # type: ignore[attr-defined]
            name="peek",
            chat_id=1,
            prompt="x",
            spawned_at=datetime.now(timezone.utc),
            log_path=log_path,
            status=TaskStatus.RUNNING,
            pid=1,
        )
        return await bg.tail_log("peek", n_lines=5)

    out = asyncio.run(scenario())
    assert out.splitlines() == ["line-95", "line-96", "line-97", "line-98", "line-99"]


def test_tail_log_unknown_task_raises(tmp_path):
    bg = _build_bg(tmp_path)

    async def scenario() -> None:
        with pytest.raises(TaskNotFound):
            await bg.tail_log("nope")

    asyncio.run(scenario())


def test_persist_writes_state_on_spawn_and_completion(
    monkeypatch, tmp_path, patch_killpg
):
    proc = FakeProc(pid=1400)
    patch_killpg[1400] = proc

    async def fake_spawn(*_argv, **_kwargs) -> FakeProc:
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)

    bg = _build_bg(tmp_path)
    state_path = tmp_path / "bg-state.json"

    async def scenario() -> dict:
        await bg.spawn(chat_id=5, name="persist-me", prompt="x")
        running_state = json.loads(state_path.read_text())
        assert any(t["name"] == "persist-me" for t in running_state["tasks"])
        proc.finish(returncode=0)
        for _ in range(100):
            t = await bg.get("persist-me")
            if t and t.status == TaskStatus.FINISHED:
                break
            await asyncio.sleep(0.01)
        return json.loads(state_path.read_text())

    final_state = asyncio.run(scenario())
    entry = next(t for t in final_state["tasks"] if t["name"] == "persist-me")
    assert entry["status"] == "finished"
    assert entry["exit_code"] == 0


def test_detect_lost_from_previous_run(tmp_path):
    state_path = tmp_path / "bg-state.json"
    # Write fake state where one task is RUNNING with a definitely-dead PID.
    state_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "name": "ghost",
                        "chat_id": 555,
                        "status": "running",
                        "spawned_at": "2026-04-29T00:00:00+00:00",
                        "finished_at": None,
                        "exit_code": None,
                        "pid": 2147483640,  # PID that almost certainly isn't alive
                        "log_path": str(tmp_path / "logs/ghost.log"),
                    },
                    {
                        "name": "stale",
                        "chat_id": 555,
                        "status": "finished",
                        "spawned_at": "2026-04-29T00:00:00+00:00",
                        "finished_at": "2026-04-29T00:01:00+00:00",
                        "exit_code": 0,
                        "pid": 9999,
                        "log_path": str(tmp_path / "logs/stale.log"),
                    },
                ]
            }
        )
    )
    bg = BackgroundTasks(
        workspace=tmp_path,
        system_prompt_provider=lambda: "S",
        log_dir=tmp_path / "logs",
        state_file=state_path,
    )

    async def scenario() -> list[dict]:
        return await bg.detect_lost_from_previous_run()

    lost = asyncio.run(scenario())
    assert lost == [{"name": "ghost", "chat_id": 555}]
    # State file should be consumed so the warning fires only once.
    assert not state_path.exists()


def test_detect_lost_returns_empty_when_no_state(tmp_path):
    bg = BackgroundTasks(
        workspace=tmp_path,
        system_prompt_provider=lambda: "S",
        log_dir=tmp_path / "logs",
        state_file=tmp_path / "missing.json",
    )

    async def scenario() -> list[dict]:
        return await bg.detect_lost_from_previous_run()

    assert asyncio.run(scenario()) == []
