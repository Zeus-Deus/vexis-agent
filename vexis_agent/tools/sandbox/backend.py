"""Docker CLI wrapper used by :mod:`vexis_agent.tools.sandbox`.

The :class:`Sandbox` class never shells out to Docker directly — every
call goes through :class:`DockerBackend`. Tests use :class:`FakeBackend`
to drive the same code path without needing a real Docker daemon.

The protocol is intentionally tiny (``run``) so swapping backends is a
single substitution.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Iterable


class BackendError(RuntimeError):
    """Raised when the underlying docker CLI is unreachable or errors out
    in a way the sandbox layer cannot recover from (e.g. missing binary,
    daemon down). Per-command non-zero exits are surfaced via
    :class:`ExecResult` and are NOT errors at this layer.
    """


@dataclass(frozen=True)
class ExecResult:
    """Outcome of a single ``docker exec`` (or any backend command).

    ``exit_code`` mirrors the underlying process exit. ``stdout`` and
    ``stderr`` are captured as decoded text. ``cmd`` is included for
    diagnostics — callers should not parse it.
    """

    cmd: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class DockerBackend:
    """Real backend: invokes the ``docker`` CLI on the host.

    A thin wrapper. The point isn't isolation from docker (we trust the
    binary) — it's isolation from the *test path*. Production code calls
    ``DockerBackend()``; unit tests inject ``FakeBackend()``.
    """

    def __init__(self, docker_bin: str = "docker") -> None:
        self._docker = docker_bin

    def run(
        self,
        argv: Iterable[str],
        *,
        timeout: float | None = None,
        input_bytes: bytes | None = None,
    ) -> ExecResult:
        cmd = (self._docker, *argv)
        try:
            proc = subprocess.run(
                cmd,
                input=input_bytes,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise BackendError(
                f"docker binary not found ({self._docker!r}): {exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise BackendError(
                f"docker timed out after {timeout}s: {shlex.join(cmd)}"
            ) from exc
        return ExecResult(
            cmd=tuple(cmd),
            exit_code=proc.returncode,
            stdout=proc.stdout.decode(errors="replace") if proc.stdout else "",
            stderr=proc.stderr.decode(errors="replace") if proc.stderr else "",
        )


# ----------------------------------------------------------------------------
# Test fake
# ----------------------------------------------------------------------------


@dataclass
class FakeBackend:
    """In-memory fake. Records every call as a tuple of argv strings and
    returns the next queued ``ExecResult``, or a default success.

    Used directly by ``tests/test_sandbox.py``. The dataclass shape makes
    assertions in tests straightforward (``backend.calls[0]`` etc.).
    """

    calls: list[tuple[str, ...]] = field(default_factory=list)
    # Either a static list popped in order, or a callable that maps argv
    # → ExecResult. A callable is more flexible for the "first ps fails,
    # second ps succeeds" patterns.
    queued: list[ExecResult] = field(default_factory=list)
    responder: Callable[[tuple[str, ...]], ExecResult] | None = None

    def run(
        self,
        argv: Iterable[str],
        *,
        timeout: float | None = None,
        input_bytes: bytes | None = None,
    ) -> ExecResult:
        argv_t = tuple(argv)
        self.calls.append(argv_t)
        if self.responder is not None:
            return self.responder(argv_t)
        if self.queued:
            return self.queued.pop(0)
        return ExecResult(cmd=("docker", *argv_t), exit_code=0, stdout="", stderr="")
