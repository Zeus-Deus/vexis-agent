"""Sandbox-routing tests for :mod:`vexis_agent.core.background_tasks`.

These complement ``test_background_tasks.py``: same FakeProc plumbing,
same kill-signal monkeypatch fixture, but with a ``FakeSandboxRunner``
wired into the registry so we can assert start/stop/verify happen at
the right moments and in the right order.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import pytest

from vexis_agent.core import background_tasks as bg_module
from vexis_agent.core.background_tasks import (
    BackgroundTasks,
    TaskStatus,
)
from vexis_agent.core.sandbox_runner import (
    FakeSandboxRunner,
    SandboxStartResult,
    SandboxVerifyResult,
    should_sandbox,
)

# Re-use the existing FakeProc plumbing.
from tests.test_background_tasks import FakeProc, _patch_spawn_factory, patch_killpg  # noqa: F401


def _build_bg_with_sandbox(
    tmp_path: Path,
    runner: FakeSandboxRunner | None,
    *,
    max_concurrent: int = 3,
) -> BackgroundTasks:
    return BackgroundTasks(
        workspace=tmp_path,
        system_prompt_provider=lambda: "TEST-SOUL",
        max_concurrent=max_concurrent,
        log_dir=tmp_path / "logs",
        state_file=tmp_path / "bg-state.json",
        sandbox_runner=runner,
    )


# ---------------------------------------------------------------------------
# Heuristic
# ---------------------------------------------------------------------------


def test_heuristic_picks_build_prompts():
    assert should_sandbox("please run pytest on the new branch") is True
    assert should_sandbox("Fix the bug in the build script.") is True
    assert should_sandbox("compile the rust code") is True


def test_heuristic_skips_research_prompts():
    assert should_sandbox("summarize the docs page") is False
    assert should_sandbox("what's the weather like") is False
    assert should_sandbox("") is False


# ---------------------------------------------------------------------------
# Sandbox start ↔ stop wiring
# ---------------------------------------------------------------------------


def test_sandbox_started_before_launch_and_stopped_on_finish(
    monkeypatch, tmp_path, patch_killpg
):
    runner = FakeSandboxRunner()
    proc = FakeProc(pid=2000)
    patch_killpg[2000] = proc

    async def fake_spawn(*_argv, **_kwargs) -> FakeProc:
        # By the time claude is spawned the sandbox should have been
        # started. Asserting here pins the order.
        assert runner.starts == ["build-task"]
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)

    bg = _build_bg_with_sandbox(tmp_path, runner)

    async def scenario() -> None:
        await bg.spawn(chat_id=1, name="build-task", prompt="run cargo test")
        proc.finish(returncode=0)
        # Wait for watcher
        for _ in range(200):
            t = await bg.get("build-task")
            if t and t.status == TaskStatus.FINISHED:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert runner.stops == ["build-task"]


def test_sandbox_off_when_heuristic_says_no(monkeypatch, tmp_path, patch_killpg):
    runner = FakeSandboxRunner()
    proc = FakeProc(pid=2100)
    patch_killpg[2100] = proc

    async def fake_spawn(*_argv, **_kwargs):
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)
    bg = _build_bg_with_sandbox(tmp_path, runner)

    async def scenario() -> None:
        await bg.spawn(chat_id=1, name="research-task", prompt="summarize the article")
        proc.finish(returncode=0)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert runner.starts == []
    assert runner.stops == []


def test_explicit_sandbox_overrides_heuristic(monkeypatch, tmp_path, patch_killpg):
    runner = FakeSandboxRunner()
    proc = FakeProc(pid=2200)
    patch_killpg[2200] = proc

    async def fake_spawn(*_argv, **_kwargs):
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)
    bg = _build_bg_with_sandbox(tmp_path, runner)

    async def scenario() -> None:
        # Pure-text prompt forced into a sandbox.
        task = await bg.spawn(
            chat_id=1,
            name="forced-sandbox",
            prompt="write a poem",
            sandbox=True,
        )
        assert task.sandbox_enabled is True
        proc.finish(returncode=0)
        for _ in range(200):
            t = await bg.get("forced-sandbox")
            if t and t.status in (TaskStatus.FINISHED, TaskStatus.FAILED):
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert runner.starts == ["forced-sandbox"]
    assert runner.stops == ["forced-sandbox"]


def test_sandbox_start_failure_raises_at_spawn(monkeypatch, tmp_path, patch_killpg):
    runner = FakeSandboxRunner(
        start_result=SandboxStartResult(ok=False, task_id="t", error="docker down")
    )

    async def fake_spawn(*_argv, **_kwargs):
        raise AssertionError("should never spawn claude when sandbox start fails")

    _patch_spawn_factory(monkeypatch, fake_spawn)
    bg = _build_bg_with_sandbox(tmp_path, runner)

    async def scenario():
        with pytest.raises(Exception):
            await bg.spawn(chat_id=1, name="bad-start", prompt="cargo test", sandbox=True)
        # Placeholder must be cleared so the user can retry the name.
        t = await bg.get("bad-start")
        assert t is None

    asyncio.run(scenario())


def test_fallback_to_direct_when_runner_absent(monkeypatch, tmp_path, patch_killpg, caplog):
    proc = FakeProc(pid=2300)
    patch_killpg[2300] = proc

    async def fake_spawn(*_argv, **_kwargs):
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)
    # No runner — sandbox=True should warn + fall back.
    bg = _build_bg_with_sandbox(tmp_path, runner=None)

    async def scenario():
        task = await bg.spawn(
            chat_id=1, name="fallback", prompt="cargo test", sandbox=True
        )
        # Fell back; sandbox_enabled flipped off on the record.
        assert task.sandbox_enabled is False
        proc.finish(returncode=0)
        await asyncio.sleep(0.05)

    with caplog.at_level("WARNING"):
        asyncio.run(scenario())
    assert any("no SandboxRunner" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Verify integration
# ---------------------------------------------------------------------------


def test_verify_pass_marks_finished(monkeypatch, tmp_path, patch_killpg):
    runner = FakeSandboxRunner()
    proc = FakeProc(pid=2400)
    patch_killpg[2400] = proc

    async def fake_spawn(*_argv, **_kwargs):
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)
    bg = _build_bg_with_sandbox(tmp_path, runner)

    async def scenario():
        await bg.spawn(
            chat_id=1,
            name="verified-pass",
            prompt="cargo test it",
            sandbox=True,
            verify_checks="/workspace/checks.yaml",
        )
        proc.finish(returncode=0)
        for _ in range(200):
            t = await bg.get("verified-pass")
            if t and t.status == TaskStatus.FINISHED:
                break
            await asyncio.sleep(0.01)
        final = await bg.get("verified-pass")
        assert final.status == TaskStatus.FINISHED
        assert final.verify_summary == "all checks passed"

    asyncio.run(scenario())
    assert runner.verifies == [("verified-pass", "/workspace/checks.yaml")]


def test_verify_fail_marks_failed_even_when_agent_exited_zero(
    monkeypatch, tmp_path, patch_killpg
):
    runner = FakeSandboxRunner(
        verify_result=SandboxVerifyResult(
            ok=True,
            all_passed=False,
            summary="1 check(s) failed: tests-pass",
            failed=["tests-pass"],
        )
    )
    proc = FakeProc(pid=2500)
    patch_killpg[2500] = proc

    async def fake_spawn(*_argv, **_kwargs):
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)
    bg = _build_bg_with_sandbox(tmp_path, runner)

    async def scenario():
        await bg.spawn(
            chat_id=1,
            name="verified-fail",
            prompt="cargo test it",
            sandbox=True,
            verify_checks="/workspace/checks.yaml",
        )
        proc.finish(returncode=0)
        for _ in range(200):
            t = await bg.get("verified-fail")
            if t and t.status == TaskStatus.FAILED:
                break
            await asyncio.sleep(0.01)
        final = await bg.get("verified-fail")
        assert final.status == TaskStatus.FAILED
        assert "tests-pass" in final.verify_summary

    asyncio.run(scenario())


def test_verify_skipped_when_sandbox_off(monkeypatch, tmp_path, patch_killpg, caplog):
    runner = FakeSandboxRunner()
    proc = FakeProc(pid=2600)
    patch_killpg[2600] = proc

    async def fake_spawn(*_argv, **_kwargs):
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)
    bg = _build_bg_with_sandbox(tmp_path, runner)

    async def scenario():
        # --verify without --sandbox is a no-op; we warn and proceed.
        task = await bg.spawn(
            chat_id=1,
            name="no-sandbox-verify",
            prompt="write a poem",
            sandbox=False,
            verify_checks="/workspace/checks.yaml",
        )
        assert task.verify_checks_path is None
        proc.finish(returncode=0)
        await asyncio.sleep(0.05)

    with caplog.at_level("WARNING"):
        asyncio.run(scenario())
    assert runner.verifies == []
    assert any("ignoring verify" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Cancel + shutdown still stop the sandbox
# ---------------------------------------------------------------------------


def test_cancel_during_sandboxed_task_stops_container(
    monkeypatch, tmp_path, patch_killpg
):
    runner = FakeSandboxRunner()
    proc = FakeProc(pid=2700)
    patch_killpg[2700] = proc

    async def fake_spawn(*_argv, **_kwargs):
        return proc

    _patch_spawn_factory(monkeypatch, fake_spawn)
    bg = _build_bg_with_sandbox(tmp_path, runner)

    async def scenario():
        await bg.spawn(
            chat_id=1, name="cancel-sb", prompt="cargo test", sandbox=True
        )
        assert await bg.cancel("cancel-sb") is True
        # Watcher reacts to the killed proc; sandbox stop fires from
        # the watcher's finally-style cleanup path.
        for _ in range(200):
            t = await bg.get("cancel-sb")
            if t and t.finished_at is not None:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert runner.starts == ["cancel-sb"]
    assert runner.stops == ["cancel-sb"]
