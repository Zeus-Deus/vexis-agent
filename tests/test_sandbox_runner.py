"""Unit tests for :mod:`vexis_agent.core.sandbox_runner`.

Cover JSON parsing of vexis-verify output, error mapping for subprocess
failures, and the ``is_available`` host probe.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from vexis_agent.core.sandbox_runner import (
    SandboxRunner,
    SandboxStartResult,
    SandboxVerifyResult,
    should_sandbox,
)


class _FakeProc:
    def __init__(self, rc: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = rc
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False

    async def communicate(self):
        await asyncio.sleep(0)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _make_runner(spawn_results: list[_FakeProc]) -> SandboxRunner:
    iterator = iter(spawn_results)

    async def fake_spawn(*_argv, **_kwargs):
        return next(iterator)

    return SandboxRunner(spawn=fake_spawn)


def test_start_ok():
    runner = _make_runner([_FakeProc(0, b'{"ok": true}\n')])
    res = asyncio.run(runner.start("task1"))
    assert isinstance(res, SandboxStartResult)
    assert res.ok and res.task_id == "task1"


def test_start_non_zero_returns_error():
    runner = _make_runner([_FakeProc(1, b"", b"docker: cannot connect")])
    res = asyncio.run(runner.start("task1"))
    assert not res.ok
    assert "docker" in res.error


def test_start_subprocess_oserror_returns_error(monkeypatch):
    async def fake_spawn(*_argv, **_kwargs):
        raise FileNotFoundError("no vexis-sandbox on PATH")

    runner = SandboxRunner(spawn=fake_spawn)
    res = asyncio.run(runner.start("task1"))
    assert not res.ok
    assert "vexis-sandbox" in res.error


def test_stop_returns_bool():
    runner = _make_runner([_FakeProc(0, b"")])
    assert asyncio.run(runner.stop("task1")) is True
    runner = _make_runner([_FakeProc(1, b"")])
    assert asyncio.run(runner.stop("task1")) is False


def test_verify_parses_pass():
    payload = json.dumps(
        {
            "ok": True,
            "result": {
                "task_id": "task1",
                "all_passed": True,
                "results": [{"name": "x", "passed": True}],
                "failed": [],
            },
        }
    )
    runner = _make_runner([_FakeProc(0, payload.encode())])
    res = asyncio.run(runner.verify("task1", "/workspace/checks.yaml"))
    assert isinstance(res, SandboxVerifyResult)
    assert res.ok and res.all_passed
    assert res.summary == "all checks passed"


def test_verify_parses_fail_lists_check_names():
    payload = json.dumps(
        {
            "ok": True,
            "result": {
                "task_id": "task1",
                "all_passed": False,
                "results": [],
                "failed": ["test-a", "test-b"],
            },
        }
    )
    runner = _make_runner([_FakeProc(1, payload.encode())])
    res = asyncio.run(runner.verify("task1", "/workspace/checks.yaml"))
    assert res.ok and not res.all_passed
    assert "test-a" in res.summary and "test-b" in res.summary


def test_verify_no_json_output_is_failure():
    runner = _make_runner([_FakeProc(2, b"", b"oops")])
    res = asyncio.run(runner.verify("task1", "/workspace/checks.yaml"))
    assert not res.ok
    assert res.summary == "oops" or "no JSON" in res.summary


def test_should_sandbox_keywords():
    assert should_sandbox("Run pytest please")
    assert should_sandbox("cargo build the lib")
    assert should_sandbox("compile this thing")
    assert not should_sandbox("tell me about quantum mechanics")
    assert not should_sandbox("")
