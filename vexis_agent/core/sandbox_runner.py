"""Thin async wrapper around the ``vexis-sandbox`` and ``vexis-verify``
console scripts, used by :mod:`vexis_agent.core.background_tasks` to wire
background work through a Docker sandbox + post-claim verification.

We deliberately *shell out* rather than import the in-process sandbox
class for a few reasons:

* the sandbox CLI is the supported public interface, so going through
  it is dogfooding;
* a daemon-level import would couple background-tasks tightly to the
  sandbox module's import-time side effects (we want background tasks
  to keep working on hosts without Docker, falling back to direct
  execution);
* tests can swap the runner whole (``FakeSandboxRunner``) without
  touching subprocess plumbing.

The runner is async because background-tasks lives in an asyncio loop;
each call awaits ``asyncio.create_subprocess_exec`` and returns the
captured stdout/stderr.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


# Keyword sets for the sandbox-default heuristic. Intentionally crude —
# false positives just give an extra container the user can ignore, and
# false negatives are recoverable by passing ``--sandbox`` explicitly.
_BUILD_TEST_KEYWORDS = frozenset(
    {
        "build",
        "compile",
        "test",
        "tests",
        "pytest",
        "cargo",
        "npm",
        "yarn",
        "pnpm",
        "make",
        "cmake",
        "ninja",
        "gradle",
        "maven",
        "go build",
        "go test",
        "implement",
        "fix the bug",
        "debug",
        "refactor",
        "ship",
        "deploy",
    }
)


def should_sandbox(prompt: str) -> bool:
    """Heuristic default for ``vexis-bg spawn``.

    Returns ``True`` when the prompt looks like a build-or-test task,
    ``False`` for pure research / text / chat prompts. Callers always
    have the option to override via an explicit ``sandbox`` argument.
    """
    if not prompt:
        return False
    lowered = prompt.lower()
    return any(kw in lowered for kw in _BUILD_TEST_KEYWORDS)


# ---------------------------------------------------------------------------
# Runner protocol
# ---------------------------------------------------------------------------


@dataclass
class SandboxStartResult:
    ok: bool
    task_id: str
    error: str | None = None


@dataclass
class SandboxVerifyResult:
    ok: bool
    all_passed: bool
    summary: str
    failed: list[str]
    raw: dict | None = None


class SandboxRunner:
    """Async wrapper around the sandbox/verify CLIs.

    Public surface: :meth:`start`, :meth:`stop`, :meth:`verify`. Each is
    a coroutine returning a typed result dataclass. Errors at the
    subprocess level are surfaced as ``ok=False`` results — we never
    raise into the caller's asyncio task, because :mod:`background_tasks`
    needs to attribute the failure to the *task*, not crash the watcher.
    """

    def __init__(
        self,
        *,
        sandbox_bin: str = "vexis-sandbox",
        verify_bin: str = "vexis-verify",
        spawn: Callable[..., Awaitable[asyncio.subprocess.Process]] | None = None,
    ) -> None:
        self._sandbox_bin = sandbox_bin
        self._verify_bin = verify_bin
        self._spawn = spawn or asyncio.create_subprocess_exec

    @staticmethod
    def is_available(sandbox_bin: str = "vexis-sandbox") -> bool:
        """Quick boolean for "should we even try sandbox routing?"

        Used at daemon startup to decide whether to fall back to direct
        execution when the user hasn't installed Docker (or the CLI
        is missing for some reason).
        """
        return shutil.which(sandbox_bin) is not None and shutil.which("docker") is not None

    async def _run(
        self,
        argv: list[str],
        *,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        proc = await self._spawn(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        rc = proc.returncode if proc.returncode is not None else -1
        return rc, out.decode(errors="replace"), err.decode(errors="replace")

    async def start(
        self,
        task_id: str,
        *,
        image: str | None = None,
        mounts: list[str] | None = None,
    ) -> SandboxStartResult:
        argv = [self._sandbox_bin, "start", task_id]
        if image:
            argv.extend(["--image", image])
        for spec in mounts or []:
            argv.extend(["--mount", spec])
        try:
            rc, _stdout, stderr = await self._run(argv, timeout=120)
        except (FileNotFoundError, OSError, asyncio.TimeoutError) as exc:
            log.warning("vexis-sandbox start failed for %r: %s", task_id, exc)
            return SandboxStartResult(ok=False, task_id=task_id, error=str(exc))
        if rc != 0:
            return SandboxStartResult(
                ok=False,
                task_id=task_id,
                error=stderr.strip() or f"vexis-sandbox exited {rc}",
            )
        return SandboxStartResult(ok=True, task_id=task_id)

    async def stop(self, task_id: str) -> bool:
        argv = [self._sandbox_bin, "stop", task_id]
        try:
            rc, _stdout, _stderr = await self._run(argv, timeout=60)
        except (FileNotFoundError, OSError, asyncio.TimeoutError) as exc:
            log.warning("vexis-sandbox stop failed for %r: %s", task_id, exc)
            return False
        return rc == 0

    async def verify(
        self,
        task_id: str,
        checks_path: str | Path,
    ) -> SandboxVerifyResult:
        argv = [
            self._verify_bin,
            "run",
            task_id,
            "--checks",
            str(checks_path),
        ]
        try:
            rc, stdout, stderr = await self._run(argv, timeout=300)
        except (FileNotFoundError, OSError, asyncio.TimeoutError) as exc:
            return SandboxVerifyResult(
                ok=False,
                all_passed=False,
                summary=f"verify subprocess error: {exc}",
                failed=[],
            )
        # vexis-verify always emits a single JSON line on stdout
        try:
            payload = json.loads(stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return SandboxVerifyResult(
                ok=False,
                all_passed=False,
                summary=stderr.strip() or "verify produced no JSON output",
                failed=[],
            )
        if not payload.get("ok"):
            return SandboxVerifyResult(
                ok=False,
                all_passed=False,
                summary=payload.get("error", "verify failed"),
                failed=[],
                raw=payload,
            )
        result = payload.get("result") or {}
        all_passed = bool(result.get("all_passed"))
        failed = list(result.get("failed") or [])
        # Construct a 1-2 line summary so the watcher can use it as the
        # next-turn observation if needed.
        if all_passed:
            summary = "all checks passed"
        else:
            summary = (
                f"{len(failed)} check(s) failed: " + ", ".join(failed)
                if failed
                else "checks failed"
            )
        return SandboxVerifyResult(
            ok=True,
            all_passed=all_passed,
            summary=summary,
            failed=failed,
            raw=payload,
        )


# ---------------------------------------------------------------------------
# Test fake — mirrors the API and records calls.
# ---------------------------------------------------------------------------


@dataclass
class FakeSandboxRunner:
    """Test substitute. Records every method call and returns canned
    outcomes from queued lists. Used by ``tests/test_background_tasks_sandbox.py``
    to assert that :class:`BackgroundTasks` actually starts/stops/verifies
    at the right moments."""

    starts: list[str] = None  # type: ignore[assignment]
    stops: list[str] = None  # type: ignore[assignment]
    verifies: list[tuple[str, str]] = None  # type: ignore[assignment]
    start_result: SandboxStartResult | None = None
    stop_result: bool = True
    verify_result: SandboxVerifyResult | None = None

    def __post_init__(self) -> None:
        if self.starts is None:
            self.starts = []
        if self.stops is None:
            self.stops = []
        if self.verifies is None:
            self.verifies = []

    async def start(
        self,
        task_id: str,
        *,
        image: str | None = None,
        mounts: list[str] | None = None,
    ) -> SandboxStartResult:
        self.starts.append(task_id)
        return self.start_result or SandboxStartResult(ok=True, task_id=task_id)

    async def stop(self, task_id: str) -> bool:
        self.stops.append(task_id)
        return self.stop_result

    async def verify(
        self,
        task_id: str,
        checks_path: str | os.PathLike,
    ) -> SandboxVerifyResult:
        self.verifies.append((task_id, str(checks_path)))
        return self.verify_result or SandboxVerifyResult(
            ok=True, all_passed=True, summary="all checks passed", failed=[]
        )
